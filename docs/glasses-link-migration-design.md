# 眼镜端 Link 迁移设计方案（v2 `/turbo/v1/*` → v3 Link role=glasses）

> 2026-11-24 · 云端开发 · **已批准并实施 core 侧**：§9 八裁决 2026-11-24 全按建议 a（Tob 批）；
> C1-C5 已落地（全量 152 绿），眼镜侧施工依据见 `docs/glasses-side-g114-handoff.md`。
> 反代与生产切换走两阶段确认，不随本文档自动生效。
> 勘察来源：v3 侧 `src/whale/link/protocol.py`、`src/whale/server/http.py`、`src/whale/server/api.py`、
> `src/whale/agent/runtime.py`、`src/whale/capabilities/{spec,policy,builtin}.py`、`src/whale/channels/*`、
> `docs/PROTOCOL.md`；v2 侧为 N100 现网只读勘察（`/opt/whale/whale-core/src/whale_core/api/` 六文件
> 3944 行，未写任何生产文件）。
> 本文所有 ID/号码/凭据均为假名占位。

## 0. 一句话结论

v2 眼镜面 17 个 HTTP 路径、14 组能力里，**4 组直通现有 Link 面、3 组被语义替代（轮询/两阶段/手动投递参数淘汰）、
3 组需 core 新增小型 `glasses.*` 方法（context.sync / qq_reply / deliver）、4 组是产品裁决项**
（quick 档位、语音 ASR、日程模块、RayNeo 三面）；眼镜从「轮询的 HTTP 客户端」变成「有推送能力的常驻
Link 客户端」——这是本次迁移白捡的能力（后台结果可直接推镜片，v2 做不到）。

## 1. v3 Link 面事实（勘察结论 A）

信封 `{v:"1.0", id, type, ts, corr, payload}`；类型 `hello/welcome/rpc.request/rpc.response/event/ping/pong/error`。
传输与分发（`server/http.py`、`link/protocol.py` 实测）：

| 事实 | 细节 |
| --- | --- |
| 端点 | `WS /api/link`；同一套方法另有 `POST /api/rpc/{method}`（HTTP 等价面） |
| 握手 | 首帧必须 `hello{token, role, name, subscribe[], capabilities[]}`；成功回 `welcome{client_id, protocol, server, methods[]}`；token 错回 `error{code:"unauthorized"}` 并 4401 关闭 |
| 鉴权 | 单一 `api_token`（Bearer 语义）。**无 per-role 令牌、无 per-role 方法列管**——只有方法级 `scope: owner\|client` 二分（`client.owner=False` 的非 owner 客户端禁调 owner 方法） |
| RPC | client→server `rpc.request{method,params}` → `rpc.response{ok, result\|error{code,message,retryable?}}`；同帧序保证：请求处理期间产生的事件先于其响应送达 |
| 反向 RPC | server→client `rpc.request{method:"capability.invoke", params:{name,args,run_id}}`；hello 声明的能力注册为 `client.<name>.<cap>`，断线即注销 |
| 推送 | `event{topic,data,event_id,ts}` 按 subscribe 的 fnmatch 模式过滤；`link.subscribe` 可随时换；outbox 5000 满则丢事件不阻塞 |
| 心跳 | 协议有 `ping/pong`，**无服务端强制心跳/离线判定**（客户端自保活） |
| 帧上限 | **Link 面当前无显式单帧上限**（设备线另有 256 KiB 约束；Link 面需补，见 §8） |
| 错误码 | `invalid_params/unauthorized/forbidden/not_found/conflict/rate_limited/internal/unavailable/timeout` |
| notifier | `hub.notify(title,body,meta)` → 在线 owner 且订阅 `notify`/`*` 的 Link 客户端 + 注册的 notifiers（手机走设备线 `notification.event`） |

