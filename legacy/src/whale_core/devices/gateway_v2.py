"""Device Gateway V2：多 transport 编排器（devices-v2-design.md §4/§5）。

职责：
- 设备维度登记多个 transport（同一设备可同时有 adb/ssh/embedded_wss）；
- 按能力类别选路（GUI 类 embedded>adb>ssh；shell/文件类 ssh>embedded>adb）；
- 幂等派发：同 device_id+idempotency_key 直接回已记录终态，绝不重放；
- scope 检查走 registry（设备真相仍在 registry，transport 健康另报）；
- 能力不可用时跳过该通道；动作一经下发，不根据模糊失败自动跨通道重放。

持久化（#77）：构造注入 ``store``（生产 MemoryStore）后，动作记录落与
EmbeddedWssGateway 相同的 kv 域（``device:action:{device}:{idem}`` +
``device:action-id:`` 索引 + ``device:receipts:`` 历史），跨重启可查、
重复键不二次执行。embedded_wss 路由的记录由网关自身 reserve/落终态
（避免双占位冲突），adb/ssh 等非自持久通路由本类 reserve→finalize。
非自持久路由在 reserve 后进程崩溃时，记录保持 pending，过期按
``expires_at`` 规则读作 unknown（fail-closed，与网关 retention 语义一致）。

无 ``store`` 时退化为进程内幂等（开发/测试），回执有上限，不承担跨重启事实源。
"""
from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import Any, Mapping, Sequence

from whale_core.devices.action_records import TERMINAL_STATES
from whale_core.devices.capabilities import GUI_CAPS, SHELL_CAPS
from whale_core.devices.receipt_codec import receipt_from_record
from whale_core.devices.transports.base import (
    ActionReceipt,
    ActionRequest,
    DeviceTransport,
    Observation,
    ObserveRequest,
    ReceiptStatus,
    TransportHealth,
    new_id,
    now_ms,
)

log = logging.getLogger("whale.devices.gateway_v2")

# 选路优先级表（V2-D4/V2-D5）
_GUI_ORDER = ("embedded_wss", "adb", "ssh")
_SHELL_ORDER = ("ssh", "embedded_wss", "adb")
_DEFAULT_ORDER = ("embedded_wss", "adb", "ssh")

#: 与 EmbeddedWssGateway 相同的幂等键长度纪律（contracts/v2 §7.1）
_IDEM_MIN_LEN = 8
_IDEM_MAX_LEN = 128


