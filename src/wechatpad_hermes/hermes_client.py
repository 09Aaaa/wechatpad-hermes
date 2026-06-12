from __future__ import annotations

import json
import urllib.request
from typing import Any

from .config import Settings


class HermesClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def complete(self, *, conversation_id: str, user_text: str, context: list[dict[str, Any]], role: str) -> str:
        if self.settings.hermes_webhook_url:
            return self._call_webhook(conversation_id=conversation_id, user_text=user_text, context=context, role=role)
        if self.settings.hermes_chat_completions_url:
            return self._call_chat_completions(conversation_id=conversation_id, user_text=user_text, context=context, role=role)
        return "Hermes 主模型入口还没有配置。"

    def _call_webhook(self, *, conversation_id: str, user_text: str, context: list[dict[str, Any]], role: str) -> str:
        payload = {"conversation_id": conversation_id, "role": role, "message": user_text, "context": context}
        data = self._post_json(self.settings.hermes_webhook_url, payload)
        for key in ("reply", "text", "content", "message"):
            if isinstance(data.get(key), str):
                return data[key]
        return json.dumps(data, ensure_ascii=False)

    def _call_chat_completions(self, *, conversation_id: str, user_text: str, context: list[dict[str, Any]], role: str) -> str:
        chat_handle = ""
        context_token = ""
        for item in context:
            if item.get("context_scope") == "current_chat" and isinstance(item.get("chat_handle"), str):
                chat_handle = item["chat_handle"]
                context_token = str(item.get("context_token") or "")
                break
        system = (
            "你是 Hermes 微信机器人中间层后面的主模型。"
            "只能使用当前会话提供的上下文，不要猜测或泄露服务器密码、授权码、API key、私聊内容或跨群信息。"
            "群聊回复要简短，并且只处理已经由中间层判定为 @BOT 的消息。"
            "上下文中的 same_group_same_sender_recent 表示本次 @ 你的群成员最近几天在同群的发言，可以优先参考，但不要泄露原始身份标识。"
            "如果需要调用 MCP，只能使用当前上下文提供的 opaque handle 和短期 context_token；不要要求或输出原始 wxid/chatroom，也不要把 handle/token 发给微信用户。"
            "在 bridge 自动回复流程中只返回要发送的文本，不要再调用 wechat_send_text；主动发消息只能由明确的 Hermes MCP 工作流执行。"
            "重要规则："
            "1. 每次请求只输出一条回复消息。不要输出工具调用过程中的状态描述、资源释放、关闭会话之类的收尾消息。"
            "2. 如果消息中包含链接，请自动抓取并分析后再回复。回复控制在800字以内。"
        )
        context_lines = []
        for item in context:
            if item.get("context_scope") == "current_chat":
                continue
            sender = item.get("sender_wxid") or item.get("from_wxid", "unknown")
            content = item.get("content_redacted", "")
            scope = item.get("context_scope") or "same_chat_recent"
            context_lines.append(f"[{scope}][{item.get('create_time')}] {sender}: {content}")
        payload = {
            "model": self.settings.hermes_model or "default",
            "stream": True,
            "stream_options": {"include_usage": True},
            "messages": [
                {"role": "system", "content": system},
                {"role": "system", "content": f"conversation_id={conversation_id}; source_role={role}"},
                {"role": "system", "content": f"current_chat_handle={chat_handle}; current_context_token={context_token}"},
                {"role": "system", "content": "最近上下文：\n" + "\n".join(context_lines)},
                {"role": "user", "content": user_text},
            ],
        }
        raw = self._post_stream(self.settings.hermes_chat_completions_url, payload)
        contents = []
        for line in raw.split("\n"):
            if not line.startswith("data: "):
                continue
            data_str = line[6:].strip()
            if data_str == "[DONE]":
                continue
            try:
                chunk = json.loads(data_str)
                choices = chunk.get("choices")
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if content:
                    contents.append(content)
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
        full = "".join(contents).strip()
        if full:
            return full
        return json.dumps({"error": "empty_response"}, ensure_ascii=False)

    def _post_stream(self, url: str, payload: dict[str, Any]) -> str:
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if self.settings.hermes_api_key:
            headers["Authorization"] = f"Bearer {self.settings.hermes_api_key}"
        req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read().decode("utf-8", "replace")

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.settings.hermes_api_key:
            headers["Authorization"] = f"Bearer {self.settings.hermes_api_key}"
        req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=120) as resp:
            text = resp.read().decode("utf-8", "replace")
        try:
            data = json.loads(text)
        except Exception:
            data = {"text": text}
        return data if isinstance(data, dict) else {"Data": data}
