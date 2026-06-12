from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wechatpad_hermes.config import load_settings
from wechatpad_hermes.privacy import PrivacyFilter
from wechatpad_hermes.wechatpad_client import WeChatPadClient


def main() -> None:
    settings = load_settings()
    privacy = PrivacyFilter()
    client = WeChatPadClient(settings)
    data = {
        "online_info": client.get_online_info(),
        "cache_info": client.get_cache_info(),
    }
    if settings.admin_tools_enabled and settings.wechatpad_admin_key:
        data["all_online"] = client.get_all_online()
    print(privacy.redact(json.dumps(data, ensure_ascii=False, indent=2)).text)


if __name__ == "__main__":
    main()
