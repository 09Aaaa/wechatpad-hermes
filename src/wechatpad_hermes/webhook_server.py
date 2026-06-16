"""WeChatPadProMAX webhook server for receiving messages via push callbacks."""
from __future__ import annotations

import hmac
import hashlib
import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Callable
from urllib.parse import urlparse, parse_qs

from .messages import ChatMessage, parse_message

log = logging.getLogger("wechatpad_hermes.webhook")


class WebhookHandler(BaseHTTPRequestHandler):
    secret: str = ""
    on_message: Callable[[ChatMessage], None] | None = None
    bot_wxid: str = ""

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/webhook":
            self._respond(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        if length > 2 * 1024 * 1024:
            self._respond(413, {"error": "payload too large"})
            return
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
        except Exception:
            self._respond(400, {"error": "invalid json"})
            return
        if self.secret and not self._verify_signature(payload):
            log.warning("webhook signature mismatch")
            self._respond(403, {"error": "invalid signature"})
            return
        self._respond(200, {"ok": True})
        threading.Thread(target=self._process_payload, args=(payload,), daemon=True).start()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.rstrip("/") not in ("/webhook", "/health"):
            self._respond(404, {"error": "not found"})
            return
        echostr = parse_qs(parsed.query).get("echostr", [None])[0]
        if echostr:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(echostr.encode())
        else:
            self._respond(200, {"status": "ok"})

    def _verify_signature(self, payload: dict[str, Any]) -> bool:
        sig = payload.get("Signature", "")
        if not sig:
            return False
        base = f"{payload.get('Wxid','')}:{payload.get('MessageType','')}:{payload.get('Timestamp',0)}"
        expected = hmac.new(self.secret.encode(), base.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)

    def _process_payload(self, payload: dict[str, Any]) -> None:
        try:
            messages_raw: list[Any] = []
            data = payload.get("Data")
            if isinstance(data, dict) and isinstance(data.get("messages"), list):
                messages_raw = data["messages"]
            elif isinstance(data, list):
                messages_raw = data
            elif isinstance(payload.get("messages"), list):
                messages_raw = payload["messages"]  # type: ignore[assignment]
            elif payload.get("msgType") or payload.get("msgId"):
                messages_raw = [payload]
            for raw_msg in messages_raw:
                if not isinstance(raw_msg, dict):
                    continue
                if raw_msg.get("isSelf", False):
                    continue
                raw = self._to_raw(raw_msg)
                msg = parse_message(raw, bot_wxid=self.bot_wxid)
                if msg and self.on_message:
                    log.info("webhook msg from=%s type=%d", msg.from_wxid[:8], msg.msg_type)
                    self.on_message(msg)
        except Exception as exc:
            log.error("webhook process error: %s: %s", type(exc).__name__, exc)

    def _to_raw(self, wm: dict[str, Any]) -> dict[str, Any]:
        text = wm.get("text", "")
        from_user = wm.get("fromUser", "")
        is_group = from_user.endswith("@chatroom")
        raw: dict[str, Any] = {
            "MsgId": wm.get("msgId"),
            "NewMsgId": wm.get("newMsgId"),
            "FromUserName": {"string": from_user},
            "ToUserName": {"string": wm.get("toUser", "")},
            "Content": {"string": text},
            "MsgType": wm.get("msgType", 0),
            "CreateTime": wm.get("createTime", 0),
            "PushContent": wm.get("pushContent", ""),
        }
        if is_group and text and ":" in text:
            parts = text.split(":", 1)
            if len(parts) == 2 and parts[0].startswith("wxid_"):
                raw["ActualUserName"] = {"string": parts[0]}
        if wm.get("rawContent"):
            raw["Xml"] = {"string": wm["rawContent"]}
        return raw

    def _respond(self, code: int, body: dict[str, Any]) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, format: str, *args: Any) -> None:
        pass


class WebhookServer:
    def __init__(
        self,
        *,
        port: int = 8070,
        host: str = "0.0.0.0",
        secret: str = "",
        bot_wxid: str = "",
        on_message: Callable[[ChatMessage], None] | None = None,
    ) -> None:
        self.port = port
        self.host = host
        self.secret = secret
        self.bot_wxid = bot_wxid
        self.on_message = on_message
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> int:
        WebhookHandler.secret = self.secret
        WebhookHandler.on_message = self.on_message
        WebhookHandler.bot_wxid = self.bot_wxid
        self._server = HTTPServer((self.host, self.port), WebhookHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        log.info("webhook server started on %s:%d", self.host, self.port)
        return self.port

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        log.info("webhook server stopped")