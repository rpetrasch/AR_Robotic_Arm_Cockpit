"""Session authentication that answers 401 (not 403) when the session is missing, so the
frontend can tell "sign in" from "refused" (docs/specs/spec_cockpit.md §7.3)."""

from rest_framework.authentication import SessionAuthentication


class SessionAuth(SessionAuthentication):
    def authenticate_header(self, request):
        return "Session"

    def check_csrf(self, request) -> None:
        """CSRF check for views that run before a session exists (login, first user)."""
        self.enforce_csrf(request)


def client_ip(request) -> str:
    return request.META.get("REMOTE_ADDR", "") or "unknown"


LOCAL_ADDRESSES = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}


def is_local(request) -> bool:
    """True for a request from this machine. The cockpit has no reverse proxy, so
    REMOTE_ADDR is the peer; X-Forwarded-For is deliberately ignored."""
    return client_ip(request) in LOCAL_ADDRESSES
