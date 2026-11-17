"""Authorized current facts: BM25 and optional embeddings, fused by rank (RRF)."""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, Sequence

from whale_core.memory.curation import (
    _bm25_scores, _contested_slots, _diverse_hits, _is_expired, _outside_validity, _terms, _space_ids,
)
from whale_core.memory.store import MemoryKind, MemoryRecord, MemoryStore

#: 词项零交集时，向量分至少达到该阈值才允许入选（防纯向量噪声）。
EMBEDDING_FLOOR = 0.3


class EmbeddingProvider(Protocol):
    """嵌入端点契约（model 类插件可提供 embedding.embed 能力；外部服务经 provider 注入）。

    S2：``namespace`` 为缓存分区键（=space_id）。相同文本在不同命名空间
    的缓存项互不复用（INV-08：向量缓存不得跨空间泄漏可读结果）。
    """

    def embed(self, texts: list[str], *, namespace: str = "") -> list[list[float]]:
        ...


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """余弦相似度；任一零向量安全返回 0.0。"""
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
    for x in a:
        na += x * x
    for y in b:
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


class HashEmbeddingProvider:
    """确定性 stdlib 占位嵌入（hashing trick：词项 → 固定维度稀疏向量）。

    诚实标注：这只是词项散列，没有语义理解能力；它让混合链路在无外部
    服务时可跑可测。真实语义嵌入由 model 插件经 EmbeddingProvider 注入。
    embed 复用 curation._terms 词项（中文单字+bigram、英文/数字 token），
    每个词项经 sha256 散列到固定维度并累加计数，最后 L2 归一化。
    同文本必得同向量（不依赖 PYTHONHASHSEED，跨进程确定）。
    """

    def __init__(self, dim: int = 256):
        if dim <= 0:
            raise ValueError("dim must be positive")
        self.dim = dim

    def embed(self, texts: list[str], *, namespace: str = "") -> list[list[float]]:
        # namespace 仅用于缓存分区语义对齐；本地散列无缓存、无状态，直接忽略。
        vectors: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dim
            for term in _terms(text):
                digest = hashlib.sha256(term.encode("utf-8")).digest()
                vec[int.from_bytes(digest[:8], "big") % self.dim] += 1.0
            norm = math.sqrt(sum(v * v for v in vec))
            if norm > 0.0:
                vec = [v / norm for v in vec]
            vectors.append(vec)
        return vectors


@dataclass(frozen=True)
class HybridHit:
    record: MemoryRecord
    score: float
    lexical_score: float
    embedding_score: float
    reasons: tuple[str, ...]


