"""Device registry and ledgers (SQLite).

* ``DeviceRegistry`` — identity (Ed25519 public key → ``device_id``), pairing
  codes, scopes, revoke/rename. ``device_id`` = first 16 bytes of
  SHA-256(raw 32-byte public key), lowercase hex (contracts V2 §3).
* ``DeviceLedger`` — what happened: experience events (deduplicated by
  ``event_id``), action records (idempotency key → action id, first device
  terminal wins) and owner notifications.

Pairing (owner first, single-use, 10 minute TTL): the console calls
``devices.pair_start`` and shows a 6-digit code; the phone posts
``{code, pubkey_hex, label}`` to ``POST /api/devices/pair`` and is active
immediately. (2026-11-23 clean break: the device-first flow and all
version-prefixed paths were removed.)
"""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import time
from typing import Any, Iterable

from ..kernel.db import Database, dumps, loads, migration
from .privacy import ROLES, clean_settings
from ..kernel.errors import WhaleError, not_found
from ..kernel.ids import now_ms
from .contract import CAPABILITIES, TERMINAL

PAIR_TTL_S = 600
_PUBKEY_RE = re.compile(r"^[0-9a-fA-F]{64}$")

migration(
    "devices.001",
    """
    CREATE TABLE IF NOT EXISTS devices (
        id TEXT PRIMARY KEY, pubkey TEXT NOT NULL, name TEXT NOT NULL, model TEXT NOT NULL DEFAULT '',
        flavor TEXT NOT NULL DEFAULT '', app_version TEXT NOT NULL DEFAULT '', android TEXT NOT NULL DEFAULT '',
        scopes TEXT NOT NULL, created_at INTEGER NOT NULL, last_seen INTEGER, revoked INTEGER NOT NULL DEFAULT 0,
        revoked_at INTEGER, paired_via TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS device_pairings (
        code TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL DEFAULT '', scopes TEXT,
        device_id TEXT, pubkey TEXT, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
        used_at INTEGER);
    CREATE TABLE IF NOT EXISTS device_events (
        device_id TEXT NOT NULL, event_id TEXT NOT NULL, kind TEXT NOT NULL, source TEXT NOT NULL,
        summary TEXT NOT NULL, privacy_class TEXT NOT NULL, occurred_at INTEGER NOT NULL,
        received_at INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY (device_id, event_id));
    CREATE INDEX IF NOT EXISTS device_events_time ON device_events(device_id, received_at);
    CREATE TABLE IF NOT EXISTS device_actions (
        action_id TEXT PRIMARY KEY, device_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
        capability TEXT NOT NULL, arguments TEXT NOT NULL, origin TEXT NOT NULL DEFAULT '', run_id TEXT,
        status TEXT NOT NULL, outcome_source TEXT NOT NULL DEFAULT '', error_code TEXT, summary TEXT,
        receipt TEXT, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
        UNIQUE (device_id, idempotency_key));
    CREATE INDEX IF NOT EXISTS device_actions_time ON device_actions(device_id, created_at);
    CREATE TABLE IF NOT EXISTS device_notifications (
        event_id TEXT PRIMARY KEY, notification_id TEXT NOT NULL, device_id TEXT NOT NULL,
        category TEXT NOT NULL, title TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL,
        created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
    CREATE INDEX IF NOT EXISTS device_notifications_dev ON device_notifications(device_id, event_id);
    """,
)


migration(
    "devices.002",
    """
    ALTER TABLE devices ADD COLUMN role TEXT NOT NULL DEFAULT '';
    ALTER TABLE devices ADD COLUMN privacy TEXT NOT NULL DEFAULT '{}';
    """,
)


def device_id_from_pubkey(pubkey_hex: str) -> str:
    raw = bytes.fromhex(pubkey_hex)
    if len(raw) != 32:
        raise WhaleError("invalid_params", "Ed25519 public key must be 32 bytes (64 hex chars)")
    return hashlib.sha256(raw).digest()[:16].hex()


