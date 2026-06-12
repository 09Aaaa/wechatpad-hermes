# WeChatPad-Hermes

Safe WeChatPadProMAX to Hermes public WeChat bot middle layer.

## Current Capabilities

- Long-running bridge polls `/Msg/Sync`, persists the returned sync key across restarts, backs off after repeated polling errors, deduplicates messages, and writes recent history to SQLite. Rejected messages are deduped with a low-information fingerprint so blocked content is not stored or repeatedly logged.
- The OpenAPI-audited `/Msg/Sync` cursor fields `CurrentSynckey.buffer` and `MaxSynckey.buffer` are supported. `/Msg/StartAutoSync` is documented but not enabled by default because it requires a reachable callback URL.
- Private chats route to isolated Hermes conversations.
- Group chats are stored silently unless BOT is explicitly mentioned. When mentioned, the bridge includes current-group recent context and, when sender extraction succeeds, that sender's recent same-group messages.
- Incoming model context, outgoing private/group replies, logs, stored raw payloads, ignored-message records, and MCP outputs are redacted or minimized for authcodes, passwords, tokens, raw wxids/chatrooms/openim IDs, server addresses, and opaque tool credentials. Raw WeChatPad payload storage is disabled by default.
- MCP tools expose safe bridge health counters, owner-only redacted BOT online/cache checks, same-chat context lookup/search, controlled sending, and owner-only online-account listing.
- Unknown outbound targets, raw wxid/chatroom inputs, stale handles, admin tools, and real sending are disabled by default.

## Layout

```text
SKILL.md
README.md
DEPLOYMENT.md
.env.example
policy.example.yaml
pyproject.toml
requirements.txt
references/
scripts/
src/wechatpad_hermes/
```

## Context Model

WeChatPadProMAX group messages can expose the real sender through different fields. The parser tries explicit sender fields such as `ActualUserName`, `SenderWxid`, and `SenderUserName`, then common `wxid_xxx:\nmessage` content prefixes.

When group @BOT triggers a reply, Hermes receives:

- `same_chat_recent`: recent messages from the current group.
- `same_group_same_sender_recent`: recent messages from the triggering sender in the same group, when the sender is known.

Context never crosses groups or private chats. Hermes/MCP safe views expose opaque `chat_handle`, `participant_handle`, and short-lived `context_token` values instead of raw wxids, chatrooms, or openim IDs. Those values are internal tool credentials and must not be shown to WeChat users.

SQLite is a runtime-private file. It stores redacted searchable text for context, but it must also keep raw chat routing identifiers locally so replies can be sent back through WeChatPadProMAX. Keep the database under the host appdata/data directory, do not mount it into public tools, and do not copy it into skill docs or MCP examples.

## Install

```bash
cd /opt/hermes/data/skills/lvwan/wechatpad-hermes
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

## Configuration

Put real runtime values in the Hermes host env file, usually `/opt/hermes/data/.env`. Keep secrets out of `SKILL.md`, README, MCP JSON, logs, and chat replies. For MCP runtimes that do not inherit the service environment, set only this pointer in MCP config:

```bash
WECHATPAD_ENV_FILE=/opt/hermes/data/.env
```

Safe defaults:

- `WECHATPAD_SEND_ENABLED=false`: no real WeChat sends.
- `WECHATPAD_DRY_RUN=true`: send calls return simulated redacted results.
- `WECHATPAD_ADMIN_TOOLS_ENABLED=false`: owner/admin listing tools are off.
- `WECHATPAD_ALLOW_UNKNOWN_OUTBOUND=false`: sends require a known fresh handle/token.
- `WECHATPAD_BLOCKED_WXIDS` and `WECHATPAD_BLOCKED_GROUP_CHATROOMS`: block private senders, group senders, or groups.

For owner-only status/admin operations, configure `WECHATPAD_OWNER_WXIDS` and keep `WECHATPAD_ADMIN_KEY` only in the server environment. Hermes calls `wechat_get_online_info`, `wechat_get_cache_info`, and `wechat_get_all_online` with an owner private `owner_chat_handle` and `context_token`, never with the admin key or authcode.

## Verification

```bash
set -a
. /opt/hermes/data/.env
set +a
python scripts/wechatpad_status.py
python -m wechatpad_hermes.doctor --strict --require-public-safe
python scripts/smoke_test.py
python scripts/mcp_stdio_smoke.py
python scripts/ops_status.py
python scripts/render_mcp_config.py --release dryrun-YYYYMMDDHHMMSS
python -m wechatpad_hermes.bridge --once
python -m wechatpad_hermes.mcp_server
```

Before copying a release package to a public or shared host, scan the archive and installer:

```bash
python scripts/scan_release.py /path/to/hermes-wechatpadpromax-YYYYMMDDHHMMSS.tar.gz \
  --installer /path/to/install-wechatpad-hermes-YYYYMMDDHHMMSS.sh
```

First deployment should stay in dry-run:

```bash
WECHATPAD_SEND_ENABLED=false
WECHATPAD_DRY_RUN=true
```

Enable real sending only after live incoming-message routing, privacy blocking, and the real Hermes endpoint are verified.

See `DEPLOYMENT.md` for the deployment checklist. The generic `HermesClient` supports either `HERMES_WEBHOOK_URL` or an OpenAI-compatible `HERMES_CHAT_COMPLETIONS_URL`; the exact production Hermes main-model endpoint still needs confirmation before live sending.

See `references/wechatpad-openapi-audit.md` for the redacted local OpenAPI/tutorial audit used to verify endpoint shapes.
