from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import stat

from .config import Settings, load_settings, load_yaml
from .privacy import PrivacyFilter, mask_secret
from .storage import MessageStore
from .wechatpad_client import WeChatPadClient


@dataclass(frozen=True)
class CheckIssue:
    level: str
    code: str
    message: str


def check_settings(settings: Settings, *, strict: bool = False, require_public_safe: bool = False) -> list[CheckIssue]:
    issues: list[CheckIssue] = []

    if not settings.wechatpad_base_url.startswith(("http://", "https://")):
        issues.append(CheckIssue("error", "invalid_wechatpad_base_url", "WECHATPAD_BASE_URL must start with http:// or https://"))
    if strict and not settings.wechatpad_authcode:
        issues.append(CheckIssue("error", "missing_wechatpad_authcode", "WECHATPAD_AUTHCODE is required for runtime checks"))
    if strict and not settings.bot_wxid:
        issues.append(CheckIssue("error", "missing_bot_wxid", "WECHATPAD_BOT_WXID is required to ignore BOT self messages and validate group mention metadata"))
    if settings.admin_tools_enabled and not settings.wechatpad_admin_key:
        issues.append(CheckIssue("error", "admin_tools_without_admin_key", "Admin MCP tools require WECHATPAD_ADMIN_KEY"))
    if settings.admin_tools_enabled and not _configured_owner_wxids(settings):
        issues.append(CheckIssue("error", "admin_tools_without_owner_wxids", "Admin MCP tools require WECHATPAD_OWNER_WXIDS for owner private context checks"))
    if settings.send_enabled and settings.dry_run:
        issues.append(CheckIssue("warning", "send_enabled_but_dry_run", "WECHATPAD_DRY_RUN=true prevents real sending even when send is enabled"))
    if settings.send_enabled and settings.allow_unknown_outbound:
        issues.append(CheckIssue("warning", "unknown_outbound_enabled", "Unknown outbound targets are allowed; keep this off for public Hermes use"))
    if settings.store_raw_messages:
        issues.append(CheckIssue("warning", "raw_message_storage_enabled", "Raw WeChatPad payload storage is enabled; keep it off for public Hermes use unless debugging"))
    if require_public_safe:
        if settings.send_enabled:
            issues.append(CheckIssue("error", "public_safe_send_enabled", "Dry-run public-safe mode requires WECHATPAD_SEND_ENABLED=false"))
        if not settings.dry_run:
            issues.append(CheckIssue("error", "public_safe_dry_run_disabled", "Dry-run public-safe mode requires WECHATPAD_DRY_RUN=true"))
        if settings.admin_tools_enabled:
            issues.append(CheckIssue("error", "public_safe_admin_tools_enabled", "Dry-run public-safe mode requires WECHATPAD_ADMIN_TOOLS_ENABLED=false"))
        if settings.allow_unknown_outbound:
            issues.append(CheckIssue("error", "public_safe_unknown_outbound_enabled", "Dry-run public-safe mode requires WECHATPAD_ALLOW_UNKNOWN_OUTBOUND=false"))
        if settings.store_raw_messages:
            issues.append(CheckIssue("error", "public_safe_raw_storage_enabled", "Dry-run public-safe mode requires WECHATPAD_STORE_RAW_MESSAGES=false"))
    if settings.allow_all_groups and not settings.allowed_group_chatrooms:
        issues.append(CheckIssue("warning", "all_groups_allowed", "All groups are allowed; consider a chatroom allowlist for public deployment"))
    if settings.allow_all_private and not settings.allowed_private_wxids:
        issues.append(CheckIssue("warning", "all_private_allowed", "All private chats are allowed; consider a private allowlist if BOT is public"))
    if settings.history_days <= 0:
        issues.append(CheckIssue("error", "invalid_history_days", "WECHATPAD_HISTORY_DAYS must be positive"))
    if settings.retention_days <= 0:
        issues.append(CheckIssue("error", "invalid_retention_days", "WECHATPAD_RETENTION_DAYS must be positive"))
    if settings.context_token_ttl_seconds < 60:
        issues.append(CheckIssue("error", "invalid_context_token_ttl", "WECHATPAD_CONTEXT_TOKEN_TTL_SECONDS must be at least 60"))
    if settings.max_context_messages <= 0:
        issues.append(CheckIssue("error", "invalid_max_context_messages", "WECHATPAD_MAX_CONTEXT_MESSAGES must be positive"))
    if settings.max_context_chars <= 0:
        issues.append(CheckIssue("error", "invalid_max_context_chars", "WECHATPAD_MAX_CONTEXT_CHARS must be positive"))
    if settings.policy_path and not settings.policy_path.exists():
        issues.append(CheckIssue("error", "policy_file_missing", "WECHATPAD_POLICY_PATH points to a missing file"))
    if settings.env_file_path and not settings.env_file_path.exists():
        issues.append(CheckIssue("error", "env_file_missing", "WECHATPAD_ENV_FILE points to a missing file"))
    if not settings.hermes_webhook_url and not settings.hermes_chat_completions_url:
        issues.append(CheckIssue("warning", "missing_hermes_endpoint", "Configure HERMES_WEBHOOK_URL or HERMES_CHAT_COMPLETIONS_URL before live replies"))

    return issues


