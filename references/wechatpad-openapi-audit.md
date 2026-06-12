# WeChatPadProMAX Interface Notes

This note records the redacted local audit of `WeChatPadProMAX.openapi.json` and the iPad deployment/login tutorial. It intentionally avoids real authcodes, admin keys, server addresses, wxids, passwords, and account details.

## Endpoints Used By This Bridge

- `POST /Msg/Sync`
  - Query: `authcode` is supported by the API and the bridge sends the configured BOT authcode there.
  - Body: `Scene` and `Synckey`, matching `Msg.SyncParamDoc`.
  - Response: message batches are under `Data.AddMsgs`; sync cursors can appear as `Data.CurrentSynckey.buffer` or `Data.MaxSynckey.buffer`.
  - Bridge behavior: stores the latest cursor and sends it in the next sync request.

- `POST /Msg/SendTxt`
  - Query: `authcode` is supported.
  - Body shape is `At`, `Content`, `ToWxid`, `Type`, matching `Msg.SendNewMsgParamDoc`.
  - Bridge behavior: real sending is blocked unless `WECHATPAD_SEND_ENABLED=true` and `WECHATPAD_DRY_RUN=false`.

- `POST /Login/GetCacheInfo`
  - Query: `authcode` is required.
  - MCP behavior: output is redacted.

- `GET /User/GetOnlineInfo`
  - Query: `authcode` is required.
  - MCP behavior: output is redacted.

- `GET /User/GetAllOnline`
  - Query: `key` is the server-side admin key.
  - MCP behavior: Hermes never supplies this key; `wechat_get_all_online` uses the configured server env key only after owner private context authorization.

## Endpoint Not Enabled By Default

- `POST /Msg/StartAutoSync`
  - Query: `authcode` is required.
  - Body: `TargetURL`, matching `Msg.SyncParam2Doc`.
  - Deployment note: this requires a callback URL reachable by WeChatPadProMAX. The current bridge uses active `/Msg/Sync` polling to keep the public attack surface smaller.

## Tutorial Deployment Notes

- The WeChatPadProMAX service is deployed separately from this bridge.
- Its runtime configuration includes a web/API port, Redis connection, Redis password, and a backend admin token key. Those values must stay in the WeChatPadProMAX host configuration and should not be copied into Hermes Skill or MCP config.
- The login flow is Swagger-driven: generate an authcode from the admin interface, request a QR login with a stable device name for one WeChat account, complete mobile confirmation when required, then verify online/cache status through API calls.
- This bridge only needs the final BOT authcode, BOT wxid, base URL, Hermes endpoint, policy, and SQLite path.
