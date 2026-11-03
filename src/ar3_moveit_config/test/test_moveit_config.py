"""Checks on the generated MoveIt configuration (no running nodes needed)."""

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

from ar3_moveit_config import build_moveit_config, default_config_dir

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"


@pytest.fixture(scope="module")
def config():
    return build_moveit_config(str(CONFIG_DIR))


@pytest.fixture(scope="module")
def srdf(config):
    return ET.fromstring(config.robot_description_semantic["robot_description_semantic"])


@pytest.fixture(scope="module")
def urdf_limits(config):
    urdf = ET.fromstring(config.robot_description["robot_description"])
    return {
        j.get("name"): (float(j.find("limit").get("lower")), float(j.find("limit").get("upper")))
        for j in urdf.findall("joint")
        if j.find("limit") is not None
    }


def test_named_poses_exist(srdf):
    states = {(s.get("group"), s.get("name")) for s in srdf.findall("group_state")}
    for name in ("home", "observe", "stow"):
        assert ("arm", name) in states
    assert {("gripper", "open"), ("gripper", "closed")} <= states


def test_named_poses_within_limits(srdf, urdf_limits):
    for state in srdf.findall("group_state"):
        for j in state.findall("joint"):
            lo, hi = urdf_limits[j.get("name")]
            assert lo <= float(j.get("value")) <= hi, f"{state.get('name')}: {j.get('name')}"


def test_arm_poses_define_all_joints(srdf):
    for state in srdf.findall("group_state[@group='arm']"):
        assert {j.get("name") for j in state.findall("joint")} == {
            f"joint_{i}" for i in range(1, 7)
        }


def test_bin_collision_pairs_follow_config(srdf):
    bins = [b["name"] for b in yaml.safe_load((CONFIG_DIR / "bins.yaml").read_text())["bins"]]
    pairs = {
        frozenset((d.get("link1"), d.get("link2"))) for d in srdf.findall("disable_collisions")
    }
    for b in bins:
        assert frozenset((f"bin_{b}", "table")) in pairs
    for i, a in enumerate(bins):
        for b in bins[i + 1 :]:
            assert frozenset((f"bin_{a}", f"bin_{b}")) in pairs


def test_workcell_collisions_stay_checked(srdf):
    # Moving links (beyond link_1, which only spins on top of the base) must
    # always be collision-checked against the table, bins and overhead camera.
    static = {"table", "cam_overhead_link"} | {
        f"bin_{b['name']}" for b in yaml.safe_load((CONFIG_DIR / "bins.yaml").read_text())["bins"]
    }
    allowed_partners = static | {"base_link", "link_1"}
    for d in srdf.findall("disable_collisions"):
        a, b = d.get("link1"), d.get("link2")
        if a in static or b in static:
            other = b if a in static else a
            assert other in allowed_partners, f"collision check disabled: {a} <-> {b}"


def test_scaling_comes_from_site_config(config):
    motion = yaml.safe_load((CONFIG_DIR / "robot.yaml").read_text())["motion"]
    planning = config.joint_limits["robot_description_planning"]
    assert planning["default_velocity_scaling_factor"] == pytest.approx(motion["velocity_scaling"])
    assert planning["default_acceleration_scaling_factor"] == pytest.approx(
        motion["acceleration_scaling"]
    )


def test_default_scaling_is_0_2():
    # SPEC §10: shipped default must stay 0.2.
    motion = yaml.safe_load((CONFIG_DIR / "robot.yaml").read_text())["motion"]
    assert motion == {"velocity_scaling": 0.2, "acceleration_scaling": 0.2}


def test_both_planning_pipelines(config):
    assert set(config.planning_pipelines["planning_pipelines"]) == {
        "ompl",
        "pilz_industrial_motion_planner",
    }


def test_default_config_dir_env(monkeypatch):
    monkeypatch.setenv("AR3_CONFIG_DIR", "/some/where")
    assert default_config_dir() == "/some/where"
