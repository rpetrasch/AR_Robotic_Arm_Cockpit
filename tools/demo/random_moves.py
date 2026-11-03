#!/usr/bin/env python3
"""Random collision-free arm moves for the demo videos (tools/demo/README.md).

Runs inside the ROS container of the running stack, with the workspace sourced:

    python3 /ar3_ws/tools/demo/random_moves.py --count 3 --seed 7 [--home]

Each move is a random joint goal around the `home` pose (within `--spread` of each joint's
range, clipped to config/robot.yaml), planned and executed by MoveIt through move_group, so
RViz, Lichtblick and the cockpit show a real plan and a real motion. A goal MoveIt rejects
(in collision, no plan) is replaced by a new sample.
"""

import argparse
import math
import random
import sys
from pathlib import Path

import rclpy
import yaml
from moveit_msgs.action import MoveGroup
from moveit_msgs.msg import MoveItErrorCodes

from ar3_bringup.named_poses import NamedPoseClient, joint_goal

ROBOT_YAML = Path("/ar3_ws/config/robot.yaml")


class RandomMover(NamedPoseClient):
    def move_joints(self, joints: dict[str, float], timeout: float = 120.0) -> int:
        goal = MoveGroup.Goal()
        req = goal.request
        req.group_name = "arm"
        req.goal_constraints = [joint_goal(joints)]
        req.num_planning_attempts = self.get_parameter("planning_attempts").value
        req.allowed_planning_time = self.get_parameter("planning_time").value
        req.max_velocity_scaling_factor, req.max_acceleration_scaling_factor = self.scaling()
        send = self._action.send_goal_async(goal)
        rclpy.spin_until_future_complete(self, send, timeout_sec=timeout)
        handle = send.result()
        if handle is None or not handle.accepted:
            return MoveItErrorCodes.FAILURE
        result = handle.get_result_async()
        rclpy.spin_until_future_complete(self, result, timeout_sec=timeout)
        if result.result() is None:
            handle.cancel_goal_async()
            return MoveItErrorCodes.TIMED_OUT
        return result.result().result.error_code.val


def limits() -> dict[str, tuple[float, float]]:
    data = yaml.safe_load(ROBOT_YAML.read_text())
    return {j: (math.radians(lo), math.radians(hi)) for j, (lo, hi) in data["joint_limits"].items()}


def sample(
    home: dict[str, float], lim: dict[str, tuple[float, float]], spread: float, rnd: random.Random
):
    goal = {}
    for joint, q0 in home.items():
        lo, hi = lim.get(joint, (q0 - math.pi, q0 + math.pi))
        half = spread * (hi - lo) / 2
        goal[joint] = min(hi, max(lo, q0 + rnd.uniform(-half, half)))
    return goal


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--count", type=int, default=3, help="number of random moves")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--spread", type=float, default=0.45, help="fraction of each joint range")
    ap.add_argument("--home", action="store_true", help="end at the named pose home")
    args = ap.parse_args(rclpy.utilities.remove_ros_args(sys.argv)[1:])

    rclpy.init()
    node = RandomMover("demo_random_moves")
    try:
        if not node.wait_ready():
            node.get_logger().error("move_group not available")
            return 2
        home = node.named_states()["arm"]["home"]
        lim, rnd = limits(), random.Random(args.seed)
        done = tries = 0
        while done < args.count and tries < args.count * 8:
            tries += 1
            goal = sample(home, lim, args.spread, rnd)
            code = node.move_joints(goal)
            pretty = ", ".join(f"{math.degrees(v):.0f}" for v in goal.values())
            if code == MoveItErrorCodes.SUCCESS:
                done += 1
                node.get_logger().info(f"random move {done}/{args.count}: [{pretty}] deg")
            else:
                node.get_logger().info(f"sample rejected (MoveIt code {code}), trying another")
        if args.home and node.move("home") != MoveItErrorCodes.SUCCESS:
            return 1
        return 0 if done == args.count else 1
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    sys.exit(main())
