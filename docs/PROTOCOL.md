# Whale Link 协议 v1

Core 与所有客户端（控制台、手机 App、眼镜桥、IO 渠道桥、脚本）之间的唯一协议。一个信封、一套方法、一套事件、一套错误。

## 1. 传输

| 通道 | 地址 | 用途 |
| --- | --- | --- |
| WebSocket | `ws(s)://<host>/api/link` | 长连接：RPC 双向调用 + 事件推送 + 能力声明 |
| HTTP | `POST /api/rpc/{method}` | 与 WS 相同的方法，适合脚本和无长连接的端 |
| SSE | `GET /api/events?topics=run.*,notify` | 只读事件流 |
| SSE | `POST /api/chat/stream` | 单轮对话流式输出（`run` / `step` / `delta` / `done` 事件） |
| HTTP | `GET /api/schema` | 全部方法与参数 JSON Schema，客户端可据此生成类型 |
| HTTP | `GET /api/artifacts/{id}` | 生成的图片/音频等产物（需鉴权） |
| WebSocket | `/api/devices/ws` | whale-mobile 设备身体（设备线协议，Ed25519 设备身份；见 `contracts/device/`） |

鉴权：`Authorization: Bearer <token>`；WS 在 hello 的 `token` 字段或 `?token=` 携带。token 由 `whale token` 查看/轮换。

## 2. 信封

```json
{"v": "1.0", "id": "01JD…", "type": "rpc.request", "ts": 1795400000000, "corr": null, "payload": {}}
```

- `id`：发送方生成的 ULID；`corr`：响应/回执关联的请求 `id`。
- `type`：`hello` · `welcome` · `rpc.request` · `rpc.response` · `event` · `ping` · `pong` · `error`。
- 同一连接上的帧**严格有序**：处理某个请求期间产生的事件一定先于该请求的响应送达。

## 3. 握手

```json
→ {"type": "hello", "id": "h1", "payload": {
     "token": "…", "role": "mobile", "name": "Pixel 9",
     "subscribe": ["run.*", "notify", "approval.*"],
     "capabilities": [{"name": "phone.vibrate", "description": "震动手机", "risk": "external",
                       "params": {"type": "object", "properties": {"ms": {"type": "integer"}}}}]}}
← {"type": "welcome", "corr": "h1", "payload": {"client_id": "cli_…", "protocol": "1.0",
     "server": {"name": "鲸鱼", "version": "1.0.0"}, "methods": ["chat.send", …]}}
```

- `role`：`console` | `mobile` | `glasses` | `bridge` | `cli`（用作对话渠道名）。
- 客户端声明的能力注册为 `client.<name>.<capability>`，Agent 可直接调用；调用时服务端向客户端发 `rpc.request {method: "capability.invoke", params: {name, args, run_id}}`，客户端回 `rpc.response`。断线即注销。
- 订阅可随时用 `link.subscribe {patterns}` 替换。

## 4. RPC

```json
→ {"type": "rpc.request", "id": "r1", "payload": {"method": "chat.send", "params": {"text": "你好"}}}
← {"type": "rpc.response", "corr": "r1", "payload": {"ok": true, "result": {…}}}
← {"type": "rpc.response", "corr": "r1", "payload": {"ok": false, "error": {"code": "not_found", "message": "…", "retryable": false}}}
```

方法按领域分组（完整参数见 `/api/schema`）：

| 领域 | 方法 |
| --- | --- |
| system | `system.status` · `system.schema` · `events.query` |
| chat | `chat.send` · `chat.cancel` · `conversations.list/messages/update/delete` |
| runs | `runs.list` · `runs.get`（含全部 Step 与子 Run） |
| memory | `memory.list/get/search/remember/update/forget/graph/stats/maintain` |
| capabilities | `capabilities.list` · `capabilities.invoke` · `policy.set` |
| approvals | `approvals.list` · `approvals.resolve` |
| tasks | `tasks.create/list/get/start/cancel/delete` · `schedule.list/set_enabled/run_now/delete` |
| personas | `personas.list/save/import/delete/activate` |
| config | `config.get/update/set_secret` · `models.status/test/upsert_provider/remove_provider/list_remote` |
| plugins | `plugins.list/set_enabled/configure` |
| autonomy | `autonomy.status` · `autonomy.explore` |
| devices | `devices.list/get/pair_start/pair_approve/revoke/rename/set_scopes/set_role/privacy/set_privacy/action/query/notify/events/actions/search_events/digest_now` |
| link | `link.subscribe` · `link.clients` |

