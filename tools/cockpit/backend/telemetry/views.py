"""Telemetry and data-capture API (docs/specs/spec_cockpit.md §10.3, §12a, §15): sessions, REC, the
joint history, the live window, exports, recordings, compression."""

from __future__ import annotations

from pathlib import Path

from asgiref.sync import async_to_sync
from django.conf import settings
from django.core.cache import cache
from django.http import FileResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from accounts import audit
from core.problems import Problem
from monitoring.views import _int, _time
from setup import service
from setup import views as setup_views

from . import events, export, ingest, policies, queries, recordings, sessions
from .models import Recording, Session

RecordingRequest = inline_serializer("RecordingRequest", {"recording": serializers.BooleanField()})
ExportRequest = inline_serializer(
    "ExportRequest",
    {
        "session": serializers.UUIDField(),
        "format": serializers.ChoiceField(choices=export.FORMATS, required=False),
        "run": serializers.UUIDField(required=False),
        "from": serializers.DateTimeField(required=False),
        "to": serializers.DateTimeField(required=False),
    },
)


def _session(session_id) -> Session:
    s = Session.objects.filter(pk=session_id).first()
    if s is None:
        raise Problem(404, "Not found", "no such session")
    return s


@extend_schema(operation_id="sessions_list", responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def sessions_view(request: Request) -> Response:
    qs = Session.objects.all()
    if mode := request.query_params.get("mode"):
        qs = qs.filter(mode=mode)
    if source := request.query_params.get("source"):
        qs = qs.filter(source=source)
    limit = _int(request, "limit", 50, 1, 500)
    return Response([sessions.to_dict(s) for s in qs[:limit]])


@extend_schema(responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def session_detail(request: Request, session_id) -> Response:
    s = _session(session_id)
    return Response(
        {
            **sessions.to_dict(s),
            "counts": queries.counts(str(s.id)),
            "runs": [
                {
                    "id": str(r.id),
                    "scenario": r.scenario_id,
                    "result": r.result,
                    "started": r.started.isoformat(),
                }
                for r in s.runs.all()
            ],  # fmt: skip
            "recordings": [recordings.to_dict(r) for r in s.recordings.all()],
            "events": events.query(session_id=s.id, limit=200),
        }
    )


@extend_schema(responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def session_gaps(request: Request, session_id) -> Response:
    """Gaps in one joint's samples (longer than 1.5 × the stored period)."""
    s = _session(session_id)
    joint = request.query_params.get("joint", "joint_1")
    return Response(queries.gaps(str(s.id), joint, 1.0 / s.rate_hz))


@extend_schema(responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def current_session(request: Request) -> Response:
    s = async_to_sync(sessions.current)()
    return Response(
        {
            "session": sessions.to_dict(s) if s else None,
            "ingest": async_to_sync(ingest.ingest_state)(),
        }
    )


@extend_schema(request=RecordingRequest, responses={200: OpenApiTypes.OBJECT})
@api_view(["POST"])
def current_recording(request: Request) -> Response:
    """REC on / off for the running session (§12a.3)."""
    on = request.data.get("recording")
    if not isinstance(on, bool):
        raise Problem(400, "Invalid request", "recording must be true or false")
    s = async_to_sync(ingest.set_recording)(on)
    if s is None:
        raise Problem(409, "No session", "no ROS stack is running, so there is nothing to record")
    audit.record_request(request, "telemetry.rec", f"session:{s.id}", {"recording": on})
    events.record_sync("rec", {"recording": on, "user": request.user.username}, s)
    return Response(sessions.to_dict(s))


@extend_schema(responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def joints_history(request: Request) -> Response:
    """Joint history from the hypertable: ``session`` (required), ``run``, ``from``, ``to``,
    ``bucket`` (seconds; default: at most 2000 points per joint), ``joints`` (comma list)."""
    session_id = request.query_params.get("session")
    if not session_id:
        raise Problem(400, "Invalid request", "session is required")
    s = _session(session_id)
    bucket = request.query_params.get("bucket")
    try:
        bucket_s = float(bucket) if bucket else None
    except ValueError:
        raise Problem(400, "Invalid request", "bucket must be seconds") from None
    names = [n for n in request.query_params.get("joints", "").split(",") if n] or None
    return Response(
        queries.joints(
            str(s.id),
            _time(request, "from"),
            _time(request, "to"),
            bucket_s,
            request.query_params.get("run") or None,
            names,
        )
    )


@extend_schema(responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def live_window(request: Request) -> Response:
    """The last 60 s at 10 Hz (run_ingest), for a page that opens."""
    return Response(cache.get(ingest.LIVE_KEY) or {"names": [], "samples": []})


@extend_schema(responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def latest_trajectory(request: Request) -> Response:
    """The last planned trajectory (/display_planned_path) of a session (default: current)."""
    session_id = request.query_params.get("session")
    if not session_id:
        s = async_to_sync(sessions.current)()
        session_id = str(s.id) if s else None
    return Response({"trajectory": queries.latest_trajectory(session_id)})


@extend_schema(methods=["GET"], operation_id="exports_list", responses={200: OpenApiTypes.OBJECT})
@extend_schema(methods=["POST"], request=ExportRequest, responses={201: OpenApiTypes.OBJECT})
@api_view(["GET", "POST"])
def exports_view(request: Request) -> Response:
    """GET: the exports in data/exports/. POST: an export job (step telemetry.export)."""
    if request.method == "GET":
        return Response(export.list_exports())
    data = request.data
    params = {
        "session": str(data.get("session", "")),
        "format": data.get("format") or "parquet",
        "run": str(data.get("run") or ""),
        "from": str(data.get("from") or ""),
        "to": str(data.get("to") or ""),
    }
    job = setup_views._start(request, "telemetry.export", params)
    return Response(service.job_dict(job), status=status.HTTP_201_CREATED)


@extend_schema(responses={(200, "application/octet-stream"): OpenApiTypes.BINARY})
@api_view(["GET"])
def export_file(request: Request, name: str, filename: str):
    base = Path(settings.EXPORT_DIR).resolve()
    path = (base / name / filename).resolve()
    if base not in path.parents or not path.is_file():
        raise Problem(404, "Not found", "no such export file")
    return FileResponse(path.open("rb"), as_attachment=True, filename=f"{name}_{filename}")


@extend_schema(responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def recordings_view(request: Request) -> Response:
    qs = Recording.objects.all()
    if session_id := request.query_params.get("session"):
        qs = qs.filter(session_id=session_id)
    limit = _int(request, "limit", 100, 1, 1000)
    return Response([recordings.to_dict(r) for r in qs[:limit]])


@extend_schema(responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def storage_view(request: Request) -> Response:
    """Chunks and compression per hypertable, the retention in force."""
    return Response({"tables": policies.stats(), "retention_days": policies.retention()})
