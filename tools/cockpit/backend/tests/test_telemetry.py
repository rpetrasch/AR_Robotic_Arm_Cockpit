"""Phase C4 data capture (docs/specs/spec_cockpit.md §12a, §18 C4): sessions, the ingest (rates,
gaps, back-pressure), REC, the history API, export to Parquet / CSV, retention and
compression, recordings, and the export / rosbag steps."""

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from asgiref.sync import async_to_sync
from django.db import connection

from core import agent, fake
from core.runner import get_runner
from environment import state as stack_state
from setup import executor, service
from setup.models import Job
from telemetry import export, ingest, policies, queries, recordings, sessions
from telemetry.models import Recording, Session

pytestmark = pytest.mark.django_db


@pytest.fixture
def fake_mode(settings, monkeypatch):
    settings.FAKE = True
    monkeypatch.setattr(agent, "FAKE_READY_AFTER_S", 0.0)
    return settings.DATA_DIR


@pytest.fixture
def sim_stack(fake_mode):
    fake.save_state(fake_mode, {"stack": {"service": "mac", "ar3_mode": "sim",
                                          "started": time.time()}})  # fmt: skip
    return fake_mode


def read_state():
    return async_to_sync(stack_state.read)(get_runner())


def make_session(**kw) -> Session:
    defaults = {
        "key": f"k{time.monotonic_ns()}",
        "host": "h",
        "os": "macos",
        "mode": "sim_gazebo",
        "source": "sim",
        "started": datetime.now(UTC),
        "recording": True,
        "rate_hz": 50,
    }
    return Session.objects.create(**{**defaults, **kw})  # fmt: skip


# --- sessions ---------------------------------------------------------------------------------


def test_a_stack_opens_a_session_and_closing_it_ends_it(sim_stack, settings):
    s = async_to_sync(sessions.sync)(read_state())
    assert s.mode == "sim_gazebo" and s.source == "sim" and s.mode_id == "sim_gazebo"
    assert s.recording is True  # TELEMETRY_RECORD_SIM=1
    assert s.git_commit == fake.HEAD and len(s.config_hash) == 16
    assert s.user == "cli"  # no env.start job
    assert async_to_sync(sessions.sync)(read_state()).id == s.id  # idempotent
    fake.save_state(sim_stack, {"stack": None})
    assert async_to_sync(sessions.sync)(read_state()) is None
    s.refresh_from_db()
    assert s.ended is not None
    kinds = [e["kind"] for e in __import__("telemetry.events").events.query(session_id=s.id)]
    assert kinds == ["session_end", "session_start"]


@pytest.mark.parametrize(
    ("robot", "record_sim", "expected"),
    [("real", False, True), ("gazebo", True, True), ("mock", False, False),
     ("remote", True, False)],
)  # fmt: skip
def test_recording_defaults(settings, robot, record_sim, expected):
    settings.TELEMETRY_RECORD_SIM = record_sim
    assert sessions.default_recording(robot) is expected


def test_the_user_who_started_it(sim_stack):
    service.create_job("env.start", {"mode": "sim_gazebo"}, "alice", "macos")
    assert async_to_sync(sessions.sync)(read_state()).user == "alice"


def test_rec_toggle_api(client_in, sim_stack):
    assert client_in.get("/api/sessions/current").json()["session"] is None
    r = client_in.post("/api/sessions/current/recording", {"recording": False},
                       content_type="application/json")  # fmt: skip
    assert r.status_code == 409  # no session yet
    s = async_to_sync(sessions.sync)(read_state())
    r = client_in.post("/api/sessions/current/recording", {"recording": False},
                       content_type="application/json")  # fmt: skip
    assert r.status_code == 200 and r.json()["recording"] is False
    s.refresh_from_db()
    assert s.recording is False
    assert client_in.post("/api/sessions/current/recording", {"recording": "no"},
                          content_type="application/json").status_code == 400  # fmt: skip
    from accounts.models import AuditEntry

    assert AuditEntry.objects.filter(action="telemetry.rec").count() == 1


# --- ingest -----------------------------------------------------------------------------------