对话主链路（`chat.send`，scope=client）：`{text, conversation_id?, images[]≤4, persona_id?, wait=true}`
→ 一条 Run（recall→think⇄tool→reply，全步骤持久化）；`channel` 自动取 client 的 `role`
（role=glasses ⇒ channel="glasses"，与迁移后历史会话同口径）；`peer` 默认 `owner` ⇒ owner 记忆空间。
`wait=true` 同步等终稿；`wait=false` 立即回 `{conversation_id, run_id, accepted:true}`。
**无论 wait 如何，`run.delta`（流式 token）、`run.step`、`run.started/completed/failed` 都上事件总线**
（runtime.py L171-178 实测：delta 与 req.on_delta 无关，一律 publish）。

审批流（`capabilities/policy.py` 实测）：能力 Risk 默认裁决 read/write→allow、external/dangerous→**ask**；
ask ⇒ 落 `approvals` 表 + 播 `approval.requested{id, run_id, capability, args, reason, risk, title}`，
Run 挂 waiting **最长 600s**；任何 owner 客户端 `approvals.resolve{id, approve, note?, remember?}`
一次性核销（重复核销回 `conflict`），过程全量在 run steps 可见。policy.set 可把某能力直接 deny。

内建能力面：`time.now`、`memory.*`、`web.search/fetch`、`notify.owner`、`shell.run`（owner_only，
dangerous）；QQ 渠道能力 `qq.send_private/qq.send_group`（owner_only，external，默认 ask）；
媒体插件 `image.generate`、`speech.synthesize`（产物落 artifacts，`GET /api/artifacts/{id}` 拉取，
Bearer 鉴权）。**v3 无文件写/自修改能力（fail-closed 默认）；无日程模块；无 RayNeo 面**。

## 2. v2 glasses 能力面清单（勘察结论 B，N100 现网只读；17 路径 / 14 组能力）

鉴权：`Authorization: Bearer <WHALE_GLASSES_TOKEN>`（phone 角色专用，与管理 token 分离，常量时间比较；
已知但非 phone 的凭据在 /v1/identity 回 403）。绑定 tailscale 地址，公网经东京反代 `/turbo/v1/*`
exact location 转发。

