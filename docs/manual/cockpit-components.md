# AR3 Cockpit: components

Generated from `config/cockpit/components.yaml` by `make cockpit-docs`; edit the YAML,
not this file. The cockpit shows the same under Environment → Components, with live
status ([cockpit.md](cockpit.md)).

## ROS core container

The Docker container that runs the whole ROS 2 stack: robot description, ros2_control, MoveIt, foxglove_bridge and the cockpit agent. Which compose service it is (mac, vnc, linux, linux-gpu, remote) decides the mode.

- **Runs in:** every_stack, remote
- **Ports:** —
- **Depends on:** —
- **Docs:** [docs/manual/usage-mac.md](usage-mac.md)

| | Command |
|---|---|
| start | `make sim-mac` |
| stop | `make down` |
| logs | `docker compose -f docker/compose.yaml logs -f <service>` |
| status | `docker compose -f docker/compose.yaml ps` |
| shell | `docker compose -f docker/compose.yaml exec <service> bash` |

## Gazebo server

Gazebo Harmonic simulates the physics and the two cameras. Headless on the Mac and in the vnc stack (watch it through RViz or Lichtblick); with a window from make sim-linux. ros_gz_bridge brings /clock and the camera topics into ROS.

- **Runs in:** gazebo
- **Ports:** —
- **Depends on:** ros
- **Docs:** [docs/manual/usage-sim.md](usage-sim.md)

| | Command |
|---|---|
| start | `make sim-mac` |
| stop | `make down` |
| logs | `docker compose -f docker/compose.yaml logs -f <service> | grep -i gz` |
| status | `docker compose -f docker/compose.yaml exec <service> bash -ic 'ros2 topic hz /clock'` |
| shell | `docker compose -f docker/compose.yaml exec <service> bash` |

## move_group (MoveIt 2)

Plans collision-free motions for the arm and the gripper and sends them to the controllers. RViz's MotionPlanning panel, move_to_named and the cockpit's scenarios (C5) all talk to its /move_action.

- **Runs in:** every_stack, real
- **Ports:** —
- **Depends on:** ros2_control
- **Docs:** [src/ar3_moveit_config/README.md](../../src/ar3_moveit_config/README.md)

| | Command |
|---|---|
| start | `make up-mac` |
| stop | `make down` |
| logs | `docker compose -f docker/compose.yaml logs -f <service> | grep move_group` |
| status | `docker compose -f docker/compose.yaml exec <service> bash -ic 'ros2 action list'` |
| shell | `docker compose -f docker/compose.yaml exec <service> bash -ic 'ros2 run ar3_bringup move_to_named home'` |

## ros2_control / controllers

The controller manager with joint_state_broadcaster, joint_trajectory_controller and gripper_controller. In mock it drives mock_components (no physics), in sim Gazebo, on the real arm the hardware interface (Spec Phase 1).

- **Runs in:** every_stack, real
- **Ports:** —
- **Depends on:** ros
- **Docs:** [src/ar3_bringup/README.md](../../src/ar3_bringup/README.md)

| | Command |
|---|---|
| start | `make up-mac` |
| stop | `make down` |
| logs | `docker compose -f docker/compose.yaml logs -f <service> | grep -i controller` |
| status | `docker compose -f docker/compose.yaml exec <service> bash -ic 'ros2 control list_controllers'` |
| shell | `docker compose -f docker/compose.yaml exec <service> bash` |

## ar3_safety

Watches the e-stop, the limits and the heartbeat on the real arm and owns the arm state (NOT HOMED, HOMED, ARMED, E-STOP). Arrives with Spec Phase 1; until then REAL cannot start.

- **Runs in:** real
- **Ports:** —
- **Depends on:** hardware
- **Docs:** [docs/specs/spec_cockpit.md](../specs/spec_cockpit.md)

| | Command |
|---|---|
| start | `# Spec Phase 1` |
| stop | `make down` |
| logs | `docker compose -f docker/compose.yaml logs -f <service> | grep safety` |
| status | `docker compose -f docker/compose.yaml exec <service> bash -ic 'ros2 topic echo /safety/status --once'` |
| shell | `docker compose -f docker/compose.yaml exec <service> bash` |

## Hardware interface

The ros2_control plugin that talks to the Teensy 4.1 over USB serial (ar3_hardware, Spec Phase 1). Not built yet; the serial protocol and pin map are open items.

- **Runs in:** real
- **Ports:** —
- **Depends on:** ros
- **Docs:** [docs/specs/spec_v0.4.md](../specs/spec_v0.4.md)

| | Command |
|---|---|
| start | `# Spec Phase 1` |
| stop | `make down` |
| logs | `docker compose -f docker/compose.yaml logs -f <service>` |
| status | `ls -l /dev/ttyACM*` |
| shell | `docker compose -f docker/compose.yaml exec <service> bash` |

## foxglove_bridge

A WebSocket server (port 8765) that serves the ROS graph to Lichtblick, web or desktop. It runs in every stack, so Lichtblick can always connect, also next to RViz.

- **Runs in:** every_stack
- **Ports:** 8765
- **Depends on:** ros
- **Docs:** [docs/manual/usage-mac.md](usage-mac.md)

| | Command |
|---|---|
| start | `make up-mac` |
| stop | `make down` |
| logs | `docker compose -f docker/compose.yaml logs -f <service> | grep foxglove` |
| status | `nc -vz 127.0.0.1 8765` |
| shell | `docker compose -f docker/compose.yaml exec <service> bash` |

## Lichtblick (web / desktop)

The 3D view, plots and camera images in the browser (:8080, container from ghcr.io/lichtblick-suite/lichtblick with the AR3 layout as default) or in the desktop app. Either one connects to foxglove_bridge at ws://<host>:8765.

