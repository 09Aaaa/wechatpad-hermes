from __future__ import annotations

import json
import re
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
            "你是 Hermes 微信机器人。"
            "只能使用当前会话提供的上下文，不要猜测或泄露服务器密码、授权码、API key、私聊内容或跨群信息。"
            "群聊回复要简短，只输出格式化的解析结果。"
            "在 bridge 自动回复流程中只返回要发送的文本，不要再调用 wechat_send_text。"
            "【链接解析 - 严格按以下规则，不要自由发挥】"
            "每个链接一条回复，用 terminal 执行对应 curl，然后按模板输出。"
            ""
            "=== B站 ==="
            "短链接先resolve: curl -sI -L -o /dev/null -w '%{url_effective}' URL | grep -oP 'BV\\w+'"
            "curl -s 'https://api.bilibili.com/x/web-interface/view?bvid={bvid}' -H 'User-Agent: Mozilla/5.0'"
            "模板:"
            "📺 {title}"
            "👤 UP主：{owner.name}"
            "📝 简介：{desc[:100]}"
            "💖 {stat.like}  🪙 {stat.coin}  ⭐ {stat.favorite}"
            "👁️ 播放：{stat.view}  💬 评论：{stat.reply}  💬 弹幕：{stat.danmaku}"
            "───"
            "🔗 https://www.bilibili.com/video/{bvid}"
            ""
            "=== GitHub ==="
            "curl -s 'https://api.github.com/repos/{owner}/{repo}'"
            "模板:"
            "📦 GitHub 仓库 | {name}"
            "👤 作者：{owner}"
            "📝 {desc[:100]}（空则'暂无描述'）"
            "───"
            "⭐ {stars} | 🍴 {forks} | 💻 {language}"
            "🔗 {html_url}"
            ""
            "=== Gitee ==="
            "curl -s 'https://gitee.com/api/v5/repos/{owner}/{repo}'"
            "模板:"
            "📦 Gitee 仓库 | {name}"
            "👤 作者：{owner}"
            "📝 {desc[:100]}（空则'暂无描述'）"
            "───"
            "⭐ {stargazers_count} | 🍴 {forks_count} | 💻 {language}"
            "🔗 {html_url}"
            ""
            "=== 抖音 ==="
            "用 python3 + urllib + re 抓取页面:"
            "  headers={'user-agent':'Mozilla/5.0 (Linux; Android 8.0) AppleWebKit/537.36'}"
            "  先GET短链接获取重定向后的URL，提取video_id"
            "  再GET https://www.iesdouyin.com/share/video/{video_id}/"
            "  从 ROUTER_DATA JSON提取: author.nickname, desc, video.cover.url_list[0]"
            "模板:"
            "🎵 抖音视频"
            "👤 作者：{nickname}"
            "📝 简介：{desc[:100]}"
            "───"
            "🔗 播放链接：{video_play_url}"
            "🔗 原链接：{douyin_url}"
            ""
            "=== AcFun ==="
            "curl -s 'https://www.acfun.cn/v/{id}' -H 'User-Agent: Mozilla/5.0'"
            "从HTML提取: <title>取视频名, window.videoInfo JSON取统计"
            "模板:"
            "🎬 AcFun 视频 | {title}"
            "👤 UP主：{up_name}"
            "👁️ 播放：{view}  💬 弹幕：{danmaku}"
            "👍 点赞：{like}  💬 评论：{comment}  ⭐ 收藏：{stow}"
            "───"
            "🔗 播放链接：{video_play_url}"
            "🔗 原链接：{douyin_url}"
            ""
            "=== YouTube ==="
            "curl -s 'https://www.youtube.com/oembed?url={url}&format=json'"
            "模板:"
            "🎬 {title}"
            "👤 {author_name}"
            "───"
            "🔗 播放链接：{video_play_url}"
            "🔗 原链接：{douyin_url}"
            ""
            "=== 其他链接 ==="
            "用 curl -s 抓取 <title> 和内容摘要"
            "模板: 🔗 {title} / 📝 {desc[:200]} / 🔗 {url}"
            ""
            "通用: 不要加引导语/收尾语，只输出模板内容。数据量大的用 python3 -c 处理JSON。"
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
        # Collect all content from streaming SSE chunks, skip tool calls
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

    def _post_stream(self, url: str, payload: dict[str, Any]) -> str:
        import http.client
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if self.settings.hermes_api_key:
            headers["Authorization"] = f"Bearer {self.settings.hermes_api_key}"
        req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                chunks = []
                while True:
                    try:
                        line = resp.readline()
                        if not line:
                            break
                        chunks.append(line.decode("utf-8", "replace"))
                    except http.client.IncompleteRead as e:
                        chunks.append(e.partial.decode("utf-8", "replace"))
                        break
                return "".join(chunks)
        except urllib.error.URLError as e:
            msg = str(e)
            # Sanitize any credentials that might leak in error messages
            msg = re.sub(r'(?i)Bearer\s+[A-Za-z0-9._~+/=-]{12,}', '[BEARER_TOKEN_REDACTED]', msg)
            return json.dumps({"error": msg}, ensure_ascii=False)
