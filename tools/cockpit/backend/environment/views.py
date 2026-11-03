"""Environment API (docs/specs/spec_cockpit.md §9, §15): modes, start / stop / restart, health,
viewers, components, the REAL arm (pre-flight wizard) and the software stop.

Starting and stopping are jobs (§8.3) of the steps env.start / env.stop / env.restart, so
they show a live log, take the one-changing-job lock and appear in the job history.
"""

from asgiref.sync import async_to_sync
from django.core.cache import cache
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from accounts import audit
from core import platform
from core.agent import AgentError, get_agent
from core.problems import Problem
from setup import service
from setup import views as setup_views

from . import arm, components
from . import status as env_status
from .modes import ALL_MODES, BY_ID

StartRequest = inline_serializer(
    "StartRequest",
    {
        "mode": serializers.CharField(),
        "lichtblick": serializers.BooleanField(required=False),
        "switch": serializers.BooleanField(required=False),
    },
)
StopRequest = inline_serializer(
    "StopRequest", {"confirm": serializers.BooleanField(required=False)}
)
PreflightRequest = inline_serializer(
    "PreflightRequest", {"action": serializers.CharField(required=False)}
)


def _snapshot(fresh: bool = False) -> dict:
    """The header status (§7.2): the last snapshot of run_monitor, else computed now."""
    snap = None if fresh else cache.get(env_status.CACHE_KEY)
    if snap is None:
        snap = async_to_sync(env_status.snapshot)()
    return snap