def sample_msg(t0: float, n: int, rate: int = 50, names=("joint_1", "gripper_jaw1_joint")):
    return {
        "names": list(names),
        "samples": [
            {"t": round(t0 + i / rate, 3), "p": [0.1, 0.002], "v": [0.0, 0.0], "e": [0.0, 0.0],
             "cp": [0.11, None], "cv": [0.0, None]}
            for i in range(n)
        ],
        "events": [{"kind": "trajectory", "t": t0, "names": ["joint_1"],
                    "points": [{"t": 0.0, "p": [0.0]}, {"t": 1.0, "p": [0.5]}]},
                   {"kind": "estop", "t": t0, "estop": True, "homed": True,
                    "in_workspace": True, "faults": []}],
        "rates": {"/joint_states": {"hz": 100.0, "age_s": 0.01}},
        "rtf": 0.97,
    }  # fmt: skip


def test_rows_split_joints_gripper_metrics_and_events():
    rows = ingest.rows_for(sample_msg(1_790_000_000.0, 5), "sid")
    assert len(rows["joint_samples"]) == 5 and len(rows["gripper_samples"]) == 5
    j = rows["joint_samples"][0]
    assert j[3:] == ("joint_1", 0.1, 0.0, 0.0, 0.11, 0.0)
    assert {m[2] for m in rows["metrics"]} == {"topic_hz", "sim_rtf"}
    assert len(rows["trajectories"]) == 1
    assert [e[3] for e in rows["events"]] == ["trajectory", "estop"]


def test_back_pressure_keeps_only_the_10_hz_grid():
    msg = sample_msg(1_790_000_000.0, 50)  # one second at 50 Hz
    kept, dropped = ingest.decimate(msg, 50, 10)
    assert len(kept["samples"]) == 10 and dropped == 40
    assert all(round(s["t"] * 10) == s["t"] * 10 for s in kept["samples"])

    rec = ingest.Recorder(make_session(), agent=object(), store=object())
    rec.offer(sample_msg(time.time() - 30, 50))  # 30 s behind: the database lags
    rec.offer(sample_msg(time.time(), 50))
    assert rec.degraded and rec.dropped == 40
    assert rec.lag() > ingest.LAG_LIMIT_S
    rec.queue.clear()
    rec.offer(sample_msg(time.time(), 50))
    assert not rec.degraded  # caught up


@pytest.mark.django_db(transaction=True)
def test_ingest_stores_the_fake_agents_50_hz_without_gaps(sim_stack, clean_raw_tables):
    """The C4 criterion in small: the stored rate is the configured one, no gaps."""

    async def go():
        ing = ingest.Ingest()
        st = await stack_state.read(get_runner())
        await ing.step(st)
        await asyncio.sleep(3.2)
        await ing.step(st)
        sid = ing.recorder.session.id
        await ing.stop()
        return sid

    sid = async_to_sync(go)()
    counts = queries.counts(str(sid))
    joint_1 = next(j for j in counts["per_joint"] if j["joint"] == "joint_1")
    assert 2.5 * 50 <= joint_1["samples"] <= 3.3 * 50
    assert abs(joint_1["rate_hz"] - 50) < 0.5
    assert {j["joint"] for j in counts["per_joint"]} == {f"joint_{i}" for i in range(1, 7)}
    assert counts["gripper_samples"] == joint_1["samples"]
    assert queries.gaps(str(sid), "joint_1", 0.02)["gaps"] == 0
    s = Session.objects.get(pk=sid)
    assert s.samples == joint_1["samples"] and s.dropped == 0
    assert counts["metrics"] > 0  # topic rates, RTF


@pytest.mark.django_db(transaction=True)
def test_rec_off_stores_nothing(sim_stack, clean_raw_tables, settings):
    settings.TELEMETRY_RECORD_SIM = False

    async def go():
        ing = ingest.Ingest()
        st = await stack_state.read(get_runner())
        await ing.step(st)
        await asyncio.sleep(1.5)
        sid = ing.recorder.session.id
        await ing.stop()
        return sid

    sid = async_to_sync(go)()
    assert queries.counts(str(sid))["joint_samples"] == 0


