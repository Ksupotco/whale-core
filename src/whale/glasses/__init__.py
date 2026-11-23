"""眼镜面（role=glasses 的 Link 客户端）：上下文快照、日程、QQ 口述回复、统一投递。

承接 v2 whale-glasses 轻量面（/turbo/v1/*）的核心能力，全部收敛为 Link RPC 方法；
v2 的轮询 followup 与 confirmId 两阶段由 Link 事件流与统一审批流替代（见
docs/glasses-link-migration-design.md）。
"""
from __future__ import annotations

from typing import Any


def register(app: Any) -> None:
    from .service import GlassesService

    service = GlassesService(app)
    app.glasses = service
    service.register_rpc()
    app.context_providers["glasses"] = service.context_hint
    app.turn_hooks["glasses"] = service.maybe_extract
    app.scheduler.handler("glasses.schedule_remind", service.remind_tick)
