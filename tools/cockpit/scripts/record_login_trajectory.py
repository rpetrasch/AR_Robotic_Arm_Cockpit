#!/usr/bin/env python3
"""Record the login screen's motion (docs/specs/spec_cockpit.md §7.3) as
public/login/trajectory.json.

Plans and executes a named-pose sequence through MoveIt (move_group, any sim mode) and stores
the planned, time-parameterised trajectories back to back. The real arm can later replay the
same file for filming real.mp4; that needs ar3_hardware (SPEC Phase 1), so --real is refused.

Run inside the dev image with a stack up (make cockpit-login-assets does all of it):

    python3 tools/cockpit/scripts/record_login_trajectory.py --out <file> home observe stow home
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime

import rclpy
from moveit_msgs.action import MoveGroup
from moveit_msgs.msg import MoveItErrorCodes

from ar3_bringup.named_poses import NamedPoseClient, joint_goal

PAUSE_S = 0.8  # still time between two moves
SEQUENCE = [
    ("arm", "home"),
    ("gripper", "open"),
    ("arm", "observe"),
    ("gripper", "closed"),
    ("arm", "stow"),
    ("gripper", "open"),
    ("arm", "home"),
    ("gripper", "closed"),
]


def plan_and_execute(node: NamedPoseClient, group: str, name: str):
    """Like NamedPoseClient.move, but returns the planned JointTrajectory."""
    goal = MoveGroup.Goal()
    req = goal.request
    req.group_name = group
    req.goal_constraints = [joint_goal(node.named_states()[group][name])]
    req.num_planning_attempts = 5
    req.allowed_planning_time = 5.0
    # The site defaults (config/robot.yaml `motion:`), read from move_group by wait_ready().
    req.max_velocity_scaling_factor, req.max_acceleration_scaling_factor = node.scaling()
    goal.planning_options.plan_only = False
    send = node._action.send_goal_async(goal)
    rclpy.spin_until_future_complete(node, send, timeout_sec=60)
    handle = send.result()
    if handle is None or not handle.accepted:
        raise RuntimeError(f"{group}/{name}: goal rejected")
    result = handle.get_result_async()
    rclpy.spin_until_future_complete(node, result, timeout_sec=120)
    res = result.result().result
    if res.error_code.val != MoveItErrorCodes.SUCCESS:
        raise RuntimeError(f"{group}/{name}: MoveIt error {res.error_code.val}")
    return res.planned_trajectory.joint_trajectory


def build(segments, all_joints: list[str], start: dict[str, float]) -> list[dict]:
    """Back-to-back segments on one clock; joints a segment does not move hold their value."""
    current = dict(start)
    points, offset = [], 0.0
    for traj in segments:
        for p in traj.points:
            t = p.time_from_start.sec + p.time_from_start.nanosec * 1e-9
            current.update(zip(traj.joint_names, p.positions, strict=True))
            q = [round(current[j], 5) for j in all_joints]
            points.append({"t": round(offset + t, 4), "q": q})
        offset = points[-1]["t"] + PAUSE_S
        points.append({"t": round(offset, 4), "q": points[-1]["q"]})
    return points


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--real", action="store_true", help="execute on the real arm (Phase 1)")
    args = parser.parse_args(rclpy.utilities.remove_ros_args(sys.argv)[1:])
    if args.real:
        print("--real needs ar3_hardware (SPEC Phase 1); mode:=real is refused today.")
        return 2

    rclpy.init()
    node = NamedPoseClient("record_login_trajectory")
    try:
        if not node.wait_ready(timeout=120):
            print("move_group not available", file=sys.stderr)
            return 2
        states = node.named_states()
        all_joints = [*states["arm"]["home"], *states["gripper"]["closed"]]
        start = {**states["arm"]["home"], **states["gripper"]["closed"]}
        scaling = node.scaling()
        segments = [plan_and_execute(node, group, name) for group, name in SEQUENCE]
    finally:
        node.destroy_node()
        rclpy.try_shutdown()

    points = build(segments, all_joints, start)
    doc = {
        "version": 1,
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": "MoveIt plans (mock), scaling from config/robot.yaml",
        "scaling": dict(zip(("velocity", "acceleration"), scaling, strict=True)),
        "sequence": [f"{g}/{n}" for g, n in SEQUENCE],
        "duration_s": points[-1]["t"],
        "joint_names": all_joints,
        "points": points,
    }
    with open(args.out, "w") as fh:
        json.dump(doc, fh, separators=(",", ":"))
        fh.write("\n")
    print(f"{args.out}: {len(points)} points, {doc['duration_s']:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
