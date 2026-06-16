# Deployment Checklist

This project is safe by default: MCP tools do not send real WeChat messages unless sending is explicitly enabled, and raw/unknown/stale outbound targets are blocked.

## Recommended Paths

Hermes skill/package path:

```text
/opt/hermes/data/skills/lvwan/wechatpad-hermes
```

Runtime data path:

```text
/opt/hermes/data/wechatpad-hermes
```

Treat the runtime data path as private. The SQLite database contains redacted searchable message text plus local routing identifiers needed to send replies, so it should stay in appdata/data, not in the skill package, public docs, MCP JSON, or chat-visible artifacts.

If Hermes on the target host uses `/opt/data` instead, keep the same subpaths under `/opt/data` and update the examples accordingly.

## Install

Build a clean release archive from the project root when copying to another host:

```bash
python scripts/package_release.py --dry-run
python scripts/package_release.py
```

The archive helper excludes `.venv`, `__pycache__`, sqlite databases, real `.env` files, egg-info, and previous archives.

After packaging, run the release scanner before upload. It fails on packaged runtime files, private-key markers, UUID-shaped authcodes, non-local IP literals, and obvious real secret assignments:

```bash
python scripts/scan_release.py /path/to/hermes-wechatpadpromax-YYYYMMDDHHMMSS.tar.gz \
  --installer /path/to/install-wechatpad-hermes-YYYYMMDDHHMMSS.sh
```

