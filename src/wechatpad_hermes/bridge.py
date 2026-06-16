from __future__ import annotations
import time

import argparse
import json
import signal
import sys
import time
from typing import Any

from .config import Settings, load_settings
from .hermes_client import HermesClient
from .media import extract_reply_media, prepare_image_reference
from .messages import ChatMessage, extract_messages
from .policy import Policy
from .privacy import PrivacyFilter, mask_secret
from .storage import MessageStore
from .wechatpad_client import WeChatPadClient


# Simple per-chat rate limiter — skip repeated messages within the cooldown
_RATE_LIMIT_COOLDOWN = 1.5     # seconds: no two messages from the same chat
_RATE_LIMIT_BURST    = 3.0     # seconds: burst window for repeated same-text messages
_last_message: dict[str, tuple[float, str]] = {}
_last_reply_time: dict[str, float] = {}


class Bridge:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.privacy = PrivacyFilter()
        self.store = MessageStore(
            settings.db_path,
            privacy=self.privacy,
            store_raw_messages=settings.store_raw_messages,
            fingerprint_key=settings.wechatpad_authcode or settings.wechatpad_admin_key,
        )
        self.policy = Policy(settings)
        self.wechat = WeChatPadClient(settings)
        self.hermes = HermesClient(settings)
        self.running = True
        self.error_count = 0
        saved_synckey = self.store.get_runtime_value("wechatpad_synckey")
        if saved_synckey:
            self.wechat.set_synckey(saved_synckey)
        self._started_at = int(time.time())
        saved_start = self.store.get_runtime_value("bridge_started_at")
        if not saved_start:
            self.store.set_runtime_value("bridge_started_at", str(self._started_at))

    def stop(self, *_args: Any) -> None:
        self.running = False

    def run_forever(self) -> None:
        mode = "webhook+poll" if self.settings.webhook_enabled else "poll"
        self.log(
            "bridge started: mode=%s base_url=%s db=%s dry_run=%s send_enabled=%s authcode=%s"
            % (
                mode,
                self.settings.wechatpad_base_url,
                self.settings.db_path,
                self.settings.dry_run,
                self.settings.send_enabled,
                mask_secret(self.settings.wechatpad_authcode),
            )
        )

        # Start webhook server if enabled
        webhook_server = None
        if self.settings.webhook_enabled:
            webhook_server = self._start_webhook()

        # Polling loop (always runs as fallback, or as primary if webhook disabled)
        last_cleanup = 0
        while self.running:
            try:
                self.poll_once()
                self.error_count = 0
                now = int(time.time())
                if now - last_cleanup > 3600:
                    deleted = self.store.cleanup(self.settings.retention_days)
                    if deleted:
                        self.log(f"cleanup removed {deleted} old messages")
                    last_cleanup = now
            except KeyboardInterrupt:
                break
            except Exception as exc:
                self.error_count += 1
                self.log(f"poll error: {type(exc).__name__}: {exc}")
            # Slower polling when webhook is primary (webhook handles real-time, poll is fallback)
            sleep_time = self._sleep_seconds()
            if self.settings.webhook_enabled:
                sleep_time = max(sleep_time, 10.0)  # Slow poll as fallback
            time.sleep(sleep_time)

        if webhook_server:
            webhook_server.stop()
        self.store.close()
        self.log("bridge stopped")

    def _start_webhook(self):
        """Start webhook server and register URL with WeChatPadPro."""
        from .webhook_server import WebhookServer

        port = self.settings.webhook_port
        secret = self.settings.webhook_secret
        host = self.settings.webhook_host

        server = WebhookServer(
            port=port,
            host=host,
            secret=secret,
            bot_wxid=self.settings.bot_wxid,
            on_message=self.handle_message,
        )
        server.start()

        # Register webhook URL with WeChatPadPro
        # Determine the callback URL: if bot is on same host, use internal IP
        callback_url = f"http://{host}:{port}/webhook"
        if host == "0.0.0.0":
            # Use the WeChatPadPro host as callback target (same machine)
            wechatpad_host = self.settings.wechatpad_base_url.split("//")[1].split(":")[0].split("/")[0]
            callback_url = f"http://{wechatpad_host}:{port}/webhook"

        try:
            result = self.wechat.set_webhook(
                url=callback_url,
                enabled=True,
                secret=secret,
                message_types=["sync_message"],
                timeout=5,
                retry_count=3,
            )
            self.log(f"webhook registered: url={callback_url} result={json.dumps(result, ensure_ascii=False)[:200]}")
        except Exception as exc:
            self.log(f"webhook registration failed: {type(exc).__name__}: {exc}")
            self.log("falling back to poll-only mode")

        return server

    def poll_once(self) -> int:
        payload = self.wechat.sync_messages()
        synckey = self.wechat.get_synckey()
        if synckey:
            self.store.set_runtime_value("wechatpad_synckey", synckey)
        messages = extract_messages(payload, bot_wxid=self.settings.bot_wxid)
        handled = 0
        for message in messages:
            if self.handle_message(message):
                handled += 1
        now = int(time.time())
        self.store.set_runtime_value("last_poll_at", str(now))
        self.store.set_runtime_value("last_poll_message_count", str(len(messages)))
        self.store.set_runtime_value("last_poll_handled_count", str(handled))
        return handled

    def handle_message(self, message: ChatMessage) -> bool:
        # -- Rate limiting --------------------------------------------------
        global _last_message, _last_reply_time
        chat_id = message.chat_id
        now_mono = time.monotonic()
        content_snippet = (message.content or "")[:200]

        # Cooldown: skip if we replied to this chat recently
        if chat_id in _last_reply_time:
            elapsed = now_mono - _last_reply_time[chat_id]
            if elapsed < _RATE_LIMIT_COOLDOWN:
                self.log(f"rate_limited chat={chat_id[:16]} cooldown={elapsed:.1f}s")
                return False

        # Dedup: skip duplicate content within burst window
        if chat_id in _last_message:
            ts, prev = _last_message[chat_id]
            if content_snippet and content_snippet == prev and (now_mono - ts) < _RATE_LIMIT_BURST:
                self.log(f"rate_limited chat={chat_id[:16]} dedup burst_window={now_mono - ts:.1f}s")
                return False
        _last_message[chat_id] = (now_mono, content_snippet)

        # -- History gate ---------------------------------------------------
        if self._started_at and message.create_time:
            if message.create_time < self._started_at - 7200:
                self.log("message chat=historical reason=historical_before_bridge")
                return True

        # -- Policy decision ------------------------------------------------
        decision = self.policy.decide(message)
        if decision.store_message:
            inserted = self.store.add_message(message)
            if not inserted:
                return False
            chat_handle = self.store.ensure_chat_handle(message.chat_id, message.is_group)
            from_handle = self.store.ensure_participant_handle(message.chat_id, message.from_wxid)
            sender_handle = self.store.ensure_participant_handle(message.chat_id, message.sender_wxid)
            log_text = self.privacy.redact(message.content[:160]).text
        else:
            inserted = self.store.add_ignored_message(message, decision.reason)
            if not inserted:
                return False
            chat_handle = "blocked_or_ignored"
            from_handle = "blocked_or_ignored"
            sender_handle = "-"
            log_text = "[not stored as context]"
        self.log(
            "message chat=%s from=%s sender=%s group=%s decision=%s reason=%s text=%s"
            % (
                chat_handle or "unknown_chat",
                from_handle or "unknown_participant",
                sender_handle or "-",
                message.is_group,
                decision.should_respond,
                decision.reason,
                log_text,
            )
        )
        if not decision.should_respond:
            return decision.store_message

        context = self.build_context(message, decision.context_since_ts)
        safe_conversation_id = f"wechat:{'group' if message.is_group else 'private'}:{chat_handle}"
        inbound = self.privacy.redact(message.content).text
        try:
            reply = self.hermes.complete(
                conversation_id=safe_conversation_id,
                user_text=inbound,
                context=context,
                role=decision.role,
            )
        except Exception as exc:
            reply = f"Hermes call failed: {type(exc).__name__}"
            self.store.add_reply(message.dedupe_key, message.chat_id, decision.target_wxid, message.is_group, reply, "hermes_error", str(exc))
            self.log(reply)
            return True

        ok, safe_reply, hits = self.privacy.safe_for_group_reply(reply) if message.is_group else self.privacy.safe_for_private_reply(reply)
        if not ok:
            blocked = "Reply blocked because it may contain private information."
            self.store.add_reply(message.dedupe_key, message.chat_id, decision.target_wxid, message.is_group, safe_reply, "blocked", ",".join(hits))
            self.log(f"reply blocked hits={hits} chat={chat_handle or 'unknown_chat'}")
            self.wechat.send_text(decision.target_wxid, blocked)
            return True

        text_reply, image_refs = extract_reply_media(safe_reply)
        send_result = {"Code": 0, "Success": True, "Message": "no text reply"}
        if text_reply:
            send_result = self.wechat.send_text(decision.target_wxid, text_reply)
        status = "sent" if send_result.get("Success", send_result.get("Code") == 0) else "send_failed"
        image_statuses: list[str] = []
        for image_ref in image_refs:
            try:
                prepared_ref = prepare_image_reference(image_ref)
                image_result = self.wechat.send_image(decision.target_wxid, prepared_ref)
                image_status = "image_sent" if image_result.get("Success", image_result.get("Code") == 0) else "image_send_failed"
            except Exception as exc:
                image_status = f"image_error:{type(exc).__name__}"
            image_statuses.append(image_status)
        if image_statuses and status == "sent" and any(item != "image_sent" for item in image_statuses):
            status = "partial_sent"
        elif image_statuses and not text_reply and all(item == "image_sent" for item in image_statuses):
            status = "sent"
        self.store.add_reply(message.dedupe_key, message.chat_id, decision.target_wxid, message.is_group, safe_reply, status, json.dumps({"text": send_result, "images": image_statuses}, ensure_ascii=False)[:500])
        target_handle = chat_handle if message.is_group else self.store.ensure_chat_handle(decision.target_wxid, False)
        self.log(f"reply {status} chat={chat_handle or 'unknown_chat'} to={target_handle or 'unknown_chat'} len={len(text_reply)} images={len(image_refs)} image_statuses={image_statuses}")

        # Track reply time for rate limiter
        _last_reply_time[chat_id] = time.monotonic()
        return True

    def build_context(self, message: ChatMessage, since_ts: int) -> list[dict[str, Any]]:
        token = self.store.issue_context_token(
            message.chat_id,
            message.is_group,
            ttl_seconds=self.settings.context_token_ttl_seconds,
        )
        chat_handle = str(token["chat_handle"])
        context_meta: dict[str, Any] = {
            "context_scope": "current_chat",
            "chat_handle": chat_handle,
            "target_handle": chat_handle,
            "context_token": token["context_token"],
            "context_token_expires_at": token["expires_at"],
            "is_group": message.is_group,
            "conversation_kind": "group" if message.is_group else "private",
        }
        same_chat = self.store.recent_messages(
            message.chat_id,
            since_ts=since_ts,
            limit=self.policy.max_messages,
            max_chars=self.policy.max_chars,
        )
        for item in same_chat:
            item["context_scope"] = "same_chat_recent"
        if not message.is_group or not message.sender_wxid:
            return [context_meta] + same_chat

        sender_recent = self.store.recent_messages(
            message.chat_id,
            since_ts=since_ts,
            limit=max(10, self.policy.max_messages // 2),
            max_chars=max(2000, self.policy.max_chars // 2),
            sender_wxid=message.sender_wxid,
        )
        seen = {_context_key(item) for item in same_chat}
        extra: list[dict[str, Any]] = []
        for item in sender_recent:
            if _context_key(item) in seen:
                continue
            item["context_scope"] = "same_group_same_sender_recent"
            extra.append(item)
        return [context_meta] + same_chat + extra

    def log(self, text: str) -> None:
        print(self.privacy.redact(text).text, flush=True)

    def _sleep_seconds(self) -> float:
        base = max(self.settings.poll_interval, 0.2)
        if self.error_count <= 0:
            return base
        return min(max(base, 1.0) * (2 ** min(self.error_count - 1, 5)), 30.0)


def _context_key(item: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
    return (
        item.get("create_time"),
        item.get("from_wxid"),
        item.get("sender_wxid"),
        item.get("content_redacted"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Safe WeChatPadProMAX to Hermes bridge")
    parser.add_argument("--once", action="store_true", help="Poll once and exit")
    parser.add_argument("--status", action="store_true", help="Print WeChatPad online status and exit")
    args = parser.parse_args()
    settings = load_settings()
    bridge = Bridge(settings)
    signal.signal(signal.SIGINT, bridge.stop)
    signal.signal(signal.SIGTERM, bridge.stop)
    if args.status:
        print(bridge.privacy.redact(json.dumps(bridge.wechat.get_online_info(), ensure_ascii=False, indent=2)).text)
        bridge.store.close()
        return
    if args.once:
        count = bridge.poll_once()
        print(json.dumps({"handled": count}, ensure_ascii=False))
        bridge.store.close()
        return
    bridge.run_forever()


if __name__ == "__main__":
    main()
