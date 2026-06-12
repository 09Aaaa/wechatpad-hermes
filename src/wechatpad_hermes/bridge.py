from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from typing import Any

from .config import Settings, load_settings
from .hermes_client import HermesClient
from .messages import ChatMessage, extract_messages
from .policy import Policy
from .privacy import PrivacyFilter, mask_secret
from .storage import MessageStore
from .wechatpad_client import WeChatPadClient


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

    def stop(self, *_args: Any) -> None:
        self.running = False

    def run_forever(self) -> None:
        self.log(
            "bridge started: base_url=%s db=%s dry_run=%s send_enabled=%s authcode=%s"
            % (
                self.settings.wechatpad_base_url,
                self.settings.db_path,
                self.settings.dry_run,
                self.settings.send_enabled,
                mask_secret(self.settings.wechatpad_authcode),
            )
        )
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
            time.sleep(self._sleep_seconds())
        self.store.close()
        self.log("bridge stopped")

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
        return handled

    def handle_message(self, message: ChatMessage) -> bool:
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

        send_result = self.wechat.send_text(decision.target_wxid, safe_reply)
        status = "sent" if send_result.get("Success", send_result.get("Code") == 0) else "send_failed"
        self.store.add_reply(message.dedupe_key, message.chat_id, decision.target_wxid, message.is_group, safe_reply, status, json.dumps(send_result, ensure_ascii=False)[:500])
        target_handle = chat_handle if message.is_group else self.store.ensure_chat_handle(decision.target_wxid, False)
        self.log(f"reply {status} chat={chat_handle or 'unknown_chat'} to={target_handle or 'unknown_chat'} len={len(safe_reply)}")
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
