# Troubleshooting

### `move_group` dies with "Segmentation fault" in `rclcpp::Executor::~Executor()` on Ctrl-C
This is a known crash in Jazzy move_group during shutdown. It happens after all work is done and
is harmless. The mock integration test ignores move_group's exit code for this reason.

### `Can't accept new action goals. Controller is not running.`
A goal was sent before the spawner had activated the controllers. In Gazebo this takes a few
seconds. `move_to_named` waits for the controllers on its own; for other clients, check
`ros2 control list_controllers` first.

### `mode:=real is not available yet`
This is intentional. The AR3 hardware interface arrives in Phase 1.

### xacro: `config_dir:=PATH_TO_CONFIG is required`
Pass `config_dir:=<repo>/config` to xacro. Launch files fill this in from `$AR3_CONFIG_DIR`
or from the installed copy.

### Gazebo: no camera images, or a very low rate
- Headless runs need EGL. The dev image includes Mesa (`libegl1`), which falls back to
  software rendering (llvmpipe) on hosts without a GPU. That is slow but works.
- On an NVIDIA host, use the `dev-gpu` compose service so rendering runs on the GPU.
- `gz topic -l | grep cam_` shows whether the sensors exist on the gz side.

### RViz or Gazebo window does not open from the container
The log says `Authorization required, but no authorization protocol specified` and
`qt.qpa.xcb: could not connect to display :0`, and RViz dies. The X server refuses the container.
The cockpit's checklist item *Desktop windows for Docker (xhost)* shows it, and its blue button runs
the fix. By hand: run `xhost +local:docker` on the host and check that `DISPLAY` is set. The grant
ends when you log out, so repeat it after every login. The compose file mounts
`/tmp/.X11-unix`. On macOS there is no X server by default: use `make sim-mac` with Lichtblick,
or `make vnc` for RViz in the browser (see usage-mac.md).

### Gazebo: after a world reset the arm is frozen
Gazebo's *Reset* (the GUI button, or `reset: {all: true}` on `/world/ar3_workcell/control`) is not
supported. Physics and `/clo
k` keep running, but the controller manager inside Gazebo
(`gz_ros2_control` 1.2.20 in Jazzy) stops. `/joint_states` goes silent,
`ros2 control list_controllers` finds no service, and MoveIt can no longer execute. Restart the
launch. To bring the arm back to a known pose without a restart, do not reset; use
`ros2 run ar3_bringup move_to_named home` instead.

### `render_engine:=ogre needs an X display`
OGRE 1 renders through GLX only and cannot run headless. Keep the default `ogre2`, which runs
headless with Mesa, or use `ogre` only where a display exists (Linux desktop, the `vnc` container).

### `ros2: command not found` in `docker compose exec`
`exec` skips the entrypoint. Start an interactive shell (`docker compose ... exec mac bash`); the
image sources ROS and the workspace in `/etc/bash.bashrc`. For a one-off command, use `bash -ic '…'`.

### Lichtblick: only table, bins and frame axes, no arm
The 3D panel is showing `/robot_description`, whose `file://` mesh paths exist only inside the
container. Use the layout `config/lichtblick/ar3_default.json`, which loads
`/viz/robot_description` (with `package://` paths served by the bridge). If the layout was imported
before 2026-11-01, import it again.

### Lichtblick: the arm looks disassembled
Every mesh is rotated 90° on its link. Set *3D panel → Scene → Mesh up axis* to *Z-up*. The layout
`config/lichtblick/ar3_default.json` does this; re-import it if you imported it before 2026-11-01.

### noVNC: "This site can't be reached"
- `make vnc` must still be running in its terminal, and the URL is http://localhost:6080/vnc.html.
- `make up-mac` / `make sim-mac` must not be running at the same time (see usage-mac.md); the make
  targets refuse to start in that case.
- `Xvnc did not start` in the output means the virtual display failed; the lines after it show
  why.

### Mac: camera images at 1–2 Hz
Docker on macOS has no GPU, so Gazebo renders with Mesa llvmpipe. This is expected. Motion and
physics are not affected.

### Zenoh: `Unable to connect to any of [tcp/…:7447]`
The router is not reachable. Check that `make zenohd` runs on Workstation A, that `ZENOH_ROUTER` in
`docker/.env` is Workstation A's address, and that TCP port 7447 is open in its firewall. Nodes on
Workstation A must also use `RMW_IMPLEMENTATION=rmw_zenoh_cpp` to be visible.

### DDS: nodes on two machines do not see each other
Use the same `ROS_DOMAIN_ID` on both machines and run the container with `network_mode: host`
(the compose file does this). Multicast must be allowed on the wired link.

