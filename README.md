# WeChatPad-Hermes

将 Hermes AI Agent 接入微信的桥接层 —— 通过 WeChatPadProMAX 协议实现微信个人号机器人。

让 Hermes 处理微信私聊、群聊 @ 提及，支持文字/图片发送、链接分析、隐私脱敏，兼具轮询和 Webhook 双通道消息接收。

## 核心特性

| 特性 | 说明 |
|---|---|
| **双通道消息接收** | Webhook 推送（低延迟）+ Poll 轮询（兜底），自动去重 |
| **上下文隔离** | 私聊一对一会话，群聊按群隔离，不跨会话泄露 |
| **隐私脱敏** | 授权码、wxid、chatroom ID、token 等敏感信息自动过滤 |
| **图片发送** | 自动提取 `MEDIA:` 标记 / 图片 URL，base64 上传发送 |
| **链接分析** | 群聊中自动识别链接并请求 Hermes 解析内容摘要 |
| **@ 触发回复** | 群聊只在明确 @BOT 时才回复，其余静默入库 |
| **Owner-only 管理** | 在线状态、缓存查询、账号列表等工具仅限主人私聊 |
| **Dry-run 安全** | 默认模式：所有发送「模拟成功」但不真实发出 |
| **断线重连** | 轮询 Synckey 持久化，重启后消息不丢 |
| **链接摘要模板** | B站、抖音、GitHub、微博等平台链接自动生成格式化摘要 |

## 架构概览

```
┌──────────────┐    Webhook (POST)     ┌──────────────────┐
│              │ ◄──────────────────── │                  │
│              │    Poll (/Msg/Sync)   │   WeChatPad-     │
│ WeChatPad    │ ◄──────────────────── │   Hermes Bridge  │
│ ProMAX       │                       │   (Python)       │
│ (Go binary)  │ ────────────────────► │                  │
│              │   /Msg/SendTxt        │   ┌──────────┐  │
│              │   /Msg/UploadImg      │   │ policy   │  │
└──────────────┘                       │   │ privacy  │  │
                                       │   │ media    │  │
                                       │   └──────────┘  │
                                       │        │         │
                                       └────────┼─────────┘
                                                │
                                        Hermes API (JSON)
                                                │
                                                ▼
                                        ┌──────────────────┐
                                        │   Hermes Agent   │
                                        │  (AI + MCP +     │
                                        │   Skills)        │
                                        └──────────────────┘
```

### 双通道消息接收

1. **Webhook 模式**（推荐）—— `WECHATPAD_WEBHOOK_ENABLED=true`
   - WeChatPadProMAX 推送到 bridge 的 Webhook 服务（端口 `8070`）
   - 可选签名验证（`WECHATPAD_WEBHOOK_SECRET`）
   - 低延迟，接近实时

2. **Poll 轮询模式**（默认，始终作为兜底）
   - bridge 定时调用 `/Msg/Sync` 拉取新消息
   - `Synckey` 游标持久化到 SQLite，重启后继续
   - 连续失败自动退避重试

## 目录结构

```
wechatpad-hermes/
├── SKILL.md                           # Hermes Agent Skill（AI 行为规则）
├── README.md                          # 本文件
├── DEPLOYMENT.md                      # 部署清单
├── pyproject.toml                     # Python 项目配置
├── requirements.txt                   # 依赖
├── .env.example                       # 环境变量模板
├── policy.example.yaml                # 策略配置模板
│
├── src/wechatpad_hermes/
│   ├── __init__.py
│   ├── bridge.py                      # 主桥接循环（Webhook + Poll）
│   ├── config.py                      # 配置加载（环境变量）
│   ├── hermes_client.py               # Hermes API 调用 + 链接摘要模板
│   ├── webhook_server.py              # Webhook 推送接收 HTTP 服务
│   ├── wechatpad_client.py            # WeChatPad API 客户端（send_text, send_image 等）
│   ├── mcp_server.py                  # MCP 工具定义（8 个工具）
│   ├── media.py                       # 图片提取/下载/发送
│   ├── messages.py                    # 消息模型与路由
│   ├── policy.py                      # 路由策略（@触发、链接分析、@BOT 检测）
│   ├── privacy.py                     # 隐私脱敏过滤
│   ├── storage.py                     # SQLite 存储（消息历史、Synckey）
│   └── doctor.py                      # 部署自检工具
│
├── scripts/
│   ├── install_local.sh               # 本地安装脚本
│   ├── wechatpad_status.py            # 运行状态检查
│   ├── smoke_test.py                  # 集成冒烟测试
│   ├── mcp_stdio_smoke.py             # MCP stdio 模式冒烟测试
│   ├── ops_status.py                  # 运维状态检查
│   ├── render_mcp_config.py           # MCP 配置生成
│   ├── package_release.py             # 发布包打包
│   └── scan_release.py                # 发布包安全检查
│
├── scripts-deploy/
│   ├── start.sh                       # 带 PID 管理的启动脚本
│   ├── bridge_check.py                # Bridge 状态巡检（看门狗用）
│   ├── login_check.py                 # 微信登录状态检测
│   └── .env.template                  # 部署环境模板
│
└── references/
    ├── mcp-config.example.json         # MCP 配置示例（通用）
    ├── mcp-config.unraid.example.json  # MCP 配置示例（Unraid）
    ├── systemd.service.example         # systemd 服务单元
    ├── unraid-user-script.example.sh   # Unraid User Scripts 示例
    ├── install-unraid-drop.example.sh  # Unraid 部署脚本
    ├── verify-unraid-dryrun.example.sh # Unraid dry-run 验证
    ├── authorize-unraid-ssh-key.example.sh
    ├── UNRAID_NEXT_STEPS.md
    └── wechatpad-openapi-audit.md      # WeChatPad API 审计
```

