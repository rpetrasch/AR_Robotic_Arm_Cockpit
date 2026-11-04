# AR3 Manipulation Stack — Implementation Spec

Owners: Richard Petrasch, Roland Petrasch (see `LICENSE.md`).
Status: v0.6 (2026-11-01). Items marked **[OPEN]** need confirmation before the phase that depends on them.
**ToDo**: Change file name spec_v0.4.md to spec.md

Changelog:
- v0.6 (2026-11-01): Camera plan — multi-webcam setup (overhead + wrist + 1–2 side), depth camera deferred (needs listed in §11); Phase 2 multi-camera calibration, setting lock, drift check, triangulation, observe-pose policy; accuracy targets per method; side-camera topics/TF; multi-view recording (Phase 4).
- v0.5 (2026-11-01): AR3 Cockpit (see spec_cockpit.md): §5 layout, §9a `docs/manual/cockpit.md`.
- v0.4 (2026-10-31): Phase 0b — macOS dev profile (multi-arch images, `mac` compose profile, headless sim, Lichtblick, optional noVNC, Zenoh endpoint); §4 visualization/macOS; `docs/manual/usage-mac.md`.
- v0.3 (2026-10-30): §9a Documentation deliverables.
- v0.2 (2026-10-30): Decisions — Teensy 4.1, Nano gripper MCU, NVIDIA GPUs, depth camera planned, hardware e-stop circuit; Phase 1 firmware/wiring tasks; open questions reduced.
- v0.1 (2026-10-30): Initial spec.

---

## 1. Goal

A ROS 2 software stack for an Annin AR3 arm (AR4 servo gripper) that performs, in order of delivery:

1. **Pick and place** of known objects on a flat table at taught or detected positions.
2. **Object detection** (class + 2D bbox + 3D table position) from calibrated fixed webcams (overhead primary, 1–2 side cameras), refined by a wrist camera.
3. **Sorting**: detect N objects, classify (class / color), place each into a configured bin.
4. (Later) **Learned policies**: teleoperated demonstrations → imitation-learning policy (ACT / SmolVLA) → optional VLA fine-tune. Same ROS interfaces as the classical pipeline so both can be swapped and benchmarked.

The classical pipeline (phases 0–3) is the baseline and the safety/fallback path. Learning components (phases 4–5) must not be required for phases 0–3 to work.

## 2. Non-goals (this spec)

- DreamerV3 / latent world model, MPPI over a learned model, LIBERO integration. These are research items (Phase 6, out of scope here) and only become meaningful after Phase 4 has produced a dataset.
- Torque / force control. The AR3 is a stepper-driven position-controlled arm; all control is joint-position trajectories.
- Mobile base, Nav2.
- macOS-native ROS 2 builds.

## 3. Hardware context & constraints

