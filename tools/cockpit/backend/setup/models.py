import uuid

from django.db import models


class Job(models.Model):
    """One execution of a step (docs/specs/spec_cockpit.md §8.3). The log is a file under
    COCKPIT_DATA_DIR/logs/jobs/, one line per output line: ``seq<TAB>ts<TAB>stream<TAB>text``."""

    QUEUED, RUNNING = "queued", "running"
    SUCCEEDED, FAILED, CANCELLED, TIMEOUT = "succeeded", "failed", "cancelled", "timeout"
    ACTIVE = (QUEUED, RUNNING)
    STATUSES = [(s, s) for s in (QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED, TIMEOUT)]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    step_id = models.CharField(max_length=64)
    params = models.JSONField(default=dict, blank=True)
    command = models.TextField()  # the CLI equivalent, recorded with the job (principle 8)
    user = models.CharField(max_length=150)
    mutating = models.BooleanField(default=True)
    status = models.CharField(max_length=16, choices=STATUSES, default=QUEUED, db_index=True)
    exit_code = models.IntegerField(null=True, blank=True)
    cancel_requested = models.BooleanField(default=False)
    matches = models.JSONField(default=list, blank=True)  # error-pattern hits (§8.3)
    lines = models.IntegerField(default=0)
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    started = models.DateTimeField(null=True, blank=True)
    finished = models.DateTimeField(null=True, blank=True)
    log_path = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self) -> str:
        return f"{self.step_id} {self.status} ({self.id})"


class CheckRecord(models.Model):
    """The last result of each check, so the checklist shows without re-running everything."""

    check_id = models.CharField(max_length=64, primary_key=True)
    data = models.JSONField()
    updated = models.DateTimeField(auto_now=True)
