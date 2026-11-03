"""What scenarios are checked against: the site's facts from config/ and the robot description
(docs/specs/spec_cockpit.md §12.3 validation). Nothing here is a constant of the cockpit.

- table size and mount pose: ``config/robot.yaml``;
- bins: ``config/bins.yaml`` (frames ``bin_<name>`` under ``table``);
- named poses: the SRDF group states (``ar3_moveit_config/srdf/ar3.srdf.xacro``);
- joint limits: ``config/robot.yaml`` ``joint_limits``; gripper range: the URDF;
- the reach ring of the scene editor: the URDF's link offsets from joint_2 to gripper_tcp
  summed, around joint_1's axis (an approximation, shown as such);
- Gazebo worlds: ``ar3_gazebo/worlds/*.sdf``.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from functools import cache
from pathlib import Path

import yaml
from django.conf import settings

SRDF = "src/ar3_moveit_config/srdf/ar3.srdf.xacro"
URDF = (
    "tools/cockpit/frontend/public/login/ar3.urdf"  # the expanded URDF (make cockpit-login-assets)
)
WORLDS = "src/ar3_gazebo/worlds"
REACH_CHAIN = ("joint_3", "joint_5", "joint_6", "gripper_tcp_joint")


def repo() -> Path:
    return Path(settings.REPO_DIR)


def _yaml(rel: str) -> dict:
    return yaml.safe_load((repo() / rel).read_text()) or {}


@cache
def _named(path: str, mtime: float) -> dict[str, dict[str, dict[str, float]]]:
    states: dict[str, dict[str, dict[str, float]]] = {}
    root = ET.fromstring(Path(path).read_text())
    for gs in root.iter("group_state"):
        joints = {j.get("name"): float(j.get("value")) for j in gs.findall("joint")}
        states.setdefault(gs.get("group"), {})[gs.get("name")] = joints
    return states


def named_poses() -> dict[str, dict[str, dict[str, float]]]:
    path = repo() / SRDF
    return _named(str(path), path.stat().st_mtime)


def _urdf() -> ET.Element:
    return ET.parse(repo() / URDF).getroot()


def gripper_range() -> tuple[float, float]:
    for j in _urdf().findall("joint"):
        if j.get("name") == "gripper_jaw1_joint":
            lim = j.find("limit")
            return float(lim.get("lower")), float(lim.get("upper"))
    return 0.0, 0.0


def reach() -> dict:
    """An approximate reach ring: joint_2's distance from joint_1's axis plus the summed link
    offsets out to the TCP. Approximate: it ignores joint limits and the table."""
    joints = {j.get("name"): j for j in _urdf().findall("joint")}

    def xyz(name: str) -> list[float]:
        o = joints[name].find("origin")
        return [float(v) for v in (o.get("xyz") if o is not None else "0 0 0").split()]

    shoulder = xyz("joint_2")
    radius = math.hypot(shoulder[0], shoulder[1]) + sum(
        math.sqrt(sum(v * v for v in xyz(n))) for n in REACH_CHAIN if n in joints
    )
    return {"radius_m": round(radius, 3), "source": f"{URDF} (approximate)"}


def site() -> dict:
    """Everything the editor and the validation need, in the table frame."""
    robot = _yaml("config/robot.yaml")
    bins = _yaml("config/bins.yaml").get("bins", [])
    worlds = sorted(p.stem for p in (repo() / WORLDS).glob("*.sdf"))
    return {
        "table": {
            "size": robot["table"]["size"],
            "xyz": robot["table"]["xyz"],
            "rpy": robot["table"].get("rpy", [0, 0, 0]),
        },  # fmt: skip
        "mount": robot.get("mount", {"xyz": [0, 0, 0], "rpy": [0, 0, 0]}),
        "bins": [{**b, "name": f"bin_{b['name']}", "ref": "config/bins.yaml"} for b in bins],
        "named_poses": named_poses(),
        "joint_limits_deg": robot.get("joint_limits", {}),
        "gripper_range_m": list(gripper_range()),
        "motion": robot.get("motion", {}),
        "reach": reach(),
        "worlds": worlds,
        "sources": {"table": "config/robot.yaml", "bins": "config/bins.yaml", "poses": SRDF},
    }


def bins_by_name() -> dict[str, dict]:
    return {b["name"]: b for b in site()["bins"]}