@extend_schema(responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def platform_view(request: Request) -> Response:
    return Response(async_to_sync(platform.current_host)().to_dict())


@extend_schema(responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def state_view(request: Request) -> Response:
    return Response(_snapshot())


@extend_schema(responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def modes_view(request: Request) -> Response:
    """Every stack the cockpit can name (§3), with its CLI command and, where it can start
    it on this host, the exact command the start job runs."""
    host_os = setup_views._host()["os"]
    out = []
    for m in ALL_MODES:
        row = m.to_dict()
        row["here"] = host_os in m.platforms
        if m.startable:
            env, argv = m.start()
            row["start_command"] = " ".join([*(f"{k}={v}" for k, v in env.items()), *argv])
        out.append(row)
    return Response(out)


def _running_mode(snap: dict) -> dict | None:
    return snap["mode"] if snap.get("mode") else None


@extend_schema(request=StartRequest, responses={201: OpenApiTypes.OBJECT})
@api_view(["POST"])
def start_view(request: Request) -> Response:
    """Start a mode as a job. A running stack → 409 with it, unless ``switch`` (stop and
    switch, §3.1): then the job runs `make down` first."""
    mode_id = str(request.data.get("mode", ""))
    switch = bool(request.data.get("switch"))
    if mode_id not in BY_ID:
        raise Problem(400, "Unknown mode", mode_id)
    running = _running_mode(_snapshot(fresh=True))
    if running and not switch:
        raise Problem(
            409,
            "A ROS stack is running",
            f"{running['chip']} ({running['command'] or running['container']}) runs already; "
            "only one stack per host. Stop it first, or stop and switch.",
            extra={"running": running},
        )
    if running and running.get("robot") == "real" and not request.data.get("confirm"):
        raise Problem(400, "Confirm stopping REAL", "Stopping the real arm needs confirm: true.")
    params = {
        "mode": mode_id,
        "lichtblick": "1" if request.data.get("lichtblick") else "0",
        "switch": "1" if running and switch else "0",
    }
    job = setup_views._start(request, "env.start", params)
    audit.record_request(request, "env.start", f"mode:{mode_id}", {"command": job.command})
    return Response(service.job_dict(job), status=status.HTTP_201_CREATED)


@extend_schema(request=StopRequest, responses={201: OpenApiTypes.OBJECT})
@api_view(["POST"])
def stop_view(request: Request) -> Response:
    """`make down` as a job. REAL needs ``confirm: true`` (§9.1)."""
    running = _running_mode(_snapshot(fresh=True))
    if running is None:
        raise Problem(409, "No ROS stack is running")
    if running.get("robot") == "real" and not request.data.get("confirm"):
        raise Problem(400, "Confirm stopping REAL", "Stopping the real arm needs confirm: true.")
    job = setup_views._start(request, "env.stop", {})
    audit.record_request(request, "env.stop", f"mode:{running['id']}", {"command": job.command})
    return Response(service.job_dict(job), status=status.HTTP_201_CREATED)


RESTARTABLE = {"ros": "env.restart", "lichtblick": "viewers.lichtblick"}


@extend_schema(request=None, responses={201: OpenApiTypes.OBJECT})
@api_view(["POST"])
def component_restart(request: Request, component_id: str) -> Response:
    """Restart a container component: the stack container, or (re)start Lichtblick web."""
    step = RESTARTABLE.get(component_id)
    if step is None:
        raise Problem(
            400,
            "Not restartable",
            f"{component_id} is not a container of its own; restart the ROS stack instead.",
        )
    job = setup_views._start(request, step, {})
    audit.record_request(request, "env.restart", f"component:{component_id}")
    return Response(service.job_dict(job), status=status.HTTP_201_CREATED)


@extend_schema(responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def health_view(request: Request) -> Response:
    snap = _snapshot()
    return Response({"ready": snap["ready"], "components": snap["components"]})


@extend_schema(responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def viewers_view(request: Request) -> Response:
    return Response(_snapshot()["viewers"])


@extend_schema(responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def components_view(request: Request) -> Response:
    """The catalogue (§3.4) with live status and whether each runs in the current mode
    (``?mode=<id>`` previews another mode for the diagram)."""
    snap = _snapshot()
    mode_id = request.query_params.get("mode") or (snap["mode"] or {}).get("id")
    mode = BY_ID.get(mode_id) if mode_id else None
    items = components.with_status(components.catalogue(), snap["components"], mode)
    return Response({"mode": mode.id if mode else None, "components": items})


# --- arm (REAL) and the software stop -------------------------------------------------------


def _preflight_problem(exc: arm.PreflightError) -> Problem:
    return Problem(exc.status, exc.title, exc.detail)


@extend_schema(responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def arm_state(request: Request) -> Response:
    return Response(async_to_sync(arm.preflight_state)())


@extend_schema(request=PreflightRequest, responses={200: OpenApiTypes.OBJECT})
@api_view(["POST"])
def preflight_step(request: Request, step: str) -> Response:
    """One pre-flight action (§9.4); ``step`` = devices | estop | workspace | homing | pose |
    limits | arm. Every call is audited with the user."""
    action = str(request.data.get("action", "run"))
    user = request.user.username
    try:
        result = async_to_sync(arm.run_step)(step, action, user)
    except arm.PreflightError as exc:
        audit.record_request(
            request, "arm.preflight", f"step:{step}", {"action": action, "refused": exc.title}
        )
        raise _preflight_problem(exc) from exc
    row = next((s for s in result["steps"] if s["id"] == step), {"status": result["arm"]})
    audit.record_request(
        request, "arm.preflight", f"step:{step}", {"action": action, "result": row["status"]}
    )
    return Response(result)


@extend_schema(request=None, responses={200: OpenApiTypes.OBJECT})
@api_view(["POST"])
def disarm_view(request: Request) -> Response:
    try:
        result = async_to_sync(arm.disarm)()
    except arm.PreflightError as exc:
        raise _preflight_problem(exc) from exc
    audit.record_request(request, "arm.disarm", "arm")
    return Response(result)


@extend_schema(request=None, responses={200: OpenApiTypes.OBJECT})
@api_view(["POST"])
def stop_motion(request: Request) -> Response:
    """The header's software stop (§7.2): cancel every goal, hold. Not an e-stop."""
    try:
        result = async_to_sync(get_agent().stop)()
    except AgentError as exc:
        audit.record_request(request, "arm.stop", "stack", {"error": exc.title})
        raise Problem(
            502,
            "Software stop failed",
            exc.detail or exc.title,
            hint="Use the hardware e-stop in an emergency.",
        ) from exc
    audit.record_request(
        request,
        "arm.stop",
        "stack",
        {"cancelled": sum(c["goals"] for c in result.get("cancelled", []))},
    )
    return Response(result)


@extend_schema(responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def joints_view(request: Request) -> Response:
    """The measured joint angles, for the pre-flight pose check (§9.4 step 5)."""
    try:
        return Response(async_to_sync(get_agent().joints)())
    except AgentError as exc:
        raise Problem(502, "No joint states", exc.detail or exc.title) from exc
