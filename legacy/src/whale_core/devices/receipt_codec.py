"""ActionReceipt 编解码（contracts/v2 §7）：payload/持久记录 → 运行时回执。

单一事实源：此前映射逻辑散落在 transports/embedded_wss.py 内部；#77 统一
选路后 DeviceGatewayV2 的持久化幂等记录也要映射成同一形状，故抽为独立模块
（transports.embedded_wss 保持 ``_receipt_from_payload`` 别名兼容）。
"""
from __future__ import annotations

from typing import Mapping

from whale_core.devices.transports.base import (
    ActionReceipt,
    ActionRequest,
    ReceiptStatus,
    Verification,
    VerificationStatus,
    now_ms,
)


def receipt_from_payload(action: ActionRequest, payload: Mapping) -> ActionReceipt:
    """v2 action.receipt payload / 幂等命中记录 → ActionReceipt。

    幂等命中（duplicate=True）时入参是动作记录（state + 内嵌 receipt），
    同样映射到真实终态，绝不重放（§7.1）。
    """
    payload = dict(payload)
    if payload.get("duplicate"):
        record = payload
        embedded = record.get("receipt") if isinstance(
            record.get("receipt"), dict) else {}
        state = record.get("state") or "unknown"
        payload = {**embedded,
                   "action_id": record.get("action_id") or action.action_id,
                   "status": "received" if state == "pending" else state,
                   "task_id": embedded.get("task_id") or action.task_id}
        payload.setdefault("provider_ref", "core:estimated-outcome" if record.get("outcome_source")
                           in ("expires_at", "retention_timeout") else "idempotent:replay-suppressed")
    status_raw = str(payload.get("status") or "unknown")
    try:
        status = ReceiptStatus(status_raw)
    except ValueError:
        status = ReceiptStatus.UNKNOWN
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    verification = None
    raw_ver = result.get("verification")
    if isinstance(raw_ver, dict) and raw_ver.get("status") in (
            "passed", "failed", "skipped"):
        verification = Verification(
            status=VerificationStatus(str(raw_ver["status"])),
            summary=str(raw_ver.get("summary") or ""))
    return ActionReceipt(
        action_id=str(payload.get("action_id") or action.action_id),
        status=status,
        # task_id 只取自 payload（duplicate 分支已从记录显式补入）；
        # 新鲜回执不回填请求侧 task_id——REST 响应须与设备回包逐字一致。
        task_id=payload.get("task_id"),
        summary=str(result.get("summary") or "")[:2000],
        output_ref=(str(result["output_ref"]) if result.get("output_ref") else None),
        verification=verification,
        error_code=(str(payload["error_code"]) if payload.get("error_code") else None),
        provider_ref=(str(payload["provider_ref"])
                      if payload.get("provider_ref") else None),
        started_at=(int(payload["started_at"]) if payload.get("started_at") is not None
                    else None),
        completed_at=((int(payload["completed_at"])
                       if payload.get("completed_at") is not None else now_ms())
                      if status.terminal else None),
    )


def receipt_from_record(record: Mapping, idempotency_key: str) -> ActionReceipt:
    """持久动作记录（``device:action:{device}:{idem}`` 值）→ ActionReceipt。

    供 DeviceGatewayV2 跨重启对账：记录即事实，绝不重放。
    """
    action = ActionRequest(str(record.get("action_id") or ""),
                           str(record.get("capability") or ""),
                           {}, idempotency_key, task_id=record.get("task_id"))
    return receipt_from_payload(action, {**dict(record), "duplicate": True})


def receipt_payload(receipt: ActionReceipt, *, duplicate: bool = False) -> dict:
    """ActionReceipt → v2 action.receipt payload 形状（REST/自主体回包用）。"""
    result: dict = {}
    if receipt.summary:
        result["summary"] = receipt.summary
    if receipt.output_ref:
        result["output_ref"] = receipt.output_ref
    if receipt.verification is not None:
        result["verification"] = {"status": receipt.verification.status.value,
                                  "summary": receipt.verification.summary}
    payload: dict = {"action_id": receipt.action_id,
                     "status": receipt.status.value}
    if receipt.task_id:
        payload["task_id"] = receipt.task_id
    if result:
        payload["result"] = result
    if receipt.error_code:
        payload["error_code"] = receipt.error_code
    if receipt.provider_ref:
        payload["provider_ref"] = receipt.provider_ref
    if receipt.started_at is not None:
        payload["started_at"] = receipt.started_at
    if receipt.completed_at is not None:
        payload["completed_at"] = receipt.completed_at
    if duplicate:
        payload["duplicate"] = True
    return payload
