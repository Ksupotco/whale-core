"""Audit a three-way TimlMemBench targeted run without copying benchmark text."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path


CONDITIONS = ("baseline", "source", "governed")


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def last_by_sample(predictions: list[dict]) -> list[dict]:
    latest = {}
    for item in predictions:
        latest[item["sample_id"]] = item["adapter_metadata"]
    return list(latest.values())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tables", type=Path, required=True)
    parser.add_argument("--stage", choices=("pilot", "full"), default="full")
    parser.add_argument("--facts", type=Path, required=True)
    args = parser.parse_args()

    data = {}
    for condition in CONDITIONS:
        prefix = args.tables / f"timlmembench_targeted_{args.stage}_{condition}"
        predictions = read_jsonl(prefix.with_name(prefix.name + "_predictions.jsonl"))
        scores = read_jsonl(prefix.with_name(prefix.name + "_scores.jsonl"))
        if len(predictions) != len(scores):
            raise ValueError(f"prediction/score count mismatch: {condition}")
        if any(p["task_id"] != s["task_id"] or not s["judge_parse_ok"]
               for p, s in zip(predictions, scores)):
            raise ValueError(f"task/score alignment or judge parse failed: {condition}")
        data[condition] = (predictions, scores)
    ids = [[item["task_id"] for item in data[condition][0]]
           for condition in CONDITIONS]
    if ids[0] != ids[1] or ids[0] != ids[2]:
        raise ValueError("condition task IDs differ")
    facts = json.loads(args.facts.read_text(encoding="utf-8"))
    if not isinstance(facts, dict):
        raise ValueError("fact cassette must be an object")
    fact_hash = hashlib.sha256(args.facts.read_bytes()).hexdigest()

    conditions = {}
    for condition in CONDITIONS:
        predictions, scores = data[condition]
        final = predictions[-1]["adapter_metadata"]
        by_type = defaultdict(lambda: {"correct": 0, "total": 0})
        for score in scores:
            group = by_type[score["case_type"]]
            group["total"] += 1
            group["correct"] += bool(score["judge_correct"])
        samples = last_by_sample(predictions)
        applied = Counter()
        rejected = Counter()
        for meta in samples:
            applied.update(meta.get("operations_applied") or {})
            rejected.update(item["reason"] for item in meta.get("operation_rejections") or [])
        judge_usage = Counter()
        for score in scores:
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                judge_usage[key] += int((score.get("judge_usage") or {}).get(key) or 0)
        conditions[condition] = {
            "correct": sum(bool(s["judge_correct"]) for s in scores),
            "total": len(scores),
            "by_type": dict(by_type),
            "sample_count": len(samples),
            "extracted_facts": final.get("extracted_facts_cumulative"),
            "extraction_calls": final.get("extraction_calls_cumulative"),
            "fact_replay_hits": final.get("fact_replay_hits_cumulative"),
            "prediction_model_calls": final.get("model_calls_cumulative"),
            "prediction_usage": final.get("model_usage_cumulative"),
            "judge_usage": dict(judge_usage),
            "operations_applied": dict(applied),
            "rejection_reasons": dict(rejected),
        }
    if (conditions["source"]["fact_replay_hits"] != len(facts)
            or conditions["governed"]["fact_replay_hits"] != len(facts)):
        raise ValueError("not all frozen session facts were replayed")
    if len({conditions[c]["extracted_facts"] for c in CONDITIONS}) != 1:
        raise ValueError("extracted fact counts differ")

    task_rows = []
    transitions = {}
    for before, after in (("baseline", "source"), ("source", "governed")):
        old = data[before][1]
        new = data[after][1]
        transitions[f"{before}_to_{after}"] = {
            "wrong_to_right": sum(not a["judge_correct"] and b["judge_correct"]
                                  for a, b in zip(old, new)),
            "right_to_wrong": sum(a["judge_correct"] and not b["judge_correct"]
                                  for a, b in zip(old, new)),
        }
    for index, task_id in enumerate(ids[0]):
        task_rows.append({
            "task_id": task_id,
            "case_type": data["baseline"][1][index]["case_type"],
            **{c: bool(data[c][1][index]["judge_correct"]) for c in CONDITIONS},
        })
    report = {
        "stage": args.stage,
        "facts_sha256": fact_hash,
        "frozen_sessions": len(facts),
        "conditions": conditions,
        "transitions": transitions,
        "tasks": task_rows,
    }
    stem = args.tables / f"timlmembench_targeted_{args.stage}_summary"
    stem.with_suffix(".json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# TimlMemBench targeted {args.stage} paired audit", "",
             f"Frozen session extracts: {len(facts)}; SHA-256 `{fact_hash}`.", "",
             "| Condition | Overall | Write ops | Provenance | Extract calls | Replay hits | Model calls |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for c in CONDITIONS:
        row = conditions[c]
        write = row["by_type"].get("write_op_accuracy", {"correct": 0, "total": 0})
        provenance = row["by_type"].get("provenance_recoverability", {"correct": 0, "total": 0})
        lines.append(
            f"| {c} | {row['correct']}/{row['total']} | "
            f"{write['correct']}/{write['total']} | "
            f"{provenance['correct']}/{provenance['total']} | "
            f"{row['extraction_calls']} | {row['fact_replay_hits']} | "
            f"{row['prediction_model_calls']} |")
    lines += ["", "## Paired transitions", ""]
    for pair, row in transitions.items():
        lines.append(f"- {pair}: {row['wrong_to_right']} wrong→right, "
                     f"{row['right_to_wrong']} right→wrong.")
    lines += ["", "## Per-question judge decisions", "",
              "| Case | Type | Baseline | Source | Governed |",
              "| --- | --- | ---: | ---: | ---: |"]
    for row in task_rows:
        lines.append(f"| {row['task_id']} | {row['case_type']} | "
                     f"{int(row['baseline'])} | {int(row['source'])} | "
                     f"{int(row['governed'])} |")
    lines += ["", "These are same-model judge decisions on a targeted dev subset, "
              "not a full benchmark score or an ACL/security proof."]
    stem.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"conditions": {c: conditions[c]["correct"] for c in CONDITIONS},
                      "transitions": transitions,
                      "frozen_sessions": len(facts)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
