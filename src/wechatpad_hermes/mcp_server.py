from __future__ import annotations

import json
import time
from functools import lru_cache
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import Settings, load_settings
from .policy import Policy
from .privacy import PrivacyFilter
from .storage import MessageStore
from .wechatpad_client import WeChatPadClient


mcp = FastMCP("wechatpad-hermes")


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


@lru_cache(maxsize=1)
def _settings() -> Settings:
    return load_settings()


@lru_cache(maxsize=1)
def _privacy() -> PrivacyFilter:
    return PrivacyFilter()


@lru_cache(maxsize=1)
def _policy() -> Policy:
    return Policy(_settings())


@lru_cache(maxsize=1)
def _store() -> MessageStore:
    settings = _settings()
    return MessageStore(
        settings.db_path,
        privacy=_privacy(),
        store_raw_messages=settings.store_raw_messages,
        fingerprint_key=settings.wechatpad_authcode or settings.wechatpad_admin_key,
    )


@lru_cache(maxsize=1)
def _wechat() -> WeChatPadClient:
    return WeChatPadClient(_settings())


def _redacted_json(data: Any) -> str:
    clean, _hits = _privacy().redact_data(data)
    return _privacy().redact(_json(clean)).text


def _resolve_chat(handle: str, context_token: str) -> tuple[dict[str, Any] | None, str | None]:
    chat, error_code = _store().validate_context_token(handle, context_token)
    if error_code:
        return None, _json({"error": error_code})
    return chat, None


def _resolve_owner_private_chat(owner_chat_handle: str, context_token: str) -> tuple[dict[str, Any] | None, str | None]:
    if not owner_chat_handle.strip() or not context_token.strip():
        return None, _json({"error": "owner_context_required"})
    chat, error = _resolve_chat(owner_chat_handle, context_token)
    if error:
        return None, error
    assert chat is not None
    if bool(chat["is_group"]):
        return None, _json({"error": "owner_private_context_required"})
    owner_wxids = set(_policy().owner_wxids)
    if not owner_wxids:
        return None, _json({"error": "owner_wxids_not_configured"})
    if str(chat["chat_id"]) not in owner_wxids:
        return None, _json({"error": "owner_private_context_required"})
    return chat, None


@mcp.tool()
def wechat_bridge_health() -> str:
    """Return safe local bridge health/config counters without calling WeChatPad or exposing raw chat IDs."""
    settings = _settings()
    data = {
        "send_enabled": settings.send_enabled,
        "dry_run": settings.dry_run,
        "admin_tools_enabled": settings.admin_tools_enabled,
        "allow_unknown_outbound": settings.allow_unknown_outbound,
        "store_raw_messages": settings.store_raw_messages,
        "bot_wxid_configured": bool(settings.bot_wxid),
        "bot_names_count": len(settings.bot_names),
        "owner_count": len(_policy().owner_wxids),
        "allow_all_private": _policy().allow_all_private,
        "allow_all_groups": _policy().allow_all_groups,
        "history_days": _policy().history_days,
        "retention_days": settings.retention_days,
        "context_token_ttl_seconds": settings.context_token_ttl_seconds,
        "stats": _store().runtime_stats(),
    }
    return _json(data)


@mcp.tool()
def wechat_get_online_info(owner_chat_handle: str = "", context_token: str = "") -> str:
    """Owner-only check for the configured BOT authcode online status. Output is redacted."""
    _chat, error = _resolve_owner_private_chat(owner_chat_handle, context_token)
    if error:
        return error
    return _redacted_json(_wechat().get_online_info())


@mcp.tool()
def wechat_get_all_online(owner_chat_handle: str = "", context_token: str = "") -> str:
    """Owner-only online account listing. Requires an owner private chat handle plus its short-lived context_token. Output is redacted."""
    if not _settings().admin_tools_enabled:
        return _json({"error": "admin_tools_disabled"})
    if not _settings().wechatpad_admin_key:
        return _json({"error": "admin_key_not_configured"})
    _chat, error = _resolve_owner_private_chat(owner_chat_handle, context_token)
    if error:
        return error
    return _redacted_json(_wechat().get_all_online())


