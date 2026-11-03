"""Phase C5 (docs/specs/spec_cockpit.md §12, §18 C5): scenario schema and validation, the store,
randomization, success evaluation, runs through the agent (fake), reachability, the API,
history and compare, export / import and pack-and-go."""

import asyncio
import io
import json
import time
import zipfile

import pytest
import yaml
from asgiref.sync import async_to_sync

from config.envfiles import REPO
from core import agent, fake
from scenarios import evaluate, execute, pack, resolve, store, validate
from setup import executor, service
from setup.models import Job
from telemetry.models import Recording, Run

pytestmark = pytest.mark.django_db
EXAMPLE = (REPO / "config/scenarios/tour-three-objects.yaml").read_text()
UNREACHABLE = (REPO / "config/scenarios/unreachable-demo.yaml").read_text()
SORT = (REPO / "config/scenarios/sort-three-colors.yaml").read_text()


@pytest.fixture
def fake_mode(settings, monkeypatch):
    settings.FAKE = True
    monkeypatch.setattr(agent, "FAKE_READY_AFTER_S", 0.0)
    monkeypatch.setattr(agent, "FAKE_STEP_S", 0.05)
    return settings.DATA_DIR


@pytest.fixture
def sim_stack(fake_mode):
    fake.save_state(fake_mode, {"stack": {"service": "mac", "ar3_mode": "sim",
                                          "started": time.time()}})  # fmt: skip
    return fake_mode


def edit(text: str, **changes) -> str:
    data = yaml.safe_load(text)
    for key, value in changes.items():
        cur = data
        *path, last = key.split("__")
        for p in path:
            cur = cur[int(p)] if p.isdigit() else cur[p]
        cur[int(last) if last.isdigit() else last] = value
    return yaml.safe_dump(data, sort_keys=False)


def messages(res) -> list[str]:
    return [p["message"] for p in res["errors"]]


# --- validation ---------------------------------------------------------------------------------


def test_the_examples():
    assert validate.validate(EXAMPLE)["ok"]
    res = validate.validate(UNREACHABLE)
    assert res["ok"] and "beyond the approximate reach" in res["warnings"][0]["message"]
    res = validate.validate(SORT)
    assert not res["ok"] and "SPEC Phase 3" in messages(res)[0]


def test_schema_errors_carry_the_line():
    text = EXAMPLE.replace("    - gripper: open", "    - gripper: openish")
    res = validate.validate(text)
    assert not res["ok"]
    line = next(i for i, ln in enumerate(text.splitlines(), 1) if "openish" in ln)
    assert res["errors"][0]["line"] == line and res["errors"][0]["path"] == "task/steps/3/gripper"
    res = validate.validate("id: x\nname: [unclosed\n")
    assert res["errors"][0]["message"].startswith("YAML") and res["errors"][0]["line"] == 3


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"scene__objects__1__id": "cube_red"}, "is used twice"),
        ({"scene__objects__0__pose": {"x": 0.9, "y": 0.0}}, "is not on the table"),
        ({"scene__objects__1__pose": {"x": 0.255, "y": -0.145}}, "overlaps cube_red"),
        ({"scene__objects__0__pose": {"x": -0.2, "y": 0.25}}, "overlaps bin_red"),
        ({"task__steps__0__move_named": "dance"}, "no named pose 'dance'"),
        ({"start": {"named_pose": "nope"}}, "no named pose 'nope'"),
        ({"task__steps__3__gripper": 0.5}, "outside [0.0, 0.014] m (URDF)"),
        ({"task__steps__0": {"move_joints": [0, 0, 0, 0, 3.0, 0]}}, "joint_5 171.9°"),
        ({"world": "moon"}, "no Gazebo world 'moon'"),
        ({"modes": ["real"]}, "COCKPIT_REAL_SCENARIOS=1"),
        ({"perception": "camera"}, "SPEC Phase 2"),
        ({"success": {"objects_in_bins": {"ghost": "bin_red"}}}, "no object 'ghost'"),
        ({"success": {"objects_in_bins": {"cube_red": "bin_violet"}}}, "no bin 'bin_violet'"),
        ({"scene__bins": [{"name": "bin_purple", "ref": "config/bins.yaml"}]},
         "no bin 'purple' in config/bins.yaml"),
        ({"task__params": {"rules": "config/sorting.yaml"}}, "config/sorting.yaml does not exist"),
    ],
)  # fmt: skip
def test_semantic_checks(changes, expected):
    res = validate.validate(edit(EXAMPLE, **changes))
    assert any(expected in m for m in messages(res)), messages(res)


