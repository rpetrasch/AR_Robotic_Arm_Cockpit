"""REAL mode: the pre-flight wizard and the arm state (docs/specs/spec_cockpit.md §9.4).

The arm state itself belongs to ar3_safety (SPEC Phase 1); the agent reports it. The cockpit
keeps the wizard's progress per stack run (``Preflight``) and only asks the agent to arm when
every step passed. Until Phase 1 the real agent answers 501 to everything arm-related, so the
wizard shows those steps as failed with the reason; fake mode (COCKPIT_FAKE_PLATFORM=
ubuntu-real) simulates the e-stop, homing and arming.

Rules:
- the steps run in order; a step needs every step before it passed (or, for the e-stop test,
  overridden with COCKPIT_ALLOW_ARM_WITHOUT_ESTOP=1);
- the e-stop test needs a seen press, then a seen release after the reset, 60 s each;
- an e-stop or a lost agent outside the test resets the wizard to the e-stop test (homing
  again too, since the arm may have moved).
"""

from __future__ import annotations

import os
from datetime import timedelta

from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone

from core.agent import AgentError, get_agent
from core.runner import get_runner

from . import state as stack_state
from .models import Preflight

ESTOP_TIMEOUT_S = 60
STEPS = (
    ("devices", "Devices", "Teensy, Nano and cameras present (from the hardware checks)."),
    ("estop", "E-stop test", "Press the hardware e-stop, then release it and reset."),
    ("workspace", "Workspace clear", "Nobody and nothing inside the arm's reach."),
    ("homing", "Homing", "The arm moves to its limit switches and calibrates."),
    ("pose", "Pose check", "The 3D model at the measured joint angles matches the real arm."),
    ("limits", "Limits", "Velocity and acceleration scaling and the workspace box from config."),
)
IDS = [s[0] for s in STEPS]
RESET_FROM = "estop"  # an e-stop outside the test sends the wizard back here


class PreflightError(Exception):
    def __init__(self, status: int, title: str, detail: str = ""):
        super().__init__(title)
        self.status, self.title, self.detail = status, title, detail


def override() -> bool:
    return os.environ.get("COCKPIT_ALLOW_ARM_WITHOUT_ESTOP") == "1"


async def _real_stack():
    st = await stack_state.read(get_runner())
    current = st.stack
    if current is None or current[0] is None or current[0].robot != "real":
        return None
    return current[1]


async def _record(container) -> Preflight:
    def get():
        obj, _ = Preflight.objects.get_or_create(session=container.session)
        return obj

    return await sync_to_async(get)()


async def _save(pf: Preflight) -> None:
    await sync_to_async(pf.save)()


def _set(pf: Preflight, step: str, status: str, detail: str, user: str = "") -> None:
    pf.steps[step] = {
        "status": status,
        "detail": detail,
        "at": timezone.now().isoformat(),
        "user": user,
    }


def _reset_from(pf: Preflight, step: str, why: str) -> None:
    for sid in IDS[IDS.index(step) :]:
        if pf.steps.get(sid, {}).get("status") in ("pass", "overridden"):
            _set(pf, sid, "pending", why)
    pf.estop_phase, pf.estop_since = "", None


def _done(pf: Preflight, step: str) -> bool:
    return pf.steps.get(step, {}).get("status") in ("pass", "overridden")


async def _agent_arm() -> tuple[str | None, bool | None, str]:
    """(arm, estop, error) from the agent; arm None when unavailable."""
    try:
        data = await get_agent().arm_state()
        return data.get("arm"), bool(data.get("estop")), ""
    except AgentError as exc:
        return None, None, exc.detail or exc.title


async def _observe(pf: Preflight) -> tuple[str, bool | None, str]:
    """Advance the e-stop test and apply the e-stop / lost-agent rules. Returns the arm state
    for the header, the e-stop flag and the agent error."""
    arm, estop, error = await _agent_arm()
    now = timezone.now()
    if pf.estop_phase:
        late = pf.estop_since and now - pf.estop_since > timedelta(seconds=ESTOP_TIMEOUT_S)
        if pf.estop_phase == "press" and estop:
            pf.estop_phase, pf.estop_since = "reset", now
            _set(pf, "estop", "running", "e-stop pressed: release it, then press Reset")
        elif pf.estop_phase == "reset" and estop is False:
            pf.estop_phase, pf.estop_since = "", None
            _set(pf, "estop", "pass", "press and release seen")
        elif late:
            what = "no e-stop press seen" if pf.estop_phase == "press" else "no reset seen"
            pf.estop_phase, pf.estop_since = "", None
            _set(pf, "estop", "fail", f"{what} within {ESTOP_TIMEOUT_S} s")
    elif estop and any(_done(pf, s) for s in IDS[IDS.index(RESET_FROM) :]):
        _reset_from(pf, RESET_FROM, "e-stop pressed: run the wizard again from the e-stop test")
    elif arm is None and error and any(_done(pf, s) for s in IDS[IDS.index("homing") :]):
        _reset_from(pf, RESET_FROM, f"agent lost ({error}): run the wizard again")
    await _save(pf)
    if arm is None:
        return ("FAULT" if error and settings.FAKE else "NOT_HOMED"), estop, error
    if estop:
        return "E_STOP", estop, error
    return arm, estop, error