def _configured_owner_wxids(settings: Settings) -> set[str]:
    owner_wxids = set(settings.owner_wxids)
    if settings.policy_path and settings.policy_path.exists():
        policy = load_yaml(settings.policy_path)
        raw_owner_wxids = policy.get("owner_wxids")
        if isinstance(raw_owner_wxids, list):
            owner_wxids.update(str(item).strip() for item in raw_owner_wxids if str(item).strip())
        elif isinstance(raw_owner_wxids, str):
            owner_wxids.update(item.strip() for item in raw_owner_wxids.split(",") if item.strip())
    return owner_wxids


def summarize_settings(settings: Settings) -> dict[str, Any]:
    return {
        "wechatpad_base_url": settings.wechatpad_base_url,
        "wechatpad_authcode": mask_secret(settings.wechatpad_authcode),
        "wechatpad_admin_key_configured": bool(settings.wechatpad_admin_key),
        "bot_wxid_configured": bool(settings.bot_wxid),
        "bot_names": settings.bot_names,
        "hermes_webhook_configured": bool(settings.hermes_webhook_url),
        "hermes_chat_completions_url": settings.hermes_chat_completions_url,
        "hermes_api_key_configured": bool(settings.hermes_api_key),
        "db_path": str(settings.db_path),
        "policy_path": str(settings.policy_path or ""),
        "env_file_path": str(settings.env_file_path or ""),
        "poll_interval": settings.poll_interval,
        "history_days": settings.history_days,
        "retention_days": settings.retention_days,
        "context_token_ttl_seconds": settings.context_token_ttl_seconds,
        "max_context_messages": settings.max_context_messages,
        "max_context_chars": settings.max_context_chars,
        "send_enabled": settings.send_enabled,
        "dry_run": settings.dry_run,
        "admin_tools_enabled": settings.admin_tools_enabled,
        "allow_unknown_outbound": settings.allow_unknown_outbound,
        "store_raw_messages": settings.store_raw_messages,
        "allow_all_private": settings.allow_all_private,
        "allow_all_groups": settings.allow_all_groups,
        "allowed_private_count": len(settings.allowed_private_wxids),
        "allowed_group_count": len(settings.allowed_group_chatrooms),
        "blocked_private_count": len(settings.blocked_wxids),
        "blocked_group_count": len(settings.blocked_group_chatrooms),
        "owner_count": len(settings.owner_wxids),
    }


def inspect_local_state(settings: Settings) -> dict[str, Any]:
    db_path = Path(settings.db_path)
    data: dict[str, Any] = {
        "db_parent_exists": db_path.parent.exists(),
        "db_exists": db_path.exists(),
        "db_private_mode": _is_private_file_mode(db_path),
        "policy_exists": bool(settings.policy_path and settings.policy_path.exists()),
    }
    if settings.policy_path and settings.policy_path.exists():
        policy = load_yaml(settings.policy_path)
        data["policy_sections"] = sorted(str(key) for key in policy.keys())
    if db_path.exists():
        store = MessageStore(
            settings.db_path,
            fingerprint_key=settings.wechatpad_authcode or settings.wechatpad_admin_key,
        )
        try:
            data["db_known_messages"] = store.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            data["db_known_replies"] = store.conn.execute("SELECT COUNT(*) FROM replies").fetchone()[0]
            data["db_known_ignored_messages"] = store.conn.execute("SELECT COUNT(*) FROM ignored_messages").fetchone()[0]
        finally:
            store.close()
    return data


def _is_private_file_mode(path: Path) -> bool | None:
    if not path.exists():
        return None
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None
    return (mode & 0o077) == 0


def run_live_checks(settings: Settings) -> dict[str, Any]:
    client = WeChatPadClient(settings)
    results: dict[str, Any] = {}
    for name, func in (
        ("online_info", client.get_online_info),
        ("cache_info", client.get_cache_info),
    ):
        try:
            results[name] = func()
        except Exception as exc:
            results[name] = {"error": type(exc).__name__, "message": str(exc)}
    return results


def build_report(settings: Settings, *, strict: bool = False, live: bool = False, require_public_safe: bool = False) -> dict[str, Any]:
    report: dict[str, Any] = {
        "settings": summarize_settings(settings),
        "issues": [asdict(issue) for issue in check_settings(settings, strict=strict or live, require_public_safe=require_public_safe)],
        "local_state": inspect_local_state(settings),
    }
    if live:
        report["live"] = run_live_checks(settings)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Check WeChatPad-Hermes configuration without leaking secrets")
    parser.add_argument("--strict", action="store_true", help="Treat runtime-required missing values as errors")
    parser.add_argument("--live", action="store_true", help="Call WeChatPad online/cache endpoints")
    parser.add_argument("--require-public-safe", action="store_true", help="Fail unless dry-run public-safe switches are enabled")
    args = parser.parse_args()

    settings = load_settings()
    privacy = PrivacyFilter()
    report = build_report(settings, strict=args.strict, live=args.live, require_public_safe=args.require_public_safe)
    print(privacy.redact(json.dumps(report, ensure_ascii=False, indent=2)).text)

    has_error = any(issue["level"] == "error" for issue in report["issues"])
    raise SystemExit(1 if has_error else 0)


if __name__ == "__main__":
    main()
