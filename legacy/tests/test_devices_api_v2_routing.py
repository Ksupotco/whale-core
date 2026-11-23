"""REST → DeviceGatewayV2 统一选路 + /devices/transports 配置端点测试（#77）。

口径锚点：
- query/action 经编排器选路（不再直调 EmbeddedWssGateway）；幂等键跨重启
  持久化于同一 kv 域；重复键回 duplicate=True 且不二次执行；
- scope/吊销门控在编排层（registry 真相）；
- /devices/transports GET/PUT 校验 + 热装配（ssh 缺 pinning 拒绝）。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from whale_core.api.devices_api import _downlink, _transports_endpoint
from whale_core.devices import transport_config as tc
from whale_core.devices.gateway_v2 import DeviceGatewayV2
from whale_core.devices.gui_agent.testing import FakeScreen, FakeTransport
from whale_core.devices.registry import DeviceRegistry
from whale_core.devices.transports.base import (
    ActionReceipt,
    ReceiptStatus,
    now_ms,
)
from whale_core.memory.store import InMemoryStore

HOST_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyMaterialForTestsOnly00"


def _rig(scopes=("device.shell", "device.observe", "device.tap",
                 "device.output.read")):
    store = InMemoryStore()
    registry = DeviceRegistry(store)
    pairing = registry.request_pairing(
        label="api-route-test", pubkey_hex="ef" * 32, scopes=list(scopes))
    registry.approve(pairing["code"])
    device_id = pairing["device_id"]
    gw2 = DeviceGatewayV2(registry, store=store)
    return store, registry, gw2, device_id


class _ShellTransport(FakeTransport):
    def __init__(self):
        super().__init__([FakeScreen("home")], "home", kind="ssh")
        self.executed: list[str] = []

    async def capabilities(self, device_id):
        return ["device.shell"]

    async def execute(self, device_id, action):
        self.executed.append(action.idempotency_key)
        return ActionReceipt(action_id=action.action_id,
                             status=ReceiptStatus.SUCCEEDED,
                             summary="shell ok", completed_at=now_ms())


class Handler:
    def __init__(self, ctx, body):
        self.ctx = ctx
        self.body = body
        self.status = None
        self.response = None

    def _body_json(self):
        return self.body

    def _send(self, status, body):
        self.status = status
        self.response = json.loads(body)


def _ctx(store, gw2):
    return SimpleNamespace(store=store, device_gateway_v2=gw2,
                           log_event=lambda *args: None)


class TestActionViaGatewayV2:
    def test_action_routes_and_persists(self):
        store, registry, gw2, device_id = _rig()
        shell = _ShellTransport()
        gw2.register_transport(shell)
        handler = _ctx(store, gw2)
        h = Handler(handler, {"capability": "device.shell",
                              "arguments": {"command": "id"},
                              "idempotency_key": "rest:k:0001"})
        _downlink(h, object(), device_id, op="action")
        assert h.status == 200
        receipt = h.response["result"]["receipt"]
        assert receipt["status"] == "succeeded"
        assert receipt["result"]["summary"] == "shell ok"
        assert shell.executed == ["rest:k:0001"]
        # 持久化落库（跨重启事实源）
        record = store.get(f"device:action:{device_id}:rest:k:0001")
        assert record["state"] == "succeeded" and record["route"] == "ssh"

    def test_duplicate_key_returns_recorded_no_replay(self):
        store, registry, gw2, device_id = _rig()
        shell = _ShellTransport()
        gw2.register_transport(shell)
        ctx = _ctx(store, gw2)
        body = {"capability": "device.shell", "arguments": {"command": "id"},
                "idempotency_key": "rest:k:0002"}
        h1 = Handler(ctx, dict(body))
        _downlink(h1, object(), device_id, op="action")
        h2 = Handler(ctx, dict(body))
        _downlink(h2, object(), device_id, op="action")
        assert h2.status == 200
        receipt = h2.response["result"]["receipt"]
        assert receipt.get("duplicate") is True
        assert receipt["action_id"] == h1.response["result"]["receipt"]["action_id"]
        assert shell.executed == ["rest:k:0002"]  # 不二次执行

    def test_scope_denied_maps_403(self):
        store, registry, gw2, device_id = _rig(scopes=("device.observe",))
        gw2.register_transport(_ShellTransport())
        h = Handler(_ctx(store, gw2), {"capability": "device.shell",
                                       "arguments": {"command": "id"},
                                       "idempotency_key": "rest:k:0003"})
        _downlink(h, object(), device_id, op="action")
        assert h.status == 403
        assert store.get(f"device:action:{device_id}:rest:k:0003") is None

    def test_unknown_capability_rejected_400(self):
        store, registry, gw2, device_id = _rig()
        h = Handler(_ctx(store, gw2), {"capability": "device.notacap",
                                       "arguments": {},
                                       "idempotency_key": "rest:k:0004"})
        _downlink(h, object(), device_id, op="action")
        assert h.status == 400

    def test_short_idem_key_rejected_400(self):
        store, registry, gw2, device_id = _rig()
        gw2.register_transport(_ShellTransport())
        h = Handler(_ctx(store, gw2), {"capability": "device.shell",
                                       "arguments": {"command": "id"},
                                       "idempotency_key": "short"})
        _downlink(h, object(), device_id, op="action")
        assert h.status == 400
        assert h.response["error"]["code"] == "invalid_message"


class TestQueryViaGatewayV2:
    def test_query_routes_via_gw2(self):
        store, registry, gw2, device_id = _rig()

        class _EmbeddedQ(_ShellTransport):
            kind = "embedded_wss"

            async def capabilities(self, device_id):
                return ["device.output.read"]

            async def query(self, device_id, *, operation, filters=None,
                            max_items=10, timeout_s=15.0):
                self.executed.append(operation)
                return {"status": "ok", "data": [{"text": "rows"}]}

        q = _EmbeddedQ()
        gw2.register_transport(q)
        h = Handler(_ctx(store, gw2), {"operation": "device.output.read",
                                       "filters": {"output_ref": "x"},
                                       "max_items": 1})
        _downlink(h, object(), device_id, op="query")
        assert h.status == 200
        assert h.response["result"]["result"]["data"] == [{"text": "rows"}]
        assert q.executed == ["device.output.read"]


class TestTransportsEndpoint:
    def test_get_empty(self):
        store, registry, gw2, _ = _rig()
        h = Handler(_ctx(store, gw2), None)
        _transports_endpoint(h, h.ctx, registry, "GET", {})
        assert h.status == 200
        assert h.response["result"]["config"] == {}
        assert h.response["result"]["registered"] == []

    def test_put_validates_persists_and_hot_reloads(self):
        pytest.importorskip("asyncssh")
        store, registry, gw2, device_id = _rig()
        h = Handler(_ctx(store, gw2), {
            "config": {"ssh": {device_id: {
                "host": "100.102.124.25", "port": 22, "username": "root",
                "key_path": "/root/.ssh/android",
                "expected_host_key": HOST_KEY}}}})
        _transports_endpoint(h, h.ctx, registry, "PUT", {})
        assert h.status == 200
        assert "ssh" in h.response["result"]["registered"]
        # 已落 kv
        assert tc.load_config(store)["ssh"][device_id]["host"] == "100.102.124.25"
        assert "ssh" in gw2.transports

    def test_put_rejects_ssh_without_host_key(self):
        store, registry, gw2, device_id = _rig()
        h = Handler(_ctx(store, gw2), {
            "ssh": {device_id: {"host": "100.102.124.25", "port": 22,
                                "username": "root",
                                "key_path": "/root/.ssh/android"}}})
        _transports_endpoint(h, h.ctx, registry, "PUT", {})
        assert h.status == 400
        assert tc.load_config(store) == {}  # 拒绝不落库
        assert "ssh" not in gw2.transports

    def test_put_rejects_unknown_device(self):
        store, registry, gw2, _ = _rig()
        h = Handler(_ctx(store, gw2), {
            "adb": {"ff" * 16: {"serial": "100.92.91.85:5555"}}})
        _transports_endpoint(h, h.ctx, registry, "PUT", {})
        assert h.status == 400
        assert tc.load_config(store) == {}

    def test_method_not_allowed(self):
        store, registry, gw2, _ = _rig()
        h = Handler(_ctx(store, gw2), None)
        _transports_endpoint(h, h.ctx, registry, "DELETE", {})
        assert h.status == 405
