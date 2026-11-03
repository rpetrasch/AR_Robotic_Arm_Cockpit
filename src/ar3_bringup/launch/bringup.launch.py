"""Bring up the AR3 workcell.

    ros2 launch ar3_bringup bringup.launch.py mode:=mock|sim|real [viz:=rviz|foxglove|none]
        [headless:=false] [use_software_rendering:=false] [render_engine:=ogre2|ogre]
        [cockpit_agent:=true]

mock - ros2_control with mock_components (no physics), MoveIt, RViz.
sim  - Gazebo Harmonic with gz_ros2_control and camera sensors, MoveIt, RViz.
real - real AR3; not available until Phase 1 (ar3_hardware) and refused here.

Visualization: ``viz:=rviz`` (default) starts RViz and foxglove_bridge, ``foxglove``
only the bridge (WebSocket, port 8765, for Lichtblick), ``none`` neither.
``headless:=true`` starts no window at all (no Gazebo GUI, no RViz); the bridge
still runs unless ``viz:=none``.

``cockpit_agent:=true`` (default) also starts ``ar3_cockpit_agent``, the AR3 Cockpit's HTTP
adapter (health, controllers, software stop). It reads its token, bind address and port
from the environment (``COCKPIT_AGENT_*``, see the package README), never from parameters.

Site configuration is read from ``config_dir`` (default ``$AR3_CONFIG_DIR``, else
the copy of the repository's ``config/`` installed with this package).
"""

import re
from pathlib import Path

from ament_index_python.packages import PackageNotFoundError, get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    IncludeLaunchDescription,
    OpaqueFunction,
    SetEnvironmentVariable,
)
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

from ar3_moveit_config import build_moveit_config, default_config_dir, move_group_node, rviz_node

CONTROLLERS = ["joint_state_broadcaster", "joint_trajectory_controller", "gripper_controller"]

# Lichtblick/Foxglove fetch only package:// assets through foxglove_bridge; file://
# URIs are read from the viewer's own disk. Our URDF uses file:// mesh paths (needed
# by Gazebo), so the bridge gets a copy with package:// URIs on /viz/robot_description.
_INSTALLED_SHARE = re.compile(r"file://[^\"']*?/share/([\w-]+)/")


def viz_description(urdf):
    """Return ``urdf`` with installed file://.../share/<pkg>/ paths as package://<pkg>/."""
    return _INSTALLED_SHARE.sub(r"package://\1/", urdf)


def _installed(package):
    try:
        get_package_share_directory(package)
    except PackageNotFoundError:
        print(f"[bringup] {package} is not built, so it does not start (run `make ws-build`).")
        return False
    return True


def _true(context, name):
    return LaunchConfiguration(name).perform(context).lower() == "true"


