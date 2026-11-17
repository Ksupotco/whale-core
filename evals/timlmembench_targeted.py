"""Run an official TimlMemBench raw-native subset through an adapter.

Selection uses public task type and case type, never expected answers or states.
The official runner's request, prediction writer, and sample split are reused.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path


def _official(path: Path):
    spec = importlib.util.spec_from_file_location("timlmembench_official_runner", path)
    if spec is None or spec.loader is None:
        raise ValueError("cannot load official runner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--official-runner", type=Path, required=True)
    parser.add_argument("--lang", choices=("zh", "en"), default="zh")
    parser.add_argument("--split", choices=("train", "dev", "holdout"), default="dev")
    parser.add_argument("--case-types", nargs="+", required=True)
    parser.add_argument("--case-family", choices=("governance", "activation"),
                        default="governance")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--failed-from", type=Path,
                        help="Retest only task IDs marked failed in a prior score JSONL")
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--adapter-command", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")

    official = _official(args.official_runner.resolve())
    data_root = official.benchmark_root()
    allowed = official.selected_ids(data_root, args.lang, args.split, "sample")
    all_cases = official.selected_cases(data_root, args.lang, args.case_family)
    cases = [(kind, case) for kind, case in all_cases
             if str(case["sample_id"]) in allowed
             and str(case.get("case_type")) in args.case_types]
    if args.failed_from:
        scores = [json.loads(line) for line in args.failed_from.open(encoding="utf-8")
                  if line.strip()]
        failed_ids = {str(score["task_id"]) for score in scores
                      if score.get("pass") is False}
        cases = [(kind, case) for kind, case in cases
                 if str(case.get("case_id")) in failed_ids]
        if len(cases) != len(failed_ids):
            raise ValueError("some failed task IDs are absent from selected official cases")
    if args.limit is not None:
        cases = cases[:args.limit]
    sessions = official.load_sessions(data_root, args.lang)
    adapter = official.JsonlAdapter(args.adapter_command)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    current_sample = None
    try:
        with args.output.open("w", encoding="utf-8") as handle:
            for task_type, case in cases:
                sample_id = str(case["sample_id"])
                if sample_id != current_sample:
                    adapter.call({"request_type": "reset", "sample_id": sample_id,
                                  "language": args.lang})
                    for session in sessions[sample_id]:
                        adapter.call({"request_type": "ingest_session",
                                      "sample_id": sample_id,
                                      "language": args.lang,
                                      "session": session})
                    current_sample = sample_id
                request = official.raw_case_request(
                    case, args.lang, args.baseline, task_type)
                started = time.perf_counter_ns()
                response = adapter.call(request)
                official.write_prediction(
                    handle, request=request, response=response,
                    elapsed_ms=(time.perf_counter_ns() - started) / 1_000_000)
    finally:
        adapter.close()
    print(f"wrote {len(cases)} official-format predictions: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
