#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

APP_NAME="personal-knowledge-rag-webapp"
LABEL="com.maber2k.personal-knowledge-rag-webapp"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8765}"
URL="http://${HOST}:${PORT}"
LOCAL_MODELS_DIR="${LOCAL_MODELS_DIR:-${PROJECT_ROOT}/local_models}"
PLIST_PATH="${HOME}/Library/LaunchAgents/${LABEL}.plist"
PID_FILE="${PROJECT_ROOT}/run/webapp.pid"
OUT_LOG="${PROJECT_ROOT}/logs/webapp.out.log"
ERR_LOG="${PROJECT_ROOT}/logs/webapp.err.log"
LAUNCHD_OUT_LOG="${PROJECT_ROOT}/logs/launchd.out.log"
LAUNCHD_ERR_LOG="${PROJECT_ROOT}/logs/launchd.err.log"

mkdir -p logs run

python_bin() {
  if [ -n "${PKB_PYTHON:-}" ]; then
    printf '%s\n' "$PKB_PYTHON"
  elif [ -n "${PYTHON:-}" ]; then
    printf '%s\n' "$PYTHON"
  else
    command -v python3
  fi
}

health() {
  "$(python_bin)" - "$URL/api/health" <<'PY'
import json
import sys
import urllib.request

url = sys.argv[1]
try:
    with urllib.request.urlopen(url, timeout=2) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("status") == "ok":
        print("ok")
        raise SystemExit(0)
    print(payload)
except Exception as exc:
    print(f"not-ready: {exc}")
    raise SystemExit(1)
PY
}

port_owner() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true
  fi
}

pid_alive() {
  local pid="${1:-}"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

start() {
  if health >/dev/null 2>&1; then
    echo "${APP_NAME} already running at ${URL}"
    return 0
  fi

  local owner
  owner="$(port_owner)"
  if [ -n "$owner" ]; then
    echo "Port ${PORT} is already occupied, but health check failed:"
    echo "$owner"
    return 2
  fi

  HOST="$HOST" PORT="$PORT" \
    PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/pkb-pycache}" \
    nohup "${PROJECT_ROOT}/scripts/run_server.sh" >>"$OUT_LOG" 2>>"$ERR_LOG" &
  echo "$!" > "$PID_FILE"

  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if health >/dev/null 2>&1; then
      echo "${APP_NAME} started at ${URL}"
      return 0
    fi
    sleep 0.5
  done

  echo "${APP_NAME} did not become healthy. See ${ERR_LOG}"
  return 1
}

stop() {
  if [ -f "$PID_FILE" ]; then
    local pid
    pid="$(cat "$PID_FILE")"
    if pid_alive "$pid"; then
      kill "$pid" || true
      for _ in 1 2 3 4 5; do
        pid_alive "$pid" || break
        sleep 0.5
      done
    fi
    rm -f "$PID_FILE"
  fi

  if health >/dev/null 2>&1; then
    echo "A service is still responding at ${URL}. If it was started by launchd, use: $0 unload"
    return 2
  fi

  echo "${APP_NAME} stopped"
}

status() {
  echo "App: ${APP_NAME}"
  echo "URL: ${URL}"
  echo "Project: ${PROJECT_ROOT}"
  echo "LaunchAgent: ${PLIST_PATH}"
  if health >/dev/null 2>&1; then
    echo "Health: ok"
  else
    echo "Health: not ready"
  fi
  if [ -f "$PID_FILE" ]; then
    echo "PID file: $(cat "$PID_FILE")"
  else
    echo "PID file: none"
  fi
  local owner
  owner="$(port_owner)"
  if [ -n "$owner" ]; then
    echo "Port owner:"
    echo "$owner"
  else
    echo "Port owner: none"
  fi
  if launchctl print "gui/$(id -u)/${LABEL}" >/dev/null 2>&1; then
    echo "LaunchAgent: loaded"
  else
    echo "LaunchAgent: not loaded"
  fi
}

plist() {
  cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PROJECT_ROOT}/scripts/run_server.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${PROJECT_ROOT}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOST</key>
    <string>${HOST}</string>
    <key>PORT</key>
    <string>${PORT}</string>
    <key>LOCAL_MODELS_DIR</key>
    <string>${LOCAL_MODELS_DIR}</string>
    <key>PYTHONPYCACHEPREFIX</key>
    <string>/tmp/pkb-pycache</string>
  </dict>
  <key>StandardOutPath</key>
  <string>${LAUNCHD_OUT_LOG}</string>
  <key>StandardErrorPath</key>
  <string>${LAUNCHD_ERR_LOG}</string>
</dict>
</plist>
PLIST
}

load() {
  mkdir -p "${HOME}/Library/LaunchAgents"
  plist > "$PLIST_PATH"
  plutil -lint "$PLIST_PATH"
  launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
  launchctl enable "gui/$(id -u)/${LABEL}"
  launchctl kickstart -k "gui/$(id -u)/${LABEL}"
  echo "LaunchAgent loaded: ${PLIST_PATH}"
}

unload() {
  launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
  rm -f "$PLIST_PATH"
  echo "LaunchAgent removed: ${PLIST_PATH}"
}

logs() {
  echo "== app stdout =="
  tail -n 80 "$OUT_LOG" 2>/dev/null || true
  echo "== app stderr =="
  tail -n 80 "$ERR_LOG" 2>/dev/null || true
  echo "== launchd stdout =="
  tail -n 80 "$LAUNCHD_OUT_LOG" 2>/dev/null || true
  echo "== launchd stderr =="
  tail -n 80 "$LAUNCHD_ERR_LOG" 2>/dev/null || true
}

usage() {
  cat <<USAGE
Usage: $0 <command>

Commands:
  start              Start in the background for the current session
  stop               Stop the background process started by this script
  restart            Stop then start
  status             Show health, port owner, and LaunchAgent state
  health             Check /api/health
  load               Install and start the macOS LaunchAgent autostart service
  unload             Remove the macOS LaunchAgent autostart service
  plist              Print the generated LaunchAgent plist
  logs               Show recent logs
USAGE
}

case "${1:-status}" in
  start) start ;;
  stop) stop ;;
  restart) stop || true; start ;;
  status) status ;;
  health) health ;;
  load|install-autostart) load ;;
  unload|uninstall-autostart) unload ;;
  plist) plist ;;
  logs) logs ;;
  *) usage; exit 2 ;;
esac
