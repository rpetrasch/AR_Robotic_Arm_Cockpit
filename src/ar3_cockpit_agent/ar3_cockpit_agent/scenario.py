"""Pure helpers of the scenario runs (docs/specs/spec_cockpit.md §12.4), unit-tested without ROS:
frames, generated SDF for spawned shapes, SRDF named poses, step timeouts.

Scenario poses are in the ``table`` frame (centre of the table top, z up): ``{x, y, z,
yaw_deg}`` for objects, ``{x, y, z, rpy_deg}`` for TCP targets. Gazebo wants the world frame,
which is ``config/robot.yaml`` table ``xyz`` / ``rpy`` away.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET

PREFIX = "scn_"  # every planning-scene object and Gazebo model a run owns
DENSITY = 500.0  # kg/m³ for the inertia of spawned shapes (light wood / plastic)
COLORS = {
    "red": (0.8, 0.1, 0.1, 1.0),
    "green": (0.1, 0.7, 0.1, 1.0),
    "blue": (0.1, 0.2, 0.8, 1.0),
    "yellow": (0.9, 0.8, 0.1, 1.0),
    "orange": (0.9, 0.45, 0.1, 1.0),
    "white": (0.9, 0.9, 0.9, 1.0),
    "black": (0.1, 0.1, 0.1, 1.0),
    "grey": (0.5, 0.5, 0.5, 1.0),
}
STEP_TYPES = ("move_named", "move_joints", "move_pose", "gripper", "wait")
TASK_TYPES = ("sequence",)  # pick_place / sort: RunTask, SPEC Phase 3


class Aborted(Exception):
    pass


class StepFailed(Exception):
    pass


MIN_EXECUTION_TIMEOUT_S = 10.0


def model_name(object_id: str) -> str:
    return object_id if object_id.startswith(PREFIX) else PREFIX + object_id


def rpy_to_quat(roll: float, pitch: float, yaw: float) -> tuple[float, float, float, float]:
    """(x, y, z, w) of fixed-axis roll, pitch, yaw (as URDF / tf2)."""
    cr, sr = math.cos(roll / 2), math.sin(roll / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    return (
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
        cr * cp * cy + sr * sp * sy,
    )


def _rot(rpy) -> list[list[float]]:
    r, p, y = rpy
    cr, sr, cp, sp, cy, sy = (
        math.cos(r),
        math.sin(r),
        math.cos(p),
        math.sin(p),
        math.cos(y),
        math.sin(y),
    )
    return [
        [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
        [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
        [-sp, cp * sr, cp * cr],
    ]


def table_to_world(xyz, table_xyz, table_rpy=(0.0, 0.0, 0.0)) -> tuple[float, float, float]:
    rot = _rot(table_rpy)
    return tuple(table_xyz[i] + sum(rot[i][j] * xyz[j] for j in range(3)) for i in range(3))


def world_to_table(xyz, table_xyz, table_rpy=(0.0, 0.0, 0.0)) -> tuple[float, float, float]:
    rot = _rot(table_rpy)
    d = [xyz[i] - table_xyz[i] for i in range(3)]
    return tuple(sum(rot[j][i] * d[j] for j in range(3)) for i in range(3))  # Rᵀ d


def geometry(obj: dict) -> tuple[str, list[float]]:
    """(shape, dimensions) as shape_msgs/SolidPrimitive wants them: box [x y z], cylinder
    [height radius], sphere [radius]. Scenario sizes: box [x y z], cylinder [diameter height],
    sphere [diameter]."""
    shape, size = obj["shape"], [float(v) for v in obj["size"]]
    if shape == "box":
        return "box", size[:3]
    if shape == "cylinder":
        return "cylinder", [size[1], size[0] / 2]
    if shape == "sphere":
        return "sphere", [size[0] / 2]
    raise ValueError(f"{obj.get('id')}: shape {shape!r} cannot be spawned (box, cylinder, sphere)")


def half_height(obj: dict) -> float:
    """Half the height: an object resting on the table has its centre this high."""
    shape, dims = geometry(obj)
    if shape == "box":
        return dims[2] / 2
    if shape == "cylinder":
        return dims[0] / 2
    return dims[0]  # sphere: the radius


def inertia(obj: dict) -> tuple[float, float, float, float]:
    """(mass, ixx, iyy, izz) of a solid shape of DENSITY."""
    shape, d = geometry(obj)
    if shape == "box":
        x, y, z = d
        m = DENSITY * x * y * z
        return m, m * (y * y + z * z) / 12, m * (x * x + z * z) / 12, m * (x * x + y * y) / 12
    if shape == "cylinder":
        h, r = d
        m = DENSITY * math.pi * r * r * h
        side = m * (3 * r * r + h * h) / 12
        return m, side, side, m * r * r / 2
    (r,) = d
    m = DENSITY * 4 / 3 * math.pi * r**3
    i = 2 / 5 * m * r * r
    return m, i, i, i


def color(obj: dict) -> tuple[float, float, float, float]:
    c = obj.get("color", "grey")
    if isinstance(c, list | tuple):
        return tuple(float(v) for v in c) + ((1.0,) if len(c) == 3 else ())
    return COLORS.get(str(c), COLORS["grey"])


def sdf(obj: dict, static: bool = False) -> str:
    """An SDF model of a scenario object (pose set by the create request)."""
    shape, d = geometry(obj)
    if shape == "box":
        geo = f"<box><size>{d[0]} {d[1]} {d[2]}</size></box>"
    elif shape == "cylinder":
        geo = f"<cylinder><radius>{d[1]}</radius><length>{d[0]}</length></cylinder>"
    else:
        geo = f"<sphere><radius>{d[0]}</radius></sphere>"
    m, ixx, iyy, izz = inertia(obj)
    r, g, b, a = color(obj)
    rgba = f"{r} {g} {b} {a}"
    return (
        f'<?xml version="1.0"?><sdf version="1.9"><model name="{model_name(obj["id"])}">'
        f"<static>{'true' if static else 'false'}</static><link name=\"link\">"
        f"<inertial><mass>{m:.6g}</mass><inertia><ixx>{ixx:.6g}</ixx><iyy>{iyy:.6g}</iyy>"
        f"<izz>{izz:.6g}</izz><ixy>0</ixy><ixz>0</ixz><iyz>0</iyz></inertia></inertial>"
        f'<collision name="collision"><geometry>{geo}</geometry>'
        "<surface><friction><ode><mu>1.0</mu><mu2>1.0</mu2></ode></friction></surface></collision>"
        f'<visual name="visual"><geometry>{geo}</geometry><material><ambient>{rgba}</ambient>'
        f"<diffuse>{rgba}</diffuse></material></visual></link></model></sdf>"
    )


def object_pose(obj: dict) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
    """Table-frame position and orientation of an object (``pose.z`` is its centre)."""
    p = obj["pose"]
    yaw = math.radians(float(p.get("yaw_deg", 0.0)))
    return (float(p["x"]), float(p["y"]), float(p.get("z", half_height(obj)))), rpy_to_quat(
        0.0, 0.0, yaw
    )


def target_pose(step: dict) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
    """A move_pose target: position and orientation in its frame (default table)."""
    rpy = [math.radians(float(v)) for v in step.get("rpy_deg", [180.0, 0.0, 0.0])]
    return (float(step["x"]), float(step["y"]), float(step["z"])), rpy_to_quat(*rpy)


def parse_group_states(srdf_xml: str) -> dict[str, dict[str, dict[str, float]]]:
    """``{group: {state: {joint: value}}}`` from the SRDF (as ar3_bringup.named_poses)."""
    states: dict[str, dict[str, dict[str, float]]] = {}
    for gs in ET.fromstring(srdf_xml).findall("group_state"):
        joints = {j.get("name"): float(j.get("value")) for j in gs.findall("joint")}
        states.setdefault(gs.get("group"), {})[gs.get("name")] = joints
    return states


def step_kind(step: dict) -> str:
    kinds = [k for k in STEP_TYPES if k in step]
    if len(kinds) != 1:
        raise ValueError(f"a step needs exactly one of {', '.join(STEP_TYPES)}: {step}")
    return kinds[0]


def execution_timeout(trajectory_s: float) -> float:
    """§12.4: execution estimate × 3 (the planned duration), at least MIN_EXECUTION_TIMEOUT_S."""
    return max(MIN_EXECUTION_TIMEOUT_S, 3.0 * trajectory_s)


def describe(step: dict) -> str:
    kind = step_kind(step)
    v = step[kind]
    if kind == "move_named":
        return f"move_named {v}"
    if kind == "gripper":
        return f"gripper {v}"
    if kind == "wait":
        return f"wait {v} s"
    if kind == "move_joints":
        return "move_joints [" + ", ".join(f"{float(x):.3f}" for x in v) + "]"
    planner = v.get("planner", "ompl")
    return f"move_pose ({v['x']}, {v['y']}, {v['z']}) {planner}"


def parse_model_list(text: str) -> list[str]:
    """Names from ``gz model --list`` ("    - red_cube" lines after "Available models:")."""
    out, on = [], False
    for line in text.splitlines():
        if line.strip().startswith("Available models"):
            on = True
        elif on and line.strip().startswith("- "):
            out.append(line.strip()[2:].strip())
    return out


def parse_model_pose(text: str):
    """((x, y, z), quaternion) from ``gz model -m <name> -p`` (two bracketed lines: XYZ, RPY)."""
    import re

    rows = re.findall(r"^\s*\[\s*(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s*\]\s*$", text, re.M)
    if len(rows) < 2:
        return None
    xyz = tuple(float(v) for v in rows[0])
    return xyz, rpy_to_quat(*(float(v) for v in rows[1]))
