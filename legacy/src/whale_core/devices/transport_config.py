"""ADB/SSH transport 配置装载（#77）：连接参数来自配置/设备注册，不硬编码。

事实源：kv ``config:device_transports``（MemoryStore，值即 JSON 对象）：

.. code-block:: json

  {
    "adb": {"<device_id>": {"serial": "100.x.x.x:5555", "adb_path": "/usr/bin/adb"}},
    "ssh": {"<device_id>": {"host": "100.x.x.x", "port": 22, "username": "root",
                             "key_path": "/root/.ssh/android",
                             "expected_host_key": "ssh-ed25519 AAAA...",
                             "jump": "<other device_id>", "env_prefix": "export PATH=...",
                             "connect_timeout": 10.0}}
  }

纪律：
- 「配置了地址」≠ 已授权：配置项仅在设备已配对且 active 时生效；
  每个动作仍由 ``DeviceGatewayV2._authorize`` 逐项查 registry 状态与 scope。
- SSH 主机密钥 pinning 强制（缺 ``expected_host_key`` 直接拒绝；
  ``SshTransport`` 构造同样 fail-closed，双重闸门）。
- ``key_path`` 只引用文件路径，私钥内容绝不落 kv / 日志 / 回包。
- serial/host/username 等一律走 argv 参数化（transport 层无 shell 拼接），
  本层再做字符集白名单收敛。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Mapping

from whale_core.devices.sync_bridge import run_sync

log = logging.getLogger("whale.devices.transport_config")

#: 传输配置 kv 键（管理端 GET/PUT /devices/transports 维护）
CONFIG_KEY = "config:device_transports"

_DEVICE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SERIAL_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")
_HOST_RE = re.compile(r"^([A-Za-z0-9._\-]{1,253}|\[[0-9a-fA-F:]+\])$")
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")

#: 配置驱动的通道类型（热重载只替换这两类，embedded_wss 不受影响）
CONFIG_KINDS = ("adb", "ssh")


class TransportConfigError(ValueError):
    """传输配置被拒绝（code 对齐管理面错误码）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def load_config(store) -> dict:
    """读 kv 配置；缺失 → 空配置。类型异常抛 TransportConfigError。"""
    raw = store.get(CONFIG_KEY)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise TransportConfigError(
            "bad_request", f"{CONFIG_KEY} 须为对象（实际 {type(raw).__name__}）")
    return raw


def _check_device(device_id: str, registry) -> str | None:
    """配置项对应的设备必须已配对且 active；返回 None 表示通过。"""
    if not isinstance(device_id, str) or not _DEVICE_ID_RE.match(device_id):
        return "device_id 须为 32 位小写 hex"
    if registry is not None:
        status = registry.status_of(device_id)
        if status != "active":
            return f"设备状态 {status}（须先配对并激活）"
    return None


def _validate_adb(spec: Any) -> dict:
    if not isinstance(spec, dict):
        raise TransportConfigError("bad_request", "adb 配置项须为对象")
    serial = spec.get("serial")
    if not isinstance(serial, str) or not _SERIAL_RE.match(serial):
        raise TransportConfigError(
            "bad_request", "adb serial 须为 1..128 位 [A-Za-z0-9._:-]")
    out: dict[str, Any] = {"serial": serial}
    adb_path = spec.get("adb_path")
    if adb_path is not None:
        if (not isinstance(adb_path, str) or not adb_path.startswith("/")
                or len(adb_path) > 256 or not re.fullmatch(r"[A-Za-z0-9._/\-]+", adb_path)):
            raise TransportConfigError(
                "bad_request", "adb_path 须为绝对路径（仅 [A-Za-z0-9._/-]）")
        out["adb_path"] = adb_path
    return out


def _validate_ssh(spec: Any, ssh_map: Mapping[str, Any]) -> dict:
    if not isinstance(spec, dict):
        raise TransportConfigError("bad_request", "ssh 配置项须为对象")
    host = spec.get("host")
    if not isinstance(host, str) or not _HOST_RE.match(host):
        raise TransportConfigError(
            "bad_request", "ssh host 须为主机名/IP（无空白与 shell 元字符）")
    port = spec.get("port", 22)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise TransportConfigError("bad_request", "ssh port 须在 1..65535")
    username = spec.get("username", "root")
    if not isinstance(username, str) or not _USERNAME_RE.match(username):
        raise TransportConfigError("bad_request", "ssh username 非法")
    key_path = spec.get("key_path")
    if not isinstance(key_path, str) or not key_path.startswith("/"):
        raise TransportConfigError(
            "bad_request", "ssh key_path 须为绝对路径（私钥内容不落配置）")
    host_key = spec.get("expected_host_key")
    if not isinstance(host_key, str) or not host_key.startswith(("ssh-", "ecdsa-")):
        # fail-closed：主机密钥 pinning 是硬要求（SshTransport 再查一次）
        raise TransportConfigError(
            "bad_request", "ssh 必须提供 expected_host_key（禁止 accept-any）")
    out: dict[str, Any] = {
        "host": host, "port": port, "username": username,
        "key_path": key_path, "expected_host_key": host_key,
    }
    jump = spec.get("jump")
    if jump is not None:
        if not isinstance(jump, str) or jump not in ssh_map:
            raise TransportConfigError(
                "bad_request", f"ssh jump 须为配置内另一 device_id（实际 {jump!r}）")
        out["jump"] = jump
    env_prefix = spec.get("env_prefix")
    if env_prefix is not None:
        if not isinstance(env_prefix, str) or len(env_prefix) > 500:
            raise TransportConfigError(
                "bad_request", "ssh env_prefix 须为 ≤500 字符文本")
        out["env_prefix"] = env_prefix
    timeout = spec.get("connect_timeout")
    if timeout is not None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) \
                or not 0.1 <= float(timeout) <= 120:
            raise TransportConfigError(
                "bad_request", "ssh connect_timeout 须在 0.1..120 秒")
        out["connect_timeout"] = float(timeout)
    return out