- **Runs in:** optional
- **Ports:** 8080
- **Depends on:** foxglove_bridge
- **Docs:** [docs/manual/usage-mac.md](usage-mac.md)

| | Command |
|---|---|
| start | `make lichtblick` |
| stop | `make down` |
| logs | `docker compose -f docker/compose.yaml logs -f lichtblick` |
| status | `curl -sI http://127.0.0.1:8080/ | head -1` |
| shell | `docker compose -f docker/compose.yaml exec lichtblick sh` |

## noVNC + RViz

RViz with MoveIt's MotionPlanning panel on a virtual desktop, shown in the browser at :6080. Software rendering; plan and execute here and the arm moves (in Gazebo with AR3_MODE=sim).

- **Runs in:** vnc
- **Ports:** 6080
- **Depends on:** move_group
- **Docs:** [docs/manual/usage-mac.md](usage-mac.md)

| | Command |
|---|---|
| start | `make vnc` |
| stop | `make down` |
| logs | `docker compose -f docker/compose.yaml logs -f vnc` |
| status | `curl -sI http://127.0.0.1:6080/vnc.html | head -1` |
| shell | `docker compose -f docker/compose.yaml exec vnc bash` |

## zenohd

The Zenoh router on Workstation A (tcp/7447). The Mac's containers join A's ROS graph as Zenoh clients through it (REMOTE). Needs ROS_RMW=rmw_zenoh_cpp on both sides.

- **Runs in:** remote
- **Ports:** 7447
- **Depends on:** —
- **Docs:** [docs/manual/usage-mac.md](usage-mac.md)

| | Command |
|---|---|
| start | `make zenohd` |
| stop | `docker compose -f docker/compose.yaml stop zenohd` |
| logs | `docker compose -f docker/compose.yaml logs -f zenohd` |
| status | `nc -vz <ZENOH_ROUTER> 7447` |
| shell | `docker compose -f docker/compose.yaml run --rm mac ros2 topic list` |

## ar3_cockpit_agent

The cockpit's adapter inside the ROS stack (HTTP on 127.0.0.1:8790, token from ~/.ar3cockpit/agent.token): reports health and controllers, runs the software stop. Started by the bringup; in REMOTE it is the only node on the Mac.

- **Runs in:** every_stack, remote
- **Ports:** 8790
- **Depends on:** ros
- **Docs:** [src/ar3_cockpit_agent/README.md](../../src/ar3_cockpit_agent/README.md)

| | Command |
|---|---|
| start | `make up-mac` |
| stop | `make down` |
| logs | `docker compose -f docker/compose.yaml logs -f <service> | grep cockpit_agent` |
| status | `curl -s -H "Authorization: Bearer $(cat ~/.ar3cockpit/agent.token)" http://127.0.0.1:8790/health` |
| shell | `docker compose -f docker/compose.yaml exec <service> bash` |

## PostgreSQL / TimescaleDB

The cockpit's database (users, audit log, jobs; telemetry from C4). Compose profile cockpit, port 127.0.0.1:5445, started by ./cockpit.sh. Not a ROS stack.

- **Runs in:** cockpit
- **Ports:** 5445
- **Depends on:** —
- **Docs:** [docs/manual/cockpit.md](cockpit.md)

| | Command |
|---|---|
| start | `./cockpit.sh` |
| stop | `./cockpit.sh down` |
| logs | `docker compose -f docker/compose.yaml logs -f cockpit-db` |
| status | `docker compose -f docker/compose.yaml ps cockpit-db` |
| shell | `docker compose -f docker/compose.yaml exec cockpit-db psql -U ar3cockpit ar3cockpit` |

## Redis

Message bus of the cockpit's web process and workers (live logs, header status). Compose profile cockpit, port 127.0.0.1:6380.

- **Runs in:** cockpit
- **Ports:** 6380
- **Depends on:** —
- **Docs:** [docs/manual/cockpit.md](cockpit.md)

| | Command |
|---|---|
| start | `./cockpit.sh` |
| stop | `./cockpit.sh down` |
| logs | `docker compose -f docker/compose.yaml logs -f cockpit-redis` |
| status | `docker compose -f docker/compose.yaml exec cockpit-redis redis-cli ping` |
| shell | `docker compose -f docker/compose.yaml exec cockpit-redis redis-cli` |

## Cockpit web / workers

This app: Django on 127.0.0.1:8400 plus the workers run_jobs (runs steps like make sim-mac), run_monitor (health, alerts, metrics and logs every 2 s) and run_ingest (telemetry into TimescaleDB). Runs natively on the host, not in Docker, so it sees the real OS and needs no Docker socket in a container.

- **Runs in:** cockpit
- **Ports:** 8400
- **Depends on:** cockpit_db, cockpit_redis
- **Docs:** [docs/manual/cockpit.md](cockpit.md)

| | Command |
|---|---|
| start | `./cockpit.sh` |
| stop | `Ctrl-C in its terminal` |
| logs | `./cockpit.sh` |
| status | `make status-json` |
| shell | `make doctor` |

## Ollama

Local LLM server on the host (127.0.0.1:11434) for the assistant. Optional; install it yourself, the cockpit pulls the models.

- **Runs in:** optional
- **Ports:** 11434
- **Depends on:** —
- **Docs:** [docs/specs/spec_cockpit.md](../specs/spec_cockpit.md)

| | Command |
|---|---|
| start | `ollama serve` |
| stop | `# quit the Ollama app` |
| logs | `# macOS: ~/.ollama/logs/server.log` |
| status | `curl -s http://127.0.0.1:11434/api/tags` |
| shell | `ollama list` |
