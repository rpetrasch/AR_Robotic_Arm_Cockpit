"""Phase C4 monitoring (docs/specs/spec_cockpit.md §10, §18 C4): the alert rules and their
lifecycle, dead containers and container_down, the log buffer and its search, the collector,
metrics, and the monitoring API."""

import ast
import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from asgiref.sync import async_to_sync
from django.db import connection

from core import agent, fake
from environment import state as stack_state
from environment import status
from monitoring import alerts, collector, logs, metrics
from monitoring.consumers import line_filter
from monitoring.models import Alert
from tests.conftest import insert_lines

pytestmark = pytest.mark.django_db


@pytest.fixture
def fake_mode(settings, monkeypatch):
    settings.FAKE = True
    monkeypatch.setattr(agent, "FAKE_READY_AFTER_S", 0.0)
    return settings.DATA_DIR


def expr(text: str) -> ast.Expression:
    return alerts.parse({"id": "t", "title": "t", "when": text, "severity": "warn"}).expr


# --- the rule language ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("when", "facts", "fires"),
    [
        ('component.status == "down" and mode != "STOPPED"',
         {"component": {"status": "down"}, "mode": "SIM · GAZEBO"}, True),
        ('component.status == "down" and mode != "STOPPED"',
         {"component": {"status": "down"}, "mode": "STOPPED"}, False),
        ("age_s > 1.0", {"age_s": 2.5}, True),
        ("age_s > 1.0", {"age_s": None}, False),  # missing fact: never fires
        ("age_s > 1.0", {}, False),
        ("safety.estop", {"safety": {"estop": True}}, True),
        ("safety.estop", {"safety": {}}, False),
        ("not safety.estop", {"safety": {"estop": False}}, True),
        ("camera.drift_mm > 3 or camera.drift_deg > 0.5", {"camera": {"drift_deg": 0.7}}, True),
        ('log.match.severity in ["error", "critical"]',
         {"log": {"match": {"severity": "critical"}}}, True),
        ('log.match.severity in ["error", "critical"]', {"log": {"match": {"severity": "warn"}}},
         False),
        ("disk.free_gb < 15", {"disk": {"free_gb": "x"}}, False),  # type mismatch: false
        ("0 < joint.margin_deg < 5", {"joint": {"margin_deg": 3}}, True),
    ],
)  # fmt: skip
def test_rule_expressions(when, facts, fires):
    assert alerts.evaluate(expr(when), facts) is fires


@pytest.mark.parametrize(
    "when",
    ["__import__('os').system('true')", "mode.upper()", "[x for x in mode]", "mode[0]",
     "lambda: 1", "a + b"],
)  # fmt: skip
def test_rules_cannot_call_or_compute(when):
    with pytest.raises(alerts.RuleError):
        expr(when)


def test_the_shipped_rules_load():
    rules = {r.id: r for r in alerts.load()}
    assert {"container_down", "topic_stale", "estop_active", "joint_near_limit", "gpu_hot",
            "disk_low", "log_error_pattern", "ingest_lag"} <= set(rules)  # fmt: skip
    assert rules["container_down"].root == "component"
    assert rules["log_error_pattern"].root == "log"
    assert rules["topic_stale"].topic == "/joint_states"
    assert rules["disk_low"].root == ""


def test_facts_and_subjects():
    snap = {
        "mode": {"chip": "SIM · GAZEBO"},
        "components": [{"id": "ros", "status": "ready", "detail": ""},
                       {"id": "lichtblick_web", "status": "down", "detail": "exited (137)"}],
        "arm": None,
    }  # fmt: skip
    extra = {
        "topics": {"/joint_states": {"hz": 0.0, "age_s": 3.2}},
        "joints": {"name": ["joint_2"], "position": [1.53]},  # 87.7°, limit 90
        "limits_deg": {"joint_2": [-42.0, 90.0]},
        "metrics": {"host": {"disk_free_gb": 9.5}, "gpus": [{"index": 0, "temp_c": 91}]},
        "log_matches": [{"id": "ros_process_died", "severity": "error", "title": "died",
                         "source": "rosout"}],
    }  # fmt: skip
    fired = {(f.rule, f.subject): f for f in alerts.firing(alerts.facts(snap, extra))}
    assert set(fired) == {
        ("container_down", "lichtblick_web"),
        ("topic_stale", "/joint_states"),
        ("joint_near_limit", "joint_2"),
        ("gpu_hot", "0"),
        ("disk_low", ""),
        ("log_error_pattern", "ros_process_died"),
    }
    assert fired[("log_error_pattern", "ros_process_died")].severity == "error"  # from_match
    assert "3.2 s" in fired[("topic_stale", "/joint_states")].message


