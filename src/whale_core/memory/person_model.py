"""Weekly, evidence-linked owner portrait and response feedback."""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone

from whale_core.memory.emotion import EmotionMemory
from whale_core.memory.extraction import contains_sensitive
from whale_core.memory.store import MemoryKind

ITEM_PREFIX = "person:model:"
REVIEW_PREFIX = "person:review:"
PENDING_PREFIX = "person:review:pending:"
_REVIEW_LOCK = threading.Lock()
_PRIVATE_TOPICS = (
    "分手", "恋爱", "亲密", "心理", "抑郁", "咨询", "男朋友", "女朋友",
)
_PROMPT = (
    "回顾以下近期对话，判断本人在什么情境下需要何种回应，以及可能的新兴趣。"
    "只根据给定证据，不写宽泛性格标签。后续语气足以表明上一回应有效或无效时，"
    "才给情绪事件填写 feedback。仅输出 JSON，格式："
    '{"feedback":[{"event_id":"id","response_style":"先共同讨论","effective":true}],'
    '"profiles":[{"context":"讨论作品","need":"共同讨论",'
    '"effective_response":"提出具体观点","expression_preference":"自然简短",'
    '"evidence_ids":["ep:1","ep:2"]}],'
    '"interests":[{"context":"科幻","topic":"天文","evidence_ids":["ep:1"]}]}'
)


def _safe_excerpt(summary: str) -> bool:
    return (bool(summary) and not contains_sensitive(summary)
            and not any(word in summary for word in _PRIVATE_TOPICS))


class PersonModel:
    def __init__(self, store):
        self.store = store

    def list_items(self, owner_id: str) -> tuple[dict, ...]:
        items = []
        for key in self.store.keys():
            if not key.startswith(f"{ITEM_PREFIX}{owner_id}:"):
                continue
            item = self.store.get(key)
            if isinstance(item, dict) and item.get("owner_id") == owner_id:
                items.append(item)
        return tuple(sorted(items, key=lambda item: item["item_id"]))

    def put_item(self, owner_id: str, *, kind: str, context: str,
                 evidence_ids: list[str], need: str = "",
                 effective_response: str = "",
                 expression_preference: str = "",
                 topic: str = "") -> dict:
        if kind not in ("interaction", "interest") or not context.strip():
            raise ValueError("kind and context required")
        identity = f"{owner_id}\0{kind}\0{context}\0{need}\0{topic}"
        item_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        key = f"{ITEM_PREFIX}{owner_id}:{item_id}"
        previous = self.store.get(key)
        if isinstance(previous, dict) and previous.get("deleted"):
            return previous
        item = {
            "item_id": item_id, "owner_id": owner_id, "kind": kind,
            "context": (previous["context"] if isinstance(previous, dict) and previous.get("user_edited") else context[:80]),
            "need": (previous["need"] if isinstance(previous, dict) and previous.get("user_edited") else need[:80]),
            "effective_response": (
                previous["effective_response"]
                if isinstance(previous, dict) and previous.get("user_edited")
                else effective_response[:120]),
            "expression_preference": (
                previous["expression_preference"]
                if isinstance(previous, dict) and previous.get("user_edited")
                else expression_preference[:100]),
            "topic": (previous["topic"] if isinstance(previous, dict) and previous.get("user_edited") else topic[:80]),
            "evidence_ids": list(dict.fromkeys(evidence_ids))[:8],
            "inferred": True,
            "user_edited": bool(previous.get("user_edited"))
                           if isinstance(previous, dict) else False,
            "active": previous.get("active", True)
                      if isinstance(previous, dict) else True,
        }
        self.store.put(key, item)
        return item

    def update_item(self, owner_id: str, item_id: str, *,
                    active: bool | None = None,
                    context: str | None = None,
                    need: str | None = None,
                    topic: str | None = None,
                    effective_response: str | None = None,
                    expression_preference: str | None = None) -> bool:
        key = f"{ITEM_PREFIX}{owner_id}:{item_id}"
        item = self.store.get(key)
        if not isinstance(item, dict) or item.get("owner_id") != owner_id or item.get("deleted"):
            return False
        if active is not None:
            item["active"] = active
        for field, value, limit in (("context", context, 80), ("need", need, 80),
                                    ("topic", topic, 80)):
            if value is not None:
                item[field] = value[:limit]
        if effective_response is not None:
            item["effective_response"] = effective_response[:120]
        if expression_preference is not None:
            item["expression_preference"] = expression_preference[:100]
        if any(value is not None for value in (
                context, need, topic, effective_response, expression_preference)):
            item["user_edited"] = True
        self.store.put(key, item)
        return True

    def delete_item(self, owner_id: str, item_id: str) -> bool:
        key = f"{ITEM_PREFIX}{owner_id}:{item_id}"
        item = self.store.get(key)
        if not isinstance(item, dict) or item.get("owner_id") != owner_id:
            return False
        item["deleted"] = True
        item["active"] = False
        self.store.put(key, item)
        return True

    def relevant_hints(self, owner_id: str, query: str) -> tuple[str, ...]:
        hints = []
        for item in self.list_items(owner_id):
            if item.get("deleted") or not item["active"] or item["context"] not in query:
                continue
            if item["kind"] == "interest":
                hints.append(
                    f"可能也对{item['topic']}感兴趣；只在合适时自然试探，不当作事实")
            else:
                hints.append(
                    f"谈到{item['context']}、需要{item['need']}时，"
                    f"{item['effective_response']}曾有效；"
                    f"表达偏好：{item['expression_preference']}")
        return tuple(hints[:2])


