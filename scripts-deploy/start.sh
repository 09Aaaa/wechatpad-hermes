#!/usr/bin/env bash
# WeChatPad-Hermes bridge startup script
# Created: 2026-06-15
set -euo pipefail

BRIDGE_DIR="/mnt/user/appdata/wechatpad-hermes/releases/dryrun-20260612185834/hermes-wechatpadpromax"
DATA_DIR="/mnt/user/appdata/wechatpad-hermes/data"
ENV_FILE="$DATA_DIR/.env"
PID_FILE="$DATA_DIR/bridge.pid"
LOG_FILE="$DATA_DIR/bridge.log"

if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: $ENV_FILE not found" >&2
  exit 1
fi

# Kill existing bridge if running
if [ -f "$PID_FILE" ]; then
  OLD_PID=$(cat "$PID_FILE")
  if kill -0 "$OLD_PID" 2>/dev/null; then
    echo "Stopping existing bridge (PID $OLD_PID)..."
    kill "$OLD_PID" 2>/dev/null || true
    sleep 2
    kill -9 "$OLD_PID" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
fi

cd "$BRIDGE_DIR"

# Source env
set -a
source "$ENV_FILE"
set +a

# Start bridge
echo "Starting bridge from $BRIDGE_DIR ..."
nohup .venv/bin/python -m wechatpad_hermes.bridge >> "$LOG_FILE" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PID_FILE"
echo "Bridge started (PID $NEW_PID)"