| # | 端点 | 请求形状 | 回执形状 | 语义要点 |
| --- | --- | --- | --- | --- |
| 1 | `POST /v1/chat` | `{text, mode: companion\|quick, sessionId?, followup?, context?, audio?{base64≤10MB,format}, deliverResult?, location?/city?/lat?/lng?}` | `{reply, sessionId, mode, locationKnown, latencyMs, intentMs, toolMs, replyMs, followup?{status}, followupMs?, replies?[], audioDelivery?, delivery?, error?}` | companion=人格+记忆召回+情节回写（会话 `glasses:{sessionId}`）；quick=轻量模型不进记忆；audio→云端 ASR 并入 text；context=ChatContext.wrap 带标签行（现在/今日日程/明日日程/天气）；位置三级解析（请求→store→env）；确定性规则抽日程候选进 owner 池 |
| 2 | `POST /v1/chat/followup` | `{sessionId, turnId?, waitMs≤25000}` | `{status: pending\|ready\|none\|unknown\|unsupported, reply, timing, sessionId}` | 并行链路第二条长轮询（FollowupBoard 内存收件板）；旧客户端无 followup 字段时服务端同步等 ≤22s 合并 replies |
| 3 | `POST /v1/qq/reply` | `{text≤4000}` | `{ok, receipt:"sent"}` \| `{ok:false, error, reason}` | 口述发**服务端固定绑定** owner QQ（目标绝不从请求推导）；走 ProactiveOutbox 风控（被动 lane、静默时段豁免、被动日额度保险丝） |
| 4 | `POST /v1/result/deliver` | `{text?, images?[{filename,data_base64}], audio?{filename,data_base64}, conversation?, preferOwnerDm?=true}` | `{ok, count, channels[], receipts[{channel: qq_c2c\|qq_group\|whale_notification\|phone_gallery\|stub_log, ok, detail, qq_online}], note}` | 统一投递：QQ 在线优先→离线走原生通知→图片回落手机图库；note=模型拟的交代语 |
| 5 | `POST /v1/agent` | `{text?, sessionId?, confirmId?}` | `{sessionId, reply, actions[{kind, summary, status: ok\|fail\|held, output_digest}], pending: null\|{confirmId, kind, description, exact}}` | 前缀命令分类（chat/tool/send/notify/shell/write/self_modify）；shell/写/自修改一律 pending 两阶段回声（confirmId 一次性+600s TTL+绑 session，exact 原文上镜片）；~34 类高危 denied 无入口；interactive 软限 60s 到点**不杀**转后台、完成经 deliver 推送；自修改=background 类（git 检查点+全量 pytest+失败回滚，默认无硬截断）；台账每回合一条≤200 字/字段 |
| 6 | `POST /v1/context/sync` | `{calendar: {ISO日期 → [{id,title,start,end,allDay}]\|null}, source?, observedAt?}` ≤64KiB | `{ok, stored, receivedAt, dates[], idempotent}`；400 带契约错误码 | 联签契约：单日≤12、title≤120、窗口 [today,today+7]（Asia/Shanghai）、`[]`=空日程/`null`=未读三态、calendarHash 幂等重放不更新 receivedAt；错误码六枚 `invalid_json\|invalid_date\|too_many_entries\|title_too_long\|unauthorized\|payload_too_large` |
| 7 | `GET /v1/identity` | — | `{role:"phone", device, label}` | phone 角色身份+绑定设备（WHALE_GLASSES_DEVICE_ID 可钉选；注册表故障 fail-soft device=""） |
| 8 | `GET/POST /v1/schedule/candidates`、`GET /v1/schedule/list`、`GET /v1/schedule/source?conversation&messageId`、`POST/DELETE /v1/schedule/items` | `{id, action: add\|reject}` / `{title, date, time?}` / `{id}` | `{candidates[]}` / `{items[]}` / `{ok, item?}` / `{context[]}`（源消息 ±10 条） | owner 日程：候选裁决、正式条目 CRUD、来源溯源（owner-only） |
| 9 | `POST /v1/reply/tier` | `{conversation_id, tier: fast\|slow}` | `{ok, conversation_id, tier}` | 会话级回复档位（管理端操作，2026-11-21 主人令） |
| 10 | `POST /v1/rayneo/ingest` | `{source:"rayneo", source_instance, batch_id?, items[1..50]}` ≤320KiB | `{ok, batch_id, results[{client_seq, effect}], ack_cursor, server_time, retry_after_s}` | 全天转写批量上行；dedup_key 幂等（effect: ingested/revision_applied/replay_idempotent/rejected*）；每 source_instance 5 批/分限速；503=ingest_disabled/store_unavailable |
| 11 | `POST /v1/rayneo/query` | `{source:"rayneo", ...}` ≤64KiB | `{ok, items[], derived[], instruction_eligible, query_disabled, error_class?}` | 原文+派生 episode 查询；故障 fail-soft 空结果+error_class |
| 12 | `POST /v1/rayneo/control` | `{source:"rayneo", action: stop_ingest\|resume_ingest\|purge_all\|delete_range{from,to}}` | `{ok, ingest_enabled?}` \| `{ok, deleted_segments, deleted_episodes}` | 墓碑真删+阻止回灌+派生 episode 联删 |
| 13 | `GET /v1/health` | — | `{ok, capabilities[]}` | 无鉴权存活面（公网刻意不暴露） |
| 14 | `GET /openapi.json` | — | OpenAPI 3.0 | 同上 |

眼镜宿主进程内还有 4 个后台循环（非端点，属能力面）：主动问候（判定→QQ 外发）、日程提醒
（agent.notify 走频控+台账）、RayNeo 总结器（独立意图模型实例、让路 /v1/chat）、owner 事实提炼
worker（眼镜宿主单 worker）。

