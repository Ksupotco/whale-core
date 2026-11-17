"""Resume an official TimlMemBench raw-native run from a saved JSONL prefix.

The first resumed sample is re-ingested because the adapter's in-memory store
is not persisted. Completed query predictions are verified and never rerun.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--adapter-command", required=True)
    parser.add_argument("--split", choices=("train", "dev", "holdout"), required=True)
    parser.add_argument("--sample-id", help="Run only this sample into a separate output")
    parser.add_argument("--baseline", required=True)
    args = parser.parse_args()

    sys.path.insert(0, str(args.benchmark_root.resolve() / "code"))
    import run_baseline_predictions as official

    data_root = args.benchmark_root.resolve() / "data" / "timlmembench"
    allowed = official.selected_ids(data_root, "zh", args.split, "sample")
    cases = [(kind, case) for kind, case in official.selected_cases(data_root, "zh", "all")
             if str(case["sample_id"]) in allowed]
    if args.sample_id:
        cases = [(kind, case) for kind, case in cases
                 if str(case["sample_id"]) == args.sample_id]
        if not cases:
            raise ValueError("sample ID is absent from selected split")
    sessions_by_sample = official.load_sessions(data_root, "zh")

    completed = []
    if args.output.exists():
        completed = official.read_jsonl(args.output)
    if len(completed) > len(cases):
        raise ValueError("output has more rows than the selected split")
    for index, row in enumerate(completed):
        expected = cases[index][1]
        if (row.get("task_id") != expected["case_id"]
                or row.get("sample_id") != expected["sample_id"]
                or row.get("baseline") != args.baseline):
            raise ValueError(f"saved row {index + 1} is not the expected prefix")
    if len(completed) == len(cases):
        print(f"already complete: {len(cases)} cases", file=sys.stderr)
        return 0

    adapter = official.JsonlAdapter(args.adapter_command)
    current_sample = None
    try:
        with args.output.open("a", encoding="utf-8") as output:
            for kind, case in cases[len(completed):]:
                sample_id = str(case["sample_id"])
                if sample_id != current_sample:
                    adapter.call({"request_type": "reset", "sample_id": sample_id,
                                  "language": "zh"})
                    for session in sessions_by_sample[sample_id]:
                        adapter.call({"request_type": "ingest_session",
                                      "sample_id": sample_id, "language": "zh",
                                      "session": session})
                    current_sample = sample_id
                request = official.raw_case_request(case, "zh", args.baseline, kind)
                started = time.perf_counter_ns()
                response = adapter.call(request)
                official.write_prediction(
                    output, request=request, response=response,
                    elapsed_ms=(time.perf_counter_ns() - started) / 1_000_000)
    finally:
        adapter.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