## 快速开始

### 前提

- 运行中的 WeChatPadProMAX（Go 二进制）
- Python 3.10+
- Hermes Agent（或兼容 OpenAI API 的 LLM endpoint）

### 安装

```bash
git clone https://github.com/09Aaaa/wechatpad-hermes.git
cd wechatpad-hermes

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

### 配置

复制 `.env.example` 到 `.env`，填写必要变量：

```bash
# WeChatPad 连接
WECHATPAD_BASE_URL=http://192.168.1.100:8062
WECHATPAD_AUTHCODE=your-authcode
WECHATPAD_BOT_WXID=wxid_xxxxxxxxxxxx

# Hermes 连接（至少配一个）
HERMES_WEBHOOK_URL=http://your-hermes:8642/webhook
# 或
HERMES_CHAT_COMPLETIONS_URL=http://127.0.0.1:9119/v1/chat/completions

# 安全
WECHATPAD_OWNER_WXIDS=wxid_yyyyyyyyyyyy
WECHATPAD_SEND_ENABLED=false    # 先保持 dry-run
WECHATPAD_DRY_RUN=true
```

### 运行

```bash
# 启动 bridge（Webhook + Poll 双模式）
python -m wechatpad_hermes.bridge

# 或仅启动 MCP 服务器（供 Hermes 直接调用）
python -m wechatpad_hermes.mcp_server
```

## 配置参考

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `WECHATPAD_BASE_URL` | — | WeChatPadProMAX API 地址 |
| `WECHATPAD_AUTHCODE` | — | WeChatPad 授权码 |
| `WECHATPAD_BOT_WXID` | — | 机器人微信 ID |
| `WECHATPAD_BOT_NAMES` | `BOT` | @ 触发的名称列表（逗号分隔） |
| `WECHATPAD_OWNER_WXIDS` | — | 主人 wxid 列表（owner-only 工具用） |
| `WECHATPAD_SEND_ENABLED` | `false` | 是否真实发送消息 |
| `WECHATPAD_DRY_RUN` | `true` | dry-run 模式（只模拟不发送） |
| `WECHATPAD_DB_PATH` | `./data/bridge.db` | SQLite 数据库路径 |
| `WECHATPAD_CONTEXT_TOKEN_TTL_SECONDS` | `600` | 上下文 token 有效期 |
| `WECHATPAD_STORE_RAW_MESSAGES` | `false` | 是否存储原始 payload |
| `WECHATPAD_BLOCKED_WXIDS` | — | 屏蔽的发送者 wxid |
| `WECHATPAD_BLOCKED_GROUP_CHATROOMS` | — | 屏蔽的群 chatroom ID |
| `WECHATPAD_WEBHOOK_ENABLED` | `false` | 启用 Webhook 接收模式 |
| `WECHATPAD_WEBHOOK_PORT` | `8070` | Webhook 服务端口 |
| `WECHATPAD_WEBHOOK_HOST` | `0.0.0.0` | Webhook 监听地址 |
| `WECHATPAD_WEBHOOK_SECRET` | — | Webhook 签名密钥 |
| `WECHATPAD_ADMIN_TOOLS_ENABLED` | `false` | 管理类 MCP 工具开关 |
| `HERMES_WEBHOOK_URL` | — | Hermes Webhook 回调地址 |
| `HERMES_CHAT_COMPLETIONS_URL` | `http://127.0.0.1:9119/v1/chat/completions` | Hermes LLM API 地址 |

## 功能详解

### 图片发送

回复中如果包含 `MEDIA:/path/to/image.jpg` 或图片 URL，bridge 会自动提取、下载、并通过 `/Msg/UploadImg` 发送：

```
这个设计图给你 MEDIA:/tmp/mockup.png
```

限制：
- 最多 3 张图片/回复
- 单图 ≤ 12 MiB
- 格式 jpg/png/gif

MCP 工具 `wechat_send_image` 也可直接触发图片发送。

### 链接分析

群聊出现 URL 时，bridge 自动将链接转发给 Hermes 分析并生成摘要。内置模板支持：

- **B站** — 标题、播放量、UP 主、弹幕数
- **抖音** — 作者、简介、封面
- **GitHub** — 仓库名、Star、描述
- **微博** — 博主、内容摘要
- **AcFun** — 标题、UP 主、播放量

同一链接在 5 分钟内不会重复分析。

### 上下文模型

- 私聊：隔离会话，各自独立上下文
- 群聊：按群隔离，@BOT 时提供该群近期消息
- 跨会话泄露禁止：权限、凭据、其他群/私聊内容不可见

### 安全默认值

所有安全性从「拒绝」开始：

| 开关 | 默认 | 含义 |
|---|---|---|
| `SEND_ENABLED` | `false` | 不真实发送任何消息 |
| `DRY_RUN` | `true` | 模拟发送，记录日志 |
| `ADMIN_TOOLS_ENABLED` | `false` | 管理工具关闭 |
| `STORE_RAW_MESSAGES` | `false` | 不存原始 payload |
| `ALLOW_UNKNOWN_OUTBOUND` | `false` | 发信必须使用已知 handle |
| `BLOCKED_WXIDS` | 空 | 可配置黑名单 |

## 验证

```bash
# 环境自检
python -m wechatpad_hermes.doctor --strict --require-public-safe

# 状态检查
python scripts/wechatpad_status.py

# 集成冒烟
python scripts/smoke_test.py
python scripts/mcp_stdio_smoke.py

# 运维巡检
python scripts/ops_status.py
```

详细部署清单见 [DEPLOYMENT.md](DEPLOYMENT.md)。

## License

MIT
