"""#60 设备管理端点测试：重命名/角色/暂停/恢复/回执列表/活动聚合。

口径锚点：
- 暂停 = 注册保留 + 动作/查询 fail-closed（网关与编排器同口径），可恢复；
- 吊销 ≠ 暂停：吊销不可 resume，须重新配对；
- 回执列表来自 kv 动作记录（真实事实源），过期非终态按 unknown 呈现；
- 活动聚合（uplink 感知事件 / downlink 动作）全部来自 kv，无静态假数据。
"""
from __future__ import annotations

import json
import time
from http.client import HTTPConnection

import pytest

pytest.importorskip("websockets")
pytest.importorskip("cryptography")

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from whale_core.api.server import ApiContext, WhaleApiServer  # noqa: E402
from whale_core.devices.registry import DeviceRegistry  # noqa: E402
from whale_core.devices.transports.base import now_ms  # noqa: E402

TOKEN = "mgmt-test-token"


def _now():
    return int(time.time() * 1000)


@pytest.fixture()
def api():
    from whale_core.memory.store import InMemoryStore
    ctx = ApiContext(store=InMemoryStore(), profile="demo")
    server = WhaleApiServer(ctx, port=0, token=TOKEN)
    server.start()
    yield server
    server.stop()


def _req(server, method, path, *, body=None):
    conn = HTTPConnection("127.0.0.1", server.port, timeout=15)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    payload = json.dumps(body) if body is not None else None
    if payload:
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    return resp.status, json.loads(raw.decode("utf-8"))


def _pair(api, label="mgmt-dev", scopes=("device.shell", "device.observe")):
    key = Ed25519PrivateKey.generate()
    status, body = _req(api, "POST", "/api/v1/devices/pair",
                        body={"label": label,
                              "pubkey_hex": key.public_key().public_bytes_raw().hex(),
                              "scopes": list(scopes)})
    assert status == 200, body
    device_id = body["result"]["device_id"]
    status, body = _req(api, "POST", "/api/v1/devices/pair/approve",
                        body={"code": body["result"]["code"]})
    assert status == 200, body
    return device_id


class TestRenameRole:
    def test_rename_and_role(self, api):
        device_id = _pair(api)
        status, body = _req(api, "POST", f"/api/v1/devices/{device_id}/rename",
                            body={"label": "叶子5号"})
        assert status == 200 and body["result"]["label"] == "叶子5号"
        status, body = _req(api, "POST", f"/api/v1/devices/{device_id}/role",
                            body={"role": "root-phone"})
        assert status == 200 and body["result"]["role"] == "root-phone"
        # 详情透出
        status, body = _req(api, "GET", f"/api/v1/devices/{device_id}")
        assert body["result"]["label"] == "叶子5号"
        assert body["result"]["role"] == "root-phone"
        # 清除角色
        status, body = _req(api, "POST", f"/api/v1/devices/{device_id}/role",
                            body={"role": ""})
        assert "role" not in body["result"]

    def test_rename_invalid_and_unknown(self, api):
        device_id = _pair(api)
        status, _ = _req(api, "POST", f"/api/v1/devices/{device_id}/rename",
                         body={"label": "  "})
        assert status == 400
        status, _ = _req(api, "POST", f"/api/v1/devices/{'ab' * 16}/rename",
                         body={"label": "x"})
        assert status == 404
        status, _ = _req(api, "POST", f"/api/v1/devices/{device_id}/role",
                         body={"role": "x" * 33})
        assert status == 400
        status, _ = _req(api, "POST", f"/api/v1/devices/{device_id}/role",
                         body={"role": "bad\nrole"})
        assert status == 400


class TestPauseResume:
    def test_pause_gates_action_and_query_fail_closed(self, api):
        device_id = _pair(api)
        status, body = _req(api, "POST", f"/api/v1/devices/{device_id}/pause")
        assert status == 200 and body["result"]["status"] == "paused"
        # 动作被拒（编排层 unavailable/409；offline 亦然——总之不是 200 成功）
        status, body = _req(api, "POST", f"/api/v1/devices/{device_id}/action",
                            body={"capability": "device.shell",
                                  "arguments": {"command": "id"},
                                  "idempotency_key": "pause-test-0001"})
        assert status == 409 and body["error"]["code"] == "unavailable"
        # 查询同样被拒
        status, body = _req(api, "POST", f"/api/v1/devices/{device_id}/query",
                            body={"operation": "device.status"})
        assert status == 409
        # 未落任何动作记录（fail-closed 不占键）
        assert api.ctx.store.get(
            f"device:action:{device_id}:pause-test-0001") is None
        # 恢复后可再授权执行（设备离线 → 明确 failed/unavailable，不再是门控 409）
        status, body = _req(api, "POST", f"/api/v1/devices/{device_id}/resume")
        assert status == 200 and body["result"]["status"] == "active"
        status, body = _req(api, "POST", f"/api/v1/devices/{device_id}/action",
                            body={"capability": "device.shell",
                                  "arguments": {"command": "id"},
                                  "idempotency_key": "pause-test-0002"})
        assert status == 200
        assert body["result"]["receipt"]["status"] == "failed"
        assert body["result"]["receipt"]["error_code"] == "unavailable"

    def test_pause_state_machine_conflicts(self, api):
        device_id = _pair(api)
        # active → pause → pause 冲突；resume → resume 冲突
        assert _req(api, "POST", f"/api/v1/devices/{device_id}/pause")[0] == 200
        assert _req(api, "POST", f"/api/v1/devices/{device_id}/pause")[0] == 409
        assert _req(api, "POST", f"/api/v1/devices/{device_id}/resume")[0] == 200
        assert _req(api, "POST", f"/api/v1/devices/{device_id}/resume")[0] == 409

    def test_revoked_cannot_resume(self, api):
        device_id = _pair(api)
        _req(api, "POST", f"/api/v1/devices/{device_id}/pause")
        _req(api, "POST", f"/api/v1/devices/{device_id}/revoke")
        status, _ = _req(api, "POST", f"/api/v1/devices/{device_id}/resume")
        assert status == 409


