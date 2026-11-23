"""Scheduler: the only clock-driven loop in the system.

Jobs are persisted with a spec: ``{"every_min": 30}``, ``{"cron": "0 8 * * 1-5"}``
or ``{"at": <unix ms>}`` (one-shot). Handlers are registered by name; a job
points at a handler plus JSON args, so tasks, memory maintenance, exploration
and reminders all share one mechanism and one console view.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from ..kernel.db import Database, dumps, loads, migration
from ..kernel.errors import WhaleError, not_found
from ..kernel.events import EventBus
from ..kernel.ids import new_id, now_ms

log = logging.getLogger("whale.scheduler")

migration(
    "scheduler.001",
    """CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, handler TEXT NOT NULL, args TEXT NOT NULL DEFAULT '{}',
        spec TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, system INTEGER NOT NULL DEFAULT 0,
        next_at INTEGER, last_at INTEGER, last_status TEXT NOT NULL DEFAULT '', last_error TEXT NOT NULL DEFAULT '',
        runs INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL);""",
)

JobHandler = Callable[[dict[str, Any]], Awaitable[Any]]


def _field(expr: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in expr.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            step = int(s)
        if part in ("*", ""):
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-", 1))
        else:
            a = b = int(part)
        if a < lo or b > hi or a > b:
            raise ValueError(f"cron field out of range: {expr}")
        out.update(range(a, b + 1, step))
    return out


def cron_next(expr: str, after: datetime) -> datetime:
    """Next time strictly after ``after`` matching a 5-field cron (min hour dom mon dow; dow 0/7=Sun)."""
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError("cron needs 5 fields")
    minutes, hours = _field(fields[0], 0, 59), _field(fields[1], 0, 23)
    dom, mon = _field(fields[2], 1, 31), _field(fields[3], 1, 12)
    dow = {d % 7 for d in _field(fields[4], 0, 7)}
    t = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(366 * 24 * 60):
        if t.month in mon and t.day in dom and (t.isoweekday() % 7) in dow and t.hour in hours \
                and t.minute in minutes:
            return t
        t += timedelta(minutes=1)
    raise ValueError("cron never fires")


class Scheduler:
    def __init__(self, db: Database, bus: EventBus, tz: str = "Asia/Shanghai", tick_s: float = 10.0) -> None:
        self.db = db
        self.bus = bus
        self.tz = tz
        self.tick_s = tick_s
        self.handlers: dict[str, JobHandler] = {}
        self._loop: asyncio.Task[None] | None = None
        self._running: set[str] = set()

    def handler(self, name: str, fn: JobHandler) -> None:
        self.handlers[name] = fn

    def _zone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.tz)
        except Exception:
            return ZoneInfo("UTC")

    def next_at(self, spec: dict[str, Any], after_ms: int | None = None) -> int | None:
        after_ms = after_ms or now_ms()
        if "at" in spec:
            at = int(spec["at"])
            return at if at > after_ms - 60_000 else None
        if "every_min" in spec:
            return after_ms + int(float(spec["every_min"]) * 60_000)
        if "cron" in spec:
            base = datetime.fromtimestamp(after_ms / 1000, self._zone())
            return int(cron_next(spec["cron"], base).timestamp() * 1000)
        raise WhaleError("invalid_params", "schedule spec needs at / every_min / cron")

    def add(self, name: str, handler: str, spec: dict[str, Any], args: dict[str, Any] | None = None, *,
            system: bool = False, job_id: str | None = None, enabled: bool = True) -> dict[str, Any]:
        if handler not in self.handlers:
            raise WhaleError("invalid_params", f"unknown job handler {handler}")
        try:
            nxt = self.next_at(spec)
        except ValueError as exc:
            raise WhaleError("invalid_params", str(exc)) from exc
        jid = job_id or new_id("job")
        self.db.execute(
            "INSERT INTO jobs(id,name,handler,args,spec,enabled,system,next_at,created_at) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, spec=excluded.spec, args=excluded.args, "
            "next_at=excluded.next_at, enabled=excluded.enabled",
            (jid, name, handler, dumps(args or {}), dumps(spec), int(enabled), int(system), nxt, now_ms()))
        job = self.get(jid)
        self.bus.publish_event("schedule.changed", {"job": job})
        return job

    def ensure_system(self, job_id: str, name: str, handler: str, spec: dict[str, Any], enabled: bool) -> None:
        existing = self.db.one("SELECT spec, enabled FROM jobs WHERE id=?", (job_id,))
        if existing and loads(existing[0]) == spec and bool(existing[1]) == enabled:
            return
        self.add(name, handler, spec, system=True, job_id=job_id, enabled=enabled)

    def get(self, job_id: str) -> dict[str, Any]:
        r = self.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not r:
            raise not_found("job", job_id)
        return dict(r) | {"args": loads(r["args"], {}), "spec": loads(r["spec"], {}), "enabled": bool(r["enabled"]),
                          "system": bool(r["system"])}

    def list(self) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT id FROM jobs ORDER BY system DESC, next_at")
        return [self.get(r[0]) for r in rows]

    def set_enabled(self, job_id: str, enabled: bool) -> dict[str, Any]:
        job = self.get(job_id)
        nxt = self.next_at(job["spec"]) if enabled else job["next_at"]
        self.db.execute("UPDATE jobs SET enabled=?, next_at=? WHERE id=?", (int(enabled), nxt, job_id))
        job = self.get(job_id)
        self.bus.publish_event("schedule.changed", {"job": job})
        return job

    def remove(self, job_id: str) -> None:
        self.db.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        self.bus.publish_event("schedule.changed", {"removed": job_id})

    async def run_now(self, job_id: str) -> dict[str, Any]:
        await self._fire(self.get(job_id))
        return self.get(job_id)

    def start(self) -> None:
        self._loop = asyncio.create_task(self._tick_loop(), name="scheduler")

    async def stop(self) -> None:
        if self._loop:
            self._loop.cancel()
            try:
                await self._loop
            except (asyncio.CancelledError, Exception):
                pass

    async def _tick_loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("scheduler tick failed")
            await asyncio.sleep(self.tick_s)

    async def tick(self) -> int:
        due = self.db.query("SELECT id FROM jobs WHERE enabled=1 AND next_at IS NOT NULL AND next_at<=?",
                            (now_ms(),))
        for (jid,) in due:
            if jid not in self._running:
                asyncio.create_task(self._fire(self.get(jid)))
        return len(due)

    async def _fire(self, job: dict[str, Any]) -> None:
        jid = job["id"]
        if jid in self._running:
            return
        self._running.add(jid)
        # Advance first so a crash inside the handler cannot cause a tight re-fire loop.
        nxt = None if "at" in job["spec"] else self.next_at(job["spec"])
        self.db.execute("UPDATE jobs SET next_at=?, last_at=?, runs=runs+1 WHERE id=?", (nxt, now_ms(), jid))
        status, error = "ok", ""
        await self.bus.emit("schedule.fired", {"job_id": jid, "name": job["name"], "handler": job["handler"]})
        try:
            await self.handlers[job["handler"]](job["args"] | {"_job_id": jid})
        except Exception as exc:
            status, error = "error", repr(exc)
            log.warning("job %s failed: %s", job["name"], error)
        finally:
            self._running.discard(jid)
        enabled = 0 if "at" in job["spec"] else int(job["enabled"])
        self.db.execute("UPDATE jobs SET last_status=?, last_error=?, enabled=? WHERE id=?",
                        (status, error[:500], enabled, jid))
        self.bus.publish_event("schedule.changed", {"job": self.get(jid)})
