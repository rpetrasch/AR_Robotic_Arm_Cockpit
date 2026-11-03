"""Gazebo Harmonic: workcell world, robot spawn and ROS <-> gz bridges.

Included by ``ar3_bringup bringup.launch.py mode:=sim``, which also starts
robot_state_publisher (the spawn reads ``/robot_description``), the controller
spawners and MoveIt.
"""

import os
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, OpaqueFunction
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def _setup(context):
    share = Path(get_package_share_directory("ar3_gazebo"))
    world = LaunchConfiguration("world").perform(context) or str(share / "worlds" / "workcell.sdf")
    gui = LaunchConfiguration("gui").perform(context).lower() == "true"
    engine = LaunchConfiguration("render_engine").perform(context)
    if engine == "ogre" and not os.environ.get("DISPLAY"):
        # OGRE 1 renders through GLX only; there is no EGL/headless path.
        raise RuntimeError(
            "render_engine:=ogre needs an X display (DISPLAY is not set). "
            "Use the default ogre2, which renders headless with Mesa."
        )
    # -r: start running; -s --headless-rendering: server only, sensors still render (EGL).
    mode = "" if gui else "-s --headless-rendering "
    gz_args = f"-r {mode}--render-engine {engine} -v 2 {world}"

    return [
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                str(Path(get_package_share_directory("ros_gz_sim")) / "launch" / "gz_sim.launch.py")
            ),
            launch_arguments={"gz_args": gz_args, "on_exit_shutdown": "true"}.items(),
        ),
        Node(
            package="ros_gz_sim",
            executable="create",
            output="screen",
            arguments=["-name", "ar3", "-topic", "/robot_description"],
        ),
        Node(
            package="ros_gz_bridge",
            executable="parameter_bridge",
            output="screen",
            parameters=[
                {"config_file": str(share / "config" / "bridge.yaml"), "use_sim_time": True}
            ],
        ),
    ]


def generate_launch_description():
    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "gui",
                default_value="true",
                description="Start the Gazebo GUI (false = headless server)",
            ),
            DeclareLaunchArgument(
                "render_engine",
                default_value="ogre2",
                choices=["ogre2", "ogre"],
                description="Gazebo render engine for server and GUI (ogre = fallback for old GL)",
            ),
            DeclareLaunchArgument(
                "world",
                default_value="",
                description="SDF world file (default: worlds/workcell.sdf)",
            ),
            OpaqueFunction(function=_setup),
        ]
    )
