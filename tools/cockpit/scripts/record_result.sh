#!/usr/bin/env bash
# Records the result of a make target for the cockpit's checklist (docs/manual/cockpit.md):
#   record_result.sh <name> <exit code>  ->  $COCKPIT_RESULTS/<name>.json (default .cockpit/results)
set -euo pipefail
name="$1" rc="$2"
# Not under build/: the dev container runs as root and creates build/ owned by root.
dir="${COCKPIT_RESULTS:-.cockpit/results}"
mkdir -p "$dir"
commit="$(git rev-parse --verify -q HEAD 2>/dev/null || echo unknown)"
printf '{"rc": %d, "commit": "%s", "finished": "%s"}\n' "$rc" "$commit" \
  "$(date -u '+%Y-%m-%d %H:%M UTC')" > "$dir/$name.json"