## 3. 映射总表

| v2 能力 | v3 Link 承接 | 方式 |
| --- | --- | --- |
| /v1/chat（companion） | `chat.send {text, conversation_id, wait}` + 订阅 `run.*` | **直通**（语义升级：单 Run 全步骤可见） |
| /v1/chat（quick 轻量档） | 裁决 D1：`chat.send` 加 `model_role:"fast", remember:false` 扩展，或砍 | 小改/砍 |
| /v1/chat（audio 内联 ASR） | 裁决 D2：端侧 ASR 只发文本（推荐），或 core 加 audio 入参 | 端侧/小改 |
| /v1/chat（context 带标签行+位置） | 新增 `glasses.context.sync`（快照）+ chat.send 经 `system_extra` 注入当轮事实 | 新增（见 §4.6） |
| /v1/chat（deliverResult=true） | 废弃参数；投递由 agent 自主（`qq.send_*`/`notify.owner`）或显式 `glasses.deliver` | 语义替代 |
| /v1/chat（日程候选抽取） | 依附裁决 D3（日程模块去留） | 裁决 |
| /v1/chat/followup | **删除**——`run.delta/run.step/run.completed` 事件流替代轮询 | 淘汰 |
| /v1/qq/reply | 新增 `glasses.qq_reply {text}`（目标服务端绑定，保持 v2 纪律） | 新增 |
| /v1/result/deliver | 新增 `glasses.deliver {text?, artifact_ids?[]}`；回执沿用 receipts 分层 | 新增 |
| /v1/agent（门控回合） | **收敛进 `chat.send`**：前缀命令改自然语言；shell=`shell.run` 能力；审批= `approval.requested` 事件 + `approvals.resolve`；高危=policy deny；软限转后台=wait=false+事件；台账=runs/steps 持久化 | 语义替代 |
| /v1/agent（write/self_modify） | v3 无此能力（fail-closed 默认）；如需要走「任务」面 `tasks.create`（goal 模式+审批） | 裁决 D6 |
| /v1/context/sync | 新增 `glasses.context.sync`（契约逐字平移：≤12/日、title≤120、窗口、三态、calendarHash 幂等、六错误码） | 新增 |
| /v1/identity | `welcome` + `system.status`（role/clients/devices 都在） | 直通（零新增） |
| /v1/schedule/*（5 端点） | 裁决 D3：移植为 `glasses.schedule.*` 方法族 / 砍 / 只读快照 | 裁决 |
| /v1/reply/tier | 裁决 D1 同案：会话级档位 → `conversations.update` 扩展或砍 | 裁决 |
| /v1/rayneo/*（3 端点） | 裁决 D4：**建议第一刀不切**（v2 面并存只管转写，或暂停采集）；迁则 `glasses.rayneo.*` | 裁决 |
| /v1/health | `GET /api/health`（已有）+ WS 连接态 | 直通 |
| /openapi.json | `GET /api/schema`（已有，含全方法 JSON Schema） | 直通 |
| 后台循环×4 | v3 内建承接：主动问候→autonomy.proactive+notify.owner；owner 事实→记忆管道（remember=true 默认开）；日程提醒→依附 D3；RayNeo 总结→依附 D4 | 语义替代 |

## 4. 逐项映射细节（payload / 回执）

### 4.1 聊天主链路（对应 /v1/chat companion）

眼镜 → core：
```json
{"type":"rpc.request","id":"<ULID>","payload":{"method":"chat.send","params":{
  "text":"帮我查明天天气","conversation_id":"glasses:<会话ULID>","wait":false}}}
```
core → 眼镜（同一连接，有序）：
```json
{"type":"rpc.response","corr":"<ULID>","payload":{"ok":true,"result":{
  "conversation_id":"glasses:<会话ULID>","run_id":"run_<ULID>","accepted":true}}}
{"type":"event","payload":{"topic":"run.step","data":{"step":{"kind":"think","title":"决定调用 web.search",...}}}}
{"type":"event","payload":{"topic":"run.delta","data":{"run_id":"run_<ULID>","delta":"明天"}}}
{"type":"event","payload":{"topic":"run.completed","data":{"run":{...}}}}
```
- 会话 id：眼镜端生成并持有（`glasses:` 前缀约定沿用，迁移后历史会话即此口径）；`conversations.ensure` 幂等。
- 上屏策略：`run.step` 标题作状态行（「决定调用 网页搜索」），`run.delta` 打字机上屏，固件不支持流式则等
  `run.completed` 取终稿（或 chat.send `wait:true` 同步等——WS  inflight 不阻塞同连接其他帧）。
- 打断：`chat.cancel {run_id}`。
- 断线补齐：`conversations.messages {id, limit}`（scope=client）+ `events.query`（owner）回放。
- **体验差异（须告知 Tob）**：v2 「第一条秒回、第二条补工具结果」在 v3 单 Run 里没有第一条；
  工具回合的首字延迟变长。缓解=状态行+delta 流式；要完全复刻需 core 加快速首答（工作量另计，裁决 D8）。

### 4.2 审批（替代 confirmId 两阶段）

```json
{"type":"event","payload":{"topic":"approval.requested","data":{
  "id":"aprv_<ULID>","capability":"shell.run","title":"执行命令",
  "args":{"command":"…原样回声…"},"reason":"glasses · 帮我看下磁盘", "risk":"dangerous"}}}
→ {"type":"rpc.request","payload":{"method":"approvals.resolve","params":{"id":"aprv_<ULID>","approve":true}}}
```
语义对照：args 原文上屏 = `pending.exact`；一次性+600s TTL = v2 confirmId 600s 逐字一致；
重复/过期核销回 `conflict` = v2「确认已过期或不存在」；高危无入口 = policy 规则 deny（`policy.set`）。
`remember=true` 可沉淀「以后此类直批」——v2 没有的能力，默认不用。

### 4.3 QQ 快捷回复（对应 /v1/qq/reply）——新增 `glasses.qq_reply`

```json
{"method":"glasses.qq_reply","params":{"text":"口述内容"}}
→ result {"ok":true,"receipt":{"status":"ok","sent":1,"total":1}} 
  ｜ {"ok":false,"error":{"code":"unavailable","message":"…"}}
```
core 侧实现要点：目标=配置绑定的 owner QQ（**不从参数推导**，v2 硬纪律平移）；经 QQ 渠道发送能力
（OneBot `send_private_by_uin`）+ 渠道自带回执；频控/静默口径由 core 侧通知/发送预算承接（v3 暂无
被动 lane 概念，实现时按 channels.qq 现有风控对齐，不旁路）。文本上限 4000 沿用。

### 4.4 统一投递（对应 /v1/result/deliver）——新增 `glasses.deliver`

```json
{"method":"glasses.deliver","params":{"text":"…","artifact_ids":["att_<ULID>"], "prefer_owner_dm":true}}
→ result {"ok":true,"delivered":[{"channel":"qq","ok":true,"detail":"sent"}],
          "receipts":[…分层回执…]}
```
路由策略平移 v2：QQ 在线优先 → 离线走 `hub.notify`（手机设备线 notification.event 持久队列/在线
Link 端）。**媒体不再内联 base64**：图片/语音先落 artifacts（agent 产物天然是 artifact；眼镜上行媒体
如需另议），眼镜端自己要在镜片展示/播放则 `GET /api/artifacts/{id}`（Bearer）拉取。
v2 的「交代语 note」由发起方 chat 回合自然覆盖，不单列。

### 4.5 门控智能体（对应 /v1/agent）——收敛，不新增

`/v1/agent` 的七个意图分支在 v3 的去向：chat/tool → 统一 Run；shell → `shell.run`（ask 审批）；
send/notify → `qq.send_*`/`notify.owner`（ask 审批）；write/self_modify → **v3 无能力=天然 denied**
（需要时走 `tasks.create` 目标模式，裁决 D6）；软限转后台 → wait=false + 事件推送（且 v3 多了
notify 直推镜片，v2 只能推 QQ）；台账 → runs/steps/events 全持久化，比 v2 台账更全。
眼镜侧动作：删掉命令前缀解析 UI（或保留为用户习惯语法糖，文本原样进 chat.send 即可，由模型意图
识别接管——这正是 v3「一个大脑」的兑现点）。

### 4.6 上下文同步（对应 /v1/context/sync）——新增 `glasses.context.sync`

params/契约/错误码逐字平移 v2 联签契约（六错误码装 `invalid_params` 的 `details.code`，HTTP 时代
的 401/413 由 Link 鉴权与帧上限承接）。存储落 core kv；**注入点**：companion 组装时对
channel="glasses" 的 Run 追加 `system_extra`（渲染「当轮手机事实资料」，口径同 v2
`render_context`——core 侧小改，chat.send 增加 server 端 context provider 钩子，**不接受客户端
直接传 system_extra**，防提示词注入面扩大）。位置摘要并入同一快照（`glasses:last_location` 语义平移）。

### 4.7 身份 / 健康 / schema

- 身份：`welcome`（client_id/server/version/methods）+ `system.status`（clients/devices 在线态）。
  v2 的 403「已知但非 phone 凭据」语义在 v3 由 role ACL（§8 裁决 D5）承接。
- 健康：`GET /api/health` 已有；公网不开（延续现网纪律），眼镜以 WS 连接态为准。
- 能力发现：`GET /api/schema` 已有。

### 4.8 日程（/v1/schedule/*）与档位（/v1/reply/tier）——裁决项

- D3 若保留日程：新增 `glasses.schedule.list / candidates / candidate_action {id,action} /
  add_item {title,date,time?} / delete_item {id} / source {conversation,messageId}`，存储与提醒循环
  一并移植（v2 `life/schedule.py` + `schedule_remind.py` 逻辑简单，移植是直译级工作量）；
  同时恢复 /v1/chat 的候选抽取（挂进 v3 记忆管道的 observe 钩子或 chat.send 后置步）。
- D1 档位：v3 ModelRouter 本有 chat/fast 角色；最小改=`chat.send` 加可选 `model_role`（fast→
  fast 角色+remember=false 即 v2 quick 语义）+ `conversations.update` 接受档位字段。

### 4.9 RayNeo（/v1/rayneo/*）——裁决项，建议缓迁

迁移评估已定 v3 无 rayneo 面、原文库留档不迁。Link 化若要承接：`glasses.rayneo.ingest`
（批 ≤50、幂等、限速 5 批/分平移；**注意 320 KiB 体与 Link 帧上限的关系**，或改分批）/
`glasses.rayneo.query` / `glasses.rayneo.control`（墓碑删除语义平移）。但原文是 owner_private
高敏数据（独立库、90 天 TTL、不进普通备份），v3 侧需要同级隐私设计，不应随本次迁移顺手做。
**建议：第一刀把 chat/审批/投递/上下文主链路 Link 化，rayneo 三面暂留 v2 glasses 服务并存**
（反代 exact location 不动），独立立项迁移。

## 5. 眼镜侧改造点清单（固件/桥接 app，交应用开发）

| # | 改造点 | 说明 |
| --- | --- | --- |
| G1 | 传输：HTTP 短连轮询 → WSS 长连 `wss://<host>/api/link`；指数退避重连；30s `ping` 保活 | 替换全部 17 个路径的 HTTP 调用层 |
| G2 | 握手：首帧 `hello{token, role:"glasses", name:"<假名设备名>", subscribe:["run.*","approval.*","notify"], capabilities:[…]}`；处理 `welcome`；token 放安全存储 | 凭据下发渠道不变 |
| G3 | RPC 框架：envelope 编解码、id/corr 配对、pending future 超时、有序收帧 | ~200 行客户端基础设施 |
| G4 | 聊天重构：发 `chat.send`（wait=false）→ 状态行渲染 `run.step` → 流式上屏 `run.delta` → 终稿 `run.completed`；长按打断 `chat.cancel`；**删 followup 轮询与 replies 合并逻辑** | 镜片 UX 主路径重写 |
| G5 | 审批 UI：`approval.requested` → 卡片显示 title+args 原文（回声语义）→ 确认/拒绝 → `approvals.resolve`；600s 未决按超时处理 | 替代 confirmId 两阶段 UI |
| G6 | 上下文同步：调用点换 `glasses.context.sync` RPC（请求/回执/重试/幂等客户端逻辑**零变化**） | 最小改动 |
| G7 | QQ 口述回复：换 `glasses.qq_reply` | 最小改动 |
| G8 | 结果投递：`deliverResult` 参数删除；后台结果改收 `notify` 事件上镜片（新增推送消费能力） | 白捡的推送通道 |
| G9 | 音频：ASR 前端化（端侧转写后只发文本）；TTS/图片产物 `GET /api/artifacts/{id}` 拉流播放/展示 | 依裁决 D2 |
| G10 | 断线补齐：重连后 `conversations.messages` 拉会话 + `events.query` 回放 | v2 没有的能力 |
| G11 | 健康检查：`/v1/health` 轮询删除，以 WS 连接态为准 | — |
| G12 | 错误处理：HTTP 状态码 → `error.code` 枚举映射（`unavailable(retryable)` 才自动重试；`timeout` 结果未知不重放） | 沿用 v2 诚实纪律 |
| G13 | 会话 id：本地生成并持久 `glasses:<ULID>`，复用至用户显式新开 | 与迁移后历史会话同口径 |
| G14 | （裁决 D3）日程 UI 五操作换 `glasses.schedule.*`；（裁决 D4）RayNeo 上传器本期不改 | 视裁决 |

## 6. core 侧新增工作清单（裁决后由我实现，另立实施单）

| # | 工作 | 规模 |
| --- | --- | --- |
| C1 | `glasses.qq_reply` / `glasses.deliver` / `glasses.context.sync` 三个方法 + 存储/注入钩子（§4.3/4.4/4.6） | 各 ~100 行 + 测试 |
| C2 | role=glasses 方法列管（裁决 D5 若做）：hello role 落 client.meta，`RpcRegistry.call` 加 role allowlist | ~30 行 + 测试 |
| C3 | Link 面单帧上限（建议 1 MiB）+ `docs/PROTOCOL.md` 补 glasses 角色约定与心跳建议 | 小改+文档 |
| C4 | （裁决 D1）`chat.send` 加 `model_role`/`remember` 可选参 | 小改+测试 |
| C5 | （裁决 D3）`glasses.schedule.*` 方法族 + 提醒循环移植 | 中 |
| C6 | （裁决 D4，另立项）`glasses.rayneo.*` + 隐私面设计 | 大 |
| C7 | 反代：东京 nginx 增 `/api/link`（WSS upgrade 头、长 read_timeout）、按需 `/api/rpc/`、`/api/artifacts/`；`/turbo/v1/*` 并存期保留 | 运维 |
| C8 | 全量测试基线复跑 + 假名契约测试（goldens 全假名） | 纪律 |

## 7. 公网/反代面

现网：`/turbo/v1/{chat,chat/followup,qq/reply,agent,context/sync,result/deliver,rayneo/*}` exact → N100:8191；
兜底 `/turbo/` → dashboard。目标：`/api/link`（WSS）→ v1 端口，其余 `/api/*` 按需白名单（**`schema`
不开公网**；`artifacts` 含 owner 产物，建议仅 tailnet 或反代加白名单）；`/turbo/v1/*` 在并存期不动，
眼镜全量切 Link 后随 v2 glasses 服务一并退役。

## 8. 安全与鉴权口径

1. **令牌等价性**：v2 glasses token 本就是 owner 通道凭据；v3 单一 api_token 上眼镜在信任级上**等价**
   ——但 v3 token 同时是全管理面钥匙（config.update 等）。**裁决 D5：建议加 role=glasses 列管**
   （白名单：chat.*/conversations.*/runs.get/link.subscribe/approvals.*/glasses.*/system.status/
   events.query），把「眼镜令牌泄露=全管理面暴露」的残余风险收掉；v2 无此问题因为 glasses 面天生只有
   14 个端点。
