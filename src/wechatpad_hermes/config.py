from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _split_csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _load_env_file() -> tuple[Path | None, dict[str, str]]:
    raw_path = os.environ.get("WECHATPAD_ENV_FILE", "").strip()
    if not raw_path:
        return None, {}
    path = Path(raw_path)
    if not path.exists():
        return path, {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return path, values


def _env_value(env_file: dict[str, str], name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is not None and value != "":
        return value
    return env_file.get(name, default)


def _env_bool(env_file: dict[str, str], name: str, default: bool) -> bool:
    value = _env_value(env_file, name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _raw_env_value(env_file: dict[str, str], name: str) -> str:
    value = os.environ.get(name)
    if value is not None:
        return value
    return env_file.get(name, "")


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(env_file: dict[str, str], name: str, default: int) -> int:
    value = _env_value(env_file, name)
    if value is None or value == "":
        return default
    return int(value)


def _env_float(env_file: dict[str, str], name: str, default: float) -> float:
    value = _env_value(env_file, name)
    if value is None or value == "":
        return default
    return float(value)


@dataclass(frozen=True)
class Settings:
    wechatpad_base_url: str = "http://127.0.0.1:8062/api"
    wechatpad_authcode: str = ""
    wechatpad_admin_key: str = ""
    bot_wxid: str = ""
    bot_names: list[str] = field(default_factory=lambda: ["BOT"])

    hermes_webhook_url: str = ""
    hermes_chat_completions_url: str = "http://127.0.0.1:9119/v1/chat/completions"
    hermes_api_key: str = ""
    hermes_model: str = ""

    db_path: Path = Path("wechatpad-hermes.sqlite3")
    policy_path: Path | None = None
    poll_interval: float = 2.0
    history_days: int = 3
    retention_days: int = 14
    context_token_ttl_seconds: int = 1800
    max_context_messages: int = 80
    max_context_chars: int = 12000
    send_enabled: bool = False
    dry_run: bool = True
    admin_tools_enabled: bool = False
    allow_unknown_outbound: bool = False
    store_raw_messages: bool = False

    owner_wxids: list[str] = field(default_factory=list)
    allow_all_private: bool = True
    allow_all_groups: bool = True
    allowed_private_wxids: list[str] = field(default_factory=list)
    allowed_group_chatrooms: list[str] = field(default_factory=list)
    blocked_wxids: list[str] = field(default_factory=list)
    blocked_group_chatrooms: list[str] = field(default_factory=list)
    env_file_path: Path | None = None

    webhook_enabled: bool = False
    webhook_port: int = 8070
    webhook_host: str = "0.0.0.0"
    webhook_secret: str = ""


def load_settings() -> Settings:
    env_path, env_file = _load_env_file()
    policy_path = _env_value(env_file, "WECHATPAD_POLICY_PATH").strip()
    policy = load_yaml(Path(policy_path)) if policy_path else {}
    privacy = policy.get("privacy") if isinstance(policy.get("privacy"), dict) else {}
    policy_store_raw_messages = _as_bool(privacy.get("store_raw_messages"), False)
    store_raw_messages = _as_bool(_raw_env_value(env_file, "WECHATPAD_STORE_RAW_MESSAGES"), policy_store_raw_messages)
    return Settings(
        wechatpad_base_url=_env_value(env_file, "WECHATPAD_BASE_URL", "http://127.0.0.1:8062/api").rstrip("/"),
        wechatpad_authcode=_env_value(env_file, "WECHATPAD_AUTHCODE"),
        wechatpad_admin_key=_env_value(env_file, "WECHATPAD_ADMIN_KEY"),
        bot_wxid=_env_value(env_file, "WECHATPAD_BOT_WXID"),
        bot_names=_split_csv(_env_value(env_file, "WECHATPAD_BOT_NAMES")) or ["BOT"],
        hermes_webhook_url=_env_value(env_file, "HERMES_WEBHOOK_URL"),
        hermes_chat_completions_url=_env_value(env_file, "HERMES_CHAT_COMPLETIONS_URL", "http://127.0.0.1:9119/v1/chat/completions"),
        hermes_api_key=_env_value(env_file, "HERMES_API_KEY"),
        hermes_model=_env_value(env_file, "HERMES_MODEL"),
        db_path=Path(_env_value(env_file, "WECHATPAD_DB_PATH", "wechatpad-hermes.sqlite3")),
        policy_path=Path(policy_path) if policy_path else None,
        poll_interval=_env_float(env_file, "WECHATPAD_POLL_INTERVAL", 2.0),
        history_days=_env_int(env_file, "WECHATPAD_HISTORY_DAYS", 3),
        retention_days=_env_int(env_file, "WECHATPAD_RETENTION_DAYS", 14),
        context_token_ttl_seconds=_env_int(env_file, "WECHATPAD_CONTEXT_TOKEN_TTL_SECONDS", 1800),
        max_context_messages=_env_int(env_file, "WECHATPAD_MAX_CONTEXT_MESSAGES", 80),
        max_context_chars=_env_int(env_file, "WECHATPAD_MAX_CONTEXT_CHARS", 12000),
        send_enabled=_env_bool(env_file, "WECHATPAD_SEND_ENABLED", False),
        dry_run=_env_bool(env_file, "WECHATPAD_DRY_RUN", True),
        admin_tools_enabled=_env_bool(env_file, "WECHATPAD_ADMIN_TOOLS_ENABLED", False),
        allow_unknown_outbound=_env_bool(env_file, "WECHATPAD_ALLOW_UNKNOWN_OUTBOUND", False),
        store_raw_messages=store_raw_messages,
        owner_wxids=_split_csv(_env_value(env_file, "WECHATPAD_OWNER_WXIDS")),
        allow_all_private=_env_bool(env_file, "WECHATPAD_ALLOW_ALL_PRIVATE", True),
        allow_all_groups=_env_bool(env_file, "WECHATPAD_ALLOW_ALL_GROUPS", True),
        allowed_private_wxids=_split_csv(_env_value(env_file, "WECHATPAD_ALLOWED_PRIVATE_WXIDS")),
        allowed_group_chatrooms=_split_csv(_env_value(env_file, "WECHATPAD_ALLOWED_GROUP_CHATROOMS")),
        blocked_wxids=_split_csv(_env_value(env_file, "WECHATPAD_BLOCKED_WXIDS")),
        blocked_group_chatrooms=_split_csv(_env_value(env_file, "WECHATPAD_BLOCKED_GROUP_CHATROOMS")),
        env_file_path=env_path,
        webhook_enabled=_env_bool(env_file, "WECHATPAD_WEBHOOK_ENABLED", False),
        webhook_port=_env_int(env_file, "WECHATPAD_WEBHOOK_PORT", 8070),
        webhook_host=_env_value(env_file, "WECHATPAD_WEBHOOK_HOST", "0.0.0.0"),
        webhook_secret=_env_value(env_file, "WECHATPAD_WEBHOOK_SECRET", ""),
    )


def load_yaml(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    try:
        import yaml
    except Exception as exc:  # pragma: no cover - dependency error path
        raise RuntimeError("PyYAML is required to load policy files") from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}