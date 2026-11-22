"""设备 WS 网关随服务启动 + 健康面 relay 存活闸（2026-11-22 懒装配静默消失实证）。"""
from __future__ import annotations

import json
from http.client import HTTPConnection

import pytest

pytest.importorskip("websockets")
pytest.importorskip("cryptography")

from whale_core.api.server import ApiContext, WhaleApiServer  # noqa: E402
from whale_core.memory.store import InMemoryStore  # noqa: E402

TOKEN = "relay-live-token-1"


@pytest.fixture()
def api():
    ctx = ApiContext(store=InMemoryStore(), profile="demo")
    server = WhaleApiServer(ctx, port=0, token=TOKEN)
    server.start()
    yield server
    server.stop()


def _get(api, path):
    conn = HTTPConnection("127.0.0.1", api.port, timeout=10)
    conn.request("GET", path, headers={"Authorization": f"Bearer {TOKEN}"})
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp.status, json.loads(body)


class TestGatewayEagerStart:
    def test_start_brings_relay_listener(self, api):
        """server.start() 后设备 relay 端口必须已在听（不再等首个 REST 调用）。"""
        import socket
        gateway = getattr(api.ctx, "device_gateway", None)
        assert gateway is not None, "网关应随 start 就位"
        assert gateway.port > 0
        with socket.create_connection(("127.0.0.1", gateway.port), timeout=3):
            pass  # 能建 TCP 即在听

    def test_health_reports_relay_liveness(self, api):
        status, body = _get(api, "/api/v1/health")
        assert status == 200
        relay = body["result"].get("device_relay")
        assert relay is not None, "健康面应带 device_relay"
        assert relay["listening"] is True
        assert relay["port"] > 0

    def test_health_still_ok_when_gateway_absent(self):
        """无设备依赖环境健康面不回 500（additive 字段，缺省不出现）。"""
        ctx = ApiContext(store=InMemoryStore(), profile="demo")
        server = WhaleApiServer(ctx, port=0, token=TOKEN)
        server.start()
        try:
            # 手工拆掉网关模拟懒装配/降级环境
            ctx.device_gateway = None
            status, body = _get(server, "/api/v1/health")
            assert status == 200
            assert body["result"]["ok"] is True
        finally:
            server.stop()
