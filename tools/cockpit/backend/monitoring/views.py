"""Monitoring API (docs/specs/spec_cockpit.md §10, §15): metrics, logs, ROS graph, alerts,
cameras."""

from __future__ import annotations

from datetime import datetime

from asgiref.sync import async_to_sync
from django.core.cache import cache
from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from accounts import audit
from core.agent import AgentError, get_agent
from core.problems import Problem

from . import alerts, logs, metrics
from .models import Alert

AckRequest = inline_serializer(
    "AckRequest", {"comment": serializers.CharField(required=False, allow_blank=True)}
)
AckManyRequest = inline_serializer(
    "AckManyRequest",
    {
        "ids": serializers.ListField(child=serializers.IntegerField()),
        "comment": serializers.CharField(required=False, allow_blank=True),
    },
)


def _agent_call(fn, *args):
    try:
        return async_to_sync(fn)(*args)
    except AgentError as exc:
        status = (
            503 if exc.status == 0 else exc.status if exc.status in (404, 501, 503, 504) else 502
        )
        raise Problem(
            status,
            exc.title,
            exc.detail or "the cockpit agent did not answer",
            hint="Start a ROS stack (Environment); the agent runs in it.",
        ) from None


def _time(request: Request, key: str) -> datetime | None:
    value = request.query_params.get(key)
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise Problem(400, "Invalid request", f"{key} must be an ISO 8601 time") from None


def _int(request: Request, key: str, default: int, lo: int, hi: int) -> int:
    try:
        return min(max(int(request.query_params.get(key, default)), lo), hi)
    except ValueError:
        raise Problem(400, "Invalid request", f"{key} must be an integer") from None


# --- metrics (§10.1) ---------------------------------------------------------------------


def _metrics() -> dict:
    return cache.get(metrics.CACHE_KEY) or {"host": None, "containers": [], "gpus": []}


