#!/usr/bin/env bash
set -euo pipefail

# Run from Unraid after installing a release and creating the private data .env.
# This script does not contain secrets; it sources them from DATA_DIR/.env.

APP_DIR="${WECHATPAD_HERMES_APP_DIR:-/mnt/user/appdata/wechatpad-hermes}"
RELEASE="${WECHATPAD_HERMES_RELEASE:-dryrun-YYYYMMDDHHMMSS}"
DATA_DIR="${WECHATPAD_HERMES_DATA_DIR:-$APP_DIR/data}"
PROJECT_DIR="${WECHATPAD_HERMES_PROJECT_DIR:-$APP_DIR/releases/$RELEASE/hermes-wechatpadpromax}"
ENV_FILE="${WECHATPAD_ENV_FILE:-$DATA_DIR/.env}"
PYTHON_BIN="${PYTHON_BIN:-$PROJECT_DIR/.venv/bin/python}"

if [ ! -d "$PROJECT_DIR" ]; then
  printf 'project directory not found: %s\n' "$PROJECT_DIR" >&2
  exit 1
fi

if [ ! -x "$PYTHON_BIN" ]; then
  printf 'python not found or not executable: %s\n' "$PYTHON_BIN" >&2
  exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
  printf 'env file not found: %s\n' "$ENV_FILE" >&2
  printf 'Create it from .env.example and keep WECHATPAD_SEND_ENABLED=false / WECHATPAD_DRY_RUN=true for this check.\n' >&2
  exit 1
fi

set -a
. "$ENV_FILE"
set +a

export PYTHONPATH="$PROJECT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
export WECHATPAD_ENV_FILE="$ENV_FILE"
export WECHATPAD_DB_PATH="${WECHATPAD_DB_PATH:-$DATA_DIR/wechatpad-hermes.sqlite3}"
export WECHATPAD_POLICY_PATH="${WECHATPAD_POLICY_PATH:-$DATA_DIR/policy.yaml}"
export WECHATPAD_SEND_ENABLED="${WECHATPAD_SEND_ENABLED:-false}"
export WECHATPAD_DRY_RUN="${WECHATPAD_DRY_RUN:-true}"
export WECHATPAD_ADMIN_TOOLS_ENABLED="${WECHATPAD_ADMIN_TOOLS_ENABLED:-false}"
export WECHATPAD_STORE_RAW_MESSAGES="${WECHATPAD_STORE_RAW_MESSAGES:-false}"

cd "$PROJECT_DIR"

printf '\n== local smoke ==\n'
"$PYTHON_BIN" scripts/smoke_test.py

printf '\n== MCP stdio smoke ==\n'
"$PYTHON_BIN" scripts/mcp_stdio_smoke.py

printf '\n== redacted ops status ==\n'
"$PYTHON_BIN" scripts/ops_status.py

printf '\n== Hermes MCP config preview ==\n'
"$PYTHON_BIN" scripts/render_mcp_config.py --release "$RELEASE"

printf '\n== WeChatPad online/cache status ==\n'
"$PYTHON_BIN" scripts/wechatpad_status.py

printf '\n== strict live doctor ==\n'
"$PYTHON_BIN" -m wechatpad_hermes.doctor --strict --live --require-public-safe

printf '\n== bridge once dry-run ==\n'
"$PYTHON_BIN" -m wechatpad_hermes.bridge --once

cat <<EOF

dry-run verification finished

Confirm before any long-running service:
  WECHATPAD_SEND_ENABLED=false
  WECHATPAD_DRY_RUN=true
  WECHATPAD_ADMIN_TOOLS_ENABLED=false
  WECHATPAD_STORE_RAW_MESSAGES=false

Do not enable real sending until Hermes endpoint behavior and privacy blocking are verified with real incoming messages.
EOF