```bash
cd /opt/hermes/data/skills/lvwan/wechatpad-hermes
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

Or use the bundled installer, which creates/keeps the policy file, installs the venv, and runs smoke/doctor checks. Use `bash` explicitly because archives copied from Windows may not preserve the executable bit:

```bash
WECHATPAD_HERMES_DATA_DIR=/opt/hermes/data/wechatpad-hermes bash scripts/install_local.sh
```

On Unraid, a verified standalone dry-run layout is:

```text
/mnt/user/appdata/wechatpad-hermes/releases/<release>/hermes-wechatpadpromax
/mnt/user/appdata/wechatpad-hermes/data
```

If this Windows workstation does not have an SSH key for the Unraid host, do not put the server password in shell commands, scripts, MCP config, or logs. Either upload the release archive with an interactive SFTP/File Manager session, or configure an SSH key first and then copy the archive. After upload, extract it into a timestamped release directory and keep the existing data directory separate from the release code.

If the Windows SMB mapping can read `appdata/wechatpad-hermes` but cannot write inside that existing directory, do not loosen permissions by guessing from Windows. Use one of these safe paths instead:

- Upload the release archive to a writable ordinary share, then use the Unraid web File Manager or terminal to move/extract it under `/mnt/user/appdata/wechatpad-hermes/releases/<release>/`.
- Or create an SSH key for this workstation, add the public key to the Unraid root account, then copy/extract the archive with key-based SSH. `references/authorize-unraid-ssh-key.example.sh` is a server-side helper for authorizing a public key that was copied to the drop share; it contains no passwords, authcodes, API keys, or WeChat identifiers.

In either path, keep `/mnt/user/appdata/wechatpad-hermes/data` as the only runtime-data directory and do not replace it with files from the release archive.

Unraid terminal extraction shape:

```bash
release=dryrun-YYYYMMDDHHMMSS
mkdir -p "/mnt/user/appdata/wechatpad-hermes/releases/$release"
tar -xzf /path/to/hermes-wechatpadpromax-YYYYMMDDHHMMSS.tar.gz -C "/mnt/user/appdata/wechatpad-hermes/releases/$release"
cd "/mnt/user/appdata/wechatpad-hermes/releases/$release/hermes-wechatpadpromax"
WECHATPAD_HERMES_DATA_DIR=/mnt/user/appdata/wechatpad-hermes/data bash scripts/install_local.sh
```

The release archive intentionally does not contain `.env`, SQLite files, or real secrets. Create or edit `/mnt/user/appdata/wechatpad-hermes/data/.env` only from the Unraid side or another trusted secret manager.

For that layout, run:

```bash
WECHATPAD_HERMES_DATA_DIR=/mnt/user/appdata/wechatpad-hermes/data bash scripts/install_local.sh
```

For a User Scripts style long-running bridge, copy `references/unraid-user-script.example.sh`, replace the `<release>` segment with the active release directory, and keep secrets in `/mnt/user/appdata/wechatpad-hermes/data/.env`. The script supports `start`, `stop`, `restart`, `status`, `doctor`, `health`, `logs`, and `once` without embedding authcodes or server credentials. `health` prints a redacted operational snapshot from SQLite counters and recent reply/ignored-message statuses.

## Environment

Put real secrets only in the host environment file, never in `SKILL.md`, README, or MCP config examples.

For MCP runtimes that do not inherit the service environment, set only this pointer in the MCP config and keep secrets in the env file:

```bash
WECHATPAD_ENV_FILE=/opt/hermes/data/.env
```

Required variables:

```bash
WECHATPAD_BASE_URL=http://127.0.0.1:8062/api
WECHATPAD_AUTHCODE=***
WECHATPAD_DB_PATH=/opt/hermes/data/wechatpad-hermes/wechatpad-hermes.sqlite3
WECHATPAD_POLICY_PATH=/opt/hermes/data/wechatpad-hermes/policy.yaml
WECHATPAD_BOT_WXID=<bot-wxid>
WECHATPAD_BOT_NAMES=BOT
WECHATPAD_OWNER_WXIDS=<owner-private-wxid-list>
WECHATPAD_CONTEXT_TOKEN_TTL_SECONDS=***
WECHATPAD_BLOCKED_WXIDS=
WECHATPAD_BLOCKED_GROUP_CHATROOMS=
```

Optional owner-admin variables:

```bash
WECHATPAD_ADMIN_KEY=
```

Set `WECHATPAD_ADMIN_KEY` only in the real server env file when owner-admin listing is needed. Hermes/MCP callers never pass this key. The MCP status/admin tools accept only an owner private `owner_chat_handle` plus its fresh `context_token`; `wechat_get_all_online` then uses the server-side key internally for the WeChatPad API request.

Safe defaults:

```bash
WECHATPAD_SEND_ENABLED=false
WECHATPAD_DRY_RUN=true
WECHATPAD_ADMIN_TOOLS_ENABLED=false
WECHATPAD_ALLOW_UNKNOWN_OUTBOUND=false
WECHATPAD_STORE_RAW_MESSAGES=false
```

Keep `WECHATPAD_STORE_RAW_MESSAGES=false` for public use. The bridge stores redacted searchable message text and handles by default; raw WeChatPad payload storage should be enabled only for short debugging windows.

`POST /Msg/StartAutoSync` exists in the WeChatPadProMAX OpenAPI and takes a `TargetURL`, but this package defaults to active `/Msg/Sync` polling so no public callback URL is required.

## Dry-run Verification

```bash
set -a
. /mnt/user/appdata/wechatpad-hermes/data/.env
set +a
export PYTHONPATH=/mnt/user/appdata/wechatpad-hermes/releases/<release>/hermes-wechatpadpromax/src
cd /mnt/user/appdata/wechatpad-hermes/releases/<release>/hermes-wechatpadpromax
python scripts/wechatpad_status.py
python -m wechatpad_hermes.doctor --strict --require-public-safe
python scripts/smoke_test.py
python scripts/mcp_stdio_smoke.py
python scripts/ops_status.py
python scripts/render_mcp_config.py --release <release>
python -m wechatpad_hermes.bridge --once
```

Expected behavior:

- BOT online/cache status can be read only from owner private context, with redacted output.
- New messages are stored in SQLite.
- The `/Msg/Sync` cursor is persisted in SQLite runtime state so bridge restarts can resume from the latest known sync key.
- Blocked, non-text, disallowed, and BOT-self messages are not stored as context; only a low-information ignored-message fingerprint is kept for dedupe/noise control.
- Private chats route to isolated conversations; Hermes receives opaque conversation handles rather than raw wxids.
- Group messages are stored but only @BOT triggers replies.
- Group @BOT context includes same-chat recent messages and, when sender extraction succeeds, same-group same-sender recent messages.
- MCP `wechat_send_text` reports dry-run or blocks raw, unknown, or stale outbound targets.
- The stdio MCP server initializes, lists the expected tools, and can call `wechat_bridge_health` without exposing secrets.
- MCP status/admin tools require owner private context; `wechat_get_all_online` also stays disabled by default and never accepts any key provided by Hermes.

## Enable Real Sending

Only after dry-run looks correct:

```bash
WECHATPAD_SEND_ENABLED=true
WECHATPAD_DRY_RUN=false
```

Keep these disabled until Hermes model endpoint behavior and privacy blocking have been verified with real incoming messages.

## MCP Config

Use `references/mcp-config.example.json` as a starting point. Prefer pointing `PYTHONPATH` at the package `src` path or using the venv console script after `pip install -e .`.

For the Unraid appdata layout, use `references/mcp-config.unraid.example.json` and adjust the `<release>` segment to the active release directory.

To avoid hand-editing path placeholders, render the Unraid config from the installed release directory:

```bash
python scripts/render_mcp_config.py --release dryrun-YYYYMMDDHHMMSS
```

The generated MCP JSON contains only paths and safe dry-run switches. It points at `WECHATPAD_ENV_FILE` and must not inline authcodes, admin keys, Hermes API keys, passwords, raw wxids, or chatroom IDs.

## Current Known Integration Gap

The generic `HermesClient` supports either `HERMES_WEBHOOK_URL` or an OpenAI-compatible `HERMES_CHAT_COMPLETIONS_URL`. The exact Hermes main-model endpoint still needs confirmation from the live Hermes configuration before production enablement.