def _setup(context):
    mode = LaunchConfiguration("mode").perform(context)
    if mode == "real":
        raise RuntimeError(
            "mode:=real is not available yet: the AR3 hardware interface is delivered in "
            "Phase 1 (see docs/changelog/phase0.md). Use mode:=mock or mode:=sim."
        )
    config_dir = LaunchConfiguration("config_dir").perform(context) or default_config_dir()
    viz = LaunchConfiguration("viz").perform(context)
    headless = _true(context, "headless")
    use_rviz = viz == "rviz" and _true(context, "rviz") and not headless
    gz_gui = "false" if headless else LaunchConfiguration("gz_gui").perform(context)
    sim = mode == "sim"

    controllers_file = str(
        Path(get_package_share_directory("ar3_bringup")) / "config" / "ros2_controllers.yaml"
    )
    moveit = build_moveit_config(
        config_dir, hardware="gz" if sim else "mock", controllers_file=controllers_file
    )

    actions = []
    if _true(context, "use_software_rendering"):
        # Mesa llvmpipe for Gazebo sensors/GUI and RViz (no GPU, e.g. Docker on macOS).
        actions.append(SetEnvironmentVariable("LIBGL_ALWAYS_SOFTWARE", "1"))
    actions.append(
        Node(
            package="robot_state_publisher",
            executable="robot_state_publisher",
            output="both",
            parameters=[moveit.robot_description, {"use_sim_time": sim}],
        )
    )

    if sim:
        actions.append(
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(
                    str(
                        Path(get_package_share_directory("ar3_gazebo"))
                        / "launch"
                        / "gazebo.launch.py"
                    )
                ),
                launch_arguments={
                    "gui": gz_gui,
                    "render_engine": LaunchConfiguration("render_engine"),
                }.items(),
            )
        )
    else:
        actions.append(
            Node(
                package="controller_manager",
                executable="ros2_control_node",
                output="both",
                parameters=[controllers_file],
                remappings=[("~/robot_description", "/robot_description")],
            )
        )

    actions.append(
        Node(
            package="controller_manager",
            executable="spawner",
            output="screen",
            arguments=[
                *CONTROLLERS,
                "--controller-manager",
                "/controller_manager",
                "--controller-manager-timeout",
                "120",
                # The switch waits for the first update cycle; while Gazebo still loads its
                # camera sensors on a busy host that took longer than the default 5 s.
                "--switch-timeout",
                "60",
            ],
            parameters=[{"use_sim_time": sim}],
        )
    )

    actions.append(move_group_node(moveit, sim))
    if use_rviz:
        actions.append(rviz_node(moveit, sim))
    if viz in ("rviz", "foxglove"):
        urdf = moveit.robot_description["robot_description"]
        actions.append(
            # Publishes /viz/robot_description only; its TF outputs go to hidden
            # topics (not bridged) and it gets no joint states.
            Node(
                package="robot_state_publisher",
                executable="robot_state_publisher",
                name="robot_description_viz",
                namespace="viz",
                output="log",
                parameters=[{"robot_description": viz_description(urdf), "use_sim_time": sim}],
                remappings=[
                    ("/tf", "/viz/_tf"),
                    ("/tf_static", "/viz/_tf_static"),
                    ("joint_states", "/viz/_joint_states"),
                ],
            )
        )
        actions.append(
            Node(
                package="foxglove_bridge",
                executable="foxglove_bridge",
                output="screen",
                parameters=[
                    {
                        "port": int(LaunchConfiguration("foxglove_port").perform(context)),
                        "address": "0.0.0.0",
                        "use_sim_time": sim,
                    }
                ],
            )
        )
    if _true(context, "cockpit_agent") and _installed("ar3_cockpit_agent"):
        actions.append(
            Node(
                package="ar3_cockpit_agent",
                executable="cockpit_agent",
                output="both",
                parameters=[{"mode": mode, "config_dir": config_dir}],
            )
        )
    return actions


def generate_launch_description():
    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "mode",
                default_value="mock",
                choices=["mock", "sim", "real"],
                description="mock | sim | real (real requires Phase 1)",
            ),
            DeclareLaunchArgument(
                "viz",
                default_value="rviz",
                choices=["rviz", "foxglove", "none"],
                description="rviz = RViz + foxglove_bridge, foxglove = bridge only, none",
            ),
            DeclareLaunchArgument(
                "rviz", default_value="true", description="With viz:=rviz: start RViz"
            ),
            DeclareLaunchArgument(
                "gz_gui",
                default_value="true",
                description="sim only: start the Gazebo GUI (false = headless)",
            ),
            DeclareLaunchArgument(
                "headless",
                default_value="false",
                description="No windows: Gazebo server only, no RViz (bridge still per viz)",
            ),
            DeclareLaunchArgument(
                "use_software_rendering",
                default_value="false",
                description="Set LIBGL_ALWAYS_SOFTWARE=1 (Mesa llvmpipe, no GPU)",
            ),
            DeclareLaunchArgument(
                "render_engine",
                default_value="ogre2",
                choices=["ogre2", "ogre"],
                description="sim only: Gazebo render engine (ogre = fallback)",
            ),
            DeclareLaunchArgument(
                "foxglove_port", default_value="8765", description="foxglove_bridge port"
            ),
            DeclareLaunchArgument(
                "cockpit_agent",
                default_value="true",
                description="Start ar3_cockpit_agent (the AR3 Cockpit's adapter, port 8790)",
            ),
            DeclareLaunchArgument(
                "config_dir",
                default_value="",
                description="Site config directory (default: $AR3_CONFIG_DIR, else installed copy)",
            ),
            OpaqueFunction(function=_setup),
        ]
    )
