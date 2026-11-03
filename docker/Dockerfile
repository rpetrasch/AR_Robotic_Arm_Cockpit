# AR3 dev image for Workstation A (driver, ros2_control, MoveIt, Gazebo, RViz).
# Multi-arch: builds on amd64 (workstations, CI) and arm64 (Apple Silicon dev).
#
#   docker build -f docker/Dockerfile --target dev -t ar3-dev .
#   docker build -f docker/Dockerfile --target vnc -t ar3-dev-vnc .   # + noVNC desktop
#   docker buildx build -f docker/Dockerfile --platform linux/amd64,linux/arm64 .
#
# Every package below exists for both amd64 and arm64. Anything amd64-only
# (CUDA, vendor SDKs) must go into its own stage, guarded by TARGETARCH, and is
# not part of this file yet (the AI image comes with Phase 4).
#
# GPU access (Workstation A, NVIDIA): run with `--gpus all` and the NVIDIA
# Container Toolkit installed on the host; nothing GPU-specific is baked in here.
ARG ROS_DISTRO=jazzy
FROM ros:${ROS_DISTRO}-ros-base AS dev

ARG ROS_DISTRO
ENV DEBIAN_FRONTEND=noninteractive \
    ROS_DISTRO=${ROS_DISTRO} \
    RMW_IMPLEMENTATION=rmw_fastrtps_cpp \
    PIP_BREAK_SYSTEM_PACKAGES=1

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Keep this list in sync with the package.xml files; `rosdep install` in CI is
# the source of truth, this layer only exists to make rebuilds fast.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ros-${ROS_DISTRO}-xacro \
      ros-${ROS_DISTRO}-robot-state-publisher \
      ros-${ROS_DISTRO}-joint-state-publisher \
      ros-${ROS_DISTRO}-ros2-control \
      ros-${ROS_DISTRO}-ros2-controllers \
      ros-${ROS_DISTRO}-gripper-controllers \
      ros-${ROS_DISTRO}-moveit \
      ros-${ROS_DISTRO}-moveit-planners-chomp \
      ros-${ROS_DISTRO}-moveit-servo \
      ros-${ROS_DISTRO}-pilz-industrial-motion-planner \
      ros-${ROS_DISTRO}-ros-gz \
      ros-${ROS_DISTRO}-gz-ros2-control \
      ros-${ROS_DISTRO}-rviz2 \
      ros-${ROS_DISTRO}-foxglove-bridge \
      ros-${ROS_DISTRO}-launch-testing-ament-cmake \
      ros-${ROS_DISTRO}-ament-cmake-pytest \
      ros-${ROS_DISTRO}-rmw-zenoh-cpp \
      python3-pytest \
      python3-colcon-common-extensions \
      python3-vcstool \
      python3-pip \
      clang-format \
      git \
      mesa-utils \
      libegl1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir --ignore-installed ruff==0.6.9 pre-commit==3.8.0

# ar3_cockpit_agent (HTTP adapter for the AR3 Cockpit). Its own layer, so adding it did not
# invalidate the large package layer above.
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3-fastapi \
      python3-uvicorn \
      python3-httpx \
    && rm -rf /var/lib/apt/lists/*

# Non-root user matching the typical host UID so bind-mounted files stay owned
# by the developer. The ros base image already ships an `ubuntu` user (1000).
RUN usermod -aG dialout,video,plugdev ubuntu 2>/dev/null || true

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
# Interactive shells that bypass the entrypoint (`docker compose exec ... bash`).
RUN printf '%s\n' "source /opt/ros/${ROS_DISTRO}/setup.bash" \
      '[ -f /ar3_ws/install/setup.bash ] && source /ar3_ws/install/setup.bash' >> /etc/bash.bashrc

# Late in the stage so a new value does not invalidate the package layers above.
# `make image` passes the hash of the image inputs; the cockpit compares it (rebuild needed?).
ARG DOCKERFILE_HASH=""
LABEL ar3.dockerfile_hash="${DOCKERFILE_HASH}"

WORKDIR /ar3_ws
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Browser desktop (SPEC §4, optional): TigerVNC + openbox, served by noVNC on
# port 6080. `docker/vnc-start.sh` starts the desktop, then runs the command on it.
FROM dev AS vnc
RUN apt-get update && apt-get install -y --no-install-recommends \
      tigervnc-standalone-server \
      openbox \
      novnc \
      websockify \
    && rm -rf /var/lib/apt/lists/*
COPY docker/vnc-start.sh /vnc-start.sh
RUN chmod +x /vnc-start.sh
ENV DISPLAY=:1
EXPOSE 6080
ENTRYPOINT ["/entrypoint.sh", "/vnc-start.sh"]
CMD ["bash"]
