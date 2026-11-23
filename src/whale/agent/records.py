"""Conversations, messages, runs and steps — the agent's observable record."""

from __future__ import annotations

from typing import Any

from ..kernel.db import Database, dumps, loads, migration
from ..kernel.errors import not_found
from ..kernel.events import EventBus
from ..kernel.ids import new_id, now_ms

migration(
    "agent.001",
    """
    CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', channel TEXT NOT NULL DEFAULT 'console',
        peer TEXT NOT NULL DEFAULT 'owner', persona_id TEXT, created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL, turns INTEGER NOT NULL DEFAULT 0, meta TEXT NOT NULL DEFAULT '{}');
    CREATE INDEX IF NOT EXISTS conversations_updated ON conversations(updated_at);
    CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
        run_id TEXT, ts INTEGER NOT NULL, meta TEXT NOT NULL DEFAULT '{}');
    CREATE INDEX IF NOT EXISTS messages_conv ON messages(conversation_id, ts);
    CREATE TABLE IF NOT EXISTS runs (
        id TEXT PRIMARY KEY, conversation_id TEXT, parent_id TEXT, task_id TEXT, mode TEXT NOT NULL,
        status TEXT NOT NULL, input TEXT NOT NULL DEFAULT '', output TEXT NOT NULL DEFAULT '',
        error TEXT NOT NULL DEFAULT '', channel TEXT NOT NULL DEFAULT 'console', model TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL, ended_at INTEGER, stats TEXT NOT NULL DEFAULT '{}');
    CREATE INDEX IF NOT EXISTS runs_created ON runs(created_at);
    CREATE INDEX IF NOT EXISTS runs_task ON runs(task_id);
    CREATE TABLE IF NOT EXISTS steps (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, input TEXT NOT NULL DEFAULT '{}',
        output TEXT NOT NULL DEFAULT '{}', started_at INTEGER NOT NULL, ended_at INTEGER);
    CREATE INDEX IF NOT EXISTS steps_run ON steps(run_id, seq);
    """,
)

STEP_KINDS = ("recall", "think", "tool", "approval", "reply", "delegate", "plan", "note")


class Conversations:
    def __init__(self, db: Database, bus: EventBus) -> None:
        self.db = db
        self.bus = bus

    def ensure(self, conversation_id: str | None, *, channel: str = "console", peer: str = "owner",
               title: str = "", persona_id: str | None = None) -> dict[str, Any]:
        if conversation_id:
            row = self.db.one("SELECT * FROM conversations WHERE id=?", (conversation_id,))
            if row:
                return self._conv(row)
        cid = conversation_id or new_id("conv")
        now = now_ms()
        self.db.execute(
            "INSERT INTO conversations(id,title,channel,peer,persona_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
            (cid, title, channel, peer, persona_id, now, now))
        return self.get(cid)

    def get(self, conversation_id: str) -> dict[str, Any]:
        row = self.db.one("SELECT * FROM conversations WHERE id=?", (conversation_id,))
        if not row:
            raise not_found("conversation", conversation_id)
        return self._conv(row)

    def list(self, limit: int = 50, channel: str | None = None) -> list[dict[str, Any]]:
        if channel:
            rows = self.db.query("SELECT * FROM conversations WHERE channel=? ORDER BY updated_at DESC LIMIT ?",
                                 (channel, limit))
        else:
            rows = self.db.query("SELECT * FROM conversations ORDER BY updated_at DESC LIMIT ?", (limit,))
        out = [self._conv(r) for r in rows]
        for conv in out:
            last = self.db.one("SELECT role, content FROM messages WHERE conversation_id=? AND role!='system' "
                               "ORDER BY ts DESC, id DESC LIMIT 1", (conv["id"],))
            conv["preview"] = {"role": last[0], "text": last[1][:80]} if last else None
        return out

    def add_message(self, conversation_id: str, role: str, content: str, *, run_id: str | None = None,
                    meta: dict[str, Any] | None = None) -> dict[str, Any]:
        mid = new_id("msg")
        now = now_ms()
        self.db.execute("INSERT INTO messages(id,conversation_id,role,content,run_id,ts,meta) VALUES (?,?,?,?,?,?,?)",
                        (mid, conversation_id, role, content, run_id, now, dumps(meta or {})))
        bump = 1 if role == "user" else 0
        title_sql = ", title=CASE WHEN title='' THEN ? ELSE title END" if role == "user" else ""
        args: list[Any] = [now, bump]
        if role == "user":
            args.append(content[:40])
        args.append(conversation_id)
        self.db.execute(f"UPDATE conversations SET updated_at=?, turns=turns+?{title_sql} WHERE id=?", args)
        return {"id": mid, "conversation_id": conversation_id, "role": role, "content": content,
                "run_id": run_id, "ts": now, "meta": meta or {}}

    def messages(self, conversation_id: str, limit: int = 100, before: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM messages WHERE conversation_id=?"
        args: list[Any] = [conversation_id]
        if before:
            sql += " AND ts < ?"
            args.append(before)
        sql += " ORDER BY ts DESC, id DESC LIMIT ?"
        args.append(limit)
        rows = self.db.query(sql, args)
        out = [dict(r) | {"meta": loads(r["meta"], {})} for r in rows]
        return list(reversed(out))

    def delete(self, conversation_id: str) -> None:
        self.db.execute("DELETE FROM messages WHERE conversation_id=?", (conversation_id,))
        self.db.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))

    def update(self, conversation_id: str, **fields: Any) -> dict[str, Any]:
        for key in ("title", "persona_id"):
            if key in fields:
                self.db.execute(f"UPDATE conversations SET {key}=? WHERE id=?", (fields[key], conversation_id))
        return self.get(conversation_id)

    @staticmethod
    def _conv(r: Any) -> dict[str, Any]:
        d = dict(r)
        d["meta"] = loads(d["meta"], {})
        return d


