#!/usr/bin/env bash
# Record a rosbag (MCAP) inside the running ROS stack: `make bag`, cockpit step rosbag.record
# (docs/specs/spec_cockpit.md §10.3). Runs in the container, from /ar3_ws.
#
#   bash docker/record_bag.sh PRESET DURATION_S
#
# PRESET is a list in config/cockpit/telemetry.yaml `bag_presets`. The bag goes to
# data/bags/<UTC time>_<preset>/ in the repo; the cockpit registers it as a recording.
set -eo pipefail
source "/opt/ros/${ROS_DISTRO:-jazzy}/setup.bash"
[ -f install/setup.bash ] && source install/setup.bash
preset="${1:-default}"
duration="${2:-60}"
topics="$(python3 - "$preset" <<'PY'
import sys, yaml
presets = yaml.safe_load(open("config/cockpit/telemetry.yaml"))["bag_presets"]
if sys.argv[1] not in presets:
    sys.exit(f"unknown preset {sys.argv[1]!r}; config/cockpit/telemetry.yaml has: {', '.join(presets)}")
print(" ".join(presets[sys.argv[1]]))
PY
)"
name="$(date -u +%Y%m%d-%H%M%S)_${preset}"
mkdir -p data/bags
echo "recording ${duration} s of: ${topics}"
rc=0
# shellcheck disable=SC2086
timeout -s INT "${duration}" ros2 bag record -s mcap -o "data/bags/${name}" ${topics} || rc=$?
if [ "$rc" -ne 0 ] && [ "$rc" -ne 124 ]; then exit "$rc"; fi  # 124: the duration ended it
ros2 bag info "data/bags/${name}"
echo "bag: data/bags/${name}"
