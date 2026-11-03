#!/usr/bin/env bash
# AR3 Cockpit bootstrap (docs/manual/cockpit.md; docs/specs/spec_cockpit.md §4).
#
#   ./cockpit.sh            check prerequisites, start DB + Redis, build the frontend if
#                           needed, migrate, then run the web server and the workers
#                           run_monitor, run_jobs, run_ingest (Ctrl-C stops them; DB and
#                           Redis keep running)
#   ./cockpit.sh --fake     the same with COCKPIT_FAKE=1 (simulated Docker/ROS/devices)
#   ./cockpit.sh down       stop DB + Redis (the data stays in the ar3cockpit_pgdata volume)
#
# Settings: process env > .env (repo root) > tools/cockpit/backend/defaults.env.
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$REPO/tools/cockpit/backend"
FRONTEND="$REPO/tools/cockpit/frontend"
COMPOSE=(docker compose -f "$REPO/docker/compose.yaml" --profile cockpit)

case "${1:-}" in
  down) exec "${COMPOSE[@]}" stop cockpit-db cockpit-redis ;;
  --fake) export COCKPIT_FAKE=1 ;;
  "") ;;
  *) sed -n '2,12p' "$0"; exit 2 ;;
esac

# 1. uv creates the Python environment; everything else is checked by ar3ctl.
# The uv installer puts it in ~/.local/bin, which a shell that was already running
# (or a desktop session started before the install) does not have on PATH yet.
if ! command -v uv >/dev/null && [ -x "$HOME/.local/bin/uv" ]; then
  export PATH="$HOME/.local/bin:$PATH"
fi
if ! command -v uv >/dev/null; then
  echo "uv (Python package manager) is missing. Install it, then run ./cockpit.sh again:"
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi
(cd "$BACKEND" && uv sync --locked --quiet)
AR3CTL="$BACKEND/.venv/bin/ar3ctl"
PY="$BACKEND/.venv/bin/python"

# 2. Host, prerequisites, Docker: the same checks as `ar3ctl doctor --pre`.
# Always the real host, also with --fake: the database needs the real Docker.
if ! COCKPIT_FAKE=0 "$AR3CTL" doctor --pre; then
  echo
  echo "Fix the failed checks above (commands shown with \$), then run ./cockpit.sh again."
  exit 1
fi

# 3. Secrets on the first run, then the values compose and uvicorn need.
"$AR3CTL" init-env
eval "$("$AR3CTL" env-export COCKPIT_DB_NAME COCKPIT_DB_USER COCKPIT_DB_PASSWORD \
  COCKPIT_DB_PORT COCKPIT_BIND COCKPIT_PORT REDIS_URL)"
[[ "$REDIS_URL" =~ :([0-9]+)(/|$) ]] && export COCKPIT_REDIS_PORT="${BASH_REMATCH[1]}"

# 4. Database and Redis (compose profile `cockpit`).
echo "Starting the cockpit database and Redis ..."
"${COMPOSE[@]}" up -d --wait cockpit-db cockpit-redis

# 5. Frontend: build the static export when it is missing or older than its sources.
if [ ! -f "$FRONTEND/out/index.html" ] || [ -n "$(find "$FRONTEND"/{app,components,lib,public} \
    "$FRONTEND"/package.json -newer "$FRONTEND/out/index.html" -print -quit 2>/dev/null)" ]; then
  echo "Building the frontend ..."
  (cd "$FRONTEND" && { [ -d node_modules ] && [ node_modules -nt package-lock.json ] || npm ci; } \
    && npm run build)
fi

# 6. Migrations, then the processes.
(cd "$BACKEND" && "$PY" manage.py migrate --noinput -v 0)
URL="http://${COCKPIT_BIND/0.0.0.0/127.0.0.1}:${COCKPIT_PORT}"
if [ "$(cd "$BACKEND" && "$PY" manage.py shell -v 0 -c \
    'from django.contrib.auth.models import User; print(int(User.objects.exists()))')" = 0 ]; then
  echo "No cockpit user yet: open $URL/setup-account on this machine, or run"
  echo "  $AR3CTL create-user <name>"
fi

cd "$BACKEND"
pids=()
# The processes run in the background, where a script's children ignore SIGINT: Ctrl-C reaches
# this trap, which stops them with SIGTERM.
stop() { kill "${pids[@]}" 2>/dev/null || true; wait 2>/dev/null || true; }
trap 'stop; exit 0' INT TERM
trap stop EXIT
"$PY" manage.py run_monitor & pids+=($!)
"$PY" manage.py run_jobs & pids+=($!)
"$PY" manage.py run_ingest & pids+=($!)
echo "AR3 Cockpit on $URL${COCKPIT_FAKE:+ (fake mode)}  (Ctrl-C stops it)"
"$BACKEND/.venv/bin/uvicorn" config.asgi:application --host "$COCKPIT_BIND" \
  --port "$COCKPIT_PORT" --no-access-log --ws websockets & pids+=($!)
# Portable to macOS bash 3.2 (no wait -n): stop when any of the processes ends.
alive() { for p in "${pids[@]}"; do kill -0 "$p" 2>/dev/null || return 1; done; }
while alive; do sleep 1; done
