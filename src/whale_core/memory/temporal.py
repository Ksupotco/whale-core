"""Small deterministic parser for explicit Chinese effective-time phrases."""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

_LOCAL = ZoneInfo("Asia/Shanghai")
_WEEKDAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}


def _day_start(value: datetime) -> datetime:
    return datetime.combine(value.date(), time.min, tzinfo=_LOCAL)


def _date_for(phrase: str, observed: datetime) -> datetime | None:
    local = observed.astimezone(_LOCAL)
    if phrase == "明天":
        return _day_start(local + timedelta(days=1))
    if phrase == "后天":
        return _day_start(local + timedelta(days=2))
    if phrase == "下周":
        return _day_start(local + timedelta(days=7 - local.weekday()))
    if phrase == "下个月":
        year = local.year + (local.month == 12)
        month = 1 if local.month == 12 else local.month + 1
        return datetime(year, month, 1, tzinfo=_LOCAL)
    match = re.fullmatch(r"下周[一二三四五六日天]", phrase)
    if match:
        return _date_for("下周", observed) + timedelta(days=_WEEKDAY[phrase[-1]])
    return None


def effective_interval(text: str, *, observed_at: datetime | None = None
                       ) -> tuple[str, str, str]:
    """Return (valid_from, valid_to, source phrase); end is exclusive.

    Only explicit start/end language is parsed. Unrecognized wording leaves the
    interval empty rather than inventing an effective date.
    """
    observed = observed_at or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    text = str(text or "")
    start = re.search(r"(明天|后天|下周[一二三四五六日天]?|下个月)(?:起|开始|以后)", text)
    end = re.search(r"(?:到|截至|截止到|直到)(明天|后天|下周[一二三四五六日天]?|下个月)(?:为止|结束)?", text)
    from_at = _date_for(start.group(1), observed) if start else None
    end_day = _date_for(end.group(1), observed) if end else None
    # "到下周五" includes Friday; "到下周" means until Monday starts.
    to_at = (end_day + timedelta(days=1) if end_day and end.group(1) not in
             ("下周", "下个月") else end_day)
    phrase = "；".join(match.group(0) for match in (start, end) if match)
    return (from_at.astimezone(timezone.utc).isoformat() if from_at else "",
            to_at.astimezone(timezone.utc).isoformat() if to_at else "",
            phrase)
