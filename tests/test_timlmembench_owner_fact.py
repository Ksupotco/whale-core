"""The benchmark bridge must use the product owner fact writer and reader."""

from evals.timlmembench_owner_fact import OwnerFactAdapter


class FakeClient:
    def __init__(self):
        self.calls = 0
        self.usage = {}

    def complete(self, messages, *, max_tokens):
        self.calls += 1
        if "用户消息原文" in messages[0]["content"]:
            return ('{"candidates":[{"kind":"fact",'
                    '"quote":"我养的猫叫年糕","predicate":"pet_name",'
                    '"value":"年糕"}]}')
        return "年糕"


def test_owner_fact_bridge_ignores_contact_and_retrieves_committed_fact():
    adapter = OwnerFactAdapter(FakeClient(), max_queries=1)
    try:
        adapter.handle({"request_type": "reset", "sample_id": "sample-1"})
        adapter.handle({"request_type": "ingest_session", "sample_id": "sample-1",
                        "session": {"session_id": "s1", "surface": "private_chat",
                                    "turns": [
                                        {"turn_id": "t1", "speaker_role": "contact",
                                         "text": "我的狗叫饼干",
                                         "timestamp": "2026-11-12T06:00:00+00:00"},
                                        {"turn_id": "t2", "speaker_role": "user",
                                         "text": "我养的猫叫年糕",
                                         "timestamp": "2026-11-12T06:00:01+00:00"},
                                    ]}})
        result = adapter.handle({"request_type": "query", "sample_id": "sample-1",
                                 "mode": "raw_native_cases", "query": "我的猫叫什么"})
        assert result["answer"] == "年糕"
        assert result["retrieved_texts"] == ["长期记忆（用户自述）：我养的猫叫年糕"]
        assert result["metadata"]["accepted_total"] == 1
        assert result["metadata"]["skipped_turns"] == 1
        hits = adapter.service.retrieve_hits(adapter.auth, "我的猫叫什么")
        assert len(hits) == 1 and hits[0].score > 0
        assert hits[0].fact.summary == "我养的猫叫年糕"
    finally:
        adapter.worker.stop(timeout=5)