@mcp.tool()
def wechat_get_cache_info(owner_chat_handle: str = "", context_token: str = "") -> str:
    """Owner-only check for WeChatPad cache/login info for the configured BOT authcode. Output is redacted."""
    _chat, error = _resolve_owner_private_chat(owner_chat_handle, context_token)
    if error:
        return error
    return _redacted_json(_wechat().get_cache_info())


@mcp.tool()
def wechat_get_recent_messages(chat_handle: str, context_token: str, days: int = 3, limit: int = 50, sender_handle: str = "") -> str:
    """Read recent redacted messages from one opaque chat_handle using its short-lived context_token."""
    chat, error = _resolve_chat(chat_handle, context_token)
    if error:
        return error
    assert chat is not None
    sender_wxid = ""
    if sender_handle.strip():
        sender_wxid = _store().resolve_participant_handle(sender_handle, chat_id=str(chat["chat_id"]))
        if not sender_wxid:
            return _json({"error": "unknown_participant_handle"})
    safe_days = min(max(int(days or 1), 1), _policy().history_days)
    safe_limit = min(max(int(limit or 1), 1), _policy().max_messages)
    since_ts = int(time.time()) - safe_days * 86400
    rows = _store().recent_messages(
        str(chat["chat_id"]),
        since_ts=since_ts,
        limit=safe_limit,
        max_chars=_policy().max_chars,
        sender_wxid=sender_wxid,
    )
    return _json(rows)


@mcp.tool()
def wechat_search_messages(chat_handle: str, context_token: str, query: str, limit: int = 20) -> str:
    """Search redacted history inside one opaque chat_handle using its short-lived context_token."""
    chat, error = _resolve_chat(chat_handle, context_token)
    if error:
        return error
    assert chat is not None
    if not query.strip():
        return _json({"error": "query must not be empty"})
    safe_limit = min(max(int(limit or 1), 1), 50)
    rows = _store().search_messages(str(chat["chat_id"]), _privacy().redact(query).text, limit=safe_limit)
    return _json(rows)


@mcp.tool()
def wechat_send_text(target_handle: str, context_token: str, content: str) -> str:
    """Send text to an opaque target_handle with its short-lived context_token. Replies pass privacy blocking first."""
    chat, error = _resolve_chat(target_handle, context_token)
    if error:
        try:
            reason = json.loads(error).get("error", "context_token_error")
        except Exception:
            reason = "context_token_error"
        return _json({"sent": False, "blocked": True, "reason": reason})
    assert chat is not None
    to_wxid = str(chat["chat_id"])
    is_group = bool(chat["is_group"])
    if not _settings().allow_unknown_outbound and not _store().has_chat(to_wxid):
        _record_mcp_send_attempt(to_wxid, is_group, content, "blocked", "stale_context_handle")
        return _json({"sent": False, "blocked": True, "reason": "stale_context_handle"})
    if is_group:
        ok, safe_content, hits = _privacy().safe_for_group_reply(content)
        if not ok:
            _record_mcp_send_attempt(to_wxid, is_group, safe_content, "blocked", "sensitive_group_reply:" + ",".join(hits))
            return _json({"sent": False, "blocked": True, "reason": "sensitive_group_reply", "hits": hits})
    else:
        ok, safe_content, hits = _privacy().safe_for_private_reply(content)
        if not ok:
            _record_mcp_send_attempt(to_wxid, is_group, safe_content, "blocked", "sensitive_private_reply:" + ",".join(hits))
            return _json({"sent": False, "blocked": True, "reason": "sensitive_private_reply", "hits": hits})
    send_result = _wechat().send_text(to_wxid, safe_content)
    status = "dry_run" if _settings().dry_run or not _settings().send_enabled else "sent"
    if not send_result.get("Success", send_result.get("Code") == 0):
        status = "send_failed"
    _record_mcp_send_attempt(to_wxid, is_group, safe_content, status, _redacted_json(send_result)[:500])
    return _redacted_json(send_result)


def _record_mcp_send_attempt(chat_id: str, is_group: bool, content: str, status: str, reason: str) -> None:
    trigger_key = f"mcp:{time.time_ns()}"
    _store().add_reply(trigger_key, chat_id, chat_id, is_group, content, status, reason)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