class GatewayError(Exception):
    """code 对齐 contracts/v2 §14 错误码。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DeviceGatewayV2:
    def __init__(self, registry=None, store=None) -> None:
        # registry 可选：缺省跳过 scope 检查（开发/测试）；生产必须注入。
        # store 可选：缺省进程内幂等；生产必须注入（跨重启持久化，#77）。
        self.registry = registry
        self._store = store
        self._transports: dict[str, DeviceTransport] = {}
        self._receipts: OrderedDict[tuple[str, str], ActionReceipt] = OrderedDict()
        self._inflight: dict[tuple[str, str], asyncio.Task[ActionReceipt]] = {}
        self._receipt_routes: dict[tuple[str, str], tuple[DeviceTransport, str]] = {}
        self._reconciling: dict[tuple[str, str], asyncio.Task[ActionReceipt | None]] = {}
        self.max_cached_receipts = 4096

    # ---- transport 登记 ----

    def register_transport(self, transport: DeviceTransport) -> None:
        self._transports[transport.kind] = transport

    def unregister_transport(self, kind: str) -> DeviceTransport | None:
        """摘除一个通道（配置热重载用；不影响在飞任务，既有回执仍可对账）。"""
        return self._transports.pop(kind, None)

    @property
    def transports(self) -> Mapping[str, DeviceTransport]:
        return dict(self._transports)

    # ---- 选路 ----

    def _route_order(self, capability: str) -> Sequence[str]:
        if capability in SHELL_CAPS:
            return _SHELL_ORDER
        if capability in GUI_CAPS:
            return _GUI_ORDER
        return _DEFAULT_ORDER

    async def _pick(self, device_id: str, capability: str) -> DeviceTransport | None:
        for kind in self._route_order(capability):
            t = self._transports.get(kind)
            if t is None:
                continue
            try:
                caps = await t.capabilities(device_id)
            except Exception:  # noqa: BLE001 — 单通道失败不阻塞选路
                continue
            if capability in caps:
                return t
        return None

    async def capabilities(self, device_id: str) -> list[str]:
        """各通道实际能力并集 ∩ registry scopes（在线/已配置能力的真实视图）。"""
        out: set[str] = set()
        for t in self._transports.values():
            try:
                out |= set(await t.capabilities(device_id))
            except Exception:  # noqa: BLE001 — 单通道失败不阻塞整体视图
                continue
        if self.registry is not None:
            out &= set(self.registry.scopes_of(device_id))
        return sorted(out)

    # ---- 持久化动作记录（#77；kv 域与 EmbeddedWssGateway 相同） ----

    @staticmethod
    def _record_key(device_id: str, idempotency_key: str) -> str:
        return f"device:action:{device_id}:{idempotency_key}"

    def _check_idem(self, idempotency_key: str) -> None:
        if not isinstance(idempotency_key, str) or not (
                _IDEM_MIN_LEN <= len(idempotency_key) <= _IDEM_MAX_LEN):
            raise GatewayError("invalid_message",
                               "idempotency_key 长度须为 8..128")

    def action_record(self, device_id: str,
                      idempotency_key: str) -> dict | None:
        """只读持久动作记录（过期的非终态按 unknown 报，不回写、不重发）。"""
        if self._store is None:
            return None
        record = self._store.get(self._record_key(device_id, idempotency_key))
        if not isinstance(record, dict):
            return None
        result = dict(record)
        if (result.get("state") not in TERMINAL_STATES
                and now_ms() > int(result.get("expires_at") or 0)):
            result["state"] = "unknown"
            result["outcome_source"] = "expires_at"
        return result

    def _durable_reserve(self, device_id: str, idempotency_key: str,
                         action_id: str, capability: str, timeout_ms: int,
                         route: str, extra: Mapping[str, Any]) -> bool:
        """原子占位 pending 记录（schema 对齐 EmbeddedWssGateway.reserve）。"""
        key = self._record_key(device_id, idempotency_key)
        record = {
            "device_id": device_id, "idempotency_key": idempotency_key,
            "action_id": action_id, "capability": capability,
            "created_at": now_ms(), "expires_at": now_ms() + timeout_ms,
            "state": "pending", "route": route,
            **{k: v for k, v in extra.items() if v is not None},
        }

        def reserve(tx) -> bool:
            if tx.get(key) is not None:
                return False
            tx.put(key, record)
            tx.put(f"device:action-id:{device_id}:{action_id}", idempotency_key)
            return True

        return bool(self._store.transact(reserve))

    def _durable_finalize(self, device_id: str, idempotency_key: str,
                          action_id: str, receipt: ActionReceipt,
                          route: str) -> None:
        """回写终态 + 回执本体 + receipt 历史（终态 first-wins，§7.2）。"""
        key = self._record_key(device_id, idempotency_key)
        history_key = f"device:receipts:{device_id}:{action_id}"
        now = now_ms()

        def finalize(tx) -> None:
            record = tx.get(key)
            if not isinstance(record, dict):
                return
            if record.get("state") in TERMINAL_STATES:
                return  # 终态落库不可翻案
            record.update(state=receipt.status.value, updated_at=now, route=route)
            if receipt.status.terminal:
                result: dict[str, Any] = {}
                if receipt.summary:
                    result["summary"] = receipt.summary
                if receipt.output_ref:
                    result["output_ref"] = receipt.output_ref
                if receipt.verification is not None:
                    result["verification"] = {
                        "status": receipt.verification.status.value,
                        "summary": receipt.verification.summary}
                record["receipt"] = {
                    "status": receipt.status.value,
                    **({"task_id": receipt.task_id} if receipt.task_id else {}),
                    **({"result": result} if result else {}),
                    **({"error_code": receipt.error_code}
                       if receipt.error_code else {}),
                    **({"provider_ref": receipt.provider_ref}
                       if receipt.provider_ref else {}),
                    **({"started_at": receipt.started_at}
                       if receipt.started_at is not None else {}),
                    **({"completed_at": receipt.completed_at}
                       if receipt.completed_at is not None else {}),
                }
            tx.put(key, record)
            history = tx.get(history_key)
            history = history if isinstance(history, list) else []
            tx.put(history_key,
                   (history + [{"status": receipt.status.value, "ts": now}])[-32:])

        self._store.transact(finalize)

    # ---- 幂等 ----

    def action_status(self, device_id: str,
                      idempotency_key: str) -> ActionReceipt | None:
        """只读对账（绝不重发）。内存热路径未命中时读持久记录（跨重启）。"""
        seen = self._receipts.get((device_id, idempotency_key))
        if seen is not None:
            return seen
        record = self.action_record(device_id, idempotency_key)
        if record is None:
            return None
        return receipt_from_record(record, idempotency_key)

    def _remember(self, key, receipt, transport, capability) -> None:
        self._receipts[key] = receipt
        self._receipts.move_to_end(key)
        if transport is not None:
            self._receipt_routes[key] = (transport, capability)
        while len(self._receipts) > self.max_cached_receipts:
            evicted, _ = self._receipts.popitem(last=False)
            self._receipt_routes.pop(evicted, None)

    async def reconcile_action(self, device_id: str, idempotency_key: str) -> ActionReceipt | None:
        """Refresh in-flight/ambiguous receipts using the original channel's optional read-only API."""
        key = (device_id, idempotency_key)
        previous = self._receipts.get(key)
        route = self._receipt_routes.get(key)
        if previous is None and self._store is not None:
            # 跨重启：内存无记录，从持久层重建视图与路由
            record = self.action_record(device_id, idempotency_key)
            if record is None:
                return None
            previous = receipt_from_record(record, idempotency_key)
            route_kind = str(record.get("route") or "") or "embedded_wss"
            transport = self._transports.get(route_kind)
            if transport is not None:
                route = (transport, str(record.get("capability") or ""))
        if previous is None or route is None:
            return previous
        transport, capability = route
        if capability:
            self._authorize(device_id, capability)
        reader = getattr(transport, "action_status", None)
        if reader is None or previous.status in (
                ReceiptStatus.SUCCEEDED, ReceiptStatus.FAILED, ReceiptStatus.CANCELLED):
            return previous
        pending = self._reconciling.get(key)
        if pending is not None:
            return await asyncio.shield(pending)
        async def refresh():
            try:
                current = await reader(device_id, idempotency_key)
                if capability:
                    self._authorize(device_id, capability)
                if current is None:
                    return previous
                if current.action_id != previous.action_id and previous.provider_ref != "core:wait-timeout":
                    raise GatewayError("invalid_message", "动作对账返回了不同的 action_id")
                self._remember(key, current, transport, capability)
                return current
            finally:
                self._reconciling.pop(key, None)
        task = asyncio.create_task(refresh())
        self._reconciling[key] = task
        return await asyncio.shield(task)

    # ---- 执行 ----

    async def execute(
        self,
        device_id: str,
        capability: str,
        arguments: Mapping[str, Any],
        *,
        idempotency_key: str,
        timeout_ms: int = 10_000,
        task_id: str | None = None,
        run_id: str | None = None,
        observation_id: str | None = None,
        approval_ref: str | None = None,
        correlation_id: str | None = None,
        authorized_scope: Sequence[str] | None = None,
    ) -> ActionReceipt:
        self._authorize(device_id, capability)
        if self._store is not None:
            self._check_idem(idempotency_key)
            # 持久幂等：同键已有记录 → 回已记录状态，绝不重放（跨重启成立）
            if self.action_record(device_id, idempotency_key) is not None:
                return await self.reconcile_action(device_id, idempotency_key)

        # 幂等：同键已有记录 → 直接回真实状态
        seen = self._receipts.get((device_id, idempotency_key))
        if seen is not None:
            return await self.reconcile_action(device_id, idempotency_key)

        key = (device_id, idempotency_key)
        pending = self._inflight.get(key)
        if pending is not None:
            return await asyncio.shield(pending)

        async def dispatch() -> ActionReceipt:
            try:
                transport = await self._pick(device_id, capability)
                action_id = new_id("act")
                if transport is None:
                    receipt = ActionReceipt(
                        action_id=action_id, status=ReceiptStatus.FAILED,
                        task_id=task_id, summary=f"无可用通道执行 {capability}",
                        error_code="unavailable", completed_at=now_ms())
                else:
                    # 选路包含 await，期间设备可能被吊销或收回 scope。
                    self._authorize(device_id, capability)
                    request = ActionRequest(
                        action_id=action_id, capability=capability,
                        arguments=dict(arguments), idempotency_key=idempotency_key,
                        timeout_ms=timeout_ms, task_id=task_id, run_id=run_id,
                        observation_id=observation_id, approval_ref=approval_ref,
                        correlation_id=correlation_id,
                        authorized_scope=authorized_scope)
                    # embedded_wss 通道的持久化幂等由 EmbeddedWssGateway 自身承担
                    #（同 kv 域；本层再 reserve 会被网关判重而拒绝下发）；
                    # 其余通道由本层 reserve→finalize，跨重启可查。
                    owns_record = (self._store is not None
                                   and transport.kind != "embedded_wss")
                    if owns_record and not self._durable_reserve(
                            device_id, idempotency_key, action_id, capability,
                            timeout_ms, transport.kind,
                            {"task_id": task_id, "run_id": run_id,
                             "observation_id": observation_id,
                             "correlation_id": correlation_id}):
                        # 并发/崩溃恢复竞态：键已被占 → 回已记录状态
                        return await self.reconcile_action(
                            device_id, idempotency_key)
                    # 动作一旦下发，失败可能发生在副作用执行之后。
                    # 不自动跨通道重放；调用方依据回执进行后续观察或人工核对。
                    try:
                        receipt = await transport.execute(device_id, request)
                    except Exception as exc:
                        if getattr(exc, "code", None) == "invalid_message":
                            # 通道预检失败 = 调用方错误（动作从未下发，无副作用），
                            # 原样上抛映射 400；不记 unknown、不污染幂等终态。
                            raise
                        # 无法确认是否已发生副作用，记录 unknown，禁止同键重放。
                        receipt = ActionReceipt(
                            action_id=action_id, status=ReceiptStatus.UNKNOWN,
                            task_id=task_id, summary="传输异常，执行结果未知",
                            error_code="internal", completed_at=now_ms())
                    if owns_record:
                        try:
                            self._durable_finalize(device_id, idempotency_key,
                                                   action_id, receipt,
                                                   transport.kind)
                        except Exception:  # noqa: BLE001 — 持久化失败不掩盖回执
                            log.warning("动作记录终态落库失败 %s/%s",
                                        device_id, idempotency_key,
                                        exc_info=True)

                self._remember(key, receipt, transport, capability)
                return receipt
            finally:
                self._inflight.pop(key, None)

        task = asyncio.create_task(dispatch())
        self._inflight[key] = task
        return await asyncio.shield(task)

    def _authorize(self, device_id: str, capability: str) -> None:
        """观察与执行共用设备状态/scope 检查；开发时允许不注入 registry。"""
        if self.registry is not None:
            status = self.registry.status_of(device_id)
            if status != "active":
                raise GatewayError(
                    "device_revoked" if status == "revoked" else "unavailable",
                    f"设备状态 {status}")
            if capability not in set(self.registry.scopes_of(device_id)):
                raise GatewayError("scope_denied", f"设备未授予 {capability}")

    async def observe(self, device_id: str,
                      request: ObserveRequest | None = None) -> Observation:
        self._authorize(device_id, "device.observe")
        transport = await self._pick(device_id, "device.observe")
        if transport is None:
            raise GatewayError("unavailable", "无可用观察通道")
        self._authorize(device_id, "device.observe")
        return await transport.observe(device_id, request or ObserveRequest())

    async def query(self, device_id: str, operation: str, *,
                    filters: Mapping[str, Any] | None = None,
                    max_items: int = 10, timeout_s: float = 15.0) -> dict:
        """sensor.query 统一入口：路由到首个实现在线 ``query`` 的通道。

        口径与 REST 历史行为一致：查询只由 scope/registry 门控（§5.3 操作
        枚举在 REST 层已校验），不按 hello 声明能力过滤——embedded 网关从不
        按声明能力拒查询。``capabilities()`` 非空仅作在线判据（离线为空集）。
        """
        self._authorize(device_id, operation)
        for kind in _DEFAULT_ORDER:
            transport = self._transports.get(kind)
            query = getattr(transport, "query", None) if transport else None
            if query is None:
                continue
            try:
                caps = await transport.capabilities(device_id)
            except Exception:  # noqa: BLE001 — 单通道失败不阻塞选路
                continue
            if not caps:
                continue  # 离线/未连接
            self._authorize(device_id, operation)
            return await query(device_id, operation=operation,
                               filters=filters, max_items=max_items,
                               timeout_s=timeout_s)
        raise GatewayError("unavailable", f"无可用查询通道执行 {operation}")

    async def read_output(self, device_id: str, output_ref: str, *,
                          offset: int = 0,
                          max_bytes: int = 65536) -> tuple[bytes, bool]:
        """分页读取动作大输出；按通道优先级尝试，首个成功者返回（只读）。"""
        last: Exception | None = None
        for kind in _DEFAULT_ORDER:
            transport = self._transports.get(kind)
            if transport is None:
                continue
            try:
                return await transport.read_output(
                    device_id, output_ref, offset=offset, max_bytes=max_bytes)
            except Exception as exc:  # noqa: BLE001 — 尝试下一通道
                last = exc
        if last is not None:
            raise last
        raise GatewayError("unavailable", "无可用输出读取通道")

    async def health_report(self, device_id: str) -> dict[str, str]:
        """各通道健康状态（一种通道断开 ≠ 设备离线）。"""
        report: dict[str, str] = {}
        for kind, t in self._transports.items():
            try:
                h = await t.health(device_id)
                report[kind] = "connected" if h.connected else f"down:{h.detail}"
            except Exception as exc:  # noqa: BLE001
                report[kind] = f"error:{type(exc).__name__}"
        return report


