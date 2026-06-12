from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from wechatpad_hermes.config import load_settings
from wechatpad_hermes.doctor import build_report
from wechatpad_hermes.privacy import PrivacyFilter
from wechatpad_hermes.storage import MessageStore


def _age_seconds(ts: int) -> int | None:
    if not ts:
        return None
    return max(int(time.time()) - int(ts), 0)


def _recent_replies(store: MessageStore, limit: int) -> list[dict[str, Any]]:
    rows = store.conn.execute(
        """
        SELECT is_group, status, reason, created_at
        FROM replies
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (max(int(limit or 1), 1),),
    ).fetchall()
    return [
        {
            "is_group": bool(row["is_group"]),
            "status": row["status"],
            "reason": row["reason"],
            "created_at": int(row["created_at"] or 0),
            "age_seconds": _age_seconds(int(row["created_at"] or 0)),
        }
        for row in rows
    ]


def _recent_ignored(store: MessageStore, limit: int) -> list[dict[str, Any]]:
    rows = store.conn.execute(
        """
        SELECT reason, is_group, msg_type, last_seen_at, seen_count
        FROM ignored_messages
        ORDER BY last_seen_at DESC
        LIMIT ?
        """,
        (max(int(limit or 1), 1),),
    ).fetchall()
    return [
        {
            "reason": row["reason"],
            "is_group": bool(row["is_group"]),
            "msg_type": int(row["msg_type"] or 0),
            "last_seen_at": int(row["last_seen_at"] or 0),
            "age_seconds": _age_seconds(int(row["last_seen_at"] or 0)),
            "seen_count": int(row["seen_count"] or 0),
        }
        for row in rows
    ]


def build_ops_status(limit: int = 10) -> dict[str, Any]:
    settings = load_settings()
    privacy = PrivacyFilter()
    report = build_report(settings, strict=False, live=False, require_public_safe=True)
    data: dict[str, Any] = {
        "generated_at": int(time.time()),
        "safe_switches": {
            "send_enabled": settings.send_enabled,
            "dry_run": settings.dry_run,
            "admin_tools_enabled": settings.admin_tools_enabled,
            "allow_unknown_outbound": settings.allow_unknown_outbound,
            "store_raw_messages": settings.store_raw_messages,
        },
        "config": {
            "bot_wxid_configured": bool(settings.bot_wxid),
            "bot_names_count": len(settings.bot_names),
            "owner_count": len(settings.owner_wxids),
            "allow_all_private": settings.allow_all_private,
            "allow_all_groups": settings.allow_all_groups,
            "allowed_private_count": len(settings.allowed_private_wxids),
            "allowed_group_count": len(settings.allowed_group_chatrooms),
            "blocked_private_count": len(settings.blocked_wxids),
            "blocked_group_count": len(settings.blocked_group_chatrooms),
            "history_days": settings.history_days,
            "retention_days": settings.retention_days,
        },
        "issues": report["issues"],
        "local_state": report["local_state"],
        "db_path": str(settings.db_path),
    }
    db_path = Path(settings.db_path)
    if not db_path.exists():
        data["runtime_stats"] = {"db_exists": False}
        return data

    store = MessageStore(settings.db_path, privacy=privacy, fingerprint_key=settings.wechatpad_authcode or settings.wechatpad_admin_key)
    try:
        stats = store.runtime_stats()
        stats["latest_message_age_seconds"] = _age_seconds(int(stats.get("latest_message_received_at") or 0))
        stats["latest_reply_age_seconds"] = _age_seconds(int(stats.get("latest_reply_created_at") or 0))
        data["runtime_stats"] = stats
        data["recent_replies"] = _recent_replies(store, limit)
        data["recent_ignored"] = _recent_ignored(store, limit)
    finally:
        store.close()
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="Print a redacted operational status snapshot for WeChatPad-Hermes")
    parser.add_argument("--limit", type=int, default=10, help="Recent reply/ignored rows to summarize")
    args = parser.parse_args()
    privacy = PrivacyFilter()
    print(privacy.redact(json.dumps(build_ops_status(args.limit), ensure_ascii=False, indent=2)).text)


if __name__ == "__main__":
    main()
