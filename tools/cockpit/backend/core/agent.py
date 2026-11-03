"""Client of ar3_cockpit_agent, the cockpit's adapter inside the ROS stack (spec §11.2, §16).

The agent listens on 127.0.0.1:<AGENT_PORT> (published from the container) and wants the
shared token from ``<COCKPIT_DATA_DIR>/agent.token`` on every request. The backend creates
the token on first use; the Makefile reads the same file and passes it into every stack, so
stacks started from the CLI are reachable too.

``FakeAgent`` answers from the fake world (core.fake) in COCKPIT_FAKE mode: the stack is
ready a few seconds after it started, the arm state and the e-stop live in state.json, the
joints move along sine waves (on the agent's 100 Hz grid) and /rosout has a few lines plus
the ``rosout`` lines of state.json.

Streams (``telemetry(rate)``, ``rosout(after)``) are async iterators of the agent's
WebSocket messages; they raise AgentError when the agent is not reachable or goes away.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TOKEN_FILE = "agent.token"
HEALTH_TIMEOUT_S = 2.0  # §9.2: the agent is ready when /health answers within 2 s


class AgentError(Exception):
    def __init__(self, status: int, title: str, detail: str = ""):
        super().__init__(f"{status} {title}" + (f": {detail}" if detail else ""))
        self.status, self.title, self.detail = status, title, detail


def token(data_dir: Path) -> str:
    """The shared agent token, created (mode 0600) on first use."""
    path = Path(data_dir) / TOKEN_FILE
    if path.exists():
        return path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(value + "\n")
    return value


@dataclass
class AgentClient:
    base_url: str
    token: str

    async def request(
        self,
        method: str,
        path: str,
        timeout: float = 5.0,
        body: dict | None = None,
        raw: bool = False,
    ) -> Any:
        def call():
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(self.base_url + path, data=data, method=method)
            req.add_header("Authorization", f"Bearer {self.token}")
            if data is not None:
                req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                    data = resp.read()
                    return data if raw else json.loads(data or b"null")
            except urllib.error.HTTPError as exc:
                try:
                    problem = json.loads(exc.read() or b"{}")
                except ValueError:
                    problem = {}
                raise AgentError(
                    exc.code, problem.get("title", exc.reason), problem.get("detail", "")
                ) from None
            except (OSError, ValueError) as exc:
                raise AgentError(0, "Agent not reachable", str(exc)) from None

        return await asyncio.to_thread(call)

    async def health(self) -> dict:
        return await self.request("GET", "/health", timeout=HEALTH_TIMEOUT_S)

    async def capabilities(self) -> dict:
        return await self.request("GET", "/capabilities")

    async def joints(self) -> dict:
        return await self.request("GET", "/joints")

    async def stop(self) -> dict:
        return await self.request("POST", "/stop", timeout=10.0)

    async def arm_state(self) -> dict:
        return await self.request("GET", "/arm/state")

    async def home(self) -> dict:
        return await self.request("POST", "/arm/home", timeout=120.0)

    async def reset_estop(self) -> dict:
        return await self.request("POST", "/arm/reset_estop")

    async def arm(self, armed: bool) -> dict:
        raise AgentError(501, "Arming not available", "Needs ar3_safety (SPEC Phase 1).")

    async def graph_detail(self) -> dict:
        return await self.request("GET", "/graph/detail", timeout=10.0)

    async def node_params(self, node: str) -> dict:
        q = urllib.parse.urlencode({"name": node})
        return await self.request("GET", f"/node/params?{q}", timeout=10.0)

    async def topic_rate(self, topic: str, seconds: float = 2.0) -> dict:
        q = urllib.parse.urlencode({"name": topic, "seconds": seconds})
        return await self.request("GET", f"/topic/rate?{q}", timeout=seconds + 5)

    async def topic_rates(self) -> dict:
        return await self.request("GET", "/topics/rates")

    async def cameras(self) -> list[str]:
        return await self.request("GET", "/cameras")

    async def thumbnail(self, camera: str) -> bytes:
        name = urllib.parse.quote(camera, safe="")
        return await self.request("GET", f"/cameras/{name}/thumb.jpg", raw=True)

    async def _stream(self, path: str):
        from websockets.asyncio.client import connect
        from websockets.exceptions import InvalidStatus, WebSocketException

        url = "ws" + self.base_url.removeprefix("http") + path
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            async with connect(
                url, additional_headers=headers, open_timeout=3, max_size=8 * 1024 * 1024
            ) as ws:
                async for raw in ws:
                    yield json.loads(raw)
        except InvalidStatus as exc:
            code = exc.response.status_code
            raise AgentError(code, "Agent refused the stream") from None
        except (OSError, TimeoutError, WebSocketException) as exc:
            raise AgentError(0, "Agent stream lost", str(exc) or type(exc).__name__) from None

    def telemetry(self, rate: int):
        return self._stream(f"/ws/telemetry?rate={int(rate)}")

    # scenario runs (§12.4)
    async def ik_check(self, targets: list[dict]) -> dict:
        return await self.request("POST", "/ik/check", timeout=30.0, body={"targets": targets})

    async def scene_clear(self) -> dict:
        return await self.request("POST", "/scene/clear", timeout=20.0, body={})

    async def run_start(self, run_id: str, scenario: dict, options: dict) -> dict:
        body = {"run_id": run_id, "scenario": scenario, "options": options}
        return await self.request("POST", "/run", timeout=10.0, body=body)

    async def run_state(self, run_id: str) -> dict:
        return await self.request("GET", f"/run/{run_id}")

    async def run_abort(self, run_id: str) -> dict:
        return await self.request("POST", f"/run/{run_id}/abort", timeout=15.0, body={})

    async def run_pause(self, run_id: str, paused: bool) -> dict:
        return await self.request("POST", f"/run/{run_id}/pause", body={"paused": paused})

    def run_events(self, run_id: str, after: int = 0):
        return self._stream(f"/ws/run/{run_id}?after={int(after)}")

    def rosout(self, after: int = 0):
        return self._stream(f"/ws/rosout?after={int(after)}")


# --- fake mode --------------------------------------------------------------------------

FAKE_READY_AFTER_S = 3.0
FAKE_NAMES = [f"joint_{i}" for i in range(1, 7)] + ["gripper_jaw1_joint"]
FAKE_JOINTS = {"name": FAKE_NAMES, "position": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]}
FAKE_BASE_HZ = 100  # the real agent's sampling grid (ar3_cockpit_agent.telemetry.BASE_HZ)
FAKE_PERIOD_S = (10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 8.0)  # sine period per joint
FAKE_AMPLITUDE = (0.6, 0.4, 0.4, 0.8, 0.5, 0.9, 0.006)
FAKE_LAG_S = 0.05  # the "actual" trails the commanded position by this much
FAKE_GRAPH_NODES = [
    "/ar3_cockpit_agent",
    "/controller_manager",
    "/foxglove_bridge",
    "/joint_state_broadcaster",
    "/joint_trajectory_controller",
    "/gripper_controller",
    "/move_group",
    "/robot_state_publisher",
]
FAKE_GRAPH_TOPICS = [
    ("/joint_states", "sensor_msgs/msg/JointState", ["/joint_state_broadcaster"],
     ["/move_group", "/robot_state_publisher", "/ar3_cockpit_agent", "/foxglove_bridge"]),
    ("/joint_trajectory_controller/controller_state",
     "control_msgs/msg/JointTrajectoryControllerState", ["/joint_trajectory_controller"],
     ["/ar3_cockpit_agent"]),
    ("/display_planned_path", "moveit_msgs/msg/DisplayTrajectory", ["/move_group"],
     ["/ar3_cockpit_agent", "/foxglove_bridge"]),
    ("/rosout", "rcl_interfaces/msg/Log", FAKE_GRAPH_NODES, ["/ar3_cockpit_agent"]),
    ("/tf", "tf2_msgs/msg/TFMessage", ["/robot_state_publisher"], ["/move_group"]),
]  # fmt: skip
FAKE_STEP_S = 0.4
FAKE_BASE_XY = (-0.32, 0.0)  # config/robot.yaml mount
FAKE_REACH_M = 0.68
FAKE_ROSOUT = [
    ("INFO", "controller_manager", "Loaded joint_trajectory_controller"),
    ("INFO", "move_group", "MoveGroup context initialization complete"),
    ("INFO", "move_group", "You can start planning now!"),
]


def fake_positions(t: float) -> list[float]:
    return [
        a * math.sin(2 * math.pi * t / period)
        for a, period in zip(FAKE_AMPLITUDE, FAKE_PERIOD_S, strict=True)
    ]


def fake_velocities(t: float) -> list[float]:
    return [
        a * 2 * math.pi / period * math.cos(2 * math.pi * t / period)
        for a, period in zip(FAKE_AMPLITUDE, FAKE_PERIOD_S, strict=True)
    ]


@dataclass
class FakeAgent:
    """The agent of the fake world's stack. ``stack.started`` (epoch s) is set by the fake
    start scripts; the stack reports ready FAKE_READY_AFTER_S later."""

    data_dir: Path

    def _state(self) -> dict:
        from . import fake

        return fake.load_state(self.data_dir)

    def _save(self, state: dict) -> None:
        from . import fake

        fake.save_state(self.data_dir, state)

    def _stack(self) -> dict:
        stack = self._state().get("stack")
        if not stack or stack.get("state", "running") != "running":
            raise AgentError(0, "Agent not reachable", "no stack running (fake)")
        if stack.get("agent") is False:
            raise AgentError(0, "Agent not reachable", "agent down (fake)")
        return stack

    async def health(self) -> dict:
        stack = self._stack()
        mode = {"sim": "sim", "mock": "mock", "real": "real", "remote": "remote"}.get(
            stack.get("ar3_mode") or "", "mock"
        )
        age = time.time() - float(stack.get("started", 0))
        ready = age >= FAKE_READY_AFTER_S

        def comp(applies: bool, detail: str) -> dict:
            if not applies:
                return {"status": "off", "detail": "not in this mode"}
            return {
                "status": "ready" if ready else "waiting",
                "detail": detail if ready else "starting",
            }

        components = {
            "gazebo": comp(mode == "sim", "/clock 1000 Hz"),
            "controllers": comp(True, "3 controllers active"),
            "move_group": comp(True, "/move_action available"),
            "cameras": comp(mode == "sim", "cam_overhead 15.0/15 Hz, cam_wrist 15.0/15 Hz"),
            "safety": comp(mode == "real", "estop released"),
        }
        waiting = [n for n, c in components.items() if c["status"] == "waiting" and n != "cameras"]
        return {
            "ok": True,
            "mode": mode,
            "host": "fake",
            "uptime_s": round(age, 1),
            "ready": not waiting,
            "waiting_for": waiting,
            "components": components,
        }

    async def capabilities(self) -> dict:
        stack = self._stack()
        state = self._state()
        if stack.get("ar3_mode") == "remote":
            allowed = bool(state.get("remote_motion"))
            motion = {
                "allowed": allowed,
                "reason": "allowed by ws-a (fake)"
                if allowed
                else "the workstation does not allow motion from remote clients "
                "(COCKPIT_REMOTE_MOTION=1 in its .env)",
            }
        elif stack.get("ar3_mode") == "real":
            armed = state.get("arm") == "ARMED"
            motion = {"allowed": armed, "reason": "armed" if armed else "REAL: needs ARMED"}
        else:
            motion = {"allowed": True, "reason": "simulation"}
        return {"version": "fake", "mode": stack.get("ar3_mode"), "motion": motion, "stop": True,
                "task_types": ["sequence"], "perception": [],
                "gazebo": stack.get("ar3_mode") == "sim"}  # fmt: skip

    async def joints(self) -> dict:
        self._stack()
        return {"name": FAKE_NAMES, "position": fake_positions(time.time())}

    async def stop(self) -> dict:
        self._stack()
        state = self._state()
        state["stops"] = state.get("stops", 0) + 1
        self._save(state)
        return {"cancelled": [{"action": "/move_action", "goals": 0}], "held": [], "errors": []}

    async def arm_state(self) -> dict:
        """Like ar3_safety will: E_STOP while the e-stop is pressed."""
        self._stack()
        state = self._state()
        estop = bool(state.get("estop"))
        return {"arm": "E_STOP" if estop else state.get("arm", "NOT_HOMED"), "estop": estop}

    async def home(self) -> dict:
        self._stack()
        state = self._state()
        if state.get("estop"):
            raise AgentError(409, "E-stop active", "release and reset the e-stop first")
        await asyncio.sleep(0.5)
        state["arm"] = "HOMED"
        self._save(state)
        return {"arm": "HOMED"}

    async def reset_estop(self) -> dict:
        self._stack()
        state = self._state()
        state["estop"] = False
        state["arm"] = "NOT_HOMED"  # after an e-stop the arm must be homed again
        self._save(state)
        return {"estop": False}

    async def arm(self, armed: bool) -> dict:
        self._stack()
        state = self._state()
        state["arm"] = "ARMED" if armed else "HOMED"
        self._save(state)
        return {"arm": state["arm"]}

    def _sim(self) -> bool:
        return self._stack().get("ar3_mode") == "sim"

    async def graph_detail(self) -> dict:
        self._stack()
        topics = [
            {"name": n, "types": [t], "publishers": sorted(p), "subscribers": sorted(sub)}
            for n, t, p, sub in FAKE_GRAPH_TOPICS
        ]
        if self._sim():
            topics.append(
                {"name": "/clock", "types": ["rosgraph_msgs/msg/Clock"],
                 "publishers": ["/ros_gz_bridge"], "subscribers": ["/move_group"]}
            )  # fmt: skip
        return {
            "nodes": FAKE_GRAPH_NODES,
            "topics": topics,
            "services": [{"name": "/controller_manager/list_controllers",
                          "types": ["controller_manager_msgs/srv/ListControllers"]}],
            "actions": [{"name": "/move_action", "types": ["moveit_msgs/action/MoveGroup"]}],
        }  # fmt: skip

    async def node_params(self, node: str) -> dict:
        self._stack()
        node = "/" + node.strip("/")
        if node not in FAKE_GRAPH_NODES:
            raise AgentError(404, "No such node", node)
        return {
            "node": node,
            "params": [
                {"name": "use_sim_time", "value": self._sim()},
                {"name": "update_rate", "value": 100} if node == "/controller_manager" else
                {"name": "planning_plugin", "value": "ompl_interface/OMPLPlanner"},
            ],
        }  # fmt: skip

    def _rates(self) -> dict:
        rates = {"/joint_states": {"hz": 100.0, "age_s": 0.01}}
        rates["/clock"] = (
            {"hz": 1000.0, "age_s": 0.001} if self._sim() else {"hz": 0.0, "age_s": None}
        )
        stale = self._state().get("stale_topics", [])
        for topic in stale:
            rates[topic] = {"hz": 0.0, "age_s": 5.0}
        return rates

    async def topic_rate(self, topic: str, seconds: float = 2.0) -> dict:
        rates = self._rates()
        if topic not in rates and topic not in [t[0] for t in FAKE_GRAPH_TOPICS]:
            raise AgentError(404, "No such topic", topic)
        hz = rates.get(topic, {"hz": 10.0})["hz"]
        return {"topic": topic, "hz": hz, "messages": int(hz * seconds), "window_s": seconds}

    async def topic_rates(self) -> dict:
        self._stack()
        return self._rates()

    async def cameras(self) -> list[str]:
        return ["cam_overhead", "cam_wrist"] if self._sim() else []

    async def thumbnail(self, camera: str) -> bytes:
        if camera not in await self.cameras():
            raise AgentError(404, "No such camera", camera)
        return (Path(__file__).parent / "fake_camera.jpg").read_bytes()

    async def telemetry(self, rate: int):
        """The agent's stream: every 100 ms the grid samples of that interval."""
        if FAKE_BASE_HZ % int(rate):
            raise AgentError(400, "rate must divide 100")
        every = FAKE_BASE_HZ // int(rate)
        self._stack()
        tick = math.floor(time.time() * FAKE_BASE_HZ)
        last_rates = 0.0
        while True:
            await asyncio.sleep(0.1)
            self._stack()  # raises when the stack is gone
            state = self._state()
            now_tick = math.floor(time.time() * FAKE_BASE_HZ)
            samples = []
            for t in range(tick + 1, now_tick + 1):
                if t % every:
                    continue
                stamp = t / FAKE_BASE_HZ
                samples.append(
                    {
                        "t": round(stamp, 3),
                        "p": [round(x, 6) for x in fake_positions(stamp - FAKE_LAG_S)],
                        "v": [round(x, 6) for x in fake_velocities(stamp - FAKE_LAG_S)],
                        "e": [0.0] * len(FAKE_NAMES),
                        "cp": [round(x, 6) for x in fake_positions(stamp)][:6] + [None],
                        "cv": [round(x, 6) for x in fake_velocities(stamp)][:6] + [None],
                        **({"s": {"estop": bool(state.get("estop")), "homed": True,
                                  "in_workspace": True, "faults": []}}
                           if self._stack().get("ar3_mode") == "real" else {}),
                    }
                )  # fmt: skip
            tick = now_tick
            msg = {
                "type": "telemetry",
                "rate": rate,
                "names": FAKE_NAMES,
                "samples": samples,
                "events": [],
                "dropped": 0,
                "skipped": 0,
            }
            if time.monotonic() - last_rates >= 1.0:
                last_rates = time.monotonic()
                msg["rates"] = self._rates()
                msg["rtf"] = 1.0 if self._sim() else None
            yield msg

    # --- scenario runs: steps take FAKE_STEP_S each (state.json ``run_step_s``); state.json's
    # ``run_fail_step`` fails that step (0-based); poses farther than FAKE_REACH_M from the
    # base are unreachable.

    def _runs(self) -> dict:
        return self._state().get("runs", {})

    def _save_run(self, run: dict) -> None:
        state = self._state()
        state.setdefault("runs", {})[run["id"]] = run
        self._save(state)

    async def ik_check(self, targets: list[dict]) -> dict:
        self._stack()
        out = []
        for t in targets:
            r = math.hypot(t["x"] - FAKE_BASE_XY[0], t["y"] - FAKE_BASE_XY[1])
            ok = r <= FAKE_REACH_M and t["z"] > 0
            out.append({**t, "reachable": ok, "error": "" if ok else "NO_IK_SOLUTION"})
        return {"targets": out}

    async def scene_clear(self) -> dict:
        self._stack()
        return {"planning_scene": [], "gazebo": []}

    async def run_start(self, run_id: str, scenario: dict, options: dict) -> dict:
        stack = self._stack()
        if any(r["status"] in ("running", "paused") for r in self._runs().values()):
            raise AgentError(409, "A run is active")
        from . import fake

        steps = scenario.get("task", {}).get("steps", [])
        spawned = [o for o in scenario.get("scene", {}).get("objects", []) if o.get("spawn")]
        run = {
            "id": run_id,
            "status": "running",
            "started": time.time(),
            "steps": [{"index": i, "label": fake.step_label(s), "status": "pending"}
                      for i, s in enumerate(steps)],
            "sim": stack.get("ar3_mode") == "sim",
            "objects": {o["id"]: o["pose"] for o in spawned},
            "fail_step": self._state().get("run_fail_step"),
            "paused": False,
            "aborted": False,
        }  # fmt: skip
        self._save_run(run)
        return {"id": run_id, "status": "running", "steps": run["steps"]}

    async def run_state(self, run_id: str) -> dict:
        run = self._runs().get(run_id)
        if run is None:
            raise AgentError(404, "No such run", run_id)
        return run

    async def run_abort(self, run_id: str) -> dict:
        run = await self.run_state(run_id)
        run["aborted"] = True
        self._save_run(run)
        return run

    async def run_pause(self, run_id: str, paused: bool) -> dict:
        run = await self.run_state(run_id)
        run["paused"] = paused
        self._save_run(run)
        return run

    async def run_events(self, run_id: str, after: int = 0):
        """The fake run itself: one step every FAKE_STEP_S, honouring pause and abort."""
        run = await self.run_state(run_id)
        seq = 0

        def ev(kind, **data):
            nonlocal seq
            seq += 1
            return {"type": "run", "seq": seq, "events": [{"kind": kind, "t": time.time(), **data}],
                    "state": {"id": run_id, "status": run["status"], "steps": run["steps"],
                              "result": run.get("result", {})}}  # fmt: skip

        yield ev("log", level="INFO", text="reset: removed 0 scene object(s), 0 Gazebo model(s)")
        yield ev("log", level="INFO", text=f"scene: {len(run['objects'])} spawned (fake)")
        result, error, failed = "succeeded", "", None
        for i, st in enumerate(run["steps"]):
            while (await self.run_state(run_id))["paused"]:
                await asyncio.sleep(0.1)
            if (await self.run_state(run_id))["aborted"]:
                result, error, failed = "aborted", "aborted by the user", i
                st["status"] = "aborted"
                break
            st["status"] = "running"
            yield ev("step", index=i, status="running", label=st["label"])
            step_s = float(self._state().get("run_step_s", FAKE_STEP_S))
            end = time.monotonic() + step_s
            while time.monotonic() < end and not (await self.run_state(run_id))["aborted"]:
                await asyncio.sleep(0.05)
            if run["fail_step"] == i:
                st.update(status="failed", error="planning failed: PLANNING_FAILED")
                result, error, failed = "failed", "planning failed: PLANNING_FAILED", i
                yield ev("step", index=i, status="failed", label=st["label"], error=error)
                break
            st.update(status="succeeded", planning_s=0.05, execution_s=FAKE_STEP_S,
                      duration_s=FAKE_STEP_S)  # fmt: skip
            yield ev("step", index=i, status="succeeded", label=st["label"], planning_s=0.05,
                     execution_s=FAKE_STEP_S, duration_s=FAKE_STEP_S)  # fmt: skip
        poses = {}
        if run["sim"]:
            poses = {oid: {"xyz": [p["x"], p["y"], 0.015], "quat": [0, 0, 0, 1]}
                     for oid, p in run["objects"].items()}  # fmt: skip
        run["status"] = result
        result_doc = {"result": result, "error": error, "failed_step": failed,
                      "duration_s": round(time.time() - run["started"], 3),
                      "object_poses": poses, "steps": run["steps"], "bag": ""}  # fmt: skip
        run["result"] = result_doc
        self._save_run(run)
        yield ev("finished", **result_doc)

    async def rosout(self, after: int = 0):
        """The startup lines, then state.json's ``rosout`` lines ([level, node, text])."""
        stack = self._stack()
        seq = after
        while True:
            lines = FAKE_ROSOUT + [tuple(x) for x in self._state().get("rosout", [])]
            new = lines[seq:]
            if new:
                started = float(stack.get("started", time.time()))
                out = [
                    {"t": started + i, "level": lvl, "node": node, "text": text}
                    for i, (lvl, node, text) in enumerate(new, start=seq)
                ]
                seq = len(lines)
                yield {"type": "rosout", "seq": seq, "lines": out}
            await asyncio.sleep(0.2)
            stack = self._stack()


def get_agent():
    from django.conf import settings

    if settings.FAKE:
        return FakeAgent(Path(settings.DATA_DIR))
    port = int(os.environ.get("AGENT_PORT", "8790"))
    return AgentClient(f"http://127.0.0.1:{port}", token(settings.DATA_DIR))