class GatewayV2Transport:
    """把 DeviceGatewayV2 包成 DeviceTransport（#77 统一入口适配器）。

    GUI Agent（``devices/gui_agent/loop.py``）等按单通道接口消费的一方，
    经本适配器即获得多通道选路 + scope 门控 + 持久化幂等，无需各自接线。
    """

    kind = "gateway_v2"

    def __init__(self, gateway: DeviceGatewayV2) -> None:
        self._gw = gateway

    async def connect(self, device_id: str) -> None:
        return None

    async def disconnect(self, device_id: str) -> None:
        return None

    async def capabilities(self, device_id: str) -> Sequence[str]:
        return await self._gw.capabilities(device_id)

    async def observe(self, device_id: str, request: ObserveRequest) -> Observation:
        return await self._gw.observe(device_id, request)

    async def execute(self, device_id: str, action: ActionRequest) -> ActionReceipt:
        return await self._gw.execute(
            device_id, action.capability, dict(action.arguments),
            idempotency_key=action.idempotency_key,
            timeout_ms=action.timeout_ms, task_id=action.task_id,
            run_id=action.run_id, observation_id=action.observation_id,
            approval_ref=action.approval_ref,
            correlation_id=action.correlation_id)

    async def read_output(self, device_id: str, output_ref: str, *,
                          offset: int = 0,
                          max_bytes: int = 65536) -> tuple[bytes, bool]:
        return await self._gw.read_output(
            device_id, output_ref, offset=offset, max_bytes=max_bytes)

    async def health(self, device_id: str) -> TransportHealth:
        report = await self._gw.health_report(device_id)
        connected = any(v == "connected" for v in report.values())
        detail = ",".join(f"{k}={v}" for k, v in sorted(report.items()))
        return TransportHealth(connected=connected, detail=detail,
                               checked_at=now_ms())
