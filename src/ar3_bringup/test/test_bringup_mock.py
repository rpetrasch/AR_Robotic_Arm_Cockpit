"""Integration test: mock bringup, MoveIt plans and executes to every named pose.

Phase 0 acceptance (SPEC §7) for mode:=mock. Runs in CI.
"""

import os
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

from ar3_bringup.named_poses import NamedPoseClient
from ar3_bringup.testing import check_named_poses

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"


@pytest.mark.launch_test
def generate_test_description():
    bringup = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(get_package_share_directory("ar3_bringup"), "launch", "bringup.launch.py")
        ),
        launch_arguments={"mode": "mock", "rviz": "false", "config_dir": str(CONFIG_DIR)}.items(),
    )
    return LaunchDescription([bringup, launch_testing.actions.ReadyToTest()])


class TestNamedPosesMock(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init()
        cls.node = NamedPoseClient("test_named_poses_mock")

    @classmethod
    def tearDownClass(cls):
        cls.node.destroy_node()
        rclpy.shutdown()

    def test_named_poses(self):
        self.assertTrue(self.node.wait_ready(timeout=120.0), "move_group did not come up")
        self.assertEqual(check_named_poses(self.node, tolerance=0.01), [])


@launch_testing.post_shutdown_test()
class TestShutdown(unittest.TestCase):
    def test_exit_codes(self, proc_info):
        # Spawner exits 0 once controllers are active; the rest are SIGINT-ed.
        # move_group on Jazzy can segfault in rclcpp::Executor::~Executor() while
        # shutting down (after all work is done), so it is excluded here; see
        # docs/manual/troubleshooting.md.
        for info in proc_info:
            if info.action.process_details["name"].startswith("move_group"):
                continue
            self.assertIn(
                info.returncode,
                [0, -2, -15, 2, 15],
                f"{info.action.process_details['name']} exited with {info.returncode}",
            )
