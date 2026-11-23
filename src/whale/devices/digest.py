"""Phone digest: turn kept phone experiences into long-term memories.

Like ScreenMemo's segment summaries: events are grouped into time segments
(gap > 20 min starts a new one) and each segment is summarised by the fast
model into episode memories — plus facts, but only facts with a verbatim
quote from the events (same evidence rule as chat extraction).

Personal phone → memories about the owner ("用户…"); agent phone → the AI's
own experiences ("我在自己的手机上…", subject ``self``). Both go to the
owner audience, so they never surface in group chats or other people's chats.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ..kernel.db import loads
from ..memory.store import normalize
from ..models.base import Message, parse_json_loose

log = logging.getLogger("whale.devices.digest")

SEGMENT_GAP_MS = 20 * 60_000
MAX_EVENTS = 300

PROMPT = """下面是{who}在一段时间内的手机使用记录（时间、应用、摘要）。请整理成长期记忆，只输出 JSON：
{"episodes": [{"content": "…", "importance": 0.3, "tags": ["…"]}],
 "facts": [{"kind": "fact|preference", "content": "…", "quote": "…", "importance": 0.5}]}
规则：
- episodes：把这段时间概括为 1-2 条经历，{voice}，包含时间段、主要应用和做了什么；琐碎重复的操作合并。
- facts：只在记录里**明确出现**的、关于{subject}的长期信息（如订了某趟行程、关注某个话题、常用某个应用）；
  quote 必须逐字复制记录中的片段。推测的不要写。没有就给空数组。
