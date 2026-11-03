# AR3 Cockpit — Web App Implementation Spec

Owners: Richard Petrasch, Roland Petrasch (see `LICENSE.md`).
Status: v0.4 (2026-11-01). Companion to `docs/specs/spec_v0.4.md` (main spec, currently v0.6). `spec_v0.4.md` below means that file.
Items marked **[OPEN]** need confirmation before the phase that depends on them.

Changelog:
- v0.4 (2026-11-01): multi-webcam setup instead of depth camera (spec_v0.4.md v0.6): camera checks, UVC settings lock, USB bandwidth, calibration status and drift (§8.1, §9.2, §10.5), multi-view thumbnails and pose check.
- v0.3 (2026-11-01, decisions after C1): Linux table confirmed with native RViz / Gazebo windows and new targets `up-linux` / `sim-linux` (§3.3); REMOTE allows motion and scenarios from the Mac (§9.5); scenario pack-and-go between hosts (§12.3, ToDo).
- v0.2: actual macOS start matrix and single-stack rule (§3); component catalogue with CLI equivalents (§3.3, principle 8); backend Django + PostgreSQL/TimescaleDB + Channels/Redis (§4–6, §11, §13); data capture (§12a); configurable LLM models per task (§14.3); logo (§7.5).
- v0.1: initial spec.

---

## 1. Purpose

The AR3 stack now has several ways to run: Linux or macOS, Gazebo or mock simulation, the real arm, and a remote view from the Mac via Zenoh. Each combines different compose profiles, launch arguments and viewers. Choosing the right one is hard to follow from the docs alone.

**AR3 Cockpit** is a local web app that puts these scenarios behind one UI:

| Menu | Job |
|---|---|
| **Install & Setup** | Bring a fresh clone to a working state; show what's done and what isn't (checklist); run setup steps with live logs and error detection |
| **Environment** | Detect the OS, offer the modes valid on it, start and stop containers and components, show health, link to viewers (Lichtblick, noVNC, …) |
| **Monitoring** | Status, logs, telemetry, alerts |
| **Simulations** | Create, edit, delete and run MoveIt 2 scenarios; keep a run history |

Plus: login (users, no roles), an always-visible status header, and an AI assistant (local Ollama or Anthropic API) for questions about ROS 2, MoveIt, Gazebo, Lichtblick, noVNC and the AR3 hardware and software.

## 2. Principles (binding)

1. **UI over existing tooling.** The cockpit calls the repo's existing Make targets, compose profiles and launch files. It never re-implements them. If a needed operation has no target yet, add the target to the Makefile first (usable from the CLI too), then call it.
2. **No arbitrary shell.** Every executable step is an entry in a whitelisted step registry (§8.2) with typed, validated parameters. There is no endpoint that runs a free-form command.
3. **No sudo.** Steps that need root (install Docker, the NVIDIA Container Toolkit, udev rules, the `docker` group) are shown as copyable commands with an explanation; the cockpit re-checks afterwards. It never stores or asks for a sudo password.
4. **Safety stays in ROS.** The cockpit never bypasses `ar3_safety`. Motion on the real arm goes through the same actions and services as any other client. The header **Stop** button is a software stop (cancel goals, hold position), labelled as such. It is **not** an e-stop and the UI says so.
5. **Same checks on CLI and UI.** Checks and steps live in a Python package that also has a CLI (`ar3ctl`), so a headless machine or CI can run `ar3ctl doctor` without the web UI.
6. **Fake mode.** A `COCKPIT_FAKE=1` mode simulates Docker, ROS and devices so the entire UI is testable in CI and on any laptop without containers.
7. **Docs describe what exists** (spec_v0.4.md §9a applies to the cockpit too).
8. **Command transparency.** Every action that runs something (check, step, start, stop, restart, scenario run) shows the exact equivalent CLI command with a copy button ("Run it yourself"), before and after it runs. The component catalogue (§3.3) explains what each container/component does, with its CLI commands. The cockpit is a teacher for the CLI, not a replacement.

## 3. Modes and start commands

### 3.1 Single-stack rule
Only **one** ROS stack may run per host. Two stacks would both bind port 8765 and join the same ROS graph, giving two MoveIts and two robots; `make vnc` already refuses to start while `make sim-mac` runs. The cockpit enforces the same rule:
- it detects a running stack (from containers and labels, also when started from the CLI);
- it offers "stop and switch" instead of starting a second one.

A stack is described by two independent choices:
- **Robot:** `mock` (`mock_components`, no physics) or `gazebo` (headless Gazebo: physics + cameras);
- **RViz in the browser:** yes / no.

`foxglove_bridge` (8765) runs in every stack, so Lichtblick can always connect, even at the same time as RViz.

### 3.2 macOS (Apple Silicon): confirmed

| Cockpit mode | Command | Robot | RViz (browser, :6080) | Lichtblick (:8765) |
|---|---|---|---|---|
| SIM · Mock | `make up-mac` | mock | no | yes |
| SIM · Gazebo | `make sim-mac` | Gazebo (headless) | no | yes |
| SIM · Mock + RViz | `make vnc` | mock | yes | yes |
| SIM · Gazebo + RViz | `AR3_MODE=sim make vnc` | Gazebo (headless) | yes | yes |

In the cockpit these are **one** "Start simulation" form with two controls (Robot: Mock / Gazebo; "RViz in browser" toggle). The form shows the resulting command live. With Gazebo + RViz: planning and executing in RViz moves the arm in Gazebo.

### 3.3 Ubuntu (Workstation A) and REAL / REMOTE
Confirmed (2026-11-01). On Linux the cockpit also offers native RViz and Gazebo
windows. The stacks run detached through `make up-linux` / `make sim-linux` (compose service
`linux`, or `linux-gpu` with `GPU=1`), so the cockpit can detect, start and stop them like the Mac
stacks. Windows need `xhost +local:docker` once per login.

