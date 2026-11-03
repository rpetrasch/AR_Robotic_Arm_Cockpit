# Usage: mock and simulation

Source the workspace first (`source install/setup.bash`). Inside the dev container this is done for you.

## Bring up

On Linux with Docker, one command starts a stack in the dev container (Ctrl-C stops it; with
`DETACH=1` it runs in the background and `make down` stops it). Only one stack may run at a time;
the targets refuse to start a second one. The AR3 Cockpit starts the same targets from its
Environment page.

```bash
xhost +local:docker          # once per login: lets the container open windows
make up-linux                # mock + MoveIt + RViz window
make sim-linux               # Gazebo + MoveIt + Gazebo and RViz windows
make sim-linux HEADLESS=1    # no windows; watch with Lichtblick on ws://<host>:8765
make sim-linux GPU=1         # on the NVIDIA GPU (needs the NVIDIA Container Toolkit)
```

The workspace is built on the first start. Inside a dev shell (`make shell`) or on a native install:

```bash
ros2 launch ar3_bringup bringup.launch.py mode:=mock    # no physics; state follows command
ros2 launch ar3_bringup bringup.launch.py mode:=sim     # Gazebo Harmonic + camera sensors
```

Useful arguments:
- `viz:=rviz|foxglove|none`: RViz plus foxglove_bridge (default), the bridge only, or neither.
- `headless:=true`: no windows at all (Gazebo server only, no RViz).
- `rviz:=false` and `gz_gui:=false`: switch off one window.
- `use_software_rendering:=true`: Mesa on the CPU, for hosts without a GPU.
- `config_dir:=<dir>`.

See [ar3_bringup](../../src/ar3_bringup/README.md). On a Mac, see [usage-mac.md](usage-mac.md).

Both modes start robot_state_publisher, ros2_control with
`joint_state_broadcaster`, `joint_trajectory_controller` and `gripper_controller`,
plus move_group and, optionally, RViz.

## Move to named poses

From RViz, open the MotionPlanning panel and go to *Planning*. Pick the group (`arm` or `gripper`), choose a
*Goal State* (`home`, `observe` or `stow`; `open` or `closed` for the gripper), then click *Plan & Execute*.
Velocity and acceleration scaling start at 0.2.

From a script or a terminal:

```bash
ros2 run ar3_bringup move_to_named observe stow home
ros2 run ar3_bringup move_to_named --group gripper open closed
```

## Cameras (sim)

| Topic | Type | Frame |
|---|---|---|
| `/cam_overhead/image_raw`, `/cam_overhead/camera_info` | Image 1280×720, CameraInfo | `cam_overhead_optical` |
| `/cam_wrist/image_raw`, `/cam_wrist/camera_info` | Image 640×480, CameraInfo | `cam_wrist_optical` |

`ros2 topic hz /cam_overhead/image_raw` shows the rate, which is set in `config/cameras.yaml`
(15 Hz). On a CPU-only host it can be lower.

## Remote view: Lichtblick

foxglove_bridge listens on WebSocket port 8765 whenever `viz` is `rviz` or `foxglove`. In Lichtblick,
open the connection `ws://<host>:8765` and import the layout `config/lichtblick/ar3_default.json`.
It shows the 3D arm, both cameras, joint plots, `/safety/status` and the planned path.
[usage-mac.md](usage-mac.md) describes the layout panels and how to install Lichtblick.

## Changing the workcell

Edit `config/`. With `--symlink-install`, the installed copy follows the edits, so no rebuild is needed:

- `robot.yaml`: table pose and size, the robot mount on the table, joint limits, and default scaling.
- `bins.yaml`: the bins (name, position, size, colour). The URDF and SRDF regenerate from it.
- `cameras.yaml`: camera mount poses and image parameters.

The sim objects (cubes and cylinders) are defined in `src/ar3_gazebo/worlds/workcell.sdf`.
