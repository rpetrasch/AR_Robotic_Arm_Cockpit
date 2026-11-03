"""Pre-flight progress of a REAL stack (docs/specs/spec_cockpit.md §9.4)."""

from django.db import models


class Preflight(models.Model):
    """One per REAL stack container (``session`` = container id + creation time), so a new
    stack starts the wizard from the beginning."""

    session = models.CharField(max_length=200, unique=True)
    steps = models.JSONField(default=dict)  # step id -> {"status", "detail", "at", "user"}
    estop_phase = models.CharField(max_length=20, blank=True)  # "" | press | reset
    estop_since = models.DateTimeField(null=True, blank=True)
    updated = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"preflight {self.session}"
