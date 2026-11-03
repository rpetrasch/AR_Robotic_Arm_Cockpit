# ar3_description

URDF/xacro for the AR3 workcell: arm, AR4 servo gripper, table, bins and camera mounts.

```bash
xacro $(ros2 pkg prefix ar3_description)/share/ar3_description/urdf/ar3.urdf.xacro \
  config_dir:=$AR3_CONFIG_DIR hardware:=mock
```

| xacro arg | Default | Meaning |
|---|---|---|
| `config_dir` | — (required) | Directory with `robot.yaml`, `bins.yaml`, `cameras.yaml` |
| `hardware` | `none` | `none` (no ros2_control), `mock` (mock_components), `gz` (Gazebo) |
| `controllers_file` | `""` | `gz` only: controller YAML passed to gz_ros2_control |
| `include_bins`, `include_cameras` | `true` | Leave out bins / camera mounts |

Frames: `world → table → base_link → link_1…link_6 → tool0 → gripper_tcp`,
`world → cam_overhead_link → cam_overhead_optical`,
`tool0 → cam_wrist_link → cam_wrist_optical`, `table → bin_<name>`.
`gripper_tcp` sits 55 mm out from the flange, between the jaw pads.

- `config/ar3_kinematics.yaml`: link lengths. These are the stock AR3 values, the same as upstream AR4 MK1. Spec §12 #3 is still open.
- `urdf/ar3_macro.xacro`: the arm, a parameterized copy of the upstream macro.
- `urdf/workcell.xacro`: table, bins and cameras, generated from the site config.
- `urdf/ar3.ros2_control.xacro`: the ros2_control tags. The command interface is position only.
- `urdf/ar3.gazebo.xacro`: the camera sensors and the gz_ros2_control plugin.
