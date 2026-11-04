"""Simulations API (docs/specs/spec_cockpit.md §12.3, §15): scenarios, validation, reachability,
runs, history, compare, export / import, pack-and-go."""

from __future__ import annotations

import json

from asgiref.sync import async_to_sync
from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response

from accounts import audit
from core import platform
from core.agent import AgentError, get_agent
from core.problems import Problem
from setup import service
from setup import views as setup_views
from telemetry.models import Run

from . import context, execute, pack, store, validate

YamlBody = inline_serializer("ScenarioYaml", {"yaml": serializers.CharField()})
RunBody = inline_serializer(
    "ScenarioRunRequest",
    {
        "mode": serializers.CharField(required=False, allow_blank=True),
        "repeat": serializers.IntegerField(required=False),
        "seed": serializers.IntegerField(required=False, allow_null=True),
        "keep_scene": serializers.BooleanField(required=False),
        "record": serializers.BooleanField(required=False),
    },
)


def host_robots() -> list[str]:
    """The robot kinds this host can start (mock, gazebo; real only on a real host)."""
    from environment.modes import ALL_MODES

    host = async_to_sync(platform.current_host)()
    kinds = {m.robot for m in ALL_MODES if host.os in m.platforms and m.startable}
    if getattr(host, "real_devices", False):
        kinds.add("real")
    return sorted(kinds)


def _caps() -> dict | None:
    try:
        return async_to_sync(get_agent().capabilities)()
    except AgentError:
        return None


def _validate(text: str) -> dict:
    return validate.validate(text, host_robots(), _caps())


def _text(request: Request) -> str:
    text = request.data.get("yaml")
    if not isinstance(text, str) or not text.strip():
        raise Problem(400, "Invalid request", "yaml (the scenario text) is required")
    return text


def _read(scenario_id: str) -> str:
    try:
        return store.read(scenario_id)
    except FileNotFoundError:
        raise Problem(404, "Not found", f"no scenario {scenario_id!r}") from None
    except store.StoreError as exc:
        raise Problem(400, "Invalid id", str(exc)) from None


def _refuse_invalid(checked: dict) -> None:
    if not checked["ok"]:
        raise Problem(
            422,
            "The scenario is not valid",
            f"{len(checked['errors'])} error(s); see `errors`",
            extra={"errors": checked["errors"], "warnings": checked["warnings"]},
        )


def _last_runs() -> dict[str, Run]:
    out = {}
    for r in Run.objects.order_by("-started")[:2000]:
        out.setdefault(r.scenario_id, r)
    return out


def summary(data: dict, last: Run | None) -> dict:
    return {
        "id": data.get("id"),
        "name": data.get("name", data.get("id")),
        "description": data.get("description", ""),
        "version": data.get("version"),
        "tags": data.get("tags", []),
        "modes": data.get("modes", []),
        "task_type": (data.get("task") or {}).get("type"),
        "steps": len((data.get("task") or {}).get("steps") or []),
        "objects": len((data.get("scene") or {}).get("objects") or []),
        "broken": data.get("broken", False),
        "last_run": None if last is None else {
            "id": str(last.id), "result": last.result or last.status,
            "started": last.started.isoformat(), "mode": last.mode},
    }  # fmt: skip


@extend_schema(methods=["GET"], operation_id="scenarios_list", responses={200: OpenApiTypes.OBJECT})
@extend_schema(methods=["POST"], request=YamlBody, responses={201: OpenApiTypes.OBJECT})
@api_view(["GET", "POST"])
def scenarios_view(request: Request) -> Response:
    """GET: every scenario with its last run. POST {yaml}: create (validated)."""
    if request.method == "GET":
        last = _last_runs()
        return Response([summary(d, last.get(d.get("id"))) for d in store.all_scenarios()])
    text = _text(request)
    checked = _validate(text)
    _refuse_invalid(checked)
    try:
        sid, version = store.create(text)
    except store.StoreError as exc:
        raise Problem(409, "Exists", str(exc)) from None
    audit.record_request(request, "scenario.create", f"scenario:{sid}", {"version": version})
    return Response({"id": sid, "version": version}, status=status.HTTP_201_CREATED)