def test_no_agent_no_topic_alerts():
    snap = {"mode": {"chip": "SIM · MOCK"}, "components": [], "arm": None}
    assert not [f for f in alerts.firing(alerts.facts(snap, {"topics": None})) if f.rule ==
                "topic_stale"]  # fmt: skip


# --- lifecycle --------------------------------------------------------------------------------


def firing(rule="container_down", subject="ros", severity="error"):
    return alerts.Firing(rule, subject, severity, f"{rule} {subject}", {})


def test_alert_lifecycle_dedup_ack_resolve():
    new = alerts.apply([firing()])["new"]
    assert len(new) == 1 and new[0]["status"] == "active"
    assert alerts.apply([firing()])["new"] == []  # dedup by rule + subject
    a = Alert.objects.get()
    assert a.count == 2
    assert alerts.summary()["worst"] == "error"
    alerts.acknowledge(a.id, "alice", "looking at it")
    assert alerts.summary()["active"] == 0 and alerts.summary()["acknowledged"] == 1
    alerts.apply([firing()])  # still firing: stays acknowledged
    assert Alert.objects.get().status == "acknowledged"
    assert alerts.apply([])["resolved"] == 1
    a.refresh_from_db()
    assert a.status == "resolved" and a.resolved_at is not None
    assert len(alerts.apply([firing()])["new"]) == 1  # a new one after it resolved
    assert Alert.objects.count() == 2


def test_log_alerts_resolve_only_after_the_hold_time():
    alerts.apply([firing("log_error_pattern", "ros_process_died")])
    assert alerts.apply([])["resolved"] == 0
    later = datetime.now(UTC) + timedelta(seconds=alerts.LOG_HOLD_S + 1)
    assert alerts.apply([], now=later)["resolved"] == 1


def test_new_alerts_are_events():
    alerts.apply([firing()])
    with connection.cursor() as cur:
        cur.execute("SELECT kind, payload FROM events WHERE kind = 'alert'")
        _kind, payload = cur.fetchone()
    assert json.loads(payload)["rule"] == "container_down"  # Django's cursor: jsonb as text


def test_pill_takes_the_worst_of_health_and_alerts():
    snap = {"overall": "OK", "reasons": []}
    summary = {"worst": "warn", "recent": [{"message": "Disk space low"}]}
    status.with_alerts(snap, summary)
    assert snap["overall"] == "WARN" and snap["reasons"] == ["Disk space low"]
    status.with_alerts(snap, {"worst": None, "recent": []})  # acknowledged / resolved
    assert snap["overall"] == "OK" and snap["reasons"] == []
    snap = {"overall": "ERROR", "reasons": ["controllers: waiting"]}
    status.with_alerts(snap, summary)
    assert snap["overall"] == "ERROR"


def test_prune_keeps_90_days(settings):
    a = Alert.objects.create(rule="r", subject="", severity="warn", message="m",
                             last_seen=datetime.now(UTC), status="resolved",
                             resolved_at=datetime.now(UTC) - timedelta(days=91))  # fmt: skip
    Alert.objects.create(rule="r", subject="", severity="warn", message="m",
                         last_seen=datetime.now(UTC))  # fmt: skip
    assert alerts.prune() == 1
    assert not Alert.objects.filter(pk=a.pk).exists()


# --- dead containers: container_down within one poll -----------------------------------------


@pytest.mark.parametrize(
    ("state", "status_text", "dead"),
    [
        ("exited", "Exited (137) 3 seconds ago", True),  # docker kill
        ("exited", "Exited (139) 1 minute ago", True),  # crash
        ("exited", "Exited (0) 3 seconds ago", False),
        ("exited", "Exited (130) 3 seconds ago", False),  # Ctrl-C
        ("exited", "Exited (143) 3 seconds ago", False),  # docker stop
        ("restarting", "Restarting (1) 2 seconds ago", True),
        ("running", "Up 3 minutes", False),
    ],
)
def test_dead_containers(state, status_text, dead):
    assert stack_state.Container("i", "n", "mac", state, status_text).dead is dead


def test_killed_stack_raises_container_down_and_turns_the_header_red(fake_mode):
    fake.save_state(fake_mode, {"stack": {"service": "mac", "ar3_mode": "sim", "started": 1}})
    snap = async_to_sync(status.snapshot)()
    assert snap["overall"] == "OK"
    state = fake.load_state(fake_mode)
    state["stack"].update(state="exited", exit_code=137)  # `docker kill ar3-mac-1`
    fake.save_state(fake_mode, state)

    from core.runner import get_runner

    st = async_to_sync(stack_state.read)(get_runner())
    assert st.stacks == [] and st.dead_stacks[0][0].id == "sim_gazebo"
    snap = async_to_sync(status.snapshot)(st=st)
    rows = {r["id"]: r for r in snap["components"]}
    assert rows["ros"]["status"] == "down" and "Exited (137)" in rows["ros"]["detail"]
    assert snap["overall"] == "ERROR"
    assert snap["dead"][0]["chip"] == "SIM · GAZEBO"
    facts = alerts.facts(snap, {"dead_mode": st.dead_stacks[0][0].chip})
    fired = alerts.firing(facts)
    assert [(f.rule, f.subject) for f in fired] == [("container_down", "ros")]
    alerts.apply(fired)
    snap = async_to_sync(status.snapshot)(st=st)
    assert snap["alerts"]["worst"] == "error" and snap["overall"] == "ERROR"

    fake.save_state(fake_mode, {**fake.load_state(fake_mode), "stack": None})  # make down
    st = async_to_sync(stack_state.read)(get_runner())
    snap = async_to_sync(status.snapshot)(st=st)
    alerts.apply(alerts.firing(alerts.facts(snap, {})))
    assert Alert.objects.get().status == "resolved"
    assert async_to_sync(status.snapshot)(st=st)["overall"] == "OFFLINE"


def test_a_clean_stop_is_not_down(fake_mode):
    fake.save_state(fake_mode, {"stack": {"service": "mac", "ar3_mode": "sim", "started": 1,
                                          "state": "exited", "exit_code": 0}})  # fmt: skip
    snap = async_to_sync(status.snapshot)()
    assert {r["id"]: r for r in snap["components"]}["ros"]["status"] == "off"
    assert snap["overall"] == "OFFLINE"


# --- logs -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "level", "node"),
    [
        ("[move_group-4] [INFO] [1790000000.200] [move_group]: You can start planning now!",
         "INFO", "move_group"),
        ("[ERROR] [1790000000.1] [controller_manager]: Failed to load controller x", "ERROR",
         "controller_manager"),
        ("[WARN] [1790000000.1] [ar3.cockpit_agent]: slow", "WARN", "ar3.cockpit_agent"),
        ("[ERROR] [ros2_control_node-1]: process has died [pid 5, exit code -11, cmd '…']",
         "ERROR", "ros2_control_node"),
        ("[INFO] [launch]: All log files can be found below /home/ubuntu/.ros/log", "INFO",
         "launch"),
        ("[gzserver-2] Traceback (most recent call last):", "INFO", "gzserver"),
        ("Traceback (most recent call last):", "ERROR", ""),
        ("ValueError: bad", "ERROR", ""),
        ("plain text", "INFO", ""),
    ],
)  # fmt: skip
def test_parse_ros_lines(line, level, node):
    got_level, got_node, _ = logs.parse_ros(line)
    assert (got_level, got_node) == (level, node)


def test_parse_docker_timestamps():
    t, text = logs.parse_docker("2026-11-02T10:15:03.123456789Z hello world")
    assert t == datetime(2026, 11, 2, 10, 15, 3, 123456, tzinfo=UTC) and text == "hello world"
    _t, text = logs.parse_docker("no stamp")
    assert text == "no stamp"


@pytest.fixture
def stored_lines(db):
    now = datetime.now(UTC)
    insert_lines(
        [
            ("container:ar3-mac-1", "INFO", "You can start planning now!", "move_group",
             now - timedelta(seconds=30)),
            ("container:ar3-mac-1", "ERROR", "process has died [pid 7, exit code -11]", "launch",
             now - timedelta(seconds=20)),
            ("rosout", "ERROR", "Unable to solve the planning problem", "move_group",
             now - timedelta(seconds=10)),
            ("rosout", "DEBUG", "tick", "controller_manager", now - timedelta(seconds=5)),
            ("job:1234", "INFO", "Summary: 8 packages finished", "ws.build", now),
        ]
    )  # fmt: skip
    return now


