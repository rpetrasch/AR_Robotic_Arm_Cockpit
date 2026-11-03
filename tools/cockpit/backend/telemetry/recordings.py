"""Recordings (docs/specs/spec_cockpit.md §12a.1): rosbag2 MCAP and LeRobot data stay files under
data/; Postgres keeps the reference and the time range (table telemetry_recording).

``register_newest`` runs after the rosbag.record job (`make bag`): it reads the newest bag's
metadata.yaml (rosbag2's own description) and stores path, start, end, topics and size.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
from asgiref.sync import sync_to_async

from .models import Recording, Session


def read_bag(path: Path) -> dict:
    info = yaml.safe_load((path / "metadata.yaml").read_text())["rosbag2_bagfile_information"]
    start_ns = info["starting_time"]["nanoseconds_since_epoch"]
    duration_ns = info["duration"]["nanoseconds"]
    start = datetime.fromtimestamp(start_ns / 1e9, UTC)
    topics = [
        {
            "name": t["topic_metadata"]["name"],
            "type": t["topic_metadata"]["type"],
            "messages": t["message_count"],
        }
        for t in info.get("topics_with_message_count", [])
    ]
    size = sum(f.stat().st_size for f in path.iterdir() if f.is_file())
    return {
        "start": start,
        "end": start + timedelta(microseconds=duration_ns / 1000),
        "topics": topics,
        "size": size,
        "messages": info.get("message_count", 0),
    }


def _register(path: Path, repo: Path) -> dict:
    meta = read_bag(path)
    rel = path.relative_to(repo).as_posix()
    session = (
        Session.objects.filter(started__lte=meta["start"])
        .filter(ended__isnull=True)
        .order_by("-started")
        .first()
        or Session.objects.filter(started__lte=meta["start"], ended__gte=meta["start"]).first()
    )
    rec, _created = Recording.objects.update_or_create(
        path=rel,
        defaults={
            "kind": "mcap",
            "session": session,
            "start": meta["start"],
            "end": meta["end"],
            "topics": meta["topics"],
            "size": meta["size"],
        },
    )
    return to_dict(rec)


@sync_to_async
def register_newest(params: dict | None = None) -> dict | None:
    from setup.executor import repo_for_jobs

    repo = repo_for_jobs()
    bags = sorted(
        (p.parent for p in (repo / "data/bags").glob("*/metadata.yaml")),
        key=lambda p: p.stat().st_mtime,
    )
    if not bags:
        return None
    return _register(bags[-1], repo)


def to_dict(r: Recording) -> dict:
    return {
        "id": str(r.id),
        "session": str(r.session_id) if r.session_id else None,
        "run": str(r.run_id) if r.run_id else None,
        "kind": r.kind,
        "path": r.path,
        "start": r.start.isoformat(),
        "end": r.end.isoformat() if r.end else None,
        "topics": r.topics,
        "size": r.size,
    }
