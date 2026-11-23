# 眼镜侧 Link 改造 Handoff（G1-G14，应用开发可直接执行）

> 2026-11-24 · 云端开发 → 应用开发 · 对应裁决：Tob §9 八项全按建议 a（2026-11-24）。
> core 侧已落地（whale-core 仓 main 工作区，未提交）：role=glasses 白名单、Link 帧上限、
> `glasses.*` 方法族、`chat.send` 轻量档。**本文是眼镜固件/桥接 app 的唯一施工依据**；
> 协议总表见 `docs/PROTOCOL.md`，映射设计见 `docs/glasses-link-migration-design.md`。
> 全文 ID/号码/凭据均为假名示例。

## 0. 一句话

眼镜从「`/turbo/v1/*` HTTP 短连 + 轮询」改为「`wss://<host>/api/link` 一条长连：
RPC 请求/响应 + 事件推送」。**followup 轮询、confirmId 两阶段、deliverResult 参数、
/v1/health 轮询全部删除**；审批、后台结果、日程候选变成服务端推过来的事件。

## 1. 连接与握手（G1/G2）

```
WSS wss://<core-host>/api/link        （公网经东京反代；TLS 由反代终结）
```

首帧必须是 hello（10s 内，否则服务端关连接）：

```json
→ {"v": "1.0", "id": "01JEXAMPLE0000000000000001", "type": "hello", "payload": {
     "token": "<api_token>", "role": "glasses", "name": "fake-glasses-01",
     "subscribe": ["run.*", "approval.*", "notify"]}}
← {"v": "1.0", "id": "…", "type": "welcome", "corr": "01JEXAMPLE0000000000000001",
   "payload": {"client_id": "cli_01J…", "protocol": "1.0",
               "server": {"name": "鲸鱼", "version": "1.0.0"},
               "methods": ["chat.send", "glasses.context.sync", …]}}
```

- token 错误：收到 `error{code:"unauthorized"}` 后服务端以 4401 关闭。**token 即 owner
  凭据**，只放安全存储，不进日志。
- `role` 必须 `"glasses"`：它是会话渠道名（channel="glasses"，历史会话已按此口径迁移），
  也决定服务端方法白名单（越权回 `forbidden`）。
- 信封四要素：`id`（发送方 ULID）、`corr`（响应关联请求 id）、`type`、`payload`。
  时间戳一律 Unix ms。
- **帧上限 1 MiB**；超限收 `error{code:"invalid_params", message:"frame too large"}`，
  连接不断。
- 保活：每 30s 发 `{"type":"ping","id":"…"}`，收 `pong`；断线指数退避重连
  （建议 1s/2s/5s/15s/30s 封顶）。服务端不主动踢线、不重放事件。

## 2. RPC 调用模型（G3）

```json
→ {"v":"1.0","id":"r1","type":"rpc.request","payload":{"method":"chat.send","params":{…}}}
← {"v":"1.0","id":"…","type":"rpc.response","corr":"r1",
   "payload":{"ok":true,"result":{…}}}
              或 {"ok":false,"error":{"code":"unavailable","message":"…","retryable":true}}
```

- 客户端按 `corr` 配对；pending 请求要自己有超时（建议 130s，覆盖审批等待上限）。
- **同一连接帧严格有序**：一个请求处理期间产生的事件，先于该请求的响应送达。
- 错误码：`invalid_params / unauthorized / forbidden / not_found / conflict /
  rate_limited / internal / unavailable / timeout`。`retryable:true` 才可自动重试；
  `timeout` 意味着**结果未知，不要自动重放**。
- 眼镜角色可调方法全集（白名单，调别的回 `forbidden`）：

| 方法 | 用途 |
| --- | --- |
| `chat.send` / `chat.cancel` | 对话 / 打断 |
| `conversations.list` / `conversations.messages` | 会话列表 / 断线补齐历史 |
| `runs.get` / `runs.list` | 回合观测（步骤轨迹） |
| `link.subscribe` / `link.clients` | 改订阅 / 看在连端 |
| `approvals.list` / `approvals.resolve` | 审批 |
| `system.status` / `system.schema` | 状态 / 方法 schema |
| `events.query` | 事件回放 `{pattern, limit, before?}` |
| `glasses.*` | §4-§7 专用面 |

## 3. 聊天主链路（G4，替代 /v1/chat + /v1/chat/followup）

**发送**（wait=false，立即拿 run_id；回复走事件流）：

```json
→ {"type":"rpc.request","id":"r1","payload":{"method":"chat.send","params":{
     "text":"帮我查明天天气","conversation_id":"glasses:01JCONV…","wait":false}}}
← {"type":"rpc.response","corr":"r1","payload":{"ok":true,"result":{
     "conversation_id":"glasses:01JCONV…","run_id":"run_01J…","accepted":true}}}
```

