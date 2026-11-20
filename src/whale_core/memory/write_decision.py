"""Choose a fact operation before calling the versioned fact store.

Only active facts of the same subject, space and replaceable slot are compared.
The model is consulted for an ambiguous relationship, never for exact repeats
or an explicit correction. Its output cannot authorize a cross-scope target.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from whale_core.memory.facts import (
    FactService, _active_fact, _normalized_claim, _opposite_claim,
)
from whale_core.memory.store import MemoryKind


@dataclass(frozen=True)
class WriteDecision:
    action: str
    target_key: str = ""
    reason: str = ""


def decide_candidate(store, model, *, summary: str, source_text: str,
                     space_id: str, subject_id: str, slot_key: str,
                     expires_at: str = "") -> WriteDecision:
    """Return one operation; uncertain changes remain pending and unreadable."""
    active = [record for record in store.list_by_kind(
        MemoryKind.PREFERENCE, space_ids=(space_id,))
        if _active_fact(record)
        and str(record.content.get("subject_id") or "owner") == subject_id]
    exact = next((record for record in active
                  if _normalized_claim(record.content.get("summary") or "") ==
                  _normalized_claim(summary)
                  and (not slot_key or not record.content.get("slot_key")
                       or record.content.get("slot_key") == slot_key)), None)
    if exact is not None:
        return WriteDecision("preserve", exact.key, "same_fact")
    related = [record for record in active
               if slot_key and record.content.get("slot_key") == slot_key]
    if not related:
        return WriteDecision("ephemeral" if expires_at else "add", reason="new_fact")

    # An explicit personal correction may replace one old claim. Unspecified
    # reversals stay pending, even if a model thinks the new sentence is likely.
    explicit = any(word in source_text for word in
                   ("以后不", "以后改", "现在不", "改成", "改为", "其实不", "之前说错"))
    if explicit and len(related) == 1:
        return WriteDecision("update", related[0].key, "explicit_correction")
    if any(_opposite_claim(str(row.content.get("summary") or ""), summary)
           for row in related):
        return WriteDecision("pending", reason="same_slot_conflict")

    if model is None:
        return WriteDecision("pending", reason="decision_unavailable")

    prompt = (
        "判断新事实与同一人的旧事实是否兼容。只输出 JSON，"
        '{"action":"compatible_add|update|preserve|revoke|pending|skip",'
        '"target_key":"旧记录ID或空字符串"}。'
        "compatible_add 表示兼容补充；update/revoke 必须有本人明确更正或收回的原话；"
        "不确定选 pending。不得对其他主体或情境的事实操作。\n"
        f"原话：{source_text}\n新事实：{summary}\n旧事实："
        + json.dumps([{"key": row.key, "summary": row.content.get("summary"),
                       "slot_key": row.content.get("slot_key")}
                      for row in related[:5]], ensure_ascii=False))
    try:
        raw = model.reply(source_text, persona=prompt, memories=(), history=())
        parsed = json.loads(str(raw).strip())
    except (TypeError, ValueError):
        return WriteDecision("pending", reason="decision_unavailable")
    action = str(parsed.get("action") or "") if isinstance(parsed, dict) else ""
    target = str(parsed.get("target_key") or "") if isinstance(parsed, dict) else ""
    valid_targets = {row.key for row in related}
    if action in {"update", "revoke"}:
        if not explicit or target not in valid_targets:
            return WriteDecision("pending", reason="unverified_target")
        return WriteDecision(action, target, "explicit_correction")
    if action == "compatible_add":
        return WriteDecision("ephemeral" if expires_at else "compatible_add",
                             reason="compatible_supplement")
    if action == "preserve" and target in valid_targets:
        return WriteDecision("pending", reason="nonidentical_preserve")
    if action == "skip":
        return WriteDecision("skip", reason="not_durable")
    return WriteDecision("pending", reason="unclear_relation")


def apply_decision(store, decision: WriteDecision, *, summary: str,
                   evidence_id: str, space_id: str, subject_id: str,
                   slot_key: str, expires_at: str = "", observed_at=None,
                   source: str = "automatic"):
    """Execute a validated decision through FactService's transaction path."""
    facts = FactService(store)
    if decision.action == "preserve":
        current = store.get_record(decision.target_key)
        if (current is None or not _active_fact(current)
                or current.space_id != space_id
                or str(current.content.get("subject_id") or "owner") != subject_id
                or _normalized_claim(current.content.get("summary") or "") !=
                _normalized_claim(summary)):
            raise ValueError("preserve target changed")
        return facts.remember(summary, source=source, evidence_id=evidence_id,
                              space_id=space_id, subject_id=subject_id,
                              slot_key=slot_key, expires_at=expires_at,
                              observed_at=observed_at)
    return facts.apply_candidate(
        summary, operation=decision.action, source=source,
        evidence_id=evidence_id, space_id=space_id, subject_id=subject_id,
        slot_key=slot_key, expires_at=expires_at,
        observed_at=observed_at, target_key=decision.target_key,
        reason=decision.reason)
