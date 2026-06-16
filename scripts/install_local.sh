#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${WECHATPAD_HERMES_DATA_DIR:-/opt/hermes/data/wechatpad-hermes}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

mkdir -p "$DATA_DIR"

if [ ! -f "$DATA_DIR/policy.yaml" ]; then
  cp "$PROJECT_DIR/policy.example.yaml" "$DATA_DIR/policy.yaml"
  printf 'created %s\n' "$DATA_DIR/policy.yaml"
else
  printf 'kept existing %s\n' "$DATA_DIR/policy.yaml"
fi

if [ ! -d "$PROJECT_DIR/.venv" ]; then
  "$PYTHON_BIN" -m venv "$PROJECT_DIR/.venv"
fi

. "$PROJECT_DIR/.venv/bin/activate"
pip install -r "$PROJECT_DIR/requirements.txt"
pip install -e "$PROJECT_DIR"

export WECHATPAD_DB_PATH="${WECHATPAD_DB_PATH:-$DATA_DIR/wechatpad-hermes.sqlite3}"
export WECHATPAD_POLICY_PATH="${WECHATPAD_POLICY_PATH:-$DATA_DIR/policy.yaml}"
export WECHATPAD_SEND_ENABLED="${WECHATPAD_SEND_ENABLED:-false}"
export WECHATPAD_DRY_RUN="${WECHATPAD_DRY_RUN:-true}"
export WECHATPAD_ADMIN_TOOLS_ENABLED="${WECHATPAD_ADMIN_TOOLS_ENABLED:-false}"
export WECHATPAD_ALLOW_UNKNOWN_OUTBOUND="${WECHATPAD_ALLOW_UNKNOWN_OUTBOUND:-false}"
export WECHATPAD_STORE_RAW_MESSAGES="${WECHATPAD_STORE_RAW_MESSAGES:-false}"

python "$PROJECT_DIR/scripts/smoke_test.py"
python "$PROJECT_DIR/scripts/mcp_stdio_smoke.py"
python -m wechatpad_hermes.doctor --require-public-safe

cat <<EOF
install ok

Next:
  1. Put real secrets only in the host env file.
  2. Run: python -m wechatpad_hermes.doctor --strict --live --require-public-safe
  3. Keep WECHATPAD_SEND_ENABLED=false and WECHATPAD_DRY_RUN=true until dry-run is verified.
EOF
