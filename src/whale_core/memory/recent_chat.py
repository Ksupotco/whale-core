"""On-demand recall of the owner's recent private-chat words."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from whale_core.memory.curation import _bm25_scores
from whale_core.memory.store import MemoryKind, MemoryRecord, MemoryStore

_LOCAL = ZoneInfo("Asia/Shanghai")
_RECALL_CUES = ("刚才", "昨天", "昨晚", "凌晨", "前天", "上次", "之前",
                "说过", "聊过", "提过", "提到", "记得", "那天", "当时")
_QUESTION_CUES = ("什么", "哪", "谁", "呢", "吗", "？", "?")
_FILLER = ("昨天", "昨晚", "凌晨", "前天", "刚才", "上次", "之前", "那天",
           "当时", "还记得", "记得", "我们", "咱们", "你", "我", "说过",
           "聊过", "聊了", "提过", "提到", "说的", "聊的", "对话",
           "什么", "哪门", "名字", "名称", "名", "叫做", "叫",
           "的", "是", "了", "吗", "呢", "？", "?", "，", "。")


def _owner_words(record: MemoryRecord) -> str:
    summary = str(record.content.get("summary") or "")
    if not summary.startswith("用户："):
        return ""
    return summary.split("\n", 1)[0][3:].strip()


def _question_echo(words: str, query: str) -> bool:
    plain = words.strip(" ？?，。")
    return (plain == query.strip(" ？?，。")
            or words.endswith(("？", "?", "呢", "吗")))


def _time_matches(query: str, created_at: datetime, now: datetime) -> bool:
    local = created_at.astimezone(_LOCAL)
    today = now.astimezone(_LOCAL).date()
    # An overnight conversation spans two calendar dates. In the morning,
    # "yesterday night" may include speech after midnight today.
    if "昨晚" in query:
        return (local.date() == today - timedelta(days=1) and local.hour >= 18
                or local.date() == today and local.hour < 6)
    if "昨天" in query:
        return (local.date() == today - timedelta(days=1)
                or local.date() == today and local.hour < 6)
    if "前天" in query:
        return local.date() == today - timedelta(days=2)
    if "凌晨" in query:
        return local.hour < 6
    return True


def recall_recent_owner_chat(store: MemoryStore, query: str, *,
                             conversation_id: str, space_ids: tuple[str, ...],
                             now: datetime | None = None, limit: int = 5
                             ) -> tuple[str, ...]:
    """Return source-labelled owner quotes, never assistant replies or other chats.

    Caller must establish owner identity. Empty authorized spaces fail closed.
    These are short-term chat excerpts, not promoted durable facts.
    """
    asks_personal_fact = (("我的" in query or "笔名" in query)
                         and any(cue in query for cue in _QUESTION_CUES))
    if (not conversation_id.startswith("private:") or not space_ids
            or not (any(cue in query for cue in _RECALL_CUES)
                    or asks_personal_fact) or limit <= 0):
        return ()
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    earliest = current - timedelta(hours=72)
    candidates: list[MemoryRecord] = []
    for record in store.list_by_kind(MemoryKind.EPISODE, space_ids=space_ids):
        stamp = record.created_at
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        if ("chat" not in record.tags or "superseded" in record.tags
                or record.content.get("status", "active") != "active"
                or record.content.get("conversation_id") != conversation_id
                or not earliest <= stamp < current
                or not _time_matches(query, stamp, current)
                or not _owner_words(record)
                or _question_echo(_owner_words(record), query)):
            continue
        candidates.append(record)
    if not candidates:
        return ()
    focus = query
    for filler in _FILLER:
        focus = focus.replace(filler, "")
    scored_records = [MemoryRecord(
        kind=record.kind, key=record.key,
        content={"summary": _owner_words(record)},
        created_at=record.created_at, space_id=record.space_id)
        for record in candidates]
    scores = _bm25_scores(focus, scored_records) if focus.strip() else {}
    ranked = sorted(candidates, key=lambda record: (
        scores.get(record.key, 0.0), record.created_at), reverse=True)
    if scores and any(scores.values()):
        ranked = [record for record in ranked if scores.get(record.key, 0.0) > 0]
    seen: set[str] = set()
    out: list[str] = []
    for record in ranked:
        words = _owner_words(record)
        if words in seen:
            continue
        seen.add(words)
        stamp = record.created_at
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        out.append(f"[主人私聊原话 {stamp.astimezone(_LOCAL):%m-%d %H:%M}] {words[:260]}")
        if len(out) >= limit:
            break
    return tuple(out)
