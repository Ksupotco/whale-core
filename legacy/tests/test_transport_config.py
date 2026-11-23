"""transport_config 校验与装配测试（#77：ADB/SSH 参数来自配置，不硬编码）。

纪律锚点：
- 「配置了地址」≠ 已授权——非 active 设备的配置项被拒绝/跳过；
- SSH 主机密钥 pinning 强制（fail-closed，禁止 accept-any）；
- serial/host/username 字符集白名单（transport 层 argv 参数化之外的第二闸）；
- key_path 只引用路径，私钥内容不进配置。
"""
from __future__ import annotations

import pytest

from whale_core.devices import transport_config as tc
from whale_core.devices.gateway_v2 import DeviceGatewayV2
from whale_core.devices.registry import DeviceRegistry
from whale_core.memory.store import InMemoryStore

DEV = "ab" * 16  # 32 hex


def _registry_with_active(store, scopes=("device.shell",)):
    registry = DeviceRegistry(store)
    pairing = registry.request_pairing(
        label="cfg-test", pubkey_hex="cd" * 32, scopes=list(scopes))
    registry.approve(pairing["code"])
    return registry, pairing["device_id"]


HOST_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyMaterialForTestsOnly00"


def _ssh_spec(**overrides):
    spec = {"host": "100.102.124.25", "port": 22, "username": "root",
            "key_path": "/root/.ssh/android", "expected_host_key": HOST_KEY}
    spec.update(overrides)
    return spec


class TestLoad:
    def test_missing_config_is_empty(self):
        assert tc.load_config(InMemoryStore()) == {}

    def test_malformed_type_rejected(self):
        store = InMemoryStore()
        store.put(tc.CONFIG_KEY, ["not-a-dict"])
        with pytest.raises(tc.TransportConfigError):
            tc.load_config(store)


class TestValidate:
    def test_adb_valid_entry(self):
        registry, device_id = _registry_with_active(InMemoryStore())
        out = tc.validate_config(
            {"adb": {device_id: {"serial": "100.92.91.85:5555"}}},
            registry)
        assert out["adb"][device_id]["serial"] == "100.92.91.85:5555"

    def test_adb_serial_charset_rejected(self):
        registry, device_id = _registry_with_active(InMemoryStore())
        with pytest.raises(tc.TransportConfigError):
            tc.validate_config(
                {"adb": {device_id: {"serial": "host; rm -rf /"}}}, registry)

    def test_unknown_kind_rejected(self):
        with pytest.raises(tc.TransportConfigError):
            tc.validate_config({"telnet": {DEV: {}}}, None)

    def test_unknown_device_rejected_strict_skipped_lenient(self):
        registry, _ = _registry_with_active(InMemoryStore())
        cfg = {"adb": {DEV: {"serial": "100.92.91.85:5555"}}}  # DEV 未配对
        with pytest.raises(tc.TransportConfigError):
            tc.validate_config(cfg, registry, strict=True)
        out = tc.validate_config(cfg, registry, strict=False)
        assert out["adb"] == {} and len(out["skipped"]) == 1
        assert out["skipped"][0]["kind"] == "adb"

    def test_revoked_device_rejected(self):
        """吊销设备不得配置通道（配置地址 ≠ 授权）。"""
        store = InMemoryStore()
        registry, device_id = _registry_with_active(store)
        registry.revoke(device_id)
        with pytest.raises(tc.TransportConfigError):
            tc.validate_config(
                {"adb": {device_id: {"serial": "100.92.91.85:5555"}}}, registry)

    def test_ssh_requires_host_key_pinning(self):
        registry, device_id = _registry_with_active(InMemoryStore())
        with pytest.raises(tc.TransportConfigError) as excinfo:
            tc.validate_config(
                {"ssh": {device_id: _ssh_spec(expected_host_key="")}}, registry)
        assert "expected_host_key" in str(excinfo.value)

    def test_ssh_valid_entry(self):
        registry, device_id = _registry_with_active(InMemoryStore())
        out = tc.validate_config({"ssh": {device_id: _ssh_spec()}}, registry)
        entry = out["ssh"][device_id]
        assert entry["host"] == "100.102.124.25" and entry["port"] == 22

    def test_ssh_jump_must_reference_configured_device(self):
        registry, device_id = _registry_with_active(InMemoryStore())
        with pytest.raises(tc.TransportConfigError):
            tc.validate_config(
                {"ssh": {device_id: _ssh_spec(jump="ff" * 16)}}, registry)

    def test_ssh_bad_host_rejected(self):
        registry, device_id = _registry_with_active(InMemoryStore())
        with pytest.raises(tc.TransportConfigError):
            tc.validate_config(
                {"ssh": {device_id: _ssh_spec(host="evil host;rm")}}, registry)

    def test_ssh_relative_key_path_rejected(self):
        registry, device_id = _registry_with_active(InMemoryStore())
        with pytest.raises(tc.TransportConfigError):
            tc.validate_config(
                {"ssh": {device_id: _ssh_spec(key_path="keys/android")}}, registry)

    def test_ssh_bad_port_rejected(self):
        registry, device_id = _registry_with_active(InMemoryStore())
        with pytest.raises(tc.TransportConfigError):
            tc.validate_config(
                {"ssh": {device_id: _ssh_spec(port=70000)}}, registry)


class TestAssembleApply:
    def test_assemble_adb_registers_serial_map(self):
        registry, device_id = _registry_with_active(InMemoryStore())
        config = tc.validate_config(
            {"adb": {device_id: {"serial": "100.92.91.85:5555"}}}, registry)
        built = tc.assemble_transports(config)
        assert set(built) == {"adb"}
        assert built["adb"].kind == "adb"

    def test_assemble_ssh_builds_targets(self):
        pytest.importorskip("asyncssh")
        registry, device_id = _registry_with_active(InMemoryStore())
        config = tc.validate_config({"ssh": {device_id: _ssh_spec()}}, registry)
        built = tc.assemble_transports(config)
        assert set(built) == {"ssh"}
        assert built["ssh"].kind == "ssh"

    def test_apply_replaces_config_kinds_keeps_embedded(self):
        """热重载：adb/ssh 被替换，embedded_wss 不受影响。"""
        pytest.importorskip("asyncssh")
        store = InMemoryStore()
        registry, device_id = _registry_with_active(store)
        gw = DeviceGatewayV2(registry, store=store)

        class _Embedded:
            kind = "embedded_wss"

        gw.register_transport(_Embedded())
        config = tc.validate_config({"ssh": {device_id: _ssh_spec()}}, registry)
        summary = tc.apply_transports(gw, config)
        assert "embedded_wss" in summary["registered"]
        assert "ssh" in summary["registered"]
        # 清空配置 → ssh 摘除，embedded 保留
        empty = tc.validate_config({}, registry)
        summary2 = tc.apply_transports(gw, empty)
        assert summary2["registered"] == ["embedded_wss"]
