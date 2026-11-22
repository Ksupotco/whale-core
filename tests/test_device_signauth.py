"""REST 设备级 Ed25519 签名鉴权测试（/api/v2/* 面，#VoIP-unauthorized）。

护栏：fail-closed（无效签名 401）/时间戳窗+nonce 防重放服务端强制/
不动控制台 Bearer+cookie/设备签名不给管理权限（/api/v1 不吃此鉴权）。
"""
from __future__ import annotations

import json
import time
from http.client import HTTPConnection

import pytest

pytest.importorskip("cryptography")
pytest.importorskip("websockets")

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402

from whale_core.api.device_signauth import (
    verify_device_request,
    sweep_nonces,
    TIMESTAMP_WINDOW_S,
)
from whale_core.devices.registry import DeviceRegistry
from whale_core.memory.store import InMemoryStore


def _keypair():
    key = Ed25519PrivateKey.generate()
    pub_hex = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    return key, pub_hex


def _registry_with(store, pubkey_hex, status="active"):
    registry = DeviceRegistry(store)
    pairing = registry.request_pairing(
        label="sign-test", pubkey_hex=pubkey_hex, scopes=["device.observe"])
    device_id = pairing["device_id"]
    if status == "active":
        registry.approve(pairing["code"])
    elif status == "revoked":
        registry.approve(pairing["code"])
        registry.revoke(device_id)
    return registry, device_id


def _sign(key, device_id, ts, nonce, method, path):
    payload = f"{device_id}\n{ts}\n{nonce}\n{method}\n{path}".encode()
    return key.sign(payload).hex()


class _Headers(dict):
    def get(self, k, default=None):
        return super().get(k, default)


def _headers(device_id, ts, nonce, sig):
    return _Headers({
        "X-Whale-Device-Id": device_id, "X-Whale-Timestamp": str(ts),
        "X-Whale-Nonce": nonce, "X-Whale-Signature": sig})


class TestVerifyDeviceRequest:
    def test_valid_signature_passes(self):
        store = InMemoryStore()
        key, pub = _keypair()
        registry, device_id = _registry_with(store, pub)
        ts = int(time.time())
        nonce = "a" * 32
        sig = _sign(key, device_id, ts, nonce, "GET", "/api/v2/conversations")
        assert verify_device_request(
            store, registry, _headers(device_id, ts, nonce, sig),
            "GET", "/api/v2/conversations") is True

    def test_bad_signature_fails(self):
        store = InMemoryStore()
        key, pub = _keypair()
        registry, device_id = _registry_with(store, pub)
        other_key, _ = _keypair()
        ts = int(time.time())
        nonce = "b" * 32
        sig = _sign(other_key, device_id, ts, nonce, "GET", "/api/v2/conversations")
        assert verify_device_request(
            store, registry, _headers(device_id, ts, nonce, sig),
            "GET", "/api/v2/conversations") is False

    def test_replay_nonce_rejected(self):
        store = InMemoryStore()
        key, pub = _keypair()
        registry, device_id = _registry_with(store, pub)
        ts = int(time.time())
        nonce = "c" * 32
        sig = _sign(key, device_id, ts, nonce, "GET", "/api/v2/conversations")
        assert verify_device_request(
            store, registry, _headers(device_id, ts, nonce, sig),
            "GET", "/api/v2/conversations") is True
        # 同 nonce 重放即拒
        assert verify_device_request(
            store, registry, _headers(device_id, ts, nonce, sig),
            "GET", "/api/v2/conversations") is False

    def test_expired_timestamp_fails(self):
        store = InMemoryStore()
        key, pub = _keypair()
        registry, device_id = _registry_with(store, pub)
        ts = int(time.time()) - TIMESTAMP_WINDOW_S - 60
        nonce = "d" * 32
        sig = _sign(key, device_id, ts, nonce, "GET", "/api/v2/conversations")
        assert verify_device_request(
            store, registry, _headers(device_id, ts, nonce, sig),
            "GET", "/api/v2/conversations") is False

    def test_revoked_device_fails(self):
        store = InMemoryStore()
        key, pub = _keypair()
        registry, device_id = _registry_with(store, pub, status="revoked")
        ts = int(time.time())
        nonce = "e" * 32
        sig = _sign(key, device_id, ts, nonce, "GET", "/api/v2/conversations")
        assert verify_device_request(
            store, registry, _headers(device_id, ts, nonce, sig),
            "GET", "/api/v2/conversations") is False

    def test_missing_headers_fail(self):
        store = InMemoryStore()
        registry, _ = _registry_with(store, "ab" * 32)
        assert verify_device_request(
            store, registry, _Headers({}), "GET", "/api/v2/conversations") is False

    def test_nonce_sweep(self):
        store = InMemoryStore()
        key, pub = _keypair()
        registry, device_id = _registry_with(store, pub)
        ts = int(time.time())
        sig = _sign(key, device_id, ts, "f" * 32, "GET", "/x")
        assert verify_device_request(
            store, registry, _headers(device_id, ts, "f" * 32, sig),
            "GET", "/x") is True
        # 手工过期后 sweep 清掉
        for k in list(store.keys()):
            if k.startswith("device:signonce:"):
                store.put(k, 0)  # 远早于 now
        assert sweep_nonces(store) == 1


