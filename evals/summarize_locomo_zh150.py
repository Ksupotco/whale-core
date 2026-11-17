"""Summarize the paired Chinese LoCoMo 150-question experiment."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--initial-retrieval", type=Path, required=True)
    parser.add_argument("--latest-retrieval", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--scores", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    retrieval = {name: json.loads(path.read_text(encoding="utf-8")) for name, path in
                 (("initial", args.initial_retrieval), ("latest", args.latest_retrieval))}
    predictions = jsonl(args.predictions)
    scores = jsonl(args.scores)
    if len(predictions) != 300 or len(scores) != 300:
        raise ValueError("expected 300 answers and scores")
    pred = {row["id"]: row for row in predictions}
    score = {row["id"]: row for row in scores}
    if len(pred) != 300 or len(score) != 300 or pred.keys() != score.keys():
        raise ValueError("duplicate or missing IDs")
    qa: dict[tuple[str, int], dict[str, dict]] = defaultdict(dict)
    by_category: dict[str, Counter] = defaultdict(Counter)
    for row in scores:
        key = (row["sample_id"], row["question_index"])
        qa[key][row["version"]] = row
        by_category[row["version"]][row["category"]] += int(row["correct"])
    if len(qa) != 150 or any(set(pair) != {"initial", "latest"} for pair in qa.values()):
        raise ValueError("questions are not paired")
    categories = Counter(pred[f"{sid}:{idx}:initial"]["category"] for sid, idx in qa)
    paired = Counter((pair["initial"]["correct"], pair["latest"]["correct"])
                     for pair in qa.values())
    paired_by_category = {}
    for category in sorted(categories):
        subset = [pair for key, pair in qa.items()
                  if pred[f"{key[0]}:{key[1]}:initial"]["category"] == category]
        outcomes = Counter((pair["initial"]["correct"], pair["latest"]["correct"])
                           for pair in subset)
        paired_by_category[str(category)] = {
            "initial_only": outcomes[(True, False)],
            "latest_only": outcomes[(False, True)],
            "both_correct": outcomes[(True, True)],
            "both_wrong": outcomes[(False, False)],
        }
    output = {
        "questions": len(qa), "answers": len(pred), "category_counts": dict(sorted(categories.items())),
        "qa": {name: {"correct": sum(by_category[name].values()),
                       "accuracy": sum(by_category[name].values()) / 150,
                       "by_category": {str(cat): {"correct": by_category[name][cat],
                                                   "total": categories[cat],
                                                   "accuracy": by_category[name][cat] / categories[cat]}
                                       for cat in sorted(categories)}}
               for name in ("initial", "latest")},
        "paired": {"both_correct": paired[(True, True)],
                   "initial_only": paired[(True, False)],
                   "latest_only": paired[(False, True)],
                   "both_wrong": paired[(False, False)]},
        "paired_by_category": paired_by_category,
    }
    retrieved = {name: {(row["sample_id"], row["question_index"]): row
                        for row in report["rows"]}
                 for name, report in retrieval.items()}
    output["retrieval"] = {}
    for name in ("initial", "latest"):
        scoreable = []
        for key in qa:
            item = pred[f"{key[0]}:{key[1]}:{name}"]
            gold = set(retrieved[name][key]["gold_evidence"])
            if item["category"] == 5 or not gold:
                continue
            found = set(item["retrieved_ids"]) & gold
            scoreable.append((bool(found), found == gold, len(found) / len(gold)))
        output["retrieval"][name] = {
            "scored_questions": len(scoreable),
            "any_hit": sum(row[0] for row in scoreable),
            "all_hit": sum(row[1] for row in scoreable),
            "any_hit_rate": sum(row[0] for row in scoreable) / len(scoreable),
            "all_hit_rate": sum(row[1] for row in scoreable) / len(scoreable),
            "mean_evidence_recall": sum(row[2] for row in scoreable) / len(scoreable),
        }
    examples = {"latest_only": [], "initial_only": []}
    for key, pair in qa.items():
        outcome = (pair["initial"]["correct"], pair["latest"]["correct"])
        label = "latest_only" if outcome == (False, True) else (
            "initial_only" if outcome == (True, False) else None)
        if label is None or len(examples[label]) >= 5:
            continue
        old = pred[f"{key[0]}:{key[1]}:initial"]
        new = pred[f"{key[0]}:{key[1]}:latest"]
        examples[label].append({
            "id": f"{key[0]}:{key[1]}", "category": old["category"],
            "question": old["question"], "reference": old["reference"],
            "initial_answer": old["answer"], "latest_answer": new["answer"],
            "initial_evidence": old["retrieved_ids"],
            "latest_evidence": new["retrieved_ids"],
            "gold_evidence": retrieved["latest"][key]["gold_evidence"],
        })
    output["examples"] = examples
    for name in ("initial", "latest"):
        for stage, rows in (("answer", predictions), ("judge", scores)):
            usage = Counter()
            for row in rows:
                if row["version"] != name:
                    continue
                stats = row.get("usage") or {}
                if isinstance(stats, dict):
                    for key, value in stats.items():
                        if isinstance(value, (int, float)):
                            usage[key] += value
            output.setdefault("token_usage", {}).setdefault(name, {})[stage] = dict(usage)
    rendered = json.dumps(output, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
