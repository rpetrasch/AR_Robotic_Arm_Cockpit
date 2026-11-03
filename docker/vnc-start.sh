#!/bin/bash
# Start a VNC desktop on $DISPLAY (TigerVNC + openbox), serve it through noVNC
# on port ${NOVNC_PORT:-6080}, then run the given command on that display.
# No VNC password: the port is only published to the host (see compose.yaml).
set -e
display_num="${DISPLAY#:}"
# A restarted container still has the previous run's X lock and socket.
rm -f "/tmp/.X${display_num}-lock" "/tmp/.X11-unix/X${display_num}"
Xvnc "$DISPLAY" -geometry "${VNC_GEOMETRY:-1600x900}" -depth 24 \
  -SecurityTypes None -localhost yes -rfbport $((5900 + display_num)) >/tmp/xvnc.log 2>&1 &
xvnc_pid=$!
for _ in $(seq 50); do
  [ -S "/tmp/.X11-unix/X${display_num}" ] && break
  sleep 0.1
done
if ! kill -0 "$xvnc_pid" 2>/dev/null || [ ! -S "/tmp/.X11-unix/X${display_num}" ]; then
  echo "Xvnc did not start on ${DISPLAY}:" >&2
  cat /tmp/xvnc.log >&2
  exit 1
fi
openbox >/tmp/openbox.log 2>&1 &
websockify --web /usr/share/novnc "${NOVNC_PORT:-6080}" "localhost:$((5900 + display_num))" \
  >/tmp/websockify.log 2>&1 &
echo "noVNC: http://localhost:${NOVNC_PORT:-6080}/vnc.html"
exec "$@"
