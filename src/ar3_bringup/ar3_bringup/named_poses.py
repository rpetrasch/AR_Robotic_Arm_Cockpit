"""Plan and execute moves to SRDF named poses through MoveIt's move_group action.

Command line (after sourcing the workspace):

    ros2 run ar3_bringup move_to_named observe stow home
    ros2 run ar3_bringup move_to_named --group gripper open closed
"""

import argparse
import sys
import time
import xml.etree.ElementTree as ET

import rclpy
from controller_manager_msgs.srv import ListControllers
from moveit_msgs.action import MoveGroup
from moveit_msgs.msg import Constraints, JointConstraint, MoveItErrorCodes
from rcl_interfaces.srv import GetParameters
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

REQUIRED_CONTROLLERS = ("joint_trajectory_controller", "gripper_controller")
# move_group's copy of config/robot.yaml `motion:` (set by ar3_moveit_config.build_moveit_config).
# A goal with scaling 0 is NOT given these by move_group: it runs at 1.0 (full speed).
SCALING_PARAMS = (
    "robot_description_planning.default_velocity_scaling_factor",
    "robot_description_planning.default_acceleration_scaling_factor",
)


def effective_scaling(requested: float, defaults: tuple[float, float]) -> tuple[float, float]:
    """(velocity, acceleration) scaling for a goal: ``requested`` for both if it is > 0, else
    the site defaults read from move_group."""
    if requested > 0.0:
        return requested, requested
    return defaults


def parse_group_states(srdf_xml: str) -> dict[str, dict[str, dict[str, float]]]:
    """Return ``{group: {state_name: {joint: value}}}`` from an SRDF string."""
    states: dict[str, dict[str, dict[str, float]]] = {}
    for gs in ET.fromstring(srdf_xml).findall("group_state"):
        joints = {j.get("name"): float(j.get("value")) for j in gs.findall("joint")}
        states.setdefault(gs.get("group"), {})[gs.get("name")] = joints
    return states


def joint_goal(joints: dict[str, float], tolerance: float = 1e-3) -> Constraints:
    c = Constraints()
    for name, value in joints.items():
        c.joint_constraints.append(
            JointConstraint(
                joint_name=name,
                position=value,
                tolerance_above=tolerance,
                tolerance_below=tolerance,
                weight=1.0,
            )
        )
    return c


