"""Import one QCE group export into Jax's group archive and typed memories.

Dry-run is the default. The local extractor handles explicit self-reports and
clear group decisions; uncertain cases stay in the source archive. No cloud
model receives group messages.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from whale_core.memory.bindings import BindingStore
from whale_core.memory.consent import MemberConsentStore
from whale_core.memory.extraction import MemberMemoryExtractor, contains_sensitive
from whale_core.memory.formation import _PRIVATE_TOPICS, _slot_for_quote
from whale_core.memory.group_archive import GroupArchive
from whale_core.memory.spaces import SpaceStore, ensure_owner_working_copy
from whale_core.memory.sqlite_store import SqliteMemoryStore
from whale_core.memory.store import MemoryKind, MemoryRecord
from whale_core.memory.types import MemorySpaceType
from whale_core.memory.write_decision import apply_decision, decide_candidate

_PUBLIC_CUE = re.compile(r"决定|约定|定了|改成|改为|取消|下次|开团|开本|集合|规则|设定|世界观")
_ROLEPLAY = ("角色", "人物卡", "oc", "OC", "kp", "KP", "模组", "扮演")
_NUMBER = re.compile(r"(?<!\d)\d{6,}(?!\d)")


def read_messages(paths: list[Path], group_id: str) -> list[dict]:
    by_id: dict[str, dict] = {}
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        info = data.get("chatInfo") or {}
        if info.get("type") != "group" or str(info.get("peerUid")) != group_id:
            raise ValueError(f"Wrong group in {path}")
        owner_qq = str(info.get("selfUin") or "")
        for row in data.get("messages") or ():
            mid = str(row.get("id") or "")
            sender = row.get("sender") or {}
            uid = str(sender.get("uin") or sender.get("uid") or "")
            body = str((row.get("content") or {}).get("text") or "").strip()
            if mid and uid and body and row.get("type") != "system":
                by_id.setdefault(mid, {
                    "id": mid, "sender_id": uid,
                    "is_owner": bool(owner_qq and uid == owner_qq),
                    "sender_name": str(sender.get("name") or sender.get("nickname") or "")[:60],
                    "text": body, "at": datetime.fromtimestamp(
                        int(row["timestamp"]) / 1000, timezone.utc),
                })
    return sorted(by_id.values(), key=lambda row: (row["at"], row["id"]))


def candidates(messages: list[dict], group_id: str) -> list[dict]:
    """Classify only claims supported by one exact, attributed source quote."""
    extractor = MemberMemoryExtractor(None, None, None, None, None)
    selected = []
    for row in messages:
        text = row["text"]
        if not 5 <= len(text) <= 220 or contains_sensitive(text) or _NUMBER.search(text):
            continue
        if any(word in text for word in _PRIVATE_TOPICS):
            continue
        self_claim = () if any(word in text for word in _ROLEPLAY) else (
            extractor.candidates_from_text(
                text, principal_id=f"anon:qq:{row['sender_id']}",
                source_group=f"group:{group_id}", message_id=row["id"],
                consent_version=0, observed_at=row["at"].isoformat()))
        for claim in self_claim:
            if claim.summary.startswith(("我", "咱")) and not any(
                    mark in claim.summary for mark in ("?", "？", "“", "”", "\"")):
                selected.append({**row, "category": "both", "quote": claim.summary})
        if (_PUBLIC_CUE.search(text) and len(text) <= 180
                and not any(mark in text for mark in ("?", "？", "可能", "也许", "大概"))):
            selected.append({**row, "category": "group_public", "quote": text})
    return selected


def _expires(row: dict) -> str:
    quote = row["quote"]
    days = 30 if any(word in quote for word in
                     ("今天", "明天", "后天", "下周", "今晚", "这周", "最近")) else 180
    return (row["at"] + timedelta(days=days)).isoformat()


def _backup(database: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = database.parent / "artifacts" / f"memory.before-qce-import-{stamp}.sqlite3"
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(database)) as source, sqlite3.connect(str(target)) as dest:
        source.backup(dest)
    return target


def apply(paths: list[Path], group_id: str, rows: list[dict], config_dir: Path) -> dict:
    database = config_dir / "memory.sqlite3"
    if not database.is_file():
        raise FileNotFoundError(database)
    backup = _backup(database)
    archive = GroupArchive(config_dir / "group_archive.sqlite3")
    try:
        archived = archive.import_qce(paths, group_id=group_id)
    finally:
        archive.close()
    store = SqliteMemoryStore(database)
    stats = {"group_public": 0, "personal_profile": 0,
             "preserved": 0, "pending": 0, "skipped": 0}
    try:
        spaces = SpaceStore(store)
        bindings = BindingStore(store, spaces)
        group_space = f"group-{group_id}"
        if spaces.ref(group_space) is None:
            spaces.create(MemorySpaceType.GROUP_SPACE, name=f"QQ 群 {group_id}",
                          owner="owner", space_id=group_space)
        bindings.bind_memory(f"qq:qq_group:group:{group_id}", group_space,
                             can_read=True, can_write=True, granted_by="owner:qce-import")
        consents = MemberConsentStore(store)
        now = datetime.now(timezone.utc)
        for row in rows:
            evidence = f"qq:group:{group_id}:{row['id']}"
            for layer in (("group_public", "personal_profile")
                          if row["category"] == "both" else (row["category"],)):
                marker = "qce:imported:" + hashlib.sha256(
                    f"{group_id}\0{row['id']}\0{layer}".encode()).hexdigest()[:24]
                if store.get(marker):
                    stats["preserved"] += 1
                    continue
                subject = ("owner" if row.get("is_owner") else
                           f"anon:qq:{row['sender_id']}")
                if layer == "personal_profile":
                    if subject != "owner" and consents.is_opted_out(subject):
                        stats["skipped"] += 1
                        continue
                    ref = (ensure_owner_working_copy(store) if subject == "owner"
                           else bindings.ensure_member_space(
                               subject, conversation_key=f"qq:qq_group:group:{group_id}",
                               granted_by="owner:qce-import", allow_group_evidence=True))
                    space_id = ref.space_id
                    summary = row["quote"]
                    slot = _slot_for_quote(summary)
                else:
                    space_id = group_space
                    summary = f"[本群发言，仅作来源证据] {row['sender_name'] or '群友'}说过：{row['quote']}"
                    slot = ""
                expires = _expires(row)
                if datetime.fromisoformat(expires) <= now:
                    stats["skipped"] += 1
                    continue
                if layer == "group_public":
                    record_key = "qce:group:" + hashlib.sha256(
                        f"{group_id}\0{row['id']}\0{row['quote']}".encode()
                    ).hexdigest()[:24]
                    if store.get_record(record_key) is None:
                        store.put_record(MemoryRecord(
                            kind=MemoryKind.EPISODE, key=record_key,
                            content={"summary": summary[:500],
                                     "source": "qce-group-import",
                                     "evidence_id": evidence,
                                     "subject_id": subject,
                                     "observed_at": row["at"].isoformat(),
                                     "expires_at": expires,
                                     "status": "active", "stable_fact": False,
                                     "importance": 0.5, "confidence": 0.7,
                                     "layer": "group_public_quote"},
                            created_at=row["at"],
                            tags=("curated", "group_import", "episode"),
                            space_id=group_space))
                        stats["group_public"] += 1
                    else:
                        stats["preserved"] += 1
                    store.put(marker, {"action": "quoted_episode", "evidence_id": evidence})
                    continue
                decision = decide_candidate(
                    store, None, summary=summary, source_text=row["text"],
                    space_id=space_id, subject_id=subject, slot_key=slot,
                    expires_at="")
                result = apply_decision(
                    store, decision, summary=summary, evidence_id=evidence,
                    space_id=space_id, subject_id=subject, slot_key=slot,
                    expires_at=expires, observed_at=row["at"],
                    source="qce-group-import")
                store.put(marker, {"action": result.action, "evidence_id": evidence})
                stats[layer if result.action in {"add", "update"} else
                      "pending" if result.action == "pending" else "preserved"] += 1
    finally:
        store.close()
    return {"backup": str(backup), **archived, **stats}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--group-id", required=True)
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    messages = read_messages(args.files, args.group_id)
    pool = candidates(messages, args.group_id)
    counts = {category: sum(row["category"] == category for row in pool)
              for category in ("both", "group_public")}
    print(json.dumps({"source_messages": len(messages), "candidate_claims": len(pool),
                      "categories": counts,
                      "status": "dry_run" if not args.apply else "writing"},
                     ensure_ascii=False))
    if not args.apply:
        return
    print(json.dumps(apply(args.files, args.group_id, pool,
                           args.config_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
