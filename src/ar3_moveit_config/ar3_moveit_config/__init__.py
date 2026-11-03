"""Shared MoveIt configuration for all AR3 launch modes.

Launch files call :func:`build_moveit_config` (inside an ``OpaqueFunction``,
since the site config directory is a launch argument) instead of each building
their own parameter dictionaries.
"""

import os
from pathlib import Path

import yaml
from ament_index_python.packages import PackageNotFoundError, get_package_share_directory
from launch_ros.actions import Node
from moveit_configs_utils import MoveItConfigs, MoveItConfigsBuilder

PLANNING_PIPELINES = ["ompl", "pilz_industrial_motion_planner"]


def default_config_dir() -> str:
    """Site config directory: ``$AR3_CONFIG_DIR``, else the copy installed by ar3_bringup."""
    if os.environ.get("AR3_CONFIG_DIR"):
        return os.environ["AR3_CONFIG_DIR"]
    try:
        return str(Path(get_package_share_directory("ar3_bringup")) / "config" / "site")
    except PackageNotFoundError as e:
        raise RuntimeError("Set AR3_CONFIG_DIR or pass config_dir:=<path to config/>") from e


def load_robot_config(config_dir: str) -> dict:
    with open(Path(config_dir) / "robot.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_moveit_config(
    config_dir: str, hardware: str = "none", controllers_file: str = ""
) -> MoveItConfigs:
    """Return the MoveIt configuration for the workcell described in ``config_dir``.

    ``hardware`` and ``controllers_file`` are forwarded to the URDF xacro so the
    same ``robot_description`` can be shared with ros2_control.
    """
    urdf = Path(get_package_share_directory("ar3_description")) / "urdf" / "ar3.urdf.xacro"
    urdf_mappings = {"config_dir": str(config_dir), "hardware": hardware}
    if controllers_file:
        urdf_mappings["controllers_file"] = controllers_file

    config = (
        MoveItConfigsBuilder("ar3", package_name="ar3_moveit_config")
        .robot_description(file_path=str(urdf), mappings=urdf_mappings)
        .robot_description_semantic(
            file_path="srdf/ar3.srdf.xacro", mappings={"config_dir": str(config_dir)}
        )
        .robot_description_kinematics(file_path="config/kinematics.yaml")
        .joint_limits(file_path="config/joint_limits.yaml")
        .pilz_cartesian_limits(file_path="config/pilz_cartesian_limits.yaml")
        .trajectory_execution(file_path="config/moveit_controllers.yaml")
        .planning_scene_monitor(
            publish_robot_description=True, publish_robot_description_semantic=True
        )
        .planning_pipelines(default_planning_pipeline="ompl", pipelines=PLANNING_PIPELINES)
        .to_moveit_configs()
    )

    # SPEC §10: default scaling comes from the site config, not from code.
    motion = load_robot_config(config_dir)["motion"]
    planning = config.joint_limits["robot_description_planning"]
    planning["default_velocity_scaling_factor"] = float(motion["velocity_scaling"])
    planning["default_acceleration_scaling_factor"] = float(motion["acceleration_scaling"])
    return config


def move_group_node(config: MoveItConfigs, use_sim_time: bool) -> Node:
    return Node(
        package="moveit_ros_move_group",
        executable="move_group",
        output="screen",
        parameters=[config.to_dict(), {"use_sim_time": use_sim_time}],
    )


def rviz_node(config: MoveItConfigs, use_sim_time: bool) -> Node:
    rviz_file = Path(get_package_share_directory("ar3_moveit_config")) / "rviz" / "moveit.rviz"
    return Node(
        package="rviz2",
        executable="rviz2",
        output="log",
        arguments=["-d", str(rviz_file)],
        parameters=[
            config.robot_description,
            config.robot_description_semantic,
            config.robot_description_kinematics,
            config.planning_pipelines,
            config.joint_limits,
            {"use_sim_time": use_sim_time},
        ],
    )