## AR3 Cockpit

### `./cockpit.sh` stops after the checks
A check failed; its fix is printed below it (commands after `$`). Commands with `sudo` are yours
to run, the cockpit never runs them. Then start `./cockpit.sh` again.

### The browser shows "The cockpit backend is not reachable"
`./cockpit.sh` is not running (or Docker, and with it the database, stopped). Start it again; it
prints the failing check.

### "No cockpit user yet" / the login only offers "Create the first user"
The first user can be created only from the cockpit host (http://127.0.0.1:8400/setup-account). From another
machine, or headless: `tools/cockpit/backend/.venv/bin/ar3ctl create-user <name>`.

### "Too many failed sign-ins"
Five failed attempts for one user from one IP within 5 minutes lock that combination until the
window has passed; the message says how long. Another user can reset the password under Settings
→ Users.

### `COCKPIT_DB_PASSWORD is not set`
The backend was started without `.env` secrets. Run `./cockpit.sh` once; it generates them. If
`.env` was deleted while the database volume still exists, the new password does not match: put
the old value back, or remove the volume (`docker volume rm ar3cockpit_pgdata`, deletes all
cockpit data).

### Port 8400, 5445 or 6380 already in use
Another program uses it. Change `COCKPIT_PORT`, `COCKPIT_DB_PORT` or `REDIS_URL` in `.env`.

### `move_to_named` moves slower than before (2026-11-01)
Before 2026-11-01 it ran at full joint speed by mistake (see cockpit-c1.md). It now uses the site
scaling from `config/robot.yaml` (0.2). For faster moves in simulation, set its
`velocity_scaling` parameter (see the ar3_bringup README). If it fails with "move_group has no robot_description_planning…", the
move_group was not started by `ar3_bringup`.

### Install & Setup: "Another job is running"
One changing step runs at a time. The message links to the running job; wait for it or cancel it
there. A job that was running when `./cockpit.sh` stopped is marked failed on the next start.

### Install & Setup: images show "rebuild needed (built before the hash label existed)"
Images built before 2026-11-01 carry no `ar3.dockerfile_hash` label. Run *Build the dev image* (or
`make image`, `make image-vnc`) once; only the last layers are rebuilt.

### "A ROS stack is already running (…)"
`make up-mac`, `sim-mac`, `vnc`, `up-linux`, `sim-linux` and `remote` refuse to start while
another stack runs: they would share ports 8765 / 8790 and the ROS graph. Stop it with `make down`
(or Ctrl-C in its terminal), or use **Stop and switch** on the Environment page.

### `make up-linux` / `make sim-linux`: no RViz or Gazebo window
Allow the container to use your X display once per login: `xhost +local:docker`, and check that
`echo $DISPLAY` is set. Without a display use `HEADLESS=1` and Lichtblick.

### Environment: the agent shows "Wrong agent token" / "token mismatch"
The stack was started with another token than `~/.ar3cockpit/agent.token`, e.g. before the
cockpit ran the first time, or with another `COCKPIT_DATA_DIR`. `make restart` keeps the old
environment; stop and start it instead (`make down`, then start it again from the cockpit or the
CLI).

### Environment: the agent stays "waiting" / "Agent not reachable"
The workspace was built before `ar3_cockpit_agent` existed: the bringup prints
`ar3_cockpit_agent is not built` and runs without it. Run `make ws-build` (Install & Setup shows
"Workspace: rebuild needed"). An image without FastAPI (`ModuleNotFoundError: fastapi` in the
container log) needs `make image` / `make image-vnc`.

### Environment: a start job ends with "not ready after 180 s"
The job prints what it was still waiting for. Once on the Mac (1 of 9 starts of
`AR3_MODE=sim make vnc` on 2026-11-01) the controllers were never loaded although Gazebo ran; the
same start right after was ready in 10–15 s. Stop and start again; if it repeats, look at
`docker compose -f docker/compose.yaml logs <service> | grep -i spawner`. The stack keeps running
after the job fails, so you can inspect it.

Cause found on 2026-11-03 (Mac, `make vnc` right after `make test`, Docker VM load ~22 on 10
CPUs): `controller_manager: Switch controller timed out after 5 seconds!`, then `spawner … Failed
to activate controller : joint_state_broadcaster` and `process has died`. The activation waits for
the controller manager's first update cycle, which came late while Gazebo was still loading its
camera sensors. The same activation a minute later took 0.7 s. The spawner in
`bringup.launch.py` now waits up to 60 s (`--switch-timeout 60`). A stack started before that
change: stop and start it again.

### Header Stop: "Software stop failed"
The agent did not answer (see above). The software stop needs the agent; in an emergency use the
hardware e-stop.

### Header ERROR, alert `container_down`: "The ROS stack died"
The stack container exited abnormally (killed, crashed, out of memory: exit code 137 is SIGKILL
or OOM) and is still there. Monitoring → Logs with source `container:ar3-<service>-1` shows its
last lines (also `docker logs ar3-<service>-1`). `make down` removes it, which resolves the alert;
then start again. A stack stopped with Ctrl-C or `docker stop` (exit 0 / 130 / 143) is not "down".

### Monitoring → Telemetry: "No ROS stack is running" / empty plots
The plots come from the agent through `run_ingest`. Check that `./cockpit.sh` shows no
`run_ingest` errors and that the agent is `ready` (Environment → Components). A stack started
before C4 runs an agent without the telemetry stream: restart it (Stop, Start). In a slow browser
tab the plots can fall behind; the server then drops messages for that tab ("over capacity" in the
cockpit log), which costs nothing else.

### REC is on but History shows "Nothing stored"
REC was off for that session (REMOTE starts with REC off; `TELEMETRY_RECORD_SIM=0` turns it off
for SIM), or `run_ingest` is not running. `ar3ctl sessions` lists the sessions with their sample
counts. While the database is slow the ingest stores 10 Hz instead of 50 Hz (alert `ingest_lag`);
the session's "dropped" counts what it left out.

### Alert `disk_low`
Less than 15 GB are free on the repo's disk. Docker images and build cache are the usual cause
(`docker system prune`); on a Mac also Docker Desktop's disk image. Old rosbags are in
`data/bags/`, exports in `data/exports/`.

### Monitoring → ROS Graph: a node's parameters show "(not readable)"
The node lists a parameter that it then does not serve (move_group does this for some
`robot_description_planning.*` joint limits). The others are shown; nothing is wrong.

### Simulations: a run fails with "planning failed: FAILURE" on a Pilz LIN step
Pilz checks the joint limits along the straight line; near the wrist's limits it refuses, e.g.
"Joint acceleration limit of joint_5 violated" in the stack's log (Monitoring → Logs, source
`container:`). Lower `acceleration_scaling` (the site default 0.2 works for the examples), or use
`planner: ompl` for that step.

