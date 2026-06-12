from __future__ import annotations

import argparse
import json
import re
from pathlib import PurePosixPath
from typing import Any


INLINE_BLOCKED_ENV_NAME_PATTERN = re.compile(r"(AUTHCODE|ADMIN_KEY|API_KEY|PASSWORD|PASSWD|SECRET|TOKEN)$", re.IGNORECASE)


def posix_join(*parts: str) -> str:
    path = PurePosixPath(parts[0])
    for part in parts[1:]:
        path = path / part
    return str(path)


def build_config(args: argparse.Namespace) -> dict[str, Any]:
    release_dir = posix_join(args.app_dir, "releases", args.release, "hermes-wechatpadpromax")
    data_dir = posix_join(args.app_dir, "data")
    env = {
        "PYTHONPATH": posix_join(release_dir, "src"),
        "WECHATPAD_ENV_FILE": posix_join(data_dir, ".env"),
        "WECHATPAD_DB_PATH": posix_join(data_dir, "wechatpad-hermes.sqlite3"),
        "WECHATPAD_POLICY_PATH": posix_join(data_dir, "policy.yaml"),
        "WECHATPAD_CONTEXT_TOKEN_TTL_SECONDS": str(args.context_token_ttl_seconds),
        "WECHATPAD_DRY_RUN": "true",
        "WECHATPAD_SEND_ENABLED": "false",
        "WECHATPAD_ADMIN_TOOLS_ENABLED": "false",
        "WECHATPAD_ALLOW_UNKNOWN_OUTBOUND": "false",
        "WECHATPAD_STORE_RAW_MESSAGES": "false",
    }
    return {
        "mcpServers": {
            args.server_name: {
                "command": posix_join(release_dir, ".venv", "bin", "python"),
                "args": ["-m", "wechatpad_hermes.mcp_server"],
                "env": env,
            }
        }
    }


def assert_no_inline_secrets(config: dict[str, Any]) -> None:
    env = next(iter(config["mcpServers"].values())).get("env", {})
    bad_keys = [
        key
        for key in env
        if INLINE_BLOCKED_ENV_NAME_PATTERN.search(str(key)) and str(key) != "WECHATPAD_CONTEXT_TOKEN_TTL_SECONDS"
    ]
    if bad_keys:
        raise ValueError("MCP config must not inline secret env vars: " + ", ".join(sorted(bad_keys)))
    text = json.dumps(config, ensure_ascii=False)
    if re.search(r"(?i)(authcode|admin[_-]?key|api[_-]?key|password|passwd|secret)\s*[:=]\s*[^\s\"'}]+", text):
        raise ValueError("MCP config appears to contain inline secret material")


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a safe Hermes MCP config for WeChatPad-Hermes")
    parser.add_argument("--release", required=True, help="Release name, for example dryrun-20260612044441")
    parser.add_argument("--app-dir", default="/mnt/user/appdata/wechatpad-hermes", help="Unraid appdata base path")
    parser.add_argument("--server-name", default="wechatpad-hermes", help="MCP server name in Hermes config")
    parser.add_argument("--context-token-ttl-seconds", type=int, default=1800)
    args = parser.parse_args()
    config = build_config(args)
    assert_no_inline_secrets(config)
    print(json.dumps(config, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
