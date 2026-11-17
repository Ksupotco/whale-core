"""Compare paired TimlMemBench score files by task ID without benchmark text."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _scores(path: Path) -> dict[str, dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    if len(rows) != len({row["task_id"] for row in rows}):
        raise ValueError(f"duplicate task ID: {path}")
    if not all(row.get("judge_parse_ok") for row in rows):
        raise ValueError(f"unparsed judge result: {path}")
    return {row["task_id"]: row for row in rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    args = parser.parse_args()
    before, after = _scores(args.before), _scores(args.after)
    if not after or not set(after) <= set(before):
        raise ValueError("new task IDs do not match the previous run")
    for case_type in sorted({row["case_type"] for row in after.values()}):
        ids = [key for key, row in after.items() if row["case_type"] == case_type]
        old_pass = sum(bool(before[key]["pass"]) for key in ids)
        new_pass = sum(bool(after[key]["pass"]) for key in ids)
        print(f"{case_type}: {old_pass}/{len(ids)} -> {new_pass}/{len(ids)}")
    improved = [key for key in after if not before[key]["pass"] and after[key]["pass"]]
    regressed = [key for key in after if before[key]["pass"] and not after[key]["pass"]]
    print(json.dumps({"improved": improved, "regressed": regressed}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