@extend_schema(methods=["GET"], responses={200: OpenApiTypes.OBJECT})
@extend_schema(methods=["PUT"], request=YamlBody, responses={200: OpenApiTypes.OBJECT})
@extend_schema(methods=["DELETE"], responses={200: OpenApiTypes.OBJECT})
@api_view(["GET", "PUT", "DELETE"])
def scenario_detail(request: Request, scenario_id: str) -> Response:
    if request.method == "GET":
        text = _read(scenario_id)
        checked = _validate(text)
        return Response({"id": scenario_id, "yaml": text, "data": checked["data"],
                         "errors": checked["errors"], "warnings": checked["warnings"]})  # fmt: skip
    if request.method == "DELETE":
        _read(scenario_id)
        if request.data.get("confirm") is not True and request.query_params.get("confirm") != "1":
            raise Problem(400, "Confirm the delete", "send confirm: true")
        store.delete(scenario_id)
        audit.record_request(request, "scenario.delete", f"scenario:{scenario_id}")
        return Response({"deleted": scenario_id})
    _read(scenario_id)
    text = _text(request)
    checked = _validate(text)
    _refuse_invalid(checked)
    try:
        sid, version = store.save(text, expected_id=scenario_id)
    except store.StoreError as exc:
        raise Problem(400, "Not saved", str(exc)) from None
    audit.record_request(request, "scenario.edit", f"scenario:{sid}", {"version": version})
    return Response({"id": sid, "version": version, "warnings": checked["warnings"]})


@extend_schema(request=YamlBody, responses={200: OpenApiTypes.OBJECT})
@api_view(["POST"])
def scenario_validate(request: Request) -> Response:
    """Validation of an unsaved text (the editor, as you type)."""
    checked = _validate(_text(request))
    return Response({k: checked[k] for k in ("ok", "errors", "warnings", "data")})


