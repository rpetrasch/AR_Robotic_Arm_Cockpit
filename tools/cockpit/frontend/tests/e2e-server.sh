#!/usr/bin/env bash
# Backend for the Playwright e2e tests (playwright.config.ts starts it): fake mode, its own
# database (ar3cockpit_e2e, recreated empty), Redis db 2, port 8401, data dir .e2e-data.
# Needs the cockpit DB + Redis running (./cockpit.sh once, or the CI service containers).
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="$HERE/../backend"
PY="$BACKEND/.venv/bin/python"
export COCKPIT_FAKE="${COCKPIT_FAKE:-1}" COCKPIT_DB_NAME=ar3cockpit_e2e COCKPIT_PORT=8401
export REDIS_URL="${E2E_REDIS_URL:-redis://127.0.0.1:6380/2}" COCKPIT_DATA_DIR="$HERE/.e2e-data"
export COCKPIT_FRONTEND_DIR="$HERE/out" COCKPIT_EXPORT_DIR="$HERE/.e2e-data/exports"
export COCKPIT_SCENARIOS_DIR="$HERE/.e2e-data/scenarios"
rm -rf "$COCKPIT_DATA_DIR" && mkdir -p "$COCKPIT_DATA_DIR" "$COCKPIT_SCENARIOS_DIR"
cp "$HERE"/../../../config/scenarios/*.yaml "$COCKPIT_SCENARIOS_DIR/"  # the repo's examples
cd "$BACKEND"
"$PY" - <<'PY'
import os, psycopg
from config import envfiles
envfiles.load()
e = os.environ
with psycopg.connect(host=e["COCKPIT_DB_HOST"], port=e["COCKPIT_DB_PORT"], user=e["COCKPIT_DB_USER"],
                     password=e["COCKPIT_DB_PASSWORD"], dbname="postgres", autocommit=True) as conn:
    conn.execute("DROP DATABASE IF EXISTS ar3cockpit_e2e WITH (FORCE)")
    conn.execute("CREATE DATABASE ar3cockpit_e2e")
PY
"$PY" manage.py migrate --noinput -v 0
"$PY" manage.py run_monitor & monitor=$!
"$PY" manage.py run_jobs & jobs=$!
"$PY" manage.py run_ingest & ingest=$!
trap 'kill $monitor $jobs $ingest 2>/dev/null' EXIT
exec_status=0
"$BACKEND/.venv/bin/uvicorn" config.asgi:application --host 127.0.0.1 --port 8401 --no-access-log \
  --ws websockets || exec_status=$?
exit $exec_status
