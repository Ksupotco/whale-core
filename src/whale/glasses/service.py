"""GlassesService：role=glasses 的 Link 客户端能力面（v2 /turbo/v1/* 的承接）。

方法族（全部经 Link/HTTP 同一注册表，鉴权与 role 列管在 link/protocol.py）：

- ``glasses.context.sync``：日历/位置/天气快照（契约见 context.py，与 v2 联签口径逐字一致）。
- ``glasses.qq_reply``：口述文本发**服务端绑定的 owner QQ**（目标绝不从参数推导）。
- ``glasses.deliver``：统一投递——QQ 在线直发优先，离线/失败走 hub.notify 扇出
  （手机设备线通知 + 在线 Link 端）；图片经 artifact id 引用（控制通道不串大负载）；
  音频产物本期如实 unsupported（QQ 语音需 silk 转码，未接线）。
- ``glasses.schedule.*``：候选两阶段（add/reject）+ 正式条目 CRUD + 来源溯源 ±10 条。
- 回合后置钩子：glasses 渠道对话完成后确定性抽取日程候选（v2 /v1/chat 同款规则）。
- 日程提醒：scheduler 系统任务 ``sys_glasses_remind``（默认关，config.glasses.schedule_remind），
  轻量模型三态判断（发消息/弹提醒/沉默），静默时段沿用 autonomy.quiet_hours，发送走
  hub.notify（不旁路频控/台账）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from ..kernel.errors import WhaleError, not_found
from ..models.base import Message
from . import context as ctx_store
from . import schedule as sched

log = logging.getLogger("whale.glasses")

TRI_STATES = ("发消息", "弹提醒", "沉默")

JUDGE_PROMPT = """你是日程提醒判断器。只回答一个词：发消息 / 弹提醒 / 沉默。

