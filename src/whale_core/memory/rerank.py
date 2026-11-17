"""Context-aware ranking of already authorized retrieval candidates."""
from __future__ import annotations

from dataclasses import dataclass

from whale_core.memory.relations import query_attributes, relation_hints
from whale_core.memory.task_plan import evidence_needs, plan_query


@dataclass(frozen=True)
class RankedHit:
    hit: object
    score: float
    reason: str
    covered_needs: tuple[str, ...] = ()


def rerank(query: str, hits) -> tuple[RankedHit, ...]:
    """Keep every candidate; use entity and attribute only to change order."""
    requested = query_attributes(query)
    topics = set(requested)
    plan = plan_query(query)
    ranked: list[RankedHit] = []
    for hit in hits:
        content = hit.record.content
        hints = tuple(content.get("relations") or ())
        if not hints:
            hints = relation_hints(str(content.get("summary") or ""))
        matches_topic = bool(topics & {hint.get("entity") for hint in hints})
        matches_attribute = any(
            bool(requested.get(str(hint.get("entity")), set()) &
                 set(str(hint.get("attribute") or "").split(",")))
            for hint in hints)
        asks_attribute = any(requested.get(str(hint.get("entity")))
                             for hint in hints if hint.get("entity") in topics)
        if topics and hints and not matches_topic:
            reason, boost = "different_topic", -1.0
        elif matches_topic and matches_attribute:
            reason, boost = "topic_and_attribute", 0.25
        elif matches_topic and asks_attribute:
            reason, boost = "different_attribute", -0.25
        elif matches_topic:
            reason, boost = "topic", 0.12
        else:
            reason, boost = "lexical_evidence", 0.0
        coverage = evidence_needs(str(content.get("summary") or ""), plan)
        # Task coverage dominates a weak lexical coincidence. Relation hints
        # still distinguish attributes within a covered task.
        if plan.needs:
            boost += 0.35 * len(set(coverage) - {"disclosure", "proactive"})
            if not coverage:
                boost -= 0.4
        ranked.append(RankedHit(hit, hit.score + boost, reason, coverage))
    ranked.sort(key=lambda item: item.score, reverse=True)
    return tuple(ranked)
