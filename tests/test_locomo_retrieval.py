"""Small offline fixtures for the LoCoMo retrieval protocol."""
from __future__ import annotations

from evals.locomo_retrieval import run, select_questions


def _sample(sample_id: str):
    return {
        "sample_id": sample_id,
        "conversation": {
            "speaker_a": "Ada", "speaker_b": "Ben",
            "session_1_date_time": "2024-01-01",
            "session_1": [
                {"speaker": "Ada", "dia_id": "D1:1",
                 "text": "I enjoy astronomy and watching meteor showers."},
                {"speaker": "Ben", "dia_id": "D1:2",
                 "text": "I collect antique maps."},
            ],
        },
        "qa": [
            {"question": "Who enjoys astronomy?", "answer": "Ada",
             "evidence": ["D1:1"], "category": 4},
            {"question": "What is Ada's favorite planet?", "answer": "unknown",
             "evidence": ["D1:1"], "category": 5},
        ],
    }


def test_retrieval_reports_evidence_without_scoring_unanswerable():
    report = run([_sample("one")], limit=None, top_k=1)
    assert report["selected_questions"] == 2
    assert report["scored_questions"] == 1
    assert report["unanswerable_questions"] == 1
    assert report["any_hit_rate"] == 1.0
    assert report["rows"][1]["any_hit"] is None
    assert report["rows"][0]["retrieved_evidence"] == ["D1:1"]
    assert "answer" not in report["rows"][0]


def test_sample_is_seeded_and_stores_are_isolated():
    samples = [_sample("one"), _sample("two")]
    first = [(s["sample_id"], i) for s, i, _ in
             select_questions(samples, limit=3, seed=17)]
    again = [(s["sample_id"], i) for s, i, _ in
             select_questions(samples, limit=3, seed=17)]
    assert first == again
    report = run(samples, limit=None, top_k=1)
    assert report["selected_questions"] == 4
    assert report["scored_questions"] == 2
    assert all(row["retrieved_evidence"] == ["D1:1"]
               for row in report["rows"] if row["category"] == "single_hop")
