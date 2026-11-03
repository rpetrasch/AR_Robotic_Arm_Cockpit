"""The session probe's expected 401 is not logged; other 4xx are (config/logfilters.py)."""

import logging
from types import SimpleNamespace

from config.logfilters import QuietSessionProbe


def record(status: int, path: str) -> logging.LogRecord:
    r = logging.LogRecord("django.request", logging.WARNING, __file__, 1, "x", None, None)
    r.status_code = status
    r.request = SimpleNamespace(path=path)
    return r


def test_only_the_probe_401_is_dropped():
    f = QuietSessionProbe()
    assert not f.filter(record(401, "/api/auth/me"))
    assert f.filter(record(401, "/api/jobs"))
    assert f.filter(record(404, "/api/auth/me"))
    assert f.filter(logging.LogRecord("x", logging.INFO, __file__, 1, "plain", None, None))
