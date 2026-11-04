# AR3 Cockpit

A local web app that puts the ways of running the AR3 stack behind one UI. The spec is
[spec_cockpit.md](../specs/spec_cockpit.md); the changelogs per phase are
[C1](../changelog/cockpit-c1.md), [C2](../changelog/cockpit-c2.md),
[C3](../changelog/cockpit-c3.md), [C4](../changelog/cockpit-c4.md),
[C5](../changelog/cockpit-c5.md) and [C6](../changelog/cockpit-c6.md).

**Current state: phases C1 to C6.** What works:
- sign-in, the first-user setup, user management and the audit log;
- the header, which shows the running ROS stack live: it is read from Docker, so stacks started
  from the CLI show up too;
- **Install & Setup**: a checklist of everything the stack needs, steps that fix what they can
  (with a live log), and "Continue setup", which runs the next one;
- **Environment**: start, stop and "stop and switch" any stack of this host with the exact command
  shown, component health, the component catalogue with its CLI commands and a diagram, the
  viewers (Lichtblick web too), the header's software stop, and the REAL pre-flight wizard (fake
  mode until Spec Phase 1);
- **Monitoring**: host and container metrics, logs, live telemetry with the 3D robot, the ROS
  graph, alerts, jobs, and data capture into TimescaleDB with exports;
- **Simulations**: MoveIt scenarios with an editor, reachability check, runs with live steps and
  evaluation, history, compare and pack-and-go;
