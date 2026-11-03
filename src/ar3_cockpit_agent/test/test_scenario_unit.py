"""Scenario helpers without ROS: frames, shapes and SDF, poses, SRDF, timeouts; and the run
endpoints of the HTTP API with a fake run machinery."""

import math
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from ar3_cockpit_agent import scenario, telemetry
from ar3_cockpit_agent.app import create_app

TOKEN = "unit-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CUBE = {"id": "cube_red_1", "shape": "box", "size": [0.03, 0.03, 0.03], "color": "red",
        "pose": {"x": 0.3, "y": -0.05, "yaw_deg": 90}}  # fmt: skip


def test_frames_round_trip():
    table = (0.0, 0.0, 0.75)
    assert scenario.table_to_world((0.3, -0.05, 0.015), table) == pytest.approx((0.3, -0.05, 0.765))
    rpy = (0.0, 0.0, math.pi / 2)
    w = scenario.table_to_world((1.0, 0.0, 0.0), (1.0, 2.0, 0.0), rpy)
    assert w == pytest.approx((1.0, 3.0, 0.0))
    assert scenario.world_to_table(w, (1.0, 2.0, 0.0), rpy) == pytest.approx((1.0, 0.0, 0.0))


def test_quaternions():
    assert scenario.rpy_to_quat(0, 0, 0) == pytest.approx((0, 0, 0, 1))
    x, y, z, w = scenario.rpy_to_quat(math.pi, 0, -math.pi / 2)  # TCP down at `observe`
    assert (abs(x), abs(y), abs(z), abs(w)) == pytest.approx((0.7071, 0.7071, 0, 0), abs=1e-4)


def test_geometry_and_rest_height():
    assert scenario.geometry(CUBE) == ("box", [0.03, 0.03, 0.03])
    cyl = {"id": "c", "shape": "cylinder", "size": [0.04, 0.06]}  # diameter, height
    assert scenario.geometry(cyl) == ("cylinder", [0.06, 0.02])  # SolidPrimitive: height, radius
    assert scenario.half_height(cyl) == pytest.approx(0.03)
    assert scenario.half_height({"id": "s", "shape": "sphere", "size": [0.05]}) == 0.025
    xyz, quat = scenario.object_pose(CUBE)
    assert xyz == pytest.approx((0.3, -0.05, 0.015))  # z defaults to resting on the table
    with pytest.raises(ValueError):
        scenario.geometry({"id": "m", "shape": "mesh", "size": [1]})


def test_sdf_is_valid_and_has_the_inertia():
    root = ET.fromstring(scenario.sdf(CUBE))
    model = root.find("model")
    assert model.get("name") == "scn_cube_red_1"
    assert model.findtext("static") == "false"
    mass = float(model.findtext("link/inertial/mass"))
    assert mass == pytest.approx(500 * 0.03**3)
    assert float(model.findtext("link/inertial/inertia/ixx")) == pytest.approx(mass * 0.0018 / 12)
    assert model.findtext("link/visual/material/diffuse") == "0.8 0.1 0.1 1.0"


def test_sdf_of_cylinders_and_spheres():
    cyl = ET.fromstring(scenario.sdf({"id": "c", "shape": "cylinder", "size": [0.03, 0.05]}))
    assert cyl.findtext("model/link/collision/geometry/cylinder/radius") == "0.015"
    assert cyl.findtext("model/link/collision/geometry/cylinder/length") == "0.05"
    ball = ET.fromstring(scenario.sdf({"id": "s", "shape": "sphere", "size": [0.04]}, static=True))
    assert ball.findtext("model/link/visual/geometry/sphere/radius") == "0.02"
    assert ball.findtext("model/static") == "true"


def test_gz_cli_output():
    listing = (
        "Requesting state…\nWarning [Utils.cc]: x\nAvailable models:\n"
        "    - ground_plane\n    - scn_a\n"
    )
    assert scenario.parse_model_list(listing) == ["ground_plane", "scn_a"]
    pose = ("Model: [8]\n  - Name: red_cube\n  - Pose [ XYZ (m) ] [ RPY (rad) ]:\n"
            "    [-0.020000 -0.200000 0.765000]\n    [-0.000000 -0.000000 0.300000]\n")  # fmt: skip
    xyz, quat = scenario.parse_model_pose(pose)
    assert xyz == (-0.02, -0.2, 0.765)
    assert quat == pytest.approx(scenario.rpy_to_quat(0, 0, 0.3))
    assert scenario.parse_model_pose("no such model") is None