**事件流**（按序到达；用 `run_id` 过滤本回合）：

| 事件 topic | 眼镜侧渲染 |
| --- | --- |
| `run.started` | 状态行「思考中」 |
| `run.step`（kind=think/tool，带 title） | 状态行：「决定调用 网页搜索」等（step 开始与结束各推一次） |
| `run.delta` `{run_id, delta}` | 回复正文流式上屏（打字机）；固件不支持流式可攒着 |
| `run.completed` `{run:{status, output…}}` | 终稿定屏；output 即全文 |
| `run.failed` / `run.cancelled` | 错误/已打断展示 |

**会话 id（G13）**：本地生成 `glasses:<ULID>` 并持久，复用到用户显式「新会话」；
服务端幂等。断线补齐（G10）：重连后 `conversations.messages {id, limit:50}` 拉历史，
需要更早事件用 `events.query {pattern:"run.*", limit:100}`。

**打断**：`chat.cancel {run_id}`（长按场景）。

**同步等终稿的退路**：`wait:true` 时 rpc.response 直接带
`{conversation_id, run_id, status, text, memories[], tools[]}`（连接不被阻塞，
事件照推）。仅建议调试/简单固件用。

**轻量档（quick 语义，裁决 D1）**：`"model_role":"fast", "remember:false"`——
轻量模型、不召回不写记忆。mode 参数废弃（companion/quick 由这两个参数表达）。

**已删除**：`/v1/chat/followup` 轮询、`replies` 合并、`followup="poll"`、
`deliverResult`、`audio{base64}`、`context` 带标签行（[现在]/[今日日程]…）、
location/city/lat/lng 请求字段。后三者的新家见 §4 与 §8。

## 4. 上下文同步（G6，替代 /v1/context/sync）

```json
→ {"method":"glasses.context.sync","params":{
     "calendar": {"2026-11-25": [{"id":"e1","title":"组会","start":"10:00",
                                  "end":"11:00","allDay":false}],
                  "2026-11-26": [] ,
                  "2026-11-27": null},
     "source": "android.calendar",
     "location": {"city":"假名市"},
     "weather": {"text":"晴 26°C"}}}
← {"ok":true,"result":{"ok":true,"stored":1,"receivedAt":"2026-11-24T…","dates":[…],
                        "idempotent":false}}
```

契约**逐字不变**：单日 ≤12 条、title ≤120、窗口 [今天, 今天+7]（core 时区）、
`[]`=空日程 / `null`=未读（三态保留）、同快照重放 `idempotent=true` 不刷新 receivedAt。
错误：`error.code="invalid_params"`，联签错误码在 `error.details.code`：
`invalid_json / invalid_date / too_many_entries / title_too_long`。

**刻意差异（一处）**：天气/位置从每请求随行改为 sync 随行，各自独立计时，
服务端渲染带龄期标注（>30min 标「缓存，可能已变化」）。同步时机建议不变：
进 app / 日程变化 / 位置或天气显著变化时。

## 5. 审批（G5，替代 /v1/agent 的 confirmId 两阶段）

模型决定调危险能力（如 `shell.run`、QQ 外发）时，服务端挂起该回合并推事件：

```json
← {"type":"event","payload":{"topic":"approval.requested","data":{
     "id":"aprv_01J…","capability":"shell.run","title":"执行命令",
     "args":{"command":"df -h"},"reason":"glasses · 看看磁盘","risk":"dangerous"}}}
```

- **args 原文上镜片**（回声语义，替代 v2 `pending.exact`），附 title/capability。
- 主人确认 → `{"method":"approvals.resolve","params":{"id":"aprv_01J…","approve":true}}`；
  拒绝 `approve:false`。一次性：重复核销回 `conflict`；**600s 未决服务端按超时拒绝**
  （与 v2 confirmId TTL 逐字一致）。
- 裁决结果另有 `approval.resolved` 事件；回合继续推进（run.step/run.completed 照常）。
- 镜片只需「确认 / 拒绝」两个动作；`note`、`remember` 参数不要上 UI。

## 6. QQ 口述回复（G7，替代 /v1/qq/reply）

```json
→ {"method":"glasses.qq_reply","params":{"text":"我晚点回你"}}
← {"ok":true,"result":{"ok":true,"receipt":{"status":"ok","sent":1,"total":1,"message_ids":["…"]}}}
```

目标 = 服务端绑定的 owner QQ，**参数里没有也不接受目标**（v2 纪律平移）。
QQ 离线/未配置 → `error.code="unavailable", retryable=true`，可稍后重试。
文本上限 4000 字符。

