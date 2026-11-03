"""Helpers shared by the mock (ar3_bringup) and sim (ar3_gazebo) integration tests."""

import rclpy
from moveit_msgs.msg import MoveItErrorCodes
from sensor_msgs.msg import JointState

from ar3_bringup.named_poses import NamedPoseClient

ARM_SEQUENCE = ["observe", "stow", "home"]
GRIPPER_SEQUENCE = ["open", "closed"]


def latest_joint_state(node: NamedPoseClient, timeout: float = 5.0) -> dict[str, float]:
    received: list[JointState] = []
    sub = node.create_subscription(JointState, "/joint_states", received.append, 10)
    try:
        end = node.get_clock().now().nanoseconds + int(timeout * 1e9)
        # Drain a few messages so we look at state after the motion finished.
        while len(received) < 3 and node.get_clock().now().nanoseconds < end:
            rclpy.spin_once(node, timeout_sec=0.1)
    finally:
        node.destroy_subscription(sub)
    if not received:
        raise AssertionError("no /joint_states received")
    return dict(zip(received[-1].name, received[-1].position, strict=True))


def check_named_poses(node: NamedPoseClient, tolerance: float) -> list[str]:
    """Move through every named pose; return a list of failures (empty = pass)."""
    failures = []
    states = node.named_states()
    for group, sequence in (("arm", ARM_SEQUENCE), ("gripper", GRIPPER_SEQUENCE)):
        for name in sequence:
            code = node.move(name, group)
            if code != MoveItErrorCodes.SUCCESS:
                failures.append(f"{group}/{name}: MoveIt error {code}")
                continue
            actual = latest_joint_state(node)
            for joint, target in states[group][name].items():
                if abs(actual[joint] - target) > tolerance:
                    failures.append(
                        f"{group}/{name}: {joint} at {actual[joint]:.4f}, expected {target:.4f}"
                    )
    return failures
