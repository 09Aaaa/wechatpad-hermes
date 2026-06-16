from __future__ import annotations

import re
import html
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any


GROUP_CONTENT_PREFIX_RE = re.compile(r"^([A-Za-z0-9_@.\-]{2,128}):\n(.*)$", re.S)
GROUP_SENDER_KEYS = (
    "ActualUserName",
    "actualUserName",
    "actual_user_name",
    "ActualSender",
    "actualSender",
    "actual_sender",
    "SenderUserName",
    "senderUserName",
    "sender_user_name",
    "SenderWxid",
    "senderWxid",
    "sender_wxid",
    "Sender",
    "sender",
    "FromMemberName",
    "fromMemberName",
    "from_member_name",
)


def nested_str(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("str") or value.get("string") or value.get("String") or "")
    if value is None:
        return ""
    return str(value)


LINK_MESSAGE_TYPES = {49, 51}
URL_RE = re.compile(r"https?://[^\s<>'\"]+", re.I)


def extract_quoted_text(raw, content, msg_type):
    """Extract plain text from quoted/reply messages."""
    if msg_type not in {1, 49, 57}:
        return ""
    if not content:
        return ""
    stripped = content.strip()
    if not stripped.startswith(("<msg>", "<xml>", "<appmsg", "<")):
        return ""
    title = _extract_xml_tag(stripped, "title")
    des = _extract_xml_tag(stripped, "des")
    quoted_content = ""
    quoted_match = re.search(r"<refermsg>.*?<content><!\[CDATA\[(.*?)\]\]></content>.*?</refermsg>", stripped, re.S)
    if quoted_match:
        quoted_content = _clean_xml_text(quoted_match.group(1))
    parts = []
    if title:
        parts.append(_clean_xml_text(title))
    if des and des != title:
        parts.append(_clean_xml_text(des))
    if quoted_content and quoted_content not in " ".join(parts):
        parts.append("[引用: " + quoted_content + "]")
    if parts:
        return "\n".join(parts)
    return ""

def extract_link_text(raw: dict[str, Any], content: str, msg_type: int) -> str:
    """Return a plain-text URL payload for WeChat appmsg/link cards."""
    if msg_type not in LINK_MESSAGE_TYPES:
        return ""
    haystack_parts = [content]
    for key in ("Url", "url", "Link", "link", "AppMsg", "appmsg", "Xml", "xml"):
        value = nested_str(raw.get(key))
        if value:
            haystack_parts.append(value)
    haystack = "\n".join(haystack_parts)
    url = _extract_url_from_xmlish(haystack) or _extract_url_by_regex(haystack)
    if not url:
        return ""
    title = _extract_xml_tag(haystack, "title") or _extract_xml_tag(haystack, "des")
    title = _clean_xml_text(title)[:120]
    if title:
        return f"请使用 link-analyzer 技能解析这个链接：{url}\n标题：{title}"
    return f"请使用 link-analyzer 技能解析这个链接：{url}"


def _extract_url_from_xmlish(text: str) -> str:
    if not text:
        return ""
    raw = html.unescape(text)
    for tag in ("url", "pagepath"):
        val = _extract_xml_tag(raw, tag)
        found = _extract_url_by_regex(val)
        if found:
            return found
    try:
        root = ET.fromstring(raw.strip())
        for tag in ("url", "pagepath"):
            node = root.find(f".//{tag}")
            if node is not None and node.text:
                found = _extract_url_by_regex(html.unescape(node.text))
                if found:
                    return found
    except Exception:
        pass
    return ""


def _extract_xml_tag(text: str, tag: str) -> str:
    if not text:
        return ""
    m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", text, re.I | re.S)
    return _clean_xml_text(m.group(1)) if m else ""


def _extract_url_by_regex(text: str) -> str:
    if not text:
        return ""
    m = URL_RE.search(html.unescape(text))
    if not m:
        return ""
    return m.group(0).rstrip(").,;，。；、]>\"")


def _clean_xml_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"^<!\[CDATA\[(.*)\]\]>$", r"\1", text.strip(), flags=re.S)
    return html.unescape(text).strip()


