"""Sessions, runs and recordings (docs/specs/spec_cockpit.md §12a.2).

A session is one run of a ROS stack (``key`` = container id + creation time, as in
environment.state.Container.session), opened by run_ingest when it first sees the stack and
closed when the stack is gone. The time series (joint_samples, metrics, events, …) are
hypertables created by raw SQL in the migrations; they reference a session by id but have no
foreign key (TimescaleDB keeps them append-only and compressed).
"""

import uuid

from django.db import models


class Session(models.Model):
    SOURCES = [("sim", "sim"), ("real", "real")]
    MODES = [(m, m) for m in ("sim_mock", "sim_gazebo", "real", "remote")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=200, unique=True)  # container id @ created
    host = models.CharField(max_length=200)
    os = models.CharField(max_length=40)
    mode = models.CharField(max_length=20, choices=MODES)
    mode_id = models.CharField(max_length=60, blank=True)  # the cockpit's mode id (modes.py)
    source = models.CharField(max_length=8, choices=SOURCES)
    git_commit = models.CharField(max_length=64, blank=True)
    config_hash = models.CharField(max_length=64, blank=True)  # sha256 of config/*.yaml
    user = models.CharField(max_length=150, blank=True)  # who started it ("cli" otherwise)
    container = models.CharField(max_length=200, blank=True)
    started = models.DateTimeField()
    ended = models.DateTimeField(null=True, blank=True)
    recording = models.BooleanField(default=False)  # REC (§12a.3)
    rate_hz = models.IntegerField(default=50)  # stored joint / gripper rate
    samples = models.BigIntegerField(default=0)  # joint samples written (one per tick)
    dropped = models.BigIntegerField(default=0)  # samples dropped (back-pressure, agent queue)

    class Meta:
        ordering = ["-started"]

    def __str__(self) -> str:
        return f"{self.mode} {self.started:%Y-%m-%d %H:%M}"


class Run(models.Model):
    """A scenario run (§12a.2, §12.3). Started by the job of step scenario.run, one per repeat.

    ``status``: running → succeeded | failed | aborted. ``result`` is the same once finished;
    ``evaluation`` the success criteria one by one, ``steps`` the agent's per-step record
    (status, planning / execution time), ``metrics`` the totals. Artefacts (resolved YAML, log,
    metrics, events) are in COCKPIT_DATA_DIR/runs/<id>/.
    """

    STATUSES = [(s, s) for s in ("running", "succeeded", "failed", "aborted")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="runs")
    scenario_id = models.CharField(max_length=100, db_index=True)
    scenario_version = models.CharField(max_length=64, blank=True)
    resolved_yaml = models.TextField(blank=True)
    seed = models.BigIntegerField(null=True, blank=True)
    result = models.CharField(max_length=20, blank=True)
    metrics = models.JSONField(default=dict, blank=True)
    started = models.DateTimeField()
    ended = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=STATUSES, default="running")
    mode = models.CharField(max_length=20, blank=True)  # sim_mock | sim_gazebo | real
    user = models.CharField(max_length=150, blank=True)
    repeat_index = models.IntegerField(default=1)
    repeat_of = models.IntegerField(default=1)
    failed_step = models.IntegerField(null=True, blank=True)
    error = models.TextField(blank=True)
    steps = models.JSONField(default=list, blank=True)
    evaluation = models.JSONField(default=list, blank=True)
    job_id = models.UUIDField(null=True, blank=True)
    options = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-started"]


class Recording(models.Model):
    """A file outside Postgres (rosbag2 MCAP, LeRobot dataset) and its time range."""

    KINDS = [("mcap", "mcap"), ("lerobot", "lerobot")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        Session, on_delete=models.SET_NULL, null=True, blank=True, related_name="recordings"
    )
    run = models.ForeignKey(Run, on_delete=models.SET_NULL, null=True, blank=True)
    kind = models.CharField(max_length=10, choices=KINDS)
    path = models.CharField(max_length=500)  # relative to the repo
    start = models.DateTimeField()
    end = models.DateTimeField(null=True, blank=True)
    topics = models.JSONField(default=list, blank=True)
    size = models.BigIntegerField(default=0)

    class Meta:
        ordering = ["-start"]
