"""Select relevant retrieved evidence for a reply without granting permissions.

Retrieval supplies authorized, currently active candidates. This module only
decides which of those candidates belong in the reply context.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from whale_core.memory.relations import query_attributes, relation_hints
from whale_core.memory.roles import memory_role
from whale_core.memory.task_plan import plan_query


@dataclass(frozen=True)
class ActivatedHit:
    hit: object
    role: str
    score: float
    reason: str
    decision: str = "USE"


def _current(record, now: datetime) -> bool:
    if "superseded" in record.tags or record.content.get("status", "active") != "active":
        return False
    for field, future in (("valid_from", True), ("valid_to", False), ("expires_at", False)):
        value = record.content.get(field)
        if not value:
            continue
        try:
            stamp = datetime.fromisoformat(str(value))
            if (stamp > now if future else stamp <= now):
                return False
        except (ValueError, TypeError):
            return False
    return True


def activate(query: str, ranked_hits, *, limit: int = 5,
             now: datetime | None = None) -> tuple[ActivatedHit, ...]:
    """Use the smallest set covering the task; rules are handled separately."""
    topics = set(query_attributes(query))
    plan = plan_query(query)
    selected: list[ActivatedHit] = []
    covered: dict[str, int] = {}
    current = now or datetime.now(timezone.utc)
    for ranked in ranked_hits:
        if ranked.reason in ("different_topic", "different_attribute"):
            continue
        hit = ranked.hit
        content = hit.record.content
        if not _current(hit.record, current):
            continue
        if memory_role(str(content.get("summary") or "")) in {
                "boundary", "access_permission", "constraint"}:
            continue  # Applicable rules come from the separate scoped read.
        needs = tuple(n for n in ranked.covered_needs
                      if n not in {"disclosure", "proactive"})
        if plan.needs and not needs:
            continue
        # A second piece of evidence may complement the first (for example a
        # drink plan and a drink preference), but do not fill Top-K with a
        # whole topic just because all records mention the same entity.
        if needs and all(covered.get(n, 0) >= 2 for n in needs):
            continue
        hints = tuple(content.get("relations") or ())
        if not hints:
            hints = relation_hints(str(content.get("summary") or ""))
        relevant = [hint for hint in hints if hint.get("entity") in topics]
        denied = any(hint.get("action") == "deny" for hint in relevant)
        conditional = any(hint.get("condition") for hint in relevant)
        role = "constraint" if denied else "conditional" if conditional else "relevant"
        selected.append(ActivatedHit(
            hit, role, ranked.score, ranked.reason,
            "CONSTRAIN" if denied else "USE"))
        for need in needs:
            covered[need] = covered.get(need, 0) + 1
        if len(selected) >= limit:
            break
    return tuple(selected)
