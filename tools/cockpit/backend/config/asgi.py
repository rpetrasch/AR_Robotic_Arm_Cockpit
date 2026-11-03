"""ASGI entry point: HTTP via Django, WebSockets via Channels (docs/specs/spec_cockpit.md §4,
§11.1).

uvicorn config.asgi:application --host 127.0.0.1 --port 8400
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django_app = get_asgi_application()  # before importing consumers: sets up the app registry

from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402

from monitoring.logs import install_handler  # noqa: E402

from .routing import websocket_urlpatterns  # noqa: E402

install_handler("web")

application = ProtocolTypeRouter(
    {
        "http": django_app,
        "websocket": AllowedHostsOriginValidator(
            AuthMiddlewareStack(URLRouter(websocket_urlpatterns))
        ),
    }
)