def test_names_steps_and_timeouts():
    assert scenario.model_name("scn_x") == "scn_x" and scenario.model_name("x") == "scn_x"
    assert scenario.step_kind({"move_named": "home"}) == "move_named"
    with pytest.raises(ValueError):
        scenario.step_kind({"move_named": "home", "wait": 1})
    assert scenario.execution_timeout(2.0) == 10.0 and scenario.execution_timeout(20.0) == 60.0
    assert scenario.describe(
        {"move_pose": {"x": 0.1, "y": 0, "z": 0.2, "planner": "pilz_lin"}}
    ) == ("move_pose (0.1, 0, 0.2) pilz_lin")
    srdf = ('<robot><group_state name="home" group="arm"><joint name="joint_1" value="0"/>'
            '</group_state><group_state name="open" group="gripper">'
            '<joint name="gripper_jaw1_joint" value="0.014"/></group_state></robot>')  # fmt: skip
    assert scenario.parse_group_states(srdf) == {
        "arm": {"home": {"joint_1": 0.0}},
        "gripper": {"open": {"gripper_jaw1_joint": 0.014}},
    }


class FakeRun:
    def __init__(self):
        self.events = telemetry.Ring()
        self.active = True
        self.aborted = False
        self.paused = None

    def state(self):
        return {"id": "r1", "status": "running" if self.active else "aborted"}

    def abort(self):
        self.aborted, self.active = True, False
        self.events.push({"kind": "finished", "result": "aborted"})

    def pause(self, on):
        self.paused = on


class FakeRuns:
    def __init__(self):
        self.runs = {}

    def start(self, run_id, sc, options):
        if any(r.active for r in self.runs.values()):
            raise RuntimeError("busy")
        self.runs[run_id] = FakeRun()
        return self.runs[run_id].state()

    def check_ik(self, targets):
        return [{**t, "reachable": t["x"] < 1.0} for t in targets]

    def clear_scene(self):
        return {"planning_scene": ["scn_a"], "gazebo": []}


class FakeAgent:
    mode = "sim"

    def __init__(self):
        self.runs = FakeRuns()

    def motion(self):
        return {"allowed": True, "reason": "simulation"}


@pytest.fixture
def agent():
    return FakeAgent()


@pytest.fixture
def client(agent):
    return TestClient(create_app(agent, TOKEN))


def test_capabilities_list_the_task_types(client):
    caps = client.get("/capabilities", headers=AUTH).json()
    assert caps["task_types"] == ["sequence"] and "move_pose" in caps["step_types"]
    assert caps["gazebo"] is True


def test_run_endpoints(client, agent):
    assert client.post("/run", json={}, headers=AUTH).status_code == 400
    sc = {"task": {"type": "sequence", "steps": []}}
    assert (
        client.post("/run", json={"run_id": "r1", "scenario": sc}, headers=AUTH).status_code == 200
    )
    assert (
        client.post("/run", json={"run_id": "r2", "scenario": sc}, headers=AUTH).status_code == 409
    )
    assert client.post("/run/r1/pause", json={"paused": True}, headers=AUTH).status_code == 200
    assert agent.runs.runs["r1"].paused is True
    assert client.post("/run/r1/abort", headers=AUTH).json()["status"] == "aborted"
    assert client.get("/run/nope", headers=AUTH).status_code == 404
    with client.websocket_connect("/ws/run/r1", headers=AUTH) as ws:
        msg = ws.receive_json()
    assert msg["type"] == "run" and msg["events"][0]["result"] == "aborted"
    ik = client.post("/ik/check", json={"targets": [{"x": 0.2}, {"x": 1.5}]}, headers=AUTH).json()
    assert [t["reachable"] for t in ik["targets"]] == [True, False]
    assert client.post("/scene/clear", headers=AUTH).json()["planning_scene"] == ["scn_a"]