def test_real_needs_the_flag_and_no_randomize(monkeypatch):
    text = edit(EXAMPLE, modes=["real"], scene__randomize={"seed": 1, "xy_jitter_m": 0.01})
    monkeypatch.setenv("COCKPIT_REAL_SCENARIOS", "1")
    assert messages(validate.validate(text)) == ["randomize is not allowed on the real arm"]


def test_host_modes_and_capabilities():
    res = validate.validate(EXAMPLE, host_modes=["mock"], capabilities={"task_types": ["sequence"]})
    assert (
        res["ok"] and "sim_gazebo cannot be started on this host" in res["warnings"][0]["message"]
    )


def test_overlap_is_a_separating_axis_test():
    sq = [(0, 0), (1, 0), (1, 1), (0, 1)]
    assert validate.overlap(sq, [(0.5, 0.5), (1.5, 0.5), (1.5, 1.5), (0.5, 1.5)])
    assert not validate.overlap(sq, [(1.1, 0), (2, 0), (2, 1), (1.1, 1)])
    diamond = [(1.6, 0.5), (2.1, 1.0), (1.6, 1.5), (1.1, 1.0)]  # rotated, just apart
    assert not validate.overlap(sq, diamond)


# --- store ------------------------------------------------------------------------------------


def test_store_save_bumps_the_version_and_keeps_comments():
    sid, version = store.create(EXAMPLE)
    assert (sid, version) == ("tour-three-objects", 1)
    with pytest.raises(store.StoreError):
        store.create(EXAMPLE)
    text = store.read(sid).replace("name: Tour over three objects", "name: Tour (edited)")
    assert store.save(text, expected_id=sid) == (sid, 2)
    saved = store.read(sid)
    assert "# Five moves over a table" in saved and "version: 2" in saved
    with pytest.raises(store.StoreError):
        store.save(saved.replace("id: tour-three-objects", "id: other"), expected_id=sid)
    new_id, v = store.duplicate(sid)
    assert new_id == "tour-three-objects-copy" and v == 1
    assert yaml.safe_load(store.read(new_id))["name"] == "Tour (edited) (copy)"
    assert store.duplicate(sid)[0] == "tour-three-objects-copy-2"
    store.delete(new_id)
    assert not store.exists(new_id)
    with pytest.raises(store.StoreError):
        store.path("../etc/passwd")


def test_version_is_inserted_when_missing():
    text = "\n".join(ln for ln in EXAMPLE.splitlines() if not ln.startswith("version:"))
    store.create(text)
    assert yaml.safe_load(store.read("tour-three-objects"))["version"] == 1
    assert "id: tour-three-objects\nversion: 1\n" in store.read("tour-three-objects")  # after id:


# --- randomize and evaluate -------------------------------------------------------------------


def test_randomize_is_repeatable_and_stays_valid():
    data = yaml.safe_load(
        edit(EXAMPLE, scene__randomize={"xy_jitter_m": 0.03, "yaw_jitter_deg": 30})
    )
    a, seed_a = resolve.resolve(data, 7)
    b, _ = resolve.resolve(data, 7)
    c, _ = resolve.resolve(data, 8)
    assert seed_a == 7 and a == b and a != c
    assert a["scene"]["randomize"]["seed"] == 7
    moved = [o["pose"] for o in a["scene"]["objects"]]
    assert moved != [o["pose"] for o in data["scene"]["objects"]]
    assert validate.validate(yaml.safe_dump(a, sort_keys=False))["ok"]
    assert resolve.resolve(yaml.safe_load(EXAMPLE), 3) == (yaml.safe_load(EXAMPLE), None)