def test_live_window_and_websocket(client_in, sim_stack, monkeypatch):
    monkeypatch.setattr(ingest, "LIVE_WINDOW_S", 2)

    async def go():
        task = asyncio.ensure_future(ingest.live())
        await asyncio.sleep(1.4)
        task.cancel()

    async_to_sync(go)()
    window = client_in.get("/api/telemetry/live").json()
    assert window["names"][0] == "joint_1" and len(window["names"]) == 7
    steps = {
        round(b["t"] - a["t"], 3)
        for a, b in zip(window["samples"], window["samples"][1:], strict=False)
    }
    assert steps == {0.1}  # 10 Hz


# --- history ----------------------------------------------------------------------------------


def _insert_samples(session: Session, seconds: float, rate: int = 50, t0: datetime | None = None):
    t0 = t0 or datetime.now(UTC) - timedelta(seconds=seconds)
    rows = []
    for i in range(int(seconds * rate)):
        t = t0 + timedelta(seconds=i / rate)
        for j in ("joint_1", "joint_2"):
            rows.append((t, session.id, None, j, 0.01 * i, 0.5, 0.0, 0.01 * i + 0.001, 0.5))
    with connection.cursor() as cur:
        cur.executemany(
            "INSERT INTO joint_samples (time, session_id, run_id, joint, position, velocity, "
            "effort, cmd_position, cmd_velocity) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            rows,
        )
    return t0


def test_joint_history_buckets(client_in):
    s = make_session()
    t0 = _insert_samples(s, 20)
    body = client_in.get("/api/telemetry/joints", {"session": str(s.id), "bucket": "1"}).json()
    assert body["names"] == ["joint_1", "joint_2"]
    j1 = body["series"]["joint_1"]
    assert 20 <= len(j1["t"]) <= 21 and sum(j1["n"]) == 1000
    assert j1["pos_min"][1] < j1["pos"][1] < j1["pos_max"][1]
    auto = client_in.get("/api/telemetry/joints", {"session": str(s.id)}).json()
    assert len(auto["series"]["joint_1"]["t"]) <= queries.MAX_POINTS
    part = client_in.get(
        "/api/telemetry/joints",
        {"session": str(s.id), "joints": "joint_2", "bucket": "1",
         "from": (t0 + timedelta(seconds=5)).isoformat(),
         "to": (t0 + timedelta(seconds=9.99)).isoformat()},
    ).json()  # fmt: skip
    assert part["names"] == ["joint_2"] and sum(part["series"]["joint_2"]["n"]) == 250
    assert client_in.get("/api/telemetry/joints").status_code == 400
    detail = client_in.get(f"/api/sessions/{s.id}").json()
    assert detail["counts"]["joint_samples"] == 2000
    assert detail["counts"]["per_joint"][0]["rate_hz"] == pytest.approx(50, abs=0.1)
    assert client_in.get(f"/api/sessions/{s.id}/gaps").json()["gaps"] == 0


def test_gaps_are_found():
    s = make_session()
    t0 = _insert_samples(s, 2)
    _insert_samples(s, 2, t0=t0 + timedelta(seconds=3))  # 1 s missing
    g = queries.gaps(str(s.id), "joint_1", 0.02)
    assert g["gaps"] == 1 and g["largest"][0]["seconds"] == pytest.approx(1.02, abs=0.01)


# --- export -----------------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["parquet", "csv"])
def test_export_is_readable_by_pandas(settings, tmp_path, fmt):
    import pandas as pd

    settings.EXPORT_DIR = tmp_path / "exports"
    s = make_session()
    _insert_samples(s, 4)
    with connection.cursor() as cur:
        cur.execute("INSERT INTO events (time, session_id, kind, payload) VALUES (now(), %s, "
                    "'alert', '{\"rule\": \"x\"}')", [s.id])  # fmt: skip
    out = export.export(str(s.id), fmt, log=lambda _line: None)
    meta = json.loads((out / "session.json").read_text())
    assert meta["files"] == {f"joint_samples.{fmt}": 400, f"events.{fmt}": 1}
    path = out / f"joint_samples.{fmt}"
    df = pd.read_parquet(path) if fmt == "parquet" else pd.read_csv(path, parse_dates=["time"])
    assert len(df) == 400 and set(df["joint"]) == {"joint_1", "joint_2"}
    assert list(df.columns) == ["time", "session_id", "run_id", "joint", "position", "velocity",
                                "effort", "cmd_position", "cmd_velocity"]  # fmt: skip
    if fmt == "parquet":
        assert str(df["time"].dt.tz) == "UTC"
    assert (df["session_id"] == str(s.id)).all()
    assert export.list_exports()[0]["session"] == str(s.id)


