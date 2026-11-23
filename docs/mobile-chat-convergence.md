# 移动端聊天面收敛契约（whale-mobile ↔ v3 统一主干）

> 2026-11-24 · 云端开发 · 状态：**M1-M5 已批（Tob，2026-11-24），C/M1/M4 配套已落地**：
> `POST /api/devices/link_ticket`（900s、绑 device_id、撤销联动踢线）、role=mobile 白名单、
> artifacts POST/DELETE 均已实现并测试（tests/test_link_ticket.py 9 例）。
> 原则：手机聊天面收敛到 v3 统一主干（Link RPC + 事件流 + artifacts），**不留 v2 聊天 REST 旁路**；
> 与设备线（`/api/devices/ws`，Ed25519 设备身份）分工不混线。全文 ID/号码/凭据/设备名均为假名示例。
> 引用：协议总表 `docs/PROTOCOL.md`；眼镜侧同口径施工件 `docs/glasses-side-g114-handoff.md`。

## 0. 两面一线的分工（先定边界，后面都由此推导）

| 面 | 通道 | 鉴权 | 承载 |
| --- | --- | --- | --- |
| **聊天面（本文）** | `wss://<host>/api/link`（role=`mobile`）+ 辅助 SSE/REST | 见 §2 鉴权方案 | 对话、历史、流式、审批、附件、观测 |
| 设备身体（不在本文范围） | `wss://<host>/api/devices/ws` | Ed25519 挑战签名（contracts/device） | 能力执行、通知投递、回执、心跳 |

## 1. 方法面（①逐项映射）

手机是**全功能端**：除聊天外可见记忆提示、工具轨迹、审批并裁决。逐项映射：

| 手机需求 | v3 承接 | 形状要点 |
| --- | --- | --- |
| 发消息 | `chat.send` | `{text, conversation_id?, images[]≤4, persona_id?, wait, model_role?, remember?}`；推荐 `wait:false` + 事件流 |
| 流式输出 | 订阅 `run.*`：`run.delta` 逐 token、`run.step` 状态行、`run.completed` 终稿 | 弱网/无长连退路：`POST /api/chat/stream`（SSE `run/step/delta/done`），同一份 Run |
| 打断 | `chat.cancel {run_id}` | run 落 `cancelled` 终态 |
| 历史 | `conversations.list {limit, channel?}` · `conversations.messages {id, limit, before?}` | `before`=毫秒 ts 分页；`conversations.update/delete`（改标题/绑人格/删会话）手机可用 |
| 事件流 | hello `subscribe:["run.*","approval.*","notify","task.*"]`；`link.subscribe` 可换 | 断线不重放，重连后 `conversations.messages` + `events.query` 补齐 |
| 回合观测 | `runs.get {id}`（全步骤+子 run）· `runs.list` | 工具轨迹=step 流（kind=think/tool/approval/reply，各带 title/output） |
| 记忆提示 | `chat.send(wait=true)` 的 `result.memories[]` + recall step | 见下「可见性建议」 |
| 审批 | `approval.requested` 事件 + `approvals.list {status}` + `approvals.resolve {id, approve, note?}` | args 原文展示；一次性；600s 未决服务端超时拒绝；重复核销 `conflict` |
| 附件 | `GET /api/artifacts/{id}`（Bearer）+ 上行 `POST /api/artifacts`（核心差距，见 §3） | 帧上限 1 MiB，大负载一律 artifact 引用 |
| 人格 | `personas.list` + `conversations.update {id, persona_id}` | 可选；不进第一版也可 |
| 状态/发现 | `system.status` · `system.schema` · `GET /api/health` | — |

**字段命名（F1 钉死）**：Link 面一律 snake_case——`conversations.list` 项为
`{id, title, channel, peer, persona_id, created_at, updated_at, turns, meta, preview:{role,text≤80}}`；
run/step/approval 载荷同例（`run_id/started_at/…`）。端侧按 snake_case 解析，勿读 camelCase
（读不到会回退本地缓存造成「列表不更新」假象——S4b F1 实证服务端投影正确）。

