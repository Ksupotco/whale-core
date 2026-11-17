"""Paired Chinese LoCoMo QA on frozen text-turn retrieval from two code versions.

This measures the read path with identical curated input, answer model, and
judge. It does not claim to test automatic fact extraction or official LoCoMo.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "plugins")]

from evals.timlmembench_deepseek import AgentScopeClient, parse_object


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as out:
        out.write(json.dumps(row, ensure_ascii=False) + "\n")
        out.flush()


def prepared(data_path: Path, old_path: Path, new_path: Path):
    samples = {str(sample["sample_id"]): sample for sample in read_json(data_path)}
    old, new = read_json(old_path), read_json(new_path)
    actual_sha256 = hashlib.sha256(data_path.read_bytes()).hexdigest()
    if old["data_sha256"] != new["data_sha256"] or old["data_sha256"] != actual_sha256:
        raise ValueError("retrieval runs use different data")
    old_rows = {(row["sample_id"], row["question_index"]): row
                for row in old["rows"]}
    new_rows = {(row["sample_id"], row["question_index"]): row
                for row in new["rows"]}
    if old_rows.keys() != new_rows.keys() or len(old_rows) != 150:
        raise ValueError("retrieval runs have different question sets")
    ordered = list(old_rows)
    for sample_id, index in ordered:
        sample = samples[sample_id]
        qa = sample["qa"][index]
        if qa["question"] != old_rows[(sample_id, index)]["question"]:
            raise ValueError("question mismatch")
        turns = {str(turn["dia_id"]): (turn, str(sample["conversation"].get(
                    f"{key}_date_time") or ""))
                 for key, session in sample["conversation"].items()
                 if re.fullmatch(r"session_\d+", key)
                 for turn in session}
        for version, report in (("initial", old_rows), ("latest", new_rows)):
            row = report[(sample_id, index)]
            contexts = [f"[{turns[eid][1]}] {turns[eid][0]['speaker']}: "
                        f"{turns[eid][0]['text']}"
                        for eid in row["retrieved_evidence"] if eid in turns]
            yield {
                "id": f"{sample_id}:{index}:{version}",
                "sample_id": sample_id, "question_index": index,
                "category": int(qa["category"]), "version": version,
                "question": qa["question"], "reference": qa.get("answer"),
                "retrieved_ids": row["retrieved_evidence"], "contexts": contexts,
            }


def answer_prompt(item: dict) -> list[dict[str, str]]:
    context = "\n".join(f"{i}. {text[:700]}" for i, text in
                        enumerate(item["contexts"], 1))
    return [
        {"role": "system", "content":
         "请仅根据给定的过往对话记忆回答中文问题。答案尽量简短，指出具体事实；"
         "如果记忆不足以回答，回答‘证据不足’。不要编造，也不要把对话外知识当成记忆。"},
        {"role": "user", "content": f"过往对话记忆：\n{context}\n\n问题：{item['question']}"},
    ]


def judge_prompt(item: dict) -> list[dict[str, str]]:
    return [
        {"role": "system", "content":
         "你是中文长期对话记忆问答裁判。比较回答与参考答案的事实含义。"
         "同义表达算正确，但必须答全问题要求的项目；漏掉列表成员、时间、数量、"
         "关键动作或关系均判错。额外编造与问题有关的事实也判错。"
         "参考答案为 null 表示无答案，只有明确拒答、指出前提错误且不编造事实才算正确。"
         "参考答案若给出确定事实，单纯拒答判错，即使题目类别标为不可回答。"
         "只返回 JSON：{\"correct\":true或false,\"reason\":简短原因}。"},
        {"role": "user", "content":
         f"问题：{item['question']}\n参考答案："
         f"{json.dumps(item['reference'], ensure_ascii=False)}\n系统回答：{item['answer']}"},
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("answer", "judge"), required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--initial-retrieval", type=Path, required=True)
    parser.add_argument("--latest-retrieval", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--scores", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    items = list(prepared(args.data, args.initial_retrieval, args.latest_retrieval))
    if args.limit is not None:
        items = items[:args.limit]
    if args.stage == "answer":
        completed = {row["id"] for row in read_jsonl(args.predictions)}
        client = AgentScopeClient(model="deepseek-v4.1-flash-1017", max_calls=700)
        for item in items:
            if item["id"] in completed:
                continue
            response, usage = client.complete(answer_prompt(item), max_tokens=180)
            append(args.predictions, {**item, "answer": response.strip(), "usage": usage})
            completed.add(item["id"])
            print(f"answered {len(completed)}/{len(items)}", flush=True)
    else:
        predictions = read_jsonl(args.predictions)
        expected = {item["id"] for item in items}
        if {row["id"] for row in predictions} != expected:
            raise ValueError("predictions must cover the exact selected questions")
        completed = {row["id"] for row in read_jsonl(args.scores)}
        client = AgentScopeClient(model="deepseek-v4.1-flash-1017", max_calls=600)
        for item in predictions:
            if item["id"] in completed:
                continue
            response, usage = client.complete(judge_prompt(item), max_tokens=120)
            parsed = parse_object(response)
            if not isinstance(parsed.get("correct"), bool):
                raise ValueError(f"invalid judge result for {item['id']}")
            correct = parsed["correct"]
            reason = str(parsed.get("reason") or "")
            append(args.scores, {"id": item["id"], "sample_id": item["sample_id"],
                                 "question_index": item["question_index"],
                                 "category": item["category"], "version": item["version"],
                                 "correct": correct, "reason": reason, "usage": usage})
            completed.add(item["id"])
            print(f"judged {len(completed)}/{len(items)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
