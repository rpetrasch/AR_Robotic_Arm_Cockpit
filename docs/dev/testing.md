# Testing

| Level | What | Command | In CI |
|---|---|---|---|
| Lint | ruff check/format, YAML/XML checks, clang-format | `pre-commit run --all-files` or `make lint` | yes |
| Unit | URDF structure, TF chain, limits vs. config, link lengths (FK); SRDF poses within limits; scaling from config; named-pose helpers; bringup argument defaults and foxglove asset allowlist | `make test` | yes |
| Integration (mock) | `bringup.launch.py mode:=mock`: MoveIt plans and executes `observe → stow → home` and gripper `open → closed`; checks `/joint_states` against each target (±0.01 rad) | `make test` | yes |
| Unit (cockpit agent) | health rules (clock rate, controllers, move_group, cameras vs. config), motion policy (REMOTE, REAL), the HTTP API with a fake node: token, endpoints, problem JSON; telemetry: the sampling grid and decimation, packing, rings, JPEG thumbnails, the WebSockets | `make test` | yes |
| Integration (mock, agent) | the agent in the mock bringup: ready, 401 without the token, controllers and graph, and its software stop halts a running MoveIt motion short of the goal (drift < 1e-3 rad); the telemetry stream on the exact 10 Hz / 50 Hz grid, /rosout with move_group's lines, publishers / subscribers, parameters, topic rates | `make test` | yes |
| Integration (sim) | Headless Gazebo: all four camera topics publish (size and frame checked); same pose sequence, ±0.03 rad | `make test-sim`, or `make smoke-sim` (the same, with the result in `.cockpit/results/smoke-sim.json` for the cockpit) | no (needs rendering, ~1–2 min) |

`make test` runs `colcon test` for `ar3_description`, `ar3_moveit_config`, `ar3_bringup` and
`ar3_cockpit_agent`, then `colcon test-result --verbose`. The vendored upstream packages are not tested.

`make build`, `make test`, `make test-sim` and `make lint` run inside the `ar3-dev` image when the host
has no `/opt/ros/jazzy` (for example on a Mac), so the same command works everywhere. The
`docker-` targets (`make docker-test`, …) always use the image.