def test_in_bin():
    b = {"xyz": [-0.2, 0.25, 0.0], "yaw": 0.0, "size": [0.12, 0.12, 0.06], "wall": 0.004}
    assert evaluate.in_bin((-0.2, 0.25, 0.015), b)
    assert not evaluate.in_bin((-0.2, 0.31, 0.015), b)  # on the wall
    assert not evaluate.in_bin((-0.2, 0.25, 0.2), b)  # above it
    rotated = {**b, "yaw": 0.7853981634, "size": [0.2, 0.02, 0.06], "wall": 0.0}
    assert evaluate.in_bin((-0.2 + 0.05, 0.25 + 0.05, 0.01), rotated)


def test_evaluation():
    sc = yaml.safe_load(
        edit(
            EXAMPLE,
            success={
                "all_steps_succeed": True,
                "max_duration_s": 10,
                "objects_in_bins": {"cube_red": "bin_red"},
            },
        )
    )
    ok = {"result": "succeeded", "duration_s": 5.0,
          "object_poses": {"cube_red": {"xyz": [-0.2, 0.25, 0.015]}}}  # fmt: skip
    result, criteria = evaluate.evaluate(sc, ok, "sim_gazebo")
    assert result == "succeeded" and [c["status"] for c in criteria] == ["pass", "pass", "pass"]
    result, criteria = evaluate.evaluate(
        sc, {**ok, "object_poses": {"cube_red": {"xyz": [0.25, -0.15, 0.015]}}}, "sim_gazebo"
    )
    assert result == "failed" and criteria[1]["status"] == "fail"
    result, criteria = evaluate.evaluate(sc, ok, "sim_mock")
    assert result == "succeeded" and criteria[1]["status"] == "skipped"
    result, criteria = evaluate.evaluate(sc, {"result": "failed", "failed_step": 2, "error": "x",
                                              "duration_s": 20}, "sim_mock")  # fmt: skip
    assert (
        result == "failed"
        and criteria[0]["detail"] == "step 3: x"
        and criteria[-1]["status"] == "fail"
    )
    assert evaluate.evaluate(sc, {"result": "aborted"}, "sim_mock")[0] == "aborted"


# --- runs (fake agent) ------------------------------------------------------------------------


def run_job(params: dict) -> Job:
    job = service.create_job("scenario.run", params, "alice", "macos")
    return async_to_sync(executor.execute)(job)


def test_a_run_in_sim_gazebo(sim_stack):
    store.create(EXAMPLE)
    job = run_job({"scenario": "tour-three-objects"})
    log = [x["text"] for x in service.read_log(job)]
    assert job.status == "succeeded", log
    assert job.command == "make scenario-run ID=tour-three-objects"
    run = Run.objects.get()
    assert run.result == run.status == "succeeded" and run.mode == "sim_gazebo"
    assert run.user == "alice" and run.job_id == job.id and run.scenario_version == "1"
    assert [s["status"] for s in run.steps] == ["succeeded"] * 5
    assert run.steps[2]["label"] == "move_pose (0.1, -0.1, 0.12) pilz_lin"
    assert [c["criterion"] for c in run.evaluation] == ["all_steps_succeed", "max_duration_s"]
    assert run.metrics["object_poses"]["cube_red"]["xyz"][:2] == [0.25, -0.15]
    assert any("step 5: move_named home succeeded" in line for line in log)
    folder = execute.runs_dir() / str(run.id)
    assert {p.name for p in folder.iterdir()} == {"scenario.yaml", "events.jsonl", "run.log",
                                                  "metrics.json", "run.json"}  # fmt: skip
    assert run.session.recording is True


def test_a_failing_step(sim_stack):
    store.create(EXAMPLE)
    fake.save_state(sim_stack, {**fake.load_state(sim_stack), "run_fail_step": 1})
    job = run_job({"scenario": "tour-three-objects", "repeat": "2"})
    assert job.status == "failed"
    runs = list(Run.objects.order_by("repeat_index"))
    assert [r.result for r in runs] == ["failed", "failed"] and runs[0].failed_step == 1
    assert runs[0].steps[1]["status"] == "failed" and runs[0].steps[2]["status"] == "pending"
    assert "step 2: planning failed" in runs[0].evaluation[0]["detail"]


