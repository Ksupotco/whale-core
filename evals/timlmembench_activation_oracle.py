"""Diagnose activation with TimlMemBench's current-state memories as input.

This isolates candidate selection. The ledger is evaluation data, never a
product retrieval source. No model or API key is required.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from whale_core.memory.activation import MemoryCandidate, activate
from whale_core.memory.rerank import rerank


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def evaluate(root: Path, *, sample_id: str = "", split: str = "") -> list[dict]:
    data = root / "data" / "timlmembench" / "zh" / "canonical"
    states = {row["state_id"]: row for row in read_jsonl(data / "gold_state_ledger_v1.jsonl")}
    allowed_samples = None
    if split:
        splits = json.loads((data / "splits.json").read_text(encoding="utf-8"))
        allowed_samples = {question.split("__q", 1)[0] for question in splits[split]}
    results = []
    for case in read_jsonl(data / "activation_cases_v1.jsonl"):
        if sample_id and case["sample_id"] != sample_id:
            continue
        if allowed_samples is not None and case["sample_id"] not in allowed_samples:
            continue
        candidates = []
        for rank, state_id in enumerate(case["available_memory_ids"], 1):
            state = states.get(state_id)
            if state is None or state["validity_state"] not in {"active", "preserved"}:
                continue
            candidates.append(MemoryCandidate(
                key=state_id, summary=state["proposition"], score=1 / rank,
                source="oracle_current_state", subject_id=state.get("entity") or "owner"))
        decisions = activate(case["current_query"],
                             rerank(case["current_query"], candidates),
                             is_group="群" in case["current_query"])
        selected = {decision.candidate.key for decision in decisions}
        expected = set(case["should_activate"])
        forbidden = set(case["should_not_activate"])
        available = {candidate.key for candidate in candidates}
        results.append({
            "case_id": case["case_id"],
            "expected_available": sorted(expected & available),
            "expected_unavailable": sorted(expected - available),
            "selected": [decision.candidate.key for decision in decisions],
            "missing": sorted((expected & available) - selected),
            "forbidden_selected": sorted(forbidden & selected),
            "exact": selected == expected,
        })
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-root", type=Path, required=True)
    parser.add_argument("--sample-id", default="")
    parser.add_argument("--split", choices=("train", "dev", "holdout"), default="")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rows = evaluate(args.benchmark_root, sample_id=args.sample_id, split=args.split)
    summary = {
        "cases": len(rows), "exact": sum(row["exact"] for row in rows),
        "eligible_cases": sum(not row["expected_unavailable"] for row in rows),
        "eligible_exact": sum(row["exact"] for row in rows
                              if not row["expected_unavailable"]),
        "expected_unavailable_cases": sum(bool(row["expected_unavailable"]) for row in rows),
        "missing_cases": sum(bool(row["missing"]) for row in rows),
        "forbidden_cases": sum(bool(row["forbidden_selected"]) for row in rows),
    }
    print(json.dumps(summary, ensure_ascii=False))
    if args.output:
        with args.output.open("w", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
