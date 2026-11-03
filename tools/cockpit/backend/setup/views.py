"""Install & Setup API (docs/specs/spec_cockpit.md §8, §15): checks, steps, jobs, continue."""

from asgiref.sync import async_to_sync
from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from accounts import audit
from core import platform
from core.problems import Problem

from . import service, steps
from .checks import BY_ID, context_from_settings, run_checks
from .models import Job


def _host() -> dict:
    return async_to_sync(platform.current_host)().to_dict()


def _run(ids: list[str] | None) -> None:
    async def go():
        ctx = await context_from_settings()
        return await run_checks(ctx, ids)

    service.store_results(async_to_sync(go)())


def _checklist() -> dict:
    results = service.stored_results()
    if len(results) < len(BY_ID):  # first visit: run everything once
        _run(None)
        results = service.stored_results()
    return service.checklist(results, _host())


RunChecks = inline_serializer(
    "RunChecks", {"ids": serializers.ListField(child=serializers.CharField(), required=False)}
)
JobRequest = inline_serializer(
    "JobRequest",
    {"step_id": serializers.CharField(), "params": serializers.DictField(required=False)},
)


@extend_schema(responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def checks_view(request: Request) -> Response:
    """The checklist with the last results (runs all checks on the first call)."""
    return Response(_checklist())


@extend_schema(request=RunChecks, responses={200: OpenApiTypes.OBJECT})
@api_view(["POST"])
def checks_run(request: Request) -> Response:
    """Re-check all (no ids) or the given checks; returns the whole checklist."""
    ids = request.data.get("ids") or None
    if ids is not None:
        unknown = [i for i in ids if i not in BY_ID]
        if unknown:
            raise Problem(400, "Unknown check", ", ".join(unknown))
    _run(ids)
    return Response(service.checklist(service.stored_results(), _host()))


@extend_schema(responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def steps_view(request: Request) -> Response:
    host_os = _host()["os"]
    return Response([s.to_dict(host_os) for s in steps.STEPS])


def _start(request: Request, step_id: str, params: dict | None) -> Job:
    try:
        job = service.create_job(step_id, params, request.user.username, _host()["os"])
    except steps.StepError as exc:
        raise Problem(400, "Step refused", str(exc)) from exc
    except service.Busy as exc:
        raise Problem(
            409,
            "Another job is running",
            f"{exc.job.step_id} is {exc.job.status}; one changing job runs at a time.",
            extra={"job_id": str(exc.job.id)},
        ) from exc
    audit.record_request(request, "job.start", f"job:{job.id}", {"step": step_id, **job.params})
    return job


@extend_schema(methods=["GET"], operation_id="jobs_list", responses={200: OpenApiTypes.OBJECT})
@extend_schema(methods=["POST"], request=JobRequest, responses={201: OpenApiTypes.OBJECT})
@api_view(["GET", "POST"])
def jobs_view(request: Request) -> Response:
    if request.method == "POST":
        job = _start(request, request.data.get("step_id", ""), request.data.get("params"))
        return Response(service.job_dict(job), status=status.HTTP_201_CREATED)
    try:
        limit = min(max(int(request.query_params.get("limit", 20)), 1), 500)
    except ValueError as exc:
        raise Problem(400, "Invalid request", "limit must be an integer") from exc
    qs = Job.objects.all()
    if step := request.query_params.get("step"):
        qs = (
            qs.filter(step_id__startswith=step[:-1])
            if step.endswith("*")
            else qs.filter(step_id=step)
        )
    if job_status := request.query_params.get("status"):
        qs = qs.filter(status__in=job_status.split(","))
    if user := request.query_params.get("user"):
        qs = qs.filter(user=user)
    if q := request.query_params.get("q"):
        qs = qs.filter(command__icontains=q)
    return Response([service.job_dict(j) for j in qs[:limit]])


def _job(job_id) -> Job:
    job = Job.objects.filter(pk=job_id).first()
    if job is None:
        raise Problem(404, "Not found", "no such job")
    return job


@extend_schema(responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def job_detail(request: Request, job_id) -> Response:
    return Response(service.job_dict(_job(job_id)))


@extend_schema(request=None, responses={200: OpenApiTypes.OBJECT})
@api_view(["POST"])
def job_cancel(request: Request, job_id) -> Response:
    job = _job(job_id)
    if job.status not in Job.ACTIVE:
        raise Problem(409, "Job finished", f"the job is {job.status}")
    service.request_cancel(job)
    audit.record_request(request, "job.cancel", f"job:{job.id}", {"step": job.step_id})
    return Response(service.job_dict(job))


@extend_schema(responses={(200, "text/plain"): OpenApiTypes.STR})
@api_view(["GET"])
def job_log(request: Request, job_id):
    """The whole log as text (download)."""
    job = _job(job_id)
    lines = [f"{ln['ts']} [{ln['stream']}] {ln['text']}" for ln in service.read_log(job)]
    resp = HttpResponse("\n".join(lines) + "\n", content_type="text/plain; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="job-{job.step_id}-{job.id}.log"'
    return resp


@extend_schema(request=None, responses={200: OpenApiTypes.OBJECT})
@api_view(["POST"])
def continue_setup(request: Request) -> Response:
    """Runs the next failing step that the cockpit can fix (§8.1 "Continue setup")."""
    results = service.stored_results()
    host_os = _host()["os"]
    nxt = service.next_fix(results, host_os)
    if nxt is None:
        return Response({"job": None, "manual": service.manual_items(results)})
    check, step = nxt
    job = _start(request, step.id, check.get("fix_params") or None)
    return Response({"job": service.job_dict(job), "check": check["id"]})
