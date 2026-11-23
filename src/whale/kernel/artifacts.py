"""Artifacts: generated files (images, audio, exports) with stable ids.

Stored under ``<data_dir>/artifacts/`` (or in memory without a data dir),
indexed in SQLite, served at ``GET /api/artifacts/{id}`` to authenticated
clients only.
"""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Any

from .db import Database, dumps, loads, migration
from .errors import WhaleError, not_found
from .ids import new_id, now_ms

migration(
    "artifacts.001",
    """CREATE TABLE IF NOT EXISTS artifacts (id TEXT PRIMARY KEY, name TEXT NOT NULL, content_type TEXT NOT NULL,
       size INTEGER NOT NULL, path TEXT, created_at INTEGER NOT NULL, meta TEXT NOT NULL DEFAULT '{}');""",
)

MAX_BYTES = 64 * 1024 * 1024


class Artifacts:
    def __init__(self, db: Database, data_dir: Path | None) -> None:
        self.db = db
        self.root = (data_dir / "artifacts") if data_dir else None
        self._mem: dict[str, bytes] = {}

    def save(self, data: bytes, *, content_type: str = "application/octet-stream", name: str = "",
             meta: dict[str, Any] | None = None) -> dict[str, Any]:
        if len(data) > MAX_BYTES:
            raise WhaleError("invalid_params", "artifact too large")
        aid = new_id("art")
        ext = mimetypes.guess_extension(content_type.split(";")[0].strip()) or ".bin"
        safe = re.sub(r"[^\w.\-]+", "_", name)[:80] or f"{aid}{ext}"
        path = None
        if self.root is not None:
            self.root.mkdir(parents=True, exist_ok=True)
            file = self.root / f"{aid}{ext}"
            file.write_bytes(data)
            path = str(file)
        else:
            self._mem[aid] = data
        self.db.execute("INSERT INTO artifacts(id,name,content_type,size,path,created_at,meta) VALUES (?,?,?,?,?,?,?)",
                        (aid, safe, content_type, len(data), path, now_ms(), dumps(meta or {})))
        return self.get(aid)

    def get(self, aid: str) -> dict[str, Any]:
        r = self.db.one("SELECT * FROM artifacts WHERE id=?", (aid,))
        if not r:
            raise not_found("artifact", aid)
        d = dict(r)
        d["meta"] = loads(d["meta"], {})
        d["url"] = f"/api/artifacts/{aid}"
        return d

    def read(self, aid: str) -> tuple[dict[str, Any], bytes]:
        info = self.get(aid)
        if info["path"]:
            return info, Path(info["path"]).read_bytes()
        if aid in self._mem:
            return info, self._mem[aid]
        raise not_found("artifact data", aid)

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT id FROM artifacts ORDER BY created_at DESC LIMIT ?", (limit,))
        return [self.get(r[0]) for r in rows]
