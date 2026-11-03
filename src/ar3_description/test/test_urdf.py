"""Static checks on the AR3 URDF generated from xacro and the site config."""

import math
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest
import xacro
import yaml
from ament_index_python.packages import get_package_share_directory

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"
XACRO_FILE = Path(get_package_share_directory("ar3_description")) / "urdf" / "ar3.urdf.xacro"
KINEMATICS = Path(get_package_share_directory("ar3_description")) / "config" / "ar3_kinematics.yaml"


def build(**mappings) -> ET.Element:
    mappings.setdefault("config_dir", str(CONFIG_DIR))
    doc = xacro.process_file(str(XACRO_FILE), mappings=mappings)
    return ET.fromstring(doc.toxml())


def load(name):
    return yaml.safe_load((CONFIG_DIR / name).read_text())


def _rpy_matrix(r, p, y):
    cr, sr = math.cos(r), math.sin(r)
    cp, sp = math.cos(p), math.sin(p)
    cy, sy = math.cos(y), math.sin(y)
    return np.array(
        [
            [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
            [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
            [-sp, cp * sr, cp * cr],
        ]
    )


def _axis_angle(axis, angle):
    axis = np.asarray(axis, dtype=float)
    axis /= np.linalg.norm(axis)
    k = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    return np.eye(3) + math.sin(angle) * k + (1 - math.cos(angle)) * k @ k


class Kinematics:
    """Minimal forward kinematics over the URDF joint tree."""

    def __init__(self, robot: ET.Element):
        self.by_child = {}
        for j in robot.findall("joint"):
            origin = j.find("origin")
            xyz = [
                float(v)
                for v in (origin.get("xyz", "0 0 0") if origin is not None else "0 0 0").split()
            ]
            rpy = [
                float(v)
                for v in (origin.get("rpy", "0 0 0") if origin is not None else "0 0 0").split()
            ]
            axis = j.find("axis")
            self.by_child[j.find("child").get("link")] = {
                "name": j.get("name"),
                "type": j.get("type"),
                "parent": j.find("parent").get("link"),
                "xyz": np.array(xyz),
                "rot": _rpy_matrix(*rpy),
                "axis": [float(v) for v in axis.get("xyz").split()] if axis is not None else None,
            }

    def chain(self, link):
        out = [link]
        while link in self.by_child:
            link = self.by_child[link]["parent"]
            out.append(link)
        return out

    def pose(self, link, q=None):
        q = q or {}
        t = np.eye(4)
        for child in reversed(self.chain(link)[:-1]):
            j = self.by_child[child]
            step = np.eye(4)
            step[:3, :3] = j["rot"]
            step[:3, 3] = j["xyz"]
            if j["type"] in ("revolute", "continuous"):
                motion = np.eye(4)
                motion[:3, :3] = _axis_angle(j["axis"], q.get(j["name"], 0.0))
                step = step @ motion
            t = t @ step
        return t


@pytest.fixture(scope="module")
def robot():
    return build(hardware="mock")


@pytest.fixture(scope="module")
def kin(robot):
    return Kinematics(robot)


def test_tf_chain(kin):
    assert kin.chain("gripper_tcp") == [
        "gripper_tcp",
        "tool0",
        "link_6",
        "link_5",
        "link_4",
        "link_3",
        "link_2",
        "link_1",
        "base_link",
        "table",
        "world",
    ]
    assert kin.chain("cam_overhead_optical")[-1] == "world"
    assert kin.chain("cam_wrist_optical")[:4] == [
        "cam_wrist_optical",
        "cam_wrist_link",
        "tool0",
        "link_6",
    ]


def test_bins_from_config(kin):
    bins = load("bins.yaml")["bins"]
    assert bins, "bins.yaml defines no bins"
    table = load("robot.yaml")["table"]
    for b in bins:
        frame = f"bin_{b['name']}"
        assert kin.chain(frame)[:2] == [frame, "table"]
        # Bin must sit on the table top and inside its footprint.
        assert b["xyz"][2] == pytest.approx(0.0)
        for i in (0, 1):
            assert abs(b["xyz"][i]) + max(b["size"][:2]) / 2 <= table["size"][i] / 2


def test_joint_limits_follow_config(robot):
    limits = load("robot.yaml")["joint_limits"]
    joints = {j.get("name"): j for j in robot.findall("joint")}
    for name, (lo, hi) in limits.items():
        lim = joints[name].find("limit")
        assert float(lim.get("lower")) == pytest.approx(math.radians(lo))
        assert float(lim.get("upper")) == pytest.approx(math.radians(hi))


def test_link_lengths_follow_kinematics_file(kin):
    k = yaml.safe_load(KINEMATICS.read_text())
    origin = {
        n: kin.pose(n)[:3, 3] for n in ("link_2", "link_3", "link_5", "link_6", "gripper_tcp")
    }
    assert np.linalg.norm(origin["link_3"] - origin["link_2"]) == pytest.approx(
        math.hypot(k["a2"], k["a2_z"]), abs=1e-6
    )
    assert np.linalg.norm(origin["link_5"] - origin["link_3"]) == pytest.approx(k["d4"], abs=1e-6)
    assert np.linalg.norm(origin["link_6"] - origin["link_5"]) == pytest.approx(k["d6"], abs=1e-6)
    assert np.linalg.norm(origin["gripper_tcp"] - origin["link_6"]) == pytest.approx(
        0.055, abs=1e-6
    )


def test_joint_1_rotates_about_vertical(kin):
    # Rotating joint 1 must keep the TCP height and its distance from the base axis.
    base = kin.pose("base_link")[:3, 3]
    p0 = kin.pose("gripper_tcp")[:3, 3]
    p1 = kin.pose("gripper_tcp", {"joint_1": 0.7})[:3, 3]
    assert p1[2] == pytest.approx(p0[2], abs=1e-9)
    assert np.linalg.norm((p1 - base)[:2]) == pytest.approx(
        np.linalg.norm((p0 - base)[:2]), abs=1e-9
    )


def test_overhead_camera_looks_down(kin):
    optical_z = kin.pose("cam_overhead_optical")[:3, 2]
    assert optical_z == pytest.approx([0.0, 0.0, -1.0], abs=1e-3)


def test_mock_hardware(robot):
    systems = robot.findall("ros2_control")
    assert {s.get("name") for s in systems} == {"ar3_arm", "ar3_gripper"}
    for s in systems:
        assert s.find("hardware/plugin").text == "mock_components/GenericSystem"
    assert robot.find("gazebo") is None


def test_gz_hardware():
    robot = build(hardware="gz", controllers_file="/tmp/controllers.yaml")
    for s in robot.findall("ros2_control"):
        assert s.find("hardware/plugin").text == "gz_ros2_control/GazeboSimSystem"
    sensors = {s.get("name") for s in robot.iter("sensor")}
    assert sensors == {"cam_overhead", "cam_wrist"}
    params = [p.text for p in robot.iter("parameters")]
    assert params == ["/tmp/controllers.yaml"]


def test_description_only():
    assert build().find("ros2_control") is None


def test_rejects_unknown_hardware():
    with pytest.raises(Exception, match="unsupported hardware"):
        build(hardware="real")


def test_requires_config_dir():
    with pytest.raises(Exception, match="config_dir"):
        build(config_dir="")