| Cockpit mode | Command | Robot | Viewers (+ Lichtblick :8765) |
|---|---|---|---|
| SIM · Mock | `make up-linux` | mock | RViz window |
| SIM · Gazebo | `make sim-linux` | Gazebo (GPU with `GPU=1`) | Gazebo and RViz windows |
| SIM · Mock, headless | `make up-linux HEADLESS=1` | mock | — |
| SIM · Gazebo, headless | `make sim-linux HEADLESS=1` | Gazebo | — |
| SIM · Mock + RViz in browser | `make vnc` | mock | RViz in the browser (:6080) |
| SIM · Gazebo + RViz in browser | `AR3_MODE=sim make vnc` | Gazebo (software rendering) | RViz in the browser (:6080) |
| **REAL · AR3** | Phase 1 (`mode:=real`; devices, pre-flight wizard §9.4) | real arm | RViz window |
| **REMOTE** (on macOS) | `mac` profile, `rmw_zenoh_cpp` client to `zenohd` on Workstation A | (A's) | Lichtblick via A's bridge |

`GPU=1` (the `dev-gpu` base with NVIDIA) combines with every Linux row. The single-stack rule
(§3.1) covers all of them; the Make targets refuse to start a second stack.

A simpler way to watch the real arm from the Mac is to open Workstation A's cockpit in the Mac browser (LAN bind, §11). REMOTE mode is for running your own nodes, motions and scenarios on the Mac against the live graph (§9.5).

### 3.4 Component catalogue (GUI page "Environment → Components")
For every container and component, a card with:
- **What it does:** 2–3 sentences in plain language.
- **When it runs:** which modes include it.
- **Ports / URLs.**
- **Depends on:** other components.
- **CLI:** start, stop, logs, status and shell commands, copyable (e.g. `make vnc`, `docker compose logs -f ros`, `docker compose exec ros bash`, `ros2 node list`).
- **Docs:** link.
- **Live status:** from §9.2.

The source is one file, `config/cockpit/components.yaml` (also used by the assistant index and `docs/manual/cockpit.md`, so the GUI and docs never diverge). Initial entries:
- ROS core container
- Gazebo server
- `move_group` (MoveIt 2)
- `ros2_control` / controllers
- `ar3_safety`
- hardware interface
- `foxglove_bridge`
- Lichtblick (web/desktop)
- noVNC + RViz
- `zenohd`
- `ar3_cockpit_agent`
- PostgreSQL/TimescaleDB
- Redis
- cockpit web / workers
- Ollama

A diagram view on the same page shows which components run in the selected mode and how they connect (ports, topics).

## 4. Architecture

```
 Browser (any host)
   │  HTTPS/HTTP + WebSocket (same origin)
   ▼
 ┌──────────────────────────── host (native, not in Docker) ─────────────────────────────┐
 │ cockpit web: Django 5 (ASGI, uvicorn) + DRF + Channels; serves built frontend          │
 │   ├─ auth/users (django.contrib.auth, no groups/permissions used)                      │
 │   ├─ API: checks, steps, jobs, env, monitoring, scenarios, assistant                   │
 │   └─ WebSocket consumers (status, jobs, logs, telemetry, runs)                         │
 │ cockpit workers (manage.py commands, native processes):                                │
 │   ├─ run_jobs     job runner ──► make / docker compose / docker CLI                    │
 │   ├─ run_monitor  health polling, metrics, alert engine, log collector                 │
 │   └─ run_ingest   telemetry ingest from agent ──► TimescaleDB (batched COPY)           │
 └───────┬───────────────────────────────┬────────────────────────────────────────────────┘
         │ HTTP + WS (localhost, token)  │ SQL / pub-sub
         │                     ┌─────────▼──────────── cockpit infra (Docker, compose profile `cockpit`) ┐
         │                     │ PostgreSQL 17 + TimescaleDB + pgvector  (timescale/timescaledb-ha)    │
         │                     │ Redis 7 (Channels layer, job wake-ups)                                 │
         │                     └────────────────────────────────────────────────────────────────────────┘
         ▼
 ┌────────────── ROS container (ar3 dev/runtime image) ───────────────┐
 │ ar3_cockpit_agent (rclpy + FastAPI)                                │
 │   graph introspection, topic rates, controller states,            │
 │   joint_states / safety / diagnostics / rosout relay (downsampled),│
 │   scenario apply (planning scene, Gazebo spawn), run tasks         │
 │   (MoveToNamed, MoveGroup goals, RunTask), software stop           │
 └────────────────────────────────────────────────────────────────────┘
```

**Why the backend runs natively on the host**
- OS detection must see the real host. Inside Docker on macOS everything looks like Linux.
- Container control without mounting the Docker socket into a container, which would be root-equivalent.

**Bootstrap order (database in Docker).** Postgres/Timescale and Redis run as containers, so Docker is a hard prerequisite of the cockpit itself:
1. `cockpit.sh` checks the pre-Docker items in the terminal: git, Python ≥ 3.12 / `uv`, Node 22, Docker installed + daemon reachable + usable without sudo. Same code as `ar3ctl doctor --pre`. If anything is missing it prints the install commands and stops.
2. It starts the `cockpit` compose profile (DB + Redis), waits for health, runs `migrate`, then starts the web and worker processes.
3. Everything after that is done in the GUI. The Install & Setup page also shows the pre-Docker items as already-passed checks.

**Why there's a ROS-side agent:** rclpy isn't available natively on macOS, and ROS introspection, actions and planning-scene edits belong inside the ROS graph. The agent is the only ROS-aware component, and the backend talks to it over HTTP/WS.

## 5. Tech stack

| Layer | Choice | Note |
|---|---|---|
| Frontend | Next.js 16 (`output: "export"`, static), React 19, TypeScript, HeroUI 2.8, Tailwind CSS v4, TanStack Query 5 | Visual language: blue→purple project gradient, 56 px top bar, HeroUI components |
| 3D | three.js + `@react-three/fiber` + `urdf-loader` | Login background, pose check, scenario preview; loads URDF/meshes from `ar3_description` via the backend |
| Charts | uPlot | High-rate telemetry |
| Logs | virtualized list (`react-virtuoso`) + ANSI colour rendering | |
| YAML editor | CodeMirror 6 with YAML mode + JSON-Schema validation | Scenario raw editor |
| Backend | Python 3.12, Django 5.2 LTS (ASGI via uvicorn), Django REST Framework, `drf-spectacular` (OpenAPI), Django Channels 4 + `channels-redis`, `psycopg` 3, Argon2 password hasher | Env-file layering, workers as management commands, one launcher script |
| Database | PostgreSQL 17 + TimescaleDB + pgvector, image `timescale/timescaledb-ha` (pinned tag), host port 5445 (5444 is left free for other local Postgres instances) | ORM for app data; hypertables for time series (§12a); pgvector for the assistant index (§14.2) |
| Cache/pub-sub | Redis 7 (pinned) | Channels layer; worker wake-ups |
| Process control | `asyncio.create_subprocess_exec` (never `shell=True`) | Through one `CommandRunner` interface with a fake implementation |
| Agent | rclpy, FastAPI, uvicorn inside the ROS image | New colcon package `ar3_cockpit_agent`. It stays FastAPI: it is a thin in-container adapter with no DB, not part of the Django app |
| Packaging | `uv` for Python; npm for the frontend | |

**Independent code base.** Do not copy code from other (proprietary) projects into it. Write everything here, or take it from dependencies with compatible licenses, recorded in `THIRD_PARTY_NOTICES.md`.

## 6. Repository layout (additions)

```
ar3_ws/
├── cockpit.sh                         # bootstrap: checks prerequisites, creates venv, builds frontend, starts backend
├── tools/cockpit/                     # COLCON_IGNORE inside
│   ├── COLCON_IGNORE
│   ├── backend/                      # Django project
│   │   ├── pyproject.toml
│   │   ├── manage.py
│   │   ├── config/                   # settings, envfiles.py (§13), asgi.py, urls.py, routing.py
│   │   ├── core/                     # CommandRunner, platform detection, fake/ (COCKPIT_FAKE), agent_client
│   │   ├── accounts/                 # users, login/throttling, first-user setup, audit log (§7.4, §11.3)
│   │   ├── setup/                    # checks (§8.1), steps (§8.2), jobs + run_jobs (§8.3)
│   │   ├── environment/              # modes, components catalogue, health, viewers, arm/pre-flight (§3, §9)
│   │   ├── monitoring/               # metrics, logs, alerts, run_monitor (§10)
│   │   ├── telemetry/                # hypertables, run_ingest, export (§12a)
│   │   ├── scenarios/                # schema, store, runs (§12)
│   │   ├── assistant/                # providers, index, context pack, threads (§14)
│   │   ├── ar3ctl/                   # CLI entry (`ar3ctl doctor|create-user|start|stop|status`)
│   │   └── tests/
│   └── frontend/
│       ├── app/                      # login, setup, environment, monitoring, simulations, settings
│       ├── components/               # shell/, status/, logs/, three/, scenarios/, assistant/
│       ├── lib/api/                  # typed client generated from the backend's OpenAPI
│       ├── public/login/             # sim.json trajectory, real.mp4, poster.jpg
│       └── tests/                    # Playwright e2e (fake mode)
├── src/ar3_cockpit_agent/            # ROS 2 package (Python)
└── config/
    ├── scenarios/*.yaml              # simulation scenarios (§12)
    ├── cockpit/alerts.yaml           # alert rules (§10.3)
    └── cockpit/error_patterns.yaml   # log error matchers (§8.3)
```

Runtime data (not committed):
- `~/.ar3cockpit/` → `logs/jobs/*.log`, `runs/` (scenario run artefacts), `agent.token`;
- DB volume `ar3cockpit_pgdata`.

The compose profile `cockpit` (DB + Redis) is added to the existing compose file. It runs independently of the ROS stacks and is **not** affected by the single-stack rule (§3.1).

## 7. Shell, header, login

### 7.1 Layout
- **Top bar** (56 px), left to right:
  - logo and app name
  - **horizontal main menu**: Install & Setup · Environment · Monitoring · Simulations
  - status cluster (§7.2)
  - assistant toggle
  - user menu (change password, logout, Settings)
- **No sidebar by default.** Pages that need sub-navigation use tabs inside the page.
- **Assistant** opens as a right-side drawer that survives navigation.
- Responsive down to 1280 px wide (laptop). Mobile is not a target, but must not break.
- Dark and light theme follow the OS, with a toggle.
- i18n-ready (message catalogue); English only in v0.1.

### 7.2 Header status cluster (always visible, live via WebSocket)
| Element | Content |
|---|---|
| **Overall status pill** | `OK` (green) / `WARN` (amber) / `ERROR` (red) / `OFFLINE` (grey); worst of component health + active alerts; click → Monitoring |
| **Mode chip** | `STOPPED` · `SIM · GAZEBO` · `SIM · MOCK` · `REAL · AR3` · `REMOTE`; transitional states shown with spinner (`STARTING…`, `STOPPING…`) |
| **Host chip** | OS + arch (`Ubuntu 24.04 · x86_64`, `macOS 15 · arm64`) |
| **Arm state** (REAL only) | `NOT HOMED` / `HOMED` / `ARMED` / `E-STOP` (red, blinking) / `FAULT` |
| **Viewer links** | Icon buttons for running viewers (Lichtblick, noVNC, Gazebo GUI note); disabled with tooltip when not running |
| **Stop** | Visible whenever ROS is running: cancels all active goals and asks `ar3_safety` to hold. Tooltip: "Software stop — use the hardware e-stop in an emergency." |
| **Job indicator** | Spinner + count when a setup/start job runs; click → job log |

### 7.3 Login screen
- Full-screen **split background**: left half the **simulated** AR3, right half the **real** AR3, both doing the same motion in sync.
  - **Left:** live three.js rendering of the AR3 URDF from `ar3_description`, playing `public/login/trajectory.json` (joint positions with timestamps, looped).
  - **Right:** `public/login/real.mp4` (muted, looped, `playsinline`, ≤ 8 MB, H.264) recorded while the real arm executed the **same** trajectory. Playback is aligned to the trajectory clock.
  - A thin vertical divider with labels "Simulation" / "Real".
  - Fallbacks:
    - no video → poster image;
    - no WebGL → static render image;
    - `prefers-reduced-motion` → both still.
  - A script `tools/cockpit/scripts/record_login_trajectory.py` generates `trajectory.json` from a named-pose sequence through MoveIt in sim, and can execute the same trajectory on the real arm for filming (hardware flag required, §Spec 9).
  - **[OPEN]** Ownefilm `real.mp4`; until then the right half shows a placeholder render with "real video pending".
- **Centre:** half-transparent panel, draggable, with brand, a one-line tagline, the login card (username, password, submit), and a small footer link to the project repository. No company branding.
- Errors: wrong credentials (401) → generic message; throttled (429) → "too many attempts, wait N s".

### 7.4 Users (no roles)
- Every user can do everything. There is an audit log (§11.3) instead of permissions.
- **First run:** if no user exists, `/setup-account` lets you create the first user. It is reachable **only from localhost**. Alternatively run `ar3ctl create-user`.
- **User management** (Settings → Users): list, create, delete (not yourself, not the last user), reset another user's password.
- Password rules: ≥ 10 characters, not in a common-password list. Hashed with argon2id.
- Implementation: `django.contrib.auth` `User`; groups and permissions unused. Every API view requires `IsAuthenticated`. `manage.py create_admin` / `changepassword` commands.

### 7.5 Logo
- Files in `frontend/public/brand/` (supplied; keep the geometry, polish allowed):
  - `ar3-cockpit-logo.svg` (mark + wordmark, light background);
  - `ar3-cockpit-logo-dark.svg` (dark background);
  - `ar3-cockpit-mark.svg` (mark only; login, collapsed header);
  - `favicon.svg` (simplified, no ticks or joints, thicker strokes).
  - Generate PNG exports 512/192/32 for the web manifest at build time.
  - The wordmark uses Inter. Convert the text to outlines for the final SVGs so it doesn't depend on installed fonts.
- Concept: a cockpit dial (270° arc gauge) with a three-segment robotic arm whose gripper reaches the gauge's "needle" position. Colours: the project gradient `#0d6efd → #6f42c1`; the arm in the gradient, the dial in a neutral tone that works on light and dark backgrounds.
- Usage:
  - header 28 px mark + wordmark;
  - login panel 64 px mark + wordmark;
  - favicon mark only;
  - minimum mark size 16 px (the 16 px variant drops the dial ticks).

## 8. Install & Setup

### 8.1 Checklist
One page, grouped. Each item has: `id`, `title`, `group`, `status` (`pass` / `warn` / `fail` / `skip` / `unknown` / `running`), `detail` (what was found), `fix` (a step id **or** manual instructions with copyable commands), `docs` link, `last_checked`. A **Re-check all** button runs every check; individual items can be re-checked too. Checks are idempotent, read-only and finish within 10 s each (time out → `unknown`).

| Group | Checks (Linux = L, macOS = M) |
|---|---|
| **Host** | OS + version supported (L: Ubuntu 24.04; M: macOS ≥ 14, Apple Silicon), CPU arch, RAM ≥ 16 GB (warn below), free disk ≥ 40 GB (warn) / 15 GB (fail), git present, repo clean or not (info) |
| **Prerequisites** | Python ≥ 3.11, `uv`, Node ≥ 22 (only to rebuild the frontend), `make` |
| **Docker** | Docker installed; daemon reachable; user can run Docker without sudo (L: `docker` group); Compose v2; buildx; M: Docker Desktop or OrbStack detected, VM memory ≥ 8 GB (warn) |
| **GPU** (L) | `nvidia-smi` works; driver version; NVIDIA Container Toolkit (`docker run --gpus all … nvidia-smi` dry check via step, cached); GPU memory |
| **Repo config** | `.env` exists (else offer to create it from `.env.example`); required keys set; `config/*.yaml` present and schema-valid |
| **Images** | Dev/runtime image built for this arch; image label `ar3.dockerfile_hash` matches the current Dockerfile hash (else `warn: rebuild needed`) |
| **Workspace** | colcon workspace built inside the container (`install/setup.bash` present in the volume, build newer than `src/` changes → else warn) |
| **Tests** | Last `make test` result (pass/fail, date, commit); stale if the commit changed |
| **Simulation smoke** | Last sim smoke result (headless bring-up, MoveIt reaches `home`) |
| **Viewers** | Lichtblick web image present; noVNC service builds; ports 8765/8080/6080 free or used by our containers |
| **Network** (optional) | `zenohd` reachable at the configured address (REMOTE); Workstation B reachable (later phases) |
| **Hardware** (L, REAL only, `skip` otherwise) | udev rules installed; Teensy (`/dev/ttyACM*` with expected VID:PID) and Nano present; each configured camera (overhead, side 1–2, wrist) present under its stable udev name, supports MJPEG at the configured resolution/fps, UVC manual focus/exposure/white balance applied and read back; cameras spread over USB controllers (warn if ≥ 3 cameras share one controller, from `lsusb -t`); calibration files present for every fixed camera and the wrist hand-eye, with age and reprojection error; user in `dialout` group |
| **AI** | Ollama reachable at `OLLAMA_BASE_URL`; chat and embedding models pulled; Anthropic key set (info only; never shown) |

A top summary shows "x / y done", a progress bar, and **one** button "Continue setup", which runs the next failing fixable step in order.

Host / Prerequisites / Docker checks are executed by `cockpit.sh` before the GUI exists (§4, bootstrap). In the GUI they appear as passed, with re-check. If Docker breaks while the cockpit runs, the DB is gone too: the frontend shows a static "backend unreachable" page that tells you to run `./cockpit.sh` again, which prints the failing check.

Every check and step row has a **"Run it yourself"** expander with the exact command(s) (principle 8).

### 8.2 Step registry (whitelist)
Each step is a typed Python object: `id`, `title`, `platforms`, `requires` (step/check ids), `argv` builder (from validated params), `timeout`, `mutating: bool`, `needs_root: bool` (never executed, shown as instructions), `post_checks` (check ids to re-run).

Initial steps (each maps to an existing or new Make target):

| Step | Command (indicative) |
|---|---|
| `env.create` | copy `.env.example` → `.env` (Python, not shell) |
| `images.build` | `make build` / `make build-mac` |
| `images.pull_viewers` | `docker compose --profile viewers pull` |
| `ws.build` | `make ws-build` (colcon build in container) |
| `tests.run` | `make test` |
| `sim.smoke` | `make smoke-sim` (headless, timeout 300 s) |
| `ollama.pull` | `ollama pull <model>` (models from settings) |
| `docker.install`, `nvidia.toolkit`, `udev.install`, `groups.add` | `needs_root` → instructions only |

New Make targets that are missing get added (`ws-build`, `smoke-sim`, `status-json`, …). They print machine-readable output where useful (`--json`).

### 8.3 Jobs, logs and error detection
- A job = one step execution. Stored: `id`, `step_id`, `params`, `user`, `status` (`queued` / `running` / `succeeded` / `failed` / `cancelled` / `timeout`), `exit_code`, timestamps, log file path.
- **Concurrency:** one mutating job at a time (global lock); read-only checks can run in parallel. A second mutating request → HTTP 409 with the running job's id.
- **Live log:** WebSocket `/ws/jobs/{id}` streams stdout/stderr lines with timestamp and stream tag. The log viewer has follow mode, search, level colouring, ANSI rendering, copy and download.
- **Error detection:**
  - The exit code decides success.
  - Additionally, `config/cockpit/error_patterns.yaml` maps regexes to `{severity, title, hint, docs, fix_step?}` (e.g. `permission denied.*docker.sock` → "Your user cannot reach Docker", hint `groups.add`; `no space left on device`; `Could not find a package configuration file provided by "…"`; `nvidia-container-cli: initialization error`; colcon `Failed   <<< pkg`; `address already in use`).
  - Lines with a `[ERROR]`, `[FATAL]` or `[WARN]` level are coloured in the log, matched or not. Above the log, a bar counts them and offers **"Show all warnings and errors"** (a filter, toggled back with "Show the whole log") and, from C6, **"AI: Explain warnings/errors"** (§14.1). A succeeded job's error panel is titled as a notice, not as a failure: the exit code decides success.
  - Matches are highlighted inline and summarised in an **error panel** above the log. On failure the panel offers: the hint, the fix step, "Ask assistant about this error" (pre-fills the assistant with the matched lines + step context).
- **Cancel:** sends SIGTERM to the process group, then SIGKILL after 10 s.
- **Retention:** job logs are kept 30 days or the last 200 jobs, whichever is more.

## 9. Environment (start/stop)

### 9.1 Mode selection
- Shows the detected host and the **modes valid on it** (§3). Invalid modes are hidden; a "Why not?" link explains (e.g. REAL on macOS).
- Per mode, an options panel lists the relevant launch arguments, with defaults from `config/` and the current `.env`:
  - SIM · Gazebo: world, headless, software rendering (forced on macOS), viewers (`foxglove`, `rviz`, `none`), noVNC on/off, Lichtblick web on/off.
  - SIM · Mock: viewers, noVNC, Lichtblick.
  - REAL: velocity scaling (read-only, shows `config/robot.yaml` value; changes require editing config, spec_v0.4.md §10), cameras on/off, viewers.
  - REMOTE: Zenoh router address (from `.env`), read-only toggle (default on).
- **Start** runs a start job (§8.3), e.g. `make sim MODE=… VIEWERS=…` / `make sim-mac` / `make real`. While running the mode chip shows `STARTING…`. Done when §9.2 health reaches `ready` or the job fails / times out (180 s).
- **Stop** = `make down` (or the profile-specific target); confirmation needed in REAL mode.
- The start form shows the resulting command live (e.g. `AR3_MODE=sim make vnc`) with a copy button. The same command is recorded in the job.
- **Switching** between modes stops the current one first (confirm dialog). A stack started from the CLI is detected and shown with its command; the cockpit can stop it.
- Only one mode runs at a time per host (§3.1). The current state is derived from running containers and labels, not from cockpit memory, so it survives cockpit restarts and starts outside the cockpit (CLI).

### 9.2 Components and health
A component table (also feeding the header pill):

| Component | Health source | Ready when |
|---|---|---|
| ROS core container | Docker state + healthcheck | running + healthy |
| `ar3_cockpit_agent` | `GET /health` | 200 within 2 s |
| Gazebo server (SIM·Gazebo) | agent: `/clock` rate | > 50 Hz sim time advancing |
| `controller_manager` + controllers | agent: list controllers | `joint_state_broadcaster` and the arm/gripper controllers `active` |
| `move_group` | agent: node present + action server available | available |
| `ar3_safety` (REAL) | agent: `/safety/status` age | < 1 s |
| Hardware interface (REAL) | agent: hardware component state | `active` |
| Cameras (each) | agent: image topic rates + UVC settings check + last drift-check result | ≥ configured fps × 0.8, settings match, drift within threshold |
| `foxglove_bridge` | TCP connect :8765 | connect OK |
| Lichtblick web | HTTP GET :8080 | 200 |
| noVNC | HTTP GET :6080 | 200 |
| `zenohd` (REMOTE / Workstation A) | TCP connect | connect OK |
| Ollama | HTTP GET `/api/tags` | 200 |

Each row: status dot, uptime, last error, actions (restart container, view logs → Monitoring). Health is polled every 2 s (backend) and pushed over `/ws/status`.

### 9.3 Viewer links
A "Viewers" card with large buttons:
- **Lichtblick** (web container): opens `http://<host>:8080/?ds=foxglove-websocket&ds.url=ws://<host>:8765` with the repo layout (`config/lichtblick/ar3_default.json`). Instructions explain importing the layout once; the link is copyable.
- Lichtblick desktop: a hint with the same WebSocket URL.
- **noVNC / RViz:** `http://<host>:6080/vnc.html?autoconnect=1&resize=remote`.
- **Gazebo GUI:** Linux native only. The cockpit can start `gz sim -g` in the container with X forwarding when a local display exists; otherwise shows "use noVNC".
- **Foxglove WebSocket URL:** copy button.

`<host>` is the host the browser used to reach the cockpit (so links work from the Mac browser against Workstation A).

### 9.4 REAL mode: pre-flight wizard and arm states
Starting REAL brings the stack up with motion **disabled** (state `NOT HOMED`). Arming requires a wizard; each step is logged in the audit log with the user:

1. **Devices:** Teensy, Nano and cameras present (from checks).
2. **E-stop test:** "Press the e-stop now" → wait for `/safety/status.estop == true`; "Release and reset" → wait for `false` after `ResetEstop`. Timeout 60 s each. Without a seen transition the step fails. Until the hardware e-stop circuit exists (spec_v0.4.md §3) this step shows `fail` with a link to `docs/hardware.md`, and arming is blocked unless we set `COCKPIT_ALLOW_ARM_WITHOUT_ESTOP=1` in `.env`. That flag shows a permanent red banner.
3. **Workspace clear:** manual confirmation.
4. **Homing:** calls `Home`; shows progress; success → `HOMED`.
5. **Pose check:** a 3D view of the URDF at the current joint states next to the images of all cameras (overhead, side, wrist); the user confirms "model matches reality".
6. **Limits:** shows the velocity/acceleration scaling and workspace box from config; the user confirms.
7. → **ARMED.** Motion commands (Simulations run, named poses) are allowed only in `ARMED`.

Any fault, e-stop or agent disconnect → `FAULT` / `E-STOP`; motion is disabled until reset + re-arm from step 2 (step 4 too if the arm moved).

### 9.5 REMOTE mode
- Decision (2026-11-01): motion commands and saved scenarios are available on the Mac in REMOTE mode too, not only observation.
- Workstation A stays in control: its agent accepts motion from remote clients only with `COCKPIT_REMOTE_MOTION=1` in A's `.env`, and on the real arm only in `ARMED` (§9.4), through `ar3_safety` like any other client (principle 4). Without the flag, the Mac cockpit shows REMOTE as read-only and says why.

## 10. Monitoring

Tabs: **Overview · Logs · Telemetry · ROS Graph · Alerts · Jobs**.

### 10.1 Overview
- Tiles: mode, uptime, active alerts by severity, CPU / RAM / disk of the host, per-container CPU / memory (`docker stats --no-stream --format json`), GPU utilisation / memory / temperature (Linux, `nvidia-smi --query-gpu … --format=csv`), sim real-time factor.
- A compact component health list (§9.2).

### 10.2 Logs
- Sources: each container (`docker logs -f --timestamps`), `/rosout` (via agent, with node name and level), job logs, cockpit backend log.
- Filters: source, level (DEBUG…FATAL), node, time range, free-text / regex search. Live tail with pause; download filtered logs as a text file.
- Storage: the backend keeps a rolling buffer (last 50 000 lines per source in a Postgres hypertable `log_lines`, 7 days retention). Raw Docker logs remain the source of truth.
- Error patterns (§8.3) also apply here and create alerts.

### 10.3 Telemetry
- Live plots (uPlot), 10 Hz downsampled by the agent:
  - joint positions and velocities (6 + gripper);
  - commanded vs. actual (from the controller state topic);
  - gripper position / current (REAL);
  - `/safety/status` timeline;
  - topic rates for configured topics.
- Window 60 s live; "freeze" and export of the visible window as CSV.
- 3D view: URDF at current joint states, planned trajectory (from `/display_planned_path`), detected objects (`/perception/objects`, when present).
- Camera thumbnails (JPEG-compressed by the agent, ≤ 5 fps) for configured image topics, as a multi-view grid with optional overlays (detections, arm-occlusion mask).
- Longer recording is **not** the cockpit's job: link to "record a rosbag" (a step that runs `ros2 bag record` with a preset in the container; files under `data/bags/`).

### 10.4 ROS Graph
- Nodes, topics (type, publishers, subscribers, measured rate), services, actions; parameters of a selected node (read-only).
- Search. A simple graph visualisation of nodes ↔ topics for a selected node.

### 10.5 Alerts
- Rules in `config/cockpit/alerts.yaml`, evaluated every 2 s:

```yaml
- id: container_down
  when: component.status == "down" and mode != "STOPPED"
  severity: error
- id: topic_stale
  topic: /joint_states
  when: age_s > 1.0
  severity: error
- id: estop_active
  when: safety.estop
  severity: critical
- id: joint_near_limit
  when: joint.margin_deg < 5
  severity: warn
- id: camera_calibration_drift
  when: camera.drift_mm > 3 or camera.drift_deg > 0.5
  severity: warn
- id: camera_settings_mismatch
  when: camera.uvc_mismatch
  severity: warn
- id: gpu_hot
  when: gpu.temp_c > 85
  severity: warn
- id: disk_low
  when: disk.free_gb < 15
  severity: warn
- id: log_error_pattern
  when: log.match.severity in ["error", "critical"]
  severity: from_match
```

- Lifecycle: `active` → `acknowledged` (by user, with optional comment) → `resolved` (condition cleared). Dedup by rule + subject. History kept 90 days.
- Header pill and a toast for new `error` / `critical` alerts; optional browser notification (permission asked once).

### 10.6 Jobs
List of jobs with filter, status, duration, user; open log; re-run (same params).

## 11. Security and operation

### 11.1 Binding and access
- Default bind `127.0.0.1:8400`. LAN access requires `COCKPIT_BIND=0.0.0.0` in `.env`; the UI then shows the LAN URL.
- Auth is always on. There is no auth-off mode.
- Django session cookie: HttpOnly, SameSite=Lax, Secure when served over HTTPS. Expires after 12 h idle.
- Django CSRF for state-changing requests. Channels `AuthMiddlewareStack` + `AllowedHostsOriginValidator` for WebSockets.
- DB and Redis ports are published to `127.0.0.1` only. The DB password comes from `.env`; it is generated by `cockpit.sh` on first run if missing.
- Login throttling: 5 failures per user+IP per 5 min → 429.
- Optional TLS: documented reverse-proxy recipe (Caddy) for LAN use; not built in.

### 11.2 Backend ↔ agent
- The agent binds to the container network. Its port is published to `127.0.0.1` only (Linux host network: binds `127.0.0.1`).
- A shared token is generated by the backend at first start (`~/.ar3cockpit/agent.token`), passed to the container via env, required on every request.
- The agent accepts motion requests only if the backend says the mode is SIM or REAL+ARMED. It re-checks `ar3_safety` state itself before every motion.

### 11.3 Audit log
Records who did what and when: login/logout, user changes, job start/cancel, mode start/stop, arming wizard steps, software stop, scenario create/edit/delete/run, alert acknowledge, settings changes. Viewable in Settings → Audit. Kept 1 year.

### 11.4 Secrets
- Read from `.env` only. Never returned by the API; shown as "set / not set".
- Redacted from logs by key-name patterns and known value prefixes (`sk-ant-`).
- Never included in assistant context.

## 12. Simulations (MoveIt 2 scenarios)

### 12.1 Scenario model
One YAML file per scenario in `config/scenarios/<id>.yaml`. It is git-friendly, so scenarios are reviewed like code. JSON Schema in `tools/cockpit/backend/ar3cockpit/scenarios/scenario.schema.json`, also used by the editor.

```yaml
id: sort-three-colors
name: Sort three colours
description: 6 cubes, 3 bins, colour rule
version: 3                       # bumped on every save
tags: [sorting, phase3]
modes: [sim_gazebo, sim_mock]    # REAL only if listed AND owner-enabled (§12.5)
world: table_default             # Gazebo world from ar3_gazebo
start:
  named_pose: home               # or joints: [j1..j6], gripper: 0.0
scene:
  objects:
    - id: cube_red_1
      shape: box                 # box | cylinder | sphere | mesh
      size: [0.03, 0.03, 0.03]
      color: red
      class: cube
      pose: {x: 0.30, y: -0.05, z: 0.015, yaw_deg: 15}   # frame: table
      spawn: true                # Gazebo model + planning-scene collision object
  bins:   [ {name: bin_red, ref: config/bins.yaml} ]     # from config, or inline
  obstacles: []                  # extra collision objects (planning scene only or spawned)
  randomize:                     # optional
    seed: 42
    xy_jitter_m: 0.02
    yaw_jitter_deg: 20
perception: ground_truth         # ground_truth | camera  (ground_truth publishes DetectedObjectArray from the scene)
task:
  type: sequence                 # sequence | pick_place | sort
  steps:                         # for sequence
    - move_named: observe
    - move_pose: {frame: table, x: 0.3, y: 0.0, z: 0.15, rpy_deg: [180, 0, 0], planner: pilz_lin}
    - gripper: open
  params:                        # for pick_place / sort, passed to RunTask
    rules: config/sorting.yaml
planning:
  pipeline: ompl                 # ompl | pilz
  planner_id: RRTConnectkConfigDefault
  velocity_scaling: 0.2
  acceleration_scaling: 0.2
  planning_time_s: 5
success:
  all_steps_succeed: true
  objects_in_bins: {cube_red_1: bin_red}   # checked against Gazebo ground truth (sim_gazebo)
  max_duration_s: 300
repeat: 1
```

### 12.2 Capabilities by implementation phase
- **Now (Phases 0/0b):** `task.type: sequence` with `move_named`, `move_joints`, `move_pose` (OMPL or Pilz LIN/PTP), `gripper`, `wait`; scene objects as planning-scene collision objects (all sim modes) and Gazebo models (`sim_gazebo`).
- **After Spec Phase 3:** `pick_place` and `sort` via `RunTask`; `perception: camera` after Phase 2. The editor greys out unavailable task types, and the agent reports its capabilities (`GET /capabilities`), so the UI follows what's actually implemented.

### 12.3 Pages
- **List:** table with name, tags, modes, task type, last run result / date, version; search, filter by tag; actions: run, edit, duplicate, delete (confirm), export (YAML download), import (YAML upload, schema-validated).
- **Pack-and-go (ToDo, 2026-11-01):** pack one or more scenarios into a single archive on one host (e.g. the Mac), copy it, and import it into the cockpit on another (e.g. Workstation A), and the other way round. The pack holds the scenario YAMLs and what they reference (e.g. a `config/bins.yaml` excerpt, meshes of `mesh` objects), plus a manifest (source host, git commit, scenario versions, checksums). On import: schema validation, a diff against existing scenarios with the same id (keep / replace / import as copy), and a warning when the target's config (bins, table) differs from the pack's.
- **Editor** (three panes):
  1. **Form:** metadata, start, task steps (ordered list with add/remove/reorder; per-step fields), planning, success.
  2. **Scene editor:** top-down 2D table view (SVG). Drag objects, rotate with a handle, add from a palette (box, cylinder, sphere, bins, obstacle), and see the reachability ring of the arm (from config, approximate). Includes a 3D preview (three.js: URDF at start pose + objects).
  3. **YAML tab:** CodeMirror with live schema validation; the form and YAML stay in sync. Invalid YAML blocks saving and shows errors with line numbers.
- **Validation** before save and before run:
  - schema;
  - object ids unique;
  - objects on the table and not overlapping each other or bins;
  - named poses exist (from SRDF);
  - referenced files exist;
  - mode supported on this host.
  - Optional "Check reachability" calls the agent to compute IK for every pose target and pre-grasp, reporting failures per step.
- **Run panel:** choose mode (if more than one is allowed and running), repeat count, randomize seed. Shows:
  - live step progress (from action feedback);
  - a 3D view with the planned path;
  - telemetry mini-plots;
  - a log filtered to the run.
  - Run, Pause between steps (sequence only), Abort (= cancel goal + hold).
- **Run history:** per scenario, with run id, scenario version, mode, user, start, duration, result (`succeeded` / `failed` / `aborted`), failed step, success-criteria evaluation, metrics (planning time, execution time per step). Compare two runs side by side. Artefacts in `~/.ar3cockpit/runs/<run_id>/`: resolved scenario YAML (after randomization), run log, metrics JSON, optional rosbag (checkbox "record rosbag").

### 12.4 Run orchestration (agent)
1. Check mode, capabilities, and the arm state (REAL: `ARMED`).
2. Reset: clear scenario-owned collision objects and Gazebo models (prefixed `scn_`), move to the start pose.
3. Apply the scene:
   - planning scene via `/apply_planning_scene`;
   - Gazebo models via `ros_gz` `/world/<w>/create` with generated SDF (simple shapes, coloured materials, inertia from size and a default density).
4. If `perception: ground_truth`: publish `DetectedObjectArray` from the scene (and from Gazebo poses in sim_gazebo).
5. Execute the task: sequence steps via MoveGroup / Pilz / gripper action; `pick_place` / `sort` via `RunTask`. Stream feedback over WS.
6. Evaluate the success criteria (Gazebo model poses vs. bin volumes in sim_gazebo; step results in mock).
7. Write the results; clean up on request ("keep scene" checkbox).

Timeouts per step from `planning_time_s` + execution estimate × 3. Abort or timeout → cancel goals, hold.

### 12.5 Scenarios on the real arm
- Off by default. Listing `real` in `modes` is accepted only if `COCKPIT_REAL_SCENARIOS=1`.
- Needs `ARMED`. `spawn` is ignored; objects become planning-scene collision objects only, and the user confirms that the physical objects are placed as shown (2D view + overhead and side camera images side by side).
- `randomize` is disabled on real.

## 12a. Data capture (TimescaleDB)

Goal: every simulated and real session leaves structured, queryable data for later analysis, AI/ML training and fine-tuning. Sim and real data are kept side by side and are always distinguishable.

### 12a.1 What goes where
| Data | Store | Why |
|---|---|---|
| Scalar time series: joint positions/velocities/efforts, commanded vs. actual, gripper position/current, safety status, topic rates, controller state, host/container/GPU metrics, sim real-time factor | **TimescaleDB hypertables** | Fast range queries, compression, continuous aggregates |
| Events: mode changes, arming steps, alerts, job results, run step start/end/result, e-stop transitions | Hypertable `events` | Same timeline as telemetry |
| Detected objects (pose, class, confidence), planned trajectories (waypoints) | Hypertables (`detections`, `trajectories`, JSONB payload) | Join with telemetry by time / run |
| Images, video, point clouds, full message streams | **Not in Postgres.** rosbag2 (MCAP) under `data/bags/`, LeRobot datasets under `data/lerobot/` | Size; existing tooling. Postgres stores the file reference + time range (`recordings` table) |

### 12a.2 Schema (minimum)
- `sessions`: id, host, os, mode (`sim_mock` / `sim_gazebo` / `real` / `remote`), `source` (`sim` | `real`), git commit, config hash, started/ended, user.
- `runs`: id, session, scenario id + version + resolved YAML, seed, result, metrics JSON.
- Hypertables (time-partitioned, `segmentby` session/run):
  - `joint_samples` (time, session_id, run_id?, joint, position, velocity, effort, cmd_position, cmd_velocity);
  - `gripper_samples`;
  - `safety_samples`;
  - `metrics` (time, session_id, name, labels JSONB, value);
  - `events` (time, session_id, run_id?, kind, payload JSONB);
  - `detections`;
  - `trajectories`.
- `recordings`: id, session, run?, kind (`mcap` / `lerobot`), path, start/end, topics, size.
- **Rates:** joints and gripper stored at the configured rate (default 50 Hz real / sim; `TELEMETRY_RATE_HZ`). The UI live plots stay at 10 Hz (§10.3). Metrics at 1 Hz.
- **Policies:**
  - compression after 1 day;
  - retention: `metrics` 30 days, everything else unlimited, configurable per table;
  - continuous aggregates for 1 s / 1 min joint stats.
- Migrations via Django; hypertables, compression and policies created in `RunSQL` migrations. Hypertable models are `managed = False` read models, or written by raw SQL in `run_ingest` for speed. The ORM is used for queries in the UI.

### 12a.3 Ingest
- `run_ingest` subscribes to the agent's telemetry WebSocket (full rate, separate from the 10 Hz UI channel) and writes in batches (`COPY`, ≤ 1 s or 5 000 rows).
- Back-pressure: if the DB lags > 10 s, drop to 10 Hz and raise an alert. It never blocks the agent or ROS.
- Recording is on by default in REAL and during scenario runs; it can be switched in SIM (header toggle "REC", visible state).

### 12a.4 Use
- Monitoring telemetry history (beyond the 60 s live window) reads from the hypertables.
- Run comparison (§12.3) uses `runs` + `joint_samples` (e.g. tracking error per joint, duration per step, sim vs. real for the same scenario).
- **Export:** per session / run / time range to Parquet and CSV (`telemetry export` step and UI button). A converter step pairs a Parquet export with the matching LeRobot dataset episode (same time range) for training on Workstation B.
- Analysis notebooks connect read-only with a dedicated DB role `ar3_reader` (created by migration; password from `.env`).

## 13. Configuration

Layered: process env > `.env` (repo root, not committed) > `tools/cockpit/backend/defaults.env` (committed, every key documented). Settings stored by the UI (e.g. model choices, §14.3) live in the DB table `app_settings` and override `defaults.env` but not `.env` / the process env. The Settings page shows each value's source.

| Key | Default | Meaning |
|---|---|---|
| `COCKPIT_BIND` | `127.0.0.1` | Bind address |
| `COCKPIT_PORT` | `8400` | Port |
| `COCKPIT_DATA_DIR` | `~/.ar3cockpit` | Logs, runs, agent token |
| `DJANGO_SECRET_KEY` | generated by `cockpit.sh` | Secret |
| `COCKPIT_DB_HOST` / `_PORT` / `_NAME` / `_USER` / `_PASSWORD` | `127.0.0.1` / `5445` / `ar3cockpit` / `ar3cockpit` / generated | PostgreSQL + TimescaleDB |
| `COCKPIT_DB_READER_PASSWORD` | generated | Read-only role `ar3_reader` |
| `REDIS_URL` | `redis://127.0.0.1:6380/0` | Channels layer |
| `TELEMETRY_RATE_HZ` | `50` | Stored joint/gripper rate (§12a) |
| `TELEMETRY_RECORD_SIM` | `1` | Record in SIM by default |
| `COCKPIT_FAKE` | `0` | Simulated Docker/ROS/devices |
| `COCKPIT_ALLOW_ARM_WITHOUT_ESTOP` | `0` | Escape hatch, red banner |
| `COCKPIT_REAL_SCENARIOS` | `0` | Allow scenarios on the real arm |
| `AGENT_PORT` | `8790` | Agent port (container) |
| `LICHTBLICK_PORT` | `8080` | Lichtblick web (image `ghcr.io/lichtblick-suite/lichtblick`, pinned version) |
| `NOVNC_PORT` | `6080` | noVNC |
| `FOXGLOVE_PORT` | `8765` | foxglove_bridge |
| `ZENOH_ROUTER` | — | `tcp/<workstation-a>:7447` (REMOTE) |
| `ASSISTANT_PROVIDER` | `ollama` | `ollama` \| `anthropic` |
| `ASSISTANT_ALLOW_EXTERNAL` | `0` | Must be `1` before `anthropic` is used |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Native Ollama |
| `ASSISTANT_CHAT_MODEL` | `llama3.1:8b` | Default chat model (overridable per task and per thread, §14.3) |
| `ASSISTANT_EMBED_MODEL` | `bge-m3` | Embeddings (changing it triggers a re-index) |
| `ANTHROPIC_API_KEY` | — | Secret |
| `ANTHROPIC_MODEL` | `claude-sonnet-5-5` | Default Anthropic model id |
| `ASSISTANT_RATE_PER_MINUTE` | `20` | Per user |

Settings page: shows the effective values (secrets as set / not set) and their source layer; edits only non-secret keys, written to `.env`, with an audit entry. Restart-required keys are marked.

## 14. AI assistant

### 14.1 Behaviour
- Drawer on every page. Multi-turn threads per user, stored with retention `AI_LOG_DAYS=180`.
- Answers questions about ROS 2 Jazzy, MoveIt 2, Gazebo Harmonic, ros2_control, Lichtblick, noVNC, Zenoh, Docker, the AR3 hardware (Teensy 4.1 retrofit, wiring, e-stop, gripper) and this repo.
- **Grounded:** answers cite numbered sources (repo docs and files with path + heading; external links only if they come from the indexed docs). If nothing relevant is found, it says so and answers from general knowledge, marked as such.
- **Context-aware:** each question carries a **context pack** built server-side:
  - current page and selection (e.g. the failing check, the selected job's error panel lines, the scenario being edited);
  - host / OS / mode / arm state;
  - failing checks;
  - active alerts;
  - last 50 error lines of the relevant job / container.

  Budget ~6 000 tokens; truncation order: old log lines → long texts → lower-ranked items.
- **Advisory only:** the assistant cannot execute anything. It may propose a whitelisted step (by id) or a Make target; the UI renders those as buttons that go through the normal job path (§8.3) with user confirmation. Free-form shell commands are shown as copyable text only.
- **Entry points:** "Ask about this error" (job error panel, log line context menu), **"AI: Explain warnings/errors"** (the bar above a job log, also for succeeded jobs; see below), "Explain this check", "Explain this mode", "Help me write this scenario" (returns YAML that's schema-validated and opened as a diff in the editor, never saved automatically).

- **Explain warnings/errors** (task `error_explain`): the button opens the drawer and sends a prepared question for this job. Its context pack holds the step (id, title, command, exit code, status), the distinct warning/error lines of the log (deduplicated, with counts, timestamps dropped, within the budget above), the error-pattern matches, and retrieved sources: the troubleshooting entries, `docs/` sections and the code or config the lines name (e.g. `src/ar3_cockpit_agent/…`, `config/…`), cited with path and heading. The answer sorts the messages into **harmless** (expected for this step, e.g. shutdown noise of a test), **worth fixing** (a defect in this repo or its configuration) and **needs action** (blocks the user), says why, and cites the evidence. Like every answer it is advisory: a proposed fix step is a button that goes through the job path, code changes are shown as text.

### 14.2 Knowledge index
- Sources:
  - repo `README.md`, `Spec_v*.md`, `docs/**/*.md` (incl. `docs/specs/spec_cockpit.md`);
  - package READMEs, `ar3_firmware/*.md`;
  - `config/**/*.yaml` (as reference);
  - optional curated external docs placed as Markdown in `tools/cockpit/knowledge/` (**[OPEN]** which ones).
- Chunking: by Markdown heading, ~800 tokens, 100 overlap.
- Store: Postgres tables `kb_chunks` (text, source path, heading, hash, `tsvector`, `vector(dim)` via pgvector with an HNSW index). Hybrid retrieval (Postgres full-text + vector) with reciprocal-rank fusion, top 8.
- Embeddings via Ollama `ASSISTANT_EMBED_MODEL`; the vector dimension is read from the model and stored with the index. Without an embedding model: full-text only (degraded, shown in the footer).
- The component catalogue (`config/cockpit/components.yaml`, §3.4) and the mode tables (§3) are indexed too, so "how do I start Gazebo with RViz on the Mac?" answers with `AR3_MODE=sim make vnc`.
- Re-index: on startup if file hashes changed, plus a "Rebuild index" button (Settings).

### 14.3 Providers and models (configurable)
- `ollama`: `/api/chat`, streaming. **Any** pulled model can be used (llama3.x, gemma3, deepseek-r1, gpt-oss, qwen3, mistral, …). Nothing is hard-coded beyond the defaults.
- `anthropic`: Messages API, streaming, used only when `ASSISTANT_ALLOW_EXTERNAL=1` and a key is set; otherwise falls back to Ollama and the footer says so.
- **Tasks**, each with its own provider + model + parameters (temperature, max tokens, context window `num_ctx`, timeout):
  - `chat` (assistant answers);
  - `error_explain` (job/log error analysis);
  - `scenario_help` (YAML generation, §14.1);
  - `embed` (index).
- **Settings → AI** page:
  - lists the models installed in Ollama (`/api/tags`, with size and quantisation) and lets you pull a new model by name (step `ollama.pull`, job with log);
  - per task, choose provider + model from the list (Anthropic model id as free text, validated by a test call);
  - "Test" button per task (short prompt, shows latency and first tokens).
- **Per thread** the user can switch provider/model in the assistant drawer; the footer of each answer shows provider, model, sources count, latency.
- **Reasoning models** (deepseek-r1, gpt-oss): `<think>` / reasoning output is stripped from the answer and shown collapsed ("Show reasoning"), not stored in the context of later turns.
- **Hardware hint** (informational, from model size vs. detected memory): e.g. gpt-oss:20b needs ~16 GB, so it fits Workstation B or the 64 GB Mac but not Workstation A's 8 GB GPU (it would run on CPU, slowly). The UI warns but doesn't block.
- Provider unavailable → clear message + link to the AI checks (§8.1).

## 15. API (backend)

REST under `/api` (Django REST Framework), OpenAPI generated by `drf-spectacular`; the frontend client is generated from it (`openapi-typescript`). WebSockets are Channels consumers. Streaming assistant answers use SSE from an async Django view.

| Area | Endpoints |
|---|---|
| Auth | `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me`, `POST /api/auth/password`, `POST /api/setup-account` (localhost, only if no users) |
| Users | `GET/POST /api/users`, `DELETE /api/users/{id}`, `POST /api/users/{id}/reset-password` |
| Platform | `GET /api/platform` (os, arch, gpu, docker, devices) |
| Checks | `GET /api/checks`, `POST /api/checks/run` (all or ids) |
| Steps/Jobs | `GET /api/steps`, `POST /api/jobs` `{step_id, params}`, `GET /api/jobs`, `GET /api/jobs/{id}`, `POST /api/jobs/{id}/cancel`, `GET /api/jobs/{id}/log` |
| Environment | `GET /api/env/modes`, `GET /api/env/state`, `POST /api/env/start` `{mode, options}`, `POST /api/env/stop`, `POST /api/env/components/{id}/restart`, `GET /api/env/viewers` |
| Arm (REAL) | `GET /api/arm/state`, `POST /api/arm/preflight/{step}`, `POST /api/arm/stop` (software stop; also valid in SIM) |
| Monitoring | `GET /api/metrics/host`, `GET /api/metrics/containers`, `GET /api/logs?source&level&q&from&to`, `GET /api/ros/graph`, `GET /api/ros/node/{name}/params`, `GET/POST /api/alerts`, `POST /api/alerts/{id}/ack` |
| Scenarios | `GET/POST /api/scenarios`, `GET/PUT/DELETE /api/scenarios/{id}`, `POST /api/scenarios/validate`, `POST /api/scenarios/{id}/reachability`, `POST /api/scenarios/{id}/runs`, `GET /api/scenarios/{id}/runs`, `GET /api/runs/{run_id}`, `POST /api/runs/{run_id}/abort` |
| Assistant | `POST /api/assistant/threads`, `GET /api/assistant/threads`, `POST /api/assistant/threads/{id}/messages` (SSE stream), `POST /api/assistant/index/rebuild` |
| Settings/Audit | `GET/PATCH /api/settings`, `GET /api/audit` |
| AI settings | `GET /api/ai/models` (Ollama installed + configured), `GET/PUT /api/ai/tasks/{task}`, `POST /api/ai/tasks/{task}/test` |
| Components | `GET /api/components` (catalogue + live status + CLI commands) |
| Telemetry | `GET /api/sessions`, `GET /api/sessions/{id}`, `GET /api/telemetry/joints?session&run&from&to&bucket`, `POST /api/telemetry/export` (Parquet/CSV job), `GET /api/recordings` |
| Files | `GET /api/robot/urdf` (expanded xacro), `GET /api/robot/meshes/{path}` |

WebSockets: `/ws/status` (header + component health + alerts), `/ws/jobs/{id}`, `/ws/logs?source…`, `/ws/telemetry?channels…`, `/ws/runs/{run_id}`.

Errors: RFC 9457 problem JSON (`type`, `title`, `detail`, `hint?`, `docs?`).

## 16. `ar3_cockpit_agent` (ROS 2 package)

- Python package, started by the bringup launch when `cockpit_agent:=true` (default true in all modes).
- Endpoints:
  - `/health`, `/capabilities`
  - `/graph`, `/node/{n}/params`
  - `/controllers`
  - `/stop`
  - `/scene/apply`, `/scene/clear`
  - `/ik/check`
  - `/run` (+ `/run/{id}/abort`)
  - `/arm/home`, `/arm/reset_estop`
- WebSockets: telemetry (configurable channels, 10 Hz), rosout, run feedback, camera thumbnails.
- Uses `rclpy` with a `MultiThreadedExecutor` in a background thread; FastAPI in the main asyncio loop; thread-safe queues between them.
- Motion goes through `moveit_py` (MoveItPy) or the `move_group` action interface, whichever Phase 0 used. Document the choice.
- Gazebo spawning uses `ros_gz_interfaces/srv/SpawnEntity` / `DeleteEntity` via the `ros_gz_bridge` service bridge, or `gz service` calls. Pick the one that works headless on both arches, and document it.
- Unit tests with a fake node graph; launch_testing in mock mode (CI).

## 17. Testing

- **Backend:** pytest + pytest-django against a real `timescale/timescaledb-ha` container (same pinned tag; CI service container), so hypertable migrations, compression policies and pgvector are tested, not mocked. Channels consumers tested with `WebsocketCommunicator`. `CommandRunner` fake (scripted outputs and exit codes), fake agent, fake platform (Linux / macOS / REAL-with-devices variants). Tests for: every check (pass/fail/unknown paths), step argv building and param validation, job concurrency lock, cancel, error-pattern matching (fixture logs), mode-validity matrix, auth (throttling, CSRF, localhost-only first user), scenario schema / validation, assistant context-pack truncation and secret redaction.
- **Agent:** pytest + launch_testing in mock mode: graph, controllers, a scenario `sequence` run reaching named poses, scene apply/clear.
- **Frontend e2e:** Playwright against the backend in `COCKPIT_FAKE=1`. Covers: login, first-user setup, full checklist flow with a simulated failure and fix, start/stop SIM and REAL (fake), arming wizard incl. e-stop transition, alerts appear in the header, scenario create → validate → run → history, assistant answer with sources (fake provider).
- **Hardware:** manual checklist in `docs/manual/cockpit.md` (REAL start, arming wizard with the real e-stop, software stop during motion), marked as verified (spec_v0.4.md §9).
- CI: backend + frontend build + e2e (fake) on every push; agent tests in the existing ROS CI job.

## 18. Phases and acceptance

| Phase | Deliverables | Accept |
|---|---|---|
| **C1 — Skeleton** | `cockpit.sh` (pre-Docker checks, `cockpit` compose profile, migrate, start web + workers), Django project + apps, Channels/Redis, settings layering, auth + users + first-user setup, shell with horizontal menu + header (status from fake), logo assets, login screen with split background (placeholder video), audit log, fake mode, CI with Timescale service; Linux mode table derived from the Makefile (§3.3) | `./cockpit.sh` on a fresh Mac and Ubuntu clone opens the login; first user created; header shows host chip; e2e green; Linux table delivered for review |
| **C2 — Install & Setup** | Checks (§8.1), step registry + jobs + live logs + error patterns, `ar3ctl doctor`, missing Make targets | On a fresh Ubuntu and a fresh Mac, following only the checklist leads to all-green (except needs-root items done by hand); a forced failure (e.g. port busy) shows the matched hint; `ar3ctl doctor --json` equals the UI checklist |
| **C3 — Environment** | Mode matrix + start form with live command, single-stack enforcement, component catalogue (§3.4) with CLI commands and diagram, start/stop, component health, viewer links, Lichtblick web + noVNC services, agent (health, controllers, stop), REAL pre-flight wizard | Mac: all four §3.2 variants start/stop from the UI and the shown command equals the one run; starting a second stack is refused with "stop and switch"; a stack started from the CLI is detected; Lichtblick and noVNC links work; Ubuntu: same + REAL reaches `ARMED` via the wizard (verified), software stop halts a running motion; state survives cockpit restart |
| **C4 — Monitoring + data capture** | Overview metrics, logs with filters, telemetry plots + 3D, ROS graph, alert engine + rules, jobs page; hypertables, `run_ingest`, sessions, REC toggle, history view, Parquet/CSV export | Killing a container raises `container_down` within 5 s and the header turns red; `/rosout` errors searchable; joint plots live at 10 Hz; a 10-min SIM session stores joints at 50 Hz without gaps (query count = expected ± 1 %), compresses after policy run, exports to Parquet readable by pandas |
| **C5 — Simulations** | Scenario schema, store, list/editor/YAML/scene editor, validation + reachability, agent run orchestration (`sequence`), run history + compare; `pick_place`/`sort` gated on capabilities | A 5-step `sequence` scenario with 3 spawned objects runs in SIM·Gazebo on Mac and Ubuntu, success evaluated, history shows the run; an unreachable pose is reported by "Check reachability" before running |
| **C6 — Assistant** | Index (pgvector + full-text), providers, per-task model config + AI settings page + model pull, context pack, entry points, proposed-step buttons, scenario YAML help | Asking "why did ws-build fail?" on a failed job cites the job log lines and the relevant doc section; "AI: Explain warnings/errors" on a succeeded `make test` job classifies each distinct warning/error line (harmless / worth fixing / needs action) with sources, and its context pack holds no secret (test); switching the chat model (e.g. llama3 → gemma3 → gpt-oss) takes effect without restart; reasoning output hidden by default; Anthropic used only with the flag; no secret ever appears in a stored context pack (test) |

Each phase updates `docs/manual/cockpit.md` (usage) and `docs/manual/troubleshooting.md` (spec_0.4.md §9a rules).

## 19. Changes to spec_v0.4.md (apply with C1)

- §5 layout: add `cockpit.sh`, `tools/cockpit/`, `src/ar3_cockpit_agent/`, `config/scenarios/`, `config/cockpit/`.
- §9a docs: add `docs/manual/cockpit.md`.
- Changelog: v0.5 — "AR3 Cockpit (see spec_cockpit.md)".

## 20. Open questions

Resolved (v0.3): Linux table (§3.3), REMOTE motion (§9.5).


Resolved (v0.2): macOS mode table (§3.2); Django + PostgreSQL/TimescaleDB; name "AR3 Cockpit"; models configurable per task.

1. ~~Linux / REAL / REMOTE table (§3.3)~~: confirmed in v0.3.
2. Data retention per hypertable (§12a.2) and default storage rate (50 Hz) OK?
3. ~~Shared Postgres with other apps~~: no, the cockpit keeps its own instance (independent project).
4. Real-arm video for the login screen (§7.3): filmed, same trajectory as `trajectory.json`.
5. External docs to include in the assistant's index (§14.2), e.g. curated ROS 2 / MoveIt / Gazebo pages saved as Markdown.
6. Should the cockpit also manage Workstation B (AI host) later, or stay single-host?
