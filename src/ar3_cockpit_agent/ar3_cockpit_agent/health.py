"""Component health and motion policy of the cockpit agent (docs/specs/spec_cockpit.md §9.2, §9.5).

Pure functions over what the node observed, so they are unit-tested without ROS. Each
component is ``{"status", "detail"}`` with status:

- ``ready``: works;
- ``waiting``: expected in this mode but not (yet) there, e.g. while the stack starts;
- ``warn``: there, but below expectations (camera rate);
- ``off``: not part of this mode.

The stack is ready when every required component (Gazebo in sim, controllers, move_group)
is ``ready``. Cameras are informational: software rendering on a Mac runs them slower.
"""

from __future__ import annotations

from collections import deque

MODES = ("mock", "sim", "real", "remote")
REQUIRED_CONTROLLERS = (
    "joint_state_broadcaster",
    "joint_trajectory_controller",
    "gripper_controller",
)
CLOCK_MIN_HZ = 50.0  # §9.2: Gazebo ready at > 50 Hz of advancing sim time
CAMERA_MIN_FRACTION = 0.8  # §9.2: ≥ configured fps × 0.8
ARMED = "ARMED"


class RateMeter:
    """Message rate over a sliding window (seconds of the caller's monotonic clock)."""

    def __init__(self, window_s: float = 2.0):
        self.window_s = window_s
        self._stamps: deque[float] = deque()

    def tick(self, now: float) -> None:
        self._stamps.append(now)
        self._trim(now)

    def rate(self, now: float) -> float:
        self._trim(now)
        if len(self._stamps) < 2:
            return 0.0
        span = now - self._stamps[0]
        return (len(self._stamps) - 1) / span if span > 0 else 0.0

    def _trim(self, now: float) -> None:
        while self._stamps and now - self._stamps[0] > self.window_s:
            self._stamps.popleft()


def component(status: str, detail: str, **extra) -> dict:
    return {"status": status, "detail": detail, **extra}


def gazebo(mode: str, clock_hz: float, advancing: bool) -> dict:
    if mode not in ("sim", "remote"):
        return component("off", "no Gazebo in this mode")
    if mode == "remote" and clock_hz == 0:
        return component("off", "no /clock in the remote graph")
    if clock_hz > CLOCK_MIN_HZ and advancing:
        return component("ready", f"/clock {clock_hz:.0f} Hz")
    if clock_hz == 0:
        return component("waiting", "no /clock yet")
    if not advancing:
        return component("waiting", f"/clock {clock_hz:.0f} Hz, sim time not advancing")
    return component("waiting", f"/clock {clock_hz:.0f} Hz (needs > {CLOCK_MIN_HZ:.0f})")


def controllers(listed: list[dict] | None) -> dict:
    """``listed``: [{"name", "state"}] from list_controllers, None if the service is missing."""
    if listed is None:
        return component("waiting", "controller_manager not reachable", controllers=[])
    states = {c["name"]: c["state"] for c in listed}
    missing = [n for n in REQUIRED_CONTROLLERS if states.get(n) != "active"]
    if missing:
        detail = ", ".join(f"{n} {states.get(n, 'missing')}" for n in missing)
        return component("waiting", detail, controllers=listed)
    return component("ready", f"{len(REQUIRED_CONTROLLERS)} controllers active", controllers=listed)


def move_group(node_present: bool, action_available: bool) -> dict:
    if not node_present:
        return component("waiting", "no move_group node")
    if not action_available:
        return component("waiting", "move_group has no /move_action server yet")
    return component("ready", "/move_action available")


def cameras(mode: str, rates: dict[str, float], expected: dict[str, float]) -> dict:
    """Rates of the cameras' camera_info topics against the configured rates."""
    if mode != "sim" or not expected:
        return component("off", "no cameras in this mode", rates={})
    slow = {name: hz for name, hz in rates.items() if hz < expected[name] * CAMERA_MIN_FRACTION}
    shown = ", ".join(f"{n} {rates[n]:.1f}/{expected[n]:g} Hz" for n in expected)
    if all(hz == 0 for hz in rates.values()):
        return component("waiting", f"no images yet ({shown})", rates=rates)
    if slow:
        return component("warn", shown, rates=rates)
    return component("ready", shown, rates=rates)


def safety(mode: str) -> dict:
    if mode != "real":
        return component("off", "REAL only")
    return component("waiting", "ar3_safety arrives with SPEC Phase 1")


REQUIRED = {
    "mock": ("controllers", "move_group"),
    "sim": ("gazebo", "controllers", "move_group"),
    "real": ("controllers", "move_group", "safety"),
    "remote": ("controllers", "move_group"),
}


def summary(mode: str, components: dict[str, dict]) -> dict:
    waiting = [n for n in REQUIRED[mode] if components[n]["status"] != "ready"]
    return {
        "ready": not waiting,
        "waiting_for": waiting,
        "components": components,
    }


def motion_policy(mode: str, owner: dict | None, arm: str | None) -> dict:
    """May this agent send motion? (§9.5, §11.2)

    ``owner``: the policy published on /cockpit/policy by the agent of the stack that owns
    the robot (only consulted in remote mode). ``arm``: the arm state in REAL.
    """
    if mode in ("mock", "sim"):
        return {"allowed": True, "reason": "simulation"}
    if mode == "real":
        if arm == ARMED:
            return {"allowed": True, "reason": "armed"}
        return {"allowed": False, "reason": "REAL: motion needs ARMED (pre-flight wizard)"}
    if owner is None:
        return {
            "allowed": False,
            "reason": "no /cockpit/policy from the workstation's agent (is its stack running?)",
        }
    if not owner.get("remote_motion"):
        return {
            "allowed": False,
            "reason": "the workstation does not allow motion from remote clients "
            "(COCKPIT_REMOTE_MOTION=1 in its .env)",
        }
    if owner.get("mode") == "real" and owner.get("arm") != ARMED:
        return {"allowed": False, "reason": "the real arm on the workstation is not ARMED"}
    return {"allowed": True, "reason": f"allowed by {owner.get('host', 'the workstation')}"}


def env_flag(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")
