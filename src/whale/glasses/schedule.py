"""眼镜日程：候选→正式两阶段 + 溯源（自 v2 life/schedule.py 逐语义移植）。

- 存储全部走 kv ``schedule`` 命名空间——不进记忆召回池，不污染记忆面。
- 候选（pending）带 source 溯源 {conversation, messageId, at, excerpt≤80}；
  「添加」转正式日程、「拒绝」标 rejected（软删，可审计）。
- 提取只发生在 owner 渠道（眼镜）：只从主人自己的话里提取。
- ID 用 v3 统一前缀 ULID（cand_/item_）。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ..kernel.db import Database
from ..kernel.ids import new_id

NS = "schedule"
CANDS_INDEX = "cands"
ITEMS_INDEX = "items"

#: 确定性提取（不调模型）：日期锚 + 事件词。保守——拿不准不建候选。
_DAY_ANCHOR_RE = re.compile(
    r"(明天|后天|大后天|下周[一二三四五六日天]?|周[一二三四五六日天]|"
    r"\d{1,2}月\d{1,2}[日号]|\d{1,2}[日号])")
_CN_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _cn_hour(text: str) -> int | None:
    m = re.search(r"([零一二两三四五六七八九十]{1,2})\s*点", text)
    if not m:
        return None
    s = m.group(1)
    if s in _CN_NUM:
        return _CN_NUM[s]
    if s.startswith("十"):
        return 10 + _CN_NUM.get(s[1:], 0)
    return None


_TIME_RE = re.compile(r"([0-2]?\d)\s*[点:：]\s*([0-5]\d)?")
_EVENT_RE = re.compile(
    r"会|考试|课|作业|交|吃|饭|餐|练|跑|约会|见|面|拿|取|还|报|面试|球|演|展|"
    r"复诊|复查|医院|体检|理发|出发|到家|接|送|聚")


def _zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except Exception:
        return ZoneInfo("Asia/Shanghai")


def _resolve_date(text: str, *, now: datetime) -> str | None:
    """日期锚 → ISO 日期。无法确定 → None。"""
    today = now.date()
    if "大后天" in text:
        return (today + timedelta(days=3)).isoformat()
    if "后天" in text:
        return (today + timedelta(days=2)).isoformat()
    if "明天" in text:
        return (today + timedelta(days=1)).isoformat()
    m = re.search(r"下周([一二三四五六日天])", text) or re.search(r"周([一二三四五六日天])", text)
    if m:
        wd = "一二三四五六日天".index(m.group(1)) % 7
        if "下周" in text:
            # 下周X = 下个历周（周一~周日）的 X：本周一 +7 + wd
            monday = today - timedelta(days=today.weekday())
            return (monday + timedelta(days=7 + wd)).isoformat()
        delta = (wd - today.weekday()) % 7 or 7
        return (today + timedelta(days=delta)).isoformat()
    m = re.search(r"(\d{1,2})月(\d{1,2})[日号]", text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        try:
            cand = datetime(today.year, month, day).date()
        except ValueError:
            return None
        if cand < today:
            cand = datetime(today.year + 1, month, day).date()
        return cand.isoformat()
    m = re.search(r"(?<!\d)(\d{1,2})[日号](?!\d)", text)
    if m:
        day = int(m.group(1))
        try:
            cand = today.replace(day=day)
        except ValueError:
            return None
        if cand < today:
            cand = (today.replace(day=28) + timedelta(days=7)).replace(day=min(day, 28))
        return cand.isoformat()
    return None


def _resolve_time(text: str) -> str:
    m = _TIME_RE.search(text)
    if m:
        hh = int(m.group(1)) % 24
        mm = int(m.group(2) or 0)
        # 「下午/晚上/中午」修正
        if re.search(r"下午|晚上|傍晚|晚", text[:m.start() + 12]) and hh < 12:
            hh += 12
        return f"{hh:02d}:{mm:02d}"
    cn = _cn_hour(text)
    if cn is not None:
        hh = cn % 24
        if re.search(r"下午|晚上|傍晚|晚", text) and hh < 12:
            hh += 12
        return f"{hh:02d}:00"
    return ""


def extract_schedule_candidate(text: str, *, source: dict, tz: str,
                               now: datetime | None = None) -> dict | None:
    """从用户原话确定性提取日程候选；拿不准返回 None（宁缺勿滥）。"""
    text = (text or "").strip()
    if len(text) < 4 or len(text) > 500:
        return None
    if not _DAY_ANCHOR_RE.search(text):
        return None
    if not _EVENT_RE.search(text):
        return None
    now = now or datetime.now(_zone(tz))
    date = _resolve_date(text, now=now)
    if date is None or date < now.date().isoformat():
        return None
    excerpt = text[:80]
    # 标题：取全文截断（40 字符）
    title = excerpt[:40].strip("，。,. ") or excerpt[:40]
    return {
        "cand_id": new_id("cand"),
        "title": title, "date": date, "time": _resolve_time(text),
        "status": "pending", "created_at": now.isoformat(),
        "source": {
            "conversation": str(source.get("conversation") or "")[:64],
            "messageId": str(source.get("messageId") or "")[:64],
            "at": str(source.get("at") or now.isoformat())[:40],
            "excerpt": excerpt,
        },
    }


def add_candidate(db: Database, cand: dict) -> None:
    db.kv_set(NS, "cand:" + cand["cand_id"], cand)
    idx = list(db.kv_get(NS, CANDS_INDEX, []))
    idx.append(cand["cand_id"])
    db.kv_set(NS, CANDS_INDEX, idx)


def list_candidates(db: Database, *, status: str = "pending") -> list[dict]:
    out = []
    for cid in list(db.kv_get(NS, CANDS_INDEX, [])):
        c = db.kv_get(NS, "cand:" + str(cid))
        if isinstance(c, dict) and c.get("status") == status:
            out.append(c)
    return out


def _find_cand(db: Database, cand_id: str) -> dict | None:
    c = db.kv_get(NS, "cand:" + str(cand_id))
    return c if isinstance(c, dict) else None


def approve_candidate(db: Database, cand_id: str, *, tz: str, now: datetime | None = None) -> dict | None:
    """候选转正式日程（返回 item；未找到/非 pending → None）。"""
    c = _find_cand(db, cand_id)
    if c is None or c.get("status") != "pending":
        return None
    now = now or datetime.now(_zone(tz))
    c["status"] = "added"
    db.kv_set(NS, "cand:" + str(cand_id), c)
    item = {
        "item_id": new_id("item"),
        "title": c["title"], "date": c["date"], "time": c.get("time") or "",
        "source": c.get("source"), "created_at": now.isoformat(),
    }
    db.kv_set(NS, "item:" + item["item_id"], item)
    idx = list(db.kv_get(NS, ITEMS_INDEX, []))
    idx.append(item["item_id"])
    db.kv_set(NS, ITEMS_INDEX, idx)
    return item


def reject_candidate(db: Database, cand_id: str) -> bool:
    c = _find_cand(db, cand_id)
    if c is None or c.get("status") != "pending":
        return False
    c["status"] = "rejected"
    db.kv_set(NS, "cand:" + str(cand_id), c)
    return True


def add_item(db: Database, *, title: str, date: str, time_: str = "", tz: str,
             source: dict | None = None) -> dict:
    """手动建正式日程（date 非法即抛 ValueError）。"""
    datetime.strptime(date, "%Y-%m-%d")
    now = datetime.now(_zone(tz))
    item = {"item_id": new_id("item"), "title": str(title or "")[:120],
            "date": date, "time": str(time_ or "")[:8],
            "source": source or {"conversation": "manual", "messageId": "",
                                  "at": now.isoformat(), "excerpt": ""},
            "created_at": now.isoformat()}
    db.kv_set(NS, "item:" + item["item_id"], item)
    idx = list(db.kv_get(NS, ITEMS_INDEX, []))
    idx.append(item["item_id"])
    db.kv_set(NS, ITEMS_INDEX, idx)
    return item


def delete_item(db: Database, item_id: str) -> bool:
    if not isinstance(db.kv_get(NS, "item:" + str(item_id)), dict):
        return False
    db.kv_delete(NS, "item:" + str(item_id))
    idx = [i for i in list(db.kv_get(NS, ITEMS_INDEX, [])) if i != item_id]
    db.kv_set(NS, ITEMS_INDEX, idx)
    return True


def list_items(db: Database, *, from_date: str = "", to_date: str = "") -> list[dict]:
    out: list[dict[str, Any]] = []
    for iid in list(db.kv_get(NS, ITEMS_INDEX, [])):
        it = db.kv_get(NS, "item:" + str(iid))
        if not isinstance(it, dict):
            continue
        if from_date and str(it.get("date") or "") < from_date:
            continue
        if to_date and str(it.get("date") or "") > to_date:
            continue
        out.append(it)
    return out
