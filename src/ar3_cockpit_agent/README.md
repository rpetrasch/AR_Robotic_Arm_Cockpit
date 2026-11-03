# ar3_cockpit_agent

The AR3 Cockpit's adapter inside the ROS stack ([docs/manual/cockpit.md](../../docs/manual/cockpit.md),
spec §16). The cockpit runs natively on the host and cannot use rclpy there (macOS), so it asks
this node over HTTP and WebSockets.

The bringup starts it in every stack (`cockpit_agent:=true`, the default); `make remote` runs it
alone (REMOTE). By hand:

```bash
ros2 run ar3_cockpit_agent cockpit_agent --ros-args -p mode:=sim
```

## Parameters and environment

| Parameter | Default | Meaning |
|---|---|---|
| `mode` | `mock` | `mock`, `sim`, `real` or `remote`: what this stack runs (decides which components count) |
| `config_dir` | `""` | Site config (`cameras.yaml` for the expected camera rates, `cockpit/telemetry.yaml` for the telemetry); empty: `$AR3_CONFIG_DIR` |
| `controller_manager` | `/controller_manager` | controller_manager node to ask |
| `move_group` | `move_group` | move_group node name |
| `gz_world` | `ar3_workcell` | Gazebo world of scenario runs (sim) |

The token and switches come from the environment, never from parameters (`ros2 param get` would
show them to the whole graph):

| Variable | Default | Meaning |
|---|---|---|
| `COCKPIT_AGENT_TOKEN` | — | Shared secret; every request needs `Authorization: Bearer <token>`. Unset: every request gets 503. The Makefile passes `~/.ar3cockpit/agent.token`. |
| `COCKPIT_AGENT_BIND` | `127.0.0.1` | Bind address. The mac / vnc / remote services set `0.0.0.0` inside the container and publish the port on `127.0.0.1` only. |
| `COCKPIT_AGENT_PORT` | `8790` | Port |
| `COCKPIT_REMOTE_MOTION` | `0` | `1`: remote clients (the Mac in REMOTE) may send motion (spec §9.5) |
| `COCKPIT_HOST_LABEL` | host name | Name in `/health` and `/cockpit/policy` |

## HTTP API

| Endpoint | Answer |
|---|---|
| `GET /health` | `ready`, `waiting_for` and per component (`gazebo`, `controllers`, `move_group`, `cameras`, `safety`): `ready` / `waiting` / `warn` / `off` with a detail |
| `GET /capabilities` | mode, whether motion is allowed and why, `stop: true`, task types (C5) |
| `GET /controllers` | name, state and type of every controller |
| `GET /graph` | nodes, topics, services, actions |
| `GET /graph/detail` | the same, with publishers and subscribers per topic |
| `GET /node/params?name=/move_group` | a node's parameters, read-only (NaN / inf as text) |
| `GET /topic/rate?name=/clock&seconds=2` | measure one topic's rate (`ros2 topic hz`) |
| `GET /topics/rates` | rate and age of the last message of each `rate_topics` entry |
| `GET /cameras`, `GET /cameras/<name>/thumb.jpg` | cameras of `cameras.yaml`; the latest image as a JPEG (320 px, at most 5 new ones a second) |
| `GET /joints` | the last `/joint_states` (names, positions) |
| `WS /ws/telemetry?rate=10` | joint samples at the rate (a divisor of 100), every 100 ms a message; plus events (planned trajectory, detections, e-stop) and once a second topic rates and the RTF |
| `WS /ws/rosout?after=<seq>` | /rosout lines (time, level, node, text) |
| `POST /run` `{run_id, scenario, options}` | start a scenario run (409 while one runs) |
| `GET /run/<id>`, `POST /run/<id>/abort`, `POST /run/<id>/pause` `{paused}` | its state; abort (cancel + hold); pause before the next step |
| `WS /ws/run/<id>?after=<seq>` | the run's events (log, step, finished) |
| `POST /ik/check` `{targets: [{x, y, z, rpy_deg, frame?}]}` | collision-aware IK per target (`/compute_ik`, TCP `gripper_tcp`) |
| `POST /scene/apply` `{scenario}`, `POST /scene/clear` | scenario objects into / out of the planning scene and Gazebo (`scn_*`) |
| `POST /stop` | software stop (below): what it cancelled and held |
| `GET /arm/state`, `POST /arm/home`, `POST /arm/reset_estop` | 501 until Spec Phase 1 (`ar3_safety`) |

```bash
curl -s -H "Authorization: Bearer $(cat ~/.ar3cockpit/agent.token)" http://127.0.0.1:8790/health
```

Ready rules: Gazebo when `/clock` runs above 50 Hz and sim time advances (sim only); controllers
when `joint_state_broadcaster`, `joint_trajectory_controller` and `gripper_controller` are active;
move_group when the node is there with its `/move_action` server. Cameras compare the rate of each
`/<camera>/camera_info` with `rate` in `config/cameras.yaml` (warn below 80 %); they do not hold up
"ready", since software rendering on a Mac runs them at about 11 of 15 Hz.

**Telemetry** (`config/cockpit/telemetry.yaml`): a sampler thread takes the latest
`/joint_states` and the trajectory controller's state (reference = commanded) on a fixed 100 Hz
wall-clock grid (sample-and-hold, nothing older than 0.5 s); a client at 50 Hz gets every 2nd grid
point, at 10 Hz every 10th, so the cockpit's stored rate is exact. The node keeps the last second
of both message streams with their receive times, so a tick the sampler reaches late still gets
the state of its own time; only no joint states, or a pause of the agent over 1 s, is a gap
(those ticks are counted as `skipped`).
`/display_planned_path` becomes a `trajectory` event. `/safety/status` and `/perception/objects`
(ar3_interfaces, Spec Phases 1–2) are subscribed when they appear, with the type the graph
reports, so the agent does not depend on a package that does not exist yet. `/rosout` is
subscribed transient-local, so it also gets what nodes logged before the agent started. Camera
images are subscribed only while someone asks for thumbnails (dropped after 10 s).

**Scenario runs** (`runs.py`, pure helpers in `scenario.py`): reset (every `scn_*` planning-scene
object and Gazebo model removed), the start pose, the scene (collision objects in frame `table`;
in sim, Gazebo models for `spawn: true` with SDF generated from the shape, colour and an inertia of
500 kg/m³), then each `sequence` step: a move is planned (MoveGroup, `plan_only`; OMPL, or Pilz
LIN / PTP) and executed (ExecuteTrajectory) with a timeout of 3 × the planned duration; `wait`
sleeps; Pause holds before the next step; Abort cancels the goal and holds. At the end the
objects' Gazebo poses (table frame) for the cockpit's success criteria, and a cleanup unless
`keep_scene`. `record_bag` runs `ros2 bag record` (preset `default` of telemetry.yaml) for the
run. Gazebo: create / remove over gz-transport, model list and poses with the `gz model` CLI (a
Python gz subscription would block the requests: the binding keeps the GIL while it waits).

**Software stop** (not an e-stop): cancels all goals of every action server of type MoveGroup,
ExecuteTrajectory, FollowJointTrajectory and GripperCommand (MoveIt first, so it does not resend),
then publishes an empty trajectory on each `…/joint_trajectory` topic, which makes the trajectory
controller hold its current position.

**Motion policy** (`/capabilities`): mock and sim allow motion; real only when ARMED (Phase 1);
remote asks the workstation: every non-remote agent publishes `{host, mode, remote_motion, arm}`
latched on `/cockpit/policy`, and a remote agent allows motion only if that says
`remote_motion: true` (and ARMED on a real arm). The motion endpoints themselves arrive with C5.

## Design

rclpy runs in a `MultiThreadedExecutor` on a background thread; FastAPI / uvicorn (Ubuntu's
`python3-fastapi`, `python3-uvicorn`, WebSockets through `wsproto`, which python3-uvicorn pulls
in) in the main thread; the telemetry sampler in a third thread. A 1 s timer refreshes the controller
list and the move_group check, so `/health` only reads snapshots. Motion goes through the
`move_group` action interface, as `move_to_named` does (Phase 0 chose it; MoveItPy is not used).

## Tests

`test/test_health_unit.py`, `test/test_app_unit.py`, `test/test_telemetry_unit.py` and
`test/test_scenario_unit.py` (no ROS graph needed); `ar3_bringup/test/test_cockpit_agent_mock.py`
runs it in the mock bringup: a software stop during a motion, the telemetry stream on the 10 Hz
and 50 Hz grid, /rosout, the graph with publishers and subscribers, parameters, a 5-step scenario
run (named pose, OMPL and Pilz LIN poses, gripper, wait), IK of a reachable and an unreachable
target. All run in `make test`.
