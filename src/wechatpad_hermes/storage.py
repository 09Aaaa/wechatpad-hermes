from __future__ import annotations

import json
import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .messages import ChatMessage
from .privacy import PrivacyFilter


class MessageStore:
    def __init__(
        self,
        path: Path | str,
        privacy: PrivacyFilter | None = None,
        *,
        store_raw_messages: bool = False,
        fingerprint_key: str = "",
    ) -> None:
        self.path = Path(path)
        self.privacy = privacy or PrivacyFilter()
        self.store_raw_messages = store_raw_messages
        self.fingerprint_key = str(fingerprint_key or "")
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.init_schema()
        self._harden_db_permissions()

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def init_schema(self) -> None:
        with self._lock:
            self.conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA busy_timeout=5000;
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    chat_id TEXT NOT NULL,
                    is_group INTEGER NOT NULL,
                    direction TEXT NOT NULL,
                    from_wxid TEXT NOT NULL,
                    to_wxid TEXT NOT NULL,
                    sender_wxid TEXT NOT NULL DEFAULT '',
                    msg_type INTEGER NOT NULL,
                    content_redacted TEXT NOT NULL,
                    sensitive_hits TEXT NOT NULL DEFAULT '[]',
                    create_time INTEGER NOT NULL,
                    received_at INTEGER NOT NULL,
                    raw_json TEXT
                );
                CREATE TABLE IF NOT EXISTS replies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trigger_dedupe_key TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    to_wxid TEXT NOT NULL,
                    is_group INTEGER NOT NULL,
                    reply_text_redacted TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chat_handles (
                    handle TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL UNIQUE,
                    is_group INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS participant_handles (
                    handle TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL,
                    participant_id TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL,
                    UNIQUE(chat_id, participant_id)
                );
                CREATE TABLE IF NOT EXISTS context_tokens (
                    token TEXT PRIMARY KEY,
                    chat_handle TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ignored_messages (
                    fingerprint TEXT PRIMARY KEY,
                    reason TEXT NOT NULL,
                    is_group INTEGER NOT NULL,
                    msg_type INTEGER NOT NULL,
                    message_create_time INTEGER NOT NULL,
                    first_seen_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL,
                    seen_count INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS runtime_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                """
            )
            self._ensure_message_columns()
            self.conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_chat_time ON messages(chat_id, create_time DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_messages_chat_sender_time ON messages(chat_id, sender_wxid, create_time DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_messages_received ON messages(received_at DESC);
                CREATE INDEX IF NOT EXISTS idx_participant_handles_chat ON participant_handles(chat_id, participant_id);
                CREATE INDEX IF NOT EXISTS idx_context_tokens_handle ON context_tokens(chat_handle, expires_at DESC);
                CREATE INDEX IF NOT EXISTS idx_context_tokens_expires ON context_tokens(expires_at);
                CREATE INDEX IF NOT EXISTS idx_ignored_messages_last_seen ON ignored_messages(last_seen_at DESC);
                """
            )
            self.conn.commit()

    def _ensure_message_columns(self) -> None:
        columns = {str(row[1]) for row in self.conn.execute("PRAGMA table_info(messages)").fetchall()}
        if "sender_wxid" not in columns:
            self.conn.execute("ALTER TABLE messages ADD COLUMN sender_wxid TEXT NOT NULL DEFAULT ''")

    def add_message(self, message: ChatMessage) -> bool:
        with self._lock:
            self.ensure_chat_handle(message.chat_id, message.is_group)
            self.ensure_participant_handle(message.chat_id, message.from_wxid)
            self.ensure_participant_handle(message.chat_id, message.to_wxid)
            self.ensure_participant_handle(message.chat_id, message.sender_wxid)
            redacted = self.privacy.redact(message.content)
            raw_redacted = self._redact_raw(message.raw) if self.store_raw_messages else None
            try:
                self.conn.execute(
                    """
                    INSERT INTO messages (
                        dedupe_key, chat_id, is_group, direction, from_wxid, to_wxid, sender_wxid, msg_type,
                        content_redacted, sensitive_hits, create_time, received_at, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message.dedupe_key,
                        message.chat_id,
                        1 if message.is_group else 0,
                        message.direction,
                        message.from_wxid,
                        message.to_wxid,
                        message.sender_wxid,
                        message.msg_type,
                        redacted.text,
                        json.dumps(redacted.hits, ensure_ascii=False),
                        message.create_time or int(time.time()),
                        int(time.time()),
                        json.dumps(raw_redacted, ensure_ascii=False) if raw_redacted is not None else None,
                    ),
                )
                self.conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def add_ignored_message(self, message: ChatMessage, reason: str) -> bool:
        with self._lock:
            fingerprint = self._ignored_fingerprint(message)
            now = int(time.time())
            try:
                self.conn.execute(
                    """
                    INSERT INTO ignored_messages (
                        fingerprint, reason, is_group, msg_type, message_create_time,
                        first_seen_at, last_seen_at, seen_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        fingerprint,
                        str(reason or "ignored")[:80],
                        1 if message.is_group else 0,
                        int(message.msg_type or 0),
                        int(message.create_time or 0),
                        now,
                        now,
                    ),
                )
                self.conn.commit()
                return True
            except sqlite3.IntegrityError:
                self.conn.execute(
                    """
                    UPDATE ignored_messages
                    SET last_seen_at = ?, seen_count = seen_count + 1
                    WHERE fingerprint = ?
                    """,
                    (now, fingerprint),
                )
                self.conn.commit()
                return False

    def get_runtime_value(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self.conn.execute("SELECT value FROM runtime_state WHERE key = ?", (str(key),)).fetchone()
            return str(row["value"]) if row else default

    def set_runtime_value(self, key: str, value: str) -> None:
        with self._lock:
            now = int(time.time())
            self.conn.execute(
                """
                INSERT INTO runtime_state (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (str(key), str(value or ""), now),
            )
            self.conn.commit()

    def ensure_chat_handle(self, chat_id: str, is_group: bool) -> str:
        with self._lock:
            chat_id = str(chat_id or "").strip()
            if not chat_id:
                return ""
            now = int(time.time())
            row = self.conn.execute("SELECT handle FROM chat_handles WHERE chat_id = ?", (chat_id,)).fetchone()
            if row:
                self.conn.execute("UPDATE chat_handles SET is_group = ?, last_seen_at = ? WHERE chat_id = ?", (1 if is_group else 0, now, chat_id))
                self.conn.commit()
                return str(row["handle"])
            for _ in range(8):
                handle = f"chat_{secrets.token_urlsafe(18)}"
                try:
                    self.conn.execute(
                        "INSERT INTO chat_handles (handle, chat_id, is_group, created_at, last_seen_at) VALUES (?, ?, ?, ?, ?)",
                        (handle, chat_id, 1 if is_group else 0, now, now),
                    )
                    self.conn.commit()
                    return handle
                except sqlite3.IntegrityError:
                    existing = self.conn.execute("SELECT handle FROM chat_handles WHERE chat_id = ?", (chat_id,)).fetchone()
                    if existing:
                        return str(existing["handle"])
        raise RuntimeError("failed to allocate chat handle")

    def resolve_chat_handle(self, handle: str) -> dict[str, Any] | None:
        with self._lock:
            handle = str(handle or "").strip()
            if not handle:
                return None
            row = self.conn.execute(
                "SELECT handle, chat_id, is_group FROM chat_handles WHERE handle = ?",
                (handle,),
            ).fetchone()
            return dict(row) if row else None

    def ensure_participant_handle(self, chat_id: str, participant_id: str) -> str:
        with self._lock:
            chat_id = str(chat_id or "").strip()
            participant_id = str(participant_id or "").strip()
            if not chat_id or not participant_id:
                return ""
            now = int(time.time())
            row = self.conn.execute(
                "SELECT handle FROM participant_handles WHERE chat_id = ? AND participant_id = ?",
                (chat_id, participant_id),
            ).fetchone()
            if row:
                self.conn.execute(
                    "UPDATE participant_handles SET last_seen_at = ? WHERE chat_id = ? AND participant_id = ?",
                    (now, chat_id, participant_id),
                )
                self.conn.commit()
                return str(row["handle"])
            for _ in range(8):
                handle = f"participant_{secrets.token_urlsafe(18)}"
                try:
                    self.conn.execute(
                        """
                        INSERT INTO participant_handles (handle, chat_id, participant_id, created_at, last_seen_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (handle, chat_id, participant_id, now, now),
                    )
                    self.conn.commit()
                    return handle
                except sqlite3.IntegrityError:
                    existing = self.conn.execute(
                        "SELECT handle FROM participant_handles WHERE chat_id = ? AND participant_id = ?",
                        (chat_id, participant_id),
                    ).fetchone()
                    if existing:
                        return str(existing["handle"])
        raise RuntimeError("failed to allocate participant handle")

    def resolve_participant_handle(self, handle: str, *, chat_id: str = "") -> str:
        with self._lock:
            handle = str(handle or "").strip()
            if not handle:
                return ""
            if chat_id:
                row = self.conn.execute(
                    "SELECT participant_id FROM participant_handles WHERE handle = ? AND chat_id = ?",
                    (handle, chat_id),
                ).fetchone()
            else:
                row = self.conn.execute("SELECT participant_id FROM participant_handles WHERE handle = ?", (handle,)).fetchone()
            return str(row["participant_id"]) if row else ""

    def issue_context_token(self, chat_id: str, is_group: bool, ttl_seconds: int = 1800) -> dict[str, Any]:
        with self._lock:
            chat_handle = self.ensure_chat_handle(chat_id, is_group)
            if not chat_handle:
                return {"chat_handle": "", "context_token": "", "expires_at": 0}
            now = int(time.time())
            expires_at = now + max(int(ttl_seconds or 0), 60)
            self.conn.execute("DELETE FROM context_tokens WHERE expires_at < ?", (now,))
            for _ in range(8):
                token = f"ctx_{secrets.token_urlsafe(24)}"
                try:
                    self.conn.execute(
                        "INSERT INTO context_tokens (token, chat_handle, chat_id, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                        (token, chat_handle, chat_id, now, expires_at),
                    )
                    self.conn.commit()
                    return {"chat_handle": chat_handle, "context_token": token, "expires_at": expires_at}
                except sqlite3.IntegrityError:
                    continue
        raise RuntimeError("failed to allocate context token")

    def validate_context_token(self, chat_handle: str, context_token: str) -> tuple[dict[str, Any] | None, str]:
        with self._lock:
            chat = self.resolve_chat_handle(chat_handle)
            if not chat:
                return None, "unknown_context_handle"
            context_token = str(context_token or "").strip()
            if not context_token:
                return None, "missing_context_token"
            row = self.conn.execute(
                "SELECT chat_id, expires_at FROM context_tokens WHERE token = ? AND chat_handle = ?",
                (context_token, chat_handle),
            ).fetchone()
            if not row:
                return None, "unauthorized_context_token"
            now = int(time.time())
            if int(row["expires_at"] or 0) < now:
                self.conn.execute("DELETE FROM context_tokens WHERE token = ?", (context_token,))
                self.conn.commit()
                return None, "expired_context_token"
            if str(row["chat_id"] or "") != str(chat["chat_id"] or ""):
                return None, "unauthorized_context_token"
            return chat, ""

    def add_reply(self, trigger_key: str, chat_id: str, to_wxid: str, is_group: bool, text: str, status: str, reason: str = "") -> None:
        with self._lock:
            redacted = self.privacy.redact(text)
            redacted_reason = self.privacy.redact(reason).text
            self.conn.execute(
                """
                INSERT INTO replies (trigger_dedupe_key, chat_id, to_wxid, is_group, reply_text_redacted, status, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (trigger_key, chat_id, to_wxid, 1 if is_group else 0, redacted.text, status, redacted_reason, int(time.time())),
            )
            self.conn.commit()

    def recent_messages(
        self,
        chat_id: str,
        since_ts: int = 0,
        limit: int = 80,
        max_chars: int = 12000,
        sender_wxid: str = "",
        safe_view: bool = True,
    ) -> list[dict[str, Any]]:
        with self._lock:
            safe_limit = max(int(limit or 1), 1)
            sender_clause = "AND sender_wxid = ?" if sender_wxid else ""
            params: tuple[Any, ...] = (chat_id, since_ts, sender_wxid, safe_limit) if sender_wxid else (chat_id, since_ts, safe_limit)
            rows = self.conn.execute(
                f"""
                SELECT chat_id, is_group, direction, from_wxid, to_wxid, sender_wxid, msg_type, content_redacted, sensitive_hits, create_time
                FROM messages
                WHERE chat_id = ? AND create_time >= ? {sender_clause}
                ORDER BY create_time DESC, id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            selected: list[dict[str, Any]] = []
            total_chars = 0
            for row in rows:
                text = str(row["content_redacted"] or "")
                next_total = total_chars + len(text)
                if max_chars and selected and next_total > max_chars:
                    break
                total_chars = next_total
                selected.append(dict(row))
            ordered = list(reversed(selected))
            return self._safe_rows(ordered) if safe_view else ordered

    def search_messages(self, chat_id: str, query: str, limit: int = 20, safe_view: bool = True) -> list[dict[str, Any]]:
        with self._lock:
            safe_limit = max(int(limit or 1), 1)
            pattern = f"%{query}%"
            rows = self.conn.execute(
                """
                SELECT chat_id, is_group, direction, from_wxid, to_wxid, sender_wxid, msg_type, content_redacted, sensitive_hits, create_time
                FROM messages
                WHERE chat_id = ? AND content_redacted LIKE ?
                ORDER BY create_time DESC, id DESC
                LIMIT ?
                """,
                (chat_id, pattern, safe_limit),
            ).fetchall()
            out = [dict(row) for row in rows]
            return self._safe_rows(out) if safe_view else out

    def has_chat(self, chat_id: str) -> bool:
        with self._lock:
            row = self.conn.execute("SELECT 1 FROM messages WHERE chat_id = ? LIMIT 1", (chat_id,)).fetchone()
            return row is not None

    def has_private_peer(self, wxid: str) -> bool:
        with self._lock:
            row = self.conn.execute(
                """
                SELECT 1 FROM messages
                WHERE is_group = 0 AND (from_wxid = ? OR to_wxid = ? OR chat_id = ?)
                LIMIT 1
                """,
                (wxid, wxid, wxid),
            ).fetchone()
            return row is not None

    def runtime_stats(self) -> dict[str, Any]:
        with self._lock:
            now = int(time.time())
            active_tokens = self.conn.execute(
                "SELECT COUNT(*) FROM context_tokens WHERE expires_at >= ?",
                (now,),
            ).fetchone()[0]
            latest_message = self.conn.execute("SELECT MAX(received_at) FROM messages").fetchone()[0]
            latest_reply = self.conn.execute("SELECT MAX(created_at) FROM replies").fetchone()[0]
            return {
                "message_count": int(self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] or 0),
                "reply_count": int(self.conn.execute("SELECT COUNT(*) FROM replies").fetchone()[0] or 0),
                "ignored_message_count": int(self.conn.execute("SELECT COUNT(*) FROM ignored_messages").fetchone()[0] or 0),
                "runtime_state_count": int(self.conn.execute("SELECT COUNT(*) FROM runtime_state").fetchone()[0] or 0),
                "known_chat_count": int(self.conn.execute("SELECT COUNT(*) FROM chat_handles").fetchone()[0] or 0),
                "known_participant_count": int(self.conn.execute("SELECT COUNT(*) FROM participant_handles").fetchone()[0] or 0),
                "active_context_token_count": int(active_tokens or 0),
                "latest_message_received_at": int(latest_message or 0),
                "latest_reply_created_at": int(latest_reply or 0),
            }

    def cleanup(self, retention_days: int) -> int:
        with self._lock:
            if retention_days <= 0:
                return 0
            cutoff = int(time.time()) - retention_days * 86400
            cur = self.conn.execute("DELETE FROM messages WHERE received_at < ?", (cutoff,))
            ignored_cur = self.conn.execute("DELETE FROM ignored_messages WHERE last_seen_at < ?", (cutoff,))
            self.conn.execute("DELETE FROM context_tokens WHERE expires_at < ?", (int(time.time()),))
            self.conn.commit()
            return int(cur.rowcount or 0) + int(ignored_cur.rowcount or 0)

    def _ignored_fingerprint(self, message: ChatMessage) -> str:
        identity = {
            "dedupe_key": message.dedupe_key,
            "msg_id": message.msg_id,
            "new_msg_id": message.new_msg_id,
            "from_wxid": message.from_wxid,
            "to_wxid": message.to_wxid,
            "sender_wxid": message.sender_wxid,
            "chat_id": message.chat_id,
            "msg_type": message.msg_type,
            "create_time": message.create_time,
            "content": message.content,
        }
        payload = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        namespaced = ("ignored-message-v1\0" + payload).encode("utf-8")
        if self.fingerprint_key:
            return hmac.new(self.fingerprint_key.encode("utf-8"), namespaced, hashlib.sha256).hexdigest()
        return hashlib.sha256(namespaced).hexdigest()

    def _harden_db_permissions(self) -> None:
        for candidate in (self.path, Path(str(self.path) + "-wal"), Path(str(self.path) + "-shm")):
            try:
                if candidate.exists():
                    candidate.chmod(0o600)
            except OSError:
                continue

    def _redact_raw(self, raw: dict[str, Any]) -> dict[str, Any]:
        clean: dict[str, Any] = {}
        for key, value in (raw or {}).items():
            if isinstance(value, dict):
                clean[key] = self._redact_raw(value)
            elif isinstance(value, list):
                clean[key] = [self._redact_raw(item) if isinstance(item, dict) else self.privacy.redact(item).text for item in value]
            elif isinstance(value, str):
                clean[key] = self.privacy.redact(value).text
            else:
                clean[key] = value
        return clean

    def _safe_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        labels: dict[str, str] = {}

        def label(value: object) -> str:
            text = str(value or "")
            if not text:
                return ""
            if text not in labels:
                labels[text] = f"participant_{len(labels) + 1}"
            return labels[text]

        safe: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            raw_chat_id = str(item.get("chat_id") or "")
            is_group = bool(item.get("is_group"))
            item["chat_handle"] = self.ensure_chat_handle(raw_chat_id, is_group)
            item["from_participant_handle"] = self.ensure_participant_handle(raw_chat_id, str(item.get("from_wxid") or ""))
            item["to_participant_handle"] = self.ensure_participant_handle(raw_chat_id, str(item.get("to_wxid") or ""))
            item["sender_participant_handle"] = self.ensure_participant_handle(raw_chat_id, str(item.get("sender_wxid") or ""))
            item["chat_id"] = "current_chat"
            item["from_wxid"] = label(item.get("from_wxid"))
            item["to_wxid"] = label(item.get("to_wxid"))
            item["sender_wxid"] = label(item.get("sender_wxid"))
            item["content_redacted"] = self.privacy.redact(item.get("content_redacted", "")).text
            item.pop("sensitive_hits", None)
            safe.append(item)
        return safe