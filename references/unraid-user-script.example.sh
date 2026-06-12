#!/usr/bin/env bash
set -euo pipefail

# Example for the Unraid User Scripts plugin.
# Keep real secrets in $DATA_DIR/.env only; do not paste them into this file.

APP_DIR="${WECHATPAD_HERMES_APP_DIR:-/mnt/user/appdata/wechatpad-hermes}"
RELEASE_DIR="${WECHATPAD_HERMES_RELEASE_DIR:-$APP_DIR/releases/<release>/hermes-wechatpadpromax}"
DATA_DIR="${WECHATPAD_HERMES_DATA_DIR:-$APP_DIR/data}"
ENV_FILE="${WECHATPAD_ENV_FILE:-$DATA_DIR/.env}"
LOG_FILE="${WECHATPAD_HERMES_LOG_FILE:-$DATA_DIR/bridge.log}"
PID_FILE="${WECHATPAD_HERMES_PID_FILE:-$DATA_DIR/bridge.pid}"
PYTHON_BIN="${PYTHON_BIN:-$RELEASE_DIR/.venv/bin/python}"

export PYTHONPATH="$RELEASE_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
export WECHATPAD_ENV_FILE="$ENV_FILE"
export WECHATPAD_DB_PATH="${WECHATPAD_DB_PATH:-$DATA_DIR/wechatpad-hermes.sqlite3}"
export WECHATPAD_POLICY_PATH="${WECHATPAD_POLICY_PATH:-$DATA_DIR/policy.yaml}"

mkdir -p "$DATA_DIR"

is_running() {
  if [ ! -s "$PID_FILE" ]; then
    return 1
  fi
  local pid
  pid="$(cat "$PID_FILE")"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

start_bridge() {
  if is_running; then
    printf 'bridge already running pid=%s\n' "$(cat "$PID_FILE")"
    return 0
  fi
  "$PYTHON_BIN" -m wechatpad_hermes.doctor --strict --require-public-safe
  nohup "$PYTHON_BIN" -m wechatpad_hermes.bridge >>"$LOG_FILE" 2>&1 &
  printf '%s\n' "$!" >"$PID_FILE"
  printf 'bridge started pid=%s log=%s\n' "$(cat "$PID_FILE")" "$LOG_FILE"
}

stop_bridge() {
  if ! is_running; then
    printf 'bridge is not running\n'
    [ -f "$PID_FILE" ] && rm "$PID_FILE"
    return 0
  fi
  local pid
  pid="$(cat "$PID_FILE")"
  kill "$pid"
  printf 'bridge stop requested pid=%s\n' "$pid"
  sleep 1
  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$PID_FILE"
  fi
}

print_logs() {
  local lines="${2:-80}"
  if [ ! -f "$LOG_FILE" ]; then
    printf 'log file not found: %s\n' "$LOG_FILE" >&2
    return 1
  fi
  tail -n "$lines" "$LOG_FILE"
}

case "${1:-start}" in
  start)
    start_bridge
    ;;
  stop)
    stop_bridge
    ;;
  restart)
    stop_bridge
    sleep 2
    start_bridge
    ;;
  status)
    if is_running; then
      printf 'bridge running pid=%s\n' "$(cat "$PID_FILE")"
    else
      printf 'bridge stopped\n'
    fi
    ;;
  doctor)
    "$PYTHON_BIN" -m wechatpad_hermes.doctor --strict --live --require-public-safe
    ;;
  health)
    "$PYTHON_BIN" scripts/ops_status.py
    ;;
  logs)
    print_logs "$@"
    ;;
  once)
    "$PYTHON_BIN" -m wechatpad_hermes.bridge --once
    ;;
  *)
    printf 'usage: %s {start|stop|restart|status|doctor|health|logs|once}\n' "$0" >&2
    exit 2
    ;;
esac
