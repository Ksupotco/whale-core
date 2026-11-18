"""DeviceGatewayV2 持久化幂等与跨重启对账测试（#77）。

事实源纪律：构造注入 store 后，动作记录落与 EmbeddedWssGateway 相同的 kv 域
（``device:action:{device}:{idem}`` + ``device:action-id:`` 索引 +
``device:receipts:`` 历史）：
- 同 device_id+idempotency_key 跨实例（模拟重启）直回已记录终态，绝不重放；
- timeout/unknown 不换 key 重执行（重复键回已记录状态）；
- 非自持久通道（adb/ssh 等）由编排层 reserve→finalize；进程崩溃遗留的
  pending 记录过期后按 expires_at 规则读作 unknown（fail-closed）；
- embedded_wss 通道的记录由网关自身承担（编排层不双占位）。
"""
from __future__ import annotations

import asyncio

from whale_core.devices.gateway_v2 import DeviceGatewayV2, GatewayError
from whale_core.devices.gui_agent.testing import FakeScreen, FakeTransport
from whale_core.devices.registry import DeviceRegistry
from whale_core.devices.transports.base import (
    ActionReceipt,
    ReceiptStatus,
    now_ms,
)
from whale_core.memory.store import InMemoryStore


def run(coro):
    return asyncio.run(coro)


def _registry(store, scopes=("device.tap", "device.observe", "device.shell")):
    registry = DeviceRegistry(store)
    pairing = registry.request_pairing(
        label="durable-test", pubkey_hex="ab" * 32, scopes=list(scopes))
    registry.approve(pairing["code"])
    return registry, pairing["device_id"]


class RecordingShellTransport(FakeTransport):
    """kind=ssh 的记录型假通道：execute 记账，可注入异常。"""

    def __init__(self, *, blow_up: bool = False):
        super().__init__([FakeScreen("home")], "home", kind="ssh")
        self._blow_up = blow_up

    async def capabilities(self, device_id):
        return ["device.shell"]

    async def execute(self, device_id, action):
        self.operations.append({"capability": action.capability,
                                "idempotency_key": action.idempotency_key})
        if self._blow_up:
            raise RuntimeError("socket lost after send")
        return ActionReceipt(
            action_id=action.action_id, status=ReceiptStatus.SUCCEEDED,
            summary="shell ok", completed_at=now_ms())


