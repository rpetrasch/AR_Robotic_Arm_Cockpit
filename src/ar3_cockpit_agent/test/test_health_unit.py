"""Health and motion policy of the cockpit agent, without ROS."""

import pytest

from ar3_cockpit_agent import health


def test_rate_meter_counts_within_the_window():
    meter = health.RateMeter(window_s=2.0)
    for i in range(101):
        meter.tick(i * 0.01)  # 100 Hz for 1 s
    assert meter.rate(1.0) == pytest.approx(100.0)
    assert meter.rate(10.0) == 0.0  # everything left the window


def test_gazebo_needs_advancing_clock_above_50_hz():
    assert health.gazebo("mock", 0, False)["status"] == "off"
    assert health.gazebo("sim", 0, False)["status"] == "waiting"
    assert health.gazebo("sim", 200, False)["status"] == "waiting"
    assert health.gazebo("sim", 40, True)["status"] == "waiting"
    assert health.gazebo("sim", 200, True)["status"] == "ready"
    assert health.gazebo("remote", 0, False)["status"] == "off"


def test_controllers_all_three_active():
    assert health.controllers(None)["status"] == "waiting"
    listed = [{"name": n, "state": "active"} for n in health.REQUIRED_CONTROLLERS]
    assert health.controllers(listed)["status"] == "ready"
    listed[1]["state"] = "inactive"
    result = health.controllers(listed)
    assert result["status"] == "waiting"
    assert "joint_trajectory_controller inactive" in result["detail"]
    assert "gripper_controller missing" in health.controllers(listed[:2])["detail"]


def test_move_group_needs_node_and_action():
    assert health.move_group(False, False)["status"] == "waiting"
    assert health.move_group(True, False)["status"] == "waiting"
    assert health.move_group(True, True)["status"] == "ready"


def test_cameras_against_the_configured_rate():
    expected = {"cam_overhead": 15.0, "cam_wrist": 15.0}
    assert health.cameras("mock", {}, expected)["status"] == "off"
    assert health.cameras("sim", {"cam_overhead": 0, "cam_wrist": 0}, expected)["status"] == (
        "waiting"
    )
    assert health.cameras("sim", {"cam_overhead": 14, "cam_wrist": 13}, expected)["status"] == (
        "ready"
    )
    slow = health.cameras("sim", {"cam_overhead": 14, "cam_wrist": 5}, expected)
    assert slow["status"] == "warn"
    assert "cam_wrist 5.0/15 Hz" in slow["detail"]


def _components(**status):
    names = ("gazebo", "controllers", "move_group", "cameras", "safety")
    return {n: {"status": status.get(n, "ready"), "detail": ""} for n in names}


def test_summary_ignores_cameras_and_components_of_other_modes():
    assert health.summary("sim", _components(cameras="warn"))["ready"]
    assert health.summary("mock", _components(gazebo="off", cameras="off"))["ready"]
    waiting = health.summary("sim", _components(gazebo="waiting"))
    assert not waiting["ready"]
    assert waiting["waiting_for"] == ["gazebo"]
    assert not health.summary("real", _components(safety="waiting"))["ready"]


def test_motion_policy():
    assert health.motion_policy("sim", None, None)["allowed"]
    assert health.motion_policy("mock", None, None)["allowed"]
    assert not health.motion_policy("real", None, "HOMED")["allowed"]
    assert health.motion_policy("real", None, "ARMED")["allowed"]
    # remote: the workstation's agent decides (COCKPIT_REMOTE_MOTION there, ARMED on real)
    assert "no /cockpit/policy" in health.motion_policy("remote", None, None)["reason"]
    off = {"host": "ws-a", "mode": "sim", "remote_motion": False}
    assert "COCKPIT_REMOTE_MOTION=1" in health.motion_policy("remote", off, None)["reason"]
    on = {**off, "remote_motion": True}
    assert health.motion_policy("remote", on, None) == {
        "allowed": True,
        "reason": "allowed by ws-a",
    }
    real = {**on, "mode": "real", "arm": "HOMED"}
    assert not health.motion_policy("remote", real, None)["allowed"]
    assert health.motion_policy("remote", {**real, "arm": "ARMED"}, None)["allowed"]


@pytest.mark.parametrize(
    ("value", "flag"), [("1", True), ("true", True), ("0", False), (None, False)]
)
def test_env_flag(value, flag):
    assert health.env_flag(value) is flag
