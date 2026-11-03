"""Integration test: headless Gazebo bringup.

Phase 0 acceptance (SPEC §7) for mode:=sim: camera topics publish and MoveIt
plans and executes to every named pose. Needs rendering (EGL) and a few minutes,
so it is not part of CI; run with `make test-sim`.
"""

import os
import time
import unittest
from pathlib import Path

import launch_testing
import launch_testing.actions
import pytest
import rclpy
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CameraInfo, Image

from ar3_bringup.named_poses import NamedPoseClient
from ar3_bringup.testing import check_named_poses

CONFIG_DIR = os.environ.get("AR3_CONFIG_DIR") or str(
    Path(get_package_share_directory("ar3_bringup")) / "config" / "site"
)
CAMERA_TOPICS = [
    ("/cam_overhead/image_raw", Image),
    ("/cam_overhead/camera_info", CameraInfo),
    ("/cam_wrist/image_raw", Image),
    ("/cam_wrist/camera_info", CameraInfo),
]


@pytest.mark.launch_test
def generate_test_description():
    bringup = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(get_package_share_directory("ar3_bringup"), "launch", "bringup.launch.py")
        ),
        launch_arguments={
            "mode": "sim",
            "rviz": "false",
            "gz_gui": "false",
            "config_dir": CONFIG_DIR,
        }.items(),
    )
    return LaunchDescription([bringup, launch_testing.actions.ReadyToTest()])


class TestSim(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init()
        cls.node = NamedPoseClient("test_named_poses_sim")
        cls.node.set_parameters([rclpy.parameter.Parameter("use_sim_time", value=True)])

    @classmethod
    def tearDownClass(cls):
        cls.node.destroy_node()
        rclpy.shutdown()

    def test_1_camera_topics_publish(self):
        seen = {}
        subs = [
            self.node.create_subscription(
                msg_type, topic, lambda m, t=topic: seen.setdefault(t, m), qos_profile_sensor_data
            )
            for topic, msg_type in CAMERA_TOPICS
        ]
        deadline = time.monotonic() + 180.0
        while len(seen) < len(CAMERA_TOPICS) and time.monotonic() < deadline:
            rclpy.spin_once(self.node, timeout_sec=0.1)
        for s in subs:
            self.node.destroy_subscription(s)
        self.assertEqual(sorted(seen), sorted(t for t, _ in CAMERA_TOPICS))
        image = seen["/cam_overhead/image_raw"]
        self.assertEqual((image.width, image.height), (1280, 720))
        self.assertEqual(image.header.frame_id, "cam_overhead_optical")

    def test_2_named_poses(self):
        self.assertTrue(self.node.wait_ready(timeout=180.0), "move_group did not come up")
        self.assertEqual(check_named_poses(self.node, tolerance=0.03), [])
