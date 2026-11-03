#!/bin/bash
# Entry point for the mac, vnc, linux and remote compose services: build the workspace
# if it has not been built yet, then bring up AR3_MODE (mock | sim) with the given
# launch arguments. AR3_HEADLESS=1 adds headless:=true viz:=foxglove (no windows).
# AR3_MODE=remote runs only the cockpit agent (REMOTE: the robot is Workstation A's).
# mode:=real is never started from here.
set -e
cd /ar3_ws
case "${AR3_MODE:=mock}" in
  mock | sim | remote) ;;
  *) echo "AR3_MODE must be mock, sim or remote in this container (got '${AR3_MODE}')" >&2; exit 2 ;;
esac
if [ ! -f install/setup.bash ]; then
  colcon build --symlink-install --cmake-args -DBUILD_TESTING=ON
fi
source install/setup.bash
if [ "${AR3_MODE}" = remote ]; then
  exec ros2 run ar3_cockpit_agent cockpit_agent --ros-args -p mode:=remote "$@"
fi
extra=()
[ "${AR3_HEADLESS:-0}" = 1 ] && extra=(headless:=true viz:=foxglove)
exec ros2 launch ar3_bringup bringup.launch.py mode:="${AR3_MODE}" "${extra[@]}" "$@"
