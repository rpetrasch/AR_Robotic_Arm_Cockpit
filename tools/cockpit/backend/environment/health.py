"""Component health (docs/specs/spec_cockpit.md §9.2): what runs, and is it ready?

Sources: Docker (the stack container, Lichtblick web, the cockpit's DB and Redis), the agent
(/health: Gazebo clock, controllers, move_group, cameras, safety), TCP / HTTP probes
(foxglove_bridge, Lichtblick web, noVNC, zenohd, Ollama). run_monitor computes it every 2 s
as part of the status snapshot.

Status values, as in the agent: ``ready``, ``waiting``, ``warn``, ``error``, ``off`` (not
part of the running mode), ``unknown`` (no source to tell, e.g. the agent is down), and
``down`` for a container that exited abnormally or keeps restarting (environment.state).
"""

from __future__ import annotations

import asyncio
import os
import time

from django.conf import settings

from core.agent import AgentError, get_agent
from core.net import FakeNet, Net

from . import state as stack_state
from .modes import Mode

READY_TIMEOUT_S = 180.0  # §9.1: a start is done when ready, or fails after 180 s
POLL_S = 1.0
AGENT_COMPONENTS = ("gazebo", "controllers", "move_group", "cameras", "safety")
# Shown, but not counted against the header pill: software rendering runs the cameras slower
# than configured on a Mac, and Ollama is optional.
INFO = ("cameras", "ollama")


def _ports() -> dict[str, int]:
    return {
        "foxglove": int(os.environ.get("FOXGLOVE_PORT", "8765")),
        "novnc": int(os.environ.get("NOVNC_PORT", "6080")),
        "lichtblick": int(os.environ.get("LICHTBLICK_PORT", "8080")),
    }


def get_net() -> Net:
    if settings.FAKE:
        from core import fake

        state = fake.load_state(settings.DATA_DIR)
        busy = set(state.get("ports_busy", []))
        stack = state.get("stack")
        ports = _ports()
        if stack and stack.get("state", "running") == "running":
            if stack.get("ar3_mode") != "remote":
                busy.add(ports["foxglove"])
            if stack.get("service") == "vnc":
                busy.add(ports["novnc"])
        if state.get("lichtblick"):
            busy.add(ports["lichtblick"])
        return FakeNet(busy=busy, ollama_up=state["ollama"]["up"])
    return Net()


def _c(cid: str, status: str, detail: str, **extra) -> dict:
    return {"id": cid, "status": status, "detail": detail, **extra}


async def _http_ok(net: Net, port: int, path: str = "/") -> bool:
    if isinstance(net, FakeNet):
        return await net.tcp_open("127.0.0.1", port)
    status, _body = await _http_status(f"http://127.0.0.1:{port}{path}")
    return status == 200


async def _http_status(url: str, timeout: float = 2.0) -> tuple[int, None]:
    import urllib.error
    import urllib.request

    def get():
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (localhost)
                return resp.status, None
        except urllib.error.HTTPError as exc:
            return exc.code, None
        except (OSError, ValueError):
            return 0, None

    return await asyncio.to_thread(get)


def _zenoh_router() -> tuple[str, int] | None:
    from setup.checks.stack import _dotenv
    from setup.executor import repo_for_jobs

    values = _dotenv(repo_for_jobs() / "docker/.env")  # the fake checkout in fake mode
    if values.get("ROS_RMW") != "rmw_zenoh_cpp" or not values.get("ZENOH_ROUTER"):
        return None
    return values["ZENOH_ROUTER"], int(values.get("ZENOH_PORT", "7447"))