class HybridCurator:
    """Fuse independent lexical and semantic ranks; keep the existing embedder."""

    def __init__(self, store: MemoryStore, *, embedder: EmbeddingProvider | None = None,
                 alpha: float = 0.6, half_life_days: float = 30.0):
        if not 0.0 < alpha < 1.0:
            raise ValueError("alpha must be within (0, 1)")
        if half_life_days <= 0:
            raise ValueError("half_life_days must be positive")
        self.store = store
        self.embedder = embedder
        self.alpha = alpha
        self.half_life_days = half_life_days

    def recall(self, query: str, *, limit: int = 5,
               allowed_spaces=None, include_protected: bool = False,
               now: datetime | None = None) -> tuple[HybridHit, ...]:
        """召回。S2：allowed_spaces 限域（MemorySpaceRef 元组）；空元组 fail
        closed；嵌入按空间分组，缓存命名空间=space_id（INV-08）。"""
        if limit <= 0:
            return ()
        space_ids = _space_ids(allowed_spaces)
        if space_ids is not None and not space_ids:
            return ()
        current = now or datetime.now(timezone.utc)
        query_terms = _terms(query)
        records = (self.store.list_by_kind(MemoryKind.EPISODE, space_ids=space_ids,
                                           include_protected=include_protected) +
                   self.store.list_by_kind(MemoryKind.PREFERENCE, space_ids=space_ids,
                                           include_protected=include_protected))
        contested = _contested_slots(records)
        candidates = [r for r in records if "curated" in r.tags
                      and "superseded" not in r.tags
                      and r.content.get("status", "active") == "active"
                      and (r.space_id, str(r.content.get("subject_id") or "owner"),
                           str(r.content.get("slot_key") or "")) not in contested
                      and not _is_expired(r.content, current)
                      and not _outside_validity(r.content, current)]
        bm25 = _bm25_scores(query, candidates)

        # 按空间分组嵌入：缓存键含 space_id，任何空间拿不到其他空间的
        # 缓存向量；任何异常 → 该空间候选退化纯词项，不影响其他空间。
        vecs_by_key: dict[str, tuple[list[float] | None, list[float] | None]] = {}
        degraded_spaces: set[str] = set()
        if self.embedder is not None and candidates:
            groups: dict[str, list[int]] = {}
            for idx, rec in enumerate(candidates):
                groups.setdefault(rec.space_id, []).append(idx)
            for sid, idxs in groups.items():
                try:
                    summaries = [str(candidates[i].content.get("summary", ""))
                                 for i in idxs]
                    # Namespace support is mandatory for shared embeddings.
                    # A legacy provider without this parameter must degrade to
                    # lexical retrieval, never silently share cross-space cache.
                    vectors = self.embedder.embed([query, *summaries],
                                                  namespace=sid)
                    qv = vectors[0]
                    for pos, i in enumerate(idxs):
                        vecs_by_key[rec_key(candidates, i)] = (qv, vectors[1 + pos])
                except Exception:
                    degraded_spaces.add(sid)

        # Ranking within each channel precedes fusion. A cosine value and a
        # normalized BM25 value are not calibrated probabilities.
        embedding_scores = {}
        for idx, rec in enumerate(candidates):
            pair = vecs_by_key.get(rec_key(candidates, idx))
            embedding_scores[rec.key] = (cosine(pair[0], pair[1])
                                         if pair is not None and pair[0] is not None
                                         and pair[1] is not None else 0.0)
        lexical_rank = {key: rank for rank, (key, _) in enumerate(
            sorted(((key, value) for key, value in bm25.items() if value > 0),
                   key=lambda row: row[1], reverse=True), 1)}
        semantic_rank = {key: rank for rank, (key, _) in enumerate(
            sorted(((key, value) for key, value in embedding_scores.items()
                    if value >= EMBEDDING_FLOOR),
                   key=lambda row: row[1], reverse=True), 1)}
        matches: list[HybridHit] = []
        for idx, rec in enumerate(candidates):
            content = rec.content
            stable = bool(content.get("stable_fact"))
            terms = _terms(str(content.get("summary", "")))
            common = terms & query_terms
            embedding = embedding_scores[rec.key]
            if query_terms:
                if not common and embedding < EMBEDDING_FLOOR:
                    continue
            elif not stable:
                continue
            lexical = bm25.get(rec.key, 0.0)
            age = max(0.0, (current - rec.created_at).total_seconds() / 86400)
            freshness = 1.0 if stable else math.pow(0.5, age / self.half_life_days)
            importance = float(content.get("importance", 0.5))
            confidence = float(content.get("confidence", 1.0))
            lexical_rrf = (1.0 / (60 + lexical_rank[rec.key])
                           if rec.key in lexical_rank else 0.0)
            semantic_rrf = (1.0 / (60 + semantic_rank[rec.key])
                            if rec.key in semantic_rank else 0.0)
            # Preserve lexical-only fallback and scale to the previous score
            # range so the existing diversity selector remains meaningful.
            fused = (((self.alpha * lexical_rrf +
                       (1 - self.alpha) * semantic_rrf) * 61)
                     if semantic_rank else lexical)
            score = confidence * (0.65 * fused + 0.25 * importance
                                  + 0.10 * freshness)
            channel = []
            if common:
                channel.append(f"词项匹配({len(common)}项)")
            if embedding >= EMBEDDING_FLOOR:
                channel.append(f"嵌入相似{embedding:.2f}")
            if lexical_rrf or semantic_rrf:
                channel.append("RRF名次融合")
            if stable:
                channel.append("稳定事实")
            reasons = tuple(channel) + (
                "长期事实不衰减" if stable else f"距今{age:.0f}天",
                f"来源:{content.get('source', 'unknown')}",
                f"证据:{content.get('evidence_id', 'unknown')}",
            ) + (("嵌入不可用，已退化纯词项",)
                  if self.embedder is not None
                  and rec.space_id in degraded_spaces else ())
            matches.append(HybridHit(rec, score, lexical, embedding, reasons))
        matches.sort(key=lambda hit: (hit.score, hit.record.created_at), reverse=True)
        return _diverse_hits(matches, limit)


def rec_key(candidates: list, idx: int) -> str:
    return str(candidates[idx].key)
