#!/usr/bin/env python3
"""WeChatPad 登录检测脚本 - 部署到 Unraid 上，被看门狗远程调用"""
import os, json, urllib.request

# 1. Read authcode from env
authcode = ""
env_path = "/mnt/user/appdata/wechatpad-hermes/data/.env"
with open(env_path) as f:
    for line in f:
        if line.startswith("WECHATPAD_AUTHCODE=***
            authcode = line.split("=", 1)[1].strip().strip('"').strip("'")
            break

if not authcode:
    print("STATUS=NO_AUTHCODE")
    exit()

# 2. Call HeartBeat
url = f"http://your_nas_ip:8062/api/Login/HeartBeat?authcode={authcode}"
try:
    req = urllib.request.Request(url, method="POST", data=b"{}", headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=10)
    body = json.loads(resp.read())
    code = body.get("Code", -99)
    msg = str(body.get("Message", ""))[:80]
    print(f"STATUS_CODE={code}")
    print(f"STATUS_MSG={msg}")
except Exception as e:
    print(f"STATUS=HTTP_ERR:{str(e)[:100]}")