| Item | Detail | Implication |
|---|---|---|
| Arm | AR3, 6 steppers, rotary encoders, limit switches | Position control only; homing via limit switches required at startup |
| Arm MCU | Teensy 3.5 → **Teensy 4.1 (decided)** | Flash the Teensy sketch shipped with `ar4_ros_driver` (not Annin's ARCS sketch). Teensy 4.1 I/O is 3.3 V only: AR3 5 V signals (AMT102 encoders, limit switches) need level shifting — documented in `ar3_firmware/WIRING.md`. AR3 steps/deg, encoder ratios, joint directions and limits must replace AR4 values in sketch + driver YAML. |
| Gripper | AR4 servo gripper | **Decided: Arduino Nano + ACS712** per upstream driver (enables the driver's gripper over-current/grasp detection). Mega is freed for peripherals. |
| E-stop | Manual latching button (present), **hardware e-stop circuit to be built** | Dual-channel: NC contact 1 drops motor-supply contactor; NC contact 2 to Teensy e-stop input (pull-up, open = stopped). Logic supply stays powered so encoder counts survive. Software reset required before motion resumes. |
| I/O MCU | Arduino Mega, ESP32s | Peripherals (LEDs, ToF, buzzer) via micro-ROS on ESP32 — not in the arm's control loop |
| Cameras | USB webcams: 1 overhead, 1–2 side, 1 wrist | Overhead is primary for detection; side cams for occlusion coverage, triangulation and multi-view datasets; wrist cam for the final approach. No depth camera for now (see §11). |
| Other | Mic, speaker, ultrasonic, LEDs | Optional nodes; not on critical path |
| Robot host | Workstation A: Ryzen, 32 GB RAM, 8 GB GPU | Runs driver, ros2_control, MoveIt, perception, Gazebo, RViz |
| AI host | Workstation B: 16 GB GPU | Runs policy training/inference; connected via wired Ethernet |
| Dev | MacBooks (64 / 32 GB) | Editing, remote RViz/Foxglove, optional local LLM/VLM for task parsing |

GPUs: NVIDIA on both workstations (AMD CPUs). CUDA available everywhere; use NVIDIA Container Toolkit for GPU access in Docker.

Cameras: webcams only. A depth camera is **deferred**; it is bought only if a need from §11 appears. Perception keeps one interface for 3D position with three methods:
- `planar` (default): single camera, ray–table-plane intersection + known object height;
- `triangulated`: ≥ 2 calibrated views;
- `aligned_depth`: reserved for a later depth camera; interface stubbed, not implemented.

Webcam rules:
- MJPEG streams;
- fixed resolution / fps;
- autofocus, auto-exposure and auto white balance locked via UVC controls (`v4l2-ctl`) at startup from `config/cameras.yaml`;
- stable device names via udev rules (by serial / USB port path);
- cameras spread across USB controllers or powered hubs;
- rigid mounts.

## 4. Platform decisions

- **OS**: Ubuntu 24.04 on Workstations A and B (native, not WSL/VM, for USB/serial reliability).
- **ROS 2**: Jazzy (LTS). Do not use Gazebo Classic.
- **Simulator**: Gazebo Harmonic via `ros_gz`, `gz_ros2_control`.
- **Motion planning**: MoveIt 2 (OMPL + Pilz industrial planner for linear approach/retreat moves).
- **Control**: `ros2_control`, `joint_trajectory_controller` (position interface), gripper via `position_controllers` or `GripperActionController`.
- **Middleware**: default `rmw_fastrtps_cpp` on one host; evaluate `rmw_zenoh_cpp` once two hosts are involved. Domain ID configurable via env.
- **Languages**: C++ for hardware interface; Python (rclpy) for perception, task logic, tools.
- **Task logic**: BehaviorTree.CPP v4 (via `py_trees_ros` acceptable if Python-only is simpler — pick one, document why).
- **Detection**: Ultralytics YOLO (v8/11) with ONNX export; ArUco/AprilTag for calibration.
- **Containerization**: Docker dev container for Workstation A (Jazzy desktop image + deps) and B (CUDA + LeRobot). Devices passed through (`/dev/ttyACM*`, `/dev/video*`).
- **Visualization**: RViz2 and Gazebo GUI natively on Workstation A. `foxglove_bridge` (WebSocket, port 8765) runs in every profile; remote viewing with **Lichtblick** (open-source, Foxglove-compatible). Optional **noVNC** service exposes RViz2 (incl. MoveIt MotionPlanning plugin) in a browser.
- **macOS development**: Docker on Apple Silicon (`linux/arm64`) for mock/sim/test work — no GPU, no USB, headless Gazebo with software rendering. Connection to the live robot graph on Workstation A via `rmw_zenoh_cpp` (router on Workstation A). See Phase 0b.

## 5. Repository layout

Monorepo, colcon workspace:

```
ar3_ws/
├── docs
│    ├── ....md                   # specs, testing, usage md files
│    └── spec_v0.4.md             # this file
├── docker/                       # Dockerfiles, compose, udev rules
├── cockpit.sh                    # AR3 Cockpit bootstrap (spec_cockpit.md)
├── tools/cockpit/                # AR3 Cockpit web app: Django backend + Next.js frontend (COLCON_IGNORE)
├── src/
│   ├── external/                 # vcs-imported, pinned (ar4_ros_driver fork, etc.), do not touch
│   ├── ar3_description/          # URDF/xacro (arm + gripper + camera mounts + table + bins), meshes
│   ├── ar3_hardware/             # ros2_control hardware interface (fork/adapt of annin_ar4_driver)
│   ├── ar3_firmware/             # Teensy + gripper MCU sketches (PlatformIO), protocol doc
│   ├── ar3_bringup/              # launch files: real | sim | mock_hardware
│   ├── ar3_moveit_config/        # SRDF, kinematics, joint limits, planning pipelines
│   ├── ar3_gazebo/               # world(s), gz plugins, camera sensors
│   ├── ar3_calibration/          # intrinsics, hand-eye, table plane
│   ├── ar3_perception/           # detection, 3D projection, object tracker
│   ├── ar3_interfaces/           # custom msgs/srvs/actions
│   ├── ar3_skills/               # pick, place, move_to_named_pose action servers
│   ├── ar3_tasks/                # behavior trees: pick_place, sort
│   ├── ar3_safety/               # watchdog, workspace limits, e-stop bridge
│   ├── ar3_peripherals/          # micro-ROS agent config, LED/ToF/audio nodes
│   ├── ar3_teleop/               # gamepad / keyboard / leader-arm teleop, MoveIt Servo
│   ├── ar3_learning/             # dataset recorder, policy inference node (Phase 4+)
│   └── ar3_cockpit_agent/        # ROS-side adapter of the AR3 Cockpit (spec_cockpit.md §16)
├── training/                     # non-ROS: LeRobot configs, training scripts (Workstation B)
├── config/                       # site-specific YAML: cameras, bins, objects, calibration results
│   ├── scenarios/                # AR3 Cockpit simulation scenarios (spec_cockpit.md §12)
│   └── cockpit/                  # AR3 Cockpit alert rules, error patterns, component catalogue
└── tests/                        # launch_testing / pytest integration tests
```

## 6. Interfaces (`ar3_interfaces`)

### Messages
- `DetectedObject.msg`: `string id`, `string class_name`, `float32 confidence`, `string color`, `geometry_msgs/PoseStamped pose` (frame `table`), `vision_msgs/BoundingBox2D bbox`, `string source_camera`
- `DetectedObjectArray.msg`: `std_msgs/Header header`, `DetectedObject[] objects`
- `SafetyStatus.msg`: `bool estop`, `bool homed`, `bool in_workspace`, `string[] faults`

### Actions
- `Pick.action` — goal: `string object_id` OR `geometry_msgs/PoseStamped target`, `float32 approach_height`; result: `bool success`, `string error_code`; feedback: `string stage`
- `Place.action` — goal: `geometry_msgs/PoseStamped target` OR `string bin_name`; result/feedback as above
- `MoveToNamed.action` — goal: `string name` (`home`, `observe`, `stow`, …)
- `RunTask.action` — goal: `string task` (`pick_place`, `sort`), `string params_yaml`; feedback: progress; result: counts + failures

### Services
- `Home.srv`, `ResetEstop.srv`, `CaptureCalibrationSample.srv`, `SetGripper.srv` (`float32 position`, `float32 max_effort`)

### Key topics (non-exhaustive)
| Topic | Type | Publisher |
|---|---|---|
| `/joint_states` | sensor_msgs/JointState | joint_state_broadcaster |
| `/cam_overhead/image_raw`, `/camera_info` | sensor_msgs | usb_cam |
| `/cam_wrist/image_raw`, `/camera_info` | sensor_msgs | usb_cam |
| `/cam_side_<n>/image_raw`, `/camera_info` | sensor_msgs | usb_cam (n = 1, 2) |
| `/perception/objects` | DetectedObjectArray | ar3_perception |
| `/perception/debug_image` | sensor_msgs/Image | ar3_perception |
| `/safety/status` | SafetyStatus | ar3_safety |
| `/gripper/tof_range` | sensor_msgs/Range | ESP32 micro-ROS (optional) |

### TF tree
`world → table → base_link → … → tool0 → gripper_tcp`; `world → cam_overhead_optical`; `world → cam_side_<n>_optical`; `tool0 → cam_wrist_optical` (from hand-eye); `table → bin_<name>` (static, from config).

## 7. Phases, deliverables, acceptance criteria

Each phase ends with: tests passing in CI (sim/mock), a short `docs/phaseN.md` with how to run it, and a demo command.

### Phase 0 — Workspace, description, simulation
- Docker dev env, pre-commit (ruff, clang-format), GitHub Actions CI running `colcon build && colcon test` with `mock_components`.
- `ar3_description`: AR3 URDF/xacro derived from upstream (verify DH/link lengths against the AR3, not AR4 — **[OPEN]** we provide measured link lengths if they differ), gripper, camera mounts, table, bins as xacro args.
- `ar3_moveit_config` via MoveIt Setup Assistant output, cleaned up; named poses `home`, `observe`, `stow`.
- `ar3_gazebo`: table world, colored cubes / cylinders, overhead + wrist camera sensors.
- `ar3_bringup`: `ros2 launch ar3_bringup bringup.launch.py mode:={real|sim|mock}`.

**Accept**: In sim and mock, MoveIt plans and executes to all named poses from RViz and from a Python script; Gazebo camera topics publish.

### Phase 0b — macOS dev profile (retrofit to completed Phase 0)
Additive change; must not alter package code or the existing Linux workflow.
- **Multi-arch images**: base/dev image builds for `linux/amd64` and `linux/arm64` via `docker buildx`. CUDA/AI image stays amd64-only and is a separate target. No arch-specific packages in the base image; guard any that exist.
- **Compose profiles**: `linux` (current: devices, NVIDIA runtime, host network) and `mac` (no device mounts, no GPU runtime, bridge network with published ports, `mode:=mock|sim` only). Device mounts and GPU settings must move out of the shared service definition into the `linux` profile/override.
- **Headless sim**: `bringup.launch.py` gets `headless:=true|false` (Gazebo server-only, no RViz) and `use_software_rendering:=true` (sets `LIBGL_ALWAYS_SOFTWARE=1`, uses ogre2 with Mesa; fall back to ogre if ogre2 fails).
- **Visualization**: `foxglove_bridge` launched in all profiles (`viz:=foxglove|rviz|none`). Provide a Lichtblick layout file (`config/lichtblick/ar3_default.json`) with 3D (URDF, TF, planned path, markers), camera images, joint state plots, `/safety/status`.
- **noVNC (optional service, `--profile vnc`)**: TigerVNC + lightweight WM + noVNC (port 6080) running RViz2 with the MoveIt config. Works on both arches.
- **Remote endpoint**: `ROS_RMW` env selectable; `rmw_zenoh_cpp` installed in the image. Workstation A runs `zenohd` router (compose service); the Mac container connects via `ZENOH_CONFIG_OVERRIDE` with Workstation A's address in `.env`. Default on Linux single-host remains Fast DDS.
- **Native AI on macOS** (documented, not containerized): LeRobot with MPS backend for policy inference on recorded datasets.
- **Make targets**: `make up-mac`, `make sim-mac`, `make vnc`, `make test` (same test command on both platforms).

**Accept**: On an Apple Silicon MacBook: `make sim-mac` starts headless Gazebo + MoveIt; Lichtblick on the Mac shows the arm and executes a named-pose motion triggered via `ros2 action send_goal`; `make vnc` serves RViz with MotionPlanning plugin in a browser; `colcon test` passes identically to Linux; Mac container sees topics from Workstation A via Zenoh. CI builds both arches.

### Phase 1 — Real hardware bring-up
- Firmware: vendor the upstream Teensy 4.1 + Nano sketches into `ar3_firmware` (PlatformIO), parametrize AR3 constants (steps/deg, encoder multipliers, directions, limits, calibration order) in one header, document the serial protocol in `ar3_firmware/PROTOCOL.md` and the pin map + level shifting + e-stop circuit in `ar3_firmware/WIRING.md` (we supply measured values; we should not invent pin assignments). **License:** the sketches are © Chris Annin under his terms (no selling; see `THIRD_PARTY_NOTICES.md`). Keep his full header in every derived file, add a change note, and keep `ar3_firmware/` free of MIT-only code so the boundary stays clear.
- Bench test tool (`ar3_firmware/tools/jog.py`): single-joint jog and encoder readout over serial, independent of ROS, max speed capped.
- `ar3_hardware`: ros2_control `SystemInterface` for arm + gripper; homing on activate; joint offsets/limits from YAML; reads encoder positions; exposes e-stop state.
- `ar3_safety`: software watchdog (stop if no command or no joint state within T ms), Cartesian workspace box check on planned trajectories, velocity/acceleration scaling cap (default 0.2), hardware e-stop status → `/safety/status`. Any fault → hold position, reject new goals until `ResetEstop`.

**Accept**: Real arm homes, reaches all named poses, gripper opens/closes from RViz; repeatability test script (10× to same pose, report encoder deviation); watchdog verified by killing the command node.

### Phase 2 — Calibration & perception
- `ar3_calibration`:
  - intrinsics per camera (checkerboard, `camera_calibration`);
  - extrinsics of **all** fixed cameras against one ArUco/ChArUco board on the table (common `table` frame), with a reprojection-error report per camera;
  - wrist hand-eye via `easy_handeye2` or equivalent;
  - table plane fit.
  - Results go to `config/calibration/*.yaml`, loaded at launch.
- **Camera settings lock:** a node or launch step applies the UVC settings from `config/cameras.yaml` (focus, exposure, white balance, gain) at startup and verifies them; mismatch → warning on `/diagnostics`.
- **Calibration drift check:** ≥ 3 ArUco markers fixed on the table (outside the work area) are re-detected periodically by each fixed camera. Pose deviation > threshold (default 3 mm / 0.5°) → diagnostic warning + "recalibrate" hint. Also run at stack start.
- Gazebo: add 1–2 side camera sensors to the table world, matching the real mounting poses from config.
- `ar3_perception`:
  - Detector node: YOLO (ONNX) on overhead cam; configurable model path & classes; color classifier (HSV) as a no-training fallback.
  - Detector runs on the overhead and (configurable) one side camera; other streams are for coverage and recording only.
  - 3D position, method per config (`planar` | `triangulated`):
    - `planar`: ray–table-plane intersection + known object height from `config/objects.yaml`;
    - `triangulated`: associate detections across calibrated views (epipolar constraint + class/colour), triangulate the object centre (DLT + refinement), and report the height above the table as a measured value (no known-height assumption);
    - fall back to `planar` when an object is seen by only one view.
  - Timestamp tolerance for multi-view association: default 50 ms (unsynchronised webcams); intended for static scenes.
  - **Observe-pose policy:** tasks detect from a named `observe` pose with the arm outside the fixed cameras' view; detections taken while the arm occludes the object (arm mask from URDF projected into each view) are flagged and not used for grasp targets.
  - Simple tracker (IoU / nearest-neighbor) for stable `object_id`s across frames.
  - Optional wrist-cam refinement service: given object_id, re-detect from `observe_close` pose and return corrected pose.
- Dataset tooling: script to capture and label images (export to YOLO format; Roboflow / CVAT compatible).

**Accept**: Position error of detected object vs. measured ground truth on 20 placements: ≤ 8 mm (`planar`, overhead only) and ≤ 5 mm (`triangulated`, overhead + side), height error ≤ 5 mm (`triangulated`); detection ≥ 10 Hz on Workstation A with all cameras streaming; drift check flags a deliberately moved camera (≥ 5 mm) within one check cycle.

### Phase 3 — Skills & tasks (pick-place, sort)
- `ar3_skills`: `Pick` = pre-grasp (above, top-down) → Pilz LIN descent → close gripper → verify grasp (gripper position / current / ToF) → LIN retreat. `Place` mirrors it. Collision objects (table, bins) added to planning scene from config.
- `ar3_tasks`: behavior trees for `pick_place` (single object, given class or id) and `sort` (loop: observe → select next object → pick → place into bin by rule → repeat until empty or max failures). Rules in `config/sorting.yaml` (class→bin, color→bin).
- Retry policy: re-observe and retry pick up to N times; log failures with images.
- Metrics logger: per-attempt CSV/JSONL (task, object, success, failure stage, duration) for later comparison with learned policies.

**Accept**: Sim: sort 10 objects into 3 bins, ≥ 90 % success over 5 runs. Real: ≥ 80 % pick success on 3 object types over 50 attempts.

### Phase 4 — Teleop & data collection (learning prerequisite)
- `ar3_teleop`: MoveIt Servo with gamepad (Cartesian jog), gripper on buttons. **[OPEN]** leader-arm teleop (low-cost leader built from servos) later.
- `ar3_learning/recorder`: synchronized recording of all camera views (overhead, side, wrist), joint states, gripper, commanded action, language instruction → **LeRobot dataset format** (v2.x/v3, parquet + mp4). Episode start/stop/discard via gamepad.
- Configurable fps (default 30 Hz images, actions at control rate downsampled to dataset fps).

**Accept**: 50 recorded episodes load in LeRobot and replay in RViz.

### Phase 5 — Learned policy integration
- `training/`: LeRobot configs for ACT (first) and SmolVLA (second) on Workstation B.
- `ar3_learning/policy_node`: loads a checkpoint, subscribes to cameras + joint_states, outputs action chunks; executes via `joint_trajectory_controller` through `ar3_safety` (same workspace/velocity limits). Exposed as an alternative implementation of `Pick`/`RunTask` (param `executor: classical|policy`).
- Inference may run on Workstation B; the action stream crosses the network — measure latency, fail safe on stale actions (> 200 ms → hold).
- Evaluation harness reuses Phase 3 metrics logger for A/B comparison.

**Accept**: Policy runs end-to-end on the real arm with safety layer active; success rate reported against the classical baseline on the same object set.

### Phase 6 — Research (not in scope; placeholder)
Here are some of the topics: VLA fine-tuning (OpenVLA-OFT / π0-family, cloud GPU), language task parsing via local VLM on MacBook, learned world model + sampling-based planner.
Another interesting extension is NVIDIA Isaac Sim and MuJoCo (see https://github.com/Ekumen-OS/ar4).

Also on the "menu" with high priority is data quality (DQ) for robotics, e.g. data checks and cleansing for sensor data, creating AI-ready data from simulations and physical twin runs, evaluation of AI results. We ould use Grafana and other DQ tools for that.

These topics require their own spec.

## 8. Configuration

All site-specific values in `config/`, never hard-coded:
`robot.yaml` (serial ports, joint offsets, limits, velocity scaling), `cameras.yaml` (per camera: role, stable device name, resolution, fps, MJPEG, UVC settings, detector on/off), `calibration/*.yaml`, `objects.yaml` (class, height, grasp width, grasp offset), `bins.yaml` (name, pose, size), `sorting.yaml`, `safety.yaml` (workspace box, watchdog timeouts).

## 9. Testing

- Unit: pytest for Python logic (projection math, sorting rules, tracker); gtest for hardware-interface parsing using a fake serial port.
- Integration: `launch_testing` in `mock` mode (no Gazebo) in CI; Gazebo tests runnable locally (`make test-sim`), headless.
- Hardware tests: scripts in `tests/hardware/` — never run in CI, require explicit `--i-am-at-the-robot` flag.
- Firmware protocol: a Python serial simulator implementing the protocol, used by hardware-interface tests.

## 9a. Documentation (deliverable, part of every phase's acceptance)

| File | Content |
|---|---|
| `README.md` | Overview, architecture diagram, quickstart (sim in ≤ 10 commands), links to `docs/` |
| `docs/manual/install.md` | Ubuntu 24.04, ROS 2 Jazzy, Gazebo Harmonic, MoveIt 2, NVIDIA driver + Container Toolkit, Docker, udev rules; separate AI-host section (CUDA, LeRobot) |
| `docs/hardware.md` | Teensy 4.1 retrofit, pin map, level shifting, e-stop circuit, firmware flashing, first-power-on checklist |
| `docs/manual/usage-mac.md` | macOS setup (Docker Desktop/OrbStack, buildx), `mac` profile, Lichtblick layout, noVNC, Zenoh connection to Workstation A, native LeRobot (MPS) |
| `docs/manual/usage-sim.md` | Gazebo bring-up, MoveIt/RViz, running `pick_place` and `sort` tasks in sim |
| `docs/usage-real.md` | Pre-flight checklist (e-stop test, homing, RViz pose check, speed scaling), then the same tasks on hardware |
| `docs/calibration.md` | Camera intrinsics, hand-eye, table plane; where results are stored and how they are loaded |
| `docs/ai.md` | Data recording, training ACT/SmolVLA, policy deployment, evaluation vs. classical baseline |
| `docs/dev/testing.md` | Unit, mock, sim and hardware tests; what CI covers; how to run each |
| `docs/manual/troubleshooting.md` | Serial ports, DDS discovery, encoder drift, camera issues, common errors and fixes |
| `docs/manual/cockpit.md` | AR3 Cockpit: start, first user, header, pages, configuration, fake mode, `ar3ctl` (spec_cockpit.md) |

Rules:
- Each phase creates or updates the docs it affects; a phase is not done until its docs are updated.
- Every command in the docs must have been executed successfully in that phase (sim/mock in CI; hardware commands marked as such and verified).
- Docs describe the current state only — no planned features. Version-pinned (ROS distro, package versions) where it matters.
- `docs/phaseN.md` stays as a short changelog per phase; user-facing instructions live in the files above.

## 10. Engineering rules

- Build on upstream `ar4_ros_driver` (vendored fork under `src/external`, pinned commit; upstream is marked unmaintained) instead of rewriting from scratch. Record every divergence in `src/external/README.md`.
- Never send motion commands to real hardware in automated runs. Real-hardware launch requires `mode:=real` explicitly.
- Default velocity/acceleration scaling 0.2; changing defaults requires a config change, not code.
- Every new node: parameters declared with defaults and descriptions, lifecycle node where it owns hardware, README section.
- Prefer standard messages (`vision_msgs`, `control_msgs`, `moveit_msgs`) over custom ones.
- Ask when an **[OPEN]** item blocks progress; don't guess hardware facts (link lengths, pin assignments, serial protocol).

## 11. Sensor plan (summary)

| Sensor | Status | Use |
|---|---|---|
| Joint encoders, limit switches | Required | Homing, drift detection |
| Overhead webcam | Required | Primary detection (`planar`) |
| Side webcams (1–2) | Recommended | Occlusion coverage, triangulated 3D, multi-view learning datasets |
| Depth camera | **Deferred** | Buy only if needed for: unknown/irregular objects (geometry-based grasps), stacked/touching objects, live 3D collision map in MoveIt (octomap), or webcam accuracy targets missed. If bought, prefer wrist/near-wrist placement (avoids occlusion); consider cheaper stereo options |
| ArUco markers (table) | Required | Common calibration frame + drift check |
| Wrist camera | Recommended | Close-range refinement, learning policies |
| Hardware e-stop | **Required (add)** | Wired into Teensy e-stop input |
| Gripper grasp sensing | Recommended (add) | Servo current (ACS712/INA219) or finger FSR, for grasp verification |
| ToF (VL53L1X) on gripper | Optional (replaces ultrasonic) | Approach distance / object presence |
| Ultrasonic | Not used | Too coarse at grasp range |
| LEDs | Optional | Status indication via ESP32 |
| Mic / speaker | Optional, Phase 6 | Voice commands, audio feedback |
| Lighting | Recommended | Fixed diffuse lighting over the table for stable detection |

## 12. Open questions (consolidated)

Resolved (2026-10-30): Teensy 4.1 upgrade; Nano for gripper; NVIDIA GPUs; depth camera deferred (2026-11-01, multi-webcam instead); hardware e-stop circuit to be built (manual button exists).

Still open:
1. AR3 pin map, steps/deg, encoder ratios, joint directions — we will provide from current ARCS config before Phase 1.
2. Object set for Phases 2–3 (classes, sizes) and bin layout.
3. AR3 link dimensions vs. AR4 URDF — measured or stock?
4. Side-camera count (1 or 2) and mounting poses; webcam models (prefer identical models with UVC manual focus/exposure).
