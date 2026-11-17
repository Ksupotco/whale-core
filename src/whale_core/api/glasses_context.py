"""Request-only glasses facts and replaceable phone calendar snapshots."""
from __future__ import annotations

import hashlib
import json
from contextvars import ContextVar
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
SNAPSHOT_KEY = "glasses:context:snapshot"
REQUEST_FACTS: ContextVar[dict | None] = ContextVar("glasses_facts", default=None)
LABELS = {"现在", "今日日程", "明日日程", "天气"}

# /v1/context/sync 契约上限（与应用开发联签 2026-11-17）
MAX_EVENTS_PER_DAY = 12
MAX_TITLE_CHARS = 120
WINDOW_DAYS = 7  # 快照窗口 [today, today+7]（含两端），Asia/Shanghai


class ContextSyncError(ValueError):
    """契约化校验错误：``code`` 即响应 error 字段（联签错误码枚举之一）。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _calendar_hash(calendar: dict) -> str:
    """规范化 calendar 的稳定哈希：日期键排序、事件键序稳定（幂等判定依据）。"""
    canonical = json.dumps(calendar, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _count_events(calendar) -> int:
    if not isinstance(calendar, dict):
        return 0
    return sum(len(events) for events in calendar.values()
               if isinstance(events, list))


def split_chat_context(text: str, context: dict | None = None) -> tuple[str, dict]:
    """Accept the shipped ChatContext.wrap format without treating facts as speech."""
    facts = {}
    lines = text.splitlines(keepends=True)
    consumed = 0
    for line in lines:
        if line.startswith("[用户]") and facts:
            user = "".join(lines[consumed:])[len("[用户]"):].strip()
            facts.update(context or {})
            return user, facts
        if not line.startswith("[") or "]" not in line:
            break
        label, value = line[1:].split("]", 1)
        if label not in LABELS:
            break
        facts[label] = value.strip()[:1500]
        consumed += 1
    # Partial or ordinary text is not silently removed.
    return text.strip(), dict(context or {})


def save_context_snapshot(store, body: dict, *, now: datetime | None = None) -> dict:
    """替换日程窗口快照；[] 空日程、null 未读语义不变。

    契约校验（错误码经 ContextSyncError.code 暴露，与手机侧联签枚举一致）：
    calendar 必填 dict；日期键合法且在窗口 [today, today+7]（Asia/Shanghai）内；
    单日 ≤12 条；title 必填非空字符串且 ≤120 字符；allDay 必须 bool。
    事件仍只保留 id/title/start/end/allDay 五键，id/start/end 维持截断。

    幂等：规范化 calendar 的 sha256 存快照（calendarHash）；同哈希重放不更新
    receivedAt，返回原快照并标 idempotent=True。返回快照附加
    {"stored": 全部日期事件总数, "idempotent": bool}（附加键不落库）。
    """
    calendar = body.get("calendar")
    if not isinstance(calendar, dict):
        raise ContextSyncError(
            "invalid_json", "calendar must map ISO dates to event arrays or null")
    now = now or datetime.now(SHANGHAI)
    today = now.date()
    last = today + timedelta(days=WINDOW_DAYS)
    normalized: dict = {}
    for day, events in calendar.items():
        try:
            parsed = datetime.strptime(str(day), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            raise ContextSyncError(
                "invalid_date", f"invalid calendar date: {day!r}") from None
        if not today <= parsed <= last:
            raise ContextSyncError(
                "invalid_date",
                f"date {parsed.isoformat()} outside window [{today}, {last}]")
        day_iso = parsed.isoformat()
        if events is None:
            normalized[day_iso] = None
            continue
        if not isinstance(events, list):
            raise ContextSyncError(
                "invalid_json", "calendar entries must be arrays or null")
        if len(events) > MAX_EVENTS_PER_DAY:
            raise ContextSyncError(
                "too_many_entries",
                f"more than {MAX_EVENTS_PER_DAY} events on {day_iso}")
        normalized[day_iso] = []
        for event in events:
            if not isinstance(event, dict):
                raise ContextSyncError("invalid_json", "event must be an object")
            title = event.get("title")
            if not isinstance(title, str) or not title.strip():
                raise ContextSyncError("invalid_json", "event title required")
            if len(title) > MAX_TITLE_CHARS:
                raise ContextSyncError(
                    "title_too_long",
                    f"title longer than {MAX_TITLE_CHARS} chars")
            if "allDay" in event and not isinstance(event["allDay"], bool):
                raise ContextSyncError("invalid_json", "allDay must be boolean")
            normalized[day_iso].append({
                key: (bool(event[key]) if key == "allDay" else str(event[key])[:300])
                for key in ("id", "title", "start", "end", "allDay") if key in event})
    # 窗口清理：写入即丢弃窗口外日期键（校验已逐键拒绝，此处为防御性兜底）。
    normalized = {d: ev for d, ev in normalized.items()
                  if today <= datetime.strptime(d, "%Y-%m-%d").date() <= last}
    digest = _calendar_hash(normalized)
    try:
        previous = store.get(SNAPSHOT_KEY)
    except Exception:
        previous = None
    if isinstance(previous, dict) and previous.get("calendarHash") == digest:
        # 幂等重放：不更新 receivedAt / source / observedAt，原快照原样返回。
        return {**previous, "stored": _count_events(previous.get("calendar")),
                "idempotent": True}
    snapshot = {"calendar": normalized,
                "source": str(body.get("source") or "android.calendar")[:120],
                "observedAt": str(body.get("observedAt") or now.isoformat()),
                "receivedAt": now.isoformat(),
                "calendarHash": digest}
    # Weather remains request-only: a calendar refresh must not extend its life.
    store.put(SNAPSHOT_KEY, snapshot)
    return {**snapshot, "stored": _count_events(normalized), "idempotent": False}


def render_context(facts: dict | None, *, now: datetime | None = None) -> str:
    """Render bounded dated data; the model's current clock remains server-owned."""
    if not facts:
        return ""
    now = now or datetime.now(SHANGHAI)
    lines = []
    calendar = facts.get("calendar")
    if isinstance(calendar, dict):
        for offset, label in ((0, "今日日程"), (1, "明日日程")):
            day = (now + timedelta(days=offset)).date().isoformat()
            if day not in calendar:
                continue
            events = calendar[day]
            if events is None:
                value = "未能读取"
            elif not events:
                value = "（无）"
            else:
                value = "、".join(
                    (str("全天" if e.get("allDay") else e.get("start") or "")
                     + " " + str(e.get("title") or "")).strip()
                    for e in events[:30] if isinstance(e, dict))
            lines.append(f"[{label} {day}] {value}")
    # A legacy block has a client date anchor; do not roll yesterday's tomorrow forward.
    anchor = str(facts.get("现在") or "")[:10]
    legacy_current = not anchor or anchor == now.date().isoformat()
    for label in ("今日日程", "明日日程", "天气"):
        value = facts.get(label)
        if value and legacy_current:
            lines.append(f"[{label}] {str(value)[:1500]}")
    weather = facts.get("weather")
    if isinstance(weather, dict):
        text = str(weather.get("text") or "")
        age = weather.get("ageSec", 0)
        if text:
            suffix = "（缓存，可能已变化）" if isinstance(age, (int, float)) and age > 1800 else ""
            lines.append("[天气] " + text[:500] + suffix)
    elif isinstance(weather, str) and weather:
        lines.append("[天气] " + weather[:500])
    if not lines:
        return ""
    return "当轮手机事实资料：\n" + "\n".join(lines)[:3000]