class NamedPoseClient(Node):
    """Small client around ``/move_action`` that knows the SRDF named poses."""

    def __init__(self, node_name: str = "move_to_named"):
        super().__init__(node_name)
        self.declare_parameter("planning_time", 5.0, _desc("Planning time per attempt [s]"))
        self.declare_parameter("planning_attempts", 5, _desc("Planning attempts"))
        self.declare_parameter(
            "velocity_scaling",
            0.0,
            _desc("Velocity scaling (0 = MoveIt default from config/robot.yaml)"),
        )
        self._srdf: str | None = None
        qos = QoSProfile(
            depth=1,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            reliability=ReliabilityPolicy.RELIABLE,
        )
        self.create_subscription(String, "/robot_description_semantic", self._on_srdf, qos)
        self._action = ActionClient(self, MoveGroup, "/move_action")
        self._list_controllers = self.create_client(
            ListControllers, "/controller_manager/list_controllers"
        )
        self._get_params = self.create_client(GetParameters, "/move_group/get_parameters")
        self._default_scaling: tuple[float, float] | None = None

    def _on_srdf(self, msg: String) -> None:
        self._srdf = msg.data

    def wait_ready(self, timeout: float = 60.0) -> bool:
        """Wait for the SRDF, the move_group action, its default scaling and active
        controllers."""
        deadline = time.monotonic() + timeout
        while self._srdf is None and time.monotonic() < deadline:
            rclpy.spin_once(self, timeout_sec=0.1)
        if self._srdf is None:
            return False
        if not self._action.wait_for_server(timeout_sec=max(0.0, deadline - time.monotonic())):
            return False
        self._default_scaling = self._read_default_scaling()
        if self._default_scaling is None:
            self.get_logger().error(f"move_group has no {SCALING_PARAMS[0]}")
            return False
        while time.monotonic() < deadline:
            if self._controllers_active():
                return True
            time.sleep(0.5)
        return False

    def _controllers_active(self) -> bool:
        if not self._list_controllers.wait_for_service(timeout_sec=1.0):
            return False
        future = self._list_controllers.call_async(ListControllers.Request())
        rclpy.spin_until_future_complete(self, future, timeout_sec=2.0)
        if future.result() is None:
            return False
        active = {c.name for c in future.result().controller if c.state == "active"}
        return all(c in active for c in REQUIRED_CONTROLLERS)

    def _read_default_scaling(self) -> tuple[float, float] | None:
        if not self._get_params.wait_for_service(timeout_sec=5.0):
            return None
        future = self._get_params.call_async(GetParameters.Request(names=list(SCALING_PARAMS)))
        rclpy.spin_until_future_complete(self, future, timeout_sec=5.0)
        values = future.result().values if future.result() else []
        doubles = [v.double_value for v in values if v.type == 3]  # PARAMETER_DOUBLE
        return (doubles[0], doubles[1]) if len(doubles) == 2 and all(doubles) else None

    def scaling(self) -> tuple[float, float]:
        """(velocity, acceleration) scaling the next goal uses."""
        if self._default_scaling is None:
            raise RuntimeError("default scaling not read yet; call wait_ready() first")
        requested = self.get_parameter("velocity_scaling").value
        return effective_scaling(requested, self._default_scaling)

    def named_states(self) -> dict[str, dict[str, dict[str, float]]]:
        if self._srdf is None:
            raise RuntimeError("SRDF not received yet; call wait_ready() first")
        return parse_group_states(self._srdf)

    def move(self, name: str, group: str = "arm", timeout: float = 120.0) -> int:
        """Plan and execute to ``name``. Returns the MoveIt error code (1 = SUCCESS)."""
        states = self.named_states().get(group, {})
        if name not in states:
            raise KeyError(f"unknown pose '{name}' for group '{group}' (have: {sorted(states)})")

        goal = MoveGroup.Goal()
        req = goal.request
        req.group_name = group
        req.goal_constraints = [joint_goal(states[name])]
        req.num_planning_attempts = self.get_parameter("planning_attempts").value
        req.allowed_planning_time = self.get_parameter("planning_time").value
        req.max_velocity_scaling_factor, req.max_acceleration_scaling_factor = self.scaling()
        goal.planning_options.plan_only = False

        send = self._action.send_goal_async(goal)
        rclpy.spin_until_future_complete(self, send, timeout_sec=timeout)
        handle = send.result()
        if handle is None or not handle.accepted:
            self.get_logger().error(f"{group}/{name}: goal rejected")
            return MoveItErrorCodes.FAILURE
        result = handle.get_result_async()
        rclpy.spin_until_future_complete(self, result, timeout_sec=timeout)
        if result.result() is None:
            self.get_logger().error(f"{group}/{name}: timed out after {timeout} s")
            handle.cancel_goal_async()
            return MoveItErrorCodes.TIMED_OUT
        code = result.result().result.error_code.val
        if code == MoveItErrorCodes.SUCCESS:
            self.get_logger().info(f"{group}/{name}: done")
        else:
            self.get_logger().error(f"{group}/{name}: MoveIt error code {code}")
        return code


def _desc(text: str):
    from rcl_interfaces.msg import ParameterDescriptor

    return ParameterDescriptor(description=text)


def main(argv=None) -> int:
    argv = rclpy.utilities.remove_ros_args(sys.argv if argv is None else argv)[1:]
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("poses", nargs="+", help="SRDF group_state names, executed in order")
    parser.add_argument("--group", default="arm", help="planning group (arm|gripper)")
    args = parser.parse_args(argv)

    rclpy.init()
    node = NamedPoseClient()
    try:
        if not node.wait_ready():
            node.get_logger().error("move_group not available")
            return 2
        for pose in args.poses:
            try:
                code = node.move(pose, args.group)
            except KeyError as e:
                node.get_logger().error(str(e.args[0]))
                return 1
            if code != MoveItErrorCodes.SUCCESS:
                return 1
        return 0
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    sys.exit(main())
