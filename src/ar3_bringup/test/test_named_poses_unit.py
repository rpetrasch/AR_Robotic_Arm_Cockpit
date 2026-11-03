"""Unit tests for the named-pose helpers (no ROS graph needed)."""

import os
from pathlib import Path

from ament_index_python.packages import get_package_share_directory

from ar3_bringup.named_poses import effective_scaling, joint_goal, parse_group_states

SRDF = """<robot name="r">
  <group_state name="home" group="arm">
    <joint name="a" value="0"/><joint name="b" value="0.5"/>
  </group_state>
  <group_state name="open" group="gripper"><joint name="g" value="0.014"/></group_state>
</robot>"""


def test_parse_group_states():
    assert parse_group_states(SRDF) == {
        "arm": {"home": {"a": 0.0, "b": 0.5}},
        "gripper": {"open": {"g": 0.014}},
    }


def test_joint_goal():
    c = joint_goal({"a": 0.1, "b": -0.2}, tolerance=0.01)
    assert [(j.joint_name, j.position) for j in c.joint_constraints] == [("a", 0.1), ("b", -0.2)]
    assert all(j.tolerance_above == j.tolerance_below == 0.01 for j in c.joint_constraints)


def test_site_config_installed():
    # bringup falls back to this copy when $AR3_CONFIG_DIR is unset.
    site = Path(get_package_share_directory("ar3_bringup")) / "config" / "site"
    for name in ("robot.yaml", "bins.yaml", "cameras.yaml"):
        assert os.path.isfile(site / name), name


def test_zero_scaling_uses_the_site_defaults():
    # move_group runs a goal with scaling 0 at 1.0, so the client must fill in the defaults.
    assert effective_scaling(0.0, (0.2, 0.3)) == (0.2, 0.3)
    assert effective_scaling(0.5, (0.2, 0.3)) == (0.5, 0.5)
