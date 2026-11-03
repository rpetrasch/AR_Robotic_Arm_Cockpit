# Usage on a Mac (Apple Silicon)

The Mac is a development and remote-viewing machine. It runs mock and simulation in Docker, with no
devices and no GPU, and never drives the real arm. The containers use the `mac` and `vnc` profiles of
`docker/compose.yaml`; the `linux` profile (devices, X11, host network, NVIDIA) is for the workstations.

| Profile | Services | Network | Devices / GPU | Modes |
|---|---|---|---|---|
| `linux` | `dev`, `dev-gpu`, `zenohd` | host | `/dev`, X11, NVIDIA (`dev-gpu`) | mock, sim (real from Phase 1) |
| `mac` | `mac` | bridge, publishes 8765 | none | mock, sim |
| `vnc` | `vnc` | bridge, publishes 6080 and 8765 | none | mock, sim |

## Setup

1. Install Docker Desktop (or OrbStack) for Apple Silicon. In Docker Desktop, give it at least 8 GB of
   memory (*Settings → Resources*); 12–16 GB is comfortable for Gazebo, MoveIt and RViz together.
2. Install Lichtblick: download `lichtblick-<version>-mac-universal.dmg` from
   https://github.com/lichtblick-suite/lichtblick/releases/latest, open it and drag Lichtblick into
   *Applications*. If macOS blocks the first start, right-click the app and choose *Open*.
3. In the repository:

   ```bash
   make image          # dev image, native arm64 (once; ~10 min)
   make test           # same tests as on Linux; runs inside the image on a Mac
   ```

`make build`, `make test`, `make test-sim` and `make lint` detect that the host has no ROS installed
and run inside the `ar3-dev` image, so the commands are the same on both platforms.

## Mock or simulation with Lichtblick

```bash
make up-mac         # mock: ros2_control mock + MoveIt + foxglove_bridge
make sim-mac        # headless Gazebo (Mesa software rendering) + MoveIt + foxglove_bridge
```

Both run in the foreground; Ctrl-C stops them. With `DETACH=1` (as the cockpit starts them) they
run in the background, and `make down` stops whichever stack runs. `LICHTBLICK=1` also starts
Lichtblick in the browser on `http://localhost:8080`. The workspace is built on the first start if
`install/` does not exist yet. Wait for `You can start planning now!` in the output.

In Lichtblick: *Open connection → Foxglove WebSocket → `ws://localhost:8765`*, then
*Layout → Import from file…* and pick `config/lichtblick/ar3_default.json`. The layout has:

- **3D**: the arm, table and bins from `/viz/robot_description` (a copy of the URDF with `package://`
  mesh paths, which Lichtblick loads through the bridge), with TF, fixed frame `world`. Frame
  labels are off and axes are scaled to 0.3; both are under *Scene → Frames* in the panel settings.
  *Scene → Mesh up axis* is set to *Z-up*. ROS meshes are Z-up, and with Lichtblick's default
  (Y-up) every link is turned by 90° and the arm looks disassembled.
  Do not switch on the plain `/robot_description` topic: its `file://` mesh paths point into the
  container, so Lichtblick shows only the table and bins.
- **Images**: `/cam_overhead/image_raw` and `/cam_wrist/image_raw` (sim only).
- **Plot**: positions of `joint_1` … `joint_6`. The plot uses indices 2–7 of
  `/joint_states.position`, because `joint_state_broadcaster` publishes the two gripper jaws first.
- **Raw messages**: `/safety/status` (empty until Phase 1 adds `ar3_safety`) and the last point of
  the planned path, `/display_planned_path`. Lichtblick cannot draw a `DisplayTrajectory` in 3D.

### Moving the arm

In a second terminal, start a shell in the running container and send a named pose:

```bash
docker compose -f docker/compose.yaml exec mac bash
ros2 run ar3_bringup move_to_named observe stow home
```

The same pose as a plain MoveIt action goal (here `observe`, joint values from the SRDF):

