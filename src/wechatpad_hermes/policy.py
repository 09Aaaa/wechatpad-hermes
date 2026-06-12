from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from .config import Settings, load_yaml
from .messages import ChatMessage


@dataclass(frozen=True)
class RouteDecision:
    should_respond: bool
    reason: str
    conversation_id: str
    target_wxid: str
    context_since_ts: int
    role: str
    store_message: bool = True


class Policy:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        data = load_yaml(settings.policy_path)
        self.owner_wxids = set(settings.owner_wxids) | set(_as_list(data.get("owner_wxids")))
        private = data.get("private") if isinstance(data.get("private"), dict) else {}
        groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}
        context = data.get("context") if isinstance(data.get("context"), dict) else {}
        self.allow_all_private = bool(private.get("allow_all", settings.allow_all_private))
        self.allow_all_groups = bool(groups.get("allow_all", settings.allow_all_groups))
        self.allowed_private_wxids = set(settings.allowed_private_wxids) | set(_as_list(private.get("allowed_wxids")))
        self.allowed_group_chatrooms = set(settings.allowed_group_chatrooms) | set(_as_list(groups.get("allowed_chatrooms")))
        self.blocked_wxids = set(settings.blocked_wxids) | set(_as_list(private.get("blocked_wxids")))
        self.blocked_chatrooms = set(settings.blocked_group_chatrooms) | set(_as_list(groups.get("blocked_chatrooms")))
        self.require_mention = bool(groups.get("require_mention", True))
        self.history_days = int(context.get("history_days", settings.history_days))
        self.max_messages = int(context.get("max_messages", settings.max_context_messages))
        self.max_chars = int(context.get("max_chars", settings.max_context_chars))

    def decide(self, message: ChatMessage) -> RouteDecision:
        if message.msg_type != 1:
            return self._deny(message, "non_text_message")
        if message.from_wxid in self.blocked_wxids:
            return self._deny(message, "blocked_private_sender")
        if self.settings.bot_wxid and message.from_wxid == self.settings.bot_wxid:
            return self._deny(message, "ignore_bot_self_message")
        if self.settings.bot_wxid and message.sender_wxid == self.settings.bot_wxid:
            return self._deny(message, "ignore_bot_group_self_message")
        if message.sender_wxid and message.sender_wxid in self.blocked_wxids:
            return self._deny(message, "blocked_group_sender")
        if message.is_group:
            return self._decide_group(message)
        return self._decide_private(message)

    def _decide_private(self, message: ChatMessage) -> RouteDecision:
        if not self.allow_all_private and message.from_wxid not in self.allowed_private_wxids and message.from_wxid not in self.owner_wxids:
            return self._deny(message, "private_sender_not_allowed")
        role = "owner" if message.from_wxid in self.owner_wxids else "private"
        return RouteDecision(
            should_respond=True,
            reason="private_direct_to_hermes",
            conversation_id="wechat:private",
            target_wxid=message.from_wxid,
            context_since_ts=self._context_since_ts(),
            role=role,
        )

    def _decide_group(self, message: ChatMessage) -> RouteDecision:
        chatroom = message.chat_id
        if chatroom in self.blocked_chatrooms:
            return self._deny(message, "group_chatroom_blocked")
        if not self.allow_all_groups and chatroom not in self.allowed_group_chatrooms:
            return self._deny(message, "group_chatroom_not_allowed")
        if self.require_mention and not self.is_mentioned(message):
            return self._deny(message, "group_message_without_bot_mention")
        return RouteDecision(
            should_respond=True,
            reason="group_bot_mention",
            conversation_id="wechat:group",
            target_wxid=chatroom,
            context_since_ts=self._context_since_ts(),
            role="group",
        )

    def is_mentioned(self, message: ChatMessage) -> bool:
        content = message.content or ""
        for name in self.settings.bot_names:
            if name and _contains_name_mention(content, name):
                return True
        if self.settings.bot_wxid:
            raw_text = "\n".join(
                str(message.raw.get(key) or "")
                for key in ("MsgSource", "msg_source", "PushContent", "push_content")
            )
            return self.settings.bot_wxid in raw_text or self.settings.bot_wxid in content
        return False

    def _context_since_ts(self) -> int:
        return int(time.time()) - max(self.history_days, 0) * 86400

    def _deny(self, message: ChatMessage, reason: str) -> RouteDecision:
        # Normal group chatter is retained as same-group context. Blocked,
        # non-text, disallowed, and BOT-self messages are not context material.
        store_message = reason == "group_message_without_bot_mention"
        return RouteDecision(False, reason, "", message.chat_id or message.from_wxid, 0, "none", store_message)


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _contains_name_mention(content: str, name: str) -> bool:
    escaped = re.escape(name.strip())
    if not escaped:
        return False
    pattern = re.compile(rf"(?<![A-Za-z0-9_@＠])[@＠]\s*{escaped}(?![A-Za-z0-9_-])", re.I)
    return bool(pattern.search(content or ""))