def test_log_query_filters(stored_lines):
    assert len(logs.query()) == 5
    assert [x["source"] for x in logs.query(sources=["container:"])] == ["container:ar3-mac-1"] * 2
    errors = logs.query(level="ERROR")
    assert {x["text"] for x in errors} == {
        "process has died [pid 7, exit code -11]",
        "Unable to solve the planning problem",
    }
    assert [x["level"] for x in logs.query(sources=["rosout"], level="WARN")] == ["ERROR"]
    assert len(logs.query(sources=["rosout"])) == 2
    assert logs.query(q="PLANNING problem")[0]["source"] == "rosout"  # case-insensitive
    assert logs.query(q="100%") == []  # % is literal, not a wildcard
    assert len(logs.query(q=r"exit code -\d+", regex=True)) == 1
    assert len(logs.query(node="move")) == 2
    newest_first = logs.query()
    assert newest_first[0]["source"] == "job:1234"
    assert logs.query(since=stored_lines - timedelta(seconds=15), until=stored_lines -
                      timedelta(seconds=7))[0]["text"].startswith("Unable")  # fmt: skip
    text = logs.as_text(logs.query(sources=["rosout"]))
    assert text.index("Unable") < text.index("tick")  # the download is oldest first


def test_rosout_errors_are_searchable_over_the_api(client_in, stored_lines):
    """§18 C4: /rosout errors searchable."""
    r = client_in.get("/api/logs", {"source": "rosout", "level": "ERROR", "q": "planning"})
    assert r.status_code == 200
    assert [x["text"] for x in r.json()["lines"]] == ["Unable to solve the planning problem"]
    r = client_in.get("/api/logs", {"q": "([", "regex": "1"})
    assert r.status_code == 400 and "regular expression" in r.json()["title"]
    r = client_in.get("/api/logs", {"source": "rosout", "download": "1"})
    assert r["Content-Type"].startswith("text/plain") and b"planning problem" in r.content
    sources = {s["source"]: s["lines"] for s in client_in.get("/api/logs/sources").json()}
    assert sources == {"container:ar3-mac-1": 2, "job:1234": 1, "rosout": 2}


def test_prune_keeps_the_newest_per_source(stored_lines):
    assert logs.prune(keep=1) == 2  # one container line, one rosout line
    assert {(x["source"], x["text"]) for x in logs.query()} == {
        ("container:ar3-mac-1", "process has died [pid 7, exit code -11]"),
        ("rosout", "tick"),
        ("job:1234", "Summary: 8 packages finished"),
    }


def test_the_sink_keeps_lines_when_the_database_is_away(monkeypatch):
    sink = logs.LogSink(thread=False)
    sink.push("rosout", "INFO", "a")
    sink.push("rosout", "WARNING", "b")

    def boom(batch):
        raise OSError("db away")

    monkeypatch.setattr(sink, "_write", boom)
    with pytest.raises(OSError):
        sink.flush()
    assert [r[2] for r in sink._take()] == ["INFO", "WARN"]  # still queued, level normalised


def test_live_tail_filter():
    ok_ = line_filter({"source": ["rosout,container:"], "level": ["warn"], "q": ["plan"]})
    line = {"source": "rosout", "level": "ERROR", "node": "move_group", "text": "planning failed"}
    assert ok_(line)
    assert not ok_({**line, "level": "INFO"})
    assert not ok_({**line, "source": "job:1"})
    assert ok_({**line, "source": "container:ar3-mac-1"})
    assert not ok_({**line, "text": "other"})
    regex = line_filter({"q": [r"exit code -\d+"], "regex": ["1"]})
    assert regex({**line, "text": "exit code -11"}) and not regex(line)


# --- the collector -----------------------------------------------------------------------------


class ListSink:
    def __init__(self):
        self.lines = []

    def push(self, source, level, text, node="", at=None, session=None):
        self.lines.append((source, level, node, text))


