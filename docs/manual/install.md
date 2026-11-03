# Install

Versions: Ubuntu 24.04, ROS 2 Jazzy, Gazebo Harmonic (through `ros-jazzy-ros-gz`) and MoveIt 2 (Jazzy binaries).

There are two ways to install. Pick one:

- **Docker** works on any host, including Macs. The image is `docker/Dockerfile`, built on `ros:jazzy-ros-base` for amd64 or arm64.
- **Native** is for Workstation A, running Ubuntu 24.04 on bare metal. This avoids USB and serial issues with containers.

## Docker

```bash
make image        # = docker build -f docker/Dockerfile --target dev -t ar3-dev .
make docker-build
make docker-test
```

`make image-vnc` builds the optional browser-desktop image (`ar3-dev-vnc`), and
`make image-multiarch` builds both images for amd64 and arm64 with buildx, as CI does.

`docker/compose.yaml` has three profiles: `linux` (the workstations), `mac` and `vnc`.
On a Mac, follow [usage-mac.md](usage-mac.md).

On Linux, for an interactive shell with X11, `/dev` access and host networking, use
`docker compose -f docker/compose.yaml run --rm dev`. On a host with an NVIDIA GPU,
use the `dev-gpu` service; it needs the NVIDIA Container Toolkit (see below).
Serial ports and cameras are reachable because `/dev` is bind-mounted with
device cgroup rules for `ttyACM`, `ttyUSB` and `video`.

## Native (Ubuntu 24.04)

This native path is for Ubuntu 24.04 only. On Ubuntu 26.04, use the Docker path
above; this project currently targets Jazzy, not native Lyrical.

1. On Ubuntu 24.04, follow the Jazzy guide in order:
   (https://docs.ros.org/en/jazzy/Installation/Ubuntu-Install-Debs.html). Complete
   **Install ROS 2** first (choose Desktop Install for RViz), then install
   **Development tools** (`ros-dev-tools`). Installing `ros-dev-tools` alone does
   not install ROS 2.
2. Install the workspace dependencies. These are the same packages as in `docker/Dockerfile`; `rosdep` resolves the rest:

   ```bash
   sudo apt install ros-jazzy-moveit ros-jazzy-ros2-control ros-jazzy-ros2-controllers \
     ros-jazzy-gripper-controllers ros-jazzy-ros-gz ros-jazzy-gz-ros2-control \
     ros-jazzy-pilz-industrial-motion-planner ros-jazzy-foxglove-bridge \
     ros-jazzy-launch-testing-ament-cmake clang-format
   pip install --user ruff==0.6.9 pre-commit==3.8.0
   sudo rosdep init 2>/dev/null; rosdep update
   make deps build
   ```
3. Add the user to the serial and video groups: `sudo usermod -aG dialout,video $USER`. Then log out and back in.

## NVIDIA driver and Container Toolkit (Workstations A and B)

1. Install the driver with `sudo ubuntu-drivers install`, then reboot. `nvidia-smi` should list the GPU.
2. Install Docker Engine (https://docs.docker.com/engine/install/ubuntu/) and the
   NVIDIA Container Toolkit
   (https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).
   Then run `sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker`.
3. Check: `docker run --rm --gpus all ubuntu nvidia-smi`.

## Status of these instructions

The Docker path (`make image`, `make docker-build`, `make docker-test`, `make docker-test-sim`)
was executed on 2026-10-30 on an arm64 host without a GPU, and `make image-vnc` on 2026-11-01. The native and NVIDIA
steps follow the upstream guides but have not yet been run on Workstation A or B.
We still need to confirm them on Workstation A.
