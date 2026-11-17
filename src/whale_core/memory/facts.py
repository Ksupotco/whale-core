"""Versioned curated facts shared by explicit commands and future extractors."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import re
from uuid import uuid4

from whale_core.memory.store import MemoryKind, MemoryRecord, MemoryStore
from whale_core.memory.types import MemorySpaceType
from whale_core.memory.relations import relation_hints
from whale_core.memory.roles import constraint_kind, memory_role
from whale_core.memory.temporal import effective_interval


@dataclass(frozen=True)
class FactResult:
    action: str
    record: MemoryRecord | None
    retired_keys: tuple[str, ...] = ()


def _normalized_claim(summary: str) -> str:
    """Ignore presentation differences, not a change in the claim itself."""
    return re.sub(r"\s+", "", str(summary or "")).rstrip("，,。！？!?；;")


def _inferred_slot(summary: str) -> str:
    """Recognize only concrete, replaceable action rules."""
    text = re.sub(r"^\[[^]]+\]\s*", "", summary)
    if (any(word in text for word in ("快递", "包裹", "小盒子"))
            and any(word in text for word in ("放", "驿站", "柜机"))):
        return "delivery:placement"
    return ""


def _explicit_replacement(summary: str) -> bool:
    return any(word in summary for word in (
        "以后", "不再", "不要", "不能", "停用", "改到", "改放", "改成"))


def _opposite_claim(old: str, new: str) -> bool:
    """Only a direct polarity flip is an implicit conflict within a slot."""
    def normalize(value: str) -> str:
        return re.sub(r"[\s，。！!、]", "", re.sub(r"^\[[^]]+\]\s*", "", value))

    left, right = normalize(old), normalize(new)
    if ("提醒" in left and "提醒" in right
            and (("允许" in left and "不想被" in right)
                 or ("允许" in right and "不想被" in left))):
        return True
    return any((negative in left and left.replace(negative, positive, 1) == right)
               or (negative in right and right.replace(negative, positive, 1) == left)
               for negative, positive in (("不喜欢", "喜欢"), ("不想", "想"),
                                          ("不能", "能"), ("不允许", "允许"),
                                          ("不愿意", "愿意"), ("不要", "要")))


def _same_action_slot(old: str, new: str, slot_key: str) -> bool:
    """A concrete placement or plan slot is single valued; broad slots are not."""
    return bool(slot_key.startswith(("delivery:", "plan:"))
                and old.strip() != new.strip())


def _same_delivery_object(left: str, right: str) -> bool:
    cold = ("冷藏件", "冷链")
    package = ("快递", "包裹", "小盒子")
    return any(word in left for word in cold) and any(word in right for word in cold) or (
        any(word in left for word in package) and any(word in right for word in package))


def _dependent_on(old: MemoryRecord, candidate: MemoryRecord) -> bool:
    """Use explicit links, with one narrow legacy allergy/reminder inference."""
    if old.key in (candidate.content.get("depends_on_keys") or ()):
        return True
    premise = str(old.content.get("summary") or "")
    text = str(candidate.content.get("summary") or "")
    # Older extracted reminders have no dependency keys. This exact causal
    # form is enough to quarantine them, without touching unrelated food rules.
    if not ("芒果" in premise and "芒果" in text
            and any(word in premise for word in ("过敏", "喉咙肿", "身体反应"))
            and ("避开芒果" in text or "不吃芒果" in text)
            and "提醒" in text):
        return False
    subject = str(old.content.get("subject_id") or "owner")
    return subject == "owner" or subject in text


def _active_fact(record: MemoryRecord, *, at: datetime | None = None) -> bool:
    current = at or datetime.now(timezone.utc)
    for field, future in (("valid_from", True), ("valid_to", False)):
        value = record.content.get(field)
        if value:
            try:
                at = datetime.fromisoformat(str(value))
                if (at > current if future else at <= current):
                    return False
            except (TypeError, ValueError):
                pass
    expires_at = record.content.get("expires_at")
    if expires_at:
        try:
            if datetime.fromisoformat(str(expires_at)) <= current:
                return False
        except (TypeError, ValueError):
            return False
    return (record.kind is MemoryKind.PREFERENCE
            and "curated" in record.tags
            and "superseded" not in record.tags
            and record.content.get("status", "active") == "active"
            and bool(record.content.get("stable_fact")))


def _decision_key(space_id: str, subject_id: str, evidence_id: str,
                  summary: str) -> str:
    digest = hashlib.sha256(
        "\0".join((space_id, subject_id, evidence_id, summary)).encode("utf-8")
    ).hexdigest()
    return f"memory:fact_decision:{digest}"


def _log_decision(tx, *, action: str, reason: str, summary: str,
                  source: str, evidence_id: str, space_id: str,
                  subject_id: str, record_key: str = "", target_keys=(),
                  support_evidence_ids=()) -> None:
    # Keep only anchors and operation metadata; the fact itself lives in records.
    tx.put(_decision_key(space_id, subject_id, evidence_id, summary), {
        "action": action, "reason": reason, "source": source[:120],
        "evidence_id": evidence_id[:160], "space_id": space_id,
        "subject_id": subject_id, "record_key": record_key,
        "target_keys": list(target_keys),
        "support_evidence_ids": list(support_evidence_ids),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    })


def _new_fact(summary: str, *, source: str, evidence_id: str,
              space_id: str, subject_id: str, family_id: str, version: int,
              now: datetime, expires_at: str = "", slot_key: str = "",
              observed_at: datetime | None = None,
              depends_on_keys: tuple[str, ...] = ()) -> MemoryRecord:
    valid_from, valid_to, expression = effective_interval(
        summary, observed_at=observed_at or now)
    content = {
        "summary": summary[:1500], "source": source[:120],
        "evidence_id": evidence_id[:160],
        "evidence_ids": [evidence_id[:160]],
        "importance": 0.8, "confidence": 1.0,
        "stable_fact": True, "layer": "preference", "consent": True,
        "last_confirmed": now.isoformat(),
        "subject_id": subject_id, "family_id": family_id, "version": version,
        "slot_key": slot_key, "status": "active",
        "memory_role": memory_role(summary),
        "constraint_kind": constraint_kind(summary),
        "depends_on_keys": list(depends_on_keys),
        "valid_from": valid_from or (observed_at or now).isoformat(),
        "relations": list(relation_hints(summary)),
    }
    if valid_to:
        content["valid_to"] = valid_to
    if expression:
        content["time_expression"] = expression
    if expires_at:
        content["expires_at"] = expires_at
    return MemoryRecord(
        kind=MemoryKind.PREFERENCE,
        key=f"curated:{uuid4().hex}",
        content=content,
        created_at=now, tags=("curated", "stable"), space_id=space_id,
    )


def _new_pending_fact(summary: str, *, source: str, evidence_id: str,
                      space_id: str, subject_id: str, slot_key: str,
                      active: MemoryRecord | None, reason: str,
                      now: datetime) -> MemoryRecord:
    record = _new_fact(
        summary, source=source, evidence_id=evidence_id,
        space_id=space_id, subject_id=subject_id,
        family_id=(str(active.content.get("family_id") or uuid4().hex)
                   if active else uuid4().hex),
        version=int(active.content.get("version") or 1) + 1 if active else 1,
        now=now, slot_key=slot_key)
    content = dict(record.content)
    content.update({"status": "pending", "valid_from": "",
                    "pending_reason": reason})
    return replace(record, content=content, tags=("curated", "pending"))


def _close_pending(tx, *, space_id: str, subject_id: str,
                   slot_key: str, family_id: str, status: str,
                   now: datetime) -> None:
    for record in tx.list_records(MemoryKind.PREFERENCE, space_id):
        if (record.content.get("status") != "pending"
                or str(record.content.get("subject_id") or "owner") != subject_id
                or not ((slot_key and record.content.get("slot_key") == slot_key)
                        or (family_id and record.content.get("family_id") == family_id))):
            continue
        content = dict(record.content)
        content.update({"status": status, "valid_to": now.isoformat()})
        if family_id:
            content["family_id"] = family_id
        tx.put_record(replace(record, content=content))


def _revalidate_dependents(tx, retired: tuple[MemoryRecord, ...], *,
                           space_id: str, evidence_id: str,
                           now: datetime) -> tuple[str, ...]:
    """Quarantine active consequences of a retired premise, preserving sources."""
    affected: list[str] = []
    retired_keys = {record.key for record in retired}
    for candidate in tx.list_records(MemoryKind.PREFERENCE, space_id):
        if candidate.key in retired_keys or not _active_fact(candidate):
            continue
        premises = [old.key for old in retired if _dependent_on(old, candidate)]
        if not premises:
            continue
        content = dict(candidate.content)
        content.update({"status": "needs_revalidation",
                        "revalidation_reason": "premise_retired",
                        "revalidation_evidence_id": evidence_id,
                        "revalidation_premise_keys": premises,
                        "valid_to": now.isoformat()})
        tx.put_record(replace(candidate, content=content,
                              tags=tuple(dict.fromkeys(
                                  (*candidate.tags, "needs_revalidation")))))
        affected.append(candidate.key)
    return tuple(affected)


class FactService:
    """One transaction per fact operation; callers supply an authorized space."""

    def __init__(self, store: MemoryStore):
        self.store = store

    def apply_candidate(self, summary: str, *, operation: str,
                        source: str, evidence_id: str, space_id: str = "",
                        subject_id: str = "owner", slot_key: str = "",
                        expires_at: str = "", target_key: str = "",
                        observed_at: datetime | None = None,
                        reason: str = "not_durable",
                        related_keys: tuple[str, ...] = (),
                        depends_on_keys: tuple[str, ...] = (),
                        support_evidence_ids: tuple[str, ...] = ()) -> FactResult:
        """Commit an extraction decision through the same fact state service."""
        slot_key = _inferred_slot(summary) or slot_key
        if operation == "skip":
            return self.skip_candidate(
                summary, source=source, evidence_id=evidence_id,
                space_id=space_id, subject_id=subject_id, reason=reason)
        if operation == "ephemeral" and not expires_at:
            return self.skip_candidate(
                summary, source=source, evidence_id=evidence_id,
                space_id=space_id, subject_id=subject_id,
                action="ephemeral", reason="short_lived")
        if operation == "pending":
            return self.pend_candidate(
                summary, source=source, evidence_id=evidence_id,
                space_id=space_id, subject_id=subject_id,
                slot_key=slot_key,
                reason="unclassified" if reason == "not_durable" else reason)
        if operation in ("add", "compatible_add", "ephemeral"):
            return self.remember(
                summary, source=source, evidence_id=evidence_id,
                space_id=space_id, subject_id=subject_id,
                slot_key=slot_key, expires_at=expires_at,
                observed_at=observed_at,
                depends_on_keys=depends_on_keys,
                decision_reason=("compatible_supplement" if operation == "compatible_add"
                                 else "new_fact"))
        if operation not in ("update", "revoke") or not target_key:
            raise ValueError("unsupported operation or missing target_key")
        target = self.store.get_record(target_key)
        if (target is None or target.space_id != space_id
                or str(target.content.get("subject_id") or "owner") != subject_id):
            raise KeyError(target_key)
        if operation == "update":
            return self.correct(target_key, summary, source=source,
                                evidence_id=evidence_id, related_keys=related_keys,
                                support_evidence_ids=support_evidence_ids,
                                observed_at=observed_at)
        return self.revoke(target_key, source=source, evidence_id=evidence_id,
                           support_evidence_ids=support_evidence_ids)

    def find_exact(self, summary: str, *, allowed_spaces=None) -> MemoryRecord | None:
        refs = None if allowed_spaces is None else tuple(allowed_spaces)
        spaces = (None if refs is None else
                  tuple(str(getattr(space, "space_id", space))
                        for space in refs))
        include_protected = any(
            getattr(space, "type", None) is MemorySpaceType.PROTECTED_ORIGINAL
            for space in (refs or ()))
        records = self.store.list_by_kind(
            MemoryKind.PREFERENCE, space_ids=spaces,
            include_protected=include_protected)
        return next((
            record for record in records
            if _active_fact(record)
            and _normalized_claim(record.content.get("summary") or "") ==
            _normalized_claim(summary)
        ), None)

    def evidence_chain(self, key: str) -> tuple[dict, ...]:
        """Return version/source anchors for one fact family, without old text."""
        target = self.store.get_record(key)
        if target is None or target.kind is not MemoryKind.PREFERENCE:
            return ()
        family_id = str(target.content.get("family_id") or "")
        subject_id = str(target.content.get("subject_id") or "owner")
        records = [
            record for record in self.store.list_by_kind(
                MemoryKind.PREFERENCE, space_ids=(target.space_id,))
            if (record.key == key or (family_id and record.content.get("family_id") == family_id))
            and str(record.content.get("subject_id") or "owner") == subject_id
        ]
        records.sort(key=lambda record: (
            int(record.content.get("version") or 1), record.created_at))
        return tuple({
            "key": record.key,
            "version": int(record.content.get("version") or 1),
            "status": record.content.get("status", "active"),
            "evidence_ids": tuple(record.content.get("evidence_ids")
                                  or [record.content.get("evidence_id")]),
            "current": _active_fact(record),
            "valid_from": record.content.get("valid_from"),
            "valid_to": record.content.get("valid_to"),
        } for record in records)

    def history_for(self, key: str, *, allowed_spaces) -> dict | None:
        """Read one authorized fact family's versions and operations for explanation."""
        target = self.store.get_record(key)
        spaces = {str(getattr(space, "space_id", space)) for space in allowed_spaces}
        if (target is None or target.space_id not in spaces
                or target.kind is not MemoryKind.PREFERENCE
                or not target.content.get("family_id")):
            return None
        chain = self.evidence_chain(key)
        keys = {row["key"] for row in chain}
        versions = []
        for row in chain:
            record = self.store.get_record(row["key"])
            sources = []
            for evidence_id in row["evidence_ids"]:
                source = self.store.get_record(str(evidence_id))
                if source is not None and source.space_id != target.space_id:
                    source = None
                raw = str(source.content.get("summary") or "") if source else ""
                quote = (raw.split("\n", 1)[0][len("用户："):]
                         if raw.startswith("用户：") else "")
                sources.append({
                    "evidence_id": evidence_id,
                    "speaker_id": str(record.content.get("subject_id") or "owner"),
                    "occurred_at": source.created_at.isoformat() if source else "",
                    "quote": quote[:500],
                })
            versions.append({**row,
                             "summary": str(record.content.get("summary") or ""),
                             "sources": sources})
        operations = []
        for decision_key in self.store.keys():
            if not decision_key.startswith("memory:fact_decision:"):
                continue
            decision = self.store.get(decision_key)
            if (not isinstance(decision, dict)
                    or decision.get("space_id") != target.space_id
                    or decision.get("subject_id") !=
                    str(target.content.get("subject_id") or "owner")
                    or not (decision.get("record_key") in keys
                            or keys.intersection(decision.get("target_keys") or ()))):
                continue
            operations.append({field: decision.get(field) for field in (
                "action", "reason", "evidence_id", "recorded_at",
                "record_key", "target_keys", "support_evidence_ids")})
        version_by_key = {row["key"]: row["version"] for row in chain}
        operations.sort(key=lambda row: (
            version_by_key.get(row.get("record_key"),
                               max((version_by_key.get(key, 0)
                                    for key in (row.get("target_keys") or ())),
                                   default=0) + 1),
            str(row.get("recorded_at") or "")))
        current = next((self.store.get_record(row["key"]) for row in reversed(chain)
                        if row["current"]), None)
        return {
            "key": key, "family_id": target.content["family_id"],
            "space_id": target.space_id,
            "subject_id": str(target.content.get("subject_id") or "owner"),
            "current_summary": str(current.content.get("summary") or "")
            if current else "",
            "versions": versions, "operations": operations,
        }

    def decision_for(self, *, summary: str, evidence_id: str,
                     space_id: str = "", subject_id: str = "owner") -> dict | None:
        return self.store.get(_decision_key(space_id, subject_id, evidence_id, summary))

    def decisions_for_evidence(self, evidence_ids, *, allowed_spaces) -> tuple[dict, ...]:
        """Read committed write decisions in source order, scoped by space."""
        order = {str(evidence_id): index for index, evidence_id in
                 enumerate(dict.fromkeys(evidence_ids))}
        spaces = set(allowed_spaces)
        if not order or not spaces:
            return ()
        rows = [
            value for key in self.store.keys()
            if key.startswith("memory:fact_decision:")
            for value in (self.store.get(key),)
            if isinstance(value, dict)
            and value.get("space_id") in spaces
            and value.get("evidence_id") in order
        ]
        rows.sort(key=lambda row: (
            order[row["evidence_id"]], str(row.get("recorded_at") or ""),
            str(row.get("action") or "")))
        return tuple(rows)

    def skip_candidate(self, summary: str, *, source: str, evidence_id: str,
                       space_id: str = "", subject_id: str = "owner",
                       reason: str = "not_durable",
                       action: str = "skip") -> FactResult:
        if not summary.strip() or not source.strip() or not evidence_id.strip():
            raise ValueError("summary, source and evidence_id are required")
        if action not in ("skip", "ephemeral"):
            raise ValueError("unsupported no-write action")
        self.store.transact(lambda tx: _log_decision(
            tx, action=action, reason=reason, summary=summary,
            source=source, evidence_id=evidence_id, space_id=space_id,
            subject_id=subject_id))
        return FactResult(action, None)

    def pend_candidate(self, summary: str, *, source: str, evidence_id: str,
                       space_id: str = "", subject_id: str = "owner",
                       slot_key: str = "", reason: str = "unclassified") -> FactResult:
        """Keep an uncertain candidate inspectable without making it recallable."""
        if not summary.strip() or not source.strip() or not evidence_id.strip():
            raise ValueError("summary, source and evidence_id are required")
        stamp = datetime.now(timezone.utc)

        def write(tx):
            existing = next((record for record in tx.list_records(
                MemoryKind.PREFERENCE, space_id)
                if record.content.get("status") == "pending"
                and record.content.get("subject_id") == subject_id
                and record.content.get("evidence_id") == evidence_id
                and record.content.get("summary") == summary), None)
            if existing is not None:
                return FactResult("pending", existing)
            active = next((record for record in tx.list_records(
                MemoryKind.PREFERENCE, space_id)
                if _active_fact(record)
                and record.content.get("subject_id") == subject_id
                and slot_key and record.content.get("slot_key") == slot_key), None)
            record = _new_pending_fact(
                summary, source=source, evidence_id=evidence_id,
                space_id=space_id, subject_id=subject_id,
                slot_key=slot_key, active=active, reason=reason, now=stamp)
            tx.put_record(record)
            _log_decision(tx, action="pending", reason=reason,
                          summary=summary, source=source, evidence_id=evidence_id,
                          space_id=space_id, subject_id=subject_id,
                          record_key=record.key,
                          target_keys=(active.key,) if active else ())
            return FactResult("pending", record)

        return self.store.transact(write)

    def remember(self, summary: str, *, source: str, evidence_id: str,
                 space_id: str = "", subject_id: str = "owner",
                 expires_at: str = "", slot_key: str = "",
                 observed_at: datetime | None = None,
                 depends_on_keys: tuple[str, ...] = (),
                 decision_reason: str = "new_fact") -> FactResult:
        if not summary.strip() or not source.strip() or not evidence_id.strip():
            raise ValueError("summary, source and evidence_id are required")
        slot_key = _inferred_slot(summary) or slot_key
        if slot_key and _explicit_replacement(summary):
            target = next((record for record in self.store.list_by_kind(
                MemoryKind.PREFERENCE, space_ids=(space_id,))
                if _active_fact(record, at=observed_at)
                and str(record.content.get("subject_id") or "owner") == subject_id
                and record.content.get("slot_key") == slot_key
                and str(record.content.get("summary") or "").strip() != summary.strip()
                and (_opposite_claim(str(record.content.get("summary") or ""), summary)
                     or _same_action_slot(str(record.content.get("summary") or ""),
                                          summary, slot_key))), None)
            if target is not None:
                return self.correct(target.key, summary, source=source,
                                    evidence_id=evidence_id,
                                    observed_at=observed_at)
        stamp = datetime.now(timezone.utc)

        def write(tx):
            for key in depends_on_keys:
                premise = tx.get_record(key)
                if premise is None or premise.space_id != space_id or not _active_fact(premise):
                    raise KeyError(key)
            active = [record for record in tx.list_records(MemoryKind.PREFERENCE, space_id)
                      if _active_fact(record, at=observed_at)
                      and str(record.content.get("subject_id") or "owner") == subject_id]
            for record in active:
                if (_normalized_claim(record.content.get("summary", ""))
                        != _normalized_claim(summary)):
                    continue
                existing_slot = str(record.content.get("slot_key") or "")
                if slot_key and existing_slot and existing_slot != slot_key:
                    continue
                content = dict(record.content)
                evidence = list(content.get("evidence_ids") or [content["evidence_id"]])
                if evidence_id not in evidence:
                    evidence.append(evidence_id[:160])
                    content["evidence_ids"] = evidence
                if expires_at and str(content.get("expires_at") or "") < expires_at:
                    content["expires_at"] = expires_at
                if slot_key and not content.get("slot_key"):
                    content["slot_key"] = slot_key
                if not content.get("relations"):
                    content["relations"] = list(relation_hints(summary))
                if content != dict(record.content):
                    tx.put_record(replace(record, content=content))
                _log_decision(tx, action="preserve", reason="same_fact",
                              summary=summary, source=source, evidence_id=evidence_id,
                              space_id=space_id, subject_id=subject_id,
                              record_key=record.key)
                return FactResult("preserve", replace(record, content=content))
            conflicting = next((record for record in active
                                if slot_key and record.content.get("slot_key") == slot_key
                                and (_opposite_claim(
                                    str(record.content.get("summary") or ""), summary)
                                     or _same_action_slot(
                                         str(record.content.get("summary") or ""),
                                         summary, slot_key))), None)
            if conflicting is not None:
                existing = next((record for record in tx.list_records(
                    MemoryKind.PREFERENCE, space_id)
                    if record.content.get("status") == "pending"
                    and record.content.get("subject_id") == subject_id
                    and record.content.get("evidence_id") == evidence_id
                    and record.content.get("summary") == summary), None)
                if existing is not None:
                    return FactResult("pending", existing)
                pending = _new_pending_fact(
                    summary, source=source, evidence_id=evidence_id,
                    space_id=space_id, subject_id=subject_id,
                    slot_key=slot_key, active=conflicting,
                    reason="same_slot_conflict", now=stamp)
                tx.put_record(pending)
                _log_decision(tx, action="pending", reason="same_slot_conflict",
                              summary=summary, source=source, evidence_id=evidence_id,
                              space_id=space_id, subject_id=subject_id,
                              record_key=pending.key,
                              target_keys=(conflicting.key,))
                return FactResult("pending", pending)
            record = _new_fact(
                summary, source=source, evidence_id=evidence_id,
                space_id=space_id, subject_id=subject_id,
                family_id=uuid4().hex, version=1, now=stamp,
                expires_at=expires_at, slot_key=slot_key,
                observed_at=observed_at, depends_on_keys=depends_on_keys)
            tx.put_record(record)
            _log_decision(tx, action="ephemeral" if expires_at else "add",
                          reason="expires_at" if expires_at else decision_reason,
                          summary=summary, source=source, evidence_id=evidence_id,
                          space_id=space_id, subject_id=subject_id,
                          record_key=record.key)
            return FactResult("add", record)

        return self.store.transact(write)

    def revoke(self, target_key: str, *, source: str, evidence_id: str,
               support_evidence_ids: tuple[str, ...] = ()) -> FactResult:
        """Retire a claim while retaining its text-free operation trail."""
        if not source.strip() or not evidence_id.strip():
            raise ValueError("source and evidence_id are required")
        stamp = datetime.now(timezone.utc)

        def write(tx):
            target = tx.get_record(target_key)
            if target is None or not (_active_fact(target)
                                      or target.content.get("status") == "needs_revalidation"):
                raise KeyError(target_key)
            subject_id = str(target.content.get("subject_id") or "owner")
            family_id = str(target.content.get("family_id") or "")
            slot_key = str(target.content.get("slot_key") or "")
            siblings = [
                record for record in tx.list_records(MemoryKind.PREFERENCE, target.space_id)
                if (_active_fact(record)
                    or record.key == target_key)
                and str(record.content.get("subject_id") or "owner") == subject_id
                and (record.key == target_key
                     or (family_id and record.content.get("family_id") == family_id))
            ]
            for old in siblings:
                content = dict(old.content)
                content.update({"status": "revoked", "valid_to": stamp.isoformat(),
                                "revoked_at": stamp.isoformat(),
                                "family_id": family_id or old.content.get("family_id", "")})
                tx.put_record(replace(
                    old, content=content,
                    tags=tuple(dict.fromkeys((*old.tags, "superseded")))))
            dependents = _revalidate_dependents(
                tx, tuple(siblings), space_id=target.space_id,
                evidence_id=evidence_id, now=stamp)
            _close_pending(tx, space_id=target.space_id, subject_id=subject_id,
                           slot_key="", family_id=family_id,
                           status="revoked", now=stamp)
            _log_decision(tx, action="revoke", reason="explicit_revocation",
                          summary=str(target.content.get("summary") or ""),
                          source=source, evidence_id=evidence_id,
                          space_id=target.space_id, subject_id=subject_id,
                          target_keys=(*[old.key for old in siblings], *dependents),
                          support_evidence_ids=support_evidence_ids)
            return FactResult("revoke", target,
                              (*[old.key for old in siblings], *dependents))

        return self.store.transact(write)

    def correct(self, target_key: str, new_summary: str, *,
                source: str, evidence_id: str,
                related_keys: tuple[str, ...] = (),
                support_evidence_ids: tuple[str, ...] = (),
                observed_at: datetime | None = None) -> FactResult:
        if not new_summary.strip() or not source.strip() or not evidence_id.strip():
            raise ValueError("new_summary, source and evidence_id are required")
        stamp = datetime.now(timezone.utc)
        effective_at = observed_at or stamp

        def write(tx):
            target = tx.get_record(target_key)
            if target is None or not (_active_fact(target, at=observed_at)
                                      or target.content.get("status") == "needs_revalidation"):
                raise KeyError(target_key)
            family_id = str(target.content.get("family_id") or uuid4().hex)
            subject_id = str(target.content.get("subject_id") or "owner")
            old_summary = str(target.content.get("summary") or "")
            slot_key = (str(target.content.get("slot_key") or "")
                        or _inferred_slot(old_summary)
                        or _inferred_slot(new_summary))
            selected_keys = {target_key, *related_keys}
            siblings = [
                record for record in tx.list_records(MemoryKind.PREFERENCE, target.space_id)
                if (_active_fact(record, at=observed_at)
                    or record.key == target_key)
                and str(record.content.get("subject_id") or "owner") == subject_id
                and not (slot_key
                         and record.content.get("slot_key")
                         and record.content.get("slot_key") != slot_key)
                and (
                    record.key in selected_keys
                    or record.content.get("family_id") == family_id
                    or (slot_key == "delivery:placement"
                        and _inferred_slot(str(record.content.get("summary") or "")) == slot_key
                        and _same_delivery_object(
                            old_summary, str(record.content.get("summary") or "")))
                    or str(record.content.get("summary") or "") in (
                        old_summary, new_summary))
            ]
            if (_normalized_claim(new_summary) == _normalized_claim(old_summary)
                    and target.content.get("status") == "active"):
                return FactResult("preserve", target)
            pending_versions = [int(record.content.get("version") or 1)
                                for record in tx.list_records(
                                    MemoryKind.PREFERENCE, target.space_id)
                                if record.content.get("status") == "pending"
                                and str(record.content.get("subject_id") or "owner") == subject_id
                                and ((slot_key
                                      and record.content.get("slot_key") == slot_key)
                                     or record.content.get("family_id") == family_id)]
            version = max([int(record.content.get("version") or 1)
                           for record in siblings] + pending_versions) + 1
            new = _new_fact(
                new_summary, source=source, evidence_id=evidence_id,
                space_id=target.space_id, subject_id=subject_id, family_id=family_id,
                version=version, now=stamp,
                slot_key=slot_key, observed_at=observed_at)
            starts = datetime.fromisoformat(str(new.content["valid_from"]))
            scheduled = starts > effective_at
            for old in siblings:
                content = dict(old.content)
                content.update({"superseded_by": new.key, "family_id": family_id,
                                "valid_to": starts.isoformat() if scheduled else effective_at.isoformat()})
                if not scheduled:
                    content.update({"layer": "superseded", "superseded_at": stamp.isoformat(),
                                    "status": "superseded"})
                tx.put_record(replace(
                    old, content=content,
                    tags=(old.tags if scheduled else
                          tuple(dict.fromkeys((*old.tags, "superseded"))))))
            dependents = (() if scheduled else _revalidate_dependents(
                tx, tuple(siblings), space_id=target.space_id,
                evidence_id=evidence_id, now=stamp))
            _close_pending(
                tx, space_id=target.space_id, subject_id=subject_id,
                slot_key="",
                family_id=family_id, status="superseded", now=stamp)
            tx.put_record(new)
            _log_decision(tx, action="update", reason="explicit_correction",
                          summary=new_summary, source=source, evidence_id=evidence_id,
                          space_id=target.space_id, subject_id=subject_id,
                          record_key=new.key,
                          target_keys=(*[old.key for old in siblings], *dependents),
                          support_evidence_ids=support_evidence_ids)
            return FactResult("update", new,
                              (*[old.key for old in siblings], *dependents))

        return self.store.transact(write)

    def forget(self, target_key: str) -> tuple[MemoryRecord, ...]:
        def write(tx):
            target = tx.get_record(target_key)
            if target is None or not (_active_fact(target)
                                      or target.content.get("status") == "needs_revalidation"):
                return ()
            family_id = target.content.get("family_id")
            subject_id = str(target.content.get("subject_id") or "owner")
            summary = str(target.content.get("summary") or "")
            slot_key = str(target.content.get("slot_key") or "")
            siblings = tuple(
                record for record in tx.list_records(MemoryKind.PREFERENCE, target.space_id)
                if (_active_fact(record)
                    or record.content.get("status") == "needs_revalidation")
                and str(record.content.get("subject_id") or "owner") == subject_id
                and (
                    (family_id and record.content.get("family_id") == family_id)
                    or str(record.content.get("summary") or "") == summary))
            for record in siblings:
                tx.delete_record(record.key)
            for record in tx.list_records(MemoryKind.PREFERENCE, target.space_id):
                if (record.content.get("status") == "pending"
                        and str(record.content.get("subject_id") or "owner") == subject_id
                        and (family_id and record.content.get("family_id") == family_id)):
                    tx.delete_record(record.key)
            return siblings

        return self.store.transact(write)
