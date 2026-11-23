"""Autonomy: exploration in idle time and the single gate for proactive messages."""

from __future__ import annotations

import logging
import random
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ..kernel.ids import now_ms
from ..models.base import parse_json_loose

log = logging.getLogger("whale.autonomy")


class Proactive:
    """Every unsolicited message goes through here: quiet hours, daily limit, cooldown."""

    def __init__(self, app: Any) -> None:
        self.app = app

    def _now(self) -> datetime:
        try:
            return datetime.now(ZoneInfo(self.app.config.value.agent.timezone))
        except Exception:
            return datetime.now()

    def verdict(self) -> tuple[bool, str]:
        cfg = self.app.config.value.autonomy
        if not cfg.proactive:
            return False, "主动消息未开启"
        start, end = cfg.quiet_hours
        hour = self._now().hour
        quiet = (start <= hour or hour < end) if start > end else (start <= hour < end)
        if quiet:
            return False, f"静默时段 {start}:00-{end}:00"
        day = self._now().date().isoformat()
        sent = int(self.app.db.kv_get("proactive", day, 0))
        if sent >= cfg.proactive_daily_limit:
            return False, f"今日已主动发送 {sent} 条"
        last = int(self.app.db.kv_get("proactive", "last_at", 0))
        if now_ms() - last < 45 * 60_000:
            return False, "冷却中"
        return True, "ok"

    async def offer(self, text: str, *, reason: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
        ok, why = self.verdict()
        record = {"text": text, "reason": reason, "sent": ok, "why": why, "at": now_ms()}
        if ok:
            day = self._now().date().isoformat()
            self.app.db.kv_set("proactive", day, int(self.app.db.kv_get("proactive", day, 0)) + 1)
            self.app.db.kv_set("proactive", "last_at", now_ms())
            record["delivered_to"] = await self.app.hub.notify(self.app.config.value.agent.name, text,
                                                               {"kind": "proactive", **(meta or {})})
        await self.app.bus.emit("autonomy.proactive", record)
        return record


class Explorer:
    def __init__(self, app: Any) -> None:
        self.app = app

    def topics(self) -> list[str]:
        cfg = self.app.config.value.autonomy
        topics = list(cfg.interests)
        prefs, _ = self.app.memory.store.list(kind="preference", limit=30, order="importance DESC")
        for p in prefs:
            topics.append(p.content)
        return topics

    async def explore(self, topic: str | None = None) -> dict[str, Any]:
        from ..agent.runtime import RunRequest

        topics = self.topics()
        topic = topic or (random.choice(topics) if topics else "最近值得关注的科技与生活新知")
        await self.app.bus.emit("autonomy.exploring", {"topic": topic})
        outcome = await self.app.agent.run(RunRequest(
            text=f"探索主题：{topic}", mode="explore", channel="autonomy", remember=False, max_steps=6,
            allowed=["web.*", "memory.search", "time.now"]))
        result: dict[str, Any] = {"topic": topic, "run_id": outcome.run["id"], "status": outcome.status,
                                  "diary": None, "findings": []}
        if outcome.status != "completed":
            await self.app.bus.emit("autonomy.explored", result)
            return result
        try:
            data = parse_json_loose(outcome.text)
        except ValueError:
            data = {"diary": outcome.text[:400], "findings": [], "share": ""}
        src = {"run_id": outcome.run["id"], "topic": topic, "autonomy": True}
        if data.get("diary"):
            res = await self.app.memory.remember(str(data["diary"]), kind="diary", importance=0.3,
                                                 tags=["探索", topic[:16]], source=src, consolidate=False)
            result["diary"] = res["item"]["id"]
        for f in (data.get("findings") or [])[:5]:
            content = str(f.get("content") or "").strip()
            if not content:
                continue
            res = await self.app.memory.remember(content, kind="knowledge", status="candidate",
                                                 importance=float(f.get("importance") or 0.5),
                                                 tags=["探索"], source={**src, "url": f.get("source", "")})
            result["findings"].append(res["item"]["id"])
        if str(data.get("share") or "").strip():
            result["proactive"] = await self.app.proactive.offer(str(data["share"]), reason=f"探索：{topic}",
                                                                 meta={"run_id": outcome.run["id"]})
        await self.app.bus.emit("autonomy.explored", result)
        return result
