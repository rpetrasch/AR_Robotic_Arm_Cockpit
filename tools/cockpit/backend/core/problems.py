"""RFC 9457 problem JSON for every API error (docs/specs/spec_cockpit.md §15).

Body: ``{"type", "title", "status", "detail", "hint"?, "docs"?}`` with content type
``application/problem+json``. Raise ``Problem`` for errors with a hint or a docs link.
"""

from __future__ import annotations

from rest_framework import status as http
from rest_framework.exceptions import APIException, Throttled, ValidationError
from rest_framework.views import exception_handler as drf_handler

DOCS = "docs/manual/cockpit.md"


class Problem(APIException):
    def __init__(
        self,
        status: int,
        title: str,
        detail: str = "",
        *,
        hint: str = "",
        docs: str = "",
        type_: str = "about:blank",
        extra: dict | None = None,
    ):
        self.status_code = status
        self.title = title
        self.hint, self.docs, self.type, self.extra = hint, docs, type_, extra or {}
        super().__init__(detail or title)


def _titles(status: int) -> str:
    return {
        400: "Invalid request",
        401: "Not signed in",
        403: "Forbidden",
        404: "Not found",
        405: "Method not allowed",
        409: "Conflict",
        429: "Too many requests",
    }.get(status, "Error")


def exception_handler(exc, context):
    response = drf_handler(exc, context)
    if response is None:
        return None
    status = response.status_code
    if isinstance(exc, Problem):
        body = {"type": exc.type, "title": exc.title, "status": status, "detail": str(exc.detail)}
        if exc.hint:
            body["hint"] = exc.hint
        if exc.docs:
            body["docs"] = exc.docs
        body.update(exc.extra)
    else:
        if isinstance(exc, ValidationError):
            detail = "; ".join(_flatten(exc.detail)) or "invalid input"
            body = {"type": "about:blank", "title": _titles(400), "status": status}
            body["detail"] = detail
            body["errors"] = exc.detail
        else:
            detail = response.data.get("detail", "") if isinstance(response.data, dict) else ""
            body = {"type": "about:blank", "title": _titles(status), "status": status}
            body["detail"] = str(detail)
        if isinstance(exc, Throttled) and exc.wait is not None:
            body["retry_after_s"] = int(exc.wait)
    if status == http.HTTP_401_UNAUTHORIZED or status == http.HTTP_403_FORBIDDEN:
        body.setdefault("docs", DOCS)
    response.data = body
    response.content_type = "application/problem+json"
    return response


def _flatten(detail, prefix: str = "") -> list[str]:
    if isinstance(detail, dict):
        return [m for k, v in detail.items() for m in _flatten(v, f"{k}: ")]
    if isinstance(detail, list):
        return [m for v in detail for m in _flatten(v, prefix)]
    return [f"{prefix}{detail}"]
