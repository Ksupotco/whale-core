"""SQLite storage with versioned migrations.

One connection, WAL mode, serialized by a lock. SQLite is fast enough for a
personal agent; the lock keeps writes consistent across asyncio tasks and the
few worker threads (model calls never hold it).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

MIGRATIONS: list[tuple[str, str]] = []


def migration(name: str, sql: str) -> None:
    """Modules register their schema at import time; applied in order."""
    if any(n == name for n, _ in MIGRATIONS):
        return
    MIGRATIONS.append((name, sql))


migration(
    "kernel.001",
    """
    CREATE TABLE IF NOT EXISTS kv (ns TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
        updated_at INTEGER NOT NULL, PRIMARY KEY (ns, key));
    CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, topic TEXT NOT NULL, ts INTEGER NOT NULL,
        data TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS events_topic ON events(topic, id);
    """,
)


class Database:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS _migrations (name TEXT PRIMARY KEY, applied_at INTEGER)"
            )
        self.fts5 = self._has_fts5()

    def _has_fts5(self) -> bool:
        try:
            with self._lock:
                self._conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts_probe USING fts5(x)")
                self._conn.execute("DROP TABLE _fts_probe")
            return True
        except sqlite3.OperationalError:
            return False

    def migrate(self) -> list[str]:
        applied = []
        with self._lock:
            done = {r[0] for r in self._conn.execute("SELECT name FROM _migrations")}
            for name, sql in MIGRATIONS:
                if name in done:
                    continue
                if "USING fts5" in sql and not self.fts5:
                    sql = "\n".join(line for line in sql.splitlines() if "fts5" not in line.lower())
                try:
                    self._conn.executescript(f"BEGIN;\n{sql}\nCOMMIT;")
                except sqlite3.OperationalError as exc:
                    if self._conn.in_transaction:
                        self._conn.execute("ROLLBACK")
                    if "tokenizer" not in str(exc):
                        raise
                    # Older SQLite without the trigram tokenizer: fall back to unicode61.
                    plain = sql.replace(", tokenize='trigram'", "")
                    self._conn.executescript(f"BEGIN;\n{plain}\nCOMMIT;")
                self._conn.execute(
                    "INSERT INTO _migrations VALUES (?, strftime('%s','now'))", (name,)
                )
                applied.append(name)
        return applied

    # -- primitives ---------------------------------------------------------
    def execute(self, sql: str, args: Sequence[Any] | dict[str, Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, args)

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        with self._lock:
            self._conn.executemany(sql, rows)

    def query(self, sql: str, args: Sequence[Any] | dict[str, Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    def one(self, sql: str, args: Sequence[Any] | dict[str, Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, args).fetchone()

    def scalar(self, sql: str, args: Sequence[Any] = ()) -> Any:
        row = self.one(sql, args)
        return row[0] if row else None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    # -- kv -----------------------------------------------------------------
    def kv_get(self, ns: str, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM kv WHERE ns=? AND key=?", (ns, key))
        return json.loads(row[0]) if row else default

    def kv_set(self, ns: str, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO kv(ns,key,value,updated_at) VALUES (?,?,?,strftime('%s','now')) "
            "ON CONFLICT(ns,key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (ns, key, json.dumps(value, ensure_ascii=False)),
        )

    def kv_delete(self, ns: str, key: str) -> None:
        self.execute("DELETE FROM kv WHERE ns=? AND key=?", (ns, key))

    def kv_all(self, ns: str) -> dict[str, Any]:
        return {r[0]: json.loads(r[1]) for r in self.query("SELECT key, value FROM kv WHERE ns=?", (ns,))}

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def loads(value: str | None, default: Any = None) -> Any:
    if value is None or value == "":
        return default
    return json.loads(value)
