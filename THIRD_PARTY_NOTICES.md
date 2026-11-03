# Third-party notices

The license in [LICENSE.md](LICENSE.md) covers the code written for this project:
the `ar3_*` ROS packages in `src/`, the AR3 Cockpit in `tools/cockpit/`, `cockpit.sh`, the
Makefile, the Docker files, `config/`, `docs/` and `tests/`, and the AR3 Cockpit logo.

It does **not** cover the third-party material listed below. That material keeps its own
license, and its copyright notices must be kept when it is copied or distributed.

## 1. Code and assets in this repository

### ar4_ros_driver (vendored in `src/external/ar4_ros_driver/`)

- Source: https://github.com/ycheng517/ar4_ros_driver, pinned commit in
  [src/external/README.md](src/external/README.md)
- License: **MIT**, Copyright (c) 2021 Dexter Ong (see `src/external/ar4_ros_driver/LICENSE`),
  for the ROS packages (`annin_ar4_description`, `annin_ar4_driver`, `annin_ar4_gazebo`,
  `annin_ar4_moveit_config`).
- **Derived file:** `src/ar3_description/urdf/ar3_macro.xacro` is a parameterized copy of
  upstream `annin_ar4_description/urdf/ar_macro.xacro`. It keeps the upstream MIT notice in
  addition to this project's.

### Annin Robotics firmware (`src/external/ar4_ros_driver/annin_ar4_firmware/`)

The microcontroller sketches come from Chris Annin's AR4 control software, adapted by the
ar4_ros_driver authors for the ROS protocol. `AR4_nano.ino` carries this notice (summary,
the full text is in the file header):

> Copyright (c) 2021, Chris Annin. All rights reserved.
> Free to share, copy, redistribute, remix and build upon; redistributions must keep the
> copyright notice and disclaimer, must be free of charge, must give appropriate credit and
> indicate changes. **Selling AR2 software, robots, robot parts, or any versions of robots or
> software based on this work is strictly prohibited.**

`AR4_teensy.ino` has no header in the vendored copy; it is derived from the same Annin
software and is treated under the same terms.

**Consequence:** this firmware, and any firmware derived from it (planned:
`src/ar3_firmware/`, Phase 1), is **not MIT** and may not be sold or used in a robot that is
sold. It stays in its own directory with Chris Annin's notice. The MIT-licensed host software
talks to it only over the serial protocol.

### Robot meshes (`src/external/ar4_ros_driver/annin_ar4_description/meshes/`)

The AR4 link and gripper meshes are derived from Annin Robotics' published CAD models of the
AR4 (https://anninrobotics.com/downloads). Design and CAD © Annin Robotics; see the terms on
https://anninrobotics.com/terms. Distributed here as part of ar4_ros_driver; no separate
license is stated upstream.

## 2. Software used at build or run time (not included in this repository)

These are installed by the Docker images, pip/uv or npm. Each keeps its license; check the
pinned version's license when upgrading.

### ROS stack (dev image)

| Component | License |
|---|---|
| ROS 2 Jazzy (rclcpp, rclpy, tf2, …) | Apache-2.0 (some packages BSD-3-Clause) |
| ros2_control, ros2_controllers | Apache-2.0 |
| MoveIt 2 | BSD-3-Clause |
| Gazebo Harmonic, ros_gz | Apache-2.0 |
| foxglove_bridge | MIT |
| Zenoh, rmw_zenoh | EPL-2.0 OR Apache-2.0 |

### Viewers (Docker images)

| Component | License | Note |
|---|---|---|
| Lichtblick (`ghcr.io/lichtblick-suite/lichtblick`) | MPL-2.0 | |
| noVNC | MPL-2.0 | |
| TigerVNC | GPL-2.0 | Part of the `ar3-dev-vnc` image. This repository only ships the Dockerfile; whoever **publishes** a built image must meet the GPL's source-offer obligations |

### AR3 Cockpit backend (Python)

| Component | License |
|---|---|
| Django, Django REST Framework, Channels, channels-redis, drf-spectacular, uvicorn, websockets, psutil | BSD-3-Clause |
| psycopg 3 | LGPL-3.0 (used as an unmodified, dynamically loaded library) |
| argon2-cffi, PyYAML, jsonschema, redis-py | MIT |
| pyarrow | Apache-2.0 |
| anthropic (Anthropic Python SDK, the assistant's Anthropic provider) and its dependencies pydantic, jiter, docstring-parser, truststore, typing-inspection | MIT |
| httpx2 (HTTP client of the anthropic SDK; also used for Ollama) | BSD-3-Clause |
| sniffio | MIT OR Apache-2.0 |

### Demo video tooling (`tools/demo/`, run on demand)

| Component | License |
|---|---|
| Pillow (installed by `uv` for `render.py`) | MIT-CMU (HPND) |
| FFmpeg (system install, called as a program) | LGPL-2.1+ / GPL-2.0+ depending on the build; not distributed here |

### AR3 Cockpit frontend (npm)

| Component | License |
|---|---|
| Next.js, React, HeroUI, TanStack Query, framer-motion, three.js, @react-three/fiber, uPlot, react-virtuoso, CodeMirror, @uiw/react-codemirror, anser, Bootstrap Icons, Tailwind CSS | MIT |
| urdf-loader | Apache-2.0 |
| yaml | ISC |
| Inter font (`@fontsource-variable/inter`) | SIL Open Font License 1.1 |

### Cockpit services (Docker images)

| Component | License | Note |
|---|---|---|
| PostgreSQL | PostgreSQL License | |
| pgvector | PostgreSQL License | |
| TimescaleDB (`timescale/timescaledb-ha`) | Apache-2.0 (core) and **Timescale License (TSL)** (compression, continuous aggregates, retention policies) | TSL allows free use, including commercial, but not offering TimescaleDB as a hosted database service |
| Redis 7.4 | **RSALv2 / SSPLv1** (source-available, not OSI open source) | Fine for running it as this app's internal cache. If an OSI license is required, the drop-in alternative is Valkey (BSD-3-Clause) |

## 3. Planned components with license constraints

| Component (phase) | License | Note |
|---|---|---|
| Ultralytics YOLO (Phase 2) | **AGPL-3.0** (or a commercial Enterprise license) | Using it in software that is distributed or offered over a network would put that software under AGPL obligations. Alternatives with permissive licenses (e.g. RT-DETR in other implementations, YOLOX: Apache-2.0) should be evaluated before Phase 2 |
| LeRobot, ACT, SmolVLA (Phases 4–5) | Apache-2.0 (code) | Model weights and datasets have their own licenses; check each checkpoint |
| Ollama (assistant) | MIT | Model licenses vary (Llama Community License, Gemma Terms of Use, Apache-2.0 for gpt-oss, MIT for DeepSeek-R1, …) |
| Anthropic API (assistant, optional) | Commercial terms of service | |

This file is maintained by hand. Every new vendored file, asset or dependency with a
non-permissive license must be added here in the same change.
