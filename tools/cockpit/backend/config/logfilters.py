"""Logging filters (config/settings.py LOGGING)."""

import logging


class QuietSessionProbe(logging.Filter):
    """The frontend asks /api/auth/me on every load; the answer 401 just means "nobody is
    signed in yet". Expected, so not logged as a warning. Other 401s are still logged."""

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "status_code", None) != 401:
            return True
        request = getattr(record, "request", None)
        return getattr(request, "path", "") != "/api/auth/me"