def test_abort_through_job_cancel(sim_stack, monkeypatch):
    monkeypatch.setattr(agent, "FAKE_STEP_S", 0.4)
    store.create(EXAMPLE)
    job = service.create_job("scenario.run", {"scenario": "tour-three-objects"}, "alice", "macos")

    async def go():
        task = asyncio.ensure_future(executor.execute(job))
        await asyncio.sleep(1.0)
        await Job.objects.filter(pk=job.pk).aupdate(cancel_requested=True)
        return await task

    done = async_to_sync(go)()
    run = Run.objects.get()
    assert run.result == "aborted" and done.status in ("failed", "cancelled")
    assert any(s["status"] == "aborted" for s in run.steps)


def test_refused_without_a_stack_or_mode(fake_mode):
    store.create(EXAMPLE)
    job = run_job({"scenario": "tour-three-objects"})
    assert job.status == "failed"
    assert any("no ROS stack is running" in x["text"] for x in service.read_log(job))
    fake.save_state(fake_mode, {"stack": {"service": "mac", "ar3_mode": "mock", "started": 1}})
    job = run_job({"scenario": "tour-three-objects", "mode": "sim_gazebo"})
    assert any("the running stack is sim_mock" in x["text"] for x in service.read_log(job))
    store.create(SORT)
    job = run_job({"scenario": "sort-three-colors"})
    assert any("SPEC Phase 3" in x["text"] for x in service.read_log(job))


@pytest.mark.django_db(transaction=True)  # asyncio.run: its own thread and DB connection
def test_ar3ctl_scenario_run(sim_stack, capsys, clean_raw_tables):
    from ar3ctl import cli

    store.create(EXAMPLE)
    args = type("A", (), {"scenario": "tour-three-objects", "mode": None, "repeat": 1, "seed": None,
                          "keep_scene": False, "bag": False})()  # fmt: skip
    assert cli.cmd_scenario_run(args) == 0
    assert "succeeded" in capsys.readouterr().out


# --- API ----------------------------------------------------------------------------------------


def post(c, path, data=None):
    return c.post(path, data or {}, content_type="application/json")


def test_crud_api(client_in):
    assert client_in.get("/api/scenarios").json() == []
    r = post(client_in, "/api/scenarios", {"yaml": EXAMPLE})
    assert r.status_code == 201 and r.json() == {"id": "tour-three-objects", "version": 1}
    bad = post(client_in, "/api/scenarios", {"yaml": SORT})
    assert bad.status_code == 422 and "SPEC Phase 3" in bad.json()["errors"][0]["message"]
    got = client_in.get("/api/scenarios/tour-three-objects").json()
    assert got["data"]["name"] == "Tour over three objects" and got["errors"] == []
    r = client_in.put("/api/scenarios/tour-three-objects", {"yaml": got["yaml"]},
                      content_type="application/json")  # fmt: skip
    assert r.json()["version"] == 2
    r = post(client_in, "/api/scenarios/validate", {"yaml": got["yaml"].replace("home", "hom")})
    assert not r.json()["ok"]
    assert post(client_in, "/api/scenarios/tour-three-objects/duplicate").json()["id"] == (
        "tour-three-objects-copy"
    )
    exp = client_in.get("/api/scenarios/tour-three-objects/export")
    assert exp["Content-Disposition"].endswith('tour-three-objects.yaml"')
    assert post(client_in, "/api/scenarios/import", {"yaml": EXAMPLE}).status_code == 409
    assert post(client_in, "/api/scenarios/import", {"yaml": EXAMPLE, "replace": True}).json()[
        "version"] == 3  # fmt: skip
    assert client_in.delete("/api/scenarios/tour-three-objects-copy").status_code == 400
    assert client_in.delete("/api/scenarios/tour-three-objects-copy?confirm=1").status_code == 200
    rows = client_in.get("/api/scenarios").json()
    assert [r["id"] for r in rows] == ["tour-three-objects"] and rows[0]["steps"] == 5
    ctx = client_in.get("/api/scenarios/context").json()
    assert ctx["table"]["size"] == [1.0, 0.8] and "observe" in ctx["named_poses"]["arm"]
    assert ctx["reach"]["radius_m"] > 0.5 and ctx["worlds"] == ["workcell"]
    from accounts.models import AuditEntry

    assert {"scenario.create", "scenario.edit", "scenario.import", "scenario.delete"} <= set(
        AuditEntry.objects.values_list("action", flat=True))  # fmt: skip


