"""Memory store: items + relations + full-text index + embeddings.

One table for every kind of memory. ``status`` carries the lifecycle:
candidate → active → superseded/archived, or forgotten (tombstone: content
wiped, id kept so late writers cannot resurrect it).
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Iterable

from ..kernel.db import Database, dumps, loads, migration
from ..kernel.errors import not_found
from ..kernel.ids import new_id, now_ms
from .scopes import sql_filter

KINDS = ("fact", "preference", "episode", "knowledge", "diary", "skill", "person", "style")
STATUSES = ("candidate", "active", "superseded", "archived", "forgotten")
RELATIONS = ("related", "supersedes", "derived_from", "about", "mentions")
DAY_MS = 86_400_000

migration(
    "memory.001",
    """
    CREATE TABLE IF NOT EXISTS memories (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL, content TEXT NOT NULL, subject TEXT NOT NULL DEFAULT 'user',
        status TEXT NOT NULL DEFAULT 'active', importance REAL NOT NULL DEFAULT 0.5,
        confidence REAL NOT NULL DEFAULT 0.8, strength REAL NOT NULL DEFAULT 0.6,
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, last_access_at INTEGER NOT NULL,
        access_count INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL DEFAULT '{}',
        tags TEXT NOT NULL DEFAULT '[]', embedding TEXT, scope TEXT NOT NULL DEFAULT 'owner');
    CREATE INDEX IF NOT EXISTS memories_status ON memories(status, kind, updated_at);
    CREATE TABLE IF NOT EXISTS memory_links (
        src TEXT NOT NULL, dst TEXT NOT NULL, relation TEXT NOT NULL, weight REAL NOT NULL DEFAULT 0.5,
        created_at INTEGER NOT NULL, PRIMARY KEY (src, dst, relation));
    CREATE INDEX IF NOT EXISTS memory_links_dst ON memory_links(dst);
    CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(id UNINDEXED, content, tags, tokenize='trigram');
    """,
)


@dataclass
class MemoryItem:
    kind: str
    content: str
    subject: str = "user"
    status: str = "active"
    importance: float = 0.5
    confidence: float = 0.8
    strength: float = 0.6
    source: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    scope: str = "owner"
    id: str = field(default_factory=lambda: new_id("mem"))
    created_at: int = field(default_factory=now_ms)
    updated_at: int = field(default_factory=now_ms)
    last_access_at: int = field(default_factory=now_ms)
    access_count: int = 0
    embedding: list[float] | None = None

    def current_strength(self, half_life_days: float, at: int | None = None) -> float:
        days = max(0.0, ((at or now_ms()) - self.last_access_at) / DAY_MS)
        return self.strength * math.pow(0.5, days / max(half_life_days, 0.1))

    def to_dict(self, half_life_days: float = 30.0) -> dict[str, Any]:
        d = asdict(self)
        d.pop("embedding", None)
        d["strength_now"] = round(self.current_strength(half_life_days), 4)
        return d


def _row_to_item(r: Any) -> MemoryItem:
    return MemoryItem(
        id=r["id"], kind=r["kind"], content=r["content"], subject=r["subject"], status=r["status"],
        importance=r["importance"], confidence=r["confidence"], strength=r["strength"],
        created_at=r["created_at"], updated_at=r["updated_at"], last_access_at=r["last_access_at"],
        access_count=r["access_count"], source=loads(r["source"], {}), tags=loads(r["tags"], []),
        embedding=loads(r["embedding"]) if r["embedding"] else None, scope=r["scope"],
    )


_LATIN = re.compile(r"[a-z0-9][a-z0-9_\-]*")
_CJK = re.compile(r"[\u4e00-\u9fff]+")
# Characters that carry little meaning on their own in queries.
_STOP = set("我你他她它的了是在有和就不也都很吗呢吧啊呀么什怎哪谁这那个些一二上下来去说要会能可以对过到着把被给让叫名字最近现在今天平时觉得还")


class MemoryStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._fts = db.fts5 and db.one(
            "SELECT 1 FROM sqlite_master WHERE name='memories_fts'") is not None
        self._vec_cache: dict[str, list[float]] | None = None
        self._vec_scope: dict[str, str] = {}

    # -- writes ---------------------------------------------------------------
    def add(self, item: MemoryItem) -> MemoryItem:
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO memories(id,kind,content,subject,status,importance,confidence,strength,created_at,"
                "updated_at,last_access_at,access_count,source,tags,embedding,scope) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (item.id, item.kind, item.content, item.subject, item.status, item.importance, item.confidence,
                 item.strength, item.created_at, item.updated_at, item.last_access_at, item.access_count,
                 dumps(item.source), dumps(item.tags), dumps(item.embedding) if item.embedding else None,
                 item.scope),
            )
            if self._fts:
                conn.execute("INSERT INTO memories_fts(id, content, tags) VALUES (?,?,?)",
                             (item.id, item.content, " ".join(item.tags)))
        if self._vec_cache is not None and item.embedding and item.status in ("active", "candidate"):
            self._vec_cache[item.id] = item.embedding
            self._vec_scope[item.id] = item.scope
        return item

    def update(self, mem_id: str, **fields: Any) -> MemoryItem:
        item = self.get(mem_id)
        allowed = {"content", "kind", "subject", "status", "importance", "confidence", "strength", "tags",
                   "source", "embedding", "last_access_at", "access_count"}
        sets, args = [], []
        changes = {k: v for k, v in fields.items() if k in allowed}
        item = replace(item, **changes)
        for key, value in changes.items():
            sets.append(f"{key}=?")
            args.append(dumps(value) if key in ("tags", "source", "embedding") and value is not None else value)
        if not sets:
            return item
        item.updated_at = now_ms()
        sets.append("updated_at=?")
        args.append(item.updated_at)
        with self.db.transaction() as conn:
            conn.execute(f"UPDATE memories SET {', '.join(sets)} WHERE id=?", (*args, mem_id))
            if self._fts and ("content" in fields or "tags" in fields):
                conn.execute("DELETE FROM memories_fts WHERE id=?", (mem_id,))
                conn.execute("INSERT INTO memories_fts(id, content, tags) VALUES (?,?,?)",
                             (mem_id, item.content, " ".join(item.tags)))
        if self._vec_cache is not None:
            if item.status in ("active", "candidate") and item.embedding:
                self._vec_cache[mem_id] = item.embedding
            else:
                self._vec_cache.pop(mem_id, None)
        return item

    def touch(self, ids: Iterable[str], half_life_days: float) -> None:
        """Retrieval reinforces memories (spaced-repetition style)."""
        at = now_ms()
        for mem_id in ids:
            try:
                item = self.get(mem_id)
            except Exception:
                continue
            boosted = min(1.0, item.current_strength(half_life_days, at) + 0.15)
            self.db.execute(
                "UPDATE memories SET strength=?, last_access_at=?, access_count=access_count+1 WHERE id=?",
                (boosted, at, mem_id),
            )

    def forget(self, mem_id: str) -> None:
        self.get(mem_id)
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE memories SET content='', status='forgotten', embedding=NULL, source='{}', tags='[]', "
                "updated_at=? WHERE id=?", (now_ms(), mem_id))
            conn.execute("DELETE FROM memory_links WHERE src=? OR dst=?", (mem_id, mem_id))
            if self._fts:
                conn.execute("DELETE FROM memories_fts WHERE id=?", (mem_id,))
        if self._vec_cache is not None:
            self._vec_cache.pop(mem_id, None)

    def link(self, src: str, dst: str, relation: str = "related", weight: float = 0.5) -> None:
        if src == dst:
            return
        self.db.execute(
            "INSERT INTO memory_links(src,dst,relation,weight,created_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(src,dst,relation) DO UPDATE SET weight=max(weight, excluded.weight)",
            (src, dst, relation, weight, now_ms()),
        )

    # -- reads ----------------------------------------------------------------
    def get(self, mem_id: str) -> MemoryItem:
        r = self.db.one("SELECT * FROM memories WHERE id=?", (mem_id,))
        if not r:
            raise not_found("memory", mem_id)
        return _row_to_item(r)

    def many(self, ids: list[str]) -> list[MemoryItem]:
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        rows = self.db.query(f"SELECT * FROM memories WHERE id IN ({marks})", ids)
        by_id = {r["id"]: _row_to_item(r) for r in rows}
        return [by_id[i] for i in ids if i in by_id]

    def list(self, *, kind: str | None = None, status: str | None = "active", query: str = "",
             limit: int = 50, offset: int = 0, order: str = "updated_at DESC",
             scope: str | None = None) -> tuple[list[MemoryItem], int]:
        where, args = [], []
        if scope == "group":
            where.append("scope LIKE 'group:%'")
        elif scope == "person":
            where.append("scope NOT IN ('owner','shared') AND scope NOT LIKE 'group:%'")
        elif scope:
            where.append("scope=?")
            args.append(scope)
        if kind:
            where.append("kind=?")
            args.append(kind)
        if status:
            where.append("status=?")
            args.append(status)
        else:
            where.append("status!='forgotten'")
        if query:
            where.append("content LIKE ?")
            args.append(f"%{query}%")
        clause = " WHERE " + " AND ".join(where) if where else ""
        if order not in ("updated_at DESC", "importance DESC", "created_at DESC", "strength DESC",
                         "access_count DESC", "last_access_at DESC"):
            order = "updated_at DESC"
        total = self.db.scalar(f"SELECT COUNT(*) FROM memories{clause}", args)
        rows = self.db.query(f"SELECT * FROM memories{clause} ORDER BY {order} LIMIT ? OFFSET ?",
                             (*args, limit, offset))
        return [_row_to_item(r) for r in rows], int(total or 0)

    def links(self, ids: list[str] | None = None) -> list[dict[str, Any]]:
        if ids is None:
            rows = self.db.query("SELECT * FROM memory_links")
        elif not ids:
            return []
        else:
            marks = ",".join("?" * len(ids))
            rows = self.db.query(
                f"SELECT * FROM memory_links WHERE src IN ({marks}) OR dst IN ({marks})", (*ids, *ids))
        return [dict(r) for r in rows]

    def text_search(self, query: str, limit: int = 30, audience: str | None = None) -> list[tuple[str, float]]:
        """(id, score in 0..1).

        Latin words go through FTS5/BM25. Chinese has no spaces, so CJK runs are
        probed as bigrams plus informative single characters and scored by
        weighted overlap ("我的猫叫什么" still finds "用户养了一只叫年糕的橘猫").
        """
        results: dict[str, float] = {}
        scope_sql, scope_args = sql_filter(audience, "m.scope")
        words = [w for w in _LATIN.findall(query.lower()) if len(w) >= 2]
        if self._fts and words:
            expr = " OR ".join(f'"{w}"' for w in words[:12] if len(w) >= 3)
            if expr:
                try:
                    rows = self.db.query(
                        "SELECT f.id, bm25(memories_fts) AS r FROM memories_fts f JOIN memories m ON m.id=f.id "
                        "WHERE memories_fts MATCH ? AND m.status IN ('active','candidate') AND " + scope_sql +
                        " ORDER BY r LIMIT ?", (expr, *scope_args, limit))
                    for r in rows:
                        results[r[0]] = 1.0 / (1.0 + math.exp(r[1] / 4.0))  # bm25: more negative = better
                except Exception:
                    pass
        probes: dict[str, float] = {}
        for run in _CJK.findall(query):
            for k in range(len(run) - 1):
                gram = run[k : k + 2]
                if not all(ch in _STOP for ch in gram):
                    probes[gram] = 1.0
            for ch in run:
                if ch not in _STOP:
                    probes.setdefault(ch, 0.45)
        for w in words:
            if len(w) < 3 or not self._fts:
                probes.setdefault(w, 1.0)
        if probes:
            items = sorted(probes.items(), key=lambda kv: -kv[1])[:32]
            weighted = " + ".join(f"((content LIKE ?) * {w})" for _, w in items)
            any_clause = " OR ".join("content LIKE ?" for _ in items)
            likes = [f"%{p}%" for p, _ in items]
            rows = self.db.query(
                f"SELECT m.id, ({weighted}) AS hits FROM memories m WHERE m.status IN ('active','candidate') "
                f"AND {scope_sql} AND ({any_clause}) ORDER BY hits DESC LIMIT ?",
                (*likes, *scope_args, *likes, limit))
            for r in rows:
                score = 1.0 - math.exp(-float(r[1]) / 1.2)
                results[r[0]] = max(results.get(r[0], 0.0), score)
        return sorted(results.items(), key=lambda kv: -kv[1])[:limit]

    def vectors(self, audience: str | None = None) -> dict[str, list[float]]:
        if self._vec_cache is None:
            rows = self.db.query("SELECT id, embedding, scope FROM memories WHERE embedding IS NOT NULL "
                                 "AND status IN ('active','candidate')")
            self._vec_cache = {r[0]: loads(r[1]) for r in rows}
            self._vec_scope = {r[0]: r[2] for r in rows}
        if audience is None:
            return self._vec_cache
        from .scopes import can_read

        return {k: v for k, v in self._vec_cache.items() if can_read(audience, self._vec_scope.get(k, "owner"))}

    def stats(self) -> dict[str, Any]:
        rows = self.db.query("SELECT kind, status, COUNT(*) FROM memories GROUP BY kind, status")
        by_kind: dict[str, int] = {}
        by_status: dict[str, int] = {}
        for kind, status, n in rows:
            by_status[status] = by_status.get(status, 0) + n
            if status == "active":
                by_kind[kind] = by_kind.get(kind, 0) + n
        links = self.db.scalar("SELECT COUNT(*) FROM memory_links") or 0
        by_scope = {r[0]: r[1] for r in self.db.query(
            "SELECT CASE WHEN scope LIKE 'group:%' THEN 'group' WHEN scope IN ('owner','shared') THEN scope "
            "ELSE 'person' END, COUNT(*) FROM memories WHERE status='active' GROUP BY 1")}
        return {"by_kind": by_kind, "by_status": by_status, "links": links, "by_scope": by_scope,
                "active": by_status.get("active", 0), "candidates": by_status.get("candidate", 0)}


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def normalize(text: str) -> str:
    return re.sub(r"[\s，。！？,.!?：:；;“”\"'（）()]+", "", text.lower())


_ = time  # keep import for monkeypatch-friendly tests
