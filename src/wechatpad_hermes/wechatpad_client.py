from __future__ import annotations

import json
import urllib.parse
import urllib.error
import urllib.request
import base64
import pathlib
from typing import Any

from .config import Settings


class WeChatPadClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = settings.wechatpad_base_url.rstrip("/")
        self._synckey = ""

    def request(
        self,
        method: str,
        path: str,
        *,
        authcode: str | None = None,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        timeout: int = 60,
    ) -> dict[str, Any]:
        query = dict(params or {})
        if authcode is None:
            authcode = self.settings.wechatpad_authcode
        if authcode:
            query["authcode"] = authcode
        url = f"{self.base_url}/{path.lstrip('/')}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"WeChatPad request failed: HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            raise RuntimeError(f"WeChatPad request failed: {type(exc.reason).__name__}") from None
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = {"raw": text}
        return parsed if isinstance(parsed, dict) else {"Data": parsed}

    def sync_messages(self) -> dict[str, Any]:
        payload = self.request("POST", "/Msg/Sync", body={"Scene": 0, "Synckey": self._synckey})
        self._update_synckey(payload)
        return payload

    def get_synckey(self) -> str:
        return self._synckey

    def set_synckey(self, value: str) -> None:
        self._synckey = str(value or "")

    def _update_synckey(self, payload: dict[str, Any]) -> None:
        data = payload.get("Data") if isinstance(payload.get("Data"), dict) else payload
        if not isinstance(data, dict):
            return
        for key in ("CurrentSynckey", "currentSynckey", "current_synckey", "Synckey", "synckey", "MaxSynckey", "maxSynckey"):
            value = _nested_text(data.get(key))
            if value:
                self._synckey = value
                return

    def send_text(self, to_wxid: str, content: str, at: str = "") -> dict[str, Any]:
        if not self.settings.send_enabled or self.settings.dry_run:
            return {"Code": 0, "Success": True, "Message": "dry-run: not sent", "Data": {"Target": "redacted", "ContentLength": len(content)}}
        return self.request("POST", "/Msg/SendTxt", body={"ToWxid": to_wxid, "Content": content, "At": at, "Type": 1})

    def send_image(self, to_wxid: str, image_path_or_url: str) -> dict[str, Any]:
        if not self.settings.send_enabled or self.settings.dry_run:
            return {
                "Code": 0,
                "Success": True,
                "Message": "dry-run: image not sent",
                "Data": {"Target": "redacted", "ImageLength": len(image_path_or_url)},
            }
        # Read local file and convert to base64
        path = pathlib.Path(image_path_or_url)
        if not path.exists() or not path.is_file():
            return {"Code": -1, "Success": False, "Message": f"Image file not found: {image_path_or_url}"}
        data = path.read_bytes()
        if len(data) > 12 * 1024 * 1024:
            return {"Code": -1, "Success": False, "Message": "Image exceeds 12MiB limit"}
        b64 = base64.b64encode(data).decode("ascii")
        payload = {
            "ToWxid": to_wxid,
            "Base64": b64,
        }
        return self.request("POST", "/Msg/UploadImg", body=payload, timeout=120)

    def set_webhook(
        self,
        url: str,
        enabled: bool = True,
        secret: str = "",
        message_types: list[str] | None = None,
        timeout: int = 5,
        retry_count: int = 3,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "url": url,
            "enabled": enabled,
            "includeSelfMessage": False,
            "messageTypes": message_types or ["*"],
            "timeout": timeout,
            "retryCount": retry_count,
        }
        if secret:
            body["secret"] = secret
        return self.request("POST", "/Webhook/Set", body=body)



    def get_online_info(self, authcode: str | None = None) -> dict[str, Any]:
        return self.request("GET", "/User/GetOnlineInfo", authcode=authcode)

    def get_all_online(self) -> dict[str, Any]:
        if not self.settings.wechatpad_admin_key:
            return {"Code": 401, "Success": False, "Message": "WECHATPAD_ADMIN_KEY not configured", "Data": None}
        return self.request("GET", "/User/GetAllOnline", authcode="", params={"key": self.settings.wechatpad_admin_key})

    def get_cache_info(self, authcode: str | None = None) -> dict[str, Any]:
        return self.request("POST", "/Login/GetCacheInfo", authcode=authcode)


def _nested_text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("str") or value.get("string") or value.get("String") or value.get("buffer") or "").strip()
    return str(value or "").strip()