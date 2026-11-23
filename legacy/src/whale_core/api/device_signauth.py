"""REST 设备级 Ed25519 签名鉴权（/api/v2/* 面；与 WSS challenge-response 同模型）。

设备请求头：
- ``X-Whale-Device-Id``：32-hex device_id
- ``X-Whale-Timestamp``：Unix 秒（|now-ts|≤300s 窗口，防重放）
- ``X-Whale-Nonce``：一次性随机（hex/base64 串，16..128 字符，服务端 5min 记重放拒）
- ``X-Whale-Signature``：Ed25519 签名（对 ``device_id + "\n" + ts + "\n" + nonce +
  "\n" + METHOD + "\n" + path`` 的 UTF-8 字节），128-hex

纪律：fail-closed（任何一步不过→False，调用方 401）；不破坏控制台 Bearer/cookie；
nonce 记 kv 防重放；吊销/非 active 设备拒。本鉴权给「设备读自己」级别，不给管理权限。
"""
from __future__ import annotations

import time

#: 时间戳容差窗（秒）
TIMESTAMP_WINDOW_S = 300
#: nonce 记账保留（毫秒）
NONCE_TTL_MS = 5 * 60 * 1000

_REQUIRED_HEADERS = (
    "X-Whale-Device-Id", "X-Whale-Timestamp", "X-Whale-Nonce", "X-Whale-Signature")


def _signed_payload(device_id: str, timestamp: str, nonce: str,
                    method: str, path: str) -> bytes:
    return f"{device_id}\n{timestamp}\n{nonce}\n{method}\n{path}".encode("utf-8")


def verify_device_request(store, registry, headers, method: str, path: str,
                          *, now: float | None = None) -> bool:
    """四头签名鉴权全链核验。任何一步不过 → False（fail-closed）。

    headers：http.client.HTTPMessage / Mapping 均可（``headers.get`` 口径）。
    store：MemoryStore（nonce 记账）；registry：DeviceRegistry（设备态+公钥）。
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
        from cryptography.exceptions import InvalidSignature
    except ImportError:
        return False

    now = time.time() if now is None else now
    get = headers.get
    device_id = (get(_REQUIRED_HEADERS[0]) or "").strip().lower()
    timestamp_raw = (get(_REQUIRED_HEADERS[1]) or "").strip()
    nonce = (get(_REQUIRED_HEADERS[2]) or "").strip()
    signature_hex = (get(_REQUIRED_HEADERS[3]) or "").strip().lower()

    # 形状闸门
    if not device_id or len(device_id) != 32:
        return False
    if any(c not in "0123456789abcdef" for c in device_id):
        return False
    if not timestamp_raw.isdigit():
        return False
    if not 16 <= len(nonce) <= 128:
        return False
    if len(signature_hex) != 128:
        return False

    # 时间戳窗
    if abs(now - int(timestamp_raw)) > TIMESTAMP_WINDOW_S:
        return False

    # 设备态（吊销/非 active 即拒）
    meta = registry.get(device_id)
    if not isinstance(meta, dict) or meta.get("status") != "active":
        return False
    pubkey_hex = meta.get("pubkey_hex")
    if not isinstance(pubkey_hex, str) or len(pubkey_hex) != 64:
        return False

    # nonce 防重放（原子记账：已见即拒）
    nonce_key = f"device:signonce:{device_id}:{nonce}"
    def _claim(tx):
        if tx.get(nonce_key) is not None:
            return False
        tx.put(nonce_key, int(now * 1000))
        return True
    if not store.transact(_claim):
        return False

    # 验签
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey_hex)).verify(
            bytes.fromhex(signature_hex),
            _signed_payload(device_id, timestamp_raw, nonce, method, path))
    except (InvalidSignature, ValueError):
        return False
    return True


def sweep_nonces(store, *, now_ms: int | None = None) -> int:
    """清理过期 nonce 记账（>TTL），返回删除数。供网关 sweep 周期调用。"""
    from whale_core.devices.transports.base import now_ms as _now_ms
    now = _now_ms() if now_ms is None else now_ms
    deleted = 0
    for key in list(store.keys()):
        if not key.startswith("device:signonce:"):
            continue
        seen = store.get(key)
        if isinstance(seen, (int, float)) and now - int(seen) > NONCE_TTL_MS:
            store.delete(key)
            deleted += 1
    return deleted