- 不要写入验证码、卡号、密码等敏感信息。"""


class PhoneDigest:
    def __init__(self, service: Any) -> None:
        self.service = service
        self.app = service.app

    def _tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.app.config.value.agent.timezone)
        except Exception:
            return ZoneInfo("UTC")

    def _pending(self, device_id: str) -> list[dict[str, Any]]:
        mark = int(self.app.db.kv_get("phone_digest", device_id, 0))
        rows = self.app.db.query(
            "SELECT event_id, kind, summary, occurred_at, received_at, data FROM device_events "
            "WHERE device_id=? AND received_at>? AND kind!='dropped' ORDER BY occurred_at LIMIT ?",
            (device_id, mark, MAX_EVENTS))
        return [dict(r) | {"data": loads(r["data"], {})} for r in rows]

    @staticmethod
    def segments(events: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        out: list[list[dict[str, Any]]] = []
        for ev in events:
            if out and ev["occurred_at"] - out[-1][-1]["occurred_at"] <= SEGMENT_GAP_MS:
                out[-1].append(ev)
            else:
                out.append([ev])
        return out

    def _lines(self, seg: list[dict[str, Any]]) -> list[str]:
        tz = self._tz()
        lines = []
        for ev in seg:
            extras = ev["data"].get("extras") or {}
            app = extras.get("app_name") or extras.get("package") or ev["data"].get("source", "")
            when = datetime.fromtimestamp(ev["occurred_at"] / 1000, tz).strftime("%H:%M")
            lines.append(f"{when} [{app}] {ev['summary'][:300]}")
        return lines

    async def run(self) -> dict[str, Any]:
        made = {"devices": 0, "segments": 0, "episodes": 0, "facts": 0}
        for rec in self.service.registry.list():
            if rec["revoked"]:
                continue
            events = self._pending(rec["id"])
            if not events:
                continue
            made["devices"] += 1
            role = self.service.role_of(rec)
            for seg in self.segments(events):
                ep, facts = await self._digest(rec, role, seg)
                made["segments"] += 1
                made["episodes"] += ep
                made["facts"] += facts
            self.app.db.kv_set("phone_digest", rec["id"], max(e["received_at"] for e in events))
        await self.app.bus.emit("device.digest", made)
        return made

    async def _digest(self, rec: dict[str, Any], role: str, seg: list[dict[str, Any]]) -> tuple[int, int]:
        lines = self._lines(seg)
        tz = self._tz()
        start = datetime.fromtimestamp(seg[0]["occurred_at"] / 1000, tz)
        end = datetime.fromtimestamp(seg[-1]["occurred_at"] / 1000, tz)
        span = f"{start:%m-%d %H:%M}–{end:%H:%M}"
        agent = role == "agent"
        source = {"device_id": rec["id"], "device": rec["name"], "role": role, "span": span,
                  "event_ids": [e["event_id"] for e in seg][:60], "from": "手机"}
        tags = ["鲸鱼的手机" if agent else "手机", rec["name"][:16]]
        model = self.app.models.chat("fast")
        data: dict[str, Any]
        if model.offline:
            apps = []
            for ev in seg:
                a = (ev["data"].get("extras") or {}).get("app_name") or (ev["data"].get("extras") or {}).get("package")
                if a and a not in apps:
                    apps.append(a)
            head = "我在自己的手机上" if agent else "用户在手机上"
            body = "；".join(e["summary"][:40] for e in seg[:4])
            data = {"episodes": [{"content": f"{span} {head}" + (f"用了{'、'.join(apps[:3])}：" if apps else "：") + body,
                                  "importance": 0.3}], "facts": []}
        else:
            prompt = (PROMPT.replace("{who}", "你（鲸鱼）自己的手机" if agent else "主人")
                      .replace("{voice}", "用“我……”第一人称写成你自己的经历" if agent else "用“用户……”第三人称")
                      .replace("{subject}", "你自己" if agent else "用户"))
            try:
                out = await model.complete([Message("system", prompt),
                                            Message("user", f"时间段 {span}\n" + "\n".join(lines))],
                                           json_mode=True, temperature=0.2, max_tokens=700)
                data = parse_json_loose(out.text)
            except Exception as exc:
                log.info("phone digest skipped: %s", exc)
                return 0, 0
        episodes = facts = 0
        for ep in (data.get("episodes") or [])[:2]:
            content = str(ep.get("content") or "").strip()
            if content:
                await self.app.memory.remember(content, kind="episode", importance=float(ep.get("importance") or 0.3),
                                               tags=tags + [str(t) for t in (ep.get("tags") or [])][:3],
                                               source=source, consolidate=False, scope="owner",
                                               subject="self" if agent else "user")
                episodes += 1
        evidence = normalize("\n".join(lines))
        for f in (data.get("facts") or [])[:5]:
            content, quote = str(f.get("content") or "").strip(), str(f.get("quote") or "").strip()
            if not content or not quote or normalize(quote) not in evidence:
                continue
            kind = "preference" if f.get("kind") == "preference" else "fact"
            await self.app.memory.remember(content, kind=kind, importance=float(f.get("importance") or 0.5),
                                           tags=tags, source={**source, "quote": quote}, scope="owner",
                                           subject="self" if agent else "user")
            facts += 1
        return episodes, facts

    def search(self, query: str, hours: float = 72, limit: int = 20) -> list[dict[str, Any]]:
        """Owner-only: find kept phone events by text (\"我刚才在手机上看的那个视频\")."""
        from ..kernel.ids import now_ms

        since = now_ms() - int(hours * 3_600_000)
        terms = [t for t in query.split() if t] or [query]
        clause = " AND ".join("summary LIKE ?" for _ in terms)
        rows = self.app.db.query(
            f"SELECT device_id, event_id, summary, occurred_at, data FROM device_events WHERE kind!='dropped' "
            f"AND occurred_at>=? AND ({clause}) ORDER BY occurred_at DESC LIMIT ?",
            (since, *[f"%{t}%" for t in terms], limit))
        tz = self._tz()
        names = {r["id"]: r["name"] for r in self.service.registry.list()}
        out = []
        for r in rows:
            extras = loads(r["data"], {}).get("extras") or {}
            out.append({"device": names.get(r["device_id"], r["device_id"][:6]),
                        "time": datetime.fromtimestamp(r["occurred_at"] / 1000, tz).strftime("%m-%d %H:%M"),
                        "app": extras.get("app_name") or extras.get("package") or "", "summary": r["summary"]})
        return out
