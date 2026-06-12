# WeChatPad-Hermes

一个安全的 `WeChatPadProMAX -> Hermes` 公用微信机器人中间层。

这个项目的目标是让 `BOT` 通过 WeChatPadProMAX 接入 Hermes 主模型，同时默认保持公开场景安全：私聊进入独立会话，群聊只有在明确 `@BOT` 时才回应，最近上下文可检索，但授权码、服务器凭据、原始 wxid/chatroom、工具 token 和跨会话内容都不能泄露。

## 当前能力

- 常驻 bridge 轮询 `/Msg/Sync`，持久化同步 key，重启后可继续拉取；连续轮询失败时会退避重试；消息会去重并写入 SQLite 最近历史。被拒绝的消息只保存低信息量指纹，避免阻塞内容被反复记录。
- 已按本地 OpenAPI 审计支持 `/Msg/Sync` 游标字段 `CurrentSynckey.buffer` 和 `MaxSynckey.buffer`。`/Msg/StartAutoSync` 已记录但默认不启用，因为它需要可被 WeChatPadProMAX 回调访问的 URL。
- 私聊会路由到隔离的 Hermes 会话。
- 群聊默认只静默入库；只有明确提到 `BOT` 时才触发回复。触发后 bridge 会提供当前群的近期上下文；如果能解析出发言人，还会补充该发言人在同群的近期消息。
- 入模上下文、私聊/群聊出模回复、日志、原始 payload 存储、忽略消息记录和 MCP 输出都会做脱敏或最小化处理，覆盖授权码、密码、token、原始 wxid/chatroom/openim ID、服务器地址和内部工具凭据。默认不存储 WeChatPad 原始 payload。
- MCP 工具提供安全的 bridge 健康状态、owner-only 的 BOT 在线/cache 查询、同会话上下文查询/搜索、受控发送，以及 owner-only 的在线账号列表。
- 默认禁用未知目标发送、原始 wxid/chatroom 输入、过期 handle、管理工具和真实发送。

## 目录结构

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

## 上下文模型

WeChatPadProMAX 的群消息可能通过不同字段暴露真实发言人。解析器会先尝试 `ActualUserName`、`SenderWxid`、`SenderUserName` 等显式字段，再尝试常见的 `wxid_xxx:\nmessage` 内容前缀。

群里 `@BOT` 触发回复时，Hermes 会收到：

- `same_chat_recent`：当前群的近期消息。
- `same_group_same_sender_recent`：触发者在当前群的近期消息，前提是能解析出发言人。

上下文不会跨群或跨私聊混用。Hermes/MCP 的安全视图只暴露不透明的 `chat_handle`、`participant_handle` 和短期 `context_token`，不会暴露原始 wxid、chatroom 或 openim ID。这些值属于内部工具凭据，不能展示给微信用户。

SQLite 是运行时私有文件。它会存储已脱敏、可搜索的文本用于上下文，但也必须在本地保存原始聊天路由标识，这样才能通过 WeChatPadProMAX 把回复发回去。数据库应放在宿主机 appdata/data 目录，不要挂载给公开工具，也不要复制进 skill 文档或 MCP 示例。

## 安装

```bash
cd /opt/hermes/data/skills/lvwan/wechatpad-hermes
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

## 配置

真实运行值应放在 Hermes 宿主机的环境文件里，通常是 `/opt/hermes/data/.env`。不要把授权码、API key、后台 key、密码、wxid 或 chatroom 写进 `SKILL.md`、README、MCP JSON、日志或聊天回复。

如果 MCP runtime 不继承服务环境，只在 MCP 配置里放这个指针：

```bash
WECHATPAD_ENV_FILE=/opt/hermes/data/.env
```

安全默认值：

- `WECHATPAD_SEND_ENABLED=false`：不执行真实微信发送。
- `WECHATPAD_DRY_RUN=true`：发送调用只返回脱敏后的模拟结果。
- `WECHATPAD_ADMIN_TOOLS_ENABLED=false`：owner/admin 列表类工具默认关闭。
- `WECHATPAD_ALLOW_UNKNOWN_OUTBOUND=false`：发送必须使用已知且未过期的 handle/token。
- `WECHATPAD_BLOCKED_WXIDS` 和 `WECHATPAD_BLOCKED_GROUP_CHATROOMS`：可阻止指定私聊发送者、群内发送者或群聊。

owner-only 状态/管理操作需要配置 `WECHATPAD_OWNER_WXIDS`，并且 `WECHATPAD_ADMIN_KEY` 只能保存在服务端环境里。Hermes 调用 `wechat_get_online_info`、`wechat_get_cache_info` 和 `wechat_get_all_online` 时，只能使用 owner 私聊里的 `owner_chat_handle` 和 `context_token`，不能在聊天里传递或索要 admin key/authcode。

## 验证

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

复制 release 包到公开或共享宿主机之前，先扫描 archive 和 installer：

```bash
python scripts/scan_release.py /path/to/hermes-wechatpadpromax-YYYYMMDDHHMMSS.tar.gz \
  --installer /path/to/install-wechatpad-hermes-YYYYMMDDHHMMSS.sh
```

首次部署必须保持 dry-run：

```bash
WECHATPAD_SEND_ENABLED=false
WECHATPAD_DRY_RUN=true
```

只有在真实入站消息路由、隐私拦截和 Hermes 主模型 endpoint 都验证通过后，才考虑开启真实发送。

部署清单见 `DEPLOYMENT.md`。通用 `HermesClient` 支持 `HERMES_WEBHOOK_URL`，也支持 OpenAI-compatible 的 `HERMES_CHAT_COMPLETIONS_URL`；生产环境的 Hermes 主模型 endpoint 仍需在开启真实发送前确认。

端点形状的本地脱敏审计记录见 `references/wechatpad-openapi-audit.md`。
