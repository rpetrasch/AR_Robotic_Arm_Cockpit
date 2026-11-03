"""Phase C3 (docs/specs/spec_cockpit.md §9, §18): start / stop from the cockpit, single-stack rule,
component catalogue, the agent client, the software stop and the REAL pre-flight wizard."""

import json
import os
import re
import stat
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from asgiref.sync import async_to_sync, sync_to_async

from accounts.models import AuditEntry
from config.envfiles import REPO
from core import agent, fake
from environment import arm, components, modes, status
from setup import executor, service, steps
from setup.models import Job

pytestmark = pytest.mark.django_db


@pytest.fixture
def fake_mode(settings, monkeypatch):
    settings.FAKE = True
    monkeypatch.setattr(agent, "FAKE_READY_AFTER_S", 0.0)
    return settings.DATA_DIR


@pytest.fixture
def real_host(settings, fake_mode):
    settings.FAKE_PLATFORM = "ubuntu-real"
    fake.save_state(fake_mode, {"stack": {"service": "dev", "ar3_mode": "real", "started": 1}})
    return fake_mode


def post(c, path, data=None):
    return c.post(path, data or {}, content_type="application/json")


async def run_job(job_id) -> Job:
    return await executor.execute(await sync_to_async(Job.objects.get)(pk=job_id))


# --- modes: the command the cockpit runs ------------------------------------------------------


@pytest.mark.parametrize(
    ("mode_id", "lichtblick", "env", "argv"),
    [
        ("sim_gazebo", False, {}, ("make", "sim-mac", "DETACH=1")),
        ("sim_gazebo_rviz", True, {"AR3_MODE": "sim"}, ("make", "vnc", "DETACH=1", "LICHTBLICK=1")),
        ("linux_gazebo_headless_gpu", False, {},
         ("make", "sim-linux", "HEADLESS=1", "GPU=1", "DETACH=1")),
        ("remote", False, {}, ("make", "remote", "DETACH=1")),
    ],
)  # fmt: skip
def test_start_command_is_the_cli_command_detached(mode_id, lichtblick, env, argv):
    assert modes.BY_ID[mode_id].start(lichtblick) == (env, argv)


def test_real_and_shells_are_not_startable():
    with pytest.raises(modes.ModeError, match="Phase 1"):
        modes.startable("real")
    with pytest.raises(modes.ModeError, match="terminal"):
        modes.startable("dev_shell")
    with pytest.raises(modes.ModeError, match="not available on this host"):
        modes.startable("linux_mock", "macos")
    assert modes.startable("remote", "macos").service == "remote"