async def current_state() -> str | None:
    """The header's arm chip (§7.2), or None without a REAL stack."""
    container = await _real_stack()
    if container is None:
        return None
    arm, _estop, _error = await _observe(await _record(container))
    return arm


def _limits() -> dict:
    import yaml

    robot = yaml.safe_load((settings.REPO_DIR / "config/robot.yaml").read_text())
    return {
        "velocity_scaling": robot["motion"]["velocity_scaling"],
        "acceleration_scaling": robot["motion"]["acceleration_scaling"],
        "table_size_m": robot["table"]["size"],
        "joint_limits_deg": robot["joint_limits"],
        "source": "config/robot.yaml",
    }


async def preflight_state() -> dict:
    container = await _real_stack()
    if container is None:
        return {"real": False, "arm": None, "steps": [], "override": override()}
    pf = await _record(container)
    arm, estop, error = await _observe(pf)
    steps = []
    for sid, title, description in STEPS:
        row = pf.steps.get(sid, {"status": "pending", "detail": ""})
        steps.append({"id": sid, "title": title, "description": description, **row})
    nxt = next((s["id"] for s in steps if s["status"] not in ("pass", "overridden")), None)
    return {
        "real": True,
        "container": container.name,
        "arm": arm,
        "estop": estop,
        "agent_error": error,
        "estop_phase": pf.estop_phase,
        "steps": steps,
        "next": nxt,
        "can_arm": nxt is None and arm == "HOMED",
        "override": override(),
        "limits": _limits(),
    }


async def _devices_ok() -> tuple[bool, str]:
    from setup.checks import context_from_settings, run_checks

    ctx = await context_from_settings()
    if settings.FAKE and ctx.host.real_devices:
        return True, "Teensy, Nano and cameras present (fake)"
    results = await run_checks(ctx, ["hw.dialout", "hw.devices"])
    bad = [r for r in results if r.status != "pass"]
    if bad:
        return False, "; ".join(f"{r.title}: {r.detail}" for r in bad)
    return True, "devices present"


async def run_step(step: str, action: str, user: str) -> dict:
    """One wizard action. Raises PreflightError with an HTTP status for the view."""
    container = await _real_stack()
    if container is None:
        raise PreflightError(409, "No REAL stack running")
    pf = await _record(container)
    await _observe(pf)
    if step == "arm":
        return await _arm(pf, user)
    if step not in IDS:
        raise PreflightError(404, f"Unknown pre-flight step {step!r}")
    before = IDS[: IDS.index(step)]
    missing = [s for s in before if not _done(pf, s)]
    if missing:
        raise PreflightError(409, "Earlier steps first", f"Not done yet: {', '.join(missing)}")
    agent = get_agent()

    if step == "devices":
        ok, detail = await _devices_ok()
        _set(pf, step, "pass" if ok else "fail", detail, user)
    elif step == "estop":
        await _estop(pf, action, user, agent)
    elif step in ("workspace", "pose", "limits"):
        if action != "confirm":
            raise PreflightError(400, "Confirm this step", "action must be 'confirm'")
        _set(pf, step, "pass", f"confirmed by {user}", user)
    elif step == "homing":
        _set(pf, step, "running", "homing…", user)
        await _save(pf)
        try:
            await agent.home()
            _set(pf, step, "pass", "homed", user)
        except AgentError as exc:
            _set(pf, step, "fail", exc.detail or exc.title, user)
    await _save(pf)
    return await preflight_state()


async def _estop(pf: Preflight, action: str, user: str, agent) -> None:
    if action == "override":
        if not override():
            raise PreflightError(403, "Not allowed", "Set COCKPIT_ALLOW_ARM_WITHOUT_ESTOP=1 first.")
        _set(pf, "estop", "overridden", "skipped: COCKPIT_ALLOW_ARM_WITHOUT_ESTOP=1", user)
        return
    if action == "start":
        _arm_state, estop, error = await _agent_arm()
        if estop is None:
            _set(
                pf,
                "estop",
                "fail",
                f"no e-stop signal from ar3_safety ({error}). The hardware e-stop circuit is "
                "not built yet (docs/hardware.md).",
                user,
            )
            return
        if estop:
            _set(pf, "estop", "fail", "the e-stop is already pressed: release it first", user)
            return
        pf.estop_phase, pf.estop_since = "press", timezone.now()
        _set(pf, "estop", "running", "press the e-stop now", user)
        return
    if action == "reset":
        if pf.estop_phase != "reset":
            raise PreflightError(409, "Press the e-stop first")
        try:
            await agent.reset_estop()
        except AgentError as exc:
            raise PreflightError(502, "Reset failed", exc.detail or exc.title) from None
        await _observe(pf)
        return
    raise PreflightError(400, "Unknown action", "e-stop actions: start, reset, override")


async def _arm(pf: Preflight, user: str) -> dict:
    missing = [s for s in IDS if not _done(pf, s)]
    if missing:
        raise PreflightError(409, "Pre-flight not complete", f"Not done yet: {', '.join(missing)}")
    try:
        await get_agent().arm(True)
    except AgentError as exc:
        raise PreflightError(502, "Arming failed", exc.detail or exc.title) from None
    return await preflight_state()


async def disarm() -> dict:
    try:
        await get_agent().arm(False)
    except AgentError as exc:
        raise PreflightError(502, "Disarming failed", exc.detail or exc.title) from None
    return await preflight_state()