## 7. 结果投递与后台推送（G8）

- **眼镜收推送**：订阅 `notify` 主题即得服务端主动消息（日程提醒、后台回合结果等）：
  `event{topic:"notify", data:{title, body, meta, delivered_to}}`。这是 v2 没有的能力
  （v2 后台结果只能推 QQ）；镜片直接展示 body。
- **往 QQ/手机投递**：`glasses.deliver {text?, artifact_ids?[]}` →
  `result {ok, count, channels[], receipts[{channel, ok, detail}]}`。
  路由：QQ 在线直发优先 → 离线走通知扇出（手机原生通知/在线端）。
  - 图片/媒体**不再内联 base64**：先经 artifact 引用（agent 产物天然是 artifact），
    镜片要展示/播放走 `GET /api/artifacts/{id}`（Bearer 鉴权，HTTPS）。
  - 音频本期如实回 `unsupported_audio`（QQ 语音需 silk 转码，未接线）——
    收到这个回执要如实上屏，不能当作已送达。
  - `prefer_owner_dm` 只接受 `true`（群路由未接线，传 false 回 invalid_params）。

## 8. 语音与媒体（G9，裁决 D2）

- **ASR 端侧化**：`/v1/chat` 的内联 `audio{base64}` 入参废除。语音在端侧转写成文本后
  只发 `chat.send.text`（控制通道不串大负载；也省掉 ≤13MB 的包体）。
- **TTS/图片产物**：run 产物里的 `att_…` id → `GET https://<host>/api/artifacts/{id}`
  （Authorization: Bearer）拉流播放/展示。

## 9. 日程（G14·D3，替代 /v1/schedule/*）

| 方法 | 参数 | 返回 |
| --- | --- | --- |
| `glasses.schedule.list` | `{}` | `{items[]}` |
| `glasses.schedule.candidates` | `{}` | `{candidates[]}`（pending，带 source 溯源） |
| `glasses.schedule.candidate_action` | `{id, action:"add"\|"reject"}` | `{ok, item?}`；未找到/已裁决 → `not_found` |
| `glasses.schedule.add_item` | `{title, date:"YYYY-MM-DD", time?}` | `{ok, item}`；date 非法 → `invalid_params` |
| `glasses.schedule.delete_item` | `{id}` | `{ok: bool}` |
| `glasses.schedule.source` | `{conversation, messageId}` | `{context[]}`（±10 条，owner-only） |

候选由服务端从眼镜对话里确定性抽取（主人原话，宁缺勿滥），眼镜只需展示
`candidates` + 两个按钮。提醒由服务端循环推送（`notify` 事件），眼镜无轮询。

## 10. 身份、健康、schema（G11）

- `/v1/identity` → `welcome` 帧（client_id/server/methods）+ `system.status`
  （含在线 clients/devices）。
- `/v1/health` 轮询删除：以 WS 连接态为准；公网本就不开 health。
- `/openapi.json` → `GET /api/schema`（Bearer，全方法 JSON Schema）。

## 11. 错误与断线纪律（G12/G10）

- HTTP 状态码思维 → `error.code` 枚举（§2）。`unavailable+retryable` 才可自动重试；
  `timeout`/`unknown` 结果未知，绝不自动重放；`forbidden` 说明越权（报云端，不要重试）。
- 断线期间事件不重放：重连 hello 后用 `conversations.messages` 补对话、
  `events.query` 补事件；在飞 run 的结果以 `conversations.messages` 里的 assistant
  消息为准。

## 12. 验收清单（眼镜侧自验后报云端联调）

- [ ] hello/welcome/重连退避/30s ping；token 错 4401 关闭路径
- [ ] 聊天：run.step 状态行 → delta 流式 → completed 定屏；长按 cancel
- [ ] 审批卡：args 原文展示、确认/拒绝、600s 超时态、重复点击 conflict
- [ ] context.sync：三态/幂等/四错误码逐条过
- [ ] qq_reply 固定对端；deliver 文本+图片 artifact；unsupported_audio 如实上屏
- [ ] 日程：候选出现→add/reject→list 联动；source 溯源打开对话上下文
- [ ] 断线 5 分钟重连：对话与审批结果补齐
- [ ] 越权自检：调 `config.get` 必须回 `forbidden`

## 13. 与 v2 并存期

迁移期 v2 `/turbo/v1/*` 面保留（RayNeo 转写三面本期不动，裁决 D4）。
新旧客户端按版本共存：新固件只走 Link；旧固件继续打 v2 直到全量切换。
切换窗口与公网路由由云端另行两阶段确认。
