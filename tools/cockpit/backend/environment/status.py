"""The header status (docs/specs/spec_cockpit.md §7.2), computed by run_monitor every 2 s and pushed
over /ws/status: the host, the running stack (from Docker), component health (§9.2), the
viewers, the jobs, active alerts (§10.5) and the recording state (REC, §12a.3).

The overall pill is the worst of the stack state, component health and active alerts
(acknowledged alerts no longer count).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from django.conf import settings

from core import platform
from core.runner import CommandRunner, get_runner

from . import health
from . import state as stack_state

CACHE_KEY = "cockpit:status"
GROUP = "status"


TRANSITIONS = {"env.start": "STARTING…", "env.stop": "STOPPING…", "env.restart": "RESTARTING…"}


def _native_viewer(mode, host_os: str, windows: bool) -> dict:
    """RViz / Gazebo as windows on the desktop: not part of the cockpit, so say how to get them."""
    if host_os != "linux":
        hint = (
            "RViz and Gazebo windows need a Linux desktop. On this computer use RViz in the "
            "browser (noVNC) or Lichtblick."
        )
        commands: list[str] = []
    else:
        state = (
            "They are open on this computer's screen now."
            if windows
            else "None are open: the running stack has no windows."
            if mode is not None
            else "Start a stack with windows to see them."
        )
        hint = (
            "RViz and the Gazebo GUI are native windows on this computer's desktop, not in the "
            f"browser. {state}"
        )
        commands = [
            "xhost +local:docker   # once per login: lets Docker open windows on your screen",
            "make up-linux         # mock: the RViz window",
            "make sim-linux        # Gazebo: Gazebo and RViz windows (GPU=1: on the NVIDIA GPU)",
        ]
    return {
        "id": "native",
        "label": "RViz / Gazebo windows",
        "available": windows,
        "port": None,
        "kind": "note",
        "path": "",
        "ws_host": None,
        "hint": hint,
        "commands": commands,
    }


def _viewers(mode, rows: dict[str, dict], host_os: str = "linux") -> list[dict]:
    """Viewer buttons (§7.2, §9.3). The browser fills in its own host name, so the links also
    work from another machine on the LAN; REMOTE points Lichtblick at Workstation A."""
    ports = health._ports()
    router = health._zenoh_router()
    remote = mode is not None and mode.robot == "remote"
    ws_host = router[0] if remote and router else None
    fox_ok = rows["foxglove_bridge"]["status"] == "ready" or (remote and ws_host is not None)
    windows = mode is not None and mode.service in ("linux", "linux-gpu") and not mode.headless
    viewers = [
        {
            "id": "lichtblick_web",
            "label": "Lichtblick (web)",
            "available": rows["lichtblick_web"]["status"] == "ready" and fox_ok,
            "port": ports["lichtblick"],
            "kind": "http",
            "path": f"/?ds=foxglove-websocket&ds.url=ws://{{host}}:{ports['foxglove']}",
            "ws_host": ws_host,
            "hint": "Lichtblick in the browser with the AR3 layout, connected to foxglove_bridge.",
        },
        {
            "id": "lichtblick",
            "label": "Lichtblick (desktop)",
            "available": fox_ok,
            "port": ports["foxglove"],
            "kind": "foxglove",
            "path": "",
            "ws_host": ws_host,
            "hint": "Lichtblick desktop: Open connection → Foxglove WebSocket → this URL.",
        },
        {
            "id": "novnc",
            "label": "RViz (noVNC)",
            "available": rows["novnc"]["status"] == "ready",
            "port": ports["novnc"],
            "kind": "http",
            "path": "/vnc.html?autoconnect=1&resize=remote",
            "ws_host": None,
            "hint": "RViz with MoveIt's MotionPlanning panel in the browser.",
        },
    ]
    for v in viewers:
        v.setdefault("commands", [])
    return [*viewers, _native_viewer(mode, host_os, windows)]


def _overall(st: stack_state.StackState) -> tuple[str, list[str]]:
    if not st.docker:
        return "ERROR", [f"Docker is not reachable: {st.error}" if st.error else "Docker down"]
    if len(st.stacks) > 1:
        names = ", ".join(c.name for _m, c in st.stacks)
        return "WARN", [f"More than one ROS stack is running ({names}); stop all but one."]
    if not st.stacks and st.dead_stacks:
        names = ", ".join(f"{c.name} ({c.status})" for _m, c in st.dead_stacks)
        return "ERROR", [f"The ROS stack died: {names}. `make down` removes it."]
    if not st.stacks:
        return "OFFLINE", ["No ROS stack is running."]
    mode, container = st.stacks[0]
    if container.state != "running":
        return "WARN", [f"{container.name} is {container.state}."]
    if mode is None:
        return "WARN", [f"{container.name} runs an unknown configuration."]
    return "OK", []


async def _jobs() -> dict:
    from setup.models import Job

    active = [
        j
        async for j in Job.objects.filter(status__in=Job.ACTIVE)
        .order_by("created")
        .values("id", "step_id", "status")
    ]
    current = active[0] if active else None
    return {
        "running": len(active),
        "current": str(current["id"]) if current else None,
        "step": current["step_id"] if current else None,
        "transition": next(
            (TRANSITIONS[j["step_id"]] for j in active if j["step_id"] in TRANSITIONS), None
        ),
    }


async def _arm(mode) -> str | None:
    if mode is None or mode.robot != "real":
        return None
    from .arm import current_state

    return await current_state()


RANK = {"OK": 0, "OFFLINE": 0, "WARN": 1, "ERROR": 2}


def with_alerts(snap: dict, summary: dict) -> dict:
    """Fold the active alerts into the pill (§7.2: worst of component health + alerts)."""
    from monitoring.alerts import pill

    snap["alerts"] = summary
    wanted = pill(summary.get("worst"))
    base_overall, base_reasons = (
        snap.get("base_overall", snap["overall"]),
        snap.get("base_reasons", snap["reasons"]),
    )
    snap["base_overall"], snap["base_reasons"] = base_overall, base_reasons
    if wanted and RANK[wanted] > RANK.get(base_overall, 0):
        snap["overall"] = wanted
        snap["reasons"] = [a["message"] for a in summary["recent"]] + list(base_reasons)
    else:
        snap["overall"], snap["reasons"] = base_overall, list(base_reasons)
    return snap


async def _rec() -> dict:
    from telemetry import sessions
    from telemetry.ingest import ingest_state

    session = await sessions.current()
    ingest = await ingest_state()
    if session is None:
        return {"session": None, "recording": False, "available": False, "ingest": ingest}
    return {
        "session": str(session.id),
        "recording": session.recording,
        "available": True,
        "mode": session.mode,
        "rate_hz": session.rate_hz,
        "samples": session.samples,
        "ingest": ingest,
    }


async def snapshot(runner: CommandRunner | None = None, agent=None, net=None, st=None) -> dict:
    """``agent`` / ``net``: injected by the tests; default from the settings (fake or real).
    ``st``: the stack state, when the caller has read it already (run_monitor)."""
    from asgiref.sync import sync_to_async

    from monitoring.alerts import summary

    runner = runner or get_runner()
    if st is None:
        host, st = await asyncio.gather(platform.current_host(runner), stack_state.read(runner))
    else:
        host = await platform.current_host(runner)
    current = st.stack
    comp = await health.components(st, net, agent)
    rows = {r["id"]: r for r in comp["components"]}
    jobs = await _jobs()
    overall, reasons = _overall(st)
    if overall == "OK" and jobs["transition"] is None:
        overall, reasons = health.overall(comp["components"], True)
    mode = None
    m = None
    if current:
        m, container = current
        mode = {
            "id": m.id if m else "unknown",
            "chip": m.chip if m else "ROS · ?",
            "robot": m.robot if m else "unknown",
            "rviz": m.rviz if m else False,
            "viewers": m.viewers if m else "",
            "command": m.command if m else "",
            "container": container.name,
            "state": container.state,
            "status": container.status,
        }
    arm = await _arm(m) if current else None
    agent_up = rows["agent"]["status"] == "ready"
    snap = {
        "ts": datetime.now(UTC).isoformat(),
        "fake": settings.FAKE,
        "overall": overall,
        "reasons": reasons,
        "host": host.to_dict(),
        "docker": {"reachable": st.docker, "error": st.error},
        "mode": mode,
        "transition": jobs["transition"],
        "arm": arm,
        "ros_running": mode is not None and mode["state"] == "running",
        "stop": {
            "available": agent_up,
            "reason": ""
            if agent_up
            else f"The cockpit agent is not reachable ({rows['agent']['detail']}). "
            "Use the hardware e-stop in an emergency.",
        },
        "ready": comp["ready"],
        "components": comp["components"],
        "viewers": _viewers(m, rows, host.os),
        "jobs": jobs,
        "infra": [c.to_dict() for c in st.infra],
        "banner": _banner(m),
        "dead": [
            {"name": c.name, "status": c.status, "chip": dm.chip if dm else "ROS · ?"}
            for dm, c in st.dead_stacks
        ],
        "rec": await _rec(),
    }
    return with_alerts(snap, await sync_to_async(summary)())


def _banner(mode) -> str:
    """A permanent red banner while an escape hatch is set (§9.4)."""
    import os

    if mode is not None and mode.robot == "real":
        if os.environ.get("COCKPIT_ALLOW_ARM_WITHOUT_ESTOP") == "1":
            return (
                "COCKPIT_ALLOW_ARM_WITHOUT_ESTOP=1: arming is allowed without an e-stop test. "
                "Remove it from .env once the hardware e-stop is wired."
            )
    return ""
