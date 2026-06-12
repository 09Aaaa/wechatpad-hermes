from __future__ import annotations

import re
from dataclasses import dataclass


SENSITIVE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("authcode", re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")),
    (
        "password",
        re.compile(
            r"(?i)(?:\b(?:password|passwd|pwd)\b|登录密码|服务器密码|密码|口令)"
            r"(?:\s*(?:[:=：]|是|为|is)\s*|\s+)[^\s,，;；。]+"
        ),
    ),
    (
        "api_key",
        re.compile(
            r"(?i)(?:\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|token|secret|authorization)\b|授权码|密钥|令牌|管理密钥|后台密钥)"
            r"(?:\s*(?:[:=：]|是|为)\s*|\s+)(?:Bearer\s+)?[A-Za-z0-9._~+/=-]{6,}"
        ),
    ),
    ("bearer_token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}\b")),
    ("context_token", re.compile(r"\bctx_[A-Za-z0-9_-]{16,}\b")),
    ("opaque_handle", re.compile(r"\b(?:chat|participant)_[A-Za-z0-9_-]{16,}\b")),
    ("phone", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("ipv4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{2,5})?\b")),
    ("wxid", re.compile(r"\bwxid_[A-Za-z0-9_]{6,}\b")),
    ("gh_id", re.compile(r"\bgh_[A-Za-z0-9_]{3,}\b")),
    ("chatroom", re.compile(r"\b[A-Za-z0-9_.-]{2,128}@chatroom\b")),
    ("openim", re.compile(r"\b[A-Za-z0-9_.-]{2,128}@openim\b")),
    ("windows_path", re.compile(r"\b[A-Za-z]:\\[^\s\"']+")),
    ("linux_secret_path", re.compile(r"\b/(?:root|opt|mnt|home)/[^\s\"']*(?:\.env|config|auth|token|secret|password)[^\s\"']*", re.I)),
]


GROUP_LEAK_PHRASES = [
    "私聊",
    "私密",
    "服务器密码",
    "授权码",
    "api key",
    "token",
    "context_token",
    "chat_handle",
    "participant_handle",
    "密码是",
    "后台",
    "wxid_",
    "@chatroom",
    "@openim",
    "跨群",
    "跨会话",
]


PRIVATE_LEAK_PHRASES = [
    "其他私聊",
    "私密",
    "服务器密码",
    "授权码",
    "api key",
    "token",
    "context_token",
    "chat_handle",
    "participant_handle",
    "密码是",
    "后台",
    "wxid_",
    "@chatroom",
    "@openim",
    "跨群",
    "跨会话",
]


@dataclass(frozen=True)
class PrivacyResult:
    text: str
    hits: list[str]


class PrivacyFilter:
    def redact(self, text: object) -> PrivacyResult:
        value = "" if text is None else str(text)
        hits: list[str] = []
        for label, pattern in SENSITIVE_PATTERNS:
            if pattern.search(value):
                hits.append(label)
                value = pattern.sub(f"[{label.upper()}_REDACTED]", value)
        return PrivacyResult(value, sorted(set(hits)))

    def contains_sensitive(self, text: object) -> bool:
        return bool(self.redact(text).hits)

    def safe_for_group_reply(self, text: object) -> tuple[bool, str, list[str]]:
        return self._safe_reply(text, GROUP_LEAK_PHRASES)

    def safe_for_private_reply(self, text: object) -> tuple[bool, str, list[str]]:
        return self._safe_reply(text, PRIVATE_LEAK_PHRASES)

    def _safe_reply(self, text: object, leak_phrases: list[str]) -> tuple[bool, str, list[str]]:
        result = self.redact(text)
        if result.hits:
            return False, result.text, result.hits
        lower = result.text.lower()
        hits = [phrase for phrase in leak_phrases if phrase.lower() in lower]
        if hits:
            return False, result.text, hits
        return True, result.text, []


def mask_secret(value: str, keep_start: int = 8, keep_end: int = 4) -> str:
    value = str(value or "")
    return "[SECRET_CONFIGURED]" if value else ""
