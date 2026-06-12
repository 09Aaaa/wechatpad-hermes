#!/usr/bin/env bash
set -euo pipefail

# Copy this script to a writable Unraid share next to the release archive,
# then run it from the Unraid terminal. It contains no authcodes, passwords,
# API keys, or server-specific secrets.

APP_DIR="${WECHATPAD_HERMES_APP_DIR:-/mnt/user/appdata/wechatpad-hermes}"
DROP_DIR="${WECHATPAD_HERMES_DROP_DIR:-/mnt/user/appdata/_wechatpad_hermes_drop}"
RELEASE="${WECHATPAD_HERMES_RELEASE:-dryrun-YYYYMMDDHHMMSS}"
ARCHIVE="${WECHATPAD_HERMES_ARCHIVE:-$DROP_DIR/hermes-wechatpadpromax-YYYYMMDDHHMMSS.tar.gz}"
EXPECTED_SHA256="${WECHATPAD_HERMES_SHA256:-}"
DATA_DIR="${WECHATPAD_HERMES_DATA_DIR:-$APP_DIR/data}"
RELEASE_ROOT="$APP_DIR/releases/$RELEASE"
PROJECT_DIR="$RELEASE_ROOT/hermes-wechatpadpromax"

if [ ! -f "$ARCHIVE" ]; then
  printf 'archive not found: %s\n' "$ARCHIVE" >&2
  exit 1
fi

if [ -n "$EXPECTED_SHA256" ]; then
  actual="$(sha256sum "$ARCHIVE" | awk '{print $1}')"
  if [ "${actual,,}" != "${EXPECTED_SHA256,,}" ]; then
    printf 'sha256 mismatch for %s\nexpected: %s\nactual:   %s\n' "$ARCHIVE" "$EXPECTED_SHA256" "$actual" >&2
    exit 1
  fi
  printf 'sha256 ok: %s\n' "$actual"
fi

if [ -e "$PROJECT_DIR" ]; then
  printf 'release already exists: %s\n' "$PROJECT_DIR" >&2
  exit 1
fi

mkdir -p "$RELEASE_ROOT" "$DATA_DIR"
tar -xzf "$ARCHIVE" -C "$RELEASE_ROOT"

cd "$PROJECT_DIR"
WECHATPAD_HERMES_DATA_DIR="$DATA_DIR" bash scripts/install_local.sh

cat <<EOF
install finished

Release: $PROJECT_DIR
Data:    $DATA_DIR

Next dry-run checks after creating $DATA_DIR/.env:
  cd "$PROJECT_DIR"
  WECHATPAD_HERMES_RELEASE="$RELEASE" bash references/verify-unraid-dryrun.example.sh

Keep WECHATPAD_SEND_ENABLED=false and WECHATPAD_DRY_RUN=true until you explicitly choose real sending.
EOF