class TestServerIntegration:
    """走真实 HTTP 服务：/api/v2 设备签名可过、无签名 401、/api/v1 不受影响。"""

    @pytest.fixture()
    def api(self):
        from whale_core.api.server import ApiContext, WhaleApiServer
        ctx = ApiContext(store=InMemoryStore(), profile="demo")
        server = WhaleApiServer(ctx, port=0, token="console-token-123")
        server.start()
        yield server
        server.stop()

    def test_v2_signed_request_passes_and_unsigned_401(self, api):
        store = api.ctx.store
        key, pub = _keypair()
        registry, device_id = _registry_with(store, pub)
        api.ctx.device_registry = registry

        # 无签名 → 401
        conn = HTTPConnection("127.0.0.1", api.port, timeout=10)
        conn.request("GET", "/api/v2/conversations")
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 401
        conn.close()

        # 带签名 → 过鉴权（后续业务另说，只要不是 401）
        ts = int(time.time())
        nonce = "0" * 32
        sig = _sign(key, device_id, ts, nonce, "GET", "/api/v2/conversations")
        conn = HTTPConnection("127.0.0.1", api.port, timeout=10)
        conn.request("GET", "/api/v2/conversations", headers={
            "X-Whale-Device-Id": device_id, "X-Whale-Timestamp": str(ts),
            "X-Whale-Nonce": nonce, "X-Whale-Signature": sig})
        resp = conn.getresponse()
        resp.read()
        assert resp.status != 401
        conn.close()

    def test_v1_still_requires_console_bearer(self, api):
        """设备签名不给 /api/v1 管理面权限。"""
        store = api.ctx.store
        key, pub = _keypair()
        registry, device_id = _registry_with(store, pub)
        api.ctx.device_registry = registry
        ts = int(time.time())
        nonce = "1" * 32
        sig = _sign(key, device_id, ts, nonce, "GET", "/api/v1/status")
        conn = HTTPConnection("127.0.0.1", api.port, timeout=10)
        conn.request("GET", "/api/v1/status", headers={
            "X-Whale-Device-Id": device_id, "X-Whale-Timestamp": str(ts),
            "X-Whale-Nonce": nonce, "X-Whale-Signature": sig})
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 401
        conn.close()

    def test_console_bearer_still_works(self, api):
        conn = HTTPConnection("127.0.0.1", api.port, timeout=10)
        conn.request("GET", "/api/v1/status",
                     headers={"Authorization": "Bearer console-token-123"})
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 200
        conn.close()
