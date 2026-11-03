import asyncio
import json
from datetime import timedelta

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.utils import timezone

from accounts.models import AuditEntry
from core import fake
from core.runner import CANCELLED, TIMED_OUT, CommandRunner
from setup import executor, patterns, service, steps
from setup.consumers import JobConsumer
from setup.models import Job

pytestmark = pytest.mark.django_db


@pytest.fixture
def fake_mode(settings):
    settings.FAKE = True
    return settings.DATA_DIR


def post(c, path, data=None):
    return c.post(path, data or {}, content_type="application/json")


# --- the step registry ----------------------------------------------------------------------


def test_every_check_fix_step_exists():
    from setup.checks import ALL_CHECKS  # noqa: F401  the fix steps come from probe results

    for step in steps.STEPS:
        assert step.id.count(".") == 1
    for check_id in ("images.dev", "ws.built", "tests.last", "sim.smoke", "ai.models"):
        assert any(check_id in s.post_checks for s in steps.STEPS), check_id


@pytest.mark.parametrize("bad", ["", "llama3; rm -rf /", "$(id)", "a b", "x" * 200, "../etc"])
def test_params_are_validated(bad):
    with pytest.raises(steps.StepError):
        steps.get("ollama.pull").validate({"model": bad})


def test_param_rules_and_cli():
    step = steps.get("ollama.pull")
    assert step.validate({"model": "llama3.1:8b"}) == {"model": "llama3.1:8b"}
    assert step.command({"model": "gemma3:4b"}) == "ollama pull gemma3:4b"
    with pytest.raises(steps.StepError, match="unknown parameter"):
        step.validate({"model": "x", "extra": "y"})
    assert steps.get("env.create").command() == "cp docker/.env.example docker/.env"
    assert steps.get("ws.build").command() == "make ws-build"


def test_every_make_target_of_a_step_exists():
    import re

    from config.envfiles import REPO

    targets = set(re.findall(r"^([a-z-]+):", (REPO / "Makefile").read_text(), re.M))
    for step in steps.STEPS:
        if step.argv[:1] == ("make",):
            assert step.argv[1] in targets, step.id


def test_steps_api_shows_instructions_for_this_host(client_in, settings):
    settings.FAKE, settings.FAKE_PLATFORM = True, "ubuntu"
    by_id = {s["id"]: s for s in client_in.get("/api/steps").json()}
    assert by_id["groups.add"]["kind"] == "manual"
    assert by_id["groups.add"]["instructions"] == ["sudo usermod -aG docker,dialout,video $USER"]
    assert by_id["ws.build"]["command"] == "make ws-build"
    # The official guide is a link for the UI, not a "# comment" among the copyable commands.
    toolkit = by_id["nvidia.toolkit"]
    assert (
        toolkit["doc_url"].startswith("https://docs.nvidia.com/")
        and "install-guide" in toolkit["doc_url"]
    )
    assert not any(
        c.startswith("#") for c in toolkit["instructions"] + by_id["docker.install"]["instructions"]
    )
    assert by_id["docker.install"]["doc_url"] == "https://docs.docker.com/engine/install/ubuntu/"
    settings.FAKE_PLATFORM = "macos"
    by_id = {s["id"]: s for s in client_in.get("/api/steps").json()}
    assert by_id["groups.add"]["available"] is False


# --- creating jobs --------------------------------------------------------------------------


def test_create_records_command_and_audit(client_in, fake_mode):
    res = post(client_in, "/api/jobs", {"step_id": "ollama.pull", "params": {"model": "bge-m3"}})
    assert res.status_code == 201
    job = res.json()
    assert (job["status"], job["command"]) == ("queued", "ollama pull bge-m3")
    entry = AuditEntry.objects.get(action="job.start")
    assert entry.payload == {"step": "ollama.pull", "model": "bge-m3"}


def test_refusals(client_in, fake_mode, settings):
    assert post(client_in, "/api/jobs", {"step_id": "rm.rf"}).status_code == 400
    res = post(client_in, "/api/jobs", {"step_id": "docker.install"})
    assert res.status_code == 400 and "needs root" in res.json()["detail"]
    settings.FAKE_PLATFORM = "macos"
    assert post(client_in, "/api/jobs", {"step_id": "groups.add"}).status_code == 400
    bad = {"step_id": "ollama.pull", "params": {"model": "x; reboot"}}
    assert post(client_in, "/api/jobs", bad).status_code == 400


def test_one_changing_job_at_a_time(client_in, fake_mode):
    first = post(client_in, "/api/jobs", {"step_id": "images.build"}).json()
    res = post(client_in, "/api/jobs", {"step_id": "ws.build"})
    assert res.status_code == 409
    assert res.json()["job_id"] == first["id"]
    Job.objects.filter(pk=first["id"]).update(status=Job.SUCCEEDED)
    assert post(client_in, "/api/jobs", {"step_id": "ws.build"}).status_code == 201