### Simulations: "Gazebo did not spawn scn_…"
Gazebo did not accept the model within 5 s. Check that the stack is SIM · GAZEBO and ready, and
that `gz model --list` in the stack's container answers. A run removes all `scn_*` models first
and replaces one of the same name, so a crashed run's leftovers do not block the next one: the
next run removes them.

### Simulations: Run is disabled / "Cannot run"
The message says why: no stack, a different mode running than the scenario lists, the agent not
answering, motion not allowed (REMOTE without `COCKPIT_REMOTE_MOTION=1` on A, REAL not ARMED), or
a task type the agent does not run yet (`pick_place` / `sort`: Spec Phase 3).

### `make bag`: "No ROS stack is running"
`make bag` records inside the running stack's container. Start a stack first; the bag goes to
`data/bags/<time>_<preset>/` and appears under Telemetry → History → Recordings.

### Assistant: "Ollama is not reachable at http://127.0.0.1:11434"
Start the Ollama app (or `ollama serve`). The AI checks under Install & Setup show what is missing.
Ollama runs natively, outside Docker; `OLLAMA_BASE_URL` points elsewhere if needed.

### Assistant: "Ollama: model 'x' not found"
The task's model is not pulled. Settings → AI → Pull (or `ollama pull x`), or choose an installed
model for the task. The error text comes from Ollama.

### Assistant: the footer says "Anthropic is not used …"
The task or the conversation asks for Anthropic, but `ASSISTANT_ALLOW_EXTERNAL=1` or
`ANTHROPIC_API_KEY` is missing in `.env`, so Ollama answered. Both are needed; restart
`./cockpit.sh` after editing `.env`.

### Assistant: "Knowledge index: full-text only"
The embedding model (`bge-m3` by default) was not available when the index was built. Pull it,
then Settings → AI → Rebuild index (or `make assistant-index FULL=1`). Answers still work; the
search only matches words, not meaning.

### Assistant: answers are slow, or "did not answer within … s"
A model larger than the GPU (or than ~¾ of a Mac's memory) runs on the CPU. Settings → AI shows a
hint per model. Choose a smaller model or raise the task's timeout. Reasoning models (gpt-oss,
deepseek-r1) think before the first word appears ("Thinking…").

### Assistant: "at most 20 questions per minute"
The per-user limit `ASSISTANT_RATE_PER_MINUTE`. Wait a minute or raise it in `.env`.
