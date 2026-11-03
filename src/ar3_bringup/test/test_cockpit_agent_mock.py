"""Integration test: the cockpit agent in the mock bringup (docs/specs/spec_cockpit.md §16, C3, C4).

The agent reports the stack ready, refuses requests without its token, and its software stop
halts a running motion: the arm holds where it was, short of the goal. Its telemetry stream
sends joint samples on the 10 Hz / 50 Hz grid, /rosout has move_group's lines, the graph has
publishers and subscribers, and node parameters are readable. Runs in CI.
"""

import itertools
import json
import os
import socket
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import launch_testing
import launch_testing.actions
import pytest
import rclpy
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription, SetEnvironmentVariable
from launch.launch_description_sources import PythonLaunchDescriptionSource
from moveit_msgs.action import MoveGroup
from moveit_msgs.msg import MoveItErrorCodes
from rclpy.action import ActionClient

from ar3_bringup.named_poses import NamedPoseClient, joint_goal
from ar3_bringup.testing import latest_joint_state

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"
TOKEN = "launch-test-token"
PORT = 18790  # not the default 8790: a developer's stack may hold that one
BASE = f"http://127.0.0.1:{PORT}"


@pytest.mark.launch_test
def generate_test_description():
    bringup = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(get_package_share_directory("ar3_bringup"), "launch", "bringup.launch.py")
        ),
        launch_arguments={"mode": "mock", "rviz": "false", "config_dir": str(CONFIG_DIR)}.items(),
    )
    return LaunchDescription(
        [
            SetEnvironmentVariable("COCKPIT_AGENT_TOKEN", TOKEN),
            SetEnvironmentVariable("COCKPIT_AGENT_PORT", str(PORT)),
            bringup,
            launch_testing.actions.ReadyToTest(),
        ]
    )


def request(path, method="GET", token=TOKEN, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, method=method, data=data)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def ws_messages(path, seconds, token=TOKEN):
    """A minimal WebSocket client (wsproto ships with uvicorn; no websockets in the image):
    the JSON messages of ``seconds``, or the HTTP status of a refused handshake."""
    from wsproto import WSConnection
    from wsproto.connection import ConnectionType
    from wsproto.events import (
        AcceptConnection,
        CloseConnection,
        RejectConnection,
        Request,
        TextMessage,
    )

    sock = socket.create_connection(("127.0.0.1", PORT), timeout=5)
    sock.settimeout(0.5)
    ws = WSConnection(ConnectionType.CLIENT)
    headers = [(b"authorization", f"Bearer {token}".encode())] if token else []
    sock.sendall(ws.send(Request(host=f"127.0.0.1:{PORT}", target=path, extra_headers=headers)))
    msgs, text = [], ""
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            try:
                data = sock.recv(65536)
            except TimeoutError:
                continue
            if not data:
                break
            ws.receive_data(data)
            for ev in ws.events():
                if isinstance(ev, RejectConnection):
                    return ev.status_code
                if isinstance(ev, CloseConnection):
                    return msgs if msgs else ev.code
                if isinstance(ev, AcceptConnection):
                    continue
                if isinstance(ev, TextMessage):
                    text += ev.data
                    if ev.message_finished:
                        msgs.append(json.loads(text))
                        text = ""
        sock.sendall(ws.send(CloseConnection(code=1000)))
    finally:
        sock.close()
    return msgs