def test_cancel_a_queued_job(client_in, fake_mode):
    job = post(client_in, "/api/jobs", {"step_id": "images.build"}).json()
    res = post(client_in, f"/api/jobs/{job['id']}/cancel")
    assert res.json()["status"] == "cancelled"
    assert post(client_in, f"/api/jobs/{job['id']}/cancel").status_code == 409
    assert AuditEntry.objects.filter(action="job.cancel").exists()


# --- running jobs (fake world) --------------------------------------------------------------


async def run_step(step_id: str, params=None) -> Job:
    job = await sync_to_async(service.create_job)(step_id, params, "alice", "macos")
    return await executor.execute(await sync_to_async(Job.objects.get)(pk=job.pk))


@pytest.mark.django_db(transaction=True)
async def test_build_job_streams_logs_and_rechecks(fake_mode):
    job = await run_step("images.build")
    assert (job.status, job.exit_code) == ("succeeded", 0)
    lines = await sync_to_async(service.read_log)(job)
    assert lines[0] == {**lines[0], "stream": "cmd", "text": "$ make image"}
    assert any("naming to docker.io/library/ar3-dev" in ln["text"] for ln in lines)
    assert [ln["seq"] for ln in lines] == list(range(1, len(lines) + 1))
    assert fake.load_state(fake_mode)["images"]["ar3-dev"] is True
    stored = {r["id"]: r for r in await sync_to_async(service.stored_results)()}
    assert stored["images.dev"]["status"] == "pass"  # post-check re-ran


@pytest.mark.django_db(transaction=True)
async def test_failed_job_explains_itself(fake_mode):
    line = "Error response from daemon: Bind for 0.0.0.0:8765 failed: port is already allocated"
    fake.save_state(fake_mode, {"fail": {"make smoke-sim": line}})
    job = await run_step("sim.smoke")
    assert (job.status, job.exit_code) == ("failed", 1)
    hit = job.matches[0]
    assert hit["id"] == "port_in_use" and "one stack per host" in hit["hint"]
    assert hit["line"] == line
    # A job opened after it finished gets the same highlighted lines as one watched live.
    replay = await sync_to_async(service.read_log)(job)
    assert [(ln["seq"], ln["match"]) for ln in replay if "match" in ln] == [
        (hit["seq"], "port_in_use")
    ]


@pytest.mark.django_db(transaction=True)
async def test_python_step_creates_docker_env(fake_mode):
    job = await run_step("env.create")
    assert job.status == "succeeded"
    repo = fake.ensure_repo(fake_mode, executor.settings.REPO_DIR)
    assert (repo / "docker/.env").read_text() == (repo / "docker/.env.example").read_text()
    job = await run_step("env.create")
    lines = await sync_to_async(service.read_log)(job)
    assert "left unchanged" in lines[-1]["text"]


