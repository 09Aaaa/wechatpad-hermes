#!/usr/bin/env bash
set -euo pipefail

# Run from the Unraid terminal as root if Windows can upload files to the drop
# share but cannot write into the protected appdata release directory.
# This authorizes only the public key copied to the drop share; it contains no
# passwords, authcodes, API keys, or WeChat identifiers.

DROP_DIR="${WECHATPAD_HERMES_DROP_DIR:-/mnt/user/appdata/_wechatpad_hermes_drop}"
PUBKEY_FILE="${WECHATPAD_HERMES_PUBKEY_FILE:-$DROP_DIR/codex-unraid-wechatpad.pub}"
SSH_DIR="/root/.ssh"
AUTHORIZED_KEYS="$SSH_DIR/authorized_keys"

if [ "$(id -u)" -ne 0 ]; then
  printf 'run this from the Unraid terminal as root\n' >&2
  exit 1
fi

if [ ! -f "$PUBKEY_FILE" ]; then
  printf 'public key not found: %s\n' "$PUBKEY_FILE" >&2
  exit 1
fi

pubkey="$(sed -n '1p' "$PUBKEY_FILE")"
if ! printf '%s\n' "$pubkey" | grep -Eq '^ssh-ed25519 [A-Za-z0-9+/=]+ [A-Za-z0-9._@:+-]+$'; then
  printf 'public key file does not look like one ed25519 public key\n' >&2
  exit 1
fi

install -d -m 700 "$SSH_DIR"
touch "$AUTHORIZED_KEYS"
chmod 600 "$AUTHORIZED_KEYS"

if grep -qxF "$pubkey" "$AUTHORIZED_KEYS"; then
  printf 'public key already authorized\n'
else
  printf '%s\n' "$pubkey" >> "$AUTHORIZED_KEYS"
  printf 'public key authorized\n'
fi

printf 'You can now test key-based SSH from Windows with the dedicated private key.\n'