def test_collector_follows_containers_and_rosout(fake_mode):
    async def go():
        state = {
            "stack": {"service": "mac", "ar3_mode": "sim", "started": 1_790_000_000},
            "logs": {
                "ar3-mac-1": [
                    "[move_group-4] [ERROR] [launch]: process has died [pid 9, exit code -6]"
                ]
            },
            "rosout": [["ERROR", "move_group", "Unable to solve the planning problem"]],
        }
        fake.save_state(fake_mode, state)  # fmt: skip
        from core.runner import get_runner

        runner = get_runner()
        sink = ListSink()
        col = collector.Collector(runner, sink)
        st = await stack_state.read(runner)
        await col.step(st)
        assert set(col.streams) == {"ar3-mac-1", "ar3-cockpit-db-1", "ar3-cockpit-redis-1"}
        for _ in range(30):
            await asyncio.sleep(0.1)
            if any(s == "rosout" and "planning" in t for s, _l, _n, t in sink.lines):
                break
        await col.stop()
        return sink.lines, col.take_matches()

    lines, matches = async_to_sync(go)()
    container = [x for x in lines if x[0] == "container:ar3-mac-1"]
    assert ("container:ar3-mac-1", "INFO", "move_group",
            "[move_group-4] [INFO] [1790000000.200] [move_group]: You can start planning now!"
            ) in container  # fmt: skip
    assert any(x[1] == "ERROR" and "process has died" in x[3] for x in container)
    assert ("rosout", "ERROR", "move_group", "Unable to solve the planning problem") in lines
    assert ("rosout", "INFO", "move_group", "You can start planning now!") in lines
    # only error / critical patterns go to the alerts (planning_failed is a warning)
    assert [m["id"] for m in matches] == ["ros_process_died"]


def test_collector_starts_after_the_stored_lines():
    t = datetime(2026, 11, 2, 10, 0, 0, 500000, tzinfo=UTC)
    assert collector._since_arg(t) == "2026-11-02T10:00:00.500001000Z"


# --- metrics ---------------------------------------------------------------------------------


def test_docker_stats_and_nvidia_smi_parsing():
    out = json.dumps({"Name": "ar3-mac-1", "CPUPerc": "143.20%", "MemUsage": "1.5GiB / 7.6GiB",
                      "MemPerc": "19.7%", "PIDs": "212"}) + "\n"  # fmt: skip
    out += json.dumps({"Name": "other", "CPUPerc": "1%", "MemUsage": "1MiB / 1GiB"}) + "\n"
    rows = metrics.parse_stats(out, {"ar3-mac-1"})
    assert rows == [{"name": "ar3-mac-1", "cpu_percent": 143.2, "mem_used_mb": 1610.6,
                     "mem_limit_mb": 8160.4, "mem_percent": 19.7, "pids": 212}]  # fmt: skip
    gpus = metrics.parse_gpu("NVIDIA GeForce RTX 4060, 23, 1024, 8188, 52\nbad line\n")
    assert gpus == [{"index": 0, "name": "NVIDIA GeForce RTX 4060", "util_percent": 23.0,
                     "mem_used_mb": 1024.0, "mem_total_mb": 8188.0, "temp_c": 52.0}]  # fmt: skip
    assert metrics.parse_gpu("RTX, [N/A], 1, 2, 40")[0]["util_percent"] is None


def test_host_metrics_and_rows():
    h = metrics.host()
    assert 0 <= h["cpu_percent"] <= 100 * h["cpu_count"] and h["disk_free_gb"] > 0
    rows = metrics.rows(
        {
            "host": h,
            "containers": [{"name": "c", "cpu_percent": 1.0, "mem_used_mb": 2.0}],
            "gpus": [],
        },
        None,
    )
    names = {r[2] for r in rows}  # fmt: skip
    assert {"host_cpu_percent", "host_disk_free_gb", "container_cpu_percent"} <= names


def test_fake_metrics_on_linux(fake_mode, settings):
    settings.FAKE_PLATFORM = "ubuntu"
    fake.save_state(fake_mode, {"gpu_temp": 91})
    from core.runner import get_runner

    runner = get_runner()
    gpus = async_to_sync(metrics.gpus)(runner, "linux")
    assert gpus[0]["temp_c"] == 91.0
    stats = async_to_sync(metrics.containers)(runner, ["ar3-mac-1"])
    assert stats[0]["name"] == "ar3-mac-1" and stats[0]["cpu_percent"] > 50


# --- the monitoring API ---------------------------------------------------------------------


def test_graph_params_rate_and_cameras_from_the_agent(client_in, fake_mode):
    assert client_in.get("/api/ros/graph").status_code == 503  # no stack: no agent
    fake.save_state(fake_mode, {"stack": {"service": "mac", "ar3_mode": "sim", "started": 1}})
    graph = client_in.get("/api/ros/graph").json()
    js = next(t for t in graph["topics"] if t["name"] == "/joint_states")
    assert js["publishers"] == ["/joint_state_broadcaster"] and js["rate"]["hz"] == 100.0
    params = client_in.get("/api/ros/node/move_group/params").json()
    assert params["node"] == "/move_group" and params["params"][0]["name"] == "use_sim_time"
    assert client_in.get("/api/ros/node/nope/params").status_code == 404
    assert client_in.get("/api/ros/topic/rate", {"name": "/clock"}).json()["hz"] == 1000.0
    assert client_in.get("/api/ros/topic/rate", {"name": "clock"}).status_code == 400
    assert client_in.get("/api/cameras").json()["cameras"] == ["cam_overhead", "cam_wrist"]
    thumb = client_in.get("/api/cameras/cam_wrist/thumb")
    assert thumb["Content-Type"] == "image/jpeg" and thumb.content[:2] == b"\xff\xd8"