@pytest.mark.django_db(transaction=True)
async def test_secrets_never_reach_the_log(fake_mode, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-supersecretvalue")
    fake.save_state(fake_mode, {"fail": {"make ws-build": "token sk-ant-api03-supersecretvalue"}})
    job = await run_step("ws.build")
    text = "\n".join(ln["text"] for ln in await sync_to_async(service.read_log)(job))
    assert "supersecret" not in text and "«redacted»" in text


async def test_real_runner_cancel_and_timeout():
    lines = []

    async def on_line(stream, text):
        lines.append((stream, text))

    cancel = asyncio.Event()
    asyncio.get_running_loop().call_later(0.3, cancel.set)
    rc = await CommandRunner().stream(["sleep", "30"], on_line, cancel=cancel, timeout=20)
    assert rc == CANCELLED and ("err", "cancelled") in lines
    rc = await CommandRunner().stream(["sleep", "30"], on_line, timeout=0.3)
    assert rc == TIMED_OUT
    rc = await CommandRunner().stream(["sh", "-c", "echo a; echo b >&2; exit 3"], on_line)
    assert rc == 3 and ("out", "a") in lines and ("err", "b") in lines


# --- continue setup -------------------------------------------------------------------------


def test_continue_runs_the_next_fixable_step(client_in, fake_mode):
    client_in.get("/api/checks")  # first run stores the fresh-clone results
    res = post(client_in, "/api/setup/continue").json()
    assert res["check"] == "repo.env" and res["job"]["step_id"] == "env.create"
    Job.objects.update(status=Job.SUCCEEDED)
    # images.dev fails but ws.build waits for it: the order follows the checklist
    results = service.stored_results()
    for r in results:
        if r["id"] == "repo.env":
            r["status"] = "pass"
    check, step = service.next_fix(results, "macos")
    assert (check["id"], step.id) == ("images.dev", "images.build")


def test_continue_with_nothing_runnable_lists_manual_items(client_in, fake_mode, settings):
    models = {"up": True, "models": ["llama3.1:8b", "bge-m3"]}
    fake.save_state(fake_mode, {"docker": False, "ollama": models})
    repo = fake.ensure_repo(fake_mode, settings.REPO_DIR)
    (repo / "docker/.env").write_text("ROS_RMW=rmw_fastrtps_cpp\n")  # env.create needs no Docker
    client_in.get("/api/checks")
    res = post(client_in, "/api/setup/continue").json()
    assert res["job"] is None  # images.build and the rest wait for docker.daemon
    assert "docker.daemon" in res["manual"]


def test_checklist_api_matches_ar3ctl_doctor(client_in, fake_mode, monkeypatch, capsys):
    """Acceptance C2: `ar3ctl doctor --json` equals the UI checklist."""
    from ar3ctl import cli

    api = client_in.post("/api/checks/run", {}, content_type="application/json").json()
    monkeypatch.setenv("COCKPIT_FAKE", "1")
    monkeypatch.setenv("COCKPIT_DATA_DIR", str(fake_mode))
    cli.main(["doctor", "--json"])
    doctor = json.loads(capsys.readouterr().out)

    def strip(checks):
        return [{k: v for k, v in c.items() if k != "last_checked"} for c in checks]

    assert strip(doctor["checks"]) == strip(api["checks"])
    assert doctor["host"] == api["host"]
    assert api["summary"]["total"] == sum(c["status"] != "skip" for c in api["checks"])


def test_rerun_single_check_and_unknown_id(client_in, fake_mode):
    res = client_in.post("/api/checks/run", {"ids": ["repo.env"]}, content_type="application/json")
    assert res.status_code == 200
    bad = client_in.post("/api/checks/run", {"ids": ["nope"]}, content_type="application/json")
    assert bad.status_code == 400


# --- logs, patterns, retention --------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
async def test_job_socket_sends_backlog_then_live(fake_mode, user):
    job = await run_step("ws.build")
    comm = WebsocketCommunicator(JobConsumer.as_asgi(), f"/ws/jobs/{job.id}")
    comm.scope["user"] = user
    comm.scope["url_route"] = {"kwargs": {"job_id": job.id}}
    assert (await comm.connect())[0]
    first = await comm.receive_json_from(timeout=5)
    assert first["type"] == "job" and first["job"]["status"] == "succeeded"
    backlog = await comm.receive_json_from(timeout=5)
    assert backlog["type"] == "lines" and backlog["lines"][0]["text"] == "$ make ws-build"
    await executor.broadcast(
        executor.job_group(job.id), {"type": "job.lines", "lines": [{"seq": 99}]}
    )
    assert (await comm.receive_json_from(timeout=5))["lines"] == [{"seq": 99}]
    await comm.disconnect()


def test_log_download(client_in, fake_mode):
    job = service.create_job("ws.build", None, "alice", "macos")
    with open(job.log_path, "w") as fh:
        fh.write("1\t2026-11-01T10:00:00\tout\thello\n")
    res = client_in.get(f"/api/jobs/{job.id}/log")
    assert res.content == b"2026-11-01T10:00:00 [out] hello\n"
    assert "attachment" in res["Content-Disposition"]


@pytest.mark.parametrize(
    ("line", "pattern"),
    [
        ("permission denied: /var/run/docker.sock", "docker_socket_denied"),
        ("write /var/lib/docker/tmp: no space left on device", "no_space"),
        ('Could not find a package configuration file provided by "x"', "cmake_package_missing"),
        ("Failed   <<< ar3_bringup [3.1s, exited with code 1]", "colcon_failed"),
        ("nvidia-container-cli: initialization error: driver not loaded", "nvidia_cli"),
        ("listen tcp 0.0.0.0:6080: bind: address already in use", "port_in_use"),
        ("A ROS stack is already running (vnc). Stop it first", "stack_running"),
        ("Summary: 34 tests, 0 errors, 2 failures, 0 skipped", "test_failures"),
        ("Temporary failure in name resolution", "network"),
    ],
)  # fmt: skip
def test_error_patterns(line, pattern):
    assert [m["id"] for m in patterns.match(line)] == [pattern]


def test_patterns_ignore_colour_codes():
    line = "\x1b[31mFailed   <<< ar3_bringup [3.1s, exited with code 1]\x1b[0m"
    assert [m["id"] for m in patterns.match(line)] == ["colcon_failed"]


def test_clean_lines_match_nothing():
    for line in ("Summary: 34 tests, 0 errors, 0 failures, 0 skipped", "Finished <<< ar3_bringup"):
        assert patterns.match(line) == []


def test_pattern_fix_steps_exist():
    for p in patterns.load():
        assert not p.fix_step or p.fix_step in steps.BY_ID, p.id


def test_prune_keeps_recent_and_the_last_200(settings):
    settings.JOB_KEEP_LAST = 2
    old = timezone.now() - timedelta(days=40)
    jobs = [Job.objects.create(step_id="ws.build", command="x", user="a") for _ in range(4)]
    Job.objects.filter(pk__in=[j.pk for j in jobs]).update(created=old, status=Job.SUCCEEDED)
    Job.objects.filter(pk=jobs[3].pk).update(created=old + timedelta(days=1))
    Job.objects.filter(pk=jobs[2].pk).update(created=old + timedelta(days=2))
    assert service.prune_jobs() == 2
    assert set(Job.objects.values_list("pk", flat=True)) == {jobs[2].pk, jobs[3].pk}