class TestDurableIdempotency:
    def test_terminal_receipt_persisted_with_route(self):
        store = InMemoryStore()
        registry, device_id = _registry(store)
        gw = DeviceGatewayV2(registry, store=store)
        gw.register_transport(RecordingShellTransport())
        receipt = run(gw.execute(device_id, "device.shell", {"command": "id"},
                                 idempotency_key="durable:k:0001"))
        assert receipt.status is ReceiptStatus.SUCCEEDED
        record = store.get(f"device:action:{device_id}:durable:k:0001")
        assert record is not None
        assert record["state"] == "succeeded"
        assert record["route"] == "ssh"
        assert record["receipt"]["status"] == "succeeded"
        # action_id 索引 + receipt 历史（对齐网关 schema）
        assert store.get(
            f"device:action-id:{device_id}:{record['action_id']}"
        ) == "durable:k:0001"
        history = store.get(f"device:receipts:{device_id}:{record['action_id']}")
        assert history and history[-1]["status"] == "succeeded"

    def test_restart_same_key_returns_recorded_never_replayed(self):
        """跨重启（新实例、同 store）：同键直回记录终态，transport 零调用。"""
        store = InMemoryStore()
        registry, device_id = _registry(store)
        gw1 = DeviceGatewayV2(registry, store=store)
        transport = RecordingShellTransport()
        gw1.register_transport(transport)
        first = run(gw1.execute(device_id, "device.shell", {"command": "id"},
                                idempotency_key="durable:k:0002"))
        assert len(transport.operations) == 1

        # 模拟进程重启：新编排器实例，甚至连 transport 都不注册
        gw2 = DeviceGatewayV2(registry, store=store)
        second = run(gw2.execute(device_id, "device.shell", {"command": "id"},
                                 idempotency_key="durable:k:0002"))
        assert second.action_id == first.action_id
        assert second.status is ReceiptStatus.SUCCEEDED
        assert second.provider_ref == "idempotent:replay-suppressed"
        assert len(transport.operations) == 1  # 绝不重放
        # action_status 同样跨实例可读
        status = gw2.action_status(device_id, "durable:k:0002")
        assert status is not None and status.action_id == first.action_id
        assert gw2.action_record(device_id, "durable:k:0002")["state"] == "succeeded"

    def test_transport_exception_unknown_persists_and_never_replayed(self):
        """传输异常 → unknown 落库；重启后同键仍回 unknown，不换 key 不重放。"""
        store = InMemoryStore()
        registry, device_id = _registry(store)
        gw1 = DeviceGatewayV2(registry, store=store)
        transport = RecordingShellTransport(blow_up=True)
        gw1.register_transport(transport)
        first = run(gw1.execute(device_id, "device.shell", {"command": "id"},
                                idempotency_key="durable:k:0003"))
        assert first.status is ReceiptStatus.UNKNOWN
        record = store.get(f"device:action:{device_id}:durable:k:0003")
        assert record["state"] == "unknown"

        gw2 = DeviceGatewayV2(registry, store=store)
        recovered = RecordingShellTransport()
        gw2.register_transport(recovered)  # 通道恢复了
        second = run(gw2.execute(device_id, "device.shell", {"command": "id"},
                                 idempotency_key="durable:k:0003"))
        assert second.status is ReceiptStatus.UNKNOWN
        assert second.action_id == first.action_id
        assert len(transport.operations) == 1
        assert not recovered.operations

    def test_pending_record_expires_to_unknown_readonly(self):
        """崩溃遗留 pending：过期后只读视图报 unknown（不回写、不重发）。"""
        store = InMemoryStore()
        registry, device_id = _registry(store)
        gw = DeviceGatewayV2(registry, store=store)
        # 手工种一条过期 pending（模拟 reserve 后进程崩溃）
        store.put(f"device:action:{device_id}:durable:k:0004", {
            "device_id": device_id, "idempotency_key": "durable:k:0004",
            "action_id": "act_orphan", "capability": "device.shell",
            "created_at": now_ms() - 120_000, "expires_at": now_ms() - 60_000,
            "state": "pending", "route": "ssh"})
        record = gw.action_record(device_id, "durable:k:0004")
        assert record["state"] == "unknown"
        assert record["outcome_source"] == "expires_at"
        # 库内仍是 pending（只读不翻案；retention sweep 才负责翻 unknown）
        assert store.get(
            f"device:action:{device_id}:durable:k:0004")["state"] == "pending"
        # 同键执行 → 对账返回（unknown 终态），不重新下发
        gw.register_transport(RecordingShellTransport())
        receipt = run(gw.execute(device_id, "device.shell", {"command": "id"},
                                 idempotency_key="durable:k:0004"))
        assert receipt.status is ReceiptStatus.UNKNOWN
        assert receipt.action_id == "act_orphan"

    def test_idem_key_length_enforced_in_durable_mode(self):
        store = InMemoryStore()
        registry, device_id = _registry(store)
        gw = DeviceGatewayV2(registry, store=store)
        gw.register_transport(RecordingShellTransport())
        for bad in ("short", "x" * 129):
            try:
                run(gw.execute(device_id, "device.shell", {"command": "id"},
                               idempotency_key=bad))
                raise AssertionError("应拒绝非法幂等键")
            except GatewayError as exc:
                assert exc.code == "invalid_message"

    def test_terminal_first_wins_no_overwrite(self):
        """终态落库不可翻案：迟到的失败回执不覆盖已记终态。"""
        store = InMemoryStore()
        registry, device_id = _registry(store)
        gw = DeviceGatewayV2(registry, store=store)
        gw.register_transport(RecordingShellTransport())
        first = run(gw.execute(device_id, "device.shell", {"command": "id"},
                               idempotency_key="durable:k:0005"))
        # 模拟一个迟到的 failed 试图翻案
        late = ActionReceipt(action_id=first.action_id,
                             status=ReceiptStatus.FAILED,
                             error_code="internal", completed_at=now_ms())
        gw._durable_finalize(device_id, "durable:k:0005", first.action_id,
                             late, "ssh")
        record = store.get(f"device:action:{device_id}:durable:k:0005")
        assert record["state"] == "succeeded"