def wait_ready(timeout=120.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            status, body = request("/health")
            last = body
            if status == 200 and body["ready"]:
                return body
        except OSError:
            pass
        time.sleep(1.0)
    raise AssertionError(f"agent not ready after {timeout} s: {last}")


class TestCockpitAgentMock(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init()
        cls.node = NamedPoseClient("test_cockpit_agent_mock")
        cls.health = wait_ready()

    @classmethod
    def tearDownClass(cls):
        cls.node.destroy_node()
        rclpy.shutdown()

    def test_health_ready_in_mock(self):
        components = self.health["components"]
        self.assertEqual(self.health["mode"], "mock")
        self.assertEqual(components["controllers"]["status"], "ready")
        self.assertEqual(components["move_group"]["status"], "ready")
        self.assertEqual(components["gazebo"]["status"], "off")

    def test_token_required(self):
        self.assertEqual(request("/health", token=None)[0], 401)
        self.assertEqual(request("/stop", method="POST", token="wrong")[0], 401)

    def test_controllers_and_graph(self):
        status, controllers = request("/controllers")
        self.assertEqual(status, 200)
        self.assertTrue({c["name"] for c in controllers} >= {"joint_trajectory_controller"})
        status, graph = request("/graph")
        self.assertIn("/move_group", graph["nodes"])
        self.assertIn("/move_action", {a["name"] for a in graph["actions"]})

    def test_telemetry_stream_on_the_grid(self):
        """C4: joint plots at 10 Hz; the stored rate (50 Hz) has no gaps."""
        for rate, seconds in ((10, 3.0), (50, 2.0)):
            msgs = ws_messages(f"/ws/telemetry?rate={rate}", seconds)
            self.assertIsInstance(msgs, list, msgs)
            names = msgs[-1]["names"]
            self.assertTrue({f"joint_{i}" for i in range(1, 7)} <= set(names), names)
            samples = [s for m in msgs for s in m["samples"]]
            self.assertGreaterEqual(len(samples), int(rate * seconds * 0.7), len(samples))
            steps = {round(b["t"] - a["t"], 3) for a, b in itertools.pairwise(samples)}
            self.assertEqual(steps, {round(1 / rate, 3)}, f"{rate} Hz: steps {steps}")
            self.assertEqual(len(samples[0]["p"]), len(names))
            self.assertIn("cp", samples[-1])  # commanded, from the controller state
            self.assertTrue(any("rates" in m for m in msgs))
        self.assertEqual(ws_messages("/ws/telemetry?rate=10", 1.0, token=None), 403)

    def test_rosout_stream(self):
        msgs = ws_messages("/ws/rosout?after=0", 2.0)
        lines = [line for m in msgs for line in m["lines"]]
        self.assertTrue(any(x["node"] == "move_group" for x in lines), lines[:5])
        self.assertTrue({x["level"] for x in lines} <= {"DEBUG", "INFO", "WARN", "ERROR", "FATAL"})

    def test_graph_detail_rates_and_params(self):
        status, graph = request("/graph/detail")
        self.assertEqual(status, 200)
        js = next(t for t in graph["topics"] if t["name"] == "/joint_states")
        self.assertIn("/joint_state_broadcaster", js["publishers"])
        # move_group subscribes through its internal node (move_group_private_<n>)
        self.assertTrue(any(n.startswith("/move_group") for n in js["subscribers"]), js)
        self.assertEqual(len(js["subscribers"]), len(set(js["subscribers"])))
        status, rates = request("/topics/rates")
        self.assertGreater(
            rates["/joint_states"]["hz"], 10, rates
        )  # 100 Hz nominal, less under load
        status, params = request("/node/params?name=/move_group")
        self.assertEqual(status, 200, params)
        values = {p["name"]: p["value"] for p in params["params"]}
        self.assertIn("use_sim_time", values, f"{len(values)}: {sorted(values)[:30]}")
        self.assertIs(values["use_sim_time"], False)
        self.assertEqual(request("/node/params?name=/no_such_node")[0], 404)
        status, hz = request("/topic/rate?name=/joint_states&seconds=1")
        self.assertGreater(hz["hz"], 10, hz)

    def test_ik_check(self):
        down = [180, 0, -90]
        status, res = request("/ik/check", "POST", body={"targets": [
            {"x": 0.1, "y": 0.1, "z": 0.2, "rpy_deg": down},
            {"x": 0.6, "y": 0.3, "z": 0.1, "rpy_deg": down}]})  # fmt: skip
        self.assertEqual(status, 200, res)
        self.assertEqual([t["reachable"] for t in res["targets"]], [True, False], res)
        self.assertEqual(res["targets"][1]["error"], "NO_IK_SOLUTION")

    def test_scenario_run(self):
        down = [180, 0, -90]
        sc = {
            "start": {"named_pose": "home"},
            "scene": {"objects": [
                {"id": "cube", "shape": "box", "size": [0.03, 0.03, 0.03],
                 "pose": {"x": 0.25, "y": -0.15}, "spawn": True},
                {"id": "can", "shape": "cylinder", "size": [0.03, 0.05],
                 "pose": {"x": 0.3, "y": 0.0}},
                {"id": "ball", "shape": "sphere", "size": [0.04],
                 "pose": {"x": 0.22, "y": 0.08}}]},
            # Pilz LIN: above 0.2, joint_5 exceeds its acceleration limit on this path
            "planning": {"velocity_scaling": 0.2, "acceleration_scaling": 0.2},
            "task": {"type": "sequence", "steps": [
                {"move_named": "observe"},
                {"move_pose": {"x": 0.1, "y": -0.1, "z": 0.2, "rpy_deg": down}},
                {"move_pose": {"x": 0.1, "y": -0.1, "z": 0.12, "rpy_deg": down,
                               "planner": "pilz_lin"}},
                {"gripper": "open"},
                {"wait": 0.2}]},
        }  # fmt: skip
        status, started = request("/run", "POST", body={"run_id": "ci-run", "scenario": sc})
        self.assertEqual(status, 200, started)
        deadline = time.monotonic() + 180
        state = started
        while time.monotonic() < deadline:
            state = request("/run/ci-run")[1]
            if state["status"] not in ("starting", "running", "paused"):
                break
            time.sleep(0.5)
        self.assertEqual(state["status"], "succeeded", state)
        self.assertEqual([st["status"] for st in state["steps"]], ["succeeded"] * 5)
        self.assertGreater(state["steps"][1]["execution_s"], 0)
        self.assertEqual(state["result"]["object_poses"], {})  # mock: no Gazebo
        # the run cleaned up its planning-scene objects
        status, cleared = request("/scene/clear", "POST", body={})
        self.assertEqual(cleared["planning_scene"], [])
        msgs = ws_messages("/ws/run/ci-run?after=0", 1.0)
        kinds = [e["kind"] for m in msgs if isinstance(m, dict) for e in m.get("events", [])]
        self.assertIn("finished", kinds)

    def test_software_stop_halts_a_running_motion(self):
        self.assertTrue(self.node.wait_ready(timeout=60.0))
        states = self.node.named_states()["arm"]
        start = latest_joint_state(self.node)
        # The named pose farthest from where the arm is now, for the longest motion.
        target = max(
            states,
            key=lambda n: sum(abs(states[n][j] - start[j]) for j in states[n]),
        )
        goal = MoveGroup.Goal()
        goal.request.group_name = "arm"
        goal.request.goal_constraints = [joint_goal(states[target])]
        goal.request.allowed_planning_time = 5.0
        goal.request.max_velocity_scaling_factor = 0.05  # slow: seconds of motion to stop
        goal.request.max_acceleration_scaling_factor = 0.05
        client = ActionClient(self.node, MoveGroup, "/move_action")
        self.assertTrue(client.wait_for_server(timeout_sec=10.0))
        send = client.send_goal_async(goal)
        rclpy.spin_until_future_complete(self.node, send, timeout_sec=10.0)
        self.assertTrue(send.result().accepted)

        moving = self._wait_moving(start, timeout=20.0)
        self.assertTrue(moving, "the arm did not start moving")
        status, stopped = request("/stop", method="POST")
        self.assertEqual(status, 200, stopped)
        self.assertGreaterEqual(sum(c["goals"] for c in stopped["cancelled"]), 1, stopped)
        self.assertEqual(stopped["errors"], [], stopped)
        self.assertTrue(stopped["held"], f"no trajectory controller was held: {stopped}")

        result = send.result().get_result_async()
        rclpy.spin_until_future_complete(self.node, result, timeout_sec=10.0)
        self.assertIsNotNone(result.result(), "the cancelled goal never finished")
        self.assertNotEqual(result.result().result.error_code.val, MoveItErrorCodes.SUCCESS)

        time.sleep(0.5)  # let the controller settle on its hold position
        held = latest_joint_state(self.node)
        time.sleep(1.5)
        later = latest_joint_state(self.node)
        drift = max(abs(later[j] - held[j]) for j in states[target])
        self.assertLess(drift, 1e-3, f"the arm kept moving after the stop ({drift:.4f} rad)")
        remaining = max(abs(states[target][j] - later[j]) for j in states[target])
        self.assertGreater(remaining, 0.02, "the arm reached the goal: nothing was stopped")

    def _wait_moving(self, start, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            now = latest_joint_state(self.node)
            if max(abs(now[j] - start[j]) for j in start if j.startswith("joint_")) > 0.01:
                return True
        return False


@launch_testing.post_shutdown_test()
class TestShutdown(unittest.TestCase):
    def test_agent_exit_code(self, proc_info):
        for info in proc_info:
            if info.action.process_details["name"].startswith("cockpit_agent"):
                self.assertIn(info.returncode, [0, -2, -15, 2, 15])
