"""Opt-in curated memories with evidence-aware, explainable retrieval.

Raw call transcripts in MemoryStore are NOT automatically promoted to facts.
This pure-Python implementation has no embeddings, LLM calls or hidden collection.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from whale_core.memory.store import MemoryKind, MemoryRecord, MemoryStore
from whale_core.memory.relations import relation_hints


def _terms(text: str) -> set[str]:
    tokens = re.findall(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]+", text.lower())
    parts: set[str] = set()
    for tok in tokens:
        if re.fullmatch(r"[\u4e00-\u9fff]+", tok):
            # 中文词项 = 单字 + 二元组：单字查询（如「辣」）也要能命中
            # 多字事实（「我怕辣」）；召回靠交集，精确度交给打分排序。
            parts.update(tok)
            parts.update(tok[i:i + 2] for i in range(max(0, len(tok) - 1)))
        else:
            parts.add(tok)
    return parts


def _term_counts(text: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for token in re.findall(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]+", text.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            counts.update(token)
            counts.update(token[i:i + 2] for i in range(len(token) - 1))
        else:
            counts[token] += 1
    return counts


def _bm25_scores(query: str, records) -> dict[str, float]:
    """Normalized BM25 over the already authorized and active candidate pool."""
    terms = _terms(query)
    if not terms or not records:
        return {}
    counts = [_term_counts(str(rec.content.get("summary") or ""))
              for rec in records]
    lengths = [sum(row.values()) for row in counts]
    average = max(1.0, sum(lengths) / len(lengths))
    frequency = Counter(term for row in counts for term in row)
    scores: dict[str, float] = {}
    for rec, row, length in zip(records, counts, lengths):
        score = 0.0
        for term in terms & row.keys():
            tf = row[term]
            idf = math.log1p((len(records) - frequency[term] + 0.5) /
                             (frequency[term] + 0.5))
            score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / average))
        scores[rec.key] = score
    maximum = max(scores.values(), default=0.0)
    return {key: value / maximum for key, value in scores.items()} if maximum else scores


def _diverse_hits(matches, limit: int):
    """Keep near-duplicate wording from filling a small reply context."""
    if len(matches) <= limit or limit <= 1:
        return tuple(matches[:limit])
    # Diversity must not promote a weakly related item over a clear match.
    floor = matches[0].score * 0.68
    remaining = [hit for hit in matches if hit.score >= floor]
    fallback = [hit for hit in matches if hit.score < floor]
    selected = []
    terms = {
        hit.record.key: {term for term in _terms(
            str(hit.record.content.get("summary") or "")) if len(term) > 1}
        for hit in matches
    }
    while remaining and len(selected) < limit:
        def rank(hit):
            own = terms[hit.record.key]
            overlap = max((len(own & terms[prior.record.key]) /
                           max(1, len(own | terms[prior.record.key]))
                           for prior in selected), default=0.0)
            return (hit.score - 0.40 * overlap, hit.score,
                    hit.record.created_at)
        best = max(remaining, key=rank)
        selected.append(best)
        remaining.remove(best)
    return tuple((selected + fallback)[:limit])


@dataclass(frozen=True)
class RecallHit:
    record: MemoryRecord
    score: float
    reasons: tuple[str, ...]


def _space_ids(allowed_spaces) -> tuple[str, ...] | None:
    """从 MemorySpaceRef / 字符串元组提取 space_id；None 原样透传（兼容期）。"""
    if allowed_spaces is None:
        return None
    out: list[str] = []
    for item in allowed_spaces:
        out.append(str(getattr(item, "space_id", item)))
    return tuple(dict.fromkeys(out))


def _is_expired(content, now: datetime) -> bool:
    """G2/MEM-04：带 expires_at 的记录到期即退出召回（不删记录本身）。"""
    exp = (content or {}).get("expires_at")
    if not exp:
        return False
    try:
        return datetime.fromisoformat(str(exp)) <= now
    except (ValueError, TypeError):
        return False  # 畸形时间戳不误杀（召回侧 fail open 仅限此字段）


def _outside_validity(content, now: datetime) -> bool:
    """Exclude facts outside their recorded effective interval."""
    for field, before in (("valid_from", True), ("valid_to", False)):
        value = (content or {}).get(field)
        if not value:
            continue
        try:
            when = datetime.fromisoformat(str(value))
            outside = when > now if before else when <= now
            if outside:
                return True
        except (ValueError, TypeError):
            continue
    return False


def _contested_slots(records) -> set[tuple[str, str, str]]:
    """A pending contradiction suspends the old slot in ordinary recall."""
    return {
        (rec.space_id, str(rec.content.get("subject_id") or "owner"),
         str(rec.content["slot_key"]))
        for rec in records
        if rec.kind is MemoryKind.PREFERENCE
        and rec.content.get("status") == "pending"
        and rec.content.get("pending_reason") == "same_slot_conflict"
        and rec.content.get("slot_key")
    }


class MemoryCurator:
    """Explicit candidate promotion and retrieval; never consumes audio or raw logs."""

    def __init__(self, store: MemoryStore, half_life_days: float = 30.0):
        if half_life_days <= 0:
            raise ValueError("half_life_days must be positive")
        self.store = store
        self.half_life_days = half_life_days

    def remember(self, summary: str, *, source: str, evidence_id: str,
                 importance: float = 0.5, confidence: float = 1.0,
                 stable_fact: bool = False, consent: bool = False,
                 key: str | None = None, now: datetime | None = None,
                 space_id: str = "") -> MemoryRecord:
        if not consent:
            raise PermissionError("Explicit memory consent required")
        if not summary.strip() or not source.strip() or not evidence_id.strip():
            raise ValueError("summary, source and evidence_id are required")
        if not 0 <= importance <= 1 or not 0 <= confidence <= 1:
            raise ValueError("importance and confidence must be within [0,1]")
        kind = MemoryKind.PREFERENCE if stable_fact else MemoryKind.EPISODE
        stamp = now or datetime.now(timezone.utc)
        record = MemoryRecord(
            kind=kind,
            key=key or f"curated:{uuid4().hex}",
            content={"summary": summary[:1500], "source": source[:120],
                     "evidence_id": evidence_id[:160], "importance": importance,
                     "confidence": confidence, "stable_fact": stable_fact,
                     # G2 分层元数据：来源/确认时间/同意/置信度显式可查
                     "layer": "preference" if stable_fact else "curated",
                     "consent": True,
                     "last_confirmed": stamp.isoformat(),
                     "relations": list(relation_hints(summary))},
            created_at=stamp,
            tags=("curated", "stable" if stable_fact else "episode"),
            space_id=space_id,
        )
        self.store.put_record(record)
        return record

    def recall(self, query: str, *, limit: int = 5,
               now: datetime | None = None,
               allowed_spaces=None, include_protected: bool = False) -> tuple[RecallHit, ...]:
        """召回。S2：``allowed_spaces`` 为 MemorySpaceRef 元组（服务端
        ResolvedContext 提供）；空元组 fail closed；None 为兼容期旧行为
        （仍不含 protected_original，存储层硬排除）。"""
        if limit <= 0:
            return ()
        space_ids = _space_ids(allowed_spaces)
        if space_ids is not None and not space_ids:
            return ()  # 已解析但零授权空间：查询前即止
        current = now or datetime.now(timezone.utc)
        query_terms = _terms(query)
        matches: list[RecallHit] = []
        records = (self.store.list_by_kind(MemoryKind.EPISODE, space_ids=space_ids,
                                           include_protected=include_protected) +
                   self.store.list_by_kind(MemoryKind.PREFERENCE, space_ids=space_ids,
                                           include_protected=include_protected))
        contested = _contested_slots(records)
        eligible = [rec for rec in records if "curated" in rec.tags
                    and "superseded" not in rec.tags
                    and rec.content.get("status", "active") == "active"
                    and (rec.space_id, str(rec.content.get("subject_id") or "owner"),
                         str(rec.content.get("slot_key") or "")) not in contested
                    and not _is_expired(rec.content, current)
                    and not _outside_validity(rec.content, current)]
        bm25 = _bm25_scores(query, eligible)
        for rec in eligible:
            content = rec.content
            stable = bool(content.get("stable_fact"))
            terms = _terms(str(content.get("summary", "")))
            common = terms & query_terms
            if query_terms and not common:
                continue
            if not query_terms and not stable:
                continue
            relevance = bm25.get(rec.key, 0.0)
            age = max(0.0, (current - rec.created_at).total_seconds() / 86400)
            freshness = 1.0 if stable else math.pow(0.5, age / self.half_life_days)
            importance = float(content.get("importance", 0.5))
            confidence = float(content.get("confidence", 1.0))
            score = confidence * (0.65 * relevance + 0.25 * importance + 0.10 * freshness)
            reasons = (f"BM25:{relevance:.2f}" if common else "稳定事实",
                       "长期事实不衰减" if stable else f"距今{age:.0f}天",
                       f"来源:{content.get('source', 'unknown')}",
                       f"证据:{content.get('evidence_id', 'unknown')}")
            matches.append(RecallHit(rec, score, reasons))
        matches.sort(key=lambda hit: (hit.score, hit.record.created_at), reverse=True)
        return _diverse_hits(matches, limit)

    def forget(self, key: str) -> bool:
        record = self.store.get_record(key)
        if record is None or "curated" not in record.tags:
            return False
        self.store.delete(key)
        return True
