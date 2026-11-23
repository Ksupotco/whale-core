"""Policy and approvals: the single gate in front of every capability call.

Decision order: explicit user rule (glob, most specific wins) → default by
risk. ``ask`` creates a persisted Approval that any connected client can
resolve; the calling run waits on it.
"""

from __future__ import annotations

import asyncio
import fnmatch
from typing import Any

from ..kernel.db import Database, dumps, loads, migration
from ..kernel.errors import WhaleError, not_found
from ..kernel.events import EventBus
from ..kernel.ids import new_id, now_ms
from ..kernel.config import Config
from .spec import CapabilitySpec, Risk

DEFAULTS = {Risk.read: "allow", Risk.write: "allow", Risk.external: "ask", Risk.dangerous: "ask"}

migration(
    "approvals.001",
    """
    CREATE TABLE IF NOT EXISTS approvals (
        id TEXT PRIMARY KEY, run_id TEXT, capability TEXT NOT NULL, args TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '', risk TEXT NOT NULL, status TEXT NOT NULL,
        created_at INTEGER NOT NULL, resolved_at INTEGER, resolved_by TEXT, note TEXT);
    CREATE INDEX IF NOT EXISTS approvals_status ON approvals(status, created_at);
    """,
)


class Policy:
    def __init__(self, config: Config) -> None:
        self.config = config

    def decide(self, spec: CapabilitySpec, mode: str = "chat") -> str:
        rules = self.config.value.policy.rules
        best: tuple[int, str] | None = None
        for pattern, verdict in rules.items():
            if fnmatch.fnmatchcase(spec.name, pattern):
                specificity = len(pattern.replace("*", ""))
                if best is None or specificity > best[0]:
                    best = (specificity, verdict)
        if best:
            return best[1]
        if mode == "explore" and spec.risk is not Risk.read:
            return "deny"  # exploration is read-only by construction
        return DEFAULTS[spec.risk]


class Approvals:
    def __init__(self, db: Database, bus: EventBus) -> None:
        self.db = db
        self.bus = bus
        self._waiters: dict[str, asyncio.Future[bool]] = {}

    def expire_orphans(self) -> int:
        """Pending approvals from a previous process cannot be resumed in-place."""
        cur = self.db.execute(
            "UPDATE approvals SET status='expired', resolved_at=?, note='service restarted' WHERE status='pending'",
            (now_ms(),),
        )
        return cur.rowcount

    async def request(self, spec: CapabilitySpec, args: dict[str, Any], *, run_id: str, reason: str = "") -> dict[str, Any]:
        aid = new_id("aprv")
        row = {"id": aid, "run_id": run_id, "capability": spec.name, "args": args, "reason": reason,
               "risk": spec.risk.value, "status": "pending", "created_at": now_ms(),
               "title": spec.title or spec.name, "provider": spec.provider}
        self.db.execute(
            "INSERT INTO approvals(id,run_id,capability,args,reason,risk,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (aid, run_id, spec.name, dumps(args), reason, spec.risk.value, "pending", row["created_at"]),
        )
        self._waiters[aid] = asyncio.get_running_loop().create_future()
        await self.bus.emit("approval.requested", row)
        return row

    async def wait(self, approval_id: str, timeout: float | None = None) -> bool:
        fut = self._waiters.get(approval_id)
        if fut is None:
            row = self.get(approval_id)
            return row["status"] == "approved"
        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout)
        except asyncio.TimeoutError:
            await self.resolve(approval_id, False, by="system", note="timed out")
            return False

    async def resolve(self, approval_id: str, approve: bool, *, by: str = "owner", note: str = "") -> dict[str, Any]:
        row = self.get(approval_id)
        if row["status"] != "pending":
            raise WhaleError("conflict", f"approval already {row['status']}")
        status = "approved" if approve else "denied"
        self.db.execute(
            "UPDATE approvals SET status=?, resolved_at=?, resolved_by=?, note=? WHERE id=?",
            (status, now_ms(), by, note, approval_id),
        )
        fut = self._waiters.pop(approval_id, None)
        if fut and not fut.done():
            fut.set_result(approve)
        row = self.get(approval_id)
        await self.bus.emit("approval.resolved", row)
        return row

    def get(self, approval_id: str) -> dict[str, Any]:
        r = self.db.one("SELECT * FROM approvals WHERE id=?", (approval_id,))
        if not r:
            raise not_found("approval", approval_id)
        return self._row(r)

    def list(self, status: str | None = "pending", limit: int = 100) -> list[dict[str, Any]]:
        if status:
            rows = self.db.query("SELECT * FROM approvals WHERE status=? ORDER BY created_at DESC LIMIT ?",
                                 (status, limit))
        else:
            rows = self.db.query("SELECT * FROM approvals ORDER BY created_at DESC LIMIT ?", (limit,))
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(r: Any) -> dict[str, Any]:
        d = dict(r)
        d["args"] = loads(d["args"], {})
        return d