class RunLedger:
    def __init__(self, db: Database, bus: EventBus) -> None:
        self.db = db
        self.bus = bus
        self._seq: dict[str, int] = {}

    async def create(self, *, mode: str, input: str, conversation_id: str | None = None,
                     parent_id: str | None = None, task_id: str | None = None, channel: str = "console",
                     model: str = "", run_id: str | None = None) -> dict[str, Any]:
        rid = run_id or new_id("run")
        now = now_ms()
        self.db.execute(
            "INSERT INTO runs(id,conversation_id,parent_id,task_id,mode,status,input,channel,model,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rid, conversation_id, parent_id, task_id, mode, "running", input, channel, model, now))
        run = self.get(rid)
        await self.bus.emit("run.started", {"run": run})
        return run

    async def finish(self, run_id: str, status: str, *, output: str = "", error: str = "",
                     stats: dict[str, Any] | None = None) -> dict[str, Any]:
        self.db.execute("UPDATE runs SET status=?, output=?, error=?, ended_at=?, stats=? WHERE id=?",
                        (status, output, error, now_ms(), dumps(stats or {}), run_id))
        self._seq.pop(run_id, None)
        run = self.get(run_id)
        await self.bus.emit(f"run.{status}", {"run": run})
        return run

    async def set_status(self, run_id: str, status: str) -> None:
        self.db.execute("UPDATE runs SET status=? WHERE id=?", (status, run_id))
        await self.bus.emit("run.status", {"run_id": run_id, "status": status})

    async def step(self, run_id: str, kind: str, title: str, input: Any = None) -> dict[str, Any]:
        seq = self._seq.get(run_id, 0) + 1
        self._seq[run_id] = seq
        sid = new_id("step")
        now = now_ms()
        self.db.execute(
            "INSERT INTO steps(id,run_id,seq,kind,title,status,input,started_at) VALUES (?,?,?,?,?,?,?,?)",
            (sid, run_id, seq, kind, title, "running", dumps(input if input is not None else {}), now))
        step = {"id": sid, "run_id": run_id, "seq": seq, "kind": kind, "title": title, "status": "running",
                "input": input if input is not None else {}, "output": {}, "started_at": now, "ended_at": None}
        await self.bus.emit("run.step", {"step": step})
        return step

    async def end_step(self, step: dict[str, Any], status: str = "done", output: Any = None,
                       title: str | None = None) -> dict[str, Any]:
        step = dict(step)
        step["status"] = status
        step["output"] = output if output is not None else {}
        step["ended_at"] = now_ms()
        if title:
            step["title"] = title
        self.db.execute("UPDATE steps SET status=?, output=?, ended_at=?, title=? WHERE id=?",
                        (status, dumps(step["output"]), step["ended_at"], step["title"], step["id"]))
        await self.bus.emit("run.step", {"step": step})
        return step

    def get(self, run_id: str) -> dict[str, Any]:
        r = self.db.one("SELECT * FROM runs WHERE id=?", (run_id,))
        if not r:
            raise not_found("run", run_id)
        d = dict(r)
        d["stats"] = loads(d["stats"], {})
        return d

    def steps(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM steps WHERE run_id=? ORDER BY seq", (run_id,))
        return [dict(r) | {"input": loads(r["input"], {}), "output": loads(r["output"], {})} for r in rows]

    def list(self, *, limit: int = 50, mode: str | None = None, status: str | None = None,
             task_id: str | None = None, parent_id: str | None = None) -> list[dict[str, Any]]:
        where, args = [], []
        for col, val in (("mode", mode), ("status", status), ("task_id", task_id), ("parent_id", parent_id)):
            if val:
                where.append(f"{col}=?")
                args.append(val)
        clause = " WHERE " + " AND ".join(where) if where else ""
        rows = self.db.query(f"SELECT * FROM runs{clause} ORDER BY created_at DESC LIMIT ?", (*args, limit))
        return [dict(r) | {"stats": loads(r["stats"], {})} for r in rows]

    def recover(self) -> int:
        """Runs left 'running' by a crash are marked interrupted (truthful state, no fake completion)."""
        cur = self.db.execute(
            "UPDATE runs SET status='interrupted', ended_at=?, error='service restarted' "
            "WHERE status IN ('running','waiting')", (now_ms(),))
        self.db.execute("UPDATE steps SET status='interrupted', ended_at=? WHERE status='running'", (now_ms(),))
        return cur.rowcount