class TestDurableScopeGate:
    def test_revoked_device_recorded_action_readable_but_execute_denied(self):
        """吊销后：旧记录仍只读可查，新执行被 scope 门控拒绝（不落新键）。"""
        store = InMemoryStore()
        registry, device_id = _registry(store)
        gw = DeviceGatewayV2(registry, store=store)
        gw.register_transport(RecordingShellTransport())
        run(gw.execute(device_id, "device.shell", {"command": "id"},
                       idempotency_key="durable:k:0006"))
        registry.revoke(device_id)
        # 历史记录只读仍在（对账不因吊销失忆）
        assert gw.action_record(device_id, "durable:k:0006") is not None
        # 新键执行被拒
        try:
            run(gw.execute(device_id, "device.shell", {"command": "id"},
                           idempotency_key="durable:k:0007"))
            raise AssertionError("应拒绝")
        except GatewayError as exc:
            assert exc.code == "device_revoked"
        assert store.get(f"device:action:{device_id}:durable:k:0007") is None


class TestUnifiedSurface:
    def test_capabilities_union_intersects_scopes(self):
        """capabilities = 各通道能力并集 ∩ registry scopes（真实有效视图）。"""
        store = InMemoryStore()
        registry, device_id = _registry(store, scopes=("device.shell",))
        gw = DeviceGatewayV2(registry, store=store)
        gw.register_transport(RecordingShellTransport())
        gui = FakeTransport([FakeScreen("home")], "home")
        gw.register_transport(gui)
        caps = run(gw.capabilities(device_id))
        assert caps == ["device.shell"]  # GUI 能力被 scopes 收窄

    def test_gateway_v2_transport_adapter_routes_and_persists(self):
        """GatewayV2Transport：GUI Agent 消费侧统一入口适配器。"""
        from whale_core.devices.gateway_v2 import GatewayV2Transport
        from whale_core.devices.gui_agent.testing import FakeTransition
        from whale_core.devices.transports.base import ActionRequest, new_id
        store = InMemoryStore()
        registry, device_id = _registry(store)
        gw = DeviceGatewayV2(registry, store=store)
        gui = FakeTransport(
            [FakeScreen("home", transitions=[
                FakeTransition("device.tap", {"x": 1}, "")])], "home")
        gw.register_transport(gui)
        adapter = GatewayV2Transport(gw)
        # GUI 能力选 embedded>adb>ssh：embedded/ssh 缺席 → adb(FakeTransport)
        req = ActionRequest(
            action_id=new_id("act"), capability="device.tap",
            arguments={"x": 1, "y": 2}, idempotency_key="durable:k:0008")
        receipt = run(adapter.execute(device_id, req))
        assert receipt.status is ReceiptStatus.SUCCEEDED
        assert gui.execute_calls == 1
        # adb 路由非自持久通道 → 编排层落了持久记录
        record = store.get(f"device:action:{device_id}:durable:k:0008")
        assert record is not None and record["route"] == "adb"
        # 健康聚合
        health = run(adapter.health(device_id))
        assert health.connected is True


class TestQueryRouting:
    def test_query_routes_to_transport_with_query_method(self):
        """query 统一入口：授权 + 能力匹配 + 委派通道 query。"""
        store = InMemoryStore()
        registry, device_id = _registry(store, scopes=("device.output.read",))

        class QueryTransport(RecordingShellTransport):
            kind = "embedded_wss"

            async def capabilities(self, device_id):
                return ["device.output.read"]

            async def query(self, device_id, *, operation, filters=None,
                            max_items=10, timeout_s=15.0):
                self.operations.append({"operation": operation})
                return {"status": "ok", "data": [{"text": "chunk"}]}

        gw = DeviceGatewayV2(registry, store=store)
        transport = QueryTransport()
        gw.register_transport(transport)
        result = run(gw.query(device_id, "device.output.read",
                              filters={"output_ref": "agent:out:act_x"},
                              max_items=1))
        assert result["status"] == "ok"
        assert transport.operations == [{"operation": "device.output.read"}]

    def test_query_no_channel_unavailable(self):
        store = InMemoryStore()
        registry, device_id = _registry(store, scopes=("device.output.read",))
        gw = DeviceGatewayV2(registry, store=store)
        try:
            run(gw.query(device_id, "device.output.read"))
            raise AssertionError("应抛 unavailable")
        except GatewayError as exc:
            assert exc.code == "unavailable"

    def test_query_scope_denied(self):
        store = InMemoryStore()
        registry, device_id = _registry(store, scopes=("device.shell",))
        gw = DeviceGatewayV2(registry, store=store)
        try:
            run(gw.query(device_id, "device.output.read"))
            raise AssertionError("应抛 scope_denied")
        except GatewayError as exc:
            assert exc.code == "scope_denied"