def test_reachability_reports_the_unreachable_step(client_in, sim_stack):
    store.create(UNREACHABLE)
    r = post(client_in, "/api/scenarios/unreachable-demo/reachability").json()
    assert r["reachable"] is False
    assert r["targets"][0]["step"] == 1 and r["targets"][0]["error"] == "NO_IK_SOLUTION"
    store.create(EXAMPLE)
    ok = post(client_in, "/api/scenarios/tour-three-objects/reachability").json()
    assert ok["reachable"] is True and len(ok["targets"]) == 2
    fake.save_state(sim_stack, {"stack": None})
    assert post(client_in, "/api/scenarios/tour-three-objects/reachability").status_code == 503


def test_run_api_history_and_compare(client_in, sim_stack):
    store.create(EXAMPLE)
    r = post(client_in, "/api/scenarios/tour-three-objects/runs", {"repeat": 2, "seed": 5})
    assert r.status_code == 201 and r.json()["step_id"] == "scenario.run"
    assert r.json()["command"] == "make scenario-run ID=tour-three-objects REPEAT=2 SEED=5"
    async_to_sync(executor.execute)(Job.objects.get(pk=r.json()["id"]))
    hist = client_in.get("/api/scenarios/tour-three-objects/runs").json()
    assert [h["result"] for h in hist] == ["succeeded", "succeeded"] and hist[0]["repeat"] == [2, 2]
    a, b = hist[1]["id"], hist[0]["id"]
    cmp = client_in.get("/api/runs/compare", {"a": a, "b": b}).json()
    assert len(cmp["steps"]) == 5 and cmp["steps"][0]["a"]["status"] == "succeeded"
    assert client_in.get(f"/api/runs/{a}").json()["scenario"] == "tour-three-objects"
    assert "succeeded" in client_in.get(f"/api/runs/{a}/log").content.decode()
    assert post(client_in, f"/api/runs/{a}/abort").status_code == 409  # finished
    last = client_in.get("/api/scenarios").json()[0]["last_run"]
    assert last["result"] == "succeeded"
    fake.save_state(sim_stack, {"stack": None})
    refused = post(client_in, "/api/scenarios/tour-three-objects/runs")
    assert refused.status_code == 409 and "no ROS stack" in refused.json()["detail"]


def test_run_websocket(client_in, user, sim_stack):
    from channels.testing import WebsocketCommunicator

    from config.asgi import application

    store.create(EXAMPLE)
    run_job({"scenario": "tour-three-objects"})
    run = Run.objects.get()

    async def go():
        comm = WebsocketCommunicator(application, f"/ws/runs/{run.id}",
                                     headers=[(b"origin", b"http://127.0.0.1")])  # fmt: skip
        comm.scope["user"] = user
        ok, _ = await comm.connect()
        msg = await comm.receive_json_from()
        await comm.disconnect()
        return ok, msg

    ok, msg = async_to_sync(go)()
    assert ok and msg["state"]["result"] == "succeeded"
    assert any(e["kind"] == "finished" for e in msg["events"])


# --- pack-and-go --------------------------------------------------------------------------------