def enqueue_review_if_due(store, owner_id: str, space_id: str, *,
                          now: datetime | None = None) -> bool:
    current = now or datetime.now(timezone.utc)
    key = f"{REVIEW_PREFIX}{owner_id}"
    last = store.get(key)
    if isinstance(last, str):
        try:
            if datetime.fromisoformat(last) + timedelta(days=7) > current:
                return False
        except ValueError:
            pass
    pending_key = f"{PENDING_PREFIX}{owner_id}"
    if store.get(pending_key):
        return False
    recent = [
        record for record in store.list_by_kind(
            MemoryKind.EPISODE, space_ids=(space_id,))
        if "chat" in record.tags
        and record.created_at >= current - timedelta(days=7)
        and _safe_excerpt(str(record.content.get("summary") or ""))
    ]
    if len(recent) < 2:
        return False
    store.put(pending_key, {
        "owner_id": owner_id, "space_id": space_id,
        "created_at": current.isoformat(),
    })
    return True


def run_pending_reviews(store, model) -> int:
    completed = 0
    with _REVIEW_LOCK:
        for key in store.keys():
            if not key.startswith(PENDING_PREFIX):
                continue
            job = store.get(key)
            if not isinstance(job, dict):
                continue
            owner_id, space_id = job["owner_id"], job["space_id"]
            since = datetime.fromisoformat(job["created_at"]) - timedelta(days=7)
            records = [
                record for record in store.list_by_kind(
                    MemoryKind.EPISODE, space_ids=(space_id,))
                if "chat" in record.tags
                and record.created_at >= since
                and _safe_excerpt(str(record.content.get("summary") or ""))
            ][:20]
            evidence = {record.key: record for record in records}
            events = [event for event in EmotionMemory(store).list_events(owner_id)
                      if event.get("trigger") not in ("亲密关系变化", "心理健康")][-20:]
            material = {
                "dialogues": [
                    {"id": record.key, "at": record.created_at.isoformat(),
                     "text": str(record.content.get("summary") or "")[:400]}
                    for record in reversed(records)],
                "emotion_events": [
                    {"event_id": event["event_id"], "emotion": event["emotion"],
                     "trigger": event["trigger"], "need": event["need"],
                     "response_ref": event["response_ref"]}
                    for event in events],
            }
            try:
                answer = model.reply(
                    json.dumps(material, ensure_ascii=False),
                    persona=_PROMPT, memories=(), history=())
                data = json.loads(str(answer).strip())
                if not isinstance(data, dict):
                    continue
                valid_events = {event["event_id"] for event in events}
                emotion = EmotionMemory(store)
                for feedback in data.get("feedback", [])[:10]:
                    if (isinstance(feedback, dict)
                            and feedback.get("event_id") in valid_events
                            and isinstance(feedback.get("effective"), bool)
                            and isinstance(feedback.get("response_style"), str)
                            and _safe_excerpt(feedback["response_style"])):
                        emotion.record_feedback(
                            owner_id, feedback["event_id"],
                            response_style=feedback["response_style"],
                            effective=feedback["effective"], kind="implicit")
                portrait = PersonModel(store)
                for row in data.get("profiles", [])[:5]:
                    if not isinstance(row, dict):
                        continue
                    ids = row.get("evidence_ids")
                    if not isinstance(ids, list) or len(set(ids) & evidence.keys()) < 2:
                        continue
                    if not all(isinstance(row.get(field), str) and row[field].strip()
                               for field in ("context", "need", "effective_response")):
                        continue
                    if not all(_safe_excerpt(str(row.get(field) or ""))
                               for field in ("context", "need", "effective_response")):
                        continue
                    if row.get("expression_preference") and not _safe_excerpt(
                            str(row["expression_preference"])):
                        continue
                    portrait.put_item(
                        owner_id, kind="interaction", context=row["context"],
                        need=row["need"],
                        effective_response=row["effective_response"],
                        expression_preference=str(
                            row.get("expression_preference") or ""),
                        evidence_ids=[item for item in ids if item in evidence])
                for row in data.get("interests", [])[:5]:
                    if not isinstance(row, dict):
                        continue
                    ids = row.get("evidence_ids")
                    if (not isinstance(ids, list)
                            or not any(item in evidence for item in ids)
                            or not isinstance(row.get("context"), str)
                            or not isinstance(row.get("topic"), str)):
                        continue
                    if not _safe_excerpt(row["context"]) or not _safe_excerpt(row["topic"]):
                        continue
                    portrait.put_item(
                        owner_id, kind="interest", context=row["context"],
                        topic=row["topic"],
                        evidence_ids=[item for item in ids if item in evidence])
                emotion.consolidate(owner_id)
                store.put(f"{REVIEW_PREFIX}{owner_id}",
                          datetime.now(timezone.utc).isoformat())
                store.delete(key)
                completed += 1
            except (TypeError, ValueError, KeyError):
                continue
    return completed


def schedule_reviews(store, model) -> None:
    def work():
        try:
            run_pending_reviews(store, model)
        except Exception:
            pass

    timer = threading.Timer(0.5, work)
    timer.daemon = True
    timer.start()