**可见性建议（裁决 M1）**：
- **审批：手机必须可见可裁决**。主人随身的是手机不是 console；v2 审批本就发生在 IM 侧。
  眼镜是最小确认卡（已批口径），手机是全量审批卡（args 展开、note、历史列表），console 同权。
- **记忆提示/工具轨迹：手机可见，与 console 同权**。UI 建议默认折叠成「想起了 N 条 · 调用了
  web.search」一行，点击展开——全功能端看得到、不吵。
- **管理面（config.*/models.*/plugins.*/memory 写/policy.set）手机不给**：UI 不提供 + 服务端
  role 白名单收紧（见 §2）。

## 2. 连接路径（②取舍）

### 2.1 三个选项

| 选项 | 形态 | 评 |
| --- | --- | --- |
| **O1（推荐）** | 两条连接并存：设备 WS（身体）+ Link WS（聊天，role=`mobile`） | 协议总表 §8 已定调此形态；信封/ID/错误码各成体系不互穿；Android 侧 Link 可**按需连接**（前台/聊天页活跃时建立，通知永不丢——通知走设备 WS），电量代价可控 |
| O2 | 设备 WS 承载 Link 帧（mux 容器消息） | 省一条 TCP，但设备协议要新增隧道类型，两协议演进锁步；鉴权栈互穿；否决 |
| O3 | 只留设备 WS，聊天做成 device capability | 聊天不是设备能力，倒退；否决 |

### 2.2 Link hello 鉴权方案（裁决 M3）

| 方案 | 形态 | 优点 | 代价/风险 |
| --- | --- | --- | --- |
| **A（第一刀，先通）** | hello 带 `api_token`（与 console 同），role=`mobile` + 服务端白名单 | 零新面，今天就能联调 | **诚实标注：role 是自报字段，白名单只约束诚实客户端，不是防盗边界**——主 token 上机，泄露=全管理面；token 轮换后手机需重新配置（无下发通道） |
| B | hello 接设备级签名（challenge + Ed25519，绑设备注册表） | 最强 | 两套协议鉴权栈互穿，实现面最大；否决 |
| **C（目标态，紧随 A）** | 设备线先验 → 签短期 Link ticket：`POST /api/devices/link_ticket`（§11 设备签名头）→ `{ticket, expires_in:900}`；hello 带 ticket，会话绑 device_id，撤销设备即失效 | 主 token 永不上机；天然可撤销；复用已验证的设备鉴权 | 核心差距（约 150 行 + 测试）：ticket 签发端点 + Link hello ticket 校验 |

**建议：A 不阻塞应用开发；C 是生产切换（③S6 公网开放）的硬前置**——公网面不挂主 token。

**role=`mobile` 白名单建议**（随 C 落地才有真实强制力；A 期先按自报 role 执行）：
`chat.send/cancel` · `conversations.*` · `runs.get/list` · `link.subscribe/clients` ·
`approvals.list/resolve` · `system.status/schema` · `events.query` · `personas.list` ·
`tasks.list/get` · `memory.search`。管理面一律 `forbidden`。

## 3. 附件 artifacts 口径（②的③）

| 项 | 口径 | 现状 |
| --- | --- | --- |
| 命名空间 | 统一 `/api/artifacts/{id}`（`att_`+ULID）；设备线 §11 的 `/api/chat/attachments` 不作聊天面入口 | GET 已实现（Bearer，单件 ≤64 MB） |
| 上行 | `POST /api/artifacts`：multipart 或 `application/octet-stream` 裸体 + `X-Whale-Name`/`X-Whale-Content-Type` 头 → `201 {id, url, content_type, size, sha256}` | **核心差距：未实现**（随 C 一起做） |
| 删除 | `DELETE /api/artifacts/{id}` | **核心差距：未实现**；保留策略另议 |
| 聊天图片引用 | 第一版：`chat.send.images` 直接收 `data:` URL（≤4 张，每张建议 ≤2 MB，离线可验） | 已实现 |
| —— 目标态 | 上行后引用 `artifact:<id>`；runtime 喂模型前由服务端把 artifact 转 data: 内联（**外部模型 provider 够不到尾网 URL，http(s) 直填不可行**） | 核心差距（runtime 小改） |
| 鉴权 | Link 面 Bearer/ticket；设备面如需另走 §11 签名头别名（聊天面不依赖） | GET 已强制 Bearer |
| 纪律 | 附件不进事件帧；>1 MiB 一律 artifact；sha256 完整性；`Content-Disposition` 原名 | 沿用现有实现 |

