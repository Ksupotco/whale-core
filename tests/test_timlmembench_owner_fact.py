"""The benchmark bridge must keep owner and contact facts separate."""

from evals.timlmembench_owner_fact import OwnerFactAdapter


class FakeClient:
    def __init__(self):
        self.calls = 0
        self.usage = {}

    def complete(self, messages, *, max_tokens):
        self.calls += 1
        if "消息原文" in messages[0]["content"]:
            return ('{"candidates":[{"kind":"fact",'
                    '"quote":"我养的猫叫年糕","predicate":"pet_name",'
                    '"value":"年糕"}]}')
        return "年糕"


class ContactClient(FakeClient):
    def complete(self, messages, *, max_tokens):
        self.calls += 1
        prompt = messages[0]["content"]
        if "消息原文" in prompt:
            if "我的狗叫饼干" in prompt:
                return ('{"candidates":[{"kind":"fact",'
                        '"quote":"我的狗叫饼干","predicate":"pet_name",'
                        '"value":"饼干"}]}')
            return '{"candidates":[]}'
        return "饼干"


def test_owner_fact_bridge_processes_contact_without_owner_pollution():
    adapter = OwnerFactAdapter(FakeClient(), max_queries=1)
    try:
        adapter.handle({"request_type": "reset", "sample_id": "sample-1"})
        adapter.handle({"request_type": "ingest_session", "sample_id": "sample-1",
                        "session": {"session_id": "s1", "surface": "private_chat",
                                    "turns": [
                                        {"turn_id": "t1", "speaker_role": "contact",
                                         "speaker_id": "A",
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
        assert result["metadata"]["skipped_turns"] == 0
        assert result["metadata"]["ingested_turn_ids"] == ["t1", "t2"]
        assert result["metadata"]["contact_writer"] == "contact_fact_v1_private_chat"
        hits = adapter.service.retrieve_hits(adapter.auth, "我的猫叫什么")
        assert len(hits) == 1 and hits[0].score > 0
        assert hits[0].fact.summary == "我养的猫叫年糕"
    finally:
        adapter.worker.stop(timeout=5)


def test_owner_fact_bridge_records_unsupported_surface_and_identity_conflict():
    adapter = OwnerFactAdapter(FakeClient(), max_queries=1)
    try:
        adapter.handle({"request_type": "reset", "sample_id": "sample-2"})
        adapter.handle({"request_type": "ingest_session", "sample_id": "sample-2",
                        "session": {"session_id": "s-group", "surface": "group_chat",
                                    "turns": [{"turn_id": "g1", "speaker_role": "user",
                                               "speaker_id": "B", "text": "我养的猫叫年糕"}]}})
        adapter.handle({"request_type": "ingest_session", "sample_id": "sample-2",
                        "session": {"session_id": "s-private", "surface": "private_chat",
                                    "turns": [
                                        {"turn_id": "p1", "speaker_role": "user",
                                         "speaker_id": "B", "text": "我养的猫叫年糕"},
                                        {"turn_id": "p2", "speaker_role": "user",
                                         "speaker_id": "C", "text": "我养的猫叫年糕"},
                                    ]}})
        result = adapter.handle({"request_type": "query", "sample_id": "sample-2",
                                 "mode": "raw_native_cases", "query": "我的猫叫什么"})
        meta = result["metadata"]
        assert meta["owner_speaker_id"] == "B"
        assert meta["ingested_turn_ids"] == ["p1"]
        assert meta["skipped_turn_ids_by_reason"]["unsupported_surface"] == ["g1"]
        assert meta["skipped_turn_ids_by_reason"]["owner_identity_conflict"] == ["p2"]
    finally:
        adapter.worker.stop(timeout=5)


def test_contact_fact_reaches_answer_context_with_contact_attribution():
    adapter = OwnerFactAdapter(ContactClient(), max_queries=1)
    try:
        adapter.handle({"request_type": "reset", "sample_id": "sample-contact"})
        adapter.handle({"request_type": "ingest_session",
                        "sample_id": "sample-contact",
                        "session": {"session_id": "s1", "surface": "private_chat",
                                    "turns": [{"turn_id": "t1", "speaker_role": "contact",
                                               "speaker_id": "A",
                                               "text": "我的狗叫饼干",
                                               "timestamp": "2026-11-12T06:00:00+00:00"}]}})
        result = adapter.handle({"request_type": "query",
                                 "sample_id": "sample-contact",
                                 "mode": "raw_native_cases",
                                 "query": "A的狗叫什么"})
        assert result["metadata"]["contact_facts_accepted"] == 1
        assert result["retrieved_texts"] == [
            "长期记忆（联系人A发言）：我的狗叫饼干"]
        assert result["metadata"]["accepted_total"] == 0
    finally:
        adapter.worker.stop(timeout=5)