def request_context_hint(store) -> str:
    snapshot = store.get(SNAPSHOT_KEY) or {}
    current = REQUEST_FACTS.get() or {}
    # Explicit current facts override synchronized facts, never modify the snapshot.
    merged = dict(snapshot)
    merged.update(current)
    current_calendar = current.get("calendar")
    if isinstance(current_calendar, dict):
        # Calendar overrides are per-date: a request carrying only tomorrow must
        # keep the snapshot's today; [] / null still cover their own date.
        snapshot_calendar = snapshot.get("calendar")
        calendar = dict(snapshot_calendar) if isinstance(snapshot_calendar, dict) else {}
        calendar.update(current_calendar)
        merged["calendar"] = calendar
    elif "calendar" in current:
        # An empty request calendar is not an explicit clear (POST sync only):
        # the synced window stays visible for this turn.
        merged["calendar"] = snapshot.get("calendar")
    anchor = str(current.get("现在") or "")[:10]
    today = datetime.now(SHANGHAI).date()
    legacy_current = not anchor or anchor == today.isoformat()
    if legacy_current and ("今日日程" in current or "明日日程" in current):
        calendar = dict(merged.get("calendar") or {})
        for offset, label in ((0, "今日日程"), (1, "明日日程")):
            if label in current:
                calendar.pop((today + timedelta(days=offset)).isoformat(), None)
        merged["calendar"] = calendar
    return render_context(merged)