def clean_scopes(scopes: Iterable[str] | None) -> list[str]:
    if scopes is None:
        return list(CAPABILITIES)
    out: list[str] = []
    for raw in scopes:
        s = str(raw).strip()
        if not s or s in out:
            continue
        if s not in CAPABILITIES:
            raise WhaleError("invalid_params", "unknown device capability: " + s, details={"scope": s})
        out.append(s)
    return out


def _clean_name(name: str) -> str:
    name = re.sub(r"[\x00-\x1f\x7f]", "", str(name or "")).strip()
    return name[:64]


class DeviceRegistry:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._failures: list[float] = []   # failed code redemptions (brute-force brake)

    # -- records ------------------------------------------------------------------------------
    @staticmethod
    def _row(r: Any) -> dict[str, Any]:
        d = dict(r)
        d["scopes"] = loads(d["scopes"], [])
        d["revoked"] = bool(d["revoked"])
        d["privacy"] = clean_settings(loads(d.get("privacy") or "{}", {}))
        return d

    def get(self, device_id: str) -> dict[str, Any] | None:
        r = self.db.one("SELECT * FROM devices WHERE id=?", (device_id,))
        return self._row(r) if r else None

    def require(self, device_id: str) -> dict[str, Any]:
        d = self.get(device_id)
        if d is None:
            raise not_found("device", device_id)
        return d

    def list(self) -> list[dict[str, Any]]:
        return [self._row(r) for r in self.db.query("SELECT * FROM devices ORDER BY created_at")]

    def _activate(self, pubkey: str, name: str, scopes: list[str], via: str) -> dict[str, Any]:
        device_id = device_id_from_pubkey(pubkey)
        now = now_ms()
        existing = self.get(device_id)
        if existing is None:
            self.db.execute(
                "INSERT INTO devices(id,pubkey,name,scopes,created_at,paired_via) VALUES (?,?,?,?,?,?)",
                (device_id, pubkey, name or ("device-" + device_id[:6]), dumps(scopes), now, via))
        else:  # re-pairing (e.g. after revoke) needs a fresh owner-approved code — that is this call
            self.db.execute(
                "UPDATE devices SET name=?, scopes=?, revoked=0, revoked_at=NULL, paired_via=? WHERE id=?",
                (name or existing["name"], dumps(scopes), via, device_id))
        return self.require(device_id)

    def _new_code(self) -> str:
        now = now_ms()
        for _ in range(16):
            code = str(secrets.randbelow(1_000_000)).zfill(6)
            if not self.db.one("SELECT 1 FROM device_pairings WHERE code=? AND used_at IS NULL AND expires_at>?",
                               (code, now)):
                return code
        raise WhaleError("conflict", "could not allocate a unique pairing code, retry", retryable=True)

    def _brake(self) -> None:
        cutoff = time.monotonic() - PAIR_TTL_S
        self._failures = [t for t in self._failures if t > cutoff]
        if len(self._failures) >= 20:
            raise WhaleError("rate_limited", "too many invalid pairing codes; start a new pairing later",
                             retryable=True)

    # -- pairing: owner first ---------------------------------------------------------------------
    def start_pairing(self, name: str = "", scopes: Iterable[str] | None = None,
                      ttl_s: int = PAIR_TTL_S) -> dict[str, Any]:
        self.db.execute("DELETE FROM device_pairings WHERE expires_at < ?", (now_ms() - 86400_000,))
        code = self._new_code()
        now = now_ms()
        expires = now + int(ttl_s) * 1000
        self.db.execute(
            "INSERT INTO device_pairings(code,kind,name,scopes,created_at,expires_at) VALUES (?,?,?,?,?,?)",
            (code, "owner", _clean_name(name), dumps(clean_scopes(scopes)), now, expires))
        return {"code": code, "expires_at": expires, "ttl_s": int(ttl_s)}

    def redeem(self, code: str, pubkey_hex: str, label: str = "") -> dict[str, Any]:
        """Device presents an owner-issued code together with its public key."""
        self._brake()
        if not isinstance(pubkey_hex, str) or not _PUBKEY_RE.match(pubkey_hex):
            raise WhaleError("invalid_params", "pubkey_hex must be the 64-hex Ed25519 public key")
        now = now_ms()
        with self.db.transaction() as conn:
            r = conn.execute("SELECT * FROM device_pairings WHERE code=? AND kind='owner'", (str(code),)).fetchone()
            if r is None or r["used_at"] is not None or r["expires_at"] <= now:
                self._failures.append(time.monotonic())
                expired = r is not None and r["used_at"] is None
                raise WhaleError("conflict" if expired else "not_found",
                                 "pairing code expired" if expired else "pairing code invalid or already used")
            conn.execute("UPDATE device_pairings SET used_at=?, pubkey=?, device_id=? WHERE code=?",
                         (now, pubkey_hex.lower(), device_id_from_pubkey(pubkey_hex), str(code)))
        name = r["name"] or _clean_name(label)
        return self._activate(pubkey_hex.lower(), name, loads(r["scopes"], list(CAPABILITIES)), "owner_code")

    # -- management --------------------------------------------------------------------------------
    def revoke(self, device_id: str) -> dict[str, Any]:
        self.require(device_id)
        self.db.execute("UPDATE devices SET revoked=1, revoked_at=COALESCE(revoked_at, ?) WHERE id=?",
                        (now_ms(), device_id))
        return self.require(device_id)

    def rename(self, device_id: str, name: str) -> dict[str, Any]:
        self.require(device_id)
        name = _clean_name(name)
        if not name:
            raise WhaleError("invalid_params", "name must be 1..64 characters")
        self.db.execute("UPDATE devices SET name=? WHERE id=?", (name, device_id))
        return self.require(device_id)

    def set_scopes(self, device_id: str, scopes: Iterable[str]) -> dict[str, Any]:
        d = self.require(device_id)
        if d["revoked"]:
            raise WhaleError("conflict", "device is revoked")
        self.db.execute("UPDATE devices SET scopes=? WHERE id=?", (dumps(clean_scopes(scopes)), device_id))
        return self.require(device_id)

    def set_role(self, device_id: str, role: str) -> dict[str, Any]:
        self.require(device_id)
        if role not in ROLES:
            raise WhaleError("invalid_params", "role must be agent or personal")
        self.db.execute("UPDATE devices SET role=? WHERE id=?", (role, device_id))
        return self.require(device_id)

    def set_privacy(self, device_id: str, settings: dict[str, Any]) -> dict[str, Any]:
        current = self.require(device_id)["privacy"]
        merged = clean_settings({**current, **settings})
        self.db.execute("UPDATE devices SET privacy=? WHERE id=?", (dumps(merged), device_id))
        return self.require(device_id)

    def seen(self, device_id: str, **descriptor: Any) -> None:
        cols = {k: v for k, v in descriptor.items() if k in ("model", "flavor", "app_version", "android")}
        sets = ", ".join(k + "=?" for k in cols)
        args = list(cols.values()) + [now_ms(), device_id]
        self.db.execute("UPDATE devices SET " + (sets + ", " if sets else "") + "last_seen=? WHERE id=?", args)