def test_pack_and_unpack(settings, tmp_path):
    store.create(EXAMPLE)
    store.create(UNREACHABLE)
    name, data = pack.pack(["tour-three-objects", "unreachable-demo"])
    assert name.startswith("ar3-scenarios-") and name.endswith(".zip")
    z = zipfile.ZipFile(io.BytesIO(data))
    manifest = json.loads(z.read("manifest.json"))
    assert manifest["format"] == pack.FORMAT
    assert [s["id"] for s in manifest["scenarios"]] == ["tour-three-objects", "unreachable-demo"]
    assert "config/robot.yaml" in z.namelist()

    # the other host: one scenario differs, the other is new
    settings.SCENARIOS_DIR = tmp_path / "other"
    store.create(EXAMPLE.replace("Tour over three objects", "Tour (theirs)"))
    prev = pack.preview(data)
    by_id = {s["id"]: s for s in prev["scenarios"]}
    assert by_id["tour-three-objects"]["status"] == "differs"
    assert "-name: Tour (theirs)" in by_id["tour-three-objects"]["diff"]
    assert by_id["unreachable-demo"]["status"] == "new" and prev["config_warnings"] == []
    done = pack.apply(prev["token"], {"tour-three-objects": "copy", "unreachable-demo": "replace"})
    assert {d["id"]: d["action"] for d in done["done"]} == {
        "tour-three-objects-imported": "copied", "unreachable-demo": "replaced"}  # fmt: skip
    assert yaml.safe_load(store.read("tour-three-objects"))["name"] == "Tour (theirs)"
    assert store.exists("tour-three-objects-imported")
    with pytest.raises(pack.PackError):
        pack.apply(prev["token"], {})  # used up


def test_pack_warns_about_a_different_table(settings, tmp_path, monkeypatch):
    store.create(EXAMPLE)
    _name, data = pack.pack(["tour-three-objects"])
    z = zipfile.ZipFile(io.BytesIO(data))
    robot = yaml.safe_load(z.read("config/robot.yaml"))
    robot["table"]["size"] = [1.2, 0.8]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        for item in z.namelist():
            body = yaml.safe_dump(robot).encode() if item == "config/robot.yaml" else z.read(item)
            if item == "manifest.json":
                m = json.loads(body)
                for f in m["files"]:
                    if f["path"] == "config/robot.yaml":
                        f["sha256"] = "changed"
                body = json.dumps(m).encode()
            out.writestr(item, body)
    prev = pack.preview(buf.getvalue())
    assert any("`table` differs" in w for w in prev["config_warnings"])


def test_pack_refuses_bad_zips():
    with pytest.raises(pack.PackError):
        pack.preview(b"not a zip")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", json.dumps({"format": pack.FORMAT, "scenarios": []}))
        z.writestr("../evil.yaml", "x")
    with pytest.raises(pack.PackError, match="unsafe path"):
        pack.preview(buf.getvalue())


def test_pack_api(client_in):
    store.create(EXAMPLE)
    r = post(client_in, "/api/scenarios/pack", {"ids": ["tour-three-objects"]})
    assert r["Content-Type"] == "application/zip"
    from django.core.files.uploadedfile import SimpleUploadedFile

    prev = client_in.post("/api/scenarios/unpack",
                          {"file": SimpleUploadedFile("p.zip", r.content)}).json()  # fmt: skip
    assert prev["scenarios"][0]["status"] == "same"
    done = post(client_in, "/api/scenarios/unpack/apply", {"token": prev["token"],
                                                         "decisions": {}}).json()  # fmt: skip
    assert done["done"][0]["action"] == "kept"


def test_recording_of_a_run(sim_stack, monkeypatch):
    """A run with record=1: the agent's bag path becomes a Recording of the run."""
    store.create(EXAMPLE)
    real_events = agent.FakeAgent.run_events

    async def with_bag(self, run_id, after=0):
        async for msg in real_events(self, run_id, after):
            for ev in msg["events"]:
                if ev["kind"] == "finished":
                    ev["bag"] = f"data/bags/run_{run_id}"
            yield msg

    monkeypatch.setattr(agent.FakeAgent, "run_events", with_bag)
    run_job({"scenario": "tour-three-objects", "record": "1"})
    rec = Recording.objects.get()
    assert rec.run_id == Run.objects.get().id and rec.path.startswith("data/bags/run_")