def test_alerts_api(client_in):
    alerts.apply([firing(), firing("disk_low", "", "warn")])
    body = client_in.get("/api/alerts").json()
    assert body["summary"]["active"] == 2 and body["summary"]["worst"] == "error"
    a = next(x for x in body["alerts"] if x["rule"] == "container_down")
    r = client_in.post(f"/api/alerts/{a['id']}/ack", {"comment": "known"},
                       content_type="application/json")  # fmt: skip
    assert r.json()["status"] == "acknowledged" and r.json()["acknowledged_by"] == "alice"
    assert client_in.post("/api/alerts/99999/ack").status_code == 404
    rest = [x["id"] for x in client_in.get("/api/alerts", {"status": "active"}).json()["alerts"]]
    r = client_in.post("/api/alerts", {"ids": rest}, content_type="application/json")
    assert len(r.json()["acknowledged"]) == 1
    assert client_in.get("/api/alerts", {"status": "active"}).json()["alerts"] == []
    rules = client_in.get("/api/alerts/rules").json()
    assert {"id": "container_down", "subjects": "component"}.items() <= rules[0].items()
    from accounts.models import AuditEntry

    assert AuditEntry.objects.filter(action="alert.ack").count() == 2


def test_metrics_api_without_the_monitor(client_in):
    h = client_in.get("/api/metrics/host").json()
    assert h["mem_total_gb"] > 0
    assert client_in.get("/api/metrics/containers").json()["containers"] == []


def test_snapshot_reports_rec_and_alerts(fake_mode):
    snap = async_to_sync(status.snapshot)()
    assert snap["rec"]["available"] is False and snap["alerts"]["active"] == 0


def test_monitor_extra_facts(fake_mode):
    from monitoring.management.commands.run_monitor import Monitor

    fake.save_state(fake_mode, {"stack": {"service": "mac", "ar3_mode": "sim", "started": 1},
                                "stale_topics": ["/joint_states"]})  # fmt: skip
    mon = Monitor()

    async def go():
        from core.runner import get_runner

        st = await stack_state.read(get_runner())
        snap = await status.snapshot(get_runner(), st=st)
        return await mon.extra(snap, st)

    extra = async_to_sync(go)()
    assert extra["topics"]["/joint_states"]["age_s"] == 5.0
    assert extra["joints"]["name"][0] == "joint_1"
    assert extra["limits_deg"]["joint_1"] == [-170.0, 170.0]


def test_ws_logs_and_telemetry_need_a_login():
    from channels.testing import WebsocketCommunicator

    from config.asgi import application

    async def go(path):
        comm = WebsocketCommunicator(application, path, headers=[(b"origin", b"http://127.0.0.1")])
        connected, _ = await comm.connect()
        closed = await comm.receive_output() if connected else None
        await comm.disconnect()
        return connected, closed

    for path in ("/ws/logs", "/ws/telemetry"):
        connected, closed = async_to_sync(go)(path)
        assert connected and closed == {"type": "websocket.close", "code": 4401}


@pytest.mark.django_db(transaction=True)
def test_job_lines_go_to_the_log_buffer(settings, fake_mode, monkeypatch, clean_raw_tables):
    """run_jobs pushes every job line (source job:<id>), with the pattern's level."""
    sink = logs.LogSink(thread=False)
    monkeypatch.setattr(logs, "sink", lambda: sink)
    from setup import executor, service

    fake.save_state(fake_mode, {"fail": {"make ws-build": "Failed   <<< ar3_bringup [1.1s]"}})
    job = service.create_job("ws.build", None, "alice", "macos")
    async_to_sync(executor.execute)(job)
    sink.flush()
    lines = logs.query(sources=[f"job:{job.id}"])
    assert any(x["level"] == "ERROR" and "Failed   <<< ar3_bringup" in x["text"] for x in lines)
    assert all(x["node"] == "ws.build" for x in lines)
