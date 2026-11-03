"""move_group (+ optional RViz) for the AR3 workcell.

Normally included by ar3_bringup/bringup.launch.py; usable on its own when a
robot (mock, sim or real) is already running:

    ros2 launch ar3_moveit_config move_group.launch.py [rviz:=true] [use_sim_time:=false]
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration

from ar3_moveit_config import build_moveit_config, default_config_dir, move_group_node, rviz_node


def _setup(context):
    config_dir = LaunchConfiguration("config_dir").perform(context) or default_config_dir()
    use_sim_time = LaunchConfiguration("use_sim_time").perform(context).lower() == "true"
    config = build_moveit_config(config_dir)
    actions = [move_group_node(config, use_sim_time)]
    if LaunchConfiguration("rviz").perform(context).lower() == "true":
        actions.append(rviz_node(config, use_sim_time))
    return actions


def generate_launch_description():
    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "config_dir",
                default_value="",
                description="Site config directory (default: $AR3_CONFIG_DIR, else installed copy)",
            ),
            DeclareLaunchArgument("use_sim_time", default_value="false"),
            DeclareLaunchArgument("rviz", default_value="true", description="Start RViz"),
            OpaqueFunction(function=_setup),
        ]
    )
