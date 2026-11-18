"""whale-mobile 设备面（contracts/v2 的 core 侧落地）。

- ``registry.DeviceRegistry``：设备注册 / 6 位配对码 / 吊销 / scope 收窄
- ``embedded_wss.EmbeddedWssGateway``：WSS 长连端点 /api/v2/devices/ws
  （挑战-应答验签、逐帧 schema 校验、心跳离线、experience/action/notification
  上下行、§7.2 五终态回执状态机）
- ``gateway_v2.DeviceGatewayV2``：多 transport 编排器（embedded_wss/adb/ssh；
  注入 store 后持久化幂等跨重启，#77 起为 REST/GUI Agent/任务统一入口）
- ``gateway_v2.GatewayV2Transport``：把编排器包成 DeviceTransport 的适配器
  （GUI Agent 等单通道消费侧走它即得统一选路）
- ``transport_config``：ADB/SSH 连接参数的 kv 配置装载与校验（不硬编码）
- ``receipt_codec``：action.receipt payload/持久记录 ↔ ActionReceipt 编解码
- ``transports.embedded_wss.EmbeddedWssTransport``：embedded 通道的
  DeviceTransport 实现（注册进 DeviceGatewayV2）
- ``perception.DevicePerceptionAdapter``：experience.event → perception 事件流
- ``schema.ContractValidator``：contracts/v2 帧校验（纯 stdlib）
- ``gui_agent``：GUI 自主体（v1 agent_loop 已由它取代并删除）

协议事实源：contracts/v2/PROTOCOL.md（whale-mobile 仓的镜像，内容相同）。
"""

from whale_core.devices.perception import DevicePerceptionAdapter
from whale_core.devices.registry import (
    DEVICE_CAPABILITIES,
    DeviceRegistry,
    DeviceRegistryError,
    device_id_from_pubkey_hex,
)

__all__ = [
    "DEVICE_CAPABILITIES",
    "DevicePerceptionAdapter",
    "DeviceRegistry",
    "DeviceRegistryError",
    "device_id_from_pubkey_hex",
]
