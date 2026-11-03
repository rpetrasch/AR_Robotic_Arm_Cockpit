"""Sessions (docs/specs/spec_cockpit.md §12a.2): one per run of a ROS stack.

``sync(st)`` opens a session for the running stack and closes the sessions whose stack is
gone. run_ingest and run_monitor both call it (idempotent: the container's session key is
unique), so a session exists as soon as either worker sees the stack, also for stacks
started from the CLI.

Recording (REC, §12a.3) starts on in REAL, in SIM as TELEMETRY_RECORD_SIM says, and off in
REMOTE (Workstation A records its own sessions).
"""

from __future__ import annotations

import hashlib
from datetime import timedelta

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Session

DB_MODE = {"mock": "sim_mock", "gazebo": "sim_gazebo", "real": "real", "remote": "remote"}
STARTED_BY_WINDOW = timedelta(minutes=10)  # an env.start job this long before the container


def config_hash(repo) -> str:
    """sha256 over the site config (config/*.yaml, config/cockpit/*.yaml)."""
    h = hashlib.sha256()
    for path in sorted([*repo.glob("config/*.yaml"), *repo.glob("config/cockpit/*.yaml")]):
        h.update(path.relative_to(repo).as_posix().encode() + b"\0" + path.read_bytes())
    return h.hexdigest()[:16]


def default_recording(robot: str) -> bool:
    if robot == "real":
        return True
    if robot == "remote":
        return False
    return settings.TELEMETRY_RECORD_SIM


async def _git_commit(runner, repo) -> str:
    res = await runner.run(["git", "-C", str(repo), "log", "-1", "--format=%H"], timeout=5)
    return res.stdout.split()[0] if res.ok and res.stdout.strip() else ""


def _started_by(started) -> str:
    from setup.models import Job

    job = (
        Job.objects.filter(
            step_id="env.start",
            created__gte=started - STARTED_BY_WINDOW,
            created__lte=started + timedelta(minutes=1),
        )
        .order_by("-created")
        .first()
    )
    return job.user if job else "cli"


def _started(container) -> object:
    """The container's creation time (docker's CreatedAt), else now."""
    from datetime import datetime

    raw = (container.created or "").strip()
    try:
        if raw.replace(".", "", 1).isdigit():  # the fake world writes epoch seconds
            return datetime.fromtimestamp(float(raw), tz=timezone.get_current_timezone())
        # "2026-11-02 10:15:03 +0100 CET"
        return datetime.strptime(" ".join(raw.split()[:3]), "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return timezone.now()


async def sync(st, host: dict | None = None, runner=None) -> Session | None:
    """Open the running stack's session (if any), close the others. Returns the open one."""
    from core.runner import get_runner
    from setup.executor import repo_for_jobs

    current = st.stack if st.docker else None
    mode, container = current if current else (None, None)
    live = mode is not None and mode.robot in DB_MODE and container.state == "running"
    key = container.session if live else None
    if st.docker:
        await _close_others(key)
    if not live:
        return None
    existing = await Session.objects.filter(key=key).afirst()
    if existing is not None:
        return existing
    runner = runner or get_runner()
    repo = repo_for_jobs()
    started = _started(container)
    if host is None:
        from core import platform

        host = (await platform.current_host(runner)).to_dict()
    fields = {
        "host": host.get("label", "") or host.get("os", ""),
        "os": host.get("os", ""),
        "mode": DB_MODE[mode.robot],
        "mode_id": mode.id,
        "source": "real" if mode.robot == "real" else "sim",
        "git_commit": await _git_commit(runner, repo),
        "config_hash": config_hash(repo),
        "user": await sync_to_async(_started_by)(started),
        "container": container.name,
        "started": started,
        "recording": default_recording(mode.robot),
        "rate_hz": settings.TELEMETRY_RATE_HZ,
    }

    def create():
        try:
            with transaction.atomic():
                return Session.objects.create(key=key, **fields), True
        except IntegrityError:  # the other worker was first
            return Session.objects.get(key=key), False

    session, created = await sync_to_async(create)()
    if created:
        from . import events

        await events.record("session_start", {"mode": session.mode, "user": session.user}, session)
    return session


async def _close_others(key: str | None) -> None:
    from . import events

    now = timezone.now()
    async for s in Session.objects.filter(ended__isnull=True).exclude(key=key or ""):
        s.ended = now
        await s.asave(update_fields=["ended"])
        await events.record("session_end", {"mode": s.mode}, s)


async def current() -> Session | None:
    return await Session.objects.filter(ended__isnull=True).order_by("-started").afirst()


def to_dict(s: Session) -> dict:
    return {
        "id": str(s.id),
        "host": s.host,
        "os": s.os,
        "mode": s.mode,
        "mode_id": s.mode_id,
        "source": s.source,
        "git_commit": s.git_commit,
        "config_hash": s.config_hash,
        "user": s.user,
        "container": s.container,
        "started": s.started.isoformat(),
        "ended": s.ended.isoformat() if s.ended else None,
        "recording": s.recording,
        "rate_hz": s.rate_hz,
        "samples": s.samples,
        "dropped": s.dropped,
    }
