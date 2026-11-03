import json

import pytest
from django.contrib.auth.models import User
from django.test import Client

from core.runner import Result

PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def _isolated(settings, tmp_path):
    """Every test: own data dir, real (not fake) mode unless the test turns it on."""
    settings.DATA_DIR = tmp_path / "data"
    settings.SCENARIOS_DIR = tmp_path / "scenarios"
    settings.FAKE = False
    settings.FAKE_PLATFORM = "macos"


@pytest.fixture
def user(db):
    return User.objects.create_user("alice", password=PASSWORD)


@pytest.fixture
def client_in(user) -> Client:
    c = Client()
    c.force_login(user)
    return c


def ps_line(service: str, state: str = "running", cid: str | None = None) -> str:
    return json.dumps(
        {
            "ID": cid or f"id-{service}",
            "Names": f"ar3-{service}-1",
            "Labels": f"com.docker.compose.project=ar3,com.docker.compose.service={service}",
            "State": state,
            "Status": "Up 2 minutes",
        }
    )


def ok(stdout: str = "") -> Result:
    return Result(("x",), 0, stdout)


def fail(stderr: str, code: int = 1) -> Result:
    return Result(("x",), code, "", stderr)


READY = {
    "ok": True,
    "mode": "sim",
    "ready": True,
    "waiting_for": [],
    "components": {
        "gazebo": {"status": "ready", "detail": "/clock 1000 Hz"},
        "controllers": {"status": "ready", "detail": "3 controllers active"},
        "move_group": {"status": "ready", "detail": "/move_action available"},
        "cameras": {"status": "warn", "detail": "cam_wrist 5.0/15 Hz"},
        "safety": {"status": "off", "detail": "REAL only"},
    },
}


class StubAgent:
    """An agent that answers ``health`` (or raises ``error``) and counts stops."""

    def __init__(self, health: dict | None = None, error: Exception | None = None):
        self._health, self.error, self.stops = health or READY, error, 0

    async def health(self) -> dict:
        if self.error:
            raise self.error
        return self._health

    async def stop(self) -> dict:
        self.stops += 1
        return {"cancelled": [{"action": "/move_action", "goals": 1}], "held": [], "errors": []}


RAW_TABLES = ("log_lines", "joint_samples", "gripper_samples", "safety_samples", "metrics",
              "events", "detections", "trajectories")  # fmt: skip


@pytest.fixture
def clean_raw_tables(django_db_blocker):
    """For transactional tests that write the hypertables over their own connection (log
    sink, ingest): Django only flushes model tables, so empty the raw ones afterwards."""
    yield
    from django.db import connection

    with django_db_blocker.unblock(), connection.cursor() as cur:
        cur.execute(f"TRUNCATE {', '.join(RAW_TABLES)}")


def insert_lines(rows) -> None:
    """Log lines through Django's connection (rolled back with the test)."""
    from django.db import connection

    with connection.cursor() as cur:
        cur.executemany(
            "INSERT INTO log_lines (time, source, level, node, text) VALUES (%s, %s, %s, %s, %s)",
            [(t, s, lv, n, x) for s, lv, x, n, t in rows],
        )