@dataclass(frozen=True)
class ChatMessage:
    msg_id: str
    new_msg_id: str
    from_wxid: str
    to_wxid: str
    content: str
    msg_type: int
    create_time: int
    chat_id: str
    is_group: bool
    sender_wxid: str = ""
    direction: str = "inbound"
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def dedupe_key(self) -> str:
        if self.new_msg_id and self.new_msg_id != "0":
            return self.new_msg_id
        if self.msg_id and self.msg_id != "0":
            return self.msg_id
        return f"{self.from_wxid}:{self.to_wxid}:{self.create_time}:{self.content[:80]}"


def parse_message(raw: dict[str, Any], bot_wxid: str = "") -> ChatMessage | None:
    if not isinstance(raw, dict):
        return None
    from_wxid = nested_str(raw.get("FromUserName") or raw.get("fromUserName") or raw.get("from_user_name"))
    to_wxid = nested_str(raw.get("ToUserName") or raw.get("toUserName") or raw.get("to_user_name"))
    content = nested_str(raw.get("Content") or raw.get("content"))
    msg_type_value = raw.get("MsgType", raw.get("msgType", raw.get("msg_type", 0)))
    try:
        msg_type = int(msg_type_value or 0)
    except Exception:
        msg_type = 0
    if not from_wxid or msg_type == 0:
        return None
    is_group = from_wxid.endswith("@chatroom") or to_wxid.endswith("@chatroom")
    chat_id = from_wxid if is_group and from_wxid.endswith("@chatroom") else to_wxid if is_group else from_wxid
    if not chat_id and bot_wxid:
        chat_id = to_wxid if from_wxid == bot_wxid else from_wxid
    sender_wxid = ""
    if is_group:
        sender_wxid, content = extract_group_sender(raw, content, chat_id=chat_id, bot_wxid=bot_wxid)
    link_text = extract_link_text(raw, content, msg_type)
    if link_text:
        content = link_text
        msg_type = 1
    elif msg_type in {1, 49, 57}:
        quoted_text = extract_quoted_text(raw, content, msg_type)
        if quoted_text:
            content = quoted_text
            msg_type = 1
    return ChatMessage(
        msg_id=str(raw.get("MsgId") or raw.get("msg_id") or ""),
        new_msg_id=str(raw.get("NewMsgId") or raw.get("new_msg_id") or ""),
        from_wxid=from_wxid,
        to_wxid=to_wxid,
        content=content,
        msg_type=msg_type,
        create_time=int(raw.get("CreateTime") or raw.get("create_time") or 0),
        chat_id=chat_id,
        is_group=is_group,
        sender_wxid=sender_wxid,
        raw=raw,
    )


def extract_group_sender(raw: dict[str, Any], content: str, *, chat_id: str, bot_wxid: str = "") -> tuple[str, str]:
    sender = _first_sender_value(raw)
    body = content
    match = GROUP_CONTENT_PREFIX_RE.match(content or "")
    if match:
        prefix = match.group(1).strip()
        possible_sender = prefix if _looks_like_group_sender(prefix, chat_id=chat_id, bot_wxid=bot_wxid) else ""
        if not sender and possible_sender:
            sender = possible_sender
        if sender and (prefix == sender or possible_sender):
            body = match.group(2)
    if sender in {chat_id, bot_wxid}:
        sender = ""
    return sender, body


def _first_sender_value(raw: dict[str, Any]) -> str:
    for key in GROUP_SENDER_KEYS:
        value = nested_str(raw.get(key))
        if value and not value.endswith("@chatroom"):
            return value
    return ""


def _looks_like_group_sender(value: str, *, chat_id: str, bot_wxid: str = "") -> bool:
    if not value or value in {chat_id, bot_wxid}:
        return False
    if value.endswith("@chatroom"):
        return False
    if value.startswith(("wxid_", "gh_")) or value.endswith("@openim"):
        return True
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_\-.]{2,127}", value))


def extract_messages(payload: dict[str, Any], bot_wxid: str = "") -> list[ChatMessage]:
    data = payload.get("Data") if isinstance(payload.get("Data"), dict) else None
    candidates: list[Any]
    if data and isinstance(data.get("AddMsgs"), list):
        candidates = data.get("AddMsgs") or []
    elif isinstance(payload.get("AddMsgs"), list):
        candidates = payload.get("AddMsgs") or []
    else:
        candidates = [payload]
    messages: list[ChatMessage] = []
    for item in candidates:
        message = parse_message(item, bot_wxid=bot_wxid)
        if message:
            messages.append(message)
    return messages