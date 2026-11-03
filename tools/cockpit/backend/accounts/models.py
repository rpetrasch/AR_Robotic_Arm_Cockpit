from django.db import models


class AuditEntry(models.Model):
    """Who did what and when (docs/specs/spec_cockpit.md §11.3). Kept AUDIT_RETENTION_DAYS."""

    ts = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.CharField(max_length=150)  # username, "?" for unknown, "system" for CLI
    action = models.CharField(max_length=64)  # e.g. auth.login, user.create
    obj = models.CharField(max_length=200, blank=True)  # e.g. user:alice
    ip = models.CharField(max_length=64, blank=True)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-ts", "-id"]
        indexes = [models.Index(fields=["action", "obj", "ip", "ts"])]

    def __str__(self) -> str:
        return f"{self.ts:%Y-%m-%d %H:%M:%S} {self.actor} {self.action} {self.obj}"