class DeviceLedger:
    def __init__(self, db: Database) -> None:
        self.db = db

    # -- experience events ---------------------------------------------------------------------------
    def add_event(self, device_id: str, p: dict[str, Any]) -> bool:
        """True when new; False for a duplicate ``event_id`` (idempotency key, §9)."""
        cur = self.db.execute(
            "INSERT OR IGNORE INTO device_events(device_id,event_id,kind,source,summary,privacy_class,"
            "occurred_at,received_at,data) VALUES (?,?,?,?,?,?,?,?,?)",
            (device_id, p["event_id"], p["kind"], p["source"], p["summary"], p["privacy_class"],
             int(p["occurred_at"]), now_ms(), dumps(p)))
        return cur.rowcount == 1

    def add_tombstone(self, device_id: str, p: dict[str, Any], reason: str) -> bool:
        """A filtered event: only its id (for dedupe) and why — no content, package or time detail."""
        cur = self.db.execute(
            "INSERT OR IGNORE INTO device_events(device_id,event_id,kind,source,summary,privacy_class,"
            "occurred_at,received_at,data) VALUES (?,?,?,?,?,?,?,?,?)",
            (device_id, p["event_id"], "dropped", "filtered", "", "owner_sensitive", 0, now_ms(),
             dumps({"event_id": p["event_id"], "dropped": reason})))
        return cur.rowcount == 1

    def drop_stats(self, device_id: str) -> dict[str, int]:
        rows = self.db.query("SELECT data FROM device_events WHERE device_id=? AND kind='dropped'", (device_id,))
        out: dict[str, int] = {}
        for r in rows:
            reason = loads(r[0], {}).get("dropped", "other")
            out[reason] = out.get(reason, 0) + 1
        return out

    def events(self, device_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT data, received_at FROM device_events WHERE device_id=? "
                             "ORDER BY received_at DESC, event_id DESC LIMIT ?", (device_id, limit))
        return [{**loads(r["data"], {}), "received_at": r["received_at"]} for r in rows]

    def count_events(self, device_id: str) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM device_events WHERE device_id=?", (device_id,)) or 0)

    # -- actions --------------------------------------------------------------------------------------
    @staticmethod
    def _action(r: Any) -> dict[str, Any]:
        d = dict(r)
        d["arguments"] = loads(d["arguments"], {})
        d["receipt"] = loads(d["receipt"], None)
        return d

    def reserve_action(self, rec: dict[str, Any]) -> bool:
        """Insert a pending action. False when the idempotency key already exists."""
        now = now_ms()
        try:
            self.db.execute(
                "INSERT INTO device_actions(action_id,device_id,idempotency_key,capability,arguments,origin,"
                "run_id,status,created_at,expires_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (rec["action_id"], rec["device_id"], rec["idempotency_key"], rec["capability"],
                 dumps(rec["arguments"]), rec.get("origin", ""), rec.get("run_id"), "pending", now,
                 rec["expires_at"], now))
        except sqlite3.IntegrityError:
            return False
        return True

    def action(self, action_id: str) -> dict[str, Any] | None:
        r = self.db.one("SELECT * FROM device_actions WHERE action_id=?", (action_id,))
        return self._action(r) if r else None

    def action_by_key(self, device_id: str, key: str) -> dict[str, Any] | None:
        r = self.db.one("SELECT * FROM device_actions WHERE device_id=? AND idempotency_key=?", (device_id, key))
        return self._action(r) if r else None

    def actions(self, device_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM device_actions WHERE device_id=? ORDER BY created_at DESC, "
                             "action_id DESC LIMIT ?", (device_id, limit))
        return [self.view(self._action(r)) for r in rows]

    def record_receipt(self, device_id: str, p: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
        """Apply a device receipt. Returns (record, changed).

        First *device-reported* terminal wins and is never rewritten. A core-side
        estimate (``unknown`` after a wait timeout or a lost connection) is
        superseded by a late real receipt, which is the more truthful fact.
        """
        rec = self.action(p["action_id"])
        if rec is None or rec["device_id"] != device_id:
            return None, False
        status = p["status"]
        current = rec["status"]
        device_terminal = current in TERMINAL and not rec["outcome_source"].startswith("core:")
        if device_terminal:
            return rec, False
        if status not in TERMINAL:
            if current != "pending":
                return rec, False
            self.db.execute("UPDATE device_actions SET status='received', updated_at=? WHERE action_id=?",
                            (now_ms(), rec["action_id"]))
            return self.action(rec["action_id"]), True
        result = p.get("result") or {}
        self.db.execute(
            "UPDATE device_actions SET status=?, outcome_source='device', error_code=?, summary=?, receipt=?, "
            "updated_at=? WHERE action_id=?",
            (status, p.get("error_code"), str(result.get("summary") or "")[:2000], dumps(p), now_ms(),
             rec["action_id"]))
        return self.action(rec["action_id"]), True

    def estimate(self, action_id: str, source: str, status: str = "unknown") -> dict[str, Any] | None:
        """Core-side terminal when the device never answered. Never overrides a device terminal."""
        self.db.execute(
            "UPDATE device_actions SET status=?, outcome_source=?, updated_at=? "
            "WHERE action_id=? AND status IN ('pending','received')",
            (status, "core:" + source, now_ms(), action_id))
        return self.action(action_id)

    def fail_unsent(self, action_id: str, error_code: str, summary: str) -> dict[str, Any] | None:
        self.db.execute(
            "UPDATE device_actions SET status='failed', outcome_source='core:not_sent', error_code=?, summary=?, "
            "updated_at=? WHERE action_id=? AND status='pending'", (error_code, summary[:2000], now_ms(), action_id))
        return self.action(action_id)

    def sweep_inflight(self) -> int:
        """After a restart nothing in flight can still be waited on: it is ``unknown`` (§16)."""
        cur = self.db.execute(
            "UPDATE device_actions SET status='unknown', outcome_source='core:restart', updated_at=? "
            "WHERE status IN ('pending','received')", (now_ms(),))
        return cur.rowcount

    @staticmethod
    def view(rec: dict[str, Any]) -> dict[str, Any]:
        receipt = rec.get("receipt") or {}
        result = receipt.get("result") or {}
        out = {"action_id": rec["action_id"], "device_id": rec["device_id"], "capability": rec["capability"],
               "idempotency_key": rec["idempotency_key"], "arguments": rec["arguments"],
               "origin": rec.get("origin", ""), "run_id": rec.get("run_id"), "status": rec["status"],
               "outcome_source": rec.get("outcome_source", ""), "error_code": rec.get("error_code"),
               "summary": rec.get("summary") or "", "created_at": rec["created_at"],
               "updated_at": rec["updated_at"], "expires_at": rec["expires_at"]}
        if result.get("output_ref"):
            out["output_ref"] = result["output_ref"]
        if result.get("verification"):
            out["verification"] = result["verification"]
        for key in ("provider_ref", "started_at", "completed_at"):
            if receipt.get(key) is not None:
                out[key] = receipt[key]
        return out

    # -- notifications ------------------------------------------------------------------------------
    def add_notification(self, payload: dict[str, Any], status: str) -> None:
        now = now_ms()
        self.db.execute(
            "INSERT OR REPLACE INTO device_notifications(event_id,notification_id,device_id,category,title,"
            "payload,status,created_at,expires_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (payload["event_id"], payload["notification_id"], payload["device_id"], payload["category"],
             payload["title"], dumps(payload), status, now, payload["expires_at"], now))

    def notification_status(self, device_id: str, event_id: str, status: str) -> bool:
        cur = self.db.execute("UPDATE device_notifications SET status=?, updated_at=? WHERE event_id=? AND device_id=?",
                              (status, now_ms(), event_id, device_id))
        return cur.rowcount == 1

    def notification(self, event_id: str) -> dict[str, Any] | None:
        r = self.db.one("SELECT * FROM device_notifications WHERE event_id=?", (event_id,))
        if not r:
            return None
        d = dict(r)
        d["payload"] = loads(d["payload"], {})
        return d

    def undelivered(self, device_id: str, after: str | None) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT payload FROM device_notifications WHERE device_id=? AND expires_at>? AND status IN ('queued','sent')"
            " AND event_id > ? ORDER BY event_id LIMIT 100", (device_id, now_ms(), after or ""))
        return [loads(r["payload"], {}) for r in rows]