```bash
ros2 action send_goal /move_action moveit_msgs/action/MoveGroup "{request: {group_name: arm,
  num_planning_attempts: 5, allowed_planning_time: 5.0,
  max_velocity_scaling_factor: 0.2, max_acceleration_scaling_factor: 0.2,
  goal_constraints: [{joint_constraints: [
    {joint_name: joint_1, position: 0.0, tolerance_above: 0.001, tolerance_below: 0.001, weight: 1.0},
    {joint_name: joint_2, position: 0.0524, tolerance_above: 0.001, tolerance_below: 0.001, weight: 1.0},
    {joint_name: joint_3, position: -0.1222, tolerance_above: 0.001, tolerance_below: 0.001, weight: 1.0},
    {joint_name: joint_4, position: 0.0, tolerance_above: 0.001, tolerance_below: 0.001, weight: 1.0},
    {joint_name: joint_5, position: 1.6406, tolerance_above: 0.001, tolerance_below: 0.001, weight: 1.0},
    {joint_name: joint_6, position: 0.0, tolerance_above: 0.001, tolerance_below: 0.001, weight: 1.0}]}]},
  planning_options: {plan_only: false}}"
```

The arm moves in Lichtblick's 3D panel and the joint plot.

### Performance

Docker on macOS has no GPU, so Gazebo renders the cameras with Mesa llvmpipe on the CPU. On an M1 Max
the overhead camera reaches about 1–2 Hz instead of the configured 15 Hz. Physics and motion are not
affected. For camera-rate work use Workstation A.

## RViz in the browser (noVNC)

`make vnc` starts its own complete stack: the robot (mock, or headless Gazebo with
`AR3_MODE=sim`), MoveIt, foxglove_bridge and RViz on a virtual desktop. Nothing else needs to run
first, and `make up-mac` / `make sim-mac` must **not** be running: both stacks would join the same
ROS graph and use port 8765. The make targets refuse to start while the other stack runs.

1. Stop `make up-mac` / `make sim-mac` if it runs (Ctrl-C in its terminal).
2. Start the stack and leave the terminal open:

   ```bash
   make vnc                 # mock; the first run builds ar3-dev-vnc (a few minutes)
   AR3_MODE=sim make vnc    # or: with headless Gazebo
   ```

3. Wait for `You can start planning now!` in the output (about 30 s for mock and 1–2 min for sim).
4. Open http://localhost:6080/vnc.html and click *Connect*.
5. Optional: Use Lichtblick and observe the run

The desktop shows RViz with the MotionPlanning panel (group `arm`, scaling 0.2). Pick a *Goal State*
(for example `observe`) and click *Plan & Execute*. Lichtblick can connect to `ws://localhost:8765`
at the same time. Ctrl-C in the terminal stops everything.

The VNC server has no password and is only published on the host; do not expose port 6080 to a
network.

## Connecting to Workstation A (Zenoh)

Across machines the Mac uses `rmw_zenoh_cpp` (DDS multicast does not cross Docker's NAT on macOS).
Workstation A runs the Zenoh router; the Mac's containers connect to it as Zenoh clients.

On Workstation A, every ROS process that the Mac should see must also use Zenoh:

```bash
make zenohd                                   # router on tcp/7447 (host network)
# in each other shell / container on Workstation A:
export RMW_IMPLEMENTATION=rmw_zenoh_cpp
```

On the Mac, copy `docker/.env.example` to `docker/.env` and set the values below. Both files start with a dot,
so Finder hides them; use `cp docker/.env.example docker/.env` in a terminal. Only Zenoh needs this
file; mock, sim and noVNC run without it.

```bash
ROS_RMW=rmw_zenoh_cpp
ZENOH_ROUTER=<Workstation A address>
```

Then any `mac` or `vnc` container connects to `tcp/<ZENOH_ROUTER>:7447`. Check with:

```bash
docker compose -f docker/compose.yaml run --rm mac ros2 topic list
```

`docker/.env` is not committed. Without it, `ROS_RMW` defaults to `rmw_fastrtps_cpp`, which is right
for single-host use on Linux and inside one Mac container.

## Native AI tools on macOS (LeRobot, MPS)

Training and inference experiments can run natively on the Mac's GPU through PyTorch's MPS backend.
This is outside Docker (containers on macOS cannot use the GPU) and outside ROS:

```bash
uv venv --python 3.12 ~/.venvs/lerobot
source ~/.venvs/lerobot/bin/activate
uv pip install lerobot
python -c "import torch; print(torch.backends.mps.is_available())"   # True
```

Use `--policy.device=mps` (or `device="mps"` in Python) where LeRobot asks for a device. The
learning stack of Phases 4–5 targets CUDA on Workstation B; MPS is for local experiments only.