@extend_schema(responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def scenario_context(request: Request) -> Response:
    """Table, bins, named poses, limits, reach ring, worlds; schema; host modes; capabilities."""
    return Response({**context.site(), "schema": validate.schema(), "host_robots": host_robots(),
                     "capabilities": _caps()})  # fmt: skip


@extend_schema(request=None, responses={201: OpenApiTypes.OBJECT})
@api_view(["POST"])
def scenario_duplicate(request: Request, scenario_id: str) -> Response:
    _read(scenario_id)
    new_id, version = store.duplicate(scenario_id, request.data.get("id") or None)
    audit.record_request(request, "scenario.create", f"scenario:{new_id}", {"from": scenario_id})
    return Response({"id": new_id, "version": version}, status=status.HTTP_201_CREATED)


@extend_schema(responses={(200, "application/yaml"): OpenApiTypes.STR})
@api_view(["GET"])
def scenario_export(request: Request, scenario_id: str):
    resp = HttpResponse(_read(scenario_id), content_type="application/yaml; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{scenario_id}.yaml"'
    return resp


@extend_schema(request=YamlBody, responses={201: OpenApiTypes.OBJECT})
@api_view(["POST"])
def scenario_import(request: Request) -> Response:
    """A YAML upload: validated; ``replace: true`` overwrites a scenario with the same id."""
    text = _text(request)
    checked = _validate(text)
    _refuse_invalid(checked)
    sid = checked["data"]["id"]
    if store.exists(sid) and request.data.get("replace") is not True:
        raise Problem(409, "Exists", f"a scenario {sid!r} exists; send replace: true")
    sid, version = store.save(text)
    audit.record_request(request, "scenario.import", f"scenario:{sid}", {"version": version})
    return Response({"id": sid, "version": version}, status=status.HTTP_201_CREATED)


@extend_schema(request=YamlBody, responses={200: OpenApiTypes.OBJECT})
@api_view(["POST"])
def scenario_reachability(request: Request, scenario_id: str) -> Response:
    """IK for every move_pose target (§12.3 "Check reachability"); body ``yaml``: an unsaved
    text instead of the stored file. Needs a running stack (any sim mode)."""
    text = request.data.get("yaml") or _read(scenario_id)
    data, _lines, problems = validate.parse(text)
    if data is None:
        raise Problem(422, "The scenario is not valid", problems[0]["message"])
    targets = []
    for i, step in enumerate((data.get("task") or {}).get("steps") or []):
        if "move_pose" in step:
            targets.append({"step": i, **step["move_pose"]})
    if not targets:
        return Response({"targets": [], "reachable": True, "detail": "no pose targets"})
    try:
        res = async_to_sync(get_agent().ik_check)(targets)
    except AgentError as exc:
        raise Problem(
            503 if exc.status in (0, 503) else 502,
            "Reachability not checked",
            exc.detail or exc.title,
            hint="Start a ROS stack (Environment).",
        ) from None
    out = res.get("targets", [])
    return Response({"targets": out, "reachable": all(t.get("reachable") for t in out)})


@extend_schema(
    methods=["GET"], operation_id="scenario_runs_list", responses={200: OpenApiTypes.OBJECT}
)
@extend_schema(methods=["POST"], request=RunBody, responses={201: OpenApiTypes.OBJECT})
@api_view(["GET", "POST"])
def scenario_runs(request: Request, scenario_id: str) -> Response:
    """GET: the run history. POST: start a run (a job of step scenario.run)."""
    if request.method == "GET":
        runs = Run.objects.filter(scenario_id=scenario_id).order_by("-started")[:200]
        return Response([execute.run_dict(r) for r in runs])
    checked = _validate(_read(scenario_id))
    _refuse_invalid(checked)
    d = request.data
    params = {
        "scenario": scenario_id,
        "mode": d.get("mode") or "",
        "repeat": str(int(d.get("repeat") or 1)),
        "seed": "" if d.get("seed") in (None, "") else str(int(d["seed"])),
        "keep_scene": "1" if d.get("keep_scene") else "0",
        "record": "1" if d.get("record") else "0",
    }
    try:
        async_to_sync(execute.check_runnable)(checked["data"], params["mode"] or None)
    except execute.RunError as exc:
        raise Problem(409, "Cannot run", str(exc)) from None
    job = setup_views._start(request, "scenario.run", params)
    return Response(service.job_dict(job), status=status.HTTP_201_CREATED)


def _run(run_id) -> Run:
    r = Run.objects.filter(pk=run_id).first()
    if r is None:
        raise Problem(404, "Not found", "no such run")
    return r


@extend_schema(responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def run_detail(request: Request, run_id) -> Response:
    return Response(execute.run_dict(_run(run_id)))


@extend_schema(responses={(200, "text/plain"): OpenApiTypes.STR})
@api_view(["GET"])
def run_log(request: Request, run_id):
    r = _run(run_id)
    p = execute.runs_dir() / str(r.id) / "run.log"
    text = p.read_text() if p.exists() else ""
    return HttpResponse(text, content_type="text/plain; charset=utf-8")


@extend_schema(request=None, responses={200: OpenApiTypes.OBJECT})
@api_view(["POST"])
def run_abort(request: Request, run_id) -> Response:
    """Abort = cancel the goal + hold (the agent), and cancel the run's job."""
    r = _run(run_id)
    if r.status != "running":
        raise Problem(409, "Finished", f"the run is {r.status}")
    try:
        async_to_sync(get_agent().run_abort)(str(r.id))
    except AgentError:
        pass
    from setup.models import Job

    job = Job.objects.filter(pk=r.job_id).first() if r.job_id else None
    if job is not None and job.status in Job.ACTIVE:
        service.request_cancel(job)
    if execute.is_orphan(r):
        execute.close_orphan(r)
    audit.record_request(request, "scenario.abort", f"run:{r.id}", {"scenario": r.scenario_id})
    return Response(execute.run_dict(r))


@extend_schema(request=inline_serializer("PauseBody", {"paused": serializers.BooleanField()}),
               responses={200: OpenApiTypes.OBJECT})  # fmt: skip
@api_view(["POST"])
def run_pause(request: Request, run_id) -> Response:
    """Pause before the next step (sequence), or resume."""
    r = _run(run_id)
    if r.status != "running":
        raise Problem(409, "Finished", f"the run is {r.status}")
    paused = request.data.get("paused") is not False
    try:
        state = async_to_sync(get_agent().run_pause)(str(r.id), paused)
    except AgentError as exc:
        raise Problem(502, "Not paused", exc.detail or exc.title) from None
    return Response({"paused": paused, "agent": {"status": state.get("status")}})


@extend_schema(responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def runs_compare(request: Request) -> Response:
    """Two runs side by side (``a``, ``b``): steps, durations, evaluation, metrics."""
    a, b = _run(request.query_params.get("a")), _run(request.query_params.get("b"))
    ra, rb = execute.run_dict(a), execute.run_dict(b)
    n = max(len(ra["steps"]), len(rb["steps"]))
    rows = []
    for i in range(n):
        sa = ra["steps"][i] if i < len(ra["steps"]) else {}
        sb = rb["steps"][i] if i < len(rb["steps"]) else {}
        rows.append(
            {
                "index": i,
                "label": sa.get("label") or sb.get("label"),
                "a": {k: sa.get(k) for k in ("status", "planning_s", "execution_s", "duration_s")},
                "b": {k: sb.get(k) for k in ("status", "planning_s", "execution_s", "duration_s")},
            }
        )
    return Response({"a": ra, "b": rb, "steps": rows})


PackBody = inline_serializer(
    "PackBody", {"ids": serializers.ListField(child=serializers.CharField())}
)


@extend_schema(request=PackBody, responses={(200, "application/zip"): OpenApiTypes.BINARY})
@api_view(["POST"])
def scenarios_pack(request: Request):
    ids = request.data.get("ids") or []
    try:
        name, data = pack.pack(list(ids))
    except pack.PackError as exc:
        raise Problem(400, "Not packed", str(exc)) from None
    audit.record_request(request, "scenario.pack", "scenarios", {"ids": ",".join(ids)})
    resp = HttpResponse(data, content_type="application/zip")
    resp["Content-Disposition"] = f'attachment; filename="{name}"'
    return resp


@extend_schema(
    request={"multipart/form-data": OpenApiTypes.OBJECT}, responses={200: OpenApiTypes.OBJECT}
)
@api_view(["POST"])
@parser_classes([MultiPartParser])
def scenarios_unpack(request: Request) -> Response:
    """Step 1 of an import: the uploaded pack (``file``) → preview with diffs and warnings."""
    f = request.FILES.get("file")
    if f is None:
        raise Problem(400, "Invalid request", "file (the .zip) is required")
    try:
        return Response(pack.preview(f.read()))
    except pack.PackError as exc:
        raise Problem(400, "Not a scenario pack", str(exc)) from None


@extend_schema(request=inline_serializer("UnpackApply", {"token": serializers.CharField(),
                                                         "decisions": serializers.DictField()}),
               responses={200: OpenApiTypes.OBJECT})  # fmt: skip
@api_view(["POST"])
@parser_classes([JSONParser])
def scenarios_unpack_apply(request: Request) -> Response:
    """Step 2: per scenario keep | replace | copy."""
    try:
        result = pack.apply(str(request.data.get("token", "")), request.data.get("decisions") or {})
    except pack.PackError as exc:
        raise Problem(400, "Not imported", str(exc)) from None
    audit.record_request(request, "scenario.unpack", "scenarios",
                         {"done": json.dumps(result["done"])[:500]})  # fmt: skip
    return Response(result)
