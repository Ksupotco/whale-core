"""EventBus: the nervous system.

Every observable state change is an Event with a dotted topic
(``run.step``, ``memory.created`` …). Subscribers match topics with glob
patterns (``run.*``). Selected topics are persisted to an append-only event
log so the console can replay recent activity after a reload.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .ids import now_ms, ulid

log = logging.getLogger("whale.events")

Handler = Callable[["Event"], Awaitable[None] | None]


@dataclass(slots=True)
class Event:
    topic: str
    data: dict[str, Any]
    id: str = field(default_factory=ulid)
    ts: int = field(default_factory=now_ms)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "topic": self.topic, "ts": self.ts, "data": self.data}


class Subscription:
    """A queue-backed subscription, used by streaming clients (WS/SSE)."""

    def __init__(self, bus: "EventBus", patterns: list[str], maxsize: int = 1000) -> None:
        self.bus = bus
        self.patterns = patterns
        self.queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0

    def matches(self, topic: str) -> bool:
        return any(fnmatch.fnmatchcase(topic, p) for p in self.patterns)

    def offer(self, event: Event) -> None:
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            # Slow consumers lose the oldest events rather than blocking the agent.
            self.dropped += 1
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(event)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    async def get(self) -> Event:
        return await self.queue.get()

    def close(self) -> None:
        self.bus._streams.discard(self)


class EventBus:
    # Topics whose payloads are large or high-frequency and not worth persisting.
    EPHEMERAL = ("run.delta", "agent.state", "link.*")

    def __init__(self, log_store: "EventLog | None" = None, recent: int = 500) -> None:
        self._handlers: list[tuple[str, Handler]] = []
        self._streams: set[Subscription] = set()
        self._recent: deque[Event] = deque(maxlen=recent)
        self._log = log_store

    def on(self, pattern: str, handler: Handler) -> Callable[[], None]:
        entry = (pattern, handler)
        self._handlers.append(entry)
        return lambda: self._handlers.remove(entry) if entry in self._handlers else None

    def stream(self, patterns: list[str] | None = None) -> Subscription:
        sub = Subscription(self, patterns or ["*"])
        self._streams.add(sub)
        return sub

    def recent(self, pattern: str = "*", limit: int = 100) -> list[Event]:
        items = [e for e in self._recent if fnmatch.fnmatchcase(e.topic, pattern)]
        return items[-limit:]

    async def emit(self, topic: str, data: dict[str, Any] | None = None) -> Event:
        event = Event(topic, data or {})
        self.publish(event)
        for pattern, handler in list(self._handlers):
            if fnmatch.fnmatchcase(topic, pattern):
                try:
                    result = handler(event)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:  # a broken subscriber must never break the emitter
                    log.exception("event handler failed for %s", topic)
        return event

    def publish_event(self, topic: str, data: dict[str, Any]) -> Event:
        event = Event(topic, data)
        self.publish(event)
        return event

    def publish(self, event: Event) -> None:
        """Synchronous fan-out to streams and the log (no handler awaiting)."""
        ephemeral = any(fnmatch.fnmatchcase(event.topic, p) for p in self.EPHEMERAL)
        if not ephemeral:
            self._recent.append(event)
            if self._log is not None:
                self._log.append(event)
        for sub in list(self._streams):
            if sub.matches(event.topic):
                sub.offer(event)


class EventLog:
    """Append-only persisted log of non-ephemeral events (bounded retention)."""

    def __init__(self, db: "Database", keep: int = 20000) -> None:  # noqa: F821
        self.db = db
        self.keep = keep
        self._count = 0

    def append(self, event: Event) -> None:
        self.db.execute(
            "INSERT INTO events(id, topic, ts, data) VALUES (?,?,?,?)",
            (event.id, event.topic, event.ts, json.dumps(event.data, ensure_ascii=False, default=str)),
        )
        self._count += 1
        if self._count % 500 == 0:
            self.db.execute(
                "DELETE FROM events WHERE id IN (SELECT id FROM events ORDER BY id DESC LIMIT -1 OFFSET ?)",
                (self.keep,),
            )

    def query(self, pattern: str = "*", limit: int = 200, before: str | None = None) -> list[dict[str, Any]]:
        like = pattern.replace("*", "%")
        sql = "SELECT id, topic, ts, data FROM events WHERE topic LIKE ?"
        args: list[Any] = [like]
        if before:
            sql += " AND id < ?"
            args.append(before)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        rows = self.db.query(sql, args)
        return [{"id": r[0], "topic": r[1], "ts": r[2], "data": json.loads(r[3])} for r in rows]