- the **assistant** (✦): answers about ROS 2, MoveIt, Gazebo, the AR3 and this repo from the repo's
  docs, with sources; local Ollama or, if you allow it, the Anthropic API (see
  [Assistant](#assistant)).

## Start

Requirements: Docker (daemon running, usable without sudo), [uv](https://docs.astral.sh/uv/), git,
make, and Node.js ≥ 22 for the first frontend build.

```bash
./cockpit.sh          # or: make cockpit
```

`cockpit.sh`:
1. creates the Python environment (`tools/cockpit/backend/.venv`, Python 3.12, via uv);
2. runs the pre-Docker checks (`ar3ctl doctor --pre`) and stops with the fix commands if one fails;
3. writes generated secrets to `.env` in the repo root on the first run (`DJANGO_SECRET_KEY`,
   `COCKPIT_DB_PASSWORD`, `COCKPIT_DB_READER_PASSWORD`; the file is git-ignored, mode 600);
4. starts the database and Redis (compose profile `cockpit`);
5. builds the frontend when `out/` is missing or older than its sources, and runs the migrations;
6. runs the web server and the two workers (monitor, jobs) until Ctrl-C.

Open http://127.0.0.1:8400. On the first run there is no user yet: the login offers
**Create the first user**. This works only from the cockpit host itself (localhost). The
alternative is the CLI:

```bash
tools/cockpit/backend/.venv/bin/ar3ctl create-user <name>     # asks for the password
```

Ctrl-C stops the web server and the worker. The database and Redis keep running in Docker. Stop
them with `./cockpit.sh down`; the data stays in the Docker volume `ar3cockpit_pgdata`.

The cockpit itself runs natively on the host, not in Docker, so it can see the real OS and
control containers without mounting the Docker socket. Only the database and Redis are
containers. They are part of the `ar3` compose project, but they are not a ROS stack, and the
one-stack-per-host rule does not apply to them.

| Process | What it does |
|---|---|
| web (`uvicorn config.asgi:application`) | Django + REST API + WebSockets; serves the built frontend |
| `run_monitor` | every 2 s: reads the stack from Docker, component health, evaluates the alert rules, pushes the header status; host / container / GPU metrics (1 s / 5 s); follows the logs of every container and /rosout into the log buffer; hourly clean-up (audit 1 year, job logs, resolved alerts 90 days, log lines over 50 000 per source) |
| `run_jobs` | runs the steps you start and streams their output: one changing job at a time; an export or a rosbag recording runs next to it |
| `run_ingest` | while a stack runs: its session, the agent's telemetry into TimescaleDB (50 Hz while REC is on), the 10 Hz live stream for the Telemetry plots |
| `cockpit-db` | PostgreSQL 17 + TimescaleDB + pgvector, `127.0.0.1:5445` |
| `cockpit-redis` | Redis 7 (WebSocket fan-out, status cache), `127.0.0.1:6380` |

## Using it

**Header** (always visible, updated over a WebSocket every 2 s):

| Element | Meaning |
|---|---|
| status pill | `OK` (a stack runs and every component it needs is ready), `WARN` (something is still starting or below expectations, the agent does not answer, more than one stack runs, or a `warn` alert is active), `ERROR` (Docker not reachable, a container died, the agent refuses the token, or an `error` / `critical` alert is active), `OFFLINE` (no stack). Hover for the reason; the number next to it counts active alerts and links to them. Slow cameras and a missing Ollama are shown in the health table but do not count. |
| mode chip | `STOPPED`, `SIM · MOCK`, `SIM · GAZEBO`, `REMOTE`, `ROS · DEV` (an interactive `make shell` container: the cockpit cannot see what runs inside), `REAL · AR3`; `STARTING…` / `STOPPING…` / `RESTARTING…` while such a job runs. Hover for the container and the command that started it. |
| host chip | OS, version and CPU architecture of the cockpit host |
| viewer icons | Lichtblick web (opens `http://<host>:8080/` connected to the bridge), Lichtblick desktop (shows the `ws://<host>:8765` URL to paste), RViz via noVNC (opens `http://<host>:6080/vnc.html`), Gazebo GUI (a note: headless except `make sim-linux`). Grey when not running. |
| **Stop** | Shown while a stack runs. A *software* stop, not an e-stop: the agent cancels every goal of MoveIt (`/move_action`, `/execute_trajectory`), the trajectory controller and the gripper, then sends the trajectory controller an empty trajectory, so it holds where it is. A message says how many goals were cancelled. Disabled while the agent does not answer. |
| arm chip | REAL only: `NOT HOMED`, `HOMED`, `ARMED`, `E STOP` (blinking), `FAULT` |
| **REC** | while a stack runs: red when the session is recorded to the database, outlined when not. Click to switch. On in REAL and (by default) SIM, off in REMOTE. |
| ✦ | the assistant drawer ([Assistant](#assistant)) |
| user menu | Settings, theme (System / Light / Dark, one click each), change password, sign out |

`<host>` in the viewer links is the host name your browser used for the cockpit, so the links also
work from another machine on the LAN.

**Environment** has the tabs *Start & stop*, *Components* and *Viewers* under the running stack, and a
*Log* tab while a start or stop job is shown.

*Running stack* (top): the mode, the container and the command that started it, also for a stack
started in a terminal, since the cockpit reads it from Docker. *Ready*, or what it still waits for.
**Stop** (`make down`, REAL asks to confirm), **Restart** (`make restart`: the same container
again), and on REAL the **Pre-flight wizard**. A start or stop job opens the *Log* tab, which is
exactly one screen tall: the log takes the remaining height and is the only thing that scrolls (so no
second scrollbar). Switch to another tab to use it; *Open the full log* leads to the job page.

*Start & stop*: one form. Robot: Mock, Gazebo, or on a Mac *Workstation A (REMOTE)*. On a Mac a
switch for RViz in the browser; on Linux the viewers (windows / RViz in the browser / none) and
`GPU=1`. A switch for Lichtblick web. Under the form, the command the cockpit will run, live, e.g.
`AR3_MODE=sim make vnc DETACH=1`; the job runs exactly that. `DETACH=1` starts the stack in the
background (without it the same command runs in a terminal until Ctrl-C). The job is done when
the agent reports the stack ready (Gazebo clock, the three controllers active, move_group's
`/move_action`), and fails after 180 s otherwise.

While a stack runs, *Start* becomes **Stop and switch**: after a confirmation the job runs
`make down && <new command>`. Starting a second stack is refused otherwise (HTTP 409 from
`/api/env/start`, and the Make targets refuse it too). On a Mac:

| Robot | RViz in browser (:6080) | Lichtblick (:8765) | Command |
|---|---|---|---|
| mock | no | yes | `make up-mac` |
| Gazebo (headless) | no | yes | `make sim-mac` |
| mock | yes | yes | `make vnc` |
| Gazebo (headless) | yes | yes | `AR3_MODE=sim make vnc` |
| Workstation A's (REMOTE) | — | A's bridge | `make remote` |

On Linux, see [Linux modes](#linux-modes). REAL cannot be started yet: it needs the hardware
interface and `ar3_safety` (Spec Phase 1).

*Components*: the health table (§9.2 of the spec: ROS container, agent, Gazebo, controllers,
move_group, cameras, safety, foxglove_bridge, Lichtblick web, noVNC, zenohd, the cockpit's DB and
Redis, Ollama), a diagram of what runs where in the selected mode (pick another mode to preview
it), and one entry per component: what it does, ports, dependencies, docs and its CLI commands
(start, stop, logs, status, shell). The catalogue is `config/cockpit/components.yaml`; the same
content as Markdown is [cockpit-components.md](cockpit-components.md).

*Viewers*: Lichtblick web and noVNC as large buttons, plus the Foxglove WebSocket URL for Lichtblick
desktop. A fourth card, *RViz / Gazebo windows*, explains that these are native windows on the
workstation's desktop (never inside the cockpit), whether the running stack has them, and the
commands to get them on Linux (`xhost +local:docker` once per login, `make up-linux`,
`make sim-linux`). On a Mac it points to noVNC and Lichtblick instead. *Start Lichtblick web* runs `make lichtblick`. Lichtblick web is the
pinned image `ghcr.io/lichtblick-suite/lichtblick:1.30.0` with the repo layout
(`config/lichtblick/ar3_default.json`) mounted as its default layout, so nothing needs importing.

**Pre-flight wizard** (`/environment/preflight/`, REAL only): devices → e-stop test → workspace
clear → homing → pose check (the URDF at the measured joint angles) → limits (from
`config/robot.yaml`) → **Arm**. Each step needs the ones before it, and every action is in the
audit log with your user name. The e-stop test waits up to 60 s for a press, then for the
release after *Reset*. An e-stop or a lost agent later sends the wizard back to the e-stop test.
Until Phase 1 brings `ar3_safety`, the real agent has no arm state: on a real host the e-stop test
fails with a pointer to `docs/hardware.md`, and only `COCKPIT_ALLOW_ARM_WITHOUT_ESTOP=1` (permanent
red banner) skips it. Homing and arming also wait for Phase 1. Fake mode simulates all of it.

**Monitoring** has six tabs (the tab is in the URL, e.g. `/monitoring/?tab=logs`):

- *Overview*: mode, active alerts by severity, host uptime, Gazebo's clock; CPU, memory and free
  disk of this host; CPU and memory per container (`docker stats`, every 5 s); on Linux the GPUs
  (`nvidia-smi`: utilisation, memory, temperature); the health table in short.
- *Logs*: one list for every container (`docker logs`), `/rosout` (with node and level), the jobs
  and the cockpit itself. Filter by source (`container:` = all containers), minimum level, node,
  text or regex, time range. *Live tail* adds new lines as they come; switch it off to read.
  *Download* gives the filtered lines as a text file. The buffer keeps 7 days and at most 50 000
  lines per source; `docker logs` stays the source of truth. The error patterns of the job page
  also run here: an `error` / `critical` match raises the alert `log_error_pattern`.
- *Telemetry → Live*: the last 60 s at 10 Hz: joint positions, velocities, commanded vs. actual
  (pick a joint; commanded is the trajectory controller's reference), gripper position; the 3D
  view at the current joints with the last planned path (orange, from `/display_planned_path`)
  and detected objects (`/perception/objects`, Phase 2); topic rates of
  `config/cockpit/telemetry.yaml`; the `/safety/status` timeline (REAL, Phase 1); camera
  thumbnails (Gazebo cameras now). *Freeze* stops the plots, *CSV of the window* downloads what
  they show. Hover a plot for the values.
- *Telemetry → History*: the recorded sessions. Pick one for its facts (mode, start / end, user,
  commit, samples stored, dropped), the joint positions over the whole session (averaged per
  bucket), *Export Parquet / CSV* (a job: `make telemetry-export SESSION=…`; the files are listed
  with download links), and the recordings (rosbags). *Record a rosbag (60 s)* runs
  `make bag DURATION=60` (MCAP in `data/bags/`).
- *ROS Graph*: nodes and topics (type, publishers / subscribers, measured rate) with search;
  ⏱ measures one topic's rate for 2 s (`ros2 topic hz`). Click a node for a diagram of the topics
  it subscribes (left) and publishes (right) and its parameters, read-only.
- *Alerts*: open alerts with severity, subject, first and last time, count; *Acknowledge* (with an
  optional comment, recorded in the audit log), *Acknowledge all*; the history (resolved, 90 days);
  the rules. New `error` and `critical` alerts also pop up as a toast on every page, and as a
  browser notification if you switch that on here.
- *Jobs*: every job with status, duration, user and command (search, status, user filters); open
  its log, *Re-run* it with the same parameters.

Alert rules (`config/cockpit/alerts.yaml`, evaluated every 2 s):

| Rule | Fires when | Severity |
|---|---|---|
| `container_down` | a container exited abnormally (killed, crashed: not exit 0 / 130 / 143) or keeps restarting, and its stack was running | error |
| `topic_stale` | no `/joint_states` for more than 1 s | error |
| `estop_active` | the e-stop is pressed (REAL) | critical |
| `joint_near_limit` | a joint is within 5° of a limit in `config/robot.yaml` | warn |
| `camera_calibration_drift`, `camera_settings_mismatch` | calibration drift, UVC settings (Phase 2: no data yet, never fires) | warn |
| `gpu_hot` | a GPU above 85 °C | warn |
| `disk_low` | less than 15 GB free | warn |
| `log_error_pattern` | an error pattern matched a container or `/rosout` line | the pattern's |
| `ingest_lag` | the telemetry ingest is more than 10 s behind (it then stores 10 Hz) | warn |

An alert is *active*, then *acknowledged* by someone, and *resolved* when its condition clears;
one alert per rule and subject. A log match resolves 10 minutes after the last match. A
`make down` removes the dead container, which resolves `container_down`.

**Data capture** (spec §12a). Every run of a stack is a *session* (also when started in a
terminal): host, mode, `sim` or `real`, the git commit, a hash of `config/`, who started it.
While REC is on, `run_ingest` stores joint positions / velocities / efforts and the commanded
position and velocity (`joint_samples`, one row per joint), the gripper (`gripper_samples`),
`/safety/status` (`safety_samples`, REAL), topic rates and the real-time factor (`metrics`),
planned trajectories (`trajectories`) and events (`events`: session start / end, alerts, REC
switched, e-stop). The rate is `TELEMETRY_RATE_HZ` (50): the agent samples on a fixed 20 ms grid,
so 10 minutes are 30 000 samples per joint, and a missing sample is a real gap. If the database
falls more than 10 s behind, the ingest stores only the 10 Hz grid points until it catches up
(counted as dropped, alert `ingest_lag`); it never slows the agent or ROS.

The tables are TimescaleDB hypertables, compressed after one day; `metrics` is kept 30 days,
`log_lines` 7, everything else forever (`TELEMETRY_RETENTION`). 1 s and 1 min joint statistics
are continuous aggregates (`joint_stats_1s`, `joint_stats_1m`). For notebooks there is a read-only
role `ar3_reader` (password `COCKPIT_DB_READER_PASSWORD` in `.env`):

```python
import pandas as pd, psycopg  # both in the cockpit's venv (tools/cockpit/backend/.venv)
dsn = "host=127.0.0.1 port=5445 dbname=ar3cockpit user=ar3_reader password=<COCKPIT_DB_READER_PASSWORD>"
with psycopg.connect(dsn) as conn:
    cur = conn.execute("SELECT time, joint, position, cmd_position FROM joint_samples "
                       "WHERE session_id = %s ORDER BY time", ["<session id>"])
    df = pd.DataFrame(cur.fetchall(), columns=[c.name for c in cur.description])
```

An export (`make telemetry-export SESSION=<id> [FORMAT=parquet|csv] [FROM=… TO=…]`, or the History
tab) writes `data/exports/<start>_<mode>_<id>/` with `session.json` and `joint_samples`,
`gripper_samples`, `safety_samples`, `events` as Parquet (`pd.read_parquet(...)`) or CSV, times in
UTC. Camera images and whole message streams are not in the database: record a rosbag
(`make bag PRESET=default|cameras DURATION=60`, presets in `config/cockpit/telemetry.yaml`); the
cockpit lists it under recordings.

**Simulations** (spec §12): MoveIt scenarios, one YAML file each in `config/scenarios/`
(reviewed like code; the schema is `tools/cockpit/backend/scenarios/scenario.schema.json`). Poses
are in the `table` frame (centre of the table top, z up), metres and degrees.

```yaml
id: tour-three-objects
name: Tour over three objects
modes: [sim_gazebo, sim_mock]
start: {named_pose: home}
scene:
  objects:
    - {id: cube_red, shape: box, size: [0.03, 0.03, 0.03], color: red, pose: {x: 0.25, y: -0.15, yaw_deg: 15}, spawn: true}
task:
  type: sequence
  steps:
    - move_named: observe
    - move_pose: {x: 0.10, y: -0.10, z: 0.20, rpy_deg: [180, 0, -90]}            # OMPL
    - move_pose: {x: 0.10, y: -0.10, z: 0.12, rpy_deg: [180, 0, -90], planner: pilz_lin}
    - gripper: open
    - move_named: home
success: {all_steps_succeed: true, max_duration_s: 300}
```

- *List*: name, tags, modes, task type, the last run, version; search, tag filter; Run, Edit,
  Duplicate, Export (the YAML), Delete (asks); *Import YAML* (validated; asks before replacing);
  *Pack* / *Unpack* (pack-and-go, below). Examples: `tour-three-objects` (the C5 acceptance),
  `unreachable-demo` (a pose out of reach), `sort-three-colors` (a `sort` task: shown as not
  runnable until Spec Phase 3).
- *Editor* (`/simulations/edit/?id=…`; without `id`: a new one): **Form** (metadata, start pose,
  steps with add / remove / reorder, planning, success), **Scene** (the table from above: drag
  objects, rotate them with the handle, add box / cylinder / sphere / obstacle; bins from
  `config/bins.yaml`, the arm's base, a dashed ring for its approximate reach from the URDF, and
  the move_pose targets numbered by step), **YAML** (CodeMirror; problems are underlined with the
  line), **3D preview** (the arm at the start pose with the objects). All four edit the same
  text, so comments in the YAML stay. The scenario is checked as you type: schema, unique ids,
  objects on the table and not overlapping each other or a bin, named poses and gripper states
  from the SRDF, joints within `config/robot.yaml`, the gripper within the URDF's range,
  referenced files and bins, the Gazebo world, modes this host can start, task types the agent
  runs (`sequence` now; `pick_place` / `sort` need RunTask, Spec Phase 3), REAL only with
  `COCKPIT_REAL_SCENARIOS=1` and without `randomize`. **Save** is blocked while there are errors
  and bumps `version`. **Check reachability** (needs a running stack) asks MoveIt's IK, collision
  aware, for every `move_pose` target and names the steps it cannot reach.
- *Run* (`/simulations/run/?id=…`): mode (what the running stack runs), repeat, seed (for
  `randomize`), keep the scene, record a rosbag; **Run** starts a job
  (`make scenario-run ID=…`). The panel shows the steps live, Pause (before the next step) /
  Resume, **Abort** (cancels the goal and holds, like the header's Stop), the 3D view with the
  planned path, a joint plot and the run's log; at the end the success criteria one by one. The
  run is recorded (REC switches on). What the agent does: remove every `scn_*` planning-scene
  object and Gazebo model, move to the start pose, add the objects as collision objects and, in
  SIM · GAZEBO, spawn the `spawn: true` ones as Gazebo models (`scn_<id>`), then plan and execute
  each step (a move is planned first and executed with a timeout of 3 × the planned duration, on the simulation clock in Gazebo),
  read the objects' final Gazebo poses, clean up unless *keep the scene*.
- *History*: per scenario every run with version, mode, user, start, duration, result, failed
  step; tick two to **compare** them step by step (status, planning and execution time) with
  their criteria. Artefacts in `~/.ar3cockpit/runs/<run id>/`: `scenario.yaml` (resolved, after
  `randomize`), `run.log`, `events.jsonl`, `metrics.json`, `run.json`.
- Success criteria: `all_steps_succeed`; `objects_in_bins` (`{object: bin}`, the object's final
  Gazebo position inside the bin's inner volume: SIM · GAZEBO only, "skipped" in mock);
  `max_duration_s`.

**Pack-and-go** (scenarios from the Mac to Workstation A and back): tick scenarios, *Pack* gives
`ar3-scenarios-<host>-<time>.zip` (the YAMLs, `config/bins.yaml` when referenced,
`config/robot.yaml` for comparison, meshes, a manifest with host, commit, versions, checksums).
*Unpack* on the other cockpit shows per scenario new / unchanged / differs (with the diff) and
validation problems, and warns when the table or the bins differ from the pack's; choose keep,
replace or import as a copy per scenario. Config files are never overwritten. From a terminal:
`make scenario-pack IDS="a b" OUT=pack.zip`, `make scenario-unpack FILE=pack.zip` (a preview;
`REPLACE=1` or `COPY=1` imports).

**Install & Setup** lists every check in groups: Host, Prerequisites, Docker, GPU (Linux), Repo
config, Images, Workspace, Tests, Simulation smoke, Viewers, Network, Hardware (Linux), AI. Each
row shows what was found. The summary on top counts "x / y done" (skipped items are not counted).
- **A blue button** runs the step that fixes the item, e.g. *Build the dev image* (`make image`).
  It opens the job page with the live log. Afterwards the item is checked again.
- **How to fix** shows what to do by hand: commands that need `sudo` (the cockpit never runs or
  asks for sudo), or programs to install.
- **Run it yourself** shows the terminal commands for the check and for its fix.
- **Continue setup** runs the next failing item that has a step and whose prerequisites pass,
  in the order of the list. When nothing is left it says so, and names what you still have to do
  by hand.
- **Re-check all** / ↻ run the checks again (each check is read-only and gets 10 s).

The steps the cockpit can run:

| Step | Command | Fixes |
|---|---|---|
| Create docker/.env | `cp docker/.env.example docker/.env` | docker/.env |
| Build the dev image | `make image` | Dev image |
| Build the noVNC image | `make image-vnc` | noVNC image |
| Build the workspace | `make ws-build` | Workspace built |
| Run the tests | `make test` | Last make test |
| Simulation smoke test | `make smoke-sim` | Simulation smoke |
| Pull the Lichtblick web image | `make pull-viewers` | Lichtblick web image |
| Allow Docker windows on this desktop (Linux) | `xhost +local:docker` | Desktop windows for Docker (xhost) |
| Pull an Ollama model | `ollama pull <model>` | Chat and embedding models |

The Environment page uses four more steps: `env.start` (`make <target> DETACH=1`, see above),
`env.stop` (`make down`), `env.restart` (`make restart`) and `viewers.lichtblick`
(`make lichtblick`). Monitoring has two: `telemetry.export` (`make telemetry-export SESSION=…`)
and `rosbag.record` (`make bag PRESET=… DURATION=…`); Simulations one: `scenario.run`
(`make scenario-run ID=…`).

Only one changing job runs at a time; starting a second one says which job is running. Exports
and rosbag recordings change nothing the other jobs use, so they run next to them. Things that need
root (install Docker, the NVIDIA driver and toolkit, the docker/dialout/video groups) appear only
as instructions. The Hardware checks for the Teensy, the Nano, the cameras and the udev rules are
skipped until Phase 1 confirms the device IDs.

How some checks decide:
- **Images** are up to date when their label `ar3.dockerfile_hash` matches the hash of
  `docker/Dockerfile`, `entrypoint.sh` and `vnc-start.sh` (`make dockerfile-hash`). Images built
  before 2026-11-01 have no label and show "rebuild needed" once.
- **Workspace** needs a rebuild when a `CMakeLists.txt`, `package.xml`, `setup.py` or C++ /
  interface file in `src/` is newer than `install/setup.bash`. Python, launch and config files
  are symlinked and need none.
- **Last make test** reads the result files in `build/*/test_results/` (also from a `make test`
  run in a terminal). It counts test cases, so it shows 29 where colcon's summary says 34
  (colcon adds one entry per test file). Results older than the last commit are a warning.
- **Simulation smoke** reads `.cockpit/results/smoke-sim.json`, which `make smoke-sim` writes.
- **Ports** 8765 / 6080 / 8080 are fine when free or held by an `ar3` container.

**Job page** (`/setup/job/?id=…`): the command, status, exit code and the log, live. If a line
matches a known error (`config/cockpit/error_patterns.yaml`: Docker permissions, disk full, port in
use, another stack running, missing ROS package, colcon failure, NVIDIA, network, test failures),
a box above the log names the problem, gives a hint and, where one exists, a button for the step
that fixes it. Click the quoted line to jump to it in the log. The log has search (text or
`/regex/`), "Only errors", follow mode, copy and download. **Cancel** stops the job's whole process
group (SIGTERM, SIGKILL after 10 s). Job logs are kept 30 days or the last 200 jobs, whichever is
more. While a job runs, the header shows a spinner that links to it.

**Settings → Users**: add users, reset a password, delete a user. You cannot delete yourself or the
last user. There are no roles: every user can do everything.

**Settings → Audit log**: sign-ins (also failed ones), sign-outs, password changes, user changes,
jobs started and cancelled, with time, user and IP. Entries are kept one year.

## Assistant

The ✦ button in the header opens the assistant on the right; it stays open while you change
pages. It answers questions about ROS 2 Jazzy, MoveIt 2, Gazebo Harmonic, ros2_control,
Lichtblick, noVNC, Zenoh, Docker, the AR3 and this repository. It is **advisory**: it cannot run
anything. When it proposes a step (e.g. *Build the workspace*), the step is a button; it runs
only after you confirm, as a normal job with its log. Any other command is shown to copy.

**Where to ask.** Type in the drawer (Enter sends; on a job page or in the scenario editor the
question carries that job or scenario along), or use an entry point:

| Where | Button | What the assistant gets |
|---|---|---|
| Job page, bar above the log | **AI: Explain warnings/errors** (also for succeeded jobs) | the step, command, exit code, every distinct `[WARN]` / `[ERROR]` / `[FATAL]` line with its count, the error-pattern matches. The answer sorts each line into *Harmless*, *Worth fixing* and *Needs action*. |
| Job page, error box | **Ask the assistant** | the matched line, the hint, the job |
| Job log | right-click a line → **Ask the assistant about this line** | the line and the job |
| Install & Setup | ✦ on a check | the check, its result and its commands |
| Environment | **Explain this mode** | the mode of the form and its command |
| Scenario editor | **Help me write this scenario** (with what you want changed) | the YAML in the editor, the schema, the site's poses and bins |

**Answers.** Sources are numbered: `[1]` in the text opens the source list (path and heading). If
the docs do not cover a question, the answer says so and marks general knowledge. Reasoning
models (deepseek-r1, gpt-oss, qwen3) think first: that text is hidden behind **Show reasoning**
and never sent back in later turns. The footer shows provider, model, the number of sources and
the time, plus notes: Anthropic not allowed so Ollama answered, the index is full-text only, the
context had to be shortened. **Stop** ends an answer; what came so far is kept.

**Scenario help** returns a whole scenario. It is validated against the schema and shown as a
diff; **Apply to the editor** puts it into the editor. Nothing is saved until you click Save.

**Every question carries a context pack**, built by the server: the page, host, OS, the running
mode and arm state, failing checks, active alerts, the job's last 50 warning/error lines (or the
distinct ones), the scenario, and up to 8 sources from the index (plus excerpts of the code or
config files the log names, e.g. `src/…/app.py:42`). It is limited to ~6000 tokens: older log
lines go first, then long texts are shortened, then the lowest-ranked sources. Secrets are
removed before anything is sent or stored (values of `*_KEY`, `*_TOKEN`, `*_PASSWORD`, `SECRET*`
settings and `sk-ant-…` keys become `«redacted»`); `.env` is never read into it. Each answer stores
its pack with the conversation.

**Conversations** are per user; the drawer lists yours. The processor icon in the drawer's header switches
provider and model for one conversation. Conversations untouched for `AI_LOG_DAYS` (180) are
deleted. At most `ASSISTANT_RATE_PER_MINUTE` (20) questions per user and minute.

**Settings → AI**:
- one card per task: *Chat* (the drawer), *Explain warnings/errors* (the job entry points),
  *Scenario help*, *Embeddings*. Provider, model and parameters (temperature, max tokens,
  `num_ctx`, timeout), each with where its value comes from (`default`, `defaults.env`,
  `settings` = this page, `.env`, `env`). A model set in `.env` or the environment wins over this
  page. Saving applies to the next question, no restart. **Test** sends a short prompt and shows
  the latency and the first words (for Anthropic it also checks the model id);
- the installed Ollama models with size, quantisation and whether they fit this machine (e.g.
  gpt-oss:20b needs ~17 GB: fine on a 64 GB Mac, on the CPU on an 8 GB GPU). The hint does not
  block anything. **Pull** downloads another model (step `ollama.pull`, a job with a log);
- the Anthropic switch (read-only: set it in `.env`);
- the knowledge index: files, chunks, how many have embeddings, the model, **Rebuild index**.

**Providers.** Ollama (`OLLAMA_BASE_URL`) by default; any pulled model works. The Anthropic API
answers only with `ASSISTANT_ALLOW_EXTERNAL=1` *and* `ANTHROPIC_API_KEY` in `.env`; otherwise the
cockpit uses Ollama and says so in the footer. With Anthropic, the question, its context pack and
the sources leave this machine (secrets removed). If the provider is down, the answer says so and
links to the AI checks.

**The knowledge index** holds the README, `docs/**/*.md`, the package READMEs, `config/**/*.yaml`
and Markdown in `tools/cockpit/knowledge/`, minus `ASSISTANT_INDEX_EXCLUDE` (your notes in
`docs/notes/`, and the AR4 driver in `src/external/`, whose hardware values are not the AR3's).
Markdown is split at headings, YAML at its top-level entries (one chunk per component of the
catalogue). Search combines Postgres full-text and pgvector similarity. `run_jobs` re-indexes
changed files when it starts; a new embedding model re-indexes everything. Without the embedding
model the index is full-text only (marked in Settings → AI and in the footer). By hand:

```bash
make assistant-index           # changed files only
make assistant-index FULL=1    # every file, embeddings too
```

## Login screen

The left half plays the AR3 model from `ar3_description` in three.js. The right half will show a
video of the real arm doing the same motion. The video does not exist yet (**[OPEN]**), so the right half shows a grey copy of the model with "real video pending". The panel in
the middle can be dragged (grab it next to the fields; at least 48 px stay on screen); a double
click on an empty spot of it centres it again. Its footer links to the project's repository.

The model and the motion are files in `tools/cockpit/frontend/public/login/`:
- `ar3.urdf`: the expanded URDF with `package://` mesh paths. The backend serves the meshes from
  the repo under `/login-assets/meshes/`, without sign-in; they are the open-source AR4 meshes.
- `trajectory.json`: MoveIt plans for `home → observe → stow → home` with gripper open/close, at the
  site scaling from `config/robot.yaml` (0.2), about 38 s.

Regenerate both after changing the robot description or `config/`. This runs a mock stack in the
dev image, isolated from any running stack:

```bash
make cockpit-login-assets
```

To add the video, film the real arm running the same `trajectory.json` (needs Phase 1). Save it as
`public/login/real.mp4` (H.264, muted, ≤ 8 MB) and optionally `poster.jpg`, then rebuild. The page
plays the video in step with the model.

Fallbacks:
- without WebGL, `public/login/still.png` is shown if present;
- with "reduce motion" set in the OS, both halves hold still.

## Configuration

Precedence, highest first: process environment > `.env` (repo root) >
`tools/cockpit/backend/defaults.env`. Every key is documented in `defaults.env`. The ones you
are most likely to change:

| Key | Default | Meaning |
|---|---|---|
| `COCKPIT_BIND` | `127.0.0.1` | `0.0.0.0` opens the cockpit to the LAN. Sign-in is always required. |
| `COCKPIT_PORT` | `8400` | web port |
| `COCKPIT_DATA_DIR` | `~/.ar3cockpit` | runtime data: job logs (`logs/jobs/`), fake-mode state |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama, checked under AI |
| `ASSISTANT_PROVIDER` | `ollama` | the assistant's default provider; Settings → AI changes it per task |
| `ASSISTANT_CHAT_MODEL` / `ASSISTANT_EMBED_MODEL` | `llama3.1:8b` / `bge-m3` | default chat and embedding models; Settings → AI overrides them (a value in `.env` wins over the UI) |
| `ASSISTANT_ALLOW_EXTERNAL` | `0` | `1` allows the Anthropic API (also needs `ANTHROPIC_API_KEY` in `.env`) |
| `ANTHROPIC_MODEL` | `claude-sonnet-5-5` | the Anthropic model id |
| `ASSISTANT_RATE_PER_MINUTE` | `20` | questions per user and minute |
| `AI_LOG_DAYS` | `180` | days an untouched assistant conversation is kept |
| `ASSISTANT_INDEX_EXCLUDE` | `docs/notes/,src/external/` | paths the knowledge index leaves out |
| `COCKPIT_FAKE` | `0` | `1` simulates Docker, ROS and devices (see below) |
| `COCKPIT_SECURE_COOKIES` | `0` | `1` behind an HTTPS reverse proxy |
| `TELEMETRY_RATE_HZ` | `50` | stored joint / gripper rate (10, 20, 25, 50 or 100) |
| `TELEMETRY_RECORD_SIM` | `1` | REC on by default in SIM sessions |
| `TELEMETRY_RETENTION` | `metrics=30,log_lines=7` | days per hypertable; others are kept |
| `LOG_KEEP_PER_SOURCE` | `50000` | log buffer lines per source |
| `COCKPIT_EXPORT_DIR` | `data/exports` | where exports go (below the repo) |
| `COCKPIT_SCENARIOS_DIR` | `config/scenarios` | the scenario files (below the repo) |
| `COCKPIT_REAL_SCENARIOS` | `0` | `1` allows `real` in a scenario's modes (§12.5; REAL needs Phase 1 anyway) |

Restart `./cockpit.sh` after a change.

Security:
- sessions expire after 12 h without activity;
- after 5 failed sign-ins for a user from one IP within 5 minutes, sign-in is refused for the rest
  of that window;
- passwords need at least 10 characters, must not be a common password, and are stored with
  Argon2id;
- the database and Redis ports are published on 127.0.0.1 only.

## Fake mode

```bash
./cockpit.sh --fake
```

Docker, ROS, the host and the checkout are simulated, so the whole UI works without containers
(CI uses this). A yellow banner says so. The simulated world starts as a fresh clone on a healthy
host: no images, nothing built, Ollama without models. The steps print a few lines and change the
world as the real ones would, so "Continue setup" leads to all-green in about a minute. The state
lives in `$COCKPIT_DATA_DIR/fake/state.json` and the checkout in `$COCKPIT_DATA_DIR/fake/repo`.
Edit the state file to change the world; the header follows within 2 s, the checklist on
re-check.

```json
{"docker": true, "stack": {"service": "vnc", "ar3_mode": "sim"}, "arm": "NOT_HOMED",
 "images": {"ar3-dev": true}, "ollama": {"up": true, "models": []}, "ports_busy": [8765],
 "gpu": true, "fail": {"make smoke-sim": "port is already allocated"}}
```

- `stack: null` means nothing runs.
- `"docker": false` simulates Docker being down.
- `ports_busy`: ports held by a foreign program; `fail`: the command with this argv prints the
  line and exits 1.
- `COCKPIT_FAKE_PLATFORM` picks the simulated host: `macos`, `ubuntu`, or `ubuntu-real`. With
  `ubuntu-real` and `{"service": "dev", "ar3_mode": "real"}`, the header shows `REAL · AR3` and the
  arm state.

## CLI: `ar3ctl`

The same checks without the web UI (`tools/cockpit/backend/.venv/bin/ar3ctl`, or `uv run ar3ctl` in
`tools/cockpit/backend`):

```bash
ar3ctl doctor [--json]         # the whole Install & Setup checklist (exit 1 if a check fails)
ar3ctl doctor --pre [--json]   # only host, prerequisites, Docker
ar3ctl status [--json]         # host, overall status, running stack and its command, viewers
ar3ctl start --list            # the modes this host can start
ar3ctl start MODE [--lichtblick] [--switch]   # like the Environment page: make … DETACH=1, wait for ready
ar3ctl stop                    # make down
ar3ctl components [--markdown] # the component catalogue
ar3ctl create-user [NAME]      # needs the cockpit DB (./cockpit.sh once)
ar3ctl sessions [--json]       # recorded sessions, newest first
ar3ctl telemetry-export SESSION [--format parquet|csv] [--from T] [--to T]   # to data/exports/
ar3ctl telemetry-compress [--all]   # run the compression policies now (--all: every chunk)
ar3ctl scenarios [--json]            # the scenarios
ar3ctl scenario-validate ID|FILE     # validate (exit 1 with errors), with line numbers
ar3ctl scenario-run ID [--mode M] [--repeat N] [--seed S] [--keep-scene] [--bag]
ar3ctl scenarios-pack ID... [-o FILE]          # pack-and-go
ar3ctl scenarios-unpack FILE [--replace|--copy]   # without an option: a preview
```

`ar3ctl start` prints the command, runs it, and waits for the agent to report the stack ready:

```bash
$ ar3ctl start sim_gazebo
$ make sim-mac DETACH=1
…
waiting for gazebo (/clock 1 Hz (needs > 50)), controllers (joint_state_broadcaster missing, …)
ready. gazebo: /clock 510 Hz, controllers: 3 controllers active, move_group: /move_action available
```

`ar3ctl doctor` needs no database and gives the same results as the web page (the same code;
`--json` has the same fields as `GET /api/checks`). From the repo root: `make doctor`,
`make status-json`. `ar3ctl init-env` and `ar3ctl env-export` are used by `cockpit.sh`.

## The cockpit agent

`ar3_cockpit_agent` ([README](../../src/ar3_cockpit_agent/README.md)) runs inside every stack (the
bringup starts it, `cockpit_agent:=true`) and answers the cockpit on `127.0.0.1:8790`: health,
controllers, graph, node parameters, topic rates, camera thumbnails, joint states, software stop,
scenario runs (scene, steps, pause, abort), IK checks, and WebSockets for telemetry (joint samples
at 10 Hz for the plots, 50 Hz for the database), /rosout and runs. Every request needs the token in
`~/.ar3cockpit/agent.token`. The cockpit creates it on first use; the Makefile reads the same file
and passes it into every stack (`COCKPIT_AGENT_TOKEN`), so stacks started from a terminal are
reachable too. A stack started before the token existed answers 401: the health table says
"token mismatch"; restart the stack (Environment → Restart is not enough, use Stop and Start, or
`make down` and start again).

The Make targets for the cockpit:

| Target | Does |
|---|---|
| `make <stack target> DETACH=1` | start in the background (`up-mac`, `sim-mac`, `vnc`, `up-linux`, `sim-linux`, `remote`) |
| `… LICHTBLICK=1` | also start Lichtblick web |
| `make down` | stop and remove the running stack and Lichtblick web (not the cockpit's DB, not `zenohd`) |
| `make restart` | restart the running stack's container |
| `make remote` | REMOTE: only the agent, as a Zenoh client of Workstation A |
| `make lichtblick` | Lichtblick web on :8080 |
| `make pull-viewers` | pull the Lichtblick web image |
| `make cockpit-docs` | regenerate `docs/manual/cockpit-components.md` |
| `make bag [PRESET=default] [DURATION=60]` | record a rosbag (MCAP) in the running stack to `data/bags/` |
| `make telemetry-export SESSION=<id> [FORMAT=parquet]` | export a session to `data/exports/` |
| `make telemetry-compress [ALL=1]` | run the compression policies now |
| `make scenario-run ID=<id> [MODE=] [REPEAT=] [SEED=] [KEEP_SCENE=1] [BAG=1]` | run a scenario in the running simulation |
| `make scenario-pack IDS="a b" [OUT=…]`, `make scenario-unpack FILE=… [REPLACE=1\|COPY=1]` | pack-and-go |

## Development

```bash
cd tools/cockpit/backend && uv run pytest -q     # backend (needs the cockpit DB running)
cd tools/cockpit/frontend && npm run dev         # UI on :3000 against the backend on :8400
cd tools/cockpit/frontend && npm run gen:api     # after an API change: OpenAPI -> lib/api/schema.d.ts
make cockpit-test                                # backend tests + build + e2e (fake mode)
cd tools/cockpit/frontend && npm run test:real   # C3 + C4 acceptance on this Mac (real stacks, local only)
```

`npm run test:real` starts the e2e backend *without* fake mode: it starts and stops the four Mac
stacks through the UI, starts one from the CLI, and stops a running Gazebo motion with the header's
Stop (C3, `tests/real/mac.spec.ts`); then it records a 10-minute Gazebo session and checks the
stored rate, compression, the Parquet export, the /rosout search, the 10 Hz plots, and
`docker kill` of the stack (C4, `tests/real/c4.spec.ts`, about 13 min); then it checks the
reachability of `unreachable-demo` and runs `tour-three-objects` in Gazebo from the UI (C5,
`tests/real/c5.spec.ts`, about 2 min). It never touches hardware. Stop your own stack first; `KEEP_STACK=1` leaves the last stack
running afterwards.

The e2e tests (`tests/e2e/`, Playwright) start their own backend on port 8401, with their own
database `ar3cockpit_e2e`, which is recreated on every run. Your cockpit data is not touched.

Code:
- `tools/cockpit/backend` is the Django project. `core` has the command runner, host detection,
  fake mode and frontend serving; `accounts` users, auth and audit; `environment` the stack
  detection, modes, start/stop API, component health (`health.py`), the catalogue
  (`components.py`), the pre-flight wizard (`arm.py`), header status and `/ws/status`; `core/agent.py`
  the agent client (and its fake); `monitoring` the `run_monitor` worker, metrics, the log
  buffer and collector, the alert engine; `telemetry` sessions, `run_ingest`, history queries,
  export, retention and recordings; `scenarios` the schema, store, validation, randomization,
  evaluation, runs (`execute.py`) and pack-and-go; `setup` the
  checks (`setup/checks`), the step registry (`steps.py`), jobs (`service.py`, `executor.py`,
  `run_jobs`) and the error patterns; `ar3ctl` the CLI.
- `tools/cockpit/frontend` is the Next.js static export.
- `tools/cockpit/COLCON_IGNORE` keeps colcon out of it.

## Linux modes

Confirmed on 2026-11-01 (spec_cockpit.md §3.3). On Workstation A the Environment page
lists these rows; the header recognises each one when it runs.

| Robot | You see (+ Lichtblick :8765) | Command |
|---|---|---|
| mock | RViz window | `make up-linux` |
| Gazebo | Gazebo and RViz windows | `make sim-linux` |
| mock | Lichtblick only | `make up-linux HEADLESS=1` |
| Gazebo | Lichtblick only | `make sim-linux HEADLESS=1` |
| mock | RViz in the browser (:6080) | `make vnc` |
| Gazebo (software rendering) | RViz in the browser (:6080) | `AR3_MODE=sim make vnc` |

`GPU=1` runs the NVIDIA service (`linux-gpu`) instead, e.g. `make sim-linux GPU=1`. Windows need X
access for Docker, once per login: `xhost +local:docker`. The Environment form offers all of them
(viewers: windows / browser / none, GPU switch). REAL needs Phase 1. See [usage-sim.md](usage-sim.md).

**REMOTE** (on the Mac, `make remote`): no robot of its own. Only the cockpit agent runs, as a
Zenoh client of Workstation A (`ROS_RMW=rmw_zenoh_cpp`, `ZENOH_ROUTER` in `docker/.env`), so the
Mac's cockpit shows A's graph and controllers; Lichtblick links point at A's bridge. Motion from
the Mac (scenarios, C5) needs `COCKPIT_REMOTE_MOTION=1` in A's environment: A's agent publishes
that on `/cockpit/policy`, and the Mac's agent reports motion as not allowed without it (and on
the real arm, unless A is ARMED). Not tested against a real Workstation A yet.

## Not yet built

- UVC-settings and calibration-drift health and their alerts, thumbnail overlays, the gripper
  current (spec v0.4; need the real cameras and hardware, Phase 1/2).
- The LeRobot converter step (pairs an export with a LeRobot episode; needs LeRobot data, Phase 4).
- `pick_place` / `sort` tasks (RunTask, Spec Phase 3), `perception: ground_truth` detections and
  `camera` (ar3_interfaces, Phase 2), scenarios on the real arm (Phase 1; §12.5 also asks for the
  camera images next to the 2D view), mesh objects in Gazebo (planning scene only now).
- The Settings page for configuration values (later; Settings → AI covers the assistant's).
- Curated external docs for the assistant's index (`tools/cockpit/knowledge/`, spec §20 item 5:
  which ones is open).

## Logo

The logo files are in `tools/cockpit/frontend/public/brand/`. The build exports 512/192/32 px PNGs
and the web manifest into `public/icons/`.

The header and login set the wordmark as text in Inter, which is bundled with the frontend. The
wordmark inside the SVG files is still live text, not converted to outlines (§7.5). That needs
the Inter font files and an outline tool, and is open.
