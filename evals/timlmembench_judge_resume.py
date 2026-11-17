"""Resume TimlMemBench's official LLM judge from a scored JSONL prefix."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def agentscope_judge(client: Any, official: Any, prompt: str) -> dict[str, Any]:
    """Use the official prompt and parse contract with AgentScope's streaming API."""
    response, call = client.complete(
        [{"role": "system", "content": "Return only the requested JSON object."},
         {"role": "user", "content": prompt}], max_tokens=512)
    payload = official.extract_json_payload(response)
    correct = official.coerce_bool(payload.get("correct")) if payload else None
    if payload is None or correct is None:
        return {
            "judge_correct": None, "judge_parse_ok": False, "judge_score": 0.0,
            "judge_rationale": f"judge returned invalid JSON: {response[:300]}",
            "judge_failure_tags": ["judge_parse_error"],
            "judge_usage": call["usage"], "judge_latency_ms": call["latency_ms"],
        }
    return {
        "judge_correct": correct, "judge_parse_ok": True,
        "judge_score": payload.get("score"),
        "judge_rationale": str(payload.get("rationale") or ""),
        "judge_failure_tags": payload.get("failure_tags")
        if isinstance(payload.get("failure_tags"), list) else [],
        "judge_usage": call["usage"], "judge_latency_ms": call["latency_ms"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-root", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provider", choices=("openai", "agentscope"), default="openai")
    parser.add_argument("--model")
    parser.add_argument("--api-key-env")
    parser.add_argument("--max-judges", type=int, required=True)
    parser.add_argument("--limit", type=int, help="Score only this many leading predictions")
    args = parser.parse_args()
    if args.model is None:
        args.model = "deepseek-v4.1-flash-1017" if args.provider == "agentscope" else "gpt-4o"
    if args.api_key_env is None:
        args.api_key_env = "AGENTSCOPE_API_KEY" if args.provider == "agentscope" else "OPENAI_API_KEY"

    sys.path.insert(0, str(args.benchmark_root.resolve() / "code"))
    import score_baseline_predictions as official

    api_key = os.environ.get(args.api_key_env, "")
    if not api_key:
        raise ValueError(f"missing {args.api_key_env}")
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be positive")
    if args.limit is None:
        predictions = official.read_jsonl(args.predictions)
    else:
        predictions = []
        with args.predictions.open(encoding="utf-8") as source:
            for line in source:
                if line.strip():
                    predictions.append(json.loads(line))
                    if len(predictions) == args.limit:
                        break
        if len(predictions) != args.limit:
            raise ValueError("fewer saved predictions than --limit")
    completed = official.read_jsonl(args.output) if args.output.exists() else []
    if len(completed) > len(predictions):
        raise ValueError("more scores than predictions")
    for index, (prediction, score) in enumerate(zip(predictions, completed)):
        if (score.get("task_id") != prediction.get("task_id")
                or score.get("baseline") != prediction.get("baseline")
                or score.get("judge_model") != args.model):
            raise ValueError(f"saved score {index + 1} is not the expected prefix")
    remaining = len(predictions) - len(completed)
    if remaining == 0:
        print(f"already complete: {len(predictions)} scores", file=sys.stderr)
        return 0
    if remaining > args.max_judges:
        raise ValueError("remaining questions exceed --max-judges")

    gold = official.load_gold("zh")
    if args.provider == "agentscope":
        from timlmembench_deepseek import AgentScopeClient
        # Each logical judgment may need up to three transport attempts.
        client = AgentScopeClient(model=args.model, max_calls=remaining * 3,
                                  api_key_env=args.api_key_env)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("a", encoding="utf-8") as output:
        for prediction in predictions[len(completed):]:
            task_id = str(prediction["task_id"])
            entry = gold[task_id]
            kind = str(entry["kind"])
            record_gold = entry["record"]
            prompt = (official.build_canonical_judge_prompt(prediction, record_gold, "zh")
                      if kind == "canonical_qa"
                      else official.build_case_judge_prompt(prediction, record_gold, "zh"))
            if args.provider == "agentscope":
                score = agentscope_judge(client, official, prompt)
            else:
                score = official.llm_judge(
                    prompt=prompt, base_url="https://api.openai.com/v1",
                    api_key=api_key, model=args.model, retries=0, timeout=120)
            result: dict[str, Any] = {
                "schema_version": "timlmembench_score_v1",
                "baseline": prediction.get("baseline"),
                "language": "zh",
                "mode": prediction.get("mode"),
                "task_type": prediction.get("task_type") or kind,
                "task_id": task_id,
                "sample_id": prediction.get("sample_id"),
                "case_type": prediction.get("case_type") or record_gold.get("case_type"),
                "metrics": prediction.get("metrics") or record_gold.get("metrics") or [],
                "prediction_elapsed_ms": prediction.get("elapsed_ms"),
                "judge_mode": "llm",
                "judge_model": args.model,
                **score,
            }
            if kind == "canonical_qa":
                retrieval_ok = official.retrieval_correct(prediction, record_gold)
                result.update({
                    "answerable": not bool(record_gold.get("unanswerable")),
                    "retrieval_correct": retrieval_ok,
                    "combined_correct": bool(score.get("judge_correct") is True and retrieval_ok),
                    "pass": bool(score.get("judge_correct") is True and retrieval_ok),
                })
            else:
                result["pass"] = bool(score.get("judge_correct") is True)
            output.write(json.dumps(result, ensure_ascii=False) + "\n")
            output.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
