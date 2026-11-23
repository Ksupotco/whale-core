"""一次性迁移器：v2 现网数据（qq_workflow.sqlite3 快照）→ v3/v1 whale.db。

MVP 面（docs/migration-assessment-v2-to-v1.md §5）：
- records → memories（一律 candidate + tag legacy-import；无 quote 不直进 active）
- kv persona:card:* → personas；persona:active_card → config agent.persona_id
- kv device:<32hex> → devices（pubkey→device_id 重算校验，不一致跳过；免重新配对）
- kv task:* → tasks（历史任务存档；状态映射见 STATUS_MAP）
- 会话绑定：chat:history:glasses:* → conversations(channel="glasses")+messages；
  bind:persona:qq:* → conversations(channel="qq"，id 与 channels.onebot 同口径)

冻结口径：
- ID 全部重发为 v3 前缀 ULID；旧 id 落 kv(ns="migrate") 映射表备查（幂等依据）
- created_at 保留原值（ISO→ms）；strength 用 v3 默认初值，last_access_at=created_at，
  有效强度由 v3 半衰期公式在检索时自然重算（即"迁移日重算"）
- 关系边不硬造，由 v3 整理管道补建

用法（在 N100 上，先快照再迁——快照用 sqlite 在线备份，绝不写现网）：
    sqlite3 -readonly /opt/whale/state/qq_workflow.sqlite3 ".backup '/tmp/qqwf-snap.db'"
    python3 scripts/migrate_v2_to_v1.py --src /tmp/qqwf-snap.db --data /opt/whale/v1-data \
        --report /tmp/migrate-report.json
    --dry-run  # 只出报告不写目标
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import whale.app  # noqa: F401  (importing the app registers every schema migration)
from whale.agent.persona import PersonaCard
from whale.devices.contract import CAPABILITIES
from whale.devices.registry import device_id_from_pubkey
from whale.kernel.config import Config
from whale.kernel.db import Database, dumps
from whale.kernel.ids import new_id, now_ms
from whale.memory.store import MemoryItem, MemoryStore

KIND_OK = {"fact", "preference", "episode", "knowledge", "diary", "skill", "person", "style"}
STATUS_MAP = {"succeeded": "completed", "answered": "completed", "delivered": "completed",
              "failed": "failed", "unknown": "failed", "stopped": "cancelled", "denied": "cancelled"}
ROLE_MAP = {"root-phone": "agent", "agent": "agent", "owner-phone": "personal", "personal": "personal"}


def iso_ms(raw: Any) -> int:
    """v2 ISO8601 文本 → Unix ms；解析失败回退迁移时刻。"""
    try:
        return int(datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp() * 1000)
    except (ValueError, TypeError, OSError):
        return now_ms()


def map_scope(space_id: str) -> str:
    """v2 space_id → v3 scope。未知空间一律隔离封存（legacy:*），不猜、不并。"""

    sid = (space_id or "").strip()
    if not sid or sid in ("owner-working", "vault-default", "owner"):
        return "owner"
    m = re.fullmatch(r"g-(?:test-)?(\d{4,13})", sid)
    if m:
        return f"group:qq:{m.group(1)}"
    if sid.startswith("member-"):
        return "member:" + sid[len("member-"):]   # 匿名成员空间：保持隔离，不反匿名
    return "legacy:" + sid[:48]


def _j(raw: Any, default: Any = None) -> Any:
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


class Migrator:
    def __init__(self, src: Path, data_dir: Path, dry_run: bool = False) -> None:
        self.src = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        self.src.row_factory = sqlite3.Row
        self.dry_run = dry_run
        self._tmp: tempfile.TemporaryDirectory[str] | None = None
        if dry_run:
            self._tmp = tempfile.TemporaryDirectory(prefix="whale-migrate-")
            data_dir = Path(self._tmp.name)
        self.data_dir = Path(data_dir)
        self.db = Database(self.data_dir / "whale.db")
        self.db.migrate()
        self.mem = MemoryStore(self.db)
        self.report: dict[str, Any] = {"src": str(src), "dry_run": dry_run, "sections": {}, "skipped": []}

    # -- helpers ---------------------------------------------------------------
    def kv(self, key: str) -> Any:
        r = self.src.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return _j(r[0]) if r else None

    def kv_like(self, prefix: str) -> list[tuple[str, Any]]:
        rows = self.src.execute("SELECT key, value FROM kv WHERE key LIKE ?", (prefix + "%",)).fetchall()
        return [(r[0], _j(r[1])) for r in rows]

    def mapped(self, legacy_key: str) -> str | None:
        return self.db.kv_get("migrate", legacy_key)

    def note_map(self, legacy_key: str, new_id_: str) -> None:
        self.db.kv_set("migrate", legacy_key, {"id": new_id_, "at": now_ms()})

    def skip(self, where: str, key: str, why: str) -> None:
        self.report["skipped"].append({"where": key, "why": why})

    def count(self, table: str) -> int:
        return int(self.db.one(f"SELECT COUNT(*) FROM {table}")[0])  # noqa: S608 - 表白名单内

    # -- 1. records → memories ----------------------------------------------------
    def memories(self) -> None:
        rows = self.src.execute("SELECT key, kind, content, record_id, created_at, tags, space_id "
                                "FROM records").fetchall()
        n = 0
        for r in rows:
            lid = f"records:{r['record_id']}"
            if self.mapped(lid):
                continue
            kind = r["kind"] if r["kind"] in KIND_OK else "episode"
            content = str(r["content"] or "").strip()
            if not content:
                self.skip("records", r["record_id"], "empty content")
                continue
            tags = _j(r["tags"], []) or []
            tags = [str(t)[:32] for t in tags if str(t).strip()][:8] + ["legacy-import"]
            ts = iso_ms(r["created_at"])
            item = MemoryItem(kind=kind, content=content[:4000], status="candidate",
                              importance=0.5, confidence=0.6, strength=0.6,
                              source={"legacy": "v2:records", "record_id": r["record_id"],
                                      "space_id": r["space_id"]},
                              tags=tags, scope=map_scope(r["space_id"]),
                              created_at=ts, updated_at=ts, last_access_at=ts)
            self.mem.add(item)
            self.note_map(lid, item.id)
            n += 1
        self.report["sections"]["memories"] = {"migrated": n, "source_rows": len(rows)}

    # -- 2. persona 卡 -------------------------------------------------------------
    def personas(self) -> dict[str, str]:
        card_ids: dict[str, str] = {}
        n = 0
        for key, card in self.kv_like("persona:card:"):
            if not isinstance(card, dict):
                self.skip("personas", key, "card is not an object")
                continue
            cid = str(card.get("card_id") or key.split(":")[-1])[:64]
            name = str(card.get("name") or cid).strip()[:64]
            style = "；".join(x for x in [str(card.get("tone") or "").strip(),
                                          *([str(s) for s in card.get("style_rules") or []])] if x)
            taboos = card.get("extra_taboos") or []
            card_ids[cid] = cid
            lid = f"persona:{cid}"
            if self.mapped(lid):
                continue
            ts = now_ms()
            p = PersonaCard(id=cid, name=name, tagline=str(card.get("title") or "")[:80],
                            description=str(card.get("description") or ""),
                            style=style[:1000], boundaries="；".join(str(t) for t in taboos)[:1000],
                            extra={"legacy": "v2", "category": card.get("category") or "",
                                   "channels": card.get("channels") or [],
                                   "address_user_as": card.get("address_user_as") or "",
                                   "source_url": card.get("source_url") or ""})
            self.db.execute("INSERT OR IGNORE INTO personas(id, card, created_at, updated_at) "
                            "VALUES (?,?,?,?)", (p.id, dumps(p.model_dump(mode="json")), ts, ts))
            self.note_map(lid, p.id)
            n += 1
        active = self.kv("persona:active_card")
        active_id = str(active.get("card_id")) if isinstance(active, dict) and active.get("card_id") else ""
        if active_id and active_id in card_ids and not self.dry_run:
            Config(self.data_dir).update({"agent": {"persona_id": active_id}})
        self.report["sections"]["personas"] = {"migrated": n, "active": active_id}
        return card_ids

    # -- 3. 设备身份 -----------------------------------------------------------------
    def devices(self) -> None:
        n = 0
        rows = self.src.execute("SELECT key, value FROM kv WHERE key LIKE 'device:%' "
                                "AND length(key)=39").fetchall()
        for r in rows:
            key, dev = r[0], _j(r[1])
            if not isinstance(dev, dict) or not dev.get("pubkey_hex"):
                self.skip("devices", key, "no pubkey")
                continue
            try:
                recomputed = device_id_from_pubkey(str(dev["pubkey_hex"]))
            except Exception:
                self.skip("devices", key, "bad pubkey")
                continue
            legacy_id = str(dev.get("device_id") or "")
            if legacy_id != recomputed:
                self.skip("devices", key, f"device_id mismatch: {legacy_id} != recomputed")
                continue
            lid = f"device:{recomputed}"
            if self.mapped(lid) or self.db.one("SELECT 1 FROM devices WHERE id=?", (recomputed,)):
                continue
            scopes = [s for s in (dev.get("scopes") or []) if s in CAPABILITIES]
            dropped = sorted(set(dev.get("scopes") or []) - set(scopes))
            if dropped:
                self.skip("devices", key, "unknown scopes dropped: " + ",".join(dropped))
            ts = iso_ms(dev.get("created_at"))
            revoked = 0 if dev.get("status") == "active" else 1
            self.db.execute(
                "INSERT OR IGNORE INTO devices(id,pubkey,name,model,flavor,app_version,android,scopes,"
                "created_at,last_seen,revoked,revoked_at,paired_via,role,privacy) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (recomputed, str(dev["pubkey_hex"]).lower(), str(dev.get("label") or "迁移设备")[:64],
                 "", "", "", "", dumps(scopes), ts, iso_ms(dev.get("updated_at")), revoked,
                 iso_ms(dev.get("updated_at")) if revoked else None, "legacy-migrate",
                 ROLE_MAP.get(str(dev.get("role") or ""), "personal"), "{}"))
            self.note_map(lid, recomputed)
            n += 1
        self.report["sections"]["devices"] = {"migrated": n, "source_rows": len(rows)}

    # -- 4. 历史任务 ------------------------------------------------------------------
    def tasks(self) -> None:
        n = 0
        for key, task in self.kv_like("task:"):
            if not isinstance(task, dict):
                continue
            lid = f"task:{task.get('task_id') or key}"
            if self.mapped(lid):
                continue
            goal = str(task.get("goal") or "").strip()
            if not goal:
                self.skip("tasks", key, "empty goal")
                continue
            tid = new_id("task")
            ts = iso_ms(task.get("at"))
            status = STATUS_MAP.get(str(task.get("status") or ""), "cancelled")
            self.db.execute(
                "INSERT OR IGNORE INTO tasks(id,title,goal,status,plan,result,error,origin,parent_id,"
                "conversation_id,job_id,notify,runs,last_run_id,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, goal[:40], goal[:8000], status, "[]", str(task.get("res") or "")[:4000], "",
                 "legacy-v2", None, None, None, 0, 0, None, ts, ts))
            self.note_map(lid, tid)
            n += 1
        self.report["sections"]["tasks"] = {"migrated": n}

    # -- 5. 会话绑定 --------------------------------------------------------------------
    def owner_qq(self) -> str:
        for key, ident in self.kv_like("identity:channel:qq:"):
            if isinstance(ident, dict) and ident.get("owner_id") == "owner":
                return str(ident.get("channel_user_id") or "")
        return ""

    def conversations(self, card_ids: dict[str, str]) -> None:
        n_conv = n_msg = 0
        owner = self.owner_qq()
        # 5a. 眼镜历史
        for key, hist in self.kv_like("chat:history:glasses:"):
            lid = f"chat:{key}"
            if self.mapped(lid):
                continue
            if not isinstance(hist, list) or not hist:
                continue
            cid = new_id("conv")
            base = now_ms() - len(hist) * 1000
            turns = 0
            for i, m in enumerate(hist):
                if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
                    continue
                content = str(m.get("content") or "")[:8000]
                if not content:
                    continue
                self.db.execute("INSERT INTO messages(id,conversation_id,role,content,run_id,ts,meta) "
                                "VALUES (?,?,?,?,?,?,?)",
                                (new_id("msg"), cid, m["role"], content, None, base + i * 1000, "{}"))
                turns += 1 if m["role"] == "user" else 0
                n_msg += 1
            now = now_ms()
            self.db.execute("INSERT OR IGNORE INTO conversations(id,title,channel,peer,persona_id,"
                            "created_at,updated_at,turns,meta) VALUES (?,?,?,?,?,?,?,?,?)",
                            (cid, "眼镜对话（v2 迁移）", "glasses", "owner", None, now, now, turns,
                             dumps({"legacy_key": key})))
            self.note_map(lid, cid)
            n_conv += 1
        # 5b. QQ 绑定（群 + 私聊；人格绑定随会话）
        uidmap: dict[str, str] = {}
        for r in self.src.execute("SELECT key, value FROM kv WHERE key LIKE 'uidmap:uid:%'"):
            raw = r[1]
            v = _j(raw)                       # 值可能是 JSON 字符串或裸文本
            uidmap[r[0][len("uidmap:uid:"):]] = str(v if v is not None else raw).strip('"')
        for key, bind in self.kv_like("bind:persona:qq:"):
            if not isinstance(bind, dict):
                continue
            persona_id = str(bind.get("persona_id") or "")
            if persona_id not in card_ids:
                persona_id = ""
            lid = f"conv:{key}"
            if self.mapped(lid):
                continue
            if ":qq_group:group:" in key:
                gid = key.rsplit(":", 1)[-1]
                cid, peer, title = f"qq:group:{gid}", f"qq:group:{gid}", f"QQ 群 {gid}"
            elif ":qq_private:" in key:
                uid = key.rsplit(":", 1)[-1]
                uin = uidmap.get(uid)
                if not uin or not uin.isdigit():
                    self.skip("conversations", key, "uid 无 uin 映射")
                    continue
                cid = f"qq:private:{uin}"
                peer = "owner" if uin == owner else f"qq:{uin}"
                title = "QQ 私聊（迁移）"
            else:
                continue
            now = now_ms()
            self.db.execute("INSERT OR IGNORE INTO conversations(id,title,channel,peer,persona_id,"
                            "created_at,updated_at,turns,meta) VALUES (?,?,?,?,?,?,?,?,?)",
                            (cid, title, "qq", peer, persona_id or None, now, now, 0,
                             dumps({"legacy_bind": key})))
            self.note_map(lid, cid)
            n_conv += 1
        self.report["sections"]["conversations"] = {"migrated": n_conv, "messages": n_msg,
                                                    "owner_qq": owner or None}

    # -- main ----------------------------------------------------------------------
    def run(self) -> dict[str, Any]:
        self.memories()
        cards = self.personas()
        self.devices()
        self.tasks()
        self.conversations(cards)
        self.report["target"] = {"memories": self.count("memories"), "personas": self.count("personas"),
                                 "devices": self.count("devices"), "tasks": self.count("tasks"),
                                 "conversations": self.count("conversations"),
                                 "messages": self.count("messages"),
                                 "legacy_map": self.count("kv")}
        self.src.close()
        self.db.close()
        if self._tmp is not None:
            self._tmp.cleanup()
        return self.report


def main() -> None:
    ap = argparse.ArgumentParser(description="v2 → v1(whale.db) one-shot migration (MVP surface)")
    ap.add_argument("--src", required=True, help="v2 qq_workflow.sqlite3 快照（先 .backup 出来）")
    ap.add_argument("--data", required=True, help="v3 数据目录（whale.db 所在）")
    ap.add_argument("--report", default="", help="对账报告 JSON 输出路径")
    ap.add_argument("--dry-run", action="store_true", help="只出报告，不写目标目录")
    args = ap.parse_args()
    report = Migrator(Path(args.src), Path(args.data), dry_run=args.dry_run).run()
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
