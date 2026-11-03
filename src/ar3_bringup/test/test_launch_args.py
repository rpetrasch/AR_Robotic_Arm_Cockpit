"""Checks on bringup.launch.py that need no running nodes."""

import importlib.util
from pathlib import Path

import pytest

LAUNCH = Path(__file__).resolve().parents[1] / "launch" / "bringup.launch.py"


@pytest.fixture(scope="module")
def bringup():
    spec = importlib.util.spec_from_file_location("bringup_launch", LAUNCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_viz_description_uses_package_uris(bringup):
    urdf = (
        '<mesh filename="file:///ar3_ws/install/annin_ar4_description/share/'
        'annin_ar4_description/meshes/ar4/base_link.STL"/>'
        '<mesh filename="file:///opt/ros/jazzy/share/some_pkg/meshes/part.dae"/>'
        '<mesh filename="package://other_pkg/meshes/x.stl"/>'
    )
    out = bringup.viz_description(urdf)
    assert "file://" not in out
    assert 'filename="package://annin_ar4_description/meshes/ar4/base_link.STL"' in out
    assert 'filename="package://some_pkg/meshes/part.dae"' in out
    assert 'filename="package://other_pkg/meshes/x.stl"' in out


def test_viz_description_matches_real_urdf(bringup):
    from ar3_moveit_config import build_moveit_config

    config_dir = Path(__file__).resolve().parents[3] / "config"
    urdf = build_moveit_config(str(config_dir)).robot_description["robot_description"]
    assert "file://" in urdf  # Gazebo needs file:// paths
    out = bringup.viz_description(urdf)
    assert "file://" not in out
    assert out.count("package://") == urdf.count("file://")


def test_new_args_default_to_previous_behaviour(bringup):
    args = {
        a.name: a.default_value
        for a in bringup.generate_launch_description().entities
        if hasattr(a, "default_value")
    }
    defaults = {k: "".join(s.text for s in v) for k, v in args.items()}
    assert defaults["mode"] == "mock"
    assert defaults["viz"] == "rviz"
    assert defaults["headless"] == "false"
    assert defaults["use_software_rendering"] == "false"
    assert defaults["render_engine"] == "ogre2"
    assert defaults["foxglove_port"] == "8765"
    assert defaults["cockpit_agent"] == "true"
