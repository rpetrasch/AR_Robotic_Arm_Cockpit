from django.urls import path

from environment.consumers import StatusConsumer
from monitoring.consumers import LogsConsumer, TelemetryConsumer
from scenarios.consumers import RunConsumer
from setup.consumers import ChecksConsumer, JobConsumer

websocket_urlpatterns = [
    path("ws/status", StatusConsumer.as_asgi()),
    path("ws/jobs/<uuid:job_id>", JobConsumer.as_asgi()),
    path("ws/checks", ChecksConsumer.as_asgi()),
    path("ws/logs", LogsConsumer.as_asgi()),
    path("ws/telemetry", TelemetryConsumer.as_asgi()),
    path("ws/runs/<uuid:run_id>", RunConsumer.as_asgi()),
]