@extend_schema(responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def host_metrics(request: Request) -> Response:
    """Host CPU / memory / disk from run_monitor (1 Hz); computed here when it does not run."""
    m = _metrics()
    return Response(m.get("host") or metrics.host())


@extend_schema(responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def container_metrics(request: Request) -> Response:
    m = _metrics()
    return Response({"containers": m.get("containers", []), "gpus": m.get("gpus", []),
                     "ts": m.get("ts")})  # fmt: skip


# --- logs (§10.2) ------------------------------------------------------------------------


@extend_schema(responses={200: OpenApiTypes.OBJECT, (200, "text/plain"): OpenApiTypes.STR})
@api_view(["GET"])
def logs_view(request: Request):
    """Search the log buffer: ``source`` (repeat or comma-separated; ``container:`` is a
    prefix), ``level`` (minimum), ``node``, ``q`` (text, or a regex with ``regex=1``),
    ``from`` / ``to`` / ``before`` (ISO 8601), ``limit``; ``download=1``: as a text file
    (``format`` is DRF's renderer switch, hence another name)."""
    qp = request.query_params
    sources = [s for v in qp.getlist("source") for s in v.split(",") if s]
    text = qp.get("download") == "1"
    limit = _int(request, "limit", 50_000 if text else 500, 1, 50_000 if text else 5000)
    try:
        lines = logs.query(
            sources=sources or None,
            level=qp.get("level") or None,
            node=qp.get("node") or None,
            q=qp.get("q") or None,
            regex=qp.get("regex") == "1",
            since=_time(request, "from"),
            until=_time(request, "to"),
            before=_time(request, "before"),
            limit=limit,
        )
    except logs.InvalidRegex as exc:
        raise Problem(400, "Invalid regular expression", str(exc)) from None
    if text:
        resp = HttpResponse(logs.as_text(lines), content_type="text/plain; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="ar3-cockpit-logs.txt"'
        return resp
    return Response({"lines": lines, "limit": limit, "more": len(lines) == limit})


@extend_schema(responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def log_sources(request: Request) -> Response:
    return Response(logs.sources())


# --- ROS graph (§10.4) -------------------------------------------------------------------


@extend_schema(responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def ros_graph(request: Request) -> Response:
    """Nodes, topics (type, publishers, subscribers), services, actions; from the agent."""
    graph = _agent_call(get_agent().graph_detail)
    rates = {}
    try:
        rates = async_to_sync(get_agent().topic_rates)()
    except AgentError:
        pass
    for t in graph.get("topics", []):
        if t["name"] in rates:
            t["rate"] = rates[t["name"]]
    return Response(graph)


@extend_schema(responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def node_params(request: Request, name: str) -> Response:
    return Response(_agent_call(get_agent().node_params, "/" + name.strip("/")))


@extend_schema(responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def topic_rate(request: Request) -> Response:
    """Measures one topic's rate for ``seconds`` (default 2), like `ros2 topic hz`."""
    name = request.query_params.get("name", "")
    if not name.startswith("/"):
        raise Problem(400, "Invalid request", "name must be a topic, e.g. /joint_states")
    try:
        seconds = min(max(float(request.query_params.get("seconds", 2)), 0.5), 10.0)
    except ValueError:
        raise Problem(400, "Invalid request", "seconds must be a number") from None
    return Response(_agent_call(get_agent().topic_rate, name, seconds))


# --- cameras (§10.3) ---------------------------------------------------------------------


@extend_schema(responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def cameras(request: Request) -> Response:
    return Response({"cameras": _agent_call(get_agent().cameras)})


@extend_schema(responses={(200, "image/jpeg"): OpenApiTypes.BINARY})
@api_view(["GET"])
def camera_thumb(request: Request, name: str):
    """The latest thumbnail (JPEG, ≤ 5 new ones a second, made by the agent)."""
    data = _agent_call(get_agent().thumbnail, name)
    resp = HttpResponse(data, content_type="image/jpeg")
    resp["Cache-Control"] = "no-store"
    return resp


# --- alerts (§10.5) ----------------------------------------------------------------------


@extend_schema(methods=["GET"], operation_id="alerts_list", responses={200: OpenApiTypes.OBJECT})
@extend_schema(methods=["POST"], request=AckManyRequest, responses={200: OpenApiTypes.OBJECT})
@api_view(["GET", "POST"])
def alerts_view(request: Request) -> Response:
    """GET: alerts (``status`` open | active | acknowledged | resolved | all, ``severity``,
    ``rule``, ``limit``). POST: acknowledge several (``ids``, ``comment``)."""
    if request.method == "POST":
        ids = request.data.get("ids") or []
        comment = request.data.get("comment") or ""
        done = []
        for alert_id in ids:
            try:
                a = alerts.acknowledge(int(alert_id), request.user.username, comment)
            except (Alert.DoesNotExist, ValueError):
                continue
            done.append(alerts.to_dict(a))
            audit.record_request(request, "alert.ack", f"alert:{a.id}", {"rule": a.rule})
        return Response({"acknowledged": done})
    qp = request.query_params
    qs = Alert.objects.all()
    st = qp.get("status", "open")
    if st == "open":
        qs = qs.filter(status__in=Alert.OPEN)
    elif st != "all":
        qs = qs.filter(status__in=st.split(","))
    if severity := qp.get("severity"):
        qs = qs.filter(severity__in=severity.split(","))
    if rule := qp.get("rule"):
        qs = qs.filter(rule=rule)
    limit = _int(request, "limit", 200, 1, 1000)
    return Response(
        {"alerts": [alerts.to_dict(a) for a in qs[:limit]], "summary": alerts.summary()}
    )


@extend_schema(request=AckRequest, responses={200: OpenApiTypes.OBJECT})
@api_view(["POST"])
def alert_ack(request: Request, alert_id: int) -> Response:
    try:
        a = alerts.acknowledge(alert_id, request.user.username, request.data.get("comment") or "")
    except Alert.DoesNotExist:
        raise Problem(404, "Not found", "no such alert") from None
    audit.record_request(request, "alert.ack", f"alert:{a.id}", {"rule": a.rule})
    return Response(alerts.to_dict(a))


@extend_schema(responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def alert_rules(request: Request) -> Response:
    """The rules of config/cockpit/alerts.yaml."""
    return Response(
        [
            {
                "id": r.id,
                "title": r.title,
                "when": r.when,
                "severity": r.severity,
                "topic": r.topic,
                "subjects": r.root or ("topic" if r.topic else "global"),
            }
            for r in alerts.load()
        ]  # fmt: skip
    )
