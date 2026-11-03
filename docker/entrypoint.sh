#!/bin/bash
# Source ROS and, if present, the workspace overlay, then run the command.
set -e
source "/opt/ros/${ROS_DISTRO}/setup.bash"
if [ -f /ar3_ws/install/setup.bash ]; then
  source /ar3_ws/install/setup.bash
fi
exec "$@"