规则：
- 有日程即将开始（30 分钟内）或刚开始没提示过 → 发消息；
- 有日程临近（2 小时内）且重要 → 弹提醒；
- 其余 → 沉默。
日程：{items}
当前时间：{now}
前台近况：{facts}
"""


def _quiet_now(now: datetime, quiet: tuple[int, int]) -> bool:
    start, end = int(quiet[0]) % 24, int(quiet[1]) % 24
    t = now.hour
    if start <= end:
        return start <= t < end
    return t >= start or t < end


class ContextSyncParams(BaseModel):
    # calendar 契约校验（含缺失 → invalid_json）在 context.save_snapshot，保证联签错误码不被
    # pydantic 层截胡
    calendar: dict[str, Any] | None = None
    source: str = ""
    observedAt: str = ""
    location: dict[str, Any] | None = None
    weather: dict[str, Any] | None = None


class QqReplyParams(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class DeliverParams(BaseModel):
    text: str = ""
    artifact_ids: list[str] = Field(default_factory=list, max_length=8)
    prefer_owner_dm: bool = True


class CandidateActionParams(BaseModel):
    id: str
    action: str  # add | reject


class AddItemParams(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    date: str
    time: str = ""


class IdParams(BaseModel):
    id: str


class SourceParams(BaseModel):
    conversation: str
    messageId: str = ""


class GlassesService:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.last_verdict = ""
        self.last_items_count = 0

    # -- 共用件 ---------------------------------------------------------------
    @property
    def tz(self) -> str:
        return self.app.config.value.agent.timezone

    def _qq_channel(self) -> Any | None:
        """在线且配置了 owner 的 QQ 渠道（目标只来自服务端配置）。"""
        from ..channels.qq import QQChannel

        for ch in self.app.channels.channels.values():
            if isinstance(ch, QQChannel) and ch.owner and ch.connected:
                return ch
        return None

    # -- 提示词注入 / 回合钩子 ---------------------------------------------------
    def context_hint(self) -> str:
        today = datetime.now(ZoneInfo(self.tz)).date()
        items = sched.list_items(self.app.db, from_date=today.isoformat(),
                                 to_date=(today + timedelta(days=7)).isoformat())
        return ctx_store.render_hint(self.app.db, tz=self.tz, schedule_items=items)

    def maybe_extract(self, text: str, conversation_id: str) -> None:
        """回合后置：从主人原话抽日程候选（fail-soft，宁缺勿滥）。"""
        try:
            msgs = self.app.conversations.messages(conversation_id, limit=4)
            mid = next((m["id"] for m in reversed(msgs) if m["role"] == "user"), "")
        except Exception:
            mid = ""
        cand = sched.extract_schedule_candidate(
            text, source={"conversation": conversation_id, "messageId": mid}, tz=self.tz)
        if cand is not None:
            sched.add_candidate(self.app.db, cand)

    # -- 日程提醒（scheduler 系统任务） ------------------------------------------
    def sync_remind_job(self) -> None:
        cfg = self.app.config.value.glasses
        self.app.scheduler.ensure_system(
            "sys_glasses_remind", "眼镜日程提醒", "glasses.schedule_remind",
            {"every_min": max(1, int(cfg.remind_interval_min))}, bool(cfg.schedule_remind))

    async def remind_tick(self, args: dict[str, Any]) -> dict[str, Any]:
        cfg = self.app.config.value
        now = datetime.now(ZoneInfo(self.tz))
        if _quiet_now(now, cfg.autonomy.quiet_hours):
            self.last_verdict = "沉默"
            return {"verdict": "沉默", "reason": "quiet_hours"}
        today = now.date().isoformat()
        items = sched.list_items(self.app.db, from_date=today, to_date=today)
        self.last_items_count = len(items)
        if not items:
            self.last_verdict = "沉默"
            return {"verdict": "沉默", "reason": "no_items"}
        items_text = "；".join(f"{it.get('time') or '全天'} {it.get('title')}" for it in items)[:400]
        try:
            facts = self.context_hint()[:300]
        except Exception:
            facts = ""
        weekday = "一二三四五六日"[now.weekday()]
        prompt = JUDGE_PROMPT.format(items=items_text, now=f"{now:%Y-%m-%d %H:%M} 星期{weekday}",
                                     facts=facts or "（无）")
        try:
            completion = await self.app.models.chat("fast").complete([Message("user", prompt)])
            raw = completion.text.strip()
        except Exception:
            self.last_verdict = "沉默"
            return {"verdict": "沉默", "reason": "judge_unavailable"}
        verdict = next((s for s in TRI_STATES if s in raw), "沉默")
        self.last_verdict = verdict
        if verdict == "沉默":
            return {"verdict": verdict}
        text = "、".join(f"{it.get('time') or '全天'} {it.get('title')}" for it in items)[:300]
        try:
            delivered = await self.app.hub.notify("日程提醒", text,
                                                  {"kind": "schedule.reminder", "verdict": verdict})
        except Exception:
            delivered = []  # 发送失败 fail-soft（频控/通道故障不炸 loop）
        return {"verdict": verdict, "delivered_to": delivered}

    # -- RPC -------------------------------------------------------------------
    def register_rpc(self) -> None:
        app = self.app
        m = app.rpc.method

        @m("glasses.context.sync", ContextSyncParams,
           "替换手机日历窗口快照（≤12/日、title≤120、窗口 [today,today+7]、calendarHash 幂等）")
        async def context_sync(p: ContextSyncParams, c: Any) -> Any:
            snap = ctx_store.save_snapshot(app.db, p.model_dump(), tz=self.tz)
            return {"ok": True, "stored": snap["stored"], "receivedAt": snap["receivedAt"],
                    "dates": sorted(snap["calendar"]), "idempotent": snap["idempotent"]}

        @m("glasses.qq_reply", QqReplyParams, "口述文本发给绑定的 owner QQ（目标不从参数推导）")
        async def qq_reply(p: QqReplyParams, c: Any) -> Any:
            ch = self._qq_channel()
            if ch is None:
                raise WhaleError("unavailable", "QQ 渠道未连接或未配置 owner", retryable=True)
            receipt = await ch.send_private_by_uin(ch.owner, p.text)
            return {"ok": receipt.get("status") == "ok", "receipt": receipt}

        @m("glasses.deliver", DeliverParams, "统一结果投递：QQ 在线直发优先，离线走通知扇出；图片经 artifact id")
        async def deliver(p: DeliverParams, c: Any) -> Any:
            if not p.prefer_owner_dm:
                raise WhaleError("invalid_params", "当前版本只投递 owner 私聊（群路由未接线）")
            text = p.text.strip()[:4000]
            if not text and not p.artifact_ids:
                raise WhaleError("invalid_params", "text or artifact_ids required")
            receipts: list[dict[str, Any]] = []
            ch = self._qq_channel()
            if text:
                sent = False
                if ch is not None:
                    try:
                        receipt = await ch.send_private_by_uin(ch.owner, text)
                        sent = receipt.get("status") == "ok"
                        receipts.append({"channel": "qq", "ok": sent,
                                         "detail": receipt.get("error") or f"sent {receipt.get('sent', 0)}"
                                                   f"/{receipt.get('total', 0)}"})
                    except WhaleError as exc:
                        receipts.append({"channel": "qq", "ok": False, "detail": exc.code})
                if not sent:
                    delivered = await app.hub.notify(app.config.value.agent.name, text,
                                                     {"kind": "glasses.deliver"})
                    receipts.append({"channel": "notify", "ok": bool(delivered),
                                     "detail": ",".join(delivered) or "no_endpoint"})
            for aid in p.artifact_ids:
                try:
                    info, data = app.artifacts.read(aid)
                except WhaleError as exc:
                    receipts.append({"channel": "artifact", "ok": False,
                                     "detail": f"{aid}:{exc.code}"})
                    continue
                ctype = str(info.get("content_type") or "")
                if ctype.startswith("image/"):
                    if ch is None:
                        receipts.append({"channel": "qq", "ok": False,
                                         "detail": f"{aid}:qq_unavailable"})
                        continue
                    try:
                        receipt = await ch.send_image("private", ch.owner, bytes(data), ctype)
                        receipts.append({"channel": "qq", "ok": receipt.get("status") == "ok",
                                         "detail": receipt.get("error") or aid})
                    except WhaleError as exc:
                        receipts.append({"channel": "qq", "ok": False,
                                         "detail": f"{aid}:{exc.code}"})
                elif ctype.startswith("audio/"):
                    # QQ 语音需 silk 转码，本期未接线——如实报不支持，不谎报已送达。
                    receipts.append({"channel": "qq", "ok": False,
                                     "detail": f"{aid}:unsupported_audio"})
                else:
                    receipts.append({"channel": "artifact", "ok": False,
                                     "detail": f"{aid}:unsupported_type:{ctype}"})
            return {"ok": bool(receipts) and all(r["ok"] for r in receipts),
                    "count": len(receipts),
                    "channels": [r["channel"] for r in receipts],
                    "receipts": receipts}

        # -- 日程 ------------------------------------------------------------------
        @m("glasses.schedule.list", summary="正式日程列表")
        async def schedule_list(p: Any, c: Any) -> Any:
            return {"items": sched.list_items(app.db)}

        @m("glasses.schedule.candidates", summary="日程候选（pending，带 source 溯源）")
        async def schedule_candidates(p: Any, c: Any) -> Any:
            return {"candidates": sched.list_candidates(app.db)}

        @m("glasses.schedule.candidate_action", CandidateActionParams, "候选裁决 {id, action: add|reject}")
        async def candidate_action(p: CandidateActionParams, c: Any) -> Any:
            if p.action == "add":
                item = sched.approve_candidate(app.db, p.id, tz=self.tz)
                if item is None:
                    raise not_found("candidate", p.id)
                return {"ok": True, "item": item}
            if p.action == "reject":
                if not sched.reject_candidate(app.db, p.id):
                    raise not_found("candidate", p.id)
                return {"ok": True}
            raise WhaleError("invalid_params", "action must be add|reject")

        @m("glasses.schedule.add_item", AddItemParams, "手动建正式日程 {title, date, time?}")
        async def schedule_add(p: AddItemParams, c: Any) -> Any:
            try:
                item = sched.add_item(app.db, title=p.title, date=p.date, time_=p.time, tz=self.tz)
            except ValueError:
                raise WhaleError("invalid_params", "invalid date") from None
            return {"ok": True, "item": item}

        @m("glasses.schedule.delete_item", IdParams, "删正式日程 {id}")
        async def schedule_delete(p: IdParams, c: Any) -> Any:
            return {"ok": sched.delete_item(app.db, p.id)}

        @m("glasses.schedule.source", SourceParams, "来源消息 ±10 条上下文（owner-only）")
        async def schedule_source(p: SourceParams, c: Any) -> Any:
            try:
                msgs = app.conversations.messages(p.conversation, limit=500)
            except WhaleError:
                return {"context": []}
            out: list[dict[str, Any]] = []
            idx = next((i for i, msg in enumerate(msgs) if msg["id"] == p.messageId), None)
            if idx is not None:
                for msg in msgs[max(0, idx - 10):idx + 11]:
                    out.append({"messageId": msg["id"], "at": msg["ts"],
                                "sender": msg["role"], "text": str(msg["content"])[:200]})
            return {"context": out}
