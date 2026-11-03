"""OpenAPI description of the cookie session (drf-spectacular extension for core.auth)."""

from drf_spectacular.authentication import SessionScheme


class CockpitSessionScheme(SessionScheme):
    target_class = "core.auth.SessionAuth"