def test_export_errors(settings, tmp_path):
    settings.EXPORT_DIR = tmp_path
    with pytest.raises(export.ExportError):
        export.export("00000000-0000-0000-0000-000000000000")
    with pytest.raises(export.ExportError):
        export.export(str(make_session().id), "xlsx")


@pytest.mark.django_db(transaction=True)  # the step exports on its own thread and connection
def test_export_step_and_download(client_in, settings, tmp_path, fake_mode, clean_raw_tables):
    settings.EXPORT_DIR = tmp_path / "exports"
    s = make_session()
    _insert_samples(s, 1)
    r = client_in.post("/api/telemetry/export", {"session": str(s.id), "format": "csv"},
                       content_type="application/json")  # fmt: skip
    assert r.status_code == 201
    job = r.json()
    assert job["command"] == f"make telemetry-export SESSION={s.id} FORMAT=csv"
    assert job["mutating"] is False
    done = async_to_sync(executor.execute)(Job.objects.get(pk=job["id"]))
    assert done.status == "succeeded", service.read_log(done)
    name = client_in.get("/api/telemetry/export").json()[0]["name"]
    f = client_in.get(f"/api/telemetry/exports/{name}/joint_samples.csv")
    assert f.status_code == 200 and b"joint_1" in f.getvalue()
    assert client_in.get(f"/api/telemetry/exports/{name}/..%2F..%2Fsecret").status_code == 404
    bad = client_in.post("/api/telemetry/export", {"session": "nope"},
                         content_type="application/json")  # fmt: skip
    assert bad.status_code == 400


def test_ar3ctl_export_and_sessions(settings, tmp_path, capsys):
    from ar3ctl import cli

    settings.EXPORT_DIR = tmp_path
    s = make_session()
    _insert_samples(s, 1)
    assert (
        cli.cmd_telemetry_export(
            type(
                "A",
                (),
                {
                    "session": str(s.id),
                    "format": "parquet",
                    "run": None,
                    "since": None,
                    "until": None,
                },
            )()  # fmt: skip
        )
        == 0
    )
    assert "joint_samples.parquet: 100 rows" in capsys.readouterr().out


# --- retention and compression ------------------------------------------------------------


def test_retention_from_the_settings(settings):
    settings.TELEMETRY_RETENTION = "metrics=30,log_lines=7,events=off"
    r = policies.retention()
    assert r["metrics"] == 30 and r["log_lines"] == 7 and r["events"] == 0
    assert r["joint_samples"] == 0
    applied = policies.apply()
    assert applied == r
    with connection.cursor() as cur:
        cur.execute(
            "SELECT hypertable_name, config->>'drop_after' FROM timescaledb_information.jobs "
            "WHERE proc_name = 'policy_retention' ORDER BY 1"
        )
        assert cur.fetchall() == [("log_lines", "7 days"), ("metrics", "30 days")]
    settings.TELEMETRY_RETENTION = "nope=3"
    with pytest.raises(ValueError):
        policies.retention()


def test_compression_keeps_every_row():
    s = make_session()
    _insert_samples(s, 4)
    before = queries.counts(str(s.id))["joint_samples"]
    stats = {r["table"]: r for r in policies.compress(everything=True)}
    assert stats["joint_samples"]["compressed_chunks"] == stats["joint_samples"]["chunks"] >= 1
    assert stats["joint_samples"]["bytes_after"] is not None
    assert queries.counts(str(s.id))["joint_samples"] == before == 400
    assert policies.compress() is not None  # the policy jobs run (nothing older than a day)


