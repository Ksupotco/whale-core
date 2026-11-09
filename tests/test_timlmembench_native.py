from evals.timlmembench_native import NativeMemoryAdapter
from whale_core.memory.store import MemoryKind


def _session(sample: str, session: str = "s1") -> dict:
    return {
        "request_type": "ingest_session",
        "sample_id": sample,
        "session": {
            "session_id": session,
            "surface": "private_chat",
            "turns": [
                {"turn_id": "t1", "speaker_role": "contact", "speaker_id": "A", "text": "旧门禁码是1234"},
                {"turn_id": "t2", "speaker_role": "user", "speaker_id": "B", "text": "收到"},
            ],
        },
    }


def _query(sample: str) -> dict:
    return {
        "request_type": "query", "mode": "raw_native_cases",
        "sample_id": sample, "query": "门禁码是什么？",
    }


def test_raw_turns_remain_non_curated_and_invisible_across_sessions():
    adapter = NativeMemoryAdapter()
    adapter.handle({"request_type": "reset", "sample_id": "one"})
    adapter.handle(_session("one"))
    records = adapter.store.list_by_kind(MemoryKind.EPISODE)
    assert len(records) == 2
    assert all("chat" in record.tags and "curated" not in record.tags for record in records)
    result = adapter.handle(_query("one"))
    assert result["answer"] == "<ABSTAIN>"
    assert result["retrieved_memory_ids"] == []
    assert result["retrieved_texts"] == []
    assert result["metadata"]["history_messages_visible"] == 0
    assert result["metadata"]["ingested_turns"] == 2


def test_reset_isolates_samples_and_rejects_mismatched_ids():
    adapter = NativeMemoryAdapter()
    adapter.handle({"request_type": "reset", "sample_id": "one"})
    adapter.handle(_session("one"))
    adapter.handle({"request_type": "reset", "sample_id": "two"})
    assert adapter.handle(_query("two"))["metadata"]["episode_count"] == 0
    try:
        adapter.handle(_session("one"))
    except ValueError as exc:
        assert "without reset" in str(exc)
    else:
        raise AssertionError("sample mismatch must fail")
