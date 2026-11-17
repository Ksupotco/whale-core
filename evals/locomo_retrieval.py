"""LoCoMo text-only evidence retrieval against whale-core's curated recall path.

This is a retrieval diagnostic, not an end-to-end memory-extraction or QA score.
Each dialogue turn is explicitly promoted to a curated memory with synthetic
benchmark consent. Gold evidence and answers are used only after retrieval.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import time
from collections import defaultdict
from pathlib import Path

from whale_core.memory.curation import MemoryCurator
from whale_core.memory.hybrid import HashEmbeddingProvider, HybridCurator
from whale_core.memory.store import InMemoryStore


CATEGORY_NAMES = {
    1: "multi_hop", 2: "temporal", 3: "open_domain",
    4: "single_hop", 5: "unanswerable",
}


def load_samples(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("LoCoMo input must be a nonempty JSON array")
    for sample in data:
        if not isinstance(sample, dict) or not all(
            key in sample for key in ("sample_id", "conversation", "qa")
        ):
            raise ValueError("Input does not have the LoCoMo sample format")
    return data


def select_questions(samples: list[dict], *, limit: int | None,
                     seed: int) -> list[tuple[dict, int, dict]]:
    """Deterministic category-balanced sample; full dataset when limit is None."""
    groups: dict[int, list[tuple[dict, int, dict]]] = defaultdict(list)
    for sample in samples:
        for index, question in enumerate(sample["qa"]):
            category = int(question["category"])
            if category not in CATEGORY_NAMES:
                raise ValueError(f"Unknown LoCoMo category: {category}")
            groups[category].append((sample, index, question))
    if limit is None:
        return [item for category in sorted(groups) for item in groups[category]]
    if limit < 1:
        raise ValueError("--limit must be positive")
    rng = random.Random(seed)
    for group in groups.values():
        rng.shuffle(group)
    selected: list[tuple[dict, int, dict]] = []
    # Round-robin retains rare categories while still working when one empties.
    while len(selected) < limit and any(groups.values()):
        for category in sorted(groups):
            if groups[category] and len(selected) < limit:
                selected.append(groups[category].pop())
    return selected


def build_curator(sample: dict, *, method: str):
    store = InMemoryStore()
    curated = MemoryCurator(store)
    conversation = sample["conversation"]
    session_keys = sorted(
        (key for key in conversation if key.startswith("session_")
         and key[8:].isdigit()), key=lambda key: int(key[8:]))
    for session_key in session_keys:
        session_time = str(conversation.get(f"{session_key}_date_time") or "")
        for turn in conversation[session_key]:
            dialogue_id = str(turn["dia_id"])
            summary = f"[{session_time}] {turn['speaker']}: {turn['text']}"
            # The public release does not include image files. Captions are not
            # silently substituted for absent images in this text-only protocol.
            curated.remember(summary, source="locomo", evidence_id=dialogue_id,
                             consent=True, key=f"locomo:{dialogue_id}")
    if method == "lexical":
        return curated, len(session_keys)
    if method == "hash":
        return HybridCurator(store, embedder=HashEmbeddingProvider()), len(session_keys)
    raise ValueError(f"Unknown retrieval method: {method}")


def run(samples: list[dict], *, limit: int | None = 100, seed: int = 42,
        top_k: int = 5, method: str = "lexical") -> dict:
    if top_k < 1:
        raise ValueError("--top-k must be positive")
    selected = select_questions(samples, limit=limit, seed=seed)
    by_sample: dict[str, list[tuple[int, dict]]] = defaultdict(list)
    for sample, index, question in selected:
        by_sample[str(sample["sample_id"])].append((index, question))
    rows: list[dict] = []
    for sample in samples:
        sample_id = str(sample["sample_id"])
        if sample_id not in by_sample:
            continue
        curator, sessions = build_curator(sample, method=method)
        for index, question in by_sample[sample_id]:
            started = time.perf_counter()
            hits = curator.recall(str(question["question"]), limit=top_k)
            elapsed_ms = (time.perf_counter() - started) * 1000
            retrieved = [str(hit.record.content["evidence_id"]) for hit in hits]
            gold = [evidence_id for value in question.get("evidence", [])
                    for evidence_id in re.findall(r"D\d+:\d+", str(value))]
            scoreable = int(question["category"]) != 5 and bool(gold)
            found = set(retrieved) & set(gold)
            rows.append({
                "sample_id": sample_id, "question_index": index,
                "category": CATEGORY_NAMES[int(question["category"])],
                "question": question["question"], "gold_evidence": gold,
                "retrieved_evidence": retrieved,
                "any_hit": bool(found) if scoreable else None,
                "all_hit": set(gold) <= set(retrieved) if scoreable else None,
                "evidence_recall": len(found) / len(set(gold)) if scoreable else None,
                "latency_ms": round(elapsed_ms, 3), "sessions": sessions,
            })
    scored = [row for row in rows if row["any_hit"] is not None]
    categories = {}
    for name in CATEGORY_NAMES.values():
        subset = [row for row in scored if row["category"] == name]
        categories[name] = {
            "questions": len(subset),
            "any_hit_rate": sum(row["any_hit"] for row in subset) / len(subset)
            if subset else None,
            "all_hit_rate": sum(row["all_hit"] for row in subset) / len(subset)
            if subset else None,
        }
    return {
        "protocol": "locomo10-text-curated-turn-retrieval-v1",
        "method": method, "top_k": top_k, "seed": seed,
        "selected_questions": len(rows), "scored_questions": len(scored),
        "unanswerable_questions": sum(row["category"] == "unanswerable"
                                      for row in rows),
        "unscored_questions": len(rows) - len(scored),
        "any_hit_rate": sum(row["any_hit"] for row in scored) / len(scored)
        if scored else None,
        "all_hit_rate": sum(row["all_hit"] for row in scored) / len(scored)
        if scored else None,
        "mean_evidence_recall": sum(row["evidence_recall"] for row in scored)
        / len(scored) if scored else None,
        "mean_latency_ms": sum(row["latency_ms"] for row in rows) / len(rows)
        if rows else None,
        "categories": categories, "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True,
                        help="Path to a LoCoMo-format JSON file")
    parser.add_argument("--data-source", default=None,
                        help="Source URL or description stored in the report")
    parser.add_argument("--out", type=Path, required=True,
                        help="JSON report path; never writes to the input file")
    parser.add_argument("--limit", type=int, default=100,
                        help="Category-balanced question count (default: 100)")
    parser.add_argument("--full", action="store_true", help="Use all questions")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--method", choices=("lexical", "hash"),
                        default="lexical")
    args = parser.parse_args(argv)
    report = run(load_samples(args.data), limit=None if args.full else args.limit,
                 seed=args.seed, top_k=args.top_k, method=args.method)
    report["data_sha256"] = hashlib.sha256(args.data.read_bytes()).hexdigest()
    report["data_source"] = args.data_source or str(args.data)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "rows"},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