def test_storage_api(client_in):
    body = client_in.get("/api/telemetry/storage").json()
    assert {t["table"] for t in body["tables"]} >= {"joint_samples", "log_lines"}
    assert body["retention_days"]["metrics"] == 30


def test_the_reader_role_can_read_the_series():
    with connection.cursor() as cur:
        cur.execute("SELECT has_table_privilege('ar3_reader', 'joint_samples', 'SELECT'), "
                    "has_table_privilege('ar3_reader', 'joint_samples', 'INSERT'), "
                    "has_table_privilege('ar3_reader', 'joint_stats_1m', 'SELECT')")  # fmt: skip
        assert cur.fetchone() == (True, False, True)


def test_continuous_aggregates_exist():
    with connection.cursor() as cur:
        cur.execute("SELECT view_name FROM timescaledb_information.continuous_aggregates "
                    "ORDER BY 1")  # fmt: skip
        assert [r[0] for r in cur.fetchall()] == ["joint_stats_1m", "joint_stats_1s"]


# --- recordings: the rosbag step --------------------------------------------------------------


def test_rosbag_step_records_and_registers(client_in, sim_stack):
    s = async_to_sync(sessions.sync)(read_state())
    r = client_in.post("/api/jobs", {"step_id": "rosbag.record",
                                     "params": {"duration": "5"}},
                       content_type="application/json")  # fmt: skip
    assert r.status_code == 201
    assert r.json()["command"] == "make bag PRESET=default DURATION=5"
    job = async_to_sync(executor.execute)(Job.objects.get(pk=r.json()["id"]))
    assert job.status == "succeeded", service.read_log(job)
    rec = Recording.objects.get()
    assert rec.kind == "mcap" and rec.path.startswith("data/bags/") and rec.session_id == s.id
    assert rec.end - rec.start == timedelta(seconds=5)
    assert {t["name"] for t in rec.topics} == {"/joint_states", "/rosout"}
    assert client_in.get("/api/recordings").json()[0]["path"] == rec.path


def test_rosbag_without_a_stack_fails(fake_mode):
    job = service.create_job("rosbag.record", {"duration": "5"}, "alice", "macos")
    job = async_to_sync(executor.execute)(job)
    assert job.status == "failed"
    assert any("No ROS stack is running" in x["text"] for x in service.read_log(job))


def test_read_bag(tmp_path):
    bag = tmp_path / "b"
    bag.mkdir()
    (bag / "b_0.mcap").write_bytes(b"x" * 10)
    (bag / "metadata.yaml").write_text(
        "rosbag2_bagfile_information:\n  duration: {nanoseconds: 2500000000}\n"
        "  starting_time: {nanoseconds_since_epoch: 1790000000000000000}\n  message_count: 3\n"
        "  topics_with_message_count:\n  - topic_metadata: {name: /a, type: std_msgs/msg/String}"
        "\n    message_count: 3\n"
    )
    meta = recordings.read_bag(bag)
    assert meta["start"] == datetime.fromtimestamp(1_790_000_000, UTC)
    assert (meta["end"] - meta["start"]).total_seconds() == 2.5
    assert meta["topics"] == [{"name": "/a", "type": "std_msgs/msg/String", "messages": 3}]


# --- jobs: non-mutating ones run next to a mutating one ------------------------------------


@pytest.mark.django_db(transaction=True)
def test_non_mutating_jobs_do_not_wait_for_a_mutating_one(sim_stack, monkeypatch):
    from setup.management.commands.run_jobs import Command

    long_job = service.create_job("ws.build", None, "alice", "macos")
    bag = service.create_job("rosbag.record", {"duration": "1"}, "alice", "macos")
    finished = {}
    real_execute = executor.execute

    async def timed(job):
        if job.step_id == "ws.build":
            await asyncio.sleep(1.5)
        out = await real_execute(job)
        finished[job.step_id] = time.monotonic()
        return out

    monkeypatch.setattr(executor, "execute", timed)
    async_to_sync(Command().loop)(True)
    assert finished["rosbag.record"] < finished["ws.build"]
    assert Job.objects.get(pk=long_job.pk).status == "succeeded"
    assert Job.objects.get(pk=bag.pk).status == "succeeded"