class TestReceiptsAndActivity:
    def test_receipts_listing_and_expiry_unknown(self, api):
        device_id = _pair(api)
        store = api.ctx.store
        # 两条终态 + 一条过期 pending（应呈现 unknown/expires_at）
        for i, state in enumerate(("succeeded", "failed")):
            store.put(f"device:action:{device_id}:key-{i:04d}", {
                "device_id": device_id, "idempotency_key": f"key-{i:04d}",
                "action_id": f"act_{i}", "capability": "device.shell",
                "created_at": _now() - 1000 * (10 - i),
                "updated_at": _now() - 1000 * (10 - i),
                "expires_at": _now() + 60_000, "state": state, "route": "ssh",
                "receipt": {"status": state, "result": {"summary": f"s{i}"}}})
        store.put(f"device:action:{device_id}:key-stale", {
            "device_id": device_id, "idempotency_key": "key-stale",
            "action_id": "act_stale", "capability": "device.shell",
            "created_at": _now() - 120_000, "expires_at": _now() - 60_000,
            "state": "pending", "route": "embedded_wss"})
        status, body = _req(api, "GET", f"/api/v1/devices/{device_id}/receipts")
        assert status == 200
        receipts = body["result"]["receipts"]
        assert len(receipts) == 3
        # 新→旧排序：最新一条是 failed（updated 最大）
        assert receipts[0]["state"] == "failed"
        stale = [r for r in receipts if r["idempotency_key"] == "key-stale"][0]
        assert stale["state"] == "unknown"
        assert stale["outcome_source"] == "expires_at"
        # 库内未被改写（只读视图）
        assert store.get(f"device:action:{device_id}:key-stale")["state"] == "pending"

    def test_activity_aggregates_real_kv(self, api):
        device_id = _pair(api)
        store = api.ctx.store
        store.put(f"device:event:{device_id}:evt_1", {
            "device_id": device_id, "event_id": "evt_1", "kind": "screen",
            "received_at": _now() - 5000})
        store.put(f"device:event:{device_id}:evt_2", {
            "device_id": device_id, "event_id": "evt_2", "kind": "screen",
            "received_at": _now() - 1000})
        store.put(f"device:action:{device_id}:inflight-1", {
            "device_id": device_id, "idempotency_key": "inflight-1",
            "action_id": "act_x", "capability": "device.shell",
            "created_at": _now() - 3000, "expires_at": _now() + 60_000,
            "state": "pending", "route": "ssh"})
        store.put(f"device:action:{device_id}:done-1", {
            "device_id": device_id, "idempotency_key": "done-1",
            "action_id": "act_y", "capability": "device.tap",
            "created_at": _now() - 1000, "updated_at": _now() - 1000,
            "expires_at": _now() + 60_000, "state": "succeeded",
            "route": "adb", "receipt": {"status": "succeeded"}})
        status, body = _req(api, "GET", f"/api/v1/devices/{device_id}")
        assert status == 200
        activity = body["result"]["activity"]
        assert activity["uplink"]["events"] == 2
        assert activity["uplink"]["last_at"] is not None
        assert activity["downlink"]["pending"] == 1
        assert activity["downlink"]["last_receipt"]["state"] == "succeeded"

    def test_receipts_limit_validation(self, api):
        device_id = _pair(api)
        assert _req(api, "GET",
                    f"/api/v1/devices/{device_id}/receipts?limit=0")[0] == 400
        assert _req(api, "GET",
                    f"/api/v1/devices/{device_id}/receipts?limit=51")[0] == 400
        assert _req(api, "GET",
                    f"/api/v1/devices/{device_id}/receipts?limit=abc")[0] == 400

    def test_list_includes_role(self, api):
        device_id = _pair(api)
        _req(api, "POST", f"/api/v1/devices/{device_id}/role",
             body={"role": "glasses"})
        status, body = _req(api, "GET", "/api/v1/devices")
        assert status == 200
        dev = [d for d in body["result"]["devices"]
               if d["device_id"] == device_id][0]
        assert dev["role"] == "glasses"
