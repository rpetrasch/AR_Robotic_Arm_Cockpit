# Project
The specification is in Spec_v0.4.md.

## Status
- Phase 0 (workspace, description, simulation): done — see `docs/changelog/phase0.md`.
- Phase 0b (Linux/macOS profiles, Lichtblick, noVNC, Zenoh): done — see `docs/changelog/phase0b.md`.
- Phase 1 (real hardware) blocked: Spec §12 open items 1 and 3.
- AR3 Cockpit (`docs/specs/spec_cockpit.md` v0.4): C1 (skeleton), C2 (Install & Setup), C3
  (Environment, `ar3_cockpit_agent`), C4 (Monitoring + data capture), C5 (Simulations, incl.
  pack-and-go) and C6 (assistant) done — see `docs/changelog/cockpit-c1.md` …
  `docs/changelog/cockpit-c6.md`. C3–C6 not verified on Ubuntu / REMOTE / REAL.

## Build & test
The workstations run Ubuntu 24.04 + Jazzy natively; on a Mac, `make build|test|test-sim|lint`
run inside the dev image automatically (no `/opt/ros` on the host). Compose profiles:
`linux` (dev, dev-gpu, linux/linux-gpu via `make up-linux|sim-linux`, zenohd), `mac` (`make up-mac`, `make sim-mac`), `vnc` (`make vnc`).
Cockpit workers: `run_monitor` (status, alerts, metrics, logs), `run_jobs`, `run_ingest` (telemetry → TimescaleDB).

```bash
make build        # colcon build --symlink-install, tests enabled
make test         # unit + mock integration tests (= CI)
make test-sim     # headless Gazebo test, local only (~1-2 min)
make lint         # ruff check + format check
```
CI: `.github/workflows/ci.yml` (lint via pre-commit; build + test in `ros:jazzy-ros-base`; cockpit).

Cockpit (native, no ROS): `./cockpit.sh` (or `--fake`); checklist in the terminal: `make doctor`;
tests: `make cockpit-test`; real-Mac C3 acceptance: `npm run test:real` in `tools/cockpit/frontend`
(backend pytest needs the cockpit DB from `./cockpit.sh`). After an API change: `npm run gen:api`
in `tools/cockpit/frontend`. After editing `config/cockpit/components.yaml`: `make cockpit-docs`.

## Conventions
- Python: ruff (`ruff.toml`, line length 100). C++: clang-format (`.clang-format`).
- Site values live in `config/` (Spec §8), never in code. Launch files read it via
  `config_dir:=` / `$AR3_CONFIG_DIR`, falling back to the copy installed by `ar3_bringup`.
- Velocity/acceleration scaling default 0.2 comes from `config/robot.yaml` `motion:`;
  a test pins the shipped default.
- Shared MoveIt setup is `ar3_moveit_config.build_moveit_config()`; launch files must
  use it rather than building their own parameter dicts.
- New nodes: declare parameters with defaults + descriptions; lifecycle node if it owns
  hardware; document in the package README.
- Prefer standard messages (`vision_msgs`, `control_msgs`, `moveit_msgs`).
- Every documented command must have been run in the phase that documents it.

## Do not touch
- `src/external/` except as a recorded divergence in `src/external/README.md`.
- Never send motion to real hardware from automated runs; `mode:=real` must be explicit
  (it is currently refused until Phase 1 delivers `ar3_hardware`).
- Do not invent hardware facts (pin map, steps/deg, link lengths, serial protocol): ask.