def validate_config(config: Mapping[str, Any], registry=None, *,
                    strict: bool = True) -> dict:
    """校验+规范化配置。

    strict=True（管理端 PUT）：任何非法项拒绝整份配置（TransportConfigError）。
    strict=False（服务启动装配）：非法/未授权设备跳过并记入 ``skipped``，
    其余通道照常装配（单设备配置错误不阻塞整体上线）。
    """
    if not isinstance(config, Mapping):
        raise TransportConfigError("bad_request", "配置须为对象")
    for key in config:
        if key not in CONFIG_KINDS:
            raise TransportConfigError(
                "bad_request", f"未知传输类别 {key!r}（允许: {list(CONFIG_KINDS)}）")
    ssh_map = config.get("ssh") or {}
    if not isinstance(ssh_map, Mapping):
        raise TransportConfigError("bad_request", "ssh 配置须为 {device_id: 参数} 对象")
    adb_map = config.get("adb") or {}
    if not isinstance(adb_map, Mapping):
        raise TransportConfigError("bad_request", "adb 配置须为 {device_id: 参数} 对象")

    out: dict[str, Any] = {"adb": {}, "ssh": {}, "skipped": []}
    for kind, mapping, validator in (
            ("adb", adb_map, lambda spec: _validate_adb(spec)),
            ("ssh", ssh_map, lambda spec: _validate_ssh(spec, ssh_map))):
        for device_id, spec in mapping.items():
            problem = _check_device(device_id, registry)
            try:
                entry = validator(spec) if problem is None else None
            except TransportConfigError as exc:
                problem = str(exc)
                entry = None
            if problem is not None:
                if strict:
                    raise TransportConfigError(
                        "bad_request", f"{kind}/{device_id}: {problem}")
                log.warning("跳过传输配置 %s/%s：%s", kind, device_id, problem)
                out["skipped"].append(
                    {"kind": kind, "device_id": str(device_id), "reason": problem})
                continue
            out[kind][device_id] = entry
    return out


def assemble_transports(config: Mapping[str, Any]) -> dict[str, Any]:
    """把已校验配置实例化为 transport（adb/ssh；空类别不出现在返回值）。

    asyncssh 为可选依赖：缺失时跳过 ssh（warning），不阻塞 adb/embedded。
    """
    built: dict[str, Any] = {}
    adb_map = config.get("adb") or {}
    if adb_map:
        from whale_core.devices.transports.adb import AdbTransport
        serials = {dev: spec["serial"] for dev, spec in adb_map.items()}
        paths = {spec["adb_path"] for spec in adb_map.values()
                 if spec.get("adb_path")}
        if len(paths) > 1:
            log.warning("多个 adb_path 配置 %s，仅第一个生效", sorted(paths))
        kwargs: dict[str, Any] = {}
        if paths:
            kwargs["adb_path"] = sorted(paths)[0]
        built["adb"] = AdbTransport(serials, **kwargs)
    ssh_map = config.get("ssh") or {}
    if ssh_map:
        try:
            from whale_core.devices.transports.ssh import (
                SshDeviceTarget,
                SshTransport,
            )
        except ImportError:
            log.warning("asyncssh 不可用，跳过 ssh transport 装配")
        else:
            targets = {
                dev: SshDeviceTarget(
                    host=spec["host"], port=spec["port"],
                    username=spec["username"], key_path=spec["key_path"],
                    expected_host_key=spec["expected_host_key"],
                    jump=spec.get("jump"),
                    env_prefix=spec.get("env_prefix", ""),
                    connect_timeout=spec.get("connect_timeout", 10.0))
                for dev, spec in ssh_map.items()
            }
            built["ssh"] = SshTransport(targets)
    return built


def _close_old(old, device_ids) -> None:
    """热替换前尽力关闭旧通道连接（失败不阻塞装配；在飞动作持有旧对象引用，
    其执行与回执不受影响）。"""
    for device_id in device_ids:
        try:
            run_sync(old.disconnect(device_id))
        except Exception:  # noqa: BLE001 — 尽力而为，断不开不阻塞新配置生效
            log.debug("旧通道 %s 断开 %s 失败（忽略）",
                      getattr(old, "kind", "?"), device_id, exc_info=True)


def apply_transports(gateway_v2, config: Mapping[str, Any]) -> dict:
    """把配置驱动的 adb/ssh transport 热替换进 DeviceGatewayV2。

    embedded_wss 不动；旧通道连接尽力断开；返回装配摘要（审计用）。
    """
    replaced: dict[str, list[str]] = {}
    for kind in CONFIG_KINDS:
        old = gateway_v2.unregister_transport(kind)
        if old is None:
            continue
        mapping = getattr(old, "_serials", None) or getattr(old, "_targets", None)
        device_ids = list(mapping.keys()) if isinstance(mapping, dict) else []
        _close_old(old, device_ids)
        replaced[kind] = device_ids
    built = assemble_transports(config)
    for transport in built.values():
        gateway_v2.register_transport(transport)
    return {
        "registered": sorted(gateway_v2.transports.keys()),
        "configured": {kind: sorted((config.get(kind) or {}).keys())
                       for kind in CONFIG_KINDS},
        "replaced": replaced,
    }
