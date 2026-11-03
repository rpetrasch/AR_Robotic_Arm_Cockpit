#!/bin/bash
# Regenerate the login screen's robot model and motion (docs/manual/cockpit.md, spec_cockpit.md §7.3).
# Runs inside the dev image (make cockpit-login-assets): expands the URDF with package://
# mesh paths, starts a mock stack without viewers, and records trajectory.json through MoveIt.
set -eo pipefail
cd /ar3_ws
OUT=tools/cockpit/frontend/public/login
source /opt/ros/jazzy/setup.bash
[ -f install/setup.bash ] || colcon build --symlink-install --cmake-args -DBUILD_TESTING=ON
source install/setup.bash

# Same rewrite as viz_description() in bringup.launch.py: file://.../share/<pkg>/ -> package://<pkg>/
xacro "$(ros2 pkg prefix ar3_description)/share/ar3_description/urdf/ar3.urdf.xacro" \
  config_dir:=/ar3_ws/config hardware:=none \
  | sed -E 's#file://[^"]*/share/([A-Za-z0-9_-]+)/#package://\1/#g' > "$OUT/ar3.urdf"
echo "$OUT/ar3.urdf: $(grep -c '<link' "$OUT/ar3.urdf") links"

ros2 launch ar3_bringup bringup.launch.py mode:=mock viz:=none > /tmp/bringup.log 2>&1 &
launch=$!
# SIGTERM, not SIGINT: a background job of a non-interactive shell ignores SIGINT.
trap 'kill -TERM $launch 2>/dev/null || true' EXIT
status=0
python3 tools/cockpit/scripts/record_login_trajectory.py --out "$OUT/trajectory.json" || status=$?
kill -TERM "$launch" 2>/dev/null
wait "$launch" 2>/dev/null || true
exit "$status"