async def components(st: stack_state.StackState, net: Net | None = None, agent=None) -> dict:
    """The §9.2 table plus the agent's raw health (or its error)."""
    net = net or get_net()
    agent = agent or get_agent()
    ports = _ports()
    current = st.stack
    mode: Mode | None = current[0] if current else None
    container = current[1] if current else None
    running = container is not None and container.state == "running"
    service = container.service if container else None
    remote = mode is not None and mode.robot == "remote"

    rows = []
    dead = st.dead_stacks[0][1] if st.dead_stacks else None
    if container is None and dead is not None:
        rows.append(_c("ros", "down", f"{dead.name}: {dead.status}"))
    elif container is None:
        rows.append(_c("ros", "off", "no ROS stack running"))
    elif running:
        rows.append(_c("ros", "ready", f"{container.name}: {container.status}"))
    else:
        rows.append(_c("ros", "down", f"{container.name} is {container.state}"))

    agent_health, agent_error = None, ""
    if running:
        started = time.monotonic()
        try:
            agent_health = await agent.health()
            rows.append(
                _c("agent", "ready", f"/health in {1000 * (time.monotonic() - started):.0f} ms")
            )
        except AgentError as exc:
            agent_error = exc.detail or exc.title
            status = "error" if exc.status in (401, 503) else "waiting"
            hint = ""
            if exc.status == 401:
                hint = " (token mismatch: restart the stack from the cockpit, or `make down` "
                "and start it again)"
            rows.append(_c("agent", status, f"{exc.title}{hint}"))
    else:
        rows.append(_c("agent", "off", "no ROS stack running"))

    agent_rows = (agent_health or {}).get("components", {})
    for cid in AGENT_COMPONENTS:
        if not running:
            rows.append(_c(cid, "off", "no ROS stack running"))
        elif cid in agent_rows:
            row = agent_rows[cid]
            rows.append(_c(cid, row["status"], row["detail"]))
        else:
            rows.append(_c(cid, "unknown", "the agent is not reachable"))

    if running and not remote:
        fox = await net.tcp_open("127.0.0.1", ports["foxglove"])
        rows.append(
            _c("foxglove_bridge", "ready" if fox else "waiting", f"TCP :{ports['foxglove']}")
        )
    else:
        rows.append(_c("foxglove_bridge", "off", "runs in the ROS stack"))

    web = await _http_ok(net, ports["lichtblick"])
    lichtblick_up = any(c.service == "lichtblick" and c.state == "running" for c in st.containers)
    lichtblick_dead = next((c for c in st.containers if c.service == "lichtblick" and c.dead), None)
    if lichtblick_dead is not None and not lichtblick_up:
        rows.append(
            _c("lichtblick_web", "down", f"{lichtblick_dead.name}: {lichtblick_dead.status}")
        )
    elif web:
        rows.append(_c("lichtblick_web", "ready", f"HTTP :{ports['lichtblick']}"))
    elif lichtblick_up:
        rows.append(_c("lichtblick_web", "waiting", f"container up, :{ports['lichtblick']} silent"))
    else:
        rows.append(_c("lichtblick_web", "off", "not started (make lichtblick)"))

    if running and service == "vnc":
        vnc = await _http_ok(net, ports["novnc"], "/vnc.html")
        rows.append(_c("novnc", "ready" if vnc else "waiting", f"HTTP :{ports['novnc']}"))
    else:
        rows.append(_c("novnc", "off", "only in the vnc stack (make vnc)"))

    router = _zenoh_router()
    if router is None:
        rows.append(_c("zenohd", "off", "not configured (docker/.env)"))
    else:
        host, port = router
        ok = await net.tcp_open(host, port, timeout=1.0)
        rows.append(
            _c("zenohd", "ready" if ok else ("error" if remote else "warn"), f"tcp/{host}:{port}")
        )

    infra = {c.service: c for c in st.infra}
    for cid, service_name in (("cockpit_db", "cockpit-db"), ("cockpit_redis", "cockpit-redis")):
        c = infra.get(service_name)
        if c is None:
            rows.append(_c(cid, "off", "not a compose container here (external service)"))
        else:
            ok = c.state == "running"
            rows.append(_c(cid, "ready" if ok else "down", c.status))

    base = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    code, _tags = await net.http_json(base + "/api/tags", timeout=1.0)
    rows.append(_c("ollama", "ready" if code == 200 else "off", base))

    return {
        "components": rows,
        "agent": agent_health,
        "agent_error": agent_error,
        "ready": bool(agent_health and agent_health.get("ready")),
    }


def overall(rows: list[dict], stack_running: bool) -> tuple[str, list[str]]:
    """The pill's contribution of component health (§7.2): worst of the rows that matter."""
    down = [r for r in rows if r["status"] == "down"]
    if not stack_running:
        return ("ERROR", [f"{r['id']}: {r['detail']}" for r in down]) if down else ("OK", [])
    bad = [r for r in rows if r["status"] in ("error", "down")]
    if bad:
        return "ERROR", [f"{r['id']}: {r['detail']}" for r in bad]
    warn = [
        r for r in rows if r["status"] in ("waiting", "warn", "unknown") and r["id"] not in INFO
    ]
    if warn:
        return "WARN", [f"{r['id']}: {r['detail']}" for r in warn]
    return "OK", []


# --- used by the env.start / env.stop jobs ------------------------------------------------


async def wait_ready(mode_id: str, log, cancel: asyncio.Event | None = None) -> int:
    """After `make <target> DETACH=1`: wait until the agent says the stack is ready."""
    from core.runner import CANCELLED, TIMED_OUT

    agent = get_agent()
    remote = mode_id == "remote"
    deadline = time.monotonic() + READY_TIMEOUT_S
    last = None
    await log("out", f"waiting for the stack to be ready (at most {READY_TIMEOUT_S:.0f} s)…")
    while time.monotonic() < deadline:
        if cancel is not None and cancel.is_set():
            await log("err", "cancelled (the stack keeps running; Stop stops it)")
            return CANCELLED
        try:
            health = await agent.health()
            if remote or health.get("ready"):
                parts = ", ".join(
                    f"{k}: {v['detail']}"
                    for k, v in health.get("components", {}).items()
                    if v["status"] == "ready"
                )
                await log("out", f"ready. {parts}")
                return 0
            now = "waiting for " + ", ".join(
                f"{n} ({health['components'][n]['detail']})" for n in health.get("waiting_for", [])
            )
        except AgentError as exc:
            now = f"waiting for the agent ({exc.title})"
            if exc.status == 401:
                await log("err", "the agent refused the token: " + exc.title)
                return 1
        if now != last:
            await log("out", now)
            last = now
        await asyncio.sleep(POLL_S)
    await log("err", f"not ready after {READY_TIMEOUT_S:.0f} s: {last}")
    return TIMED_OUT


async def wait_stopped(log, cancel: asyncio.Event | None = None, timeout: float = 30.0) -> int:
    from core.runner import get_runner

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = await stack_state.read(get_runner())
        live = [c for _m, c in st.stacks if c.service not in ("dev", "dev-gpu")]
        if not live:
            await log("out", "stopped.")
            return 0
        await asyncio.sleep(POLL_S)
    await log("err", "a stack container is still running")
    return 1
