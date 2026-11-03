"""Alerts (docs/specs/spec_cockpit.md §10.5). Log lines are the hypertable ``log_lines`` (raw SQL in
the migration, written by monitoring.logs)."""

from django.db import models


class Alert(models.Model):
    """One alert per rule + subject while it is open (dedup, §10.5).

    ``active`` → ``acknowledged`` (a user, with an optional comment) → ``resolved`` (the
    condition cleared). An acknowledged alert whose condition returns stays acknowledged;
    a resolved one is history, and the condition's return opens a new alert.
    """

    ACTIVE, ACKNOWLEDGED, RESOLVED = "active", "acknowledged", "resolved"
    OPEN = (ACTIVE, ACKNOWLEDGED)
    STATUSES = [(s, s) for s in (ACTIVE, ACKNOWLEDGED, RESOLVED)]
    SEVERITIES = [(s, s) for s in ("info", "warn", "error", "critical")]

    rule = models.CharField(max_length=64)
    subject = models.CharField(max_length=200, blank=True)  # component id, topic, joint, …
    severity = models.CharField(max_length=10, choices=SEVERITIES)
    status = models.CharField(max_length=14, choices=STATUSES, default=ACTIVE, db_index=True)
    message = models.TextField()
    details = models.JSONField(default=dict, blank=True)
    first_seen = models.DateTimeField(auto_now_add=True, db_index=True)
    last_seen = models.DateTimeField()
    count = models.IntegerField(default=1)  # evaluations / matches while open
    acknowledged_by = models.CharField(max_length=150, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    comment = models.TextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    session_id = models.UUIDField(null=True, blank=True)

    class Meta:
        ordering = ["-first_seen"]
        constraints = [
            models.UniqueConstraint(
                fields=["rule", "subject"],
                condition=models.Q(status__in=("active", "acknowledged")),
                name="one_open_alert_per_rule_subject",
            )
        ]

    def __str__(self) -> str:
        return f"{self.rule}[{self.subject}] {self.status}"
