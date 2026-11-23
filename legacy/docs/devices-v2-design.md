# Devices V2：当前实现与集成边界

> 更新：2026-11-08。当前设备协议只有 V2。协议 canonical source 是
> [whale-mobile/contracts/v2](https://github.com/amwangfan/whale-mobile/tree/main/contracts/v2)，
> 本仓 `contracts/v2/` 为同步镜像。旧版 V1 设计仅作历史记录，不代表当前运行时。

## 1. 当前架构

```text
Android NodeRuntimeV2
  ├─ WssClientV2 / DeviceIdentity / EventOutboxV2
  ├─ ReceiptDispatcherV2 / OfflineQueue
  ├─ Agent2 / Notification2 / Recording
  └─ daily 与 root 两个 APK flavor
             │ contracts/v2 · WSS
             ▼
Core EmbeddedWssGateway ── DeviceRegistry / MemoryStore
             │
             ├─ experience.event → perception adapter
             ├─ sensor.query / sensor.result
             ├─ action.request / action.receipt
             ├─ notification.* / device.confirm.*
             └─ run.trace.event / recording.state
```

管理 REST 仍沿用全站 `/api/v1` 前缀；设备长连接使用
`/api/v2/devices/ws`。两者的 URL 前缀不同，不代表设备协议仍为 V1。

## 2. Core 组件

| 路径 | 当前职责 |
| --- | --- |
| `devices/registry.py` | 配对、设备身份、撤销与 scope 上限 |
| `devices/embedded_wss.py` | 生产 WSS 会话、握手、心跳、V2 消息、持久化动作幂等与回执 |
| `devices/gateway_v2.py` | 多 transport 选路与动作编排接口 |
| `devices/transports/` | ADB、SSH、embedded WSS 的 transport 实现 |
| `devices/gui_agent/` | observe → decide → act → verify 自主 GUI 循环 |
| `devices/recording/` | 录屏、RunTrace 与时间戳对齐 |
| `api/devices_api.py` | 配对、列表、scope、query、action、确认和撤销管理 API |

## 3. 当前生产接线状态

- 生产设备长连接由 `EmbeddedWssGateway` 承担，V2 手机端已完成 leaf5-root 与
  nova-daily 双机联调。
- REST query/action 经 `DeviceGatewayV2` 统一选路（#77，2026-11-18）：
  GUI 类 embedded>adb>ssh、shell/文件类 ssh>embedded>adb；编排层逐动作
  复查 registry 状态与 scope（「有 ADB/SSH 地址」≠ 已授权）。
- `DeviceGatewayV2` 构造注入生产 MemoryStore：动作幂等记录落与
  EmbeddedWssGateway 相同的 kv 域（`device:action:{device}:{idem}` 等），
  跨重启可查、重复键不重放；embedded 路由由网关自身持久化，adb/ssh 路由
  由编排层 reserve→finalize（崩溃遗留 pending 过期按 unknown 只读呈现）。
- ADB/SSH 连接参数来自 kv `config:device_transports`（管理端
  `GET/PUT /devices/transports` 维护，ssh 强制主机密钥 pinning），
  不再硬编码于演示脚本；装配失败/非法项跳过不阻塞 embedded 上线。
- GUI Agent 可经 `GatewayV2Transport` 适配器直接跑在编排器上；
  自主体（initiative 闭环）动作端口优先 `GatewayV2ActionPort`。
- GUI Agent 已有真实设备闭环记录（设置 → 电池状态回读）；Bilibili 完整
  自主浏览流程仍待真机验收。

## 4. 请求与回执

- sensor.query、action.request 与 device.confirm.request 的等待项记录发起设备
  身份；回包必须来自同一条已认证设备连接，不能由其他设备以相同 ID 消费。
- action 的逻辑幂等键为 `device_id + idempotency_key`。重复键返回已记录状态，
  不重新下发可能有副作用的动作。
- 五种终态为 succeeded / failed / timeout / cancelled / unknown；
  received 只是已接收，不等于执行完成。
- 超时、断线或结果未知不代表动作没有发生；状态查询只读，不自动重放。

## 5. 验证

```bash
python -m pytest tests/test_embedded_wss.py tests/test_embedded_wss_transport.py -q
python -m pytest tests/test_gateway_v2.py tests/test_gui_agent.py -q
python scripts/contract_sync_check.py --core . --mobile ../whale-mobile
```

跨仓协议校验以同一批次的两个候选 ref 运行；真机验收另看
whale-mobile 的 `docs/STATUS.md`。仓库 CI 采用手动触发，不把 PR 合并本身当作
测试通过的证据。

## 6. 下一步

1. ~~将 ADB / SSH / embedded WSS 统一接入生产动作与 GUI Agent 选路入口~~（#77 已落地）。
2. ~~将 DeviceGatewayV2 的幂等预留与回执记录持久化，并统一其状态机语义~~（#77 已落地）。
3. 补齐设备列表、详情、能力协商和真实拓扑视图（Core #60）。
4. 完成 Root 手机 Bilibili 端到端任务验收（Core #58）。
