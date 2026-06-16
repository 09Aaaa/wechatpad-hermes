from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

EXPECTED_TOOLS = {
    "wechat_bridge_health",
    "wechat_get_online_info",
    "wechat_get_cache_info",
    "wechat_get_all_online",
    "wechat_get_recent_messages",
    "wechat_search_messages",
    "wechat_send_text",
}


def _text_content(result: Any) -> str:
    parts: list[str] = []
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text is not None:
            parts.append(str(text))
    return "\n".join(parts)


def _safe_env(db_path: Path) -> dict[str, str]:
    env = {
        "PYTHONPATH": str(SRC_DIR),
        "WECHATPAD_ENV_FILE": "",
        "WECHATPAD_DB_PATH": str(db_path),
        "WECHATPAD_BOT_NAMES": "BOT",
        "WECHATPAD_DRY_RUN": "true",
        "WECHATPAD_SEND_ENABLED": "false",
        "WECHATPAD_ADMIN_TOOLS_ENABLED": "false",
        "WECHATPAD_ALLOW_UNKNOWN_OUTBOUND": "false",
        "WECHATPAD_STORE_RAW_MESSAGES": "false",
    }
    if os.environ.get("PATH"):
        env["PATH"] = os.environ["PATH"]
    return env


async def _run() -> None:
    with tempfile.TemporaryDirectory(prefix="wechatpad-hermes-mcp-") as temp_dir:
        db_path = Path(temp_dir) / "mcp-smoke.sqlite3"
        server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "wechatpad_hermes.mcp_server"],
            env=_safe_env(db_path),
            cwd=str(PROJECT_ROOT),
        )
        with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as errlog:
            async with stdio_client(server, errlog=errlog) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    init = await session.initialize()
                    tools_result = await session.list_tools()
                    tool_names = {tool.name for tool in tools_result.tools}
                    missing = sorted(EXPECTED_TOOLS - tool_names)
                    if missing:
                        raise AssertionError(f"missing MCP tools: {missing}")

                    health_result = await session.call_tool("wechat_bridge_health", {})
                    health_text = _text_content(health_result)
                    health = json.loads(health_text)
                    if health.get("dry_run") is not True:
                        raise AssertionError("wechat_bridge_health should report dry_run=true in smoke env")
                    if health.get("send_enabled") is not False:
                        raise AssertionError("wechat_bridge_health should report send_enabled=false in smoke env")
                    if "authcode" in health_text.lower() or "admin_key" in health_text.lower():
                        raise AssertionError("wechat_bridge_health output must not expose authcode/admin key fields")
                    print(
                        json.dumps(
                            {
                                "mcp_stdio": "ok",
                                "server": init.serverInfo.name,
                                "tools": sorted(tool_names),
                                "dry_run": health.get("dry_run"),
                                "send_enabled": health.get("send_enabled"),
                            },
                            ensure_ascii=False,
                        )
                    )


def main() -> None:
    anyio.run(_run)


if __name__ == "__main__":
    main()
