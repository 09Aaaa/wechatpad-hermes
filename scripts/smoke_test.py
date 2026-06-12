from __future__ import annotations

import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wechatpad_hermes.bridge import Bridge
from wechatpad_hermes.config import load_settings
from wechatpad_hermes.doctor import build_report, check_settings
from wechatpad_hermes.hermes_client import HermesClient
from wechatpad_hermes.messages import parse_message
from wechatpad_hermes.policy import Policy
from wechatpad_hermes.privacy import PrivacyFilter, mask_secret
from wechatpad_hermes.storage import MessageStore


def assert_true(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def sample_wxid(label: str) -> str:
    return "wxid_" + label


def sample_chatroom(label: str = "123") -> str:
    return label + "@chatroom"


def sample_authcode() -> str:
    return "-".join(["00000000", "1111", "2222", "3333", "444444444444"])


def main() -> None:
    env_keys = [
        "WECHATPAD_DB_PATH",
        "WECHATPAD_POLICY_PATH",
        "WECHATPAD_BOT_WXID",
        "WECHATPAD_BOT_NAMES",
        "WECHATPAD_DRY_RUN",
        "WECHATPAD_ENV_FILE",
        "WECHATPAD_BLOCKED_WXIDS",
        "WECHATPAD_BLOCKED_GROUP_CHATROOMS",
        "WECHATPAD_ADMIN_TOOLS_ENABLED",
        "WECHATPAD_ADMIN_KEY",
        "WECHATPAD_OWNER_WXIDS",
        "WECHATPAD_STORE_RAW_MESSAGES",
    ]
    original_env = {key: os.environ.get(key) for key in env_keys}
    tmp_dir = Path(os.environ.get("TEMP") or os.environ.get("TMP") or ".")
    db_path = tmp_dir / f"wechatpad-hermes-smoke-{uuid.uuid4().hex}.sqlite3"
    env_file: Path | None = None
    store = None
    mcp_server = None
    try:
        os.environ["WECHATPAD_DB_PATH"] = str(db_path)
        bot_wxid = sample_wxid("bot_test")
        user_a_wxid = sample_wxid("user_a")
        user_private_leak_wxid = sample_wxid("user_private_leak")
        sender_a_wxid = sample_wxid("sender_a")
        blocked_sender_wxid = sample_wxid("blocked_sender")
        someone_wxid = sample_wxid("someone")
        non_owner_admin_wxid = sample_wxid("non_owner_admin")
        owner_admin_wxid = sample_wxid("owner_admin")
        group_chatroom = sample_chatroom()

        os.environ["WECHATPAD_BOT_WXID"] = bot_wxid
        os.environ["WECHATPAD_BOT_NAMES"] = "BOT,robot,机器人"
        os.environ["WECHATPAD_DRY_RUN"] = "true"
        os.environ["WECHATPAD_ENV_FILE"] = ""
        os.environ["WECHATPAD_POLICY_PATH"] = ""
        settings = load_settings()
        assert_true(settings.dry_run, "dry-run should be enabled in smoke")
        assert_true(not settings.admin_tools_enabled, "admin tools should be disabled by default")
        assert_true(not settings.allow_unknown_outbound, "unknown outbound should be disabled by default")
        assert_true(not settings.store_raw_messages, "raw message storage should be disabled by default")
        issues = check_settings(settings, strict=False)
        assert_true(not [issue for issue in issues if issue.level == "error"], "non-strict doctor settings should have no errors")
        public_safe_issues = check_settings(settings, strict=False, require_public_safe=True)
        assert_true(not [issue for issue in public_safe_issues if issue.level == "error"], "public-safe doctor settings should pass with dry-run defaults")
        report = build_report(settings, strict=False, live=False)
        assert_true(report["settings"]["wechatpad_authcode"] == "", "doctor report should not invent authcode")
        assert_true("db_private_mode" in report["local_state"], "doctor local state should report db file privacy mode")

        unsafe_settings = replace(settings, send_enabled=True, dry_run=False, admin_tools_enabled=True, allow_unknown_outbound=True, store_raw_messages=True)
        unsafe_codes = {issue.code for issue in check_settings(unsafe_settings, strict=False, require_public_safe=True) if issue.level == "error"}
        assert_true(
            {
                "public_safe_send_enabled",
                "public_safe_dry_run_disabled",
                "public_safe_admin_tools_enabled",
                "public_safe_unknown_outbound_enabled",
                "public_safe_raw_storage_enabled",
            }.issubset(unsafe_codes),
            "public-safe doctor settings should fail when dry-run safety switches are disabled",
        )

        os.environ["WECHATPAD_ADMIN_TOOLS_ENABLED"] = "true"
        os.environ["WECHATPAD_ADMIN_KEY"] = "test-admin-key"
        os.environ["WECHATPAD_OWNER_WXIDS"] = ""
        admin_issues = check_settings(load_settings(), strict=False)
        assert_true(
            any(issue.code == "admin_tools_without_owner_wxids" for issue in admin_issues),
            "admin tools should require owner wxids for owner-context checks",
        )
        os.environ["WECHATPAD_ADMIN_TOOLS_ENABLED"] = "false"
        os.environ["WECHATPAD_ADMIN_KEY"] = ""
        os.environ["WECHATPAD_OWNER_WXIDS"] = ""

        policy = Policy(settings)
        privacy = PrivacyFilter()
        assert_true(mask_secret(sample_authcode()) == "[SECRET_CONFIGURED]", "secret masks should not reveal stable prefixes or suffixes")
        store = MessageStore(settings.db_path, privacy=privacy)
        now = int(time.time())

        private_msg = parse_message(
            {"MsgId": 1, "NewMsgId": 11, "FromUserName": user_a_wxid, "ToUserName": bot_wxid, "Content": "hello", "MsgType": 1, "CreateTime": now},
            bot_wxid=settings.bot_wxid,
        )
        assert_true(private_msg is not None, "private message should parse")
        assert_true(store.add_message(private_msg), "private message should insert")
        raw_row = store.conn.execute("SELECT raw_json FROM messages WHERE dedupe_key = ?", (private_msg.dedupe_key,)).fetchone()
        assert_true(raw_row is not None and raw_row["raw_json"] is None, "raw WeChatPad payloads should not be stored by default")
        private_decision = policy.decide(private_msg)
        assert_true(private_decision.should_respond, "private message should trigger response")
        assert_true(private_decision.conversation_id == "wechat:private", "policy conversation id should not expose raw private wxid")

        private_leak_msg = parse_message(
            {"MsgId": 8, "NewMsgId": 18, "FromUserName": user_private_leak_wxid, "ToUserName": bot_wxid, "Content": "hello", "MsgType": 1, "CreateTime": now},
            bot_wxid=settings.bot_wxid,
        )
        assert_true(private_leak_msg is not None, "private leak test message should parse")
        private_bridge = Bridge(settings)
        try:
            sent_private: dict[str, object] = {}
            private_bridge.hermes.complete = lambda **_kwargs: "服务器密码 abc123"  # type: ignore[method-assign]
            private_bridge.wechat.send_text = lambda to_wxid, content, at="": sent_private.update({"to_wxid": to_wxid, "content": content, "at": at}) or {"Code": 0, "Success": True}  # type: ignore[method-assign]
            private_bridge_log = StringIO()
            with redirect_stdout(private_bridge_log):
                handled_private_leak = private_bridge.handle_message(private_leak_msg)
            assert_true(handled_private_leak, "bridge should handle private messages even when model reply is blocked")
            assert_true(sent_private.get("to_wxid") == user_private_leak_wxid, "blocked private replies should notify only the current private chat")
            assert_true("private information" in str(sent_private.get("content")), "blocked private replies should send only a safe placeholder")
            assert_true("abc123" not in str(sent_private) and "服务器密码" not in str(sent_private), "blocked private replies should not send sensitive content")
            blocked_reply_row = private_bridge.store.conn.execute("SELECT status, reason FROM replies WHERE trigger_dedupe_key = ?", (private_leak_msg.dedupe_key,)).fetchone()
            assert_true(blocked_reply_row is not None and blocked_reply_row["status"] == "blocked", "blocked private replies should be recorded as blocked")
        finally:
            private_bridge.store.close()

        group_msg = parse_message(
            {"MsgId": 2, "NewMsgId": 12, "FromUserName": group_chatroom, "ToUserName": bot_wxid, "ActualUserName": sender_a_wxid, "Content": "ordinary group chat", "MsgType": 1, "CreateTime": now},
            bot_wxid=settings.bot_wxid,
        )
        assert_true(group_msg is not None, "group message should parse")
        assert_true(group_msg.sender_wxid == sender_a_wxid, "group ActualUserName should become sender_wxid")
        assert_true(store.add_message(group_msg), "group message should insert")
        group_decision = policy.decide(group_msg)
        assert_true(not group_decision.should_respond, "group message without mention should not trigger")

        os.environ["WECHATPAD_BLOCKED_WXIDS"] = sender_a_wxid
        blocked_policy = Policy(load_settings())
        assert_true(
            blocked_policy.decide(group_msg).reason == "blocked_group_sender",
            "blocked wxids should apply to group senders as well as private senders",
        )
        assert_true(
            not blocked_policy.decide(group_msg).store_message,
            "blocked group senders should not be stored as future context",
        )
        os.environ["WECHATPAD_BLOCKED_WXIDS"] = ""

        os.environ["WECHATPAD_BLOCKED_GROUP_CHATROOMS"] = group_chatroom
        blocked_group_policy = Policy(load_settings())
        assert_true(
            blocked_group_policy.decide(group_msg).reason == "group_chatroom_blocked",
            "blocked group chatrooms should be configurable from env",
        )
        assert_true(
            not blocked_group_policy.decide(group_msg).store_message,
            "blocked groups should not be stored as future context",
        )
        os.environ["WECHATPAD_BLOCKED_GROUP_CHATROOMS"] = ""
        policy = Policy(load_settings())

        mention_msg = parse_message(
            {"MsgId": 3, "NewMsgId": 13, "FromUserName": group_chatroom, "ToUserName": bot_wxid, "Content": f"{sender_a_wxid}:\n@BOT summarize this", "MsgType": 1, "CreateTime": now},
            bot_wxid=settings.bot_wxid,
        )
        assert_true(mention_msg is not None, "mention message should parse")
        assert_true(mention_msg.sender_wxid == sender_a_wxid, "group content prefix should become sender_wxid")
        assert_true(mention_msg.content == "@BOT summarize this", "group content prefix should be stripped")
        assert_true(store.add_message(mention_msg), "mention message should insert")
        mention_decision = policy.decide(mention_msg)
        assert_true(mention_decision.should_respond, "group mention should trigger")
        assert_true(mention_decision.conversation_id == "wechat:group", "policy conversation id should not expose raw chatroom")
        fullwidth_mention_msg = parse_message(
            {"MsgId": 4, "NewMsgId": 14, "FromUserName": group_chatroom, "ToUserName": bot_wxid, "ActualUserName": sender_a_wxid, "Content": "＠BOT summarize this", "MsgType": 1, "CreateTime": now},
            bot_wxid=settings.bot_wxid,
        )
        assert_true(fullwidth_mention_msg is not None, "full-width mention message should parse")
        assert_true(policy.decide(fullwidth_mention_msg).should_respond, "full-width group mention should trigger")
        spaced_fullwidth_msg = parse_message(
            {"MsgId": 5, "NewMsgId": 15, "FromUserName": group_chatroom, "ToUserName": bot_wxid, "ActualUserName": sender_a_wxid, "Content": "＠ BOT summarize this", "MsgType": 1, "CreateTime": now},
            bot_wxid=settings.bot_wxid,
        )
        assert_true(spaced_fullwidth_msg is not None, "spaced full-width mention message should parse")
        assert_true(policy.decide(spaced_fullwidth_msg).should_respond, "spaced full-width group mention should trigger")
        chinese_alias_msg = parse_message(
            {"MsgId": 6, "NewMsgId": 16, "FromUserName": group_chatroom, "ToUserName": bot_wxid, "ActualUserName": sender_a_wxid, "Content": "@机器人帮我看下", "MsgType": 1, "CreateTime": now},
            bot_wxid=settings.bot_wxid,
        )
        assert_true(chinese_alias_msg is not None, "Chinese alias mention message should parse")
        assert_true(policy.decide(chinese_alias_msg).should_respond, "Chinese alias group mention should trigger even when followed by Chinese text")
        false_mention_msg = parse_message(
            {"MsgId": 7, "NewMsgId": 17, "FromUserName": group_chatroom, "ToUserName": bot_wxid, "ActualUserName": sender_a_wxid, "Content": "@BOTany should not wake", "MsgType": 1, "CreateTime": now},
            bot_wxid=settings.bot_wxid,
        )
        assert_true(false_mention_msg is not None, "false mention message should parse")
        assert_true(not policy.decide(false_mention_msg).should_respond, "mention matching should avoid prefix false positives")
        assert_true(policy.decide(false_mention_msg).store_message, "ordinary group text without a valid mention should still be stored as context")

        blocked_bridge_msg = parse_message(
            {"MsgId": 9, "NewMsgId": 19, "FromUserName": group_chatroom, "ToUserName": bot_wxid, "ActualUserName": blocked_sender_wxid, "Content": "@BOT should not be stored", "MsgType": 1, "CreateTime": now},
            bot_wxid=settings.bot_wxid,
        )
        assert_true(blocked_bridge_msg is not None, "blocked bridge test message should parse")
        blocked_bridge = Bridge(replace(settings, blocked_wxids=[blocked_sender_wxid]))
        try:
            bridge_log = StringIO()
            with redirect_stdout(bridge_log):
                handled_blocked = blocked_bridge.handle_message(blocked_bridge_msg)
            assert_true(not handled_blocked, "bridge should not count blocked messages as handled context")
            assert_true(blocked_sender_wxid not in bridge_log.getvalue(), "bridge logs should not expose blocked raw sender ids")
            assert_true("should not be stored" not in bridge_log.getvalue(), "bridge logs should not echo blocked message content")
            blocked_row = store.conn.execute("SELECT 1 FROM messages WHERE dedupe_key = ?", (blocked_bridge_msg.dedupe_key,)).fetchone()
            assert_true(blocked_row is None, "bridge should not store blocked sender messages")
            ignored_row = store.conn.execute("SELECT reason, seen_count FROM ignored_messages").fetchone()
            assert_true(ignored_row is not None and ignored_row["reason"] == "blocked_group_sender", "bridge should persist ignored-message dedupe metadata")
            ignored_schema = [row[1] for row in store.conn.execute("PRAGMA table_info(ignored_messages)").fetchall()]
            assert_true("content" not in ignored_schema and "chat_id" not in ignored_schema and "from_wxid" not in ignored_schema, "ignored-message dedupe table should not store raw content or ids")
            duplicate_log = StringIO()
            with redirect_stdout(duplicate_log):
                handled_duplicate_blocked = blocked_bridge.handle_message(blocked_bridge_msg)
            assert_true(not handled_duplicate_blocked, "duplicate blocked messages should remain unhandled context")
            assert_true(duplicate_log.getvalue() == "", "duplicate blocked messages should not be logged repeatedly")
            ignored_row = store.conn.execute("SELECT seen_count FROM ignored_messages").fetchone()
            assert_true(ignored_row is not None and ignored_row["seen_count"] == 2, "ignored-message dedupe should count repeated blocked messages")
        finally:
            blocked_bridge.store.close()

        context = store.recent_messages(group_chatroom, since_ts=now - 86400, limit=10, max_chars=1000, safe_view=False)
        assert_true(len(context) == 2, "group context should include only same chat messages")
        sender_context = store.recent_messages(group_chatroom, since_ts=now - 86400, limit=10, max_chars=1000, sender_wxid=sender_a_wxid, safe_view=False)
        assert_true(len(sender_context) == 2, "sender context should include only same sender in same chat")
        safe_context = store.recent_messages("123@chatroom", since_ts=now - 86400, limit=10, max_chars=1000)
        safe_json = str(safe_context)
        assert_true(sender_a_wxid not in safe_json and group_chatroom not in safe_json, "safe context should not expose raw wxid/chatroom")
        group_chat_handle = safe_context[0]["chat_handle"]
        sender_handle = safe_context[0]["sender_participant_handle"]
        assert_true(str(group_chat_handle).startswith("chat_"), "safe context should expose only opaque chat handles")
        assert_true(str(sender_handle).startswith("participant_"), "safe context should expose only opaque sender handles")
        assert_true(store.resolve_chat_handle(group_chat_handle)["chat_id"] == group_chatroom, "chat handle should resolve locally")
        token_info = store.issue_context_token("123@chatroom", True, ttl_seconds=300)
        context_token = token_info["context_token"]
        assert_true(str(context_token).startswith("ctx_"), "context token should be opaque")
        assert_true(store.validate_context_token(group_chat_handle, context_token)[1] == "", "context token should validate for its chat")
        assert_true(
            store.validate_context_token(group_chat_handle, "wrong-token")[1] == "unauthorized_context_token",
            "wrong context token should fail",
        )
        assert_true(
            store.resolve_participant_handle(sender_handle, chat_id=group_chatroom) == sender_a_wxid,
            "sender handle should resolve only within the same chat",
        )
        store.add_reply("reply-test", group_chatroom, group_chatroom, True, "ok", "sent", f'{{"ToWxid":"{group_chatroom}","Content":"{sender_a_wxid}"}}')
        reply_row = store.conn.execute("SELECT reason FROM replies WHERE trigger_dedupe_key = ?", ("reply-test",)).fetchone()
        assert_true(
            group_chatroom not in str(reply_row["reason"]) and sender_a_wxid not in str(reply_row["reason"]),
            "stored reply reasons should be redacted",
        )

        ok, safe_text, hits = privacy.safe_for_group_reply(f"服务器密码 abc123，授权码 {sample_authcode()}")
        assert_true(not ok, "sensitive group reply should be blocked")
        assert_true("PASSWORD_REDACTED" in safe_text and "AUTHCODE_REDACTED" in safe_text, "sensitive reply should be redacted")
        assert_true(bool(hits), "sensitive hits should be reported")
        ok, safe_text, hits = privacy.safe_for_private_reply("这里是后台 token abcdefghijklmnop")
        assert_true(not ok and "API_KEY_REDACTED" in safe_text and bool(hits), "sensitive private replies should also be blocked")
        ok, _safe_text, hits = privacy.safe_for_group_reply("这是私聊里说过的后台信息")
        assert_true(not ok and bool(hits), "group reply should block Chinese leak phrases even without explicit credentials")
        ok, safe_text, hits = privacy.safe_for_group_reply("use ctx_abcdefghijklmnopqrstuvwxyz123456 and chat_abcdefghijklmnopqrstuvwxyz")
        assert_true(not ok, "group reply should block opaque tool credentials")
        assert_true("CONTEXT_TOKEN_REDACTED" in safe_text and "OPAQUE_HANDLE_REDACTED" in safe_text, "opaque tool credentials should be redacted")

        captured_payload: dict[str, object] = {}
        hermes = HermesClient(settings)
        hermes._post_json = lambda _url, payload: captured_payload.update(payload) or {"choices": [{"message": {"content": "ok"}}]}
        assert_true(hermes.complete(conversation_id="wechat:group:chat_safe", user_text="hi", context=safe_context, role="group") == "ok", "Hermes test call should return mocked content")
        prompt_text = str(captured_payload)
        assert_true("你是 Hermes 微信机器人" in prompt_text and "最近上下文" in prompt_text, "Hermes system prompt should be readable UTF-8 Chinese")
        assert_true(group_chatroom not in prompt_text and sender_a_wxid not in prompt_text, "Hermes payload should not include raw group ids from safe context")

        store.close()
        store = None

        default_db = ROOT / "wechatpad-hermes.sqlite3"
        default_db_existed = default_db.exists()
        import wechatpad_hermes.mcp_server as mcp_server

        assert_true(type(mcp_server.mcp).__name__ == "FastMCP", "MCP server should import")
        assert_true(default_db.exists() == default_db_existed, "MCP import should not create default db")
        health_result = mcp_server.wechat_bridge_health()
        assert_true("message_count" in health_result and "dry_run" in health_result, "MCP health should expose safe aggregate state")
        assert_true("ignored_message_count" in health_result, "MCP health should include ignored-message dedupe counters")
        assert_true(group_chatroom not in health_result and sender_a_wxid not in health_result, "MCP health should not expose raw ids")
        blocked_send = mcp_server.wechat_send_text(sample_wxid("unknown_target"), context_token, "hello")
        assert_true("unknown_context_handle" in blocked_send, "MCP send should block raw wxid targets by default")
        stale_user_wxid = sample_wxid("stale_user")
        stale_private_handle = mcp_server._store().ensure_chat_handle(stale_user_wxid, False)
        stale_token = mcp_server._store().issue_context_token(stale_user_wxid, False, ttl_seconds=300)["context_token"]
        stale_send = mcp_server.wechat_send_text(stale_private_handle, stale_token, "hello")
        assert_true("stale_context_handle" in stale_send, "MCP send should block handles without stored messages")
        stale_reply_row = mcp_server._store().conn.execute("SELECT status, reason FROM replies WHERE status = 'blocked' AND reason LIKE '%stale_context_handle%' ORDER BY id DESC LIMIT 1").fetchone()
        assert_true(stale_reply_row is not None, "blocked stale MCP sends should be stored as redacted audit records")
        private_handle = mcp_server._store().ensure_chat_handle(user_a_wxid, False)
        private_token = mcp_server._store().issue_context_token(user_a_wxid, False, ttl_seconds=300)["context_token"]
        blocked_private_reply = mcp_server.wechat_send_text(private_handle, private_token, "服务器密码 abc123")
        assert_true("sensitive_private_reply" in blocked_private_reply, "MCP private send should block sensitive replies")
        blocked_mcp_row = mcp_server._store().conn.execute("SELECT reply_text_redacted, status, reason FROM replies WHERE status = 'blocked' AND reason LIKE '%sensitive_private_reply%' ORDER BY id DESC LIMIT 1").fetchone()
        assert_true(blocked_mcp_row is not None, "blocked sensitive MCP sends should be stored as redacted audit records")
        assert_true("PASSWORD_REDACTED" in blocked_mcp_row["reply_text_redacted"], "MCP send audit records should redact sensitive text")
        missing_token_result = mcp_server.wechat_get_recent_messages(group_chat_handle, "")
        assert_true("missing_context_token" in missing_token_result, "MCP recent messages should require a context token")
        wrong_token_result = mcp_server.wechat_get_recent_messages(group_chat_handle, "wrong-token")
        assert_true("unauthorized_context_token" in wrong_token_result, "MCP recent messages should reject wrong context tokens")
        recent_result = mcp_server.wechat_get_recent_messages(group_chat_handle, context_token, sender_handle=sender_handle)
        assert_true("ordinary group chat" in recent_result, "MCP recent messages should read by opaque chat handle")
        assert_true(group_chatroom not in recent_result and sender_a_wxid not in recent_result, "MCP recent output should not expose raw ids")
        with ThreadPoolExecutor(max_workers=4) as executor:
            parallel_results = list(executor.map(lambda _i: mcp_server.wechat_get_recent_messages(group_chat_handle, context_token), range(8)))
        assert_true(all("ordinary group chat" in item for item in parallel_results), "MCP storage should tolerate parallel recent-message reads")
        negative_limit_result = mcp_server.wechat_get_recent_messages(group_chat_handle, context_token, days=999, limit=-1)
        assert_true(negative_limit_result.count("content_redacted") == 1, "MCP recent messages should clamp negative limits instead of reading everything")
        negative_search_result = mcp_server.wechat_search_messages(group_chat_handle, context_token, "group", limit=-1)
        assert_true(negative_search_result.count("content_redacted") == 1, "MCP search should clamp negative limits instead of reading everything")
        raw_chat_result = mcp_server.wechat_get_recent_messages(group_chatroom, context_token)
        assert_true("unknown_context_handle" in raw_chat_result, "MCP recent messages should reject raw chat ids")
        blocked_group_reply = mcp_server.wechat_send_text(group_chat_handle, context_token, "服务器密码 abc123")
        assert_true("sensitive_group_reply" in blocked_group_reply, "MCP group send should block sensitive replies")
        before_reply_count = mcp_server._store().runtime_stats()["reply_count"]
        mcp_server._wechat().send_text = lambda _to_wxid, content, at="": {"Code": 0, "Success": True, "Message": "dry-run: not sent", "Data": {"Target": "redacted", "ContentLength": len(content)}}  # type: ignore[method-assign]
        dry_run_group_send = mcp_server.wechat_send_text(group_chat_handle, context_token, "safe group ping")
        after_reply_count = mcp_server._store().runtime_stats()["reply_count"]
        assert_true("dry-run" in dry_run_group_send and after_reply_count == before_reply_count + 1, "successful dry-run MCP sends should be audited")
        dry_run_mcp_row = mcp_server._store().conn.execute("SELECT reply_text_redacted, status, reason FROM replies ORDER BY id DESC LIMIT 1").fetchone()
        assert_true(dry_run_mcp_row is not None and dry_run_mcp_row["status"] == "dry_run", "MCP dry-run send audit should record dry_run status")
        dry_run_audit = str(dict(dry_run_mcp_row))
        assert_true(group_chatroom not in dry_run_audit and sender_a_wxid not in dry_run_audit, "MCP send audit rows should not expose raw ids in text fields")
        online_without_owner = mcp_server.wechat_get_online_info()
        assert_true("owner_context_required" in online_without_owner, "MCP online status should require owner context")
        cache_without_owner = mcp_server.wechat_get_cache_info()
        assert_true("owner_context_required" in cache_without_owner, "MCP cache status should require owner context")
        admin_result = mcp_server.wechat_get_all_online()
        assert_true("admin_tools_disabled" in admin_result, "admin MCP tools should be disabled by default")

        mcp_server._settings.cache_clear()
        mcp_server._policy.cache_clear()
        mcp_server._wechat.cache_clear()
        os.environ["WECHATPAD_ADMIN_TOOLS_ENABLED"] = "true"
        os.environ["WECHATPAD_ADMIN_KEY"] = "test-admin-key"
        os.environ["WECHATPAD_OWNER_WXIDS"] = ""
        missing_context_admin_result = mcp_server.wechat_get_all_online()
        assert_true("owner_context_required" in missing_context_admin_result, "admin MCP tools should require owner context")
        missing_owner_handle = mcp_server._store().ensure_chat_handle(non_owner_admin_wxid, False)
        missing_owner_token = mcp_server._store().issue_context_token(non_owner_admin_wxid, False, ttl_seconds=300)["context_token"]
        missing_owner_admin_result = mcp_server.wechat_get_all_online(missing_owner_handle, missing_owner_token)
        assert_true("owner_wxids_not_configured" in missing_owner_admin_result, "admin MCP tools should require configured owner wxids")
        group_admin_result = mcp_server.wechat_get_all_online(group_chat_handle, context_token)
        assert_true("owner_private_context_required" in group_admin_result, "group context should not authorize admin MCP tools")
        os.environ["WECHATPAD_OWNER_WXIDS"] = owner_admin_wxid
        mcp_server._settings.cache_clear()
        mcp_server._policy.cache_clear()
        mcp_server._wechat.cache_clear()
        non_owner_admin_result = mcp_server.wechat_get_all_online(missing_owner_handle, missing_owner_token)
        assert_true("owner_private_context_required" in non_owner_admin_result, "non-owner private context should not authorize admin MCP tools")
        owner_handle = mcp_server._store().ensure_chat_handle(owner_admin_wxid, False)
        owner_token = mcp_server._store().issue_context_token(owner_admin_wxid, False, ttl_seconds=300)["context_token"]
        mcp_server._wechat().get_online_info = lambda: {
            "Code": 0,
            "Success": True,
            "Data": {"authcode": sample_authcode(), "wxid": owner_admin_wxid},
        }
        mcp_server._wechat().get_cache_info = lambda: {
            "Code": 0,
            "Success": True,
            "Data": {"UserName": owner_admin_wxid, "CacheKey": sample_authcode()},
        }
        mcp_server._wechat().get_all_online = lambda: {
            "Code": 0,
            "Success": True,
            "Data": {"authcode": sample_authcode(), "wxid": owner_admin_wxid},
        }
        owner_online_result = mcp_server.wechat_get_online_info(owner_handle, owner_token)
        assert_true("AUTHCODE_REDACTED" in owner_online_result, "owner online output should redact authcodes")
        assert_true(owner_admin_wxid not in owner_online_result, "owner online output should redact raw wxids")
        owner_cache_result = mcp_server.wechat_get_cache_info(owner_handle, owner_token)
        assert_true("AUTHCODE_REDACTED" in owner_cache_result, "owner cache output should redact cache authcodes")
        assert_true(owner_admin_wxid not in owner_cache_result, "owner cache output should redact raw wxids")
        owner_admin_result = mcp_server.wechat_get_all_online(owner_handle, owner_token)
        assert_true("AUTHCODE_REDACTED" in owner_admin_result, "owner admin output should redact authcodes")
        assert_true(owner_admin_wxid not in owner_admin_result, "owner admin output should redact raw wxids")

        from wechatpad_hermes.wechatpad_client import WeChatPadClient

        client = WeChatPadClient(settings)
        captured_request: dict[str, object] = {}

        def fake_request(method: str, path: str, **kwargs: object) -> dict[str, object]:
            captured_request.update({"method": method, "path": path, **kwargs})
            return {"Data": {"CurrentSynckey": {"buffer": "sync-key-buffer"}, "AddMsgs": []}}

        client.request = fake_request  # type: ignore[method-assign]
        sync_payload = client.sync_messages()
        assert_true(client._synckey == "sync-key-buffer", "WeChatPad client should read OpenAPI CurrentSynckey.buffer")
        assert_true(client.get_synckey() == "sync-key-buffer", "WeChatPad client should expose current sync key")
        assert_true(sync_payload["Data"]["AddMsgs"] == [], "WeChatPad sync test should return mocked payload")
        body = captured_request.get("body")
        assert_true(isinstance(body, dict) and body.get("Scene") == 0 and body.get("Synckey") == "", "WeChatPad sync should POST Scene and Synckey body")
        captured_request.clear()
        client.sync_messages()
        body = captured_request.get("body")
        assert_true(isinstance(body, dict) and body.get("Synckey") == "sync-key-buffer", "second WeChatPad sync should reuse previous sync key")

        bridge_sync = Bridge(settings)
        try:
            bridge_sync.wechat.request = fake_request  # type: ignore[method-assign]
            bridge_sync.poll_once()
            assert_true(bridge_sync.store.get_runtime_value("wechatpad_synckey") == "sync-key-buffer", "bridge should persist sync key after polling")
            restored_bridge = Bridge(settings)
            try:
                assert_true(restored_bridge.wechat.get_synckey() == "sync-key-buffer", "bridge should restore sync key from runtime state on startup")
            finally:
                restored_bridge.store.close()
            assert_true(bridge_sync._sleep_seconds() == max(settings.poll_interval, 0.2), "bridge should use normal poll interval without errors")
            bridge_sync.error_count = 3
            assert_true(bridge_sync._sleep_seconds() > settings.poll_interval, "bridge should back off after repeated poll errors")
            bridge_sync.error_count = 99
            assert_true(bridge_sync._sleep_seconds() == 30.0, "bridge poll backoff should be capped")
        finally:
            bridge_sync.store.close()

        client.request = lambda _method, _path, **_kwargs: {}  # type: ignore[method-assign]
        client._update_synckey({"Data": {"CurrentSynckey": "sync-key-1"}})
        assert_true(client._synckey == "sync-key-1", "WeChatPad client should remember CurrentSynckey")
        client._update_synckey({"Data": {"MaxSynckey": {"str": "sync-key-2"}}})
        assert_true(client._synckey == "sync-key-2", "WeChatPad client should read nested MaxSynckey fallback")
        client._update_synckey({"Data": {"MaxSynckey": {"buffer": "sync-key-3"}}})
        assert_true(client._synckey == "sync-key-3", "WeChatPad client should read OpenAPI MaxSynckey.buffer fallback")
        dry_run_payload = client.send_text("123@chatroom", "hello dry run")
        dry_run_json = str(dry_run_payload)
        assert_true("123@chatroom" not in dry_run_json and "hello dry run" not in dry_run_json, "dry-run send output should not echo raw target or content")

        live_send_settings = replace(settings, send_enabled=True, dry_run=False)
        live_send_client = WeChatPadClient(live_send_settings)
        captured_send: dict[str, object] = {}
        live_send_client.request = lambda method, path, **kwargs: captured_send.update({"method": method, "path": path, **kwargs}) or {"Code": 0, "Success": True}  # type: ignore[method-assign]
        live_send_client.send_text(group_chatroom, "hello live shape", at=someone_wxid)
        send_body = captured_send.get("body")
        assert_true(captured_send.get("method") == "POST" and captured_send.get("path") == "/Msg/SendTxt", "send_text should call the WeChatPad SendTxt endpoint")
        assert_true(
            isinstance(send_body, dict)
            and send_body.get("ToWxid") == group_chatroom
            and send_body.get("Content") == "hello live shape"
            and send_body.get("At") == someone_wxid
            and send_body.get("Type") == 1,
            "send_text should use the OpenAPI SendNewMsgParamDoc field shape",
        )

        env_file = tmp_dir / f"wechatpad-hermes-smoke-{uuid.uuid4().hex}.env"
        env_file.write_text("WECHATPAD_BOT_NAMES=FromFile\nWECHATPAD_DRY_RUN=false\n", encoding="utf-8")
        previous_bot_names = os.environ.pop("WECHATPAD_BOT_NAMES", None)
        os.environ["WECHATPAD_ENV_FILE"] = str(env_file)
        os.environ["WECHATPAD_DRY_RUN"] = "true"
        try:
            env_settings = load_settings()
            assert_true(env_settings.bot_names == ["FromFile"], "settings should load values from WECHATPAD_ENV_FILE")
            assert_true(env_settings.dry_run, "process environment should override WECHATPAD_ENV_FILE")
        finally:
            if previous_bot_names is not None:
                os.environ["WECHATPAD_BOT_NAMES"] = previous_bot_names
            os.environ["WECHATPAD_ENV_FILE"] = ""

        policy_file = tmp_dir / f"wechatpad-hermes-smoke-{uuid.uuid4().hex}.yaml"
        policy_file.write_text("privacy:\n  store_raw_messages: true\n", encoding="utf-8")
        try:
            os.environ["WECHATPAD_POLICY_PATH"] = str(policy_file)
            os.environ["WECHATPAD_STORE_RAW_MESSAGES"] = ""
            assert_true(load_settings().store_raw_messages, "policy privacy.store_raw_messages should be read")
            assert_true(
                any(issue.code == "raw_message_storage_enabled" for issue in check_settings(load_settings(), strict=False)),
                "doctor should warn when raw message storage is enabled",
            )
            os.environ["WECHATPAD_STORE_RAW_MESSAGES"] = "false"
            assert_true(not load_settings().store_raw_messages, "env should override policy raw storage setting")
        finally:
            os.environ["WECHATPAD_POLICY_PATH"] = ""
            os.environ["WECHATPAD_STORE_RAW_MESSAGES"] = ""
            if policy_file.exists():
                policy_file.unlink()

        policy_merge_file = tmp_dir / f"wechatpad-hermes-smoke-{uuid.uuid4().hex}.yaml"
        policy_private_wxid = sample_wxid("policy_private")
        policy_owner_wxid = sample_wxid("policy_owner")
        env_private_wxid = sample_wxid("env_private")
        env_owner_wxid = sample_wxid("env_owner")
        policy_merge_file.write_text(
            "owner_wxids:\n"
            f"  - {policy_owner_wxid}\n"
            "private:\n"
            "  allow_all: false\n"
            "  allowed_wxids:\n"
            f"    - {policy_private_wxid}\n",
            encoding="utf-8",
        )
        try:
            os.environ["WECHATPAD_POLICY_PATH"] = str(policy_merge_file)
            os.environ["WECHATPAD_ALLOWED_PRIVATE_WXIDS"] = env_private_wxid
            os.environ["WECHATPAD_OWNER_WXIDS"] = env_owner_wxid
            merged_policy = Policy(load_settings())
            assert_true(
                {policy_private_wxid, env_private_wxid}.issubset(merged_policy.allowed_private_wxids),
                "policy and env private allowlists should merge",
            )
            assert_true(
                {policy_owner_wxid, env_owner_wxid}.issubset(merged_policy.owner_wxids),
                "policy and env owner wxids should merge",
            )
        finally:
            os.environ["WECHATPAD_POLICY_PATH"] = ""
            os.environ["WECHATPAD_ALLOWED_PRIVATE_WXIDS"] = ""
            os.environ["WECHATPAD_OWNER_WXIDS"] = ""
            if policy_merge_file.exists():
                policy_merge_file.unlink()
    finally:
        if store is not None:
            store.close()
        if mcp_server is not None:
            if mcp_server._store.cache_info().currsize:
                mcp_server._store().close()
                mcp_server._store.cache_clear()
            mcp_server._settings.cache_clear()
            mcp_server._policy.cache_clear()
            mcp_server._wechat.cache_clear()
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(db_path) + suffix)
            if candidate.exists():
                candidate.unlink()
        if env_file is not None and env_file.exists():
            env_file.unlink()
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke ok")


if __name__ == "__main__":
    main()