@pytest.mark.parametrize("mode", [m for m in modes.ALL_MODES if m.startable], ids=lambda m: m.id)
def test_make_really_detaches_and_starts_lichtblick(mode):
    """`make -n` of the exact start command: detached, with the Lichtblick container."""
    env, argv = mode.start(lichtblick=True)
    out = subprocess.run(
        ["make", "-n", *argv[1:]],
        cwd=REPO,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    up = [ln for ln in out.splitlines() if " up " in ln and "--force-recreate" in ln]
    assert up and "--detach" in up[-1], out
    assert up[-1].split()[-1] == mode.service
    assert "--profile viewers up --detach lichtblick" in out
    assert "A ROS stack is already running" in out  # the single-stack guard runs first


def test_down_stops_every_stack_service():
    out = subprocess.run(["make", "-n", "down"], cwd=REPO, capture_output=True, text=True).stdout
    for svc in ("mac", "vnc", "linux", "linux-gpu", "remote", "lichtblick"):
        assert re.search(rf"\s{svc}(\s|$)", out), svc


def test_start_step_plans_stop_and_switch():
    step = steps.get("env.start")
    params = step.validate({"mode": "sim_gazebo_rviz", "switch": "1"})
    assert params == {"mode": "sim_gazebo_rviz", "lichtblick": "0", "switch": "1"}
    assert step.command(params) == "make down && AR3_MODE=sim make vnc DETACH=1"
    with pytest.raises(steps.StepError, match="unknown mode"):
        step.command(step.validate({"mode": "nope"}))
    with pytest.raises(steps.StepError):
        step.validate({"mode": "sim_mock; rm -rf /"})


# --- start / stop through the API, in the fake world -----------------------------------------


@pytest.mark.django_db(transaction=True)
def test_start_shows_and_runs_the_same_command(client_in, fake_mode):
    modes_api = {m["id"]: m for m in client_in.get("/api/env/modes").json()}
    shown = modes_api["sim_gazebo_rviz"]["start_command"]
    assert shown == "AR3_MODE=sim make vnc DETACH=1"
    res = post(client_in, "/api/env/start", {"mode": "sim_gazebo_rviz"})
    assert res.status_code == 201, res.json()
    job = res.json()
    assert job["command"] == shown
    done = async_to_sync(run_job)(job["id"])
    assert done.status == "succeeded", service.read_log(done)
    log = [ln["text"] for ln in service.read_log(done)]
    assert log[0] == f"$ {shown}"
    assert any(t.startswith("ready.") for t in log)
    assert fake.load_state(fake_mode)["stack"]["ar3_mode"] == "sim"
    assert AuditEntry.objects.filter(action="env.start", obj="mode:sim_gazebo_rviz").exists()
    snap = client_in.get("/api/env/state").json()
    assert snap["mode"]["id"] == "sim_gazebo_rviz" and snap["ready"]
    assert snap["stop"]["available"] is True


@pytest.mark.django_db(transaction=True)
def test_second_stack_is_refused_then_stop_and_switch(client_in, fake_mode):
    fake.save_state(fake_mode, {"stack": {"service": "mac", "ar3_mode": "sim", "started": 1}})
    res = post(client_in, "/api/env/start", {"mode": "sim_mock"})
    assert res.status_code == 409
    body = res.json()
    assert body["title"] == "A ROS stack is running"
    assert body["running"]["id"] == "sim_gazebo"
    res = post(client_in, "/api/env/start", {"mode": "sim_mock", "switch": True})
    assert res.json()["command"] == "make down && make up-mac DETACH=1"
    done = async_to_sync(run_job)(res.json()["id"])
    assert done.status == "succeeded"
    assert fake.load_state(fake_mode)["stack"]["ar3_mode"] == "mock"


@pytest.mark.django_db(transaction=True)
def test_make_guard_message_gets_the_stack_running_hint(fake_mode):
    """A stack started from the CLI between the check and the job: make's own guard."""
    job = service.create_job("env.start", {"mode": "sim_mock"}, "alice", "macos")
    fake.save_state(fake_mode, {"stack": {"service": "vnc", "ar3_mode": "mock", "started": 1}})
    done = async_to_sync(run_job)(job.id)
    assert done.status == "failed"
    assert done.matches[0]["id"] == "stack_running"
    assert done.matches[0]["fix_step"] == "env.stop"


@pytest.mark.django_db(transaction=True)
def test_stop_runs_make_down(client_in, fake_mode):
    assert post(client_in, "/api/env/stop").status_code == 409  # nothing runs
    fake.save_state(fake_mode, {"stack": {"service": "vnc", "ar3_mode": "mock", "started": 1}})
    job = post(client_in, "/api/env/stop").json()
    assert job["command"] == "make down"
    done = async_to_sync(run_job)(job["id"])
    assert done.status == "succeeded"
    assert service.read_log(done)[-1]["text"] == "stopped."
    assert fake.load_state(fake_mode)["stack"] is None


def test_real_stop_needs_confirmation(client_in, real_host):
    res = post(client_in, "/api/env/stop")
    assert res.status_code == 400 and "confirm" in res.json()["detail"]
    assert post(client_in, "/api/env/stop", {"confirm": True}).status_code == 201


def test_linux_mode_refused_on_the_mac_and_real_everywhere(client_in, fake_mode):
    res = post(client_in, "/api/env/start", {"mode": "linux_mock"})
    assert res.status_code == 400 and "not available on this host" in res.json()["detail"]
    res = post(client_in, "/api/env/start", {"mode": "real"})
    assert res.status_code == 400 and "Phase 1" in res.json()["detail"]


@pytest.mark.django_db(transaction=True)  # the job is created on another thread's connection
async def test_start_job_shows_starting_in_the_header(fake_mode):
    await sync_to_async(service.create_job)("env.start", {"mode": "sim_mock"}, "alice", "macos")
    snap = await status.snapshot()
    assert snap["transition"] == "STARTING…"
    assert snap["jobs"]["step"] == "env.start"


async def test_wait_ready_times_out_without_an_agent(fake_mode, monkeypatch):
    from environment import health

    monkeypatch.setattr(health, "READY_TIMEOUT_S", 0.3)
    monkeypatch.setattr(health, "POLL_S", 0.05)
    lines = []

    async def log(stream, text):
        lines.append(text)

    from core.runner import TIMED_OUT

    assert await health.wait_ready("sim_mock", log) == TIMED_OUT
    assert "waiting for the agent" in lines[1] and "not ready after" in lines[-1]


def test_restart_only_for_containers(client_in, fake_mode):
    assert post(client_in, "/api/env/components/move_group/restart").status_code == 400
    job = post(client_in, "/api/env/components/ros/restart").json()
    assert job["command"] == "make restart"


# --- health in the fake world ----------------------------------------------------------------


async def test_fake_stack_health_and_viewers(fake_mode):
    fake.save_state(
        fake_mode,
        {"stack": {"service": "vnc", "ar3_mode": "sim", "started": 1}, "lichtblick": True},
    )
    snap = await status.snapshot()
    rows = {c["id"]: c["status"] for c in snap["components"]}
    assert rows["gazebo"] == rows["novnc"] == rows["lichtblick_web"] == "ready"
    assert rows["safety"] == "off"
    viewers = {v["id"]: v for v in snap["viewers"]}
    assert viewers["lichtblick_web"]["available"] is True
    assert viewers["lichtblick_web"]["path"] == "/?ds=foxglove-websocket&ds.url=ws://{host}:8765"
    assert snap["overall"] == "OK"


async def test_stack_with_a_dead_agent(fake_mode):
    fake.save_state(
        fake_mode, {"stack": {"service": "mac", "ar3_mode": "mock", "started": 1, "agent": False}}
    )
    snap = await status.snapshot()
    assert snap["overall"] == "WARN"
    assert snap["stop"]["available"] is False


# --- component catalogue ---------------------------------------------------------------------


def test_catalogue_is_valid_and_covers_the_spec_list():
    items = components.catalogue()
    assert components.validate(items) == []
    ids = {c["id"] for c in items}
    assert ids >= {
        "ros", "gazebo", "move_group", "ros2_control", "ar3_safety", "hardware",
        "foxglove_bridge", "lichtblick", "novnc", "zenohd", "agent", "cockpit_db",
        "cockpit_redis", "cockpit", "ollama",
    }  # fmt: skip
    health_ids = {"ros", "agent", "gazebo", "controllers", "move_group", "cameras", "safety",
                  "foxglove_bridge", "lichtblick_web", "novnc", "zenohd", "cockpit_db",
                  "cockpit_redis", "ollama"}  # fmt: skip
    assert {c["health"] for c in items if c["health"]} <= health_ids


def test_generated_component_docs_are_current():
    doc = (REPO / "docs/manual/cockpit-components.md").read_text()
    assert doc == components.markdown(components.catalogue()), "run `make cockpit-docs`"


def test_which_components_run_in_a_mode():
    by_id = {c["id"]: c for c in components.catalogue()}

    def running(mode_id):
        mode = modes.BY_ID[mode_id] if mode_id else None
        return {cid for cid, c in by_id.items() if components.applies(c, mode)}

    assert "gazebo" not in running("sim_mock") and "gazebo" in running("sim_gazebo")
    assert "novnc" in running("sim_mock_rviz") and "novnc" not in running("sim_mock")
    assert running("remote") >= {"agent", "zenohd", "ros"} and "move_group" not in running("remote")
    assert "ar3_safety" in running("real") and "ar3_safety" not in running("sim_gazebo")
    assert "ros" not in running(None) and "cockpit" in running(None)


def test_components_api(client_in, fake_mode):
    fake.save_state(fake_mode, {"stack": {"service": "mac", "ar3_mode": "sim", "started": 1}})
    body = client_in.get("/api/components").json()
    assert body["mode"] == "sim_gazebo"
    by_id = {c["id"]: c for c in body["components"]}
    assert by_id["gazebo"]["in_mode"] and by_id["gazebo"]["live"]["status"] == "ready"
    assert by_id["gazebo"]["cli"]["start"] == "make sim-mac"
    preview = client_in.get("/api/components?mode=sim_mock_rviz").json()
    assert {c["id"]: c["in_mode"] for c in preview["components"]}["novnc"] is True


# --- agent client and token ------------------------------------------------------------------


def test_token_is_private_and_stable(settings):
    first = agent.token(settings.DATA_DIR)
    assert len(first) >= 40 and agent.token(settings.DATA_DIR) == first
    mode = (settings.DATA_DIR / "agent.token").stat().st_mode
    assert stat.S_IMODE(mode) == 0o600
    assert executor.job_env()["COCKPIT_AGENT_TOKEN"] == first


class _Agent(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.headers.get("Authorization") != "Bearer good":
            body, code = {"title": "Wrong agent token"}, 401
        else:
            body, code = {"ok": True, "ready": True}, 200
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def http_agent():
    server = HTTPServer(("127.0.0.1", 0), _Agent)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


async def test_agent_client_errors(http_agent):
    assert (await agent.AgentClient(http_agent, "good").health())["ready"] is True
    with pytest.raises(agent.AgentError) as err:
        await agent.AgentClient(http_agent, "bad").health()
    assert (err.value.status, err.value.title) == (401, "Wrong agent token")
    with pytest.raises(agent.AgentError) as err:
        await agent.AgentClient("http://127.0.0.1:9", "good").health()
    assert err.value.status == 0


# --- software stop ---------------------------------------------------------------------------


def test_software_stop_is_audited(client_in, fake_mode):
    assert post(client_in, "/api/arm/stop").status_code == 502  # no stack
    fake.save_state(fake_mode, {"stack": {"service": "mac", "ar3_mode": "sim", "started": 1}})
    res = post(client_in, "/api/arm/stop")
    assert res.status_code == 200 and res.json()["held"] == []
    assert fake.load_state(fake_mode)["stops"] == 1
    assert AuditEntry.objects.filter(action="arm.stop").count() == 2


# --- REAL pre-flight wizard (fake ubuntu-real) -----------------------------------------------


def _step(c, step, action="run"):
    return post(c, f"/api/arm/preflight/{step}", {"action": action})


def _estop(state_dir, pressed: bool):
    state = fake.load_state(state_dir)
    state["estop"] = pressed
    fake.save_state(state_dir, state)


@pytest.mark.django_db(transaction=True)
def test_wizard_arms_after_every_step(client_in, real_host):
    state = client_in.get("/api/arm/state").json()
    assert state["real"] and state["arm"] == "NOT_HOMED" and state["next"] == "devices"
    assert _step(client_in, "workspace", "confirm").status_code == 409  # in order
    assert _step(client_in, "devices").json()["steps"][0]["status"] == "pass"

    body = _step(client_in, "estop", "start").json()
    assert body["estop_phase"] == "press"
    _estop(real_host, True)  # the operator presses the mushroom
    body = client_in.get("/api/arm/state").json()
    assert body["estop_phase"] == "reset" and body["arm"] == "E_STOP"
    # Released: ar3_safety latches the e-stop until Reset, which calls the agent.
    body = _step(client_in, "estop", "reset").json()
    assert {s["id"]: s["status"] for s in body["steps"]}["estop"] == "pass"

    _step(client_in, "workspace", "confirm")
    assert _step(client_in, "homing").json()["arm"] == "HOMED"
    _step(client_in, "pose", "confirm")
    body = _step(client_in, "limits", "confirm").json()
    assert body["limits"]["velocity_scaling"] == 0.2 and body["can_arm"]
    assert _step(client_in, "arm").json()["arm"] == "ARMED"
    assert client_in.get("/api/env/state").json()["arm"] == "ARMED"
    audited = AuditEntry.objects.filter(action="arm.preflight")
    assert {e.obj for e in audited} >= {f"step:{s}" for s in arm.IDS} | {"step:arm"}
    assert all(e.actor == "alice" for e in audited)

    # An e-stop while armed: E-STOP, and the wizard goes back to the e-stop test.
    _estop(real_host, True)
    body = client_in.get("/api/arm/state").json()
    assert body["arm"] == "E_STOP" and body["next"] == "estop"
    assert {s["id"]: s["status"] for s in body["steps"]}["homing"] == "pending"
    assert _step(client_in, "arm").status_code == 409


@pytest.mark.django_db(transaction=True)
def test_estop_test_times_out(client_in, real_host, monkeypatch):
    monkeypatch.setattr(arm, "ESTOP_TIMEOUT_S", 0)
    _step(client_in, "devices")
    _step(client_in, "estop", "start")
    body = client_in.get("/api/arm/state").json()
    estop = {s["id"]: s for s in body["steps"]}["estop"]
    assert estop["status"] == "fail" and "no e-stop press seen" in estop["detail"]


@pytest.mark.django_db(transaction=True)
def test_without_an_estop_signal_the_override_is_the_only_way(client_in, real_host, monkeypatch):
    async def no_signal(self):
        raise agent.AgentError(501, "Arm state not available", "Needs ar3_safety (Phase 1)")

    monkeypatch.setattr(agent.FakeAgent, "arm_state", no_signal)
    _step(client_in, "devices")
    body = _step(client_in, "estop", "start").json()
    estop = {s["id"]: s for s in body["steps"]}["estop"]
    assert estop["status"] == "fail" and "docs/hardware.md" in estop["detail"]
    assert _step(client_in, "estop", "override").status_code == 403
    monkeypatch.setenv("COCKPIT_ALLOW_ARM_WITHOUT_ESTOP", "1")
    body = _step(client_in, "estop", "override").json()
    assert {s["id"]: s["status"] for s in body["steps"]}["estop"] == "overridden"
    assert "COCKPIT_ALLOW_ARM_WITHOUT_ESTOP=1" in client_in.get("/api/env/state").json()["banner"]


def test_no_wizard_without_a_real_stack(client_in, fake_mode):
    assert client_in.get("/api/arm/state").json()["real"] is False
    assert _step(client_in, "devices").status_code == 409


def test_lichtblick_image_is_pinned_once():
    from setup.checks.stack import LICHTBLICK_IMAGE

    assert f"image: {LICHTBLICK_IMAGE}" in (REPO / "docker/compose.yaml").read_text()


@pytest.mark.django_db(transaction=True)
def test_pulling_the_viewer_image_turns_its_check_green(client_in, fake_mode):
    checks = {c["id"]: c for c in client_in.get("/api/checks").json()["checks"]}
    assert checks["viewers.lichtblick"]["status"] == "warn"
    assert checks["viewers.lichtblick"]["fix_step"] == "images.pull_viewers"
    job = post(client_in, "/api/jobs", {"step_id": "images.pull_viewers"}).json()
    assert async_to_sync(run_job)(job["id"]).status == "succeeded"
    checks = {c["id"]: c for c in client_in.get("/api/checks").json()["checks"]}
    assert checks["viewers.lichtblick"]["status"] == "pass"
