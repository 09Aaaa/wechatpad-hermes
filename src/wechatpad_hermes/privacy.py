from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


SENSITIVE_FIELD_NAMES = {
    "aeskey",
    "autoauthkey",
    "clientsessionkey",
    "cooike",
    "cookie",
    "curdecryptseqiv",
    "curencryptseqiv",
    "decrypt_part2_hash256",
    "decrypt_part3_hash256",
    "decrypt_part4_hash256",
    "decryptmmtlsapplicationiv",
    "decryptmmtlsapplicationkey",
    "decryptmmtlsiv",
    "decryptmmtlskey",
    "decrptshortmmtlsiv",
    "decrptshortmmtlskey",
    "deviceid_byte",
    "deviceid_str",
    "devicetoken",
    "earlydatapart",
    "encrptmmtlsapplicationiv",
    "encrptmmtlsapplicationkey",
    "encrptmmtlsiv",
    "encrptmmtlskey",
    "encrptshortmmtlsiv",
    "encrptshortmmtlskey",
    "hkdexpand_application_key",
    "hkdexpand_clientfinish_key",
    "hkdexpand_secret_key",
    "hkdfexpand_application_key",
    "hkdfexpand_clientfinish_key",
    "hkdfexpand_pskaccess_key",
    "hkdfexpand_pskrefresh_key",
    "hkdfexpand_secret_key",
    "hkdfexpand_serverfinish_key",
    "hkdfexpand_info_serverfinish_key",
    "hybridecdhinitserverpubkey",
    "hybridecdhprivkey",
    "hybridecdhpubkey",
    "login_data",
    "logindata",
    "loginecdhkey",
    "loginrsaver",
    "mmtlskey",
    "newpassword",
    "newsendbufferhashs",
    "notifykey",
    "pass",
    "password",
    "passwd",
    "proxy",
    "proxypassword",
    "pskiv",
    "pskkey",
    "pwd",
    "rsaprivatekey",
    "rsapublickey",
    "serversessionkey",
    "sessionkey",
    "sessionkey_2",
    "shakehandecdhkey",
    "shakehandecdhkeyhash",
    "shakehandprikey",
    "shakehandprikey_2",
    "shakehandpubkey",
    "shakehandpubkey_2",
    "sync_key",
    "synckey",
    "ticket",
    "token",
    "uuid",
}


SENSITIVE_FIELD_SUBSTRINGS = (
    "authkey",
    "authorization",
    "cookie",
    "ecdh",
    "encrypt",
    "encrpt",
    "decrypt",
    "decrpt",
    "hkdf",
    "key",
    "loginrsa",
    "mmtls",
    "newpass",
    "password",
    "passwd",
    "prikey",
    "privatekey",
    "proxy",
    "pwd",
    "secret",
    "session",
    "shakehand",
    "ticket",
    "token",
)


FIELD_REDACTION_ALLOWLIST = {
    "admin_key_configured",
    "api_key_configured",
    "clientversion",
    "contentlength",
    "context_token_ttl_seconds",
    "codevalue",
    "db_known_messages",
    "db_known_replies",
    "debug",
    "deviceinfo",
    "devicename",
    "devicetype",
    "email",
    "enable_service",
    "enableservice",
    "hermes_api_key_configured",
    "headurl",
    "history_days",
    "id",
    "login_date",
    "logindate",
    "loginmode",
    "mars_host",
    "marshost",
    "max_context_chars",
    "max_context_messages",
    "message",
    "mmtlshost",
    "mmtlsip",
    "mobile",
    "nickname",
    "online",
    "online_secs",
    "onlinesecs",
    "online_since",
    "onlinesince",
    "osversion",
    "poll_interval",
    "retention_days",
    "rommodel",
    "active_context_token_count",
    "wechatpad_context_token_ttl_seconds",
    "wechatpad_admin_key_configured",
    "success",
    "uin",
    "username",
    "wxid",
}


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
            r"\s*(?:[:=：])\s*(?:Bearer\s+)?(?!\*{3}\b)[A-Za-z0-9._~+/=-]{8,}"
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
    "context_token",
    "chat_handle",
    "participant_handle",
    "密码是",
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
    "context_token",
    "chat_handle",
    "participant_handle",
    "密码是",
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
    def redact_data(self, data: Any) -> tuple[Any, list[str]]:
        hits: list[str] = []
        clean = self._redact_data(data, hits, parent_key="")
        return clean, sorted(set(hits))

    def _redact_data(self, data: Any, hits: list[str], *, parent_key: str) -> Any:
        if isinstance(data, dict):
            clean: dict[str, Any] = {}
            redacted_secret_fields = 0
            for key, value in data.items():
                key_text = str(key)
                if _is_sensitive_field_name(key_text):
                    hits.append("secret_field")
                    redacted_secret_fields += 1
                else:
                    clean[key_text] = self._redact_data(value, hits, parent_key=key_text)
            if redacted_secret_fields:
                clean["_redacted_secret_field_count"] = redacted_secret_fields
            return clean
        if isinstance(data, list):
            return [self._redact_data(item, hits, parent_key=parent_key) for item in data]
        if data is None or isinstance(data, (bool, int, float)):
            return data
        result = self.redact(data)
        hits.extend(result.hits)
        return result.text

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


def _normalize_field_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _is_sensitive_field_name(value: str) -> bool:
    normalized = _normalize_field_name(value)
    allowlist = {_normalize_field_name(item) for item in FIELD_REDACTION_ALLOWLIST}
    sensitive_names = {_normalize_field_name(item) for item in SENSITIVE_FIELD_NAMES}
    if not normalized or normalized in allowlist:
        return False
    if normalized in sensitive_names:
        return True
    return any(part in normalized for part in SENSITIVE_FIELD_SUBSTRINGS)