"""Jobs, stored check results and "Continue setup" (docs/specs/spec_cockpit.md §8.1, §8.3)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from . import patterns, steps
from .checks import ALL_CHECKS, BY_ID, GROUPS
from .models import CheckRecord, Job

MUTATING_LOCK = 0x41523343  # pg advisory lock id ("AR3C"): one mutating job at a time


class Busy(Exception):
    def __init__(self, job: Job):
        super().__init__(f"job {job.id} ({job.step_id}) is {job.status}")
        self.job = job


def log_dir() -> Path:
    path = Path(settings.DATA_DIR) / "logs" / "jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def job_dict(job: Job) -> dict:
    step = steps.BY_ID.get(job.step_id)
    duration = None
    if job.started:
        duration = ((job.finished or timezone.now()) - job.started).total_seconds()
    return {
        "id": str(job.id),
        "step_id": job.step_id,
        "title": step.title if step else job.step_id,
        "params": job.params,
        "command": job.command,
        "user": job.user,
        "mutating": job.mutating,
        "status": job.status,
        "exit_code": job.exit_code,
        "cancel_requested": job.cancel_requested,
        "matches": job.matches,
        "lines": job.lines,
        "created": job.created.isoformat() if job.created else None,
        "started": job.started.isoformat() if job.started else None,
        "finished": job.finished.isoformat() if job.finished else None,
        "duration_s": duration,
        "post_checks": list(step.post_checks) if step else [],
    }


def create_job(step_id: str, params: dict | None, user: str, host_os: str) -> Job:
    step = steps.get(step_id)
    if step.needs_root:
        raise steps.StepError(f"{step_id} needs root: run its instructions yourself")
    if host_os not in step.platforms:
        raise steps.StepError(f"{step_id} is not available on this host")
    clean = step.validate(params)
    if step.host_check is not None:
        step.host_check(clean, host_os)
    command = step.command(clean)  # a plan validates its parameters here
    with transaction.atomic():
        if step.mutating:
            # Serialises concurrent requests; the lock is released at the end of the transaction.
            with connection.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", [MUTATING_LOCK])
            active = Job.objects.filter(mutating=True, status__in=Job.ACTIVE).first()
            if active:
                raise Busy(active)
        job = Job.objects.create(
            step_id=step.id,
            params=clean,
            command=command,
            user=user,
            mutating=step.mutating,
        )
        job.log_path = str(log_dir() / f"{job.id}.log")
        job.save(update_fields=["log_path"])
    return job


def request_cancel(job: Job) -> Job:
    if job.status == Job.QUEUED:
        job.status, job.finished = Job.CANCELLED, timezone.now()
        job.save(update_fields=["status", "finished"])
    elif job.status == Job.RUNNING:
        job.cancel_requested = True
        job.save(update_fields=["cancel_requested"])
    return job


def read_log(job: Job, after_seq: int = 0) -> list[dict]:
    path = Path(job.log_path)
    if not job.log_path or not path.exists():
        return []
    out = []
    for raw in path.read_text(errors="replace").splitlines():
        seq, ts, stream, text = (raw.split("\t", 3) + ["", "", ""])[:4]
        if seq.isdigit() and int(seq) > after_seq:
            line = {"seq": int(seq), "ts": ts, "stream": stream, "text": text}
            # The file has no match flags (the live lines get them in the executor): without
            # this a job opened after it finished would show no highlighted error lines.
            if hits := patterns.match(text):
                line["match"] = hits[0]["id"]
            out.append(line)
    return out


def prune_jobs(now=None) -> int:
    """Keep 30 days or the last 200 jobs, whichever is more (§8.3)."""
    cutoff = (now or timezone.now()) - timedelta(days=settings.JOB_RETENTION_DAYS)
    keep = list(
        Job.objects.order_by("-created").values_list("id", flat=True)[: settings.JOB_KEEP_LAST]
    )
    old = Job.objects.filter(created__lt=cutoff).exclude(id__in=keep).exclude(status__in=Job.ACTIVE)
    for job in old:
        if job.log_path:
            Path(job.log_path).unlink(missing_ok=True)
    deleted, _ = old.delete()
    return deleted


# --- check results ------------------------------------------------------------------------


def store_results(results) -> None:
    for r in results:
        d = r.to_dict()
        CheckRecord.objects.update_or_create(check_id=d["id"], defaults={"data": d})


def stored_results() -> list[dict]:
    records = {r.check_id: r.data for r in CheckRecord.objects.all()}
    return [records[c.id] for c in ALL_CHECKS if c.id in records]


def summary(results: list[dict]) -> dict:
    """ "x / y done": pass counts as done; skipped items are not counted."""
    counted = [r for r in results if r["status"] != "skip"]
    done = sum(r["status"] == "pass" for r in counted)
    return {"done": done, "total": len(counted)}


def checklist(results: list[dict], host: dict) -> dict:
    return {"host": host, "groups": GROUPS, "checks": results, "summary": summary(results)}


def next_fix(results: list[dict], host_os: str) -> tuple[dict, steps.Step] | None:
    """The first failing or warning check, in checklist order, whose fix is a runnable step
    with its requirements met (§8.1 "Continue setup")."""
    status = {r["id"]: r["status"] for r in results}
    for r in results:
        if r["status"] not in ("fail", "warn") or not r.get("fix_step"):
            continue
        step = steps.BY_ID.get(r["fix_step"])
        if step is None or step.needs_root or host_os not in step.platforms:
            continue
        if all(status.get(req) == "pass" for req in step.requires):
            return r, step
    return None


def manual_items(results: list[dict]) -> list[str]:
    """Failing checks whose fix the user has to do by hand (needs root or no step)."""
    out = []
    for r in results:
        if r["status"] != "fail":
            continue
        step = steps.BY_ID.get(r.get("fix_step", ""))
        if step is None or step.needs_root:
            out.append(r["id"])
    return out


def check_title(check_id: str) -> str:
    return BY_ID[check_id].title if check_id in BY_ID else check_id
