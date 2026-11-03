# ar3_bringup

```bash
ros2 launch ar3_bringup bringup.launch.py mode:=mock          # ros2_control mock + MoveIt + RViz
ros2 launch ar3_bringup bringup.launch.py mode:=sim           # Gazebo Harmonic + MoveIt + RViz
ros2 launch ar3_bringup bringup.launch.py mode:=sim headless:=true             # no windows
ros2 launch ar3_bringup bringup.launch.py mode:=sim headless:=true viz:=foxglove use_software_rendering:=true  # Mac
```

| Launch arg | Default | Meaning |
|---|---|---|
| `mode` | `mock` | `mock`, `sim`, or `real`. `real` is refused until Phase 1 delivers `ar3_hardware`. |
| `viz` | `rviz` | `rviz`: RViz plus foxglove_bridge. `foxglove`: bridge only. `none`: neither. |
| `rviz` | `true` | With `viz:=rviz`: start RViz with the MotionPlanning panel |
| `gz_gui` | `true` | sim only: Gazebo GUI; `false` runs the server headless, and sensors still render |
| `headless` | `false` | No windows: no Gazebo GUI and no RViz. The bridge still follows `viz`. |
| `use_software_rendering` | `false` | Sets `LIBGL_ALWAYS_SOFTWARE=1` (Mesa llvmpipe) for Gazebo and RViz |
| `render_engine` | `ogre2` | sim only: Gazebo render engine. `ogre` is a fallback for old GL and needs an X display. |
| `foxglove_port` | `8765` | foxglove_bridge WebSocket port |
| `cockpit_agent` | `true` | Start `ar3_cockpit_agent` (the AR3 Cockpit's adapter, `127.0.0.1:8790`, see its README). Skipped with a message when the package is not built. |
| `config_dir` | `""` | Site config directory. Empty means `$AR3_CONFIG_DIR`, else the installed copy (`share/ar3_bringup/config/site`). |

foxglove_bridge listens on all interfaces and serves `package://` assets only (its default allowlist).
Lichtblick reads `file://` URIs from the viewer's own disk, but our URDF needs `file://` mesh paths
for Gazebo. So, together with the bridge, a second `robot_state_publisher` (`/viz/robot_description_viz`)
publishes a copy of the description with `package://` URIs on `/viz/robot_description`. Its TF
outputs go to hidden topics, which the bridge does not forward. `viz_description()` in the launch
file does the rewrite, and `test/test_launch_args.py` tests it.

Controllers (`config/ros2_controllers.yaml`): `joint_state_broadcaster`,
`joint_trajectory_controller` (position), and `gripper_controller` (`GripperActionController`, `/gripper_controller/gripper_cmd`).

## move_to_named

Plans and executes to SRDF named poses through `/move_action`. Before the first move it waits for
move_group, reads its default scaling and waits for the controllers to be active.

```bash
ros2 run ar3_bringup move_to_named observe stow home
ros2 run ar3_bringup move_to_named --group gripper open closed
```

| Parameter | Default | Meaning |
|---|---|---|
| `planning_time` | 5.0 | Planning time per attempt [s] |
| `planning_attempts` | 5 | Planning attempts |
| `velocity_scaling` | 0.0 | Velocity and acceleration scaling of each goal. 0: the defaults from `config/robot.yaml` `motion:`, read from move_group's `robot_description_planning` parameters (a goal with 0 would otherwise run at 1.0) |

Exit code: 0 if all moves succeed, 1 if a move fails, 2 if move_group is not available.
