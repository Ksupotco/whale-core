"""Owner emotion events and evidence-backed interaction preferences."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from whale_core.memory.store import MemoryStore

EVENT_PREFIX = "emotion:event:"
PATTERN_PREFIX = "emotion:pattern:"
REVIEW_PREFIX = "emotion:review:"
EVENT_DAYS = 90
REVIEW_DAYS = 7

_EMOTIONS = (
    ("难过", ("难过", "伤心", "失落", "崩溃")),
    ("焦虑", ("焦虑", "紧张", "压力大")),
    ("烦躁", ("烦死", "好烦", "烦躁", "生气")),
    ("疲惫", ("好累", "很累", "累死", "疲惫")),
    ("开心", ("开心", "高兴", "兴奋")),
)


def emotion_signal(text: str) -> tuple[str, str, str] | None:
    """Recognize only clear first-person signals; return broad context and need."""
    message = str(text or "").strip()
    if not message or ("我朋友" in message or "他说" in message):
        return None
    if "我" not in message and not message.startswith(
            ("烦死", "好烦", "好累", "累死", "难过", "伤心", "崩溃", "好开心")):
        return None
    emotion = next((label for label, words in _EMOTIONS
                    if any(word in message for word in words)), "")
    if not emotion:
        return None
    if any(word in message for word in ("分手", "恋爱", "亲密关系")):
        trigger = "亲密关系变化"
    elif any(word in message for word in ("心理咨询", "抑郁", "心理健康")):
        trigger = "心理健康"
    elif any(word in message for word in ("工作", "导师", "论文", "答辩", "考试")):
        trigger = "学习或工作"
    else:
        trigger = ""
    if any(word in message for word in ("给我建议", "帮我想办法", "怎么办")):
        need = "具体建议"
    elif any(word in message for word in ("陪我聊", "听我说", "别给建议")):
        need = "陪伴倾听"
    else:
        need = ""
    return emotion, trigger, need


class EmotionMemory:
    """Small KV-backed event store. It never reads raw conversations."""

    def __init__(self, store: MemoryStore):
        self.store = store

    def record(self, owner_id: str, *, source_id: str, conversation_id: str,
               emotion: str, trigger: str = "", need: str = "",
               response_style: str = "", space_id: str = "",
               now: datetime | None = None) -> str:
        if not owner_id or not source_id or not emotion:
            raise ValueError("owner_id, source_id and emotion are required")
        event_id = hashlib.sha256(
            f"{owner_id}\0{source_id}".encode("utf-8")).hexdigest()[:24]
        key = f"{EVENT_PREFIX}{owner_id}:{event_id}"
        if self.store.get(key) is None:
            stamp = now or datetime.now(timezone.utc)
            self.store.put(key, {
                "event_id": event_id, "owner_id": owner_id,
                "source_id": source_id, "conversation_id": conversation_id,
                "space_id": space_id, "occurred_at": stamp.isoformat(),
                "emotion": emotion, "trigger": trigger, "need": need,
                "response_ref": source_id, "response_style": response_style[:80],
                "feedback": "", "feedback_kind": "",
            })
        return event_id

    def list_events(self, owner_id: str, *, now: datetime | None = None) -> tuple[dict, ...]:
        current = now or datetime.now(timezone.utc)
        events = []
        for key in self.store.keys():
            if not key.startswith(f"{EVENT_PREFIX}{owner_id}:"):
                continue
            event = self.store.get(key)
            if not isinstance(event, dict) or event.get("owner_id") != owner_id:
                continue
            try:
                when = datetime.fromisoformat(event["occurred_at"])
            except (KeyError, TypeError, ValueError):
                continue
            if when + timedelta(days=EVENT_DAYS) > current:
                events.append(event)
        return tuple(sorted(events, key=lambda event: event["occurred_at"]))

    def prune_expired(self, owner_id: str, *, now: datetime | None = None) -> int:
        current = now or datetime.now(timezone.utc)
        removed = 0
        for key in self.store.keys():
            if not key.startswith(f"{EVENT_PREFIX}{owner_id}:"):
                continue
            event = self.store.get(key)
            if not isinstance(event, dict) or event.get("owner_id") != owner_id:
                continue
            try:
                when = datetime.fromisoformat(event["occurred_at"])
            except (KeyError, TypeError, ValueError):
                continue
            if when + timedelta(days=EVENT_DAYS) <= current:
                self.store.delete(key)
                removed += 1
        return removed

    def record_feedback(self, owner_id: str, event_id: str, *,
                        response_style: str, effective: bool,
                        kind: str = "explicit") -> bool:
        key = f"{EVENT_PREFIX}{owner_id}:{event_id}"
        event = self.store.get(key)
        if not isinstance(event, dict) or kind not in ("explicit", "implicit"):
            return False
        event["response_style"] = response_style[:80]
        event["feedback"] = "effective" if effective else "ineffective"
        event["feedback_kind"] = kind
        self.store.put(key, event)
        if not effective:
            negative_episodes = {
                (item["occurred_at"][:10], item["conversation_id"])
                for item in self.list_events(owner_id)
                if item.get("trigger") == event["trigger"]
                and item.get("need") == event["need"]
                and item.get("response_style") == response_style
                and item.get("feedback") == "ineffective"
                and item.get("feedback_kind") == "implicit"
            }
            if kind == "implicit" and len(negative_episodes) < 2:
                return True
            for pattern_key in self.store.keys():
                if not pattern_key.startswith(f"{PATTERN_PREFIX}{owner_id}:"):
                    continue
                pattern = self.store.get(pattern_key)
                if (isinstance(pattern, dict)
                        and pattern.get("owner_id") == owner_id
                        and pattern.get("trigger") == event["trigger"]
                        and pattern.get("need") == event["need"]
                        and pattern.get("response_style") == response_style):
                    pattern["active"] = False
                    self.store.put(pattern_key, pattern)
        return True

    def delete_event(self, owner_id: str, event_id: str) -> bool:
        key = f"{EVENT_PREFIX}{owner_id}:{event_id}"
        if self.store.get(key) is None:
            return False
        self.store.delete(key)
        for pattern_key in self.store.keys():
            if not pattern_key.startswith(f"{PATTERN_PREFIX}{owner_id}:"):
                continue
            pattern = self.store.get(pattern_key)
            if isinstance(pattern, dict) and event_id in pattern.get("evidence_ids", ()):
                pattern["active"] = False
                self.store.put(pattern_key, pattern)
        return True

    def update_event(self, owner_id: str, event_id: str, *,
                     emotion: str | None = None, trigger: str | None = None,
                     need: str | None = None) -> bool:
        key = f"{EVENT_PREFIX}{owner_id}:{event_id}"
        event = self.store.get(key)
        if not isinstance(event, dict) or event.get("owner_id") != owner_id:
            return False
        for field, value in (("emotion", emotion), ("trigger", trigger), ("need", need)):
            if value is not None:
                event[field] = value[:80]
        event["user_edited"] = True
        self.store.put(key, event)
        for pattern_key in self.store.keys():
            if not pattern_key.startswith(f"{PATTERN_PREFIX}{owner_id}:"):
                continue
            pattern = self.store.get(pattern_key)
            if (isinstance(pattern, dict)
                    and pattern.get("active")
                    and event_id in pattern.get("evidence_ids", ())):
                pattern["active"] = False
                self.store.put(pattern_key, pattern)
        return True

    def consolidate(self, owner_id: str, *, now: datetime | None = None) -> tuple[dict, ...]:
        """Promote two independent, matching events when at least one had feedback."""
        groups: dict[tuple[str, str, str], list[dict]] = {}
        for event in self.list_events(owner_id, now=now):
            key = (event["trigger"], event["need"], event["response_style"])
            if key[1] and key[2]:
                groups.setdefault(key, []).append(event)
        patterns = []
        for (trigger, need, style), events in groups.items():
            distinct = {(event["occurred_at"][:10], event["conversation_id"])
                        for event in events}
            positive = [event for event in events
                        if event["feedback"] == "effective"]
            if len(distinct) < 2 or not positive:
                continue
            pattern_id = hashlib.sha256(
                f"{owner_id}\0{trigger}\0{need}\0{style}".encode("utf-8")
            ).hexdigest()[:24]
            pattern_key = f"{PATTERN_PREFIX}{owner_id}:{pattern_id}"
            existing = self.store.get(pattern_key)
            if isinstance(existing, dict):
                if not existing.get("active"):
                    continue
                evidence_ids = [event["event_id"] for event in events]
                if existing.get("evidence_ids") != evidence_ids:
                    existing["evidence_ids"] = evidence_ids
                    self.store.put(pattern_key, existing)
                continue
            pattern = {
                "pattern_id": pattern_id, "owner_id": owner_id,
                "trigger": trigger, "need": need, "response_style": style,
                "inferred": True,
                "active": True,
                "evidence_ids": [event["event_id"] for event in events],
            }
            self.store.put(pattern_key, pattern)
            patterns.append(pattern)
        return tuple(patterns)

    def review_if_due(self, owner_id: str, *,
                      now: datetime | None = None) -> dict[str, int] | None:
        """Run the deterministic weekly maintenance when this owner next chats."""
        current = now or datetime.now(timezone.utc)
        key = f"{REVIEW_PREFIX}{owner_id}"
        last = self.store.get(key)
        if isinstance(last, str):
            try:
                if datetime.fromisoformat(last) + timedelta(days=REVIEW_DAYS) > current:
                    return None
            except ValueError:
                pass
        removed = self.prune_expired(owner_id, now=current)
        formed = len(self.consolidate(owner_id, now=current))
        self.store.put(key, current.isoformat())
        return {"expired_events": removed, "formed_patterns": formed}

    def relevant_patterns(self, owner_id: str, query: str) -> tuple[str, ...]:
        """Read only an active pattern whose broad context appears in this turn."""
        hints = []
        for key in self.store.keys():
            if not key.startswith(f"{PATTERN_PREFIX}{owner_id}:"):
                continue
            pattern = self.store.get(key)
            if (not isinstance(pattern, dict)
                    or pattern.get("owner_id") != owner_id
                    or not pattern.get("active")):
                continue
            trigger = str(pattern.get("trigger") or "")
            if trigger == "学习或工作" and not any(
                    word in query for word in ("工作", "导师", "论文", "答辩", "考试")):
                continue
            if trigger == "亲密关系变化" and not any(
                    word in query for word in ("关系", "恋爱", "分手")):
                continue
            if trigger == "心理健康" and not any(
                    word in query for word in ("心理", "咨询")):
                continue
            if not trigger:
                continue
            hints.append(f"在{trigger}、需要{pattern['need']}时，{pattern['response_style']}曾有效")
        return tuple(hints[:2])
