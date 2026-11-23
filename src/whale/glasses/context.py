"""眼镜上下文快照：手机日历窗口 + 位置 + 天气（kv 存储，绝不进记忆面）。

契约与 v2 ``POST /v1/context/sync`` 逐字一致（与应用开发联签 2026-11-17，Link 面沿用）：

- ``calendar`` 必填 dict：ISO 日期（``%Y-%m-%d``）→ 事件数组 / ``null``（未读）；
  窗口 [today, today+7]（core 所在时区，含两端）；单日 ≤12 条；``title`` 必填非空
  ≤120 字符；``allDay`` 必须 bool；事件只保留 id/title/start/end/allDay 五键。
- ``[]`` 空日程与 ``null`` 未读是两种语义，渲染时区分。
- 幂等：规范化 calendar 的 sha256（``calendarHash``）；同哈希重放不更新
  ``receivedAt``，返回原快照并标 ``idempotent=true``。
- 契约错误码经 ``WhaleError("invalid_params", details={"code": ...})`` 暴露，枚举值不变：
  ``invalid_json`` / ``invalid_date`` / ``too_many_entries`` / ``title_too_long``。

与 v2 的唯一刻意差异：天气/位置从「每请求随行」改为「sync 随行」——各自带独立
``receivedAt``，渲染时按龄期标注「（缓存，可能已变化）」，诚实性不降。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ..kernel.db import Database
from ..kernel.errors import WhaleError

MAX_EVENTS_PER_DAY = 12
MAX_TITLE_CHARS = 120
WINDOW_DAYS = 7  # 快照窗口 [today, today+7]（含两端）

NS = "glasses"
SNAPSHOT_KEY = "context:snapshot"
LOCATION_KEY = "last_location"
WEATHER_KEY = "weather"
WEATHER_STALE_S = 1800


def _err(code: str, message: str) -> WhaleError:
    return WhaleError("invalid_params", message, details={"code": code})


def _calendar_hash(calendar: dict) -> str:
    """规范化 calendar 的稳定哈希：日期键排序、事件键序稳定（幂等判定依据）。"""
    canonical = json.dumps(calendar, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _count_events(calendar: Any) -> int:
    if not isinstance(calendar, dict):
        return 0
    return sum(len(events) for events in calendar.values() if isinstance(events, list))


def _coerce_coord(value: Any) -> float | None:
    if value is None or value is False:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return None if num != num else num  # NaN


def summarize_location(city: str = "", location: str = "", lat: Any = None, lng: Any = None) -> str:
    """位置字段收成一行摘要（城市优先；经纬度只留约街区精度）。"""
    city, location = (city or "").strip()[:80], (location or "").strip()[:120]
    lat_f, lng_f = _coerce_coord(lat), _coerce_coord(lng)
    parts: list[str] = []
    if city:
        parts.append(city)
    if location and location not in parts:
        parts.append(location)
    if lat_f is not None and lng_f is not None:
        parts.append(f"约 {lat_f:.4f},{lng_f:.4f}")
    return "；".join(parts)[:200]


def _now(tz: str) -> datetime:
    try:
        return datetime.now(ZoneInfo(tz))
    except Exception:
        return datetime.now()


def save_snapshot(db: Database, body: dict, *, tz: str, now: datetime | None = None) -> dict:
    """替换日历窗口快照；``[]`` 空日程、``null`` 未读语义不变。

    同哈希重放返回原快照并标 ``idempotent=True``（不更新 receivedAt/source/observedAt）。
    返回快照附加 ``{"stored": 事件总数, "idempotent": bool}``（附加键不落库）。
    """
    if not isinstance(body, dict):
        raise _err("invalid_json", "body must be an object")
    calendar = body.get("calendar")
    if not isinstance(calendar, dict):
        raise _err("invalid_json", "calendar must map ISO dates to event arrays or null")
    zone = ZoneInfo(tz) if tz else ZoneInfo("Asia/Shanghai")
    now = now or datetime.now(zone)
    today, last = now.date(), now.date() + timedelta(days=WINDOW_DAYS)
    normalized: dict[str, Any] = {}
    for day, events in calendar.items():
        try:
            parsed = datetime.strptime(str(day), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            raise _err("invalid_date", f"invalid calendar date: {day!r}") from None
        if not today <= parsed <= last:
            raise _err("invalid_date", f"date {parsed.isoformat()} outside window [{today}, {last}]")
        day_iso = parsed.isoformat()
        if events is None:
            normalized[day_iso] = None
            continue
        if not isinstance(events, list):
            raise _err("invalid_json", "calendar entries must be arrays or null")
        if len(events) > MAX_EVENTS_PER_DAY:
            raise _err("too_many_entries", f"more than {MAX_EVENTS_PER_DAY} events on {day_iso}")
        normalized[day_iso] = []
        for event in events:
            if not isinstance(event, dict):
                raise _err("invalid_json", "event must be an object")
            title = event.get("title")
            if not isinstance(title, str) or not title.strip():
                raise _err("invalid_json", "event title required")
            if len(title) > MAX_TITLE_CHARS:
                raise _err("title_too_long", f"title longer than {MAX_TITLE_CHARS} chars")
            if "allDay" in event and not isinstance(event["allDay"], bool):
                raise _err("invalid_json", "allDay must be boolean")
            normalized[day_iso].append({
                key: (bool(event[key]) if key == "allDay" else str(event[key])[:300])
                for key in ("id", "title", "start", "end", "allDay") if key in event})
    # 窗口清理：写入即丢弃窗口外日期键（校验已逐键拒绝，此处为防御性兜底）。
    normalized = {d: ev for d, ev in normalized.items()
                  if today <= datetime.strptime(d, "%Y-%m-%d").date() <= last}
    digest = _calendar_hash(normalized)
    previous = db.kv_get(NS, SNAPSHOT_KEY)
    if isinstance(previous, dict) and previous.get("calendarHash") == digest:
        return {**previous, "stored": _count_events(previous.get("calendar")), "idempotent": True}
    snapshot = {"calendar": normalized,
                "source": str(body.get("source") or "android.calendar")[:120],
                "observedAt": str(body.get("observedAt") or now.isoformat()),
                "receivedAt": now.isoformat(),
                "calendarHash": digest}
    db.kv_set(NS, SNAPSHOT_KEY, snapshot)
    # 随行字段：位置与天气各自独立计时（日历刷新不延长它们的寿命）。
    loc = body.get("location")
    if isinstance(loc, dict):
        summary = summarize_location(str(loc.get("city") or ""), str(loc.get("location") or ""),
                                     loc.get("lat"), loc.get("lng"))
        if summary:
            db.kv_set(NS, LOCATION_KEY, {"summary": summary, "receivedAt": now.isoformat()})
    weather = body.get("weather")
    if isinstance(weather, dict) and str(weather.get("text") or "").strip():
        db.kv_set(NS, WEATHER_KEY, {"text": str(weather["text"]).strip()[:500],
                                    "receivedAt": now.isoformat()})
    return {**snapshot, "stored": _count_events(normalized), "idempotent": False}


def render_hint(db: Database, *, tz: str, schedule_items: list[dict] | None = None,
                now: datetime | None = None) -> str:
    """把快照渲染成「当轮手机事实资料」提示段；空快照 → 空串。

    正式日程条目以事件形态并入日历视图（快照语义不动：``null`` 未读不覆盖）；
    天气/位置带龄期诚实标注。
    """
    zone = ZoneInfo(tz) if tz else ZoneInfo("Asia/Shanghai")
    now = now or datetime.now(zone)
    today = now.date()
    snapshot = db.kv_get(NS, SNAPSHOT_KEY) or {}
    calendar = dict(snapshot.get("calendar") or {}) if isinstance(snapshot, dict) else {}
    for item in schedule_items or []:
        day = str(item.get("date") or "")
        if not day or (day in calendar and calendar[day] is None):
            continue  # null=未读语义不覆盖
        cur = list(calendar.get(day) or [])
        cur.append({"title": str(item.get("title") or "")[:120], "start": str(item.get("time") or "")})
        calendar[day] = cur
    lines: list[str] = []
    for offset, label in ((0, "今日日程"), (1, "明日日程")):
        day = (today + timedelta(days=offset)).isoformat()
        if day not in calendar:
            continue
        events = calendar[day]
        if events is None:
            value = "未能读取"
        elif not events:
            value = "（无）"
        else:
            value = "、".join(
                (str("全天" if e.get("allDay") else e.get("start") or "") + " " + str(e.get("title") or "")).strip()
                for e in events[:30] if isinstance(e, dict))
        lines.append(f"[{label} {day}] {value}")
    loc = db.kv_get(NS, LOCATION_KEY)
    if isinstance(loc, dict) and loc.get("summary"):
        lines.append(f"[位置] {str(loc['summary'])[:200]}")
    weather = db.kv_get(NS, WEATHER_KEY)
    if isinstance(weather, dict) and weather.get("text"):
        age = "（缓存，可能已变化）"
        try:
            age_s = (now - datetime.fromisoformat(str(weather["receivedAt"]))).total_seconds()
            age = "（缓存，可能已变化）" if age_s > WEATHER_STALE_S else ""
        except Exception:
            pass  # 龄期不可考时按陈旧标注，绝不装新鲜
        lines.append(f"[天气] {str(weather['text'])[:500]}{age}")
    if not lines:
        return ""
    return "当轮手机事实资料：\n" + "\n".join(lines)[:3000]
