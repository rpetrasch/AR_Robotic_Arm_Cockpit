"""Audit log helpers (docs/specs/spec_cockpit.md §11.3)."""

from __future__ import annotations

from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from core.auth import client_ip

from .models import AuditEntry

SECRET_MARKERS = ("password", "secret", "token", "key")


def _clean(payload: dict) -> dict:
    """Never store a secret in the audit log, whatever a caller passes."""
    return {
        k: "<redacted>" if any(m in k.lower() for m in SECRET_MARKERS) else v
        for k, v in (payload or {}).items()
    }


def record(actor: str, action: str, obj: str = "", payload: dict | None = None, ip: str = ""):
    return AuditEntry.objects.create(
        actor=actor or "?", action=action, obj=obj, ip=ip, payload=_clean(payload or {})
    )


def record_request(request, action: str, obj: str = "", payload: dict | None = None):
    user = getattr(request, "user", None)
    actor = user.get_username() if user is not None and user.is_authenticated else "?"
    return record(actor, action, obj, payload, client_ip(request))


def count_since(action: str, obj: str, ip: str, since: datetime) -> int:
    return AuditEntry.objects.filter(action=action, obj=obj, ip=ip, ts__gt=since).count()


def last_ts(action: str, obj: str, ip: str) -> datetime | None:
    entry = AuditEntry.objects.filter(action=action, obj=obj, ip=ip).order_by("-ts").first()
    return entry.ts if entry else None


def prune(now: datetime | None = None) -> int:
    cutoff = (now or timezone.now()) - timedelta(days=settings.AUDIT_RETENTION_DAYS)
    deleted, _ = AuditEntry.objects.filter(ts__lt=cutoff).delete()
    return deleted
