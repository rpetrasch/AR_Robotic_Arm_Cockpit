"""Runs one job (docs/specs/spec_cockpit.md §8.3): streams the output to the log file and to
/ws/jobs/<id>, matches the error patterns, honours cancel and timeout, then re-runs the step's
post-checks so the checklist updates."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.conf import settings
from django.utils import timezone

from core import agent, redact
from core.runner import CANCELLED, TIMED_OUT, get_runner
from monitoring import logs

from . import patterns, service, steps
from .checks import context_from_settings, run_checks
from .models import Job

log = logging.getLogger("cockpit.jobs")
MAX_MATCHES = 50
FLUSH_S = 0.2
CANCEL_POLL_S = 0.5
CHECKS_GROUP = "checks"


def job_group(job_id) -> str:
    return f"job.{job_id}"


async def broadcast(group: str, message: dict) -> None:
    layer = get_channel_layer()
    if layer is not None:
        await layer.group_send(group, message)


async def send_job(job: Job) -> None:
    await broadcast(job_group(job.id), {"type": "job.update", "job": service.job_dict(job)})


def repo_for_jobs() -> Path:
    if settings.FAKE:
        from core.fake import ensure_repo

        return ensure_repo(settings.DATA_DIR, settings.REPO_DIR)
    return settings.REPO_DIR


async def execute(job: Job) -> Job:
    step = steps.get(job.step_id)
    secrets = [*redact.secret_values(), agent.token(settings.DATA_DIR)]
    seq = 0
    pending: list[dict] = []
    path = Path(job.log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("a", encoding="utf-8")

    async def flush() -> None:
        nonlocal pending
        if pending:
            batch, pending = pending, []
            await broadcast(job_group(job.id), {"type": "job.lines", "lines": batch})

    async def on_line(stream: str, text: str) -> None:
        nonlocal seq
        seq += 1
        text = redact.redact(text, secrets)
        ts = datetime.now(UTC).isoformat(timespec="milliseconds")
        fh.write(f"{seq}\t{ts}\t{stream}\t{text}\n")
        fh.flush()
        hits = patterns.match(text)
        line = {"seq": seq, "ts": ts, "stream": stream, "text": text}
        worst = max((h["severity"] for h in hits), key=patterns.SEVERITIES.index, default="")
        level = {"critical": "FATAL", "error": "ERROR", "warn": "WARN"}.get(worst, "INFO")
        logs.sink().push(f"job:{job.id}", level, patterns.ANSI.sub("", text), node=job.step_id)
        if hits:
            line["match"] = hits[0]["id"]
            for h in hits:
                if len(job.matches) < MAX_MATCHES:
                    job.matches.append({**h, "seq": seq, "line": patterns.ANSI.sub("", text)[:500]})
            await sync_to_async(job.save)(update_fields=["matches"])
            await send_job(job)
        pending.append(line)
        if len(pending) >= 50:
            await flush()

    async def flusher() -> None:
        while True:
            await asyncio.sleep(FLUSH_S)
            await flush()

    cancel = asyncio.Event()

    async def cancel_watch() -> None:
        while not cancel.is_set():
            await asyncio.sleep(CANCEL_POLL_S)
            fresh = (
                await Job.objects.filter(pk=job.pk)
                .values_list("cancel_requested", flat=True)
                .afirst()
            )
            if fresh:
                cancel.set()

    job.status, job.started = Job.RUNNING, timezone.now()
    await sync_to_async(job.save)(update_fields=["status", "started"])
    await send_job(job)
    helpers = [asyncio.ensure_future(flusher()), asyncio.ensure_future(cancel_watch())]
    repo = repo_for_jobs()
    try:
        if step.python is not None:
            await on_line("cmd", f"$ {job.command}")
            token = steps.CURRENT_JOB.set(job)
            try:
                rc = await step.python(repo, on_line, job.params, cancel)
            finally:
                steps.CURRENT_JOB.reset(token)
        else:
            rc = await _run_commands(step, job, on_line, repo, cancel)
        if rc == 0 and step.after is not None:
            rc = await step.after(job.params, on_line, cancel)
    except Exception as exc:  # the job fails, the worker keeps running
        log.exception("job %s crashed", job.id)
        await on_line("err", f"cockpit error: {exc}")
        rc = 1
    finally:
        for h in helpers:
            h.cancel()
        await flush()
        fh.close()

    job.exit_code = rc
    job.lines = seq
    job.status = {CANCELLED: Job.CANCELLED, TIMED_OUT: Job.TIMEOUT}.get(
        rc, Job.SUCCEEDED if rc == 0 else Job.FAILED
    )
    # Re-check before the final status goes out: a finished job means an updated checklist.
    if step.post_checks:
        await recheck(list(step.post_checks))
    job.finished = timezone.now()
    await sync_to_async(job.save)(
        update_fields=["exit_code", "lines", "finished", "status", "matches"]
    )
    await send_job(job)
    return job


def job_env() -> dict[str, str]:
    """Environment of every job command: the agent token (the Makefile passes it into the
    stack) and the data dir it lives in."""
    return {
        "COCKPIT_AGENT_TOKEN": agent.token(settings.DATA_DIR),
        "COCKPIT_DATA_DIR": str(settings.DATA_DIR),
    }


async def _run_commands(step, job: Job, on_line, repo: Path, cancel: asyncio.Event) -> int:
    runner = get_runner()
    rc = 0
    for env, argv in step.commands(job.params):
        await on_line("cmd", f"$ {steps.shell_words((env, argv))}")
        rc = await runner.stream(
            argv,
            on_line,
            cwd=repo,
            env={**job_env(), **env},
            timeout=step.timeout_s,
            cancel=cancel,
        )
        if rc != 0:
            break
    return rc


async def recheck(ids: list[str]) -> None:
    ctx = await context_from_settings()
    results = await run_checks(ctx, ids)
    await sync_to_async(service.store_results)(results)
    await broadcast(CHECKS_GROUP, {"type": "checks.update", "ids": ids})


async def claim_next() -> Job | None:
    """The oldest queued job, marked as taken (one worker; SKIP LOCKED keeps it safe)."""

    def claim():
        from django.db import transaction

        with transaction.atomic():
            job = (
                Job.objects.select_for_update(skip_locked=True)
                .filter(status=Job.QUEUED)
                .order_by("created")
                .first()
            )
            if job is None:
                return None
            job.status = Job.RUNNING
            job.save(update_fields=["status"])
            return job

    return await sync_to_async(claim)()