2. **owner 语义**：role=glasses ⇒ channel="glasses"、peer=owner、记忆 owner 空间——与 v2 逐字一致；
   身份只来自令牌，绝不取自请求体（v2 纪律平移，Link 面天然如此：client 对象由服务端构造）。
3. **审批回声**：args 原文下发上屏；一次性+600s；policy deny 无入口——三层与 v2 对齐。
4. **隐私分级**：context 快照与 rayneo 原文维持 owner_private 口径；run.step/approval 事件含主人
   指令摘要，只在 owner 客户端间投递（scope 机制保证）；artifacts 拉取需 Bearer。
5. **secrets 纪律**：token 只经 hello/环境变量，不进日志（http.py 现状不记 payload，保持）；
   文档/测试全假名。
6. **审计**：v3 runs/steps/events 持久化全覆盖 v2 台账字段（会话/意图/动作/裁决/时长/步数），
   不记命令原始输出的纪律由 step output 摘要口径沿用。

## 9. 待 Tob / 主人裁决清单

| # | 问题 | 选项 | 我的建议 |
| --- | --- | --- | --- |
| D1 | quick 轻量档 + reply/tier 档位 | a) chat.send 加 `model_role:"fast"`+`remember:false`（兼作档位）b) 砍，单 loop | a（小改，保住眼镜省电快答路径） |
| D2 | 语音输入 ASR | a) 端侧 ASR 只发文本 b) chat.send 加 audio 入参（云端 ASR 平移） | a（控制通道不串大负载；省 13MB 体） |
| D3 | 日程模块 | a) 移植 `glasses.schedule.*`+提醒循环+候选抽取 b) 砍 c) 只读快照 | a（直译级工作量，体验无损） |
| D4 | RayNeo 三面 | a) 本期留 v2 并存 b) 同步迁 `glasses.rayneo.*` c) 暂停采集 | a（隐私面独立立项） |
| D5 | role=glasses 方法列管 | a) 加白名单 b) 不加（与 v2 信任等价） | a（30 行收掉泄露面） |
| D6 | write/self_modify 快捷指令 | a) 不恢复（v3 无能力=deny）b) 经 tasks.create 目标模式承接 | a（自修改走 console/SSH，不上语音面） |
| D7 | 公网暴露面 | /api/link 必开；/api/rpc、/api/artifacts 是否开 | link 开；artifacts 加白名单；rpc 不开（眼镜全走 WS） |
| D8 | 「第一条秒回」体验 | a) 接受状态行+流式 b) core 加快速首答（工作量另计） | a 先行，不够再议 b |

## 10. 实施顺序建议（裁决后）

1. C2+C3（列管+帧上限+文档）→ 2. C1 三方法 → 3. 反代 /api/link → 4. 眼镜侧 G1-G8 联调
（假名环境）→ 5. 灰度：单副眼镜切 Link，v2 面并存 → 6. 全量切换 + v2 glasses 退役观察 →
7.（独立立项）rayneo。全程 git 不提交本仓（归统一压缩）；生产改动走两阶段确认。
