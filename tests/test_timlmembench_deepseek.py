import json

from evals.timlmembench_deepseek import AgentScopeClient, DeepSeekMemoryAdapter, parse_object
from whale_core.memory.store import MemoryKind
from whale_core.memory.hybrid import HybridCurator


def test_extraction_requires_matching_source_turn_and_uses_native_recall(monkeypatch):
    monkeypatch.setenv("AGENTSCOPE_API_KEY", "test-only")

    def fake_complete(self, messages, *, max_tokens):
        self.calls += 1
        if max_tokens == 512:
            return json.dumps({"facts": [
                {"turn_id": "t1", "speaker_id": "A", "fact": "喜欢绿茶"},
                {"turn_id": "t1", "speaker_id": "B", "fact": "喜欢咖啡"},
                {"turn_id": "missing", "speaker_id": "A", "fact": "喜欢红茶"},
            ]}), {}
        return "喜欢绿茶", {"calls": 1, "usage": {"total_tokens": 10}}

    monkeypatch.setattr(AgentScopeClient, "complete", fake_complete)
    adapter = DeepSeekMemoryAdapter(max_queries=1, max_calls=2)
    adapter.handle({"request_type": "reset", "sample_id": "s"})
    adapter.handle({"request_type": "ingest_session", "sample_id": "s", "session": {
        "session_id": "session1", "turns": [
            {"turn_id": "t1", "speaker_id": "A", "text": "我喜欢绿茶"},
        ]}})
    result = adapter.handle({"request_type": "query", "sample_id": "s",
                             "mode": "raw_native_cases", "query": "A 喜欢什么茶？"})
    assert result["answer"] == "喜欢绿茶"
    assert len(adapter.store.list_by_kind(MemoryKind.EPISODE)) == 1
    assert len(adapter.store.list_by_kind(MemoryKind.PREFERENCE)) == 1
    assert result["metadata"]["curated_count"] == 1
    assert result["retrieved_memory_ids"]


def test_no_memory_control_skips_extraction(monkeypatch):
    monkeypatch.setenv("AGENTSCOPE_API_KEY", "test-only")
    monkeypatch.setattr(AgentScopeClient, "complete",
                        lambda self, messages, *, max_tokens: ("证据不足", {"calls": 1}))
    adapter = DeepSeekMemoryAdapter(max_queries=1, max_calls=1, no_memory=True)
    adapter.handle({"request_type": "reset", "sample_id": "s"})
    adapter.handle({"request_type": "ingest_session", "sample_id": "s", "session": {
        "session_id": "session1", "turns": [
            {"turn_id": "t1", "speaker_id": "A", "text": "我喜欢绿茶"},
        ]}})
    result = adapter.handle({"request_type": "query", "sample_id": "s",
                             "mode": "raw_native_cases", "query": "A 喜欢什么茶？"})
    assert result["metadata"]["curated_count"] == 0
    assert result["metadata"]["no_memory_control"] is True
    assert not result["retrieved_memory_ids"]


def test_expanded_extraction_keeps_twelve_grounded_facts(monkeypatch):
    monkeypatch.setenv("AGENTSCOPE_API_KEY", "test-only")
    budgets = []

    def fake_complete(self, messages, *, max_tokens):
        budgets.append(max_tokens)
        return json.dumps({"facts": [
            {"turn_id": f"t{i}", "speaker_id": "A", "fact": f"喜欢话题{i}"}
            for i in range(12)
        ]}, ensure_ascii=False), {}

    monkeypatch.setattr(AgentScopeClient, "complete", fake_complete)
    adapter = DeepSeekMemoryAdapter(max_queries=1, max_calls=1,
                                    max_facts_per_session=12)
    adapter.handle({"request_type": "reset", "sample_id": "s"})
    adapter.handle({"request_type": "ingest_session", "sample_id": "s", "session": {
        "session_id": "one", "turns": [
            {"turn_id": f"t{i}", "speaker_id": "A", "text": f"我喜欢话题{i}"}
            for i in range(12)
        ]}})
    assert budgets == [1024]
    assert len(adapter.recorded_facts["s/one"]) == 12
    assert len(adapter.store.list_by_kind(MemoryKind.PREFERENCE)) == 12


def test_parse_object_accepts_fenced_json():
    assert parse_object('```json\n{"facts": []}\n```') == {"facts": []}


def test_cloud_embedding_is_opt_in_and_uses_configured_provider(monkeypatch):
    monkeypatch.setenv("AGENTSCOPE_API_KEY", "test-only")
    monkeypatch.setenv("WHALE_EMBEDDING_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("WHALE_EMBEDDING_API_KEY", "test-only")
    monkeypatch.setenv("WHALE_EMBEDDING_MODEL", "test-embedding")
    adapter = DeepSeekMemoryAdapter(max_queries=1, max_calls=1)
    assert isinstance(adapter.curator, HybridCurator)
    assert adapter.curator.embedder.model == "test-embedding"
    assert adapter.companion.curator is adapter.curator


def test_benchmark_fact_time_uses_source_turn_timestamp(monkeypatch):
    monkeypatch.setenv("AGENTSCOPE_API_KEY", "test-only")
    cassette = {"sample/session": [{"turn_id": "t1", "speaker_id": "A",
                                    "fact": "下周起包裹放楼下柜机"}]}
    adapter = DeepSeekMemoryAdapter(max_queries=1, max_calls=1,
                                    fact_cassette=cassette)
    adapter.handle({"request_type": "reset", "sample_id": "sample"})
    adapter.handle({"request_type": "ingest_session", "sample_id": "sample",
                    "session": {"session_id": "session", "turns": [{
                        "turn_id": "t1", "speaker_id": "A",
                        "timestamp": "2026-11-15T20:00:00+08:00",
                        "text": "下周起包裹放楼下柜机"}]}})
    record = adapter.store.list_by_kind(MemoryKind.PREFERENCE)[0]
    assert record.content["valid_from"] == "2026-11-15T16:00:00+00:00"


def test_agentscope_stream_retries_transport_failure_with_call_cap(monkeypatch):
    import requests

    monkeypatch.setenv("AGENTSCOPE_API_KEY", "test-only")
    client = AgentScopeClient(model="test-model", max_calls=2)
    attempts = 0

    class Response:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def iter_lines(self, **_kwargs):
            yield 'data: {"choices":[{"delta":{"content":"好"}}]}'
            yield 'data: {"usage":{"prompt_tokens":3,"completion_tokens":1}}'
            yield "data: [DONE]"

    def post(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise requests.ConnectionError("temporary disconnect")
        return Response()

    monkeypatch.setattr(client.http, "post", post)
    monkeypatch.setattr("evals.timlmembench_deepseek.time.sleep", lambda _n: None)
    answer, meta = client.complete([{"role": "user", "content": "hi"}], max_tokens=8)
    assert answer == "好"
    assert meta["calls"] == client.calls == 2
    assert client.usage["prompt_tokens"] == 3