Current totals: `make test` 59 tests (colcon's count), `make test-sim` 2 tests.

## CI

`.github/workflows/ci.yml` has four jobs:

1. `lint` runs pre-commit.
2. `build-test` installs dependencies with `rosdep` in `ros:jazzy-ros-base`, builds, and runs `make test`'s test set.
3. `images` builds the `dev` and `vnc` images for `linux/amd64` and `linux/arm64` with buildx (no push).
   The arm64 build was run locally (`docker buildx build --platform linux/arm64`).
4. `cockpit` runs the AR3 Cockpit tests (below) with TimescaleDB and Redis service containers.

The repository is hosted on Codeberg. Forgejo Actions reads `.github/workflows` when no
`.forgejo/workflows` directory exists, but a runner has to be enabled for the repository.
The `build-test` steps were run locally in a clean `ros:jazzy-ros-base` container.
The workflow itself has not run on a hosted runner yet.

## AR3 Cockpit

| Level | What | Command | In CI |
|---|---|---|---|
| Backend | auth, users, audit, stack detection, header status, WebSockets, every check (pass/fail/unknown), steps and parameter validation, job lock (409), executor, cancel/timeout, error patterns, secret redaction, "Continue setup", `ar3ctl doctor` = the API checklist, serving, DB extensions; C3: start commands (`make -n` of each one), start / stop / stop-and-switch jobs, 409 for a second stack, component health and pill, catalogue valid and docs current, agent client and token, software stop, the pre-flight wizard with e-stop transitions and timeouts; C4: the alert rule language (and what it refuses), facts, lifecycle and pill, dead containers and container_down, log parsing / search / prune / live-tail filter, the collector with fake `docker logs` and /rosout, metrics parsing, the monitoring API; sessions and REC, the ingest at 50 Hz without gaps and its back-pressure, history buckets and gaps, Parquet / CSV export read by pandas, retention and compression, the reader role, continuous aggregates, rosbag registration, non-mutating jobs next to a mutating one; C5: scenario schema and every semantic check with line numbers, store (versions, comments, duplicate), randomize, evaluation (bins, duration), runs through the fake agent (success, failing step, abort, refusals), reachability, the API, history and compare, the run WebSocket, pack-and-go (diff, copy, config warnings, unsafe zips) (pytest, real TimescaleDB) | `cd tools/cockpit/backend && uv run pytest -q` | yes |
| e2e | first user, login, throttling, header states, users/audit, sign-out; the Install & Setup checklist to all-green with "Continue setup", a busy port, a failing job's hint; C3: start from the form with the shown command, stop and switch, the header's Stop, components, Lichtblick web, stop, a CLI-started stack, the REAL wizard to ARMED with an e-stop; C4: overview and REC, /rosout errors searchable and the live tail, live plots at 10 Hz and freeze, ROS graph and parameters, a killed container turning the header red within 5 s with ack and resolve, jobs, history with a Parquet export; C5: the scenario list, reachability reporting the unreachable step, the editor (form ↔ YAML, scene editor, invalid YAML blocks saving), a 5-step run with 3 spawned objects, a failing step, compare, abort, pack-and-go (Playwright, fake mode, own DB) | `cd tools/cockpit/frontend && npm run build && npx playwright test` | yes |

`make cockpit-test` runs both. The backend tests need the cockpit database: run `./cockpit.sh`
once (or `docker compose -f docker/compose.yaml --profile cockpit up -d` with
`COCKPIT_DB_PASSWORD` set). Current totals: 309 backend tests, 38 e2e tests. See
[cockpit.md](../manual/cockpit.md).

Playwright 1.56 does not know Ubuntu 26.04 and refuses to download its browser. Use
`PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu24.04-x64 npx playwright install chromium` once, and
set the same variable when running the tests.

`npm audit` in `tools/cockpit/frontend` reports 56 findings (53 low, 3 moderate, none high).
They are accepted: the frontend is a static export, so nothing from the build tools ships, and
the rest is `deepmerge` (inherited by every `@heroui/*` package; the fix is HeroUI v3) and
`minimatch` (through `openapi-typescript`, dev-only). Do not run `npm audit fix --force`: it
downgrades `openapi-typescript` and breaks `gen:api`. `postcss` and `sharp` are pinned to the
patched versions.

The C3, C4 and C5 acceptance on a real Mac is `npm run test:real` in `tools/cockpit/frontend`
(5 tests, about 17 min, local only, never in CI): it starts and stops real stacks through the UI,
records a 10-minute Gazebo session and kills its container, and runs a scenario in Gazebo (no
hardware). Stop your own stack first. One phase only, e.g.
`npx playwright test -c playwright.real.config.ts tests/real/c5.spec.ts`.

`tests/real/c6.spec.ts` (part of `npm run test:real` too; alone 4.7 min on the M1 Max) asks the
real Ollama: it needs `llama3.1:8b`, `gemma3:4b`, `gpt-oss:20b` and `bge-m3` pulled. It makes
`ws-build` fail with a throwaway package `src/zz_c6_broken` (removed again, then the workspace is
rebuilt) and runs `make test`.

The assistant's tests (`tests/test_assistant.py`, e2e `c6.spec.ts`) use the scripted provider
and bag-of-words embeddings (`ASSISTANT_FAKE`, on with `COCKPIT_FAKE=1`), so retrieval, the
context pack (secrets, budget) and the streamed answer run without a model.

The agent's telemetry, /rosout, graph and parameters are tested in the mock bringup
(`ar3_bringup/test/test_cockpit_agent_mock.py`) and without ROS
(`ar3_cockpit_agent/test/test_telemetry_unit.py`), its scenario runs in the mock bringup and in
`test_scenario_unit.py`; all in `make test` (91 tests in total).

## Hardware tests

These do not exist yet; they arrive with Phase 1 under `tests/hardware/`. They will never run in CI.