`scope`：多数管理方法要求 owner 身份；`chat.send`、`runs.get`、`system.status` 等标记为 `client` 的方法所有已认证客户端可用。

## 5. 错误

| code | HTTP | 含义 |
| --- | --- | --- |
| `invalid_params` | 400 | 参数不合法（`details.errors` 给出字段位置） |
| `unauthorized` | 401 | token 缺失或错误 |
| `forbidden` | 403 | 权限或策略禁止 |
| `not_found` | 404 | 对象或方法不存在 |
| `conflict` | 409 | 状态冲突（如审批已处理） |
| `rate_limited` | 429 | 超出每日模型请求预算 |
| `internal` | 500 | 未预期错误 |
| `unavailable` | 503 | 依赖不可用（模型、设备、客户端离线），`retryable` 指示能否重试 |
| `timeout` | 504 | 超时；对外部动作意味着**结果未知**，不要自动重放 |

## 6. 事件

`event.payload = {topic, data, event_id}`。主题：

| 主题 | data |
| --- | --- |
| `agent.state` | `{phase: idle|thinking|acting|waiting, active: [{run_id, mode, status, conversation_id, title, channel}]}` |
| `run.started` · `run.completed` · `run.failed` · `run.cancelled` | `{run}` |
| `run.step` | `{step: {id, run_id, seq, kind, title, status, input, output, started_at, ended_at}}`（开始与结束各一次） |
| `run.delta` | `{run_id, conversation_id, delta}`（流式文本，不持久化） |
| `run.plan` · `run.progress` · `run.status` | 计划更新、工具进度、等待审批 |
| `memory.created/updated/reinforced/superseded/forgotten/extracted/maintained` | 记忆生命周期 |
| `approval.requested` · `approval.resolved` | 审批对象 |
| `task.created/updated/deleted` · `schedule.changed/fired` | 任务与调度 |
| `device.connected/disconnected/status/event/event_filtered/action/paired/revoked/updated/digest` | 设备（`event_filtered` 只含原因，不含内容） |
| `notify` | `{title, body, meta, delivered_to}`：Agent 主动给主人的消息 |
| `plugin.changed` · `config.changed` · `clients.changed` · `capability.registered/unregistered` | 系统状态 |
| `autonomy.exploring/explored/proactive` | 自主行为 |

非临时事件写入事件日志，`events.query {pattern, limit, before}` 可回放。

## 7. Step 类型（可观测性）

| kind | 含义 | output |
| --- | --- | --- |
| `recall` | 检索记忆 | `{memories: [{id, kind, content, score, why[]}]}` |
| `think` | 一次模型调用 | `{latency_ms, decision: reply|tools, tools[], reasoning}` |
| `tool` | 调用能力 | `{status: ok|error|unknown, data, summary, error, latency_ms}` |
| `approval` | 等待用户批准 | `{approval_id, approved}` |
| `reply` | 最终回复 | `{text}` |

## 8. 与 whale-mobile 的关系

- **设备身体**（感知、通知、动作、Root Shell）走设备线协议 `/api/devices/ws`（信封 `v` 1.x）：已在真机验证，具备 Ed25519 身份、序号、回执五终态与幂等键。Core 在边缘把设备能力翻译成 `device.<name>.<capability>`。
- **聊天界面**（Flutter）迁移到 Link v1：一条 `/api/link` 连接 + `chat.send` + 订阅 `run.*`/`notify`，即可获得与控制台完全一致的流式回复、思维轨迹和审批。
