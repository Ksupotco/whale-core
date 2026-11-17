"""Post-run audit of benchmark states needing a contact memory writer.

Gold state and activation labels are read here for diagnosis only. They are
never passed to the product adapter, extractor, retriever, or answer model.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def rows(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def audit(root: Path, split: str) -> list[dict]:
    data = root / "data" / "timlmembench" / "zh" / "canonical"
    selected = json.loads((data / "splits.json").read_text(encoding="utf-8"))[split]
    sample_ids = {question.split("__q", 1)[0] for question in selected}
    speaker_roles = {}
    speakers_by_sample = defaultdict(lambda: {"user": set(), "contact": set()})
    for session in rows(data / "sessions.jsonl"):
        sample = session["session_id"].split("__s", 1)[0]
        if sample not in sample_ids:
            continue
        for turn in session.get("turns") or ():
            role = str(turn.get("speaker_role") or "")
            speaker = str(turn.get("speaker_id") or "")
            speaker_roles[str(turn.get("turn_id") or "")] = (role, speaker)
            if role in ("user", "contact"):
                speakers_by_sample[sample][role].add(speaker)
    states = {state["state_id"]: state
              for state in rows(data / "gold_state_ledger_v1.jsonl")
              if state["state_id"].split("__st", 1)[0] in sample_ids}
    output = []
    for case in rows(data / "activation_cases_v1.jsonl"):
        sample = case["sample_id"]
        if sample not in sample_ids:
            continue
        contact_ids = speakers_by_sample[sample]["contact"]
        blocked = []
        for state_id in case["should_activate"]:
            state = states.get(state_id)
            if state is None or state.get("entity") not in contact_ids:
                continue
            if any(speaker_roles.get(turn_id) == ("contact", state["entity"])
                   for turn_id in state.get("provenance_turn_ids") or ()):
                blocked.append(state_id)
        output.append({"case_id": case["case_id"],
                       "expected_states_needing_contact_writer": blocked,
                       "reason": "contact_private_chat_writer_unavailable" if blocked else ""})
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-root", type=Path, required=True)
    parser.add_argument("--split", choices=("train", "dev", "holdout"), required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.benchmark_root, args.split)
    print(json.dumps({"cases": len(result),
                      "cases_needing_contact_writer": sum(bool(row["reason"])
                                                          for row in result)},
                     ensure_ascii=False))
    if args.output:
        with args.output.open("w", encoding="utf-8") as stream:
            for item in result:
                stream.write(json.dumps(item, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