## 4. ③S4 staging 配合面（magic8/nova 真机预发）

### 4.1 形态

- **staging 实例**：N100 起独立 `whale-v1-staging.service`（新 unit，**不动现有五服务**），
  数据目录独立 `/opt/whale/v1-staging/`，监听 **tailscale IP `100.114.93.90:8189`**
  （尾网 only；与生产规划端口错开，不碰 127.0.0.1 生产纪律、不碰公网反代）。
- 配置：真实模型 provider（secrets 复制值、不落日志）；**QQ 渠道不接**（避免与现网双发）；
  设备面照常（两台真机已配对迁移口径见迁移评估 §6）。
- magic8/nova 经 tailnet 直达 `http://100.114.93.90:8189`（尾网自带加密；如需 https 再叠加
  tailscale serve，非前置）。

### 4.2 鸡生蛋（APK 先于生产切换到机）解法

- **base_url 不写死**：APK 以构建 flavor/运行时设置项区分——`staging` flavor 默认
  `http://100.114.93.90:8189`，`release` flavor 默认 `https://rayneo.wangfanlab.com`；
  所有路径从 base_url 派生：Link = `ws(s)://{base}/api/link`、设备线 = `…/api/devices/ws`、
  附件 = `…/api/artifacts/…`。**ws_path 动态派生，无硬编码**。
- 并存吸收：旧 APK 打 `/turbo/*`（v2 面保留）、新 APK 打 `/api/*`，版本并存期互不影响；
  生产切换只动反代路由，APK 无需再发版。

### 4.3 窗口步骤与验收矩阵

| 步 | 动作 | 负责 | 验收 |
| --- | --- | --- | --- |
| S4a | staging 起服 + 自验 | 云端 | health/schema/Link hello/chat 一轮/artifacts GET |
| S4b | staging APK 真机验证 | 应用开发 | 下表矩阵全绿 |
| S4c | 双端回归 + 封盘进 ③S5/S6 | 双方 | 回归记录归档 |

真机验收矩阵（staging，逐项过）：hello/welcome/重连退避 · 聊天流式（delta/状态行/定屏）·
打断 · 历史分页与断线补齐 · 审批卡全链路（请求→裁决→run 续跑）· 图片 data: 发送与 artifact
拉取 · （C 落地后）ticket 签发/失效/设备撤销联动 · 越权自检（`config.get` 必须 `forbidden`）。

纪律：staging 全程不碰生产 secrets 本体与 `/opt/whale/state/`；staging 数据可随时清空重建。

## 5. 裁决点汇总（报 Tob）

| # | 问题 | 建议 |
| --- | --- | --- |
| M1 | 审批手机可见可裁决；记忆/工具轨迹手机可见默认折叠 | 按建议 |
| M2 | 连接形态 O1 双连接（Link 按需，通知走设备 WS） | 按建议 |
| M3 | 鉴权 A（token+白名单）先通、C（设备线签 ticket）为生产硬前置 | 按建议 |
| M4 | 附件统一 `/api/artifacts`；上传/删除/artifact 引用为核心差距随 C 排期 | 按建议 |
| M5 | staging=尾网 `100.114.93.90:8189` 独立实例；APK base_url flavor 化 | 按建议 |

## 6. 错误与断线纪律（与眼镜 handoff 同口径，不复制正文）

`error.code` 枚举、`retryable` 才可自动重试、`timeout` 结果未知不重放、断线补齐路径、
越权自检——逐条沿用 `docs/glasses-side-g114-handoff.md` §2/§11/§12。
