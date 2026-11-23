# whale-mobile 端侧同步清单（对齐 whale-core v1 clean break）

> 2026-11-23 · 云端开发出，Tob 转应用开发。目标：whale-mobile 接入已 clean break 的 whale-core v1（无版本路径 + 信封 1.x）。
> **没有双挂载、没有回退档**：旧路径 core 侧已彻底移除。端侧发版必须先于（或同步于）core 生产切换（阶段③），否则设备断连。

## 1. 地址变更（必改）

| 用途 | 旧（已删除） | 新（唯一） |
| --- | --- | --- |
| 设备 WSS 长连 | `wss://<core-host>/api/v2/devices/ws` | `wss://<core-host>/api/devices/ws` |
| 配对兑换 | `POST /api/v2/devices/pair` | `POST /api/devices/pair` |
| device-first 配对 | `POST /api/v1/devices/pair`、`POST /api/v1/devices/pair/approve` | **已删除，无替代** |

- 配对流程只剩 **owner-first**：主人在控制台「设备 → 配对新设备」生成 6 位码 → 手机在设置页**输入该码** → `POST /api/devices/pair {code, pubkey_hex, label}` → 立即激活（应答含 `device_id`、`name`、`scopes`、`ws_path`）。
- APK 设置页原来的「发起配对 → 显示码等主人批准」入口需改为「输入主人出示的 6 位码」。
- 建议：WS 地址优先使用配对应答里的 `ws_path` 字段（若当前硬编码，这次改掉——以后路径再变端侧零改动）。

## 2. 信封版本（必改）

- 所有帧 `"v": "2.0"` → `"v": "1.0"`（正则 `^1\.[0-9]+$`）。
- `device.hello.payload.protocol_version` → `"1.0"`。
- core 校验器对 `2.x` 一律回 `error.protocol_version` 并关闭连接（fail-closed，无容忍窗）。
- **除版本号外消息 schema 零变化**：能力枚举（35 项）、回执五终态（succeeded/failed/timeout/cancelled/unknown）、幂等键、ID 规则（前缀+ULID、device_id 32-hex）、错误码单表、`experience.event`/`action.request`/`sensor.query` 等全部沿用——端侧校验器只需改版本常量与地址。

## 3. 契约镜像（必改）

- 仓内 `contracts/v2/` 目录 → 更名/替换为 `contracts/device/`（whale-core 侧已完成；内容除信封与路径外逐字一致，可整目录覆盖同步）。
- `PROTOCOL.md` §1 路径表、§2 信封、§18 新增「2026-11-23 · clean break」条目以 core 仓为准。
- 端侧 `CapabilitiesV2` 等带代次的符号名建议择机更名（非阻塞，纯观感）。

## 4. 不变量（确认端侧没依赖被破坏的行为）

- 心跳 30s / 90s 无心跳标离线（不踢线）；撤销才踢线（`error.device_revoked`）。
- `action.request` 带 `idempotency_key` + `timeout_ms` + `expires_at`；回执五终态；超时 core 记 `unknown` 不重放。
- `notification.sync` 离线重放、`experience.event` 至少一次 + core 去重、`device.confirm` 挑战应答——全部不变。
- 附件 REST（`/api/chat/attachments`、`/api/attachments/{id}`）契约路径已去版本化，但 **core 尚未实现该面**——端侧若已写死调用请降级为特性探测（404 即视为未实现）。

## 5. 端侧自测口径（发版前必过）

1. 配对：控制台出码 → APK 输码 → `POST /api/devices/pair` 200，应答 `ws_path=/api/devices/ws`。
2. WSS 握手：challenge → hello（v=1.0）→ hello_ack；心跳/断线重连正常。
3. 全能力冒烟：`device.status`/`device.observe`/`device.tap`（或端侧 CI 的等效假 core）。
4. 通知：`notification.event` 接收 + receipt 回执；离线后重连 `notification.sync` 重放。
5. 负例：`v=2.0` 帧 → `error.protocol_version` 且连接关闭（确认 core fail-closed 行为与端侧错误展示）。
6. 版本 floor：建议在端侧记录 core 协议代次，hello 被拒时给出「需要升级 App/核心」的明确提示而非裸断连。

## 6. 时序耦合（Tob 裁决窗口）

- core v1 生产切换（阶段③）之日旧路径即不存在；**APK 新版必须先到达全部在用设备**（两台真机先行）。
- 建议顺序：端侧改完并自测 → 真机对预发 core 验证 → Tob 定生产切换日 → 切换当天 APK 已全量。
- 回滚预案：core 侧旧库/旧码全留档（git + N100 快照），若切换失败 core 回退旧版即恢复旧路径，APK 新版因无回退档会断——所以切换日 core 回退包与 APK 旧版包都要在手。
