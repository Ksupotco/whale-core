"""Offline end-to-end checks for benchmark governance through whale-core."""
import json

from evals.timlmembench_deepseek import AgentScopeClient
from evals.timlmembench_governed import GovernedDeepSeekMemoryAdapter
from whale_core.memory.facts import FactService
from whale_core.memory.store import MemoryKind


def _session(session_id, surface, turns):
    return {"request_type": "ingest_session", "sample_id": "sample",
            "session": {"session_id": session_id, "surface": surface,
                        "turns": [{"turn_id": tid, "speaker_id": speaker, "text": text}
                                  for tid, speaker, text in turns]}}


def _event(op, turn_id, speaker, quote, fact="", target_turn_id=""):
    return {"op": op, "turn_id": turn_id, "speaker_id": speaker,
            "quote": quote, "fact": fact, "target_turn_id": target_turn_id}


def _adapter(monkeypatch, batches, *, apply_governance=True, system_log=None,
             source_context_enabled=True, decisions_by_batch=None):
    monkeypatch.setenv("AGENTSCOPE_API_KEY", "test-only")
    facts = iter([[{"turn_id": event["turn_id"],
                    "speaker_id": event["speaker_id"], "fact": event["fact"]}
                   for event in batch if event["op"] == "remember"]
                  for batch in batches])
    operations = iter([[event for event in batch if event["op"] != "remember"]
                       for batch in batches])
    decisions = iter(decisions_by_batch or [[] for _ in batches])
    current_facts = []

    def fake_complete(self, messages, *, max_tokens):
        nonlocal current_facts
        self.calls += 1
        if max_tokens == 512:
            current_facts = next(facts)
            return json.dumps({"facts": current_facts}, ensure_ascii=False), {}
        if max_tokens == 1280:
            return json.dumps({"events": next(operations),
                               "decisions": next(decisions),
                               "candidate_decisions": [
                                   {"candidate_id": index, "action": "add"}
                                   for index in range(len(current_facts))]},
                              ensure_ascii=False), {}
        # The answer stage sees only memories passed through WhaleCompanion.
        system = messages[0]["content"]
        if system_log is not None:
            system_log.append(system)
        return ("红茶" if "现在喜欢红茶" in system else "证据不足"), {"usage": {}}

    monkeypatch.setattr(AgentScopeClient, "complete", fake_complete)
    adapter = GovernedDeepSeekMemoryAdapter(
        max_queries=5, max_calls=15, apply_governance=apply_governance,
        source_context_enabled=source_context_enabled)
    adapter.handle({"request_type": "reset", "sample_id": "sample"})
    return adapter


def _active(adapter):
    return [rec for kind in (MemoryKind.EPISODE, MemoryKind.PREFERENCE)
            for rec in adapter.store.list_by_kind(kind)
            if ("curated" in rec.tags and "superseded" not in rec.tags
                and rec.content.get("status", "active") == "active")]


def test_revoke_supersede_and_preserve_change_real_memory_state(monkeypatch):
    adapter = _adapter(monkeypatch, [
        [_event("remember", "t1", "A", "我喜欢绿茶", "喜欢绿茶"),
         _event("remember", "t2", "B", "我喜欢咖啡", "喜欢咖啡")],
        [_event("supersede", "t3", "A", "现在改喝红茶", "现在喜欢红茶", "t1")],
        [_event("revoke", "t4", "B", "请忘掉咖啡那条", target_turn_id="t2")],
        [_event("preserve", "t5", "A", "红茶偏好继续保留", target_turn_id="t3")],
    ])
    adapter.handle(_session("s1", "private_chat", [
        ("t1", "A", "我喜欢绿茶"), ("t2", "B", "我喜欢咖啡")]))
    adapter.handle(_session("s2", "private_chat", [
        ("t3", "A", "现在改喝红茶")]))
    adapter.handle(_session("s3", "private_chat", [
        ("t4", "B", "请忘掉咖啡那条")]))
    adapter.handle(_session("s4", "private_chat", [
        ("t5", "A", "红茶偏好继续保留")]))

    active = _active(adapter)
    assert len(active) == 1
    assert active[0].content["summary"] == "[A] 现在喜欢红茶"
    assert any("superseded" in rec.tags for rec in adapter.store.list_by_kind(MemoryKind.PREFERENCE))
    result = adapter.handle({"request_type": "query", "mode": "raw_native_cases",
                             "sample_id": "sample", "query": "A 喜欢什么茶？"})
    assert result["answer"] == "红茶"
    assert result["metadata"]["operations_applied"] == {
        "revoke": 1, "supersede": 1, "preserve": 1}
    assert result["metadata"]["curated_count"] == 1
    assert all("绿茶" not in text and "咖啡" not in text
               for text in result["retrieved_texts"])
    assert all("target_summary" not in event
               for event in result["metadata"]["operation_trace"])
    assert all("reason" in event for event in result["metadata"]["operation_rejections"])


def test_repeated_fact_uses_shared_fact_service_and_later_source_can_revoke(monkeypatch):
    adapter = _adapter(monkeypatch, [
        [_event("remember", "t1", "A", "我喜欢绿茶", "喜欢绿茶")],
        [_event("remember", "t2", "A", "我还是喜欢绿茶", "喜欢绿茶")],
        [_event("revoke", "t3", "A", "以后别记绿茶偏好",
                target_turn_id="t2")],
    ])
    adapter.handle(_session("s1", "private_chat", [("t1", "A", "我喜欢绿茶")]))
    adapter.handle(_session("s2", "private_chat", [("t2", "A", "我还是喜欢绿茶")]))
    records = _active(adapter)
    assert len(records) == 1
    assert records[0].content["evidence_ids"] == ["t1", "t2"]
    adapter.handle(_session("s3", "private_chat", [("t3", "A", "以后别记绿茶偏好")]))
    assert _active(adapter) == []
    assert adapter.operations_applied["revoke"] == 1
    assert adapter.operation_trace[-1]["target_evidence_ids"] == ["t1", "t2"]


def test_write_question_sees_add_preserve_and_unpromoted_turns(monkeypatch):
    prompts = []
    adapter = _adapter(monkeypatch, [
        [_event("remember", "sample__s001_t001", "A", "我喜欢绿茶", "喜欢绿茶"),
         _event("remember", "sample__s001_t002", "A", "我仍喜欢绿茶", "喜欢绿茶")],
    ], system_log=prompts, decisions_by_batch=[[
        {"turn_id": "sample__s001_t003", "speaker_id": "A",
         "action": "ephemeral", "quote": "今天先放在这里"},
    ]])
    adapter.handle(_session("s1", "private_chat", [
        ("sample__s001_t001", "A", "我喜欢绿茶"),
        ("sample__s001_t002", "A", "我仍喜欢绿茶"),
        ("sample__s001_t003", "A", "今天先放在这里"),
    ]))
    adapter.write_trace.clear()  # write answers must come from committed decisions
    adapter.handle({"request_type": "query", "mode": "raw_native_cases",
                    "sample_id": "sample", "case_type": "write_op_accuracy",
                    "query": "Session 1 中哪些事实写入了？"})
    assert "事实写入 add: Session 1" in prompts[-1]
    assert "事实写入 preserve: Session 1" in prompts[-1]
    assert "发言者 A; 原话：我喜欢绿茶" in prompts[-1]
    assert "未写入判定候选 ephemeral: Session 1" in prompts[-1]
    assert "今天先放在这里" in prompts[-1]


def test_wrong_speaker_quote_and_cross_space_operations_are_rejected(monkeypatch):
    adapter = _adapter(monkeypatch, [
        [_event("remember", "t1", "A", "我的安排是周三", "安排在周三")],
        [_event("revoke", "t2", "B", "把 A 的安排删掉", target_turn_id="t1")],
        [_event("revoke", "t3", "A", "把旧安排删掉", target_turn_id="t1")],
        [_event("revoke", "t4", "A", "编造的引文", target_turn_id="t1")],
    ])
    adapter.handle(_session("s1", "private_chat", [("t1", "A", "我的安排是周三")]))
    adapter.handle(_session("s2", "private_chat", [("t2", "B", "把 A 的安排删掉")]))
    adapter.handle(_session("s3", "work_collaboration", [("t3", "A", "把旧安排删掉")]))
    adapter.handle(_session("s4", "private_chat", [("t4", "A", "并未提到旧安排")]))
    assert len(_active(adapter)) == 1
    assert adapter.operations_rejected == 3
    assert {item["reason"] for item in adapter.operation_rejections} == {
        "wrong_speaker_target", "cross_space_target", "source_evidence_mismatch"}
    result = adapter.handle({"request_type": "query", "mode": "raw_native_cases",
                             "sample_id": "sample", "query": "安排在什么时候？",
                             "allowed_spaces": ["timlmembench:work_collaboration"]})
    assert result["retrieved_memory_ids"] == []
    assert result["retrieved_texts"] == []
    assert result["metadata"]["audience_acl_available"] is True
    allowed = adapter.handle({"request_type": "query", "mode": "raw_native_cases",
                              "sample_id": "sample", "query": "安排在什么时候？",
                              "allowed_spaces": ["timlmembench:private_chat"]})
    assert allowed["retrieved_memory_ids"]


def test_official_query_without_audience_cannot_claim_third_party_acl(monkeypatch):
    adapter = _adapter(monkeypatch, [
        [_event("remember", "t1", "A", "A 喜欢绿茶", "A 喜欢绿茶")],
        [_event("remember", "t2", "B", "B 喜欢红茶", "B 喜欢红茶")],
    ], apply_governance=False)
    adapter.handle(_session("s1", "private_chat", [("t1", "A", "A 喜欢绿茶")]))
    adapter.handle(_session("s2", "work_collaboration", [("t2", "B", "B 喜欢红茶")]))
    result = adapter.handle({"request_type": "query", "mode": "raw_native_cases",
                             "sample_id": "sample", "query": "喜欢什么茶？"})
    assert result["metadata"]["audience_acl_available"] is False
    assert result["metadata"]["scope_mode"] == "all_surfaces_no_audience_acl"
    assert len(result["retrieved_memory_ids"]) == 2


def test_provenance_prompt_uses_current_authorized_anchors_without_deleted_text(monkeypatch):
    prompts = []
    adapter = _adapter(monkeypatch, [
        [_event("remember", "s001_t001", "A", "旧绿茶偏好", "喜欢绿茶")],
        [_event("supersede", "s002_t003", "A", "现在改喝红茶",
                "现在喜欢红茶", "s001_t001")],
        [_event("remember", "s003_t001", "B", "不公开地址", "住在旧址")],
        [_event("revoke", "s004_t002", "B", "别再记我的地址",
                target_turn_id="s003_t001")],
    ], system_log=prompts)
    adapter.handle(_session("s1", "private_chat", [
        ("s001_t001", "A", "旧绿茶偏好")]))
    adapter.handle(_session("s2", "private_chat", [
        ("s002_t003", "A", "现在改喝红茶")]))
    adapter.handle(_session("s3", "work_collaboration", [
        ("s003_t001", "B", "不公开地址")]))
    adapter.handle(_session("s4", "work_collaboration", [
        ("s004_t002", "B", "别再记我的地址")]))

    result = adapter.handle({"request_type": "query", "mode": "raw_native_cases",
                             "sample_id": "sample", "case_type": "provenance_recoverability",
                             "query": "A 现在喜欢什么茶？来源是哪次？",
                             "allowed_spaces": ["timlmembench:private_chat"]})
    assert result["answer"] == "红茶"
    assert "Session 2, turn s002_t003" in prompts[-1]
    assert "s004_t002" not in prompts[-1]
    assert "旧绿茶偏好" not in prompts[-1]
    assert "喜欢绿茶" not in prompts[-1]
    assert "住在旧址" not in prompts[-1]

    adapter.handle({"request_type": "query", "mode": "raw_native_cases",
                    "sample_id": "sample", "case_type": "activation_appropriateness",
                    "query": "A 喜欢什么茶？",
                    "allowed_spaces": ["timlmembench:private_chat"]})
    assert "可核验的当前记忆来源" not in prompts[-1]


def test_revoked_fact_keeps_source_anchor_without_reviving_old_text(monkeypatch):
    prompts = []
    adapter = _adapter(monkeypatch, [
        [_event("remember", "s001_t001", "A", "我喜欢绿茶", "喜欢绿茶")],
        [_event("revoke", "s002_t001", "A", "别再记我的绿茶偏好",
                target_turn_id="s001_t001")],
    ], system_log=prompts)
    adapter.handle(_session("s1", "private_chat", [("s001_t001", "A", "我喜欢绿茶")]))
    adapter.handle(_session("s2", "private_chat", [
        ("s002_t001", "A", "别再记我的绿茶偏好")]))
    result = adapter.handle({"request_type": "query", "mode": "raw_native_cases",
                             "sample_id": "sample", "case_type": "provenance_recoverability",
                             "query": "A 的绿茶偏好为什么被撤销？",
                             "allowed_spaces": ["timlmembench:private_chat"]})
    system = prompts[-1]
    assert any("已撤销" in item for item in result["retrieved_texts"])
    assert any("历史版本：revoked" in item for item in result["retrieved_texts"])
    assert "事实版本 revoked; 来源 Session 1, turn s001_t001" in system
    assert "已执行 revoke: Session 2, turn s002_t001" in system
    assert "历史版本：revoked" in system
    assert "原话：我喜欢绿茶" not in system


def test_provenance_can_find_relevant_revoke_after_target_record_is_gone(monkeypatch):
    adapter = _adapter(monkeypatch, [])
    space = "timlmembench:private_chat"
    adapter.source_turns = {
        "s001_t001": (space, "旧密码只用于取包", "A"),
        "s003_t001": (space, "旧密码已作废，改走前台", "A"),
        "s004_t001": (space, "下周点餐先问忌口", "A"),
    }
    adapter.operation_trace = [
        {"op": "revoke", "source_turn_id": "s003_t001",
         "target_key": "deleted-old-record", "new_key": "",
         "target_evidence_ids": ["s001_t001"], "space_id": space},
        {"op": "supersede", "source_turn_id": "s004_t001",
         "target_key": "deleted-food-record", "new_key": "",
         "target_evidence_ids": [], "space_id": space},
    ]
    context = adapter._source_context(
        "旧密码为什么不能用了？", [space], "provenance_recoverability")
    assert "已执行 revoke: Session 3, turn s003_t001" in context
    assert "目标旧版本来源 Session 1, turn s001_t001" in context
    assert "s004_t001" not in context


def test_governance_accepts_only_same_speaker_support_turns(monkeypatch):
    prompts = []
    event = _event("revoke", "s002_t001", "A", "旧安排别再用",
                   target_turn_id="s001_t001")
    event["support_turn_ids"] = ["s002_t002", "s002_t003", "s003_t001"]
    adapter = _adapter(monkeypatch, [
        [_event("remember", "s001_t001", "A", "旧安排有效", "旧安排有效")],
        [event],
    ], system_log=prompts)
    adapter.handle(_session("s1", "private_chat", [
        ("s001_t001", "A", "旧安排有效")]))
    adapter.handle(_session("s2", "private_chat", [
        ("s002_t001", "A", "旧安排别再用"),
        ("s002_t002", "B", "是因为下雨吗"),
        ("s002_t003", "A", "改走新入口"),
    ]))
    assert adapter.operation_trace[-1]["support_turn_ids"] == ["s002_t003"]
    decision = FactService(adapter.store).decisions_for_evidence(
        ("s002_t001",), allowed_spaces=("timlmembench:private_chat",))[-1]
    assert decision["support_evidence_ids"] == ["s002_t003"]
    adapter.handle({"request_type": "query", "mode": "raw_native_cases",
                    "sample_id": "sample", "case_type": "provenance_recoverability",
                    "query": "旧安排为什么不能用？",
                    "allowed_spaces": ["timlmembench:private_chat"]})
    assert "同一变更的补充证据 Session 2, turn s002_t003" in prompts[-1]
    assert "s002_t002" not in prompts[-1]


def test_write_operation_answer_excludes_unrelated_ordinary_recall(monkeypatch):
    prompts = []
    adapter = _adapter(monkeypatch, [
        [_event("remember", "sample__s001_t001", "A", "我喜欢绿茶", "喜欢绿茶")],
        [_event("remember", "sample__s004_t001", "A", "我喜欢科幻", "喜欢科幻")],
    ], system_log=prompts)
    adapter.handle(_session("s1", "private_chat", [
        ("sample__s001_t001", "A", "我喜欢绿茶")]))
    adapter.handle(_session("s4", "private_chat", [
        ("sample__s004_t001", "A", "我喜欢科幻")]))
    adapter.handle({"request_type": "query", "mode": "raw_native_cases",
                    "sample_id": "sample", "case_type": "write_op_accuracy",
                    "query": "Session 4 写入了哪些事实？"})
    assert "我喜欢科幻" in prompts[-1]
    assert "我喜欢绿茶" not in prompts[-1]


def test_rejections_report_reasons_without_copying_source_text(monkeypatch):
    adapter = _adapter(monkeypatch, [
        [_event("remember", "t1", "A", "原话含私人细节", "喜欢绿茶")],
        [_event("revoke", "t2", "B", "删除 A 的记忆", target_turn_id="t1")],
        [_event("revoke", "t3", "A", "虚构引文", target_turn_id="t1")],
    ])
    adapter.handle(_session("s1", "private_chat", [
        ("t1", "A", "原话含私人细节")]))
    adapter.handle(_session("s2", "private_chat", [
        ("t2", "B", "删除 A 的记忆")]))
    adapter.handle(_session("s3", "private_chat", [
        ("t3", "A", "实际并没有撤销")]))
    result = adapter.handle({"request_type": "query", "mode": "raw_native_cases",
                             "sample_id": "sample", "query": "A 喜欢什么？"})
    reasons = result["metadata"]["operation_rejections"]
    assert {item["reason"] for item in reasons} == {
        "wrong_speaker_target", "source_evidence_mismatch"}
    assert "原话含私人细节" not in json.dumps(reasons, ensure_ascii=False)


def test_paired_runs_replay_identical_facts_without_second_extraction(monkeypatch):
    monkeypatch.setenv("AGENTSCOPE_API_KEY", "test-only")
    budgets = []

    def fake_complete(self, messages, *, max_tokens):
        budgets.append(max_tokens)
        if max_tokens == 512:
            raise AssertionError("frozen facts must bypass the extraction model")
        if max_tokens == 1280:
            return json.dumps({"events": [_event(
                "supersede", "s002_t001", "A", "改喝红茶",
                "现在喜欢红茶", "s001_t001")],
                "candidate_decisions": [{"candidate_id": 0,
                                         "action": "add"}]}), {}
        return "已回答", {"usage": {}}

    monkeypatch.setattr(AgentScopeClient, "complete", fake_complete)
    cassette = {
        "sample/s1": [{"turn_id": "s001_t001", "speaker_id": "A",
                       "fact": "喜欢绿茶"}],
        "sample/s2": [],
    }
    for governed in (False, True):
        adapter = GovernedDeepSeekMemoryAdapter(
            max_queries=1, max_calls=3, apply_governance=governed,
            fact_cassette=cassette)
        adapter.handle({"request_type": "reset", "sample_id": "sample"})
        adapter.handle(_session("s1", "private_chat", [
            ("s001_t001", "A", "我喜欢绿茶")]))
        adapter.handle(_session("s2", "private_chat", [
            ("s002_t001", "A", "改喝红茶")]))
        assert adapter.recorded_facts == cassette
        assert adapter.fact_replay_hits == 2
        assert adapter.extract_calls == 0
        active = [rec.content["summary"] for rec in _active(adapter)]
        assert active == (["[A] 现在喜欢红茶"] if governed
                          else ["[A] 喜欢绿茶"])
    assert 512 not in budgets
    assert budgets.count(1280) == 2


def test_frozen_candidates_are_classified_before_fact_commit(monkeypatch):
    monkeypatch.setenv("AGENTSCOPE_API_KEY", "test-only")

    def fake_complete(self, messages, *, max_tokens):
        assert max_tokens == 1280
        assert "提取候选尚未入库" in messages[0]["content"]
        assert adapter.store.list_by_kind(MemoryKind.PREFERENCE) == []
        return json.dumps({"events": [], "candidate_decisions": [
            {"candidate_id": 0, "action": "skip"},
            {"candidate_id": 1, "action": "ephemeral"},
            {"candidate_id": 2, "action": "add", "slot_key": "interest:tea"},
            {"candidate_id": 3, "action": "add", "slot_key": "interest:tea"},
        ]}, ensure_ascii=False), {}

    monkeypatch.setattr(AgentScopeClient, "complete", fake_complete)
    cassette = {"sample/s1": [
        {"turn_id": "t1", "speaker_id": "A", "fact": "一次性门码"},
        {"turn_id": "t2", "speaker_id": "A", "fact": "今晚喝红茶"},
        {"turn_id": "t3", "speaker_id": "A", "fact": "喜欢绿茶"},
        {"turn_id": "t4", "speaker_id": "A", "fact": "不喜欢绿茶"},
    ]}
    adapter = GovernedDeepSeekMemoryAdapter(
        max_queries=1, max_calls=1, fact_cassette=cassette)
    adapter.handle({"request_type": "reset", "sample_id": "sample"})
    adapter.handle(_session("s1", "private_chat", [
        ("t1", "A", "一次性门码"), ("t2", "A", "今晚喝红茶"),
        ("t3", "A", "我喜欢绿茶"), ("t4", "A", "我不喜欢绿茶"),
    ]))
    assert [record.content["summary"] for record in _active(adapter)] == [
        "[A] 喜欢绿茶"]
    assert [item["action"] for item in adapter.write_trace] == [
        "skip", "ephemeral", "add", "pending"]
    assert adapter.store.get_record(adapter.write_trace[2]["fact_key"]).content[
        "slot_key"] == "interest:tea"
    records = adapter.store.list_by_kind(MemoryKind.PREFERENCE)
    assert sorted(record.content["status"] for record in records) == [
        "active", "pending"]
    assert adapter.store.get_record(adapter.write_trace[3]["fact_key"]).content[
        "status"] == "pending"


def test_missing_candidate_decision_remains_pending(monkeypatch):
    monkeypatch.setenv("AGENTSCOPE_API_KEY", "test-only")
    monkeypatch.setattr(AgentScopeClient, "complete",
                        lambda self, messages, *, max_tokens:
                        ('{"events":[]}', {}) if max_tokens == 1280
                        else ("证据不足", {"usage": {}}))
    adapter = GovernedDeepSeekMemoryAdapter(
        max_queries=1, max_calls=1,
        fact_cassette={"sample/s1": [{"turn_id": "t1", "speaker_id": "A",
                                      "fact": "喜欢绿茶"}]})
    adapter.handle({"request_type": "reset", "sample_id": "sample"})
    adapter.handle(_session("s1", "private_chat", [("t1", "A", "我喜欢绿茶")]))
    assert adapter.write_trace[0]["action"] == "pending"
    assert _active(adapter) == []
    result = adapter.handle({"request_type": "query", "sample_id": "sample",
                             "mode": "raw_native_cases", "query": "喜欢什么茶？"})
    assert result["metadata"]["curated_count"] == 0


def test_source_context_can_be_disabled_without_changing_stored_facts(monkeypatch):
    prompts = []
    adapter = _adapter(monkeypatch, [
        [_event("remember", "s001_t001", "A", "我喜欢绿茶", "喜欢绿茶")],
    ], apply_governance=False, source_context_enabled=False, system_log=prompts)
    adapter.handle(_session("s1", "private_chat", [
        ("s001_t001", "A", "我喜欢绿茶")]))
    result = adapter.handle({"request_type": "query", "mode": "raw_native_cases",
                             "sample_id": "sample", "case_type": "provenance_recoverability",
                             "query": "来源是哪次？"})
    assert result["metadata"]["source_context_enabled"] is False
    assert result["metadata"]["curated_count"] == 1
    assert "可核验的当前记忆来源" not in prompts[-1]


def test_correction_invalidates_all_named_old_fragments_but_keeps_other_facts(monkeypatch):
    prompts = []
    monkeypatch.setenv("AGENTSCOPE_API_KEY", "test-only")

    def fake_complete(self, messages, *, max_tokens):
        if max_tokens == 1280:
            body = messages[0]["content"]
            assert "2705和伞桶钥匙都不能再用" not in body.split("已有记忆：", 1)[1].split("本段对话：", 1)[0]
            return json.dumps({"events": [{
                "op": "revoke", "turn_id": "s004_t003", "speaker_id": "A",
                "quote": "不能再用", "target_key": "old-code",
                "related_target_keys": ["old-key", "missing-key"],
            }]}, ensure_ascii=False), {}
        if max_tokens == 512:
            return '{"facts":[]}', {}
        prompts.append(messages[0]["content"])
        return "已回答", {"usage": {}}

    monkeypatch.setattr(AgentScopeClient, "complete", fake_complete)
    adapter = GovernedDeepSeekMemoryAdapter(max_queries=1, max_calls=5)
    adapter.handle({"request_type": "reset", "sample_id": "sample"})
    space = "timlmembench:private_chat"
    for key, evidence, fact in (
        ("old-code", "s001_t003", "七点前先按2705"),
        ("old-key", "s001_t003", "按不开就用伞桶钥匙"),
        ("unrelated", "s001_t001", "冷藏件由B代收"),
    ):
        adapter.curator.remember(
            f"[A] {fact}", source="test", evidence_id=evidence,
            consent=True, key=key, space_id=space)
    adapter.handle(_session("s4", "private_chat", [
        ("s004_t003", "A", "2705和伞桶钥匙都不能再用")]))
    assert {rec.key for rec in _active(adapter)} == {"unrelated"}
    result = adapter.handle({"request_type": "query", "mode": "raw_native_cases",
                             "sample_id": "sample", "case_type": "write_op_accuracy",
                             "query": "现在冷藏件如何处理？"})
    assert result["metadata"]["operation_trace"][0]["affected_count"] == "2"
    assert "target_not_found" in {
        item["reason"] for item in result["metadata"]["operation_rejections"]}
    assert "2705" not in " ".join(result["retrieved_texts"])
    assert "更正发言：2705和伞桶钥匙都不能再用" in prompts[-1]


def test_session_focused_write_audit_includes_current_rules(monkeypatch):
    prompts = []
    adapter = _adapter(monkeypatch, [
        [_event("remember", "sample__s001_t001", "A", "旧安排", "旧安排")],
        [_event("remember", "sample__s004_t005", "A", "新安排放蓝色袋", "新安排放蓝色袋")],
    ], apply_governance=False, system_log=prompts)
    adapter.handle(_session("s1", "private_chat", [("sample__s001_t001", "A", "旧安排")]))
    adapter.handle(_session("s4", "private_chat", [("sample__s004_t005", "A", "新安排放蓝色袋")]))
    adapter.handle({"request_type": "query", "mode": "raw_native_cases",
                    "sample_id": "sample", "case_type": "write_op_accuracy",
                    "query": "Session 4 后应写入哪些规则？"})
    assert "操作后仍有效：[A] 新安排放蓝色袋" in prompts[-1]


def test_write_audit_includes_revoke_reason_from_same_speaker(monkeypatch):
    adapter = _adapter(monkeypatch, [])
    space = "timlmembench:private_chat"
    adapter.source_turns = {
        "sample__s005_t002": (space, "前台别留", "A"),
        "sample__s005_t003": (space, "她们当着别人念出名字两次", "A"),
    }
    adapter.operation_trace = [{
        "op": "revoke", "source_turn_id": "sample__s005_t002",
        "target_key": "old", "new_key": "", "target_evidence_ids": (),
        "support_turn_ids": ("sample__s005_t003",), "space_id": space,
    }]
    context = adapter._source_context(
        "Session 5 前台为什么不再代收？", [space], "write_op_accuracy")
    assert "同一变更的补充证据 Session 5, turn sample__s005_t003" in context
    assert "她们当着别人念出名字两次" in context


def test_provenance_quotes_only_current_facts_in_authorized_space(monkeypatch):
    prompts = []
    adapter = _adapter(monkeypatch, [
        [_event("remember", "s001_t001", "A", "原来喜欢绿茶", "喜欢绿茶")],
        [_event("supersede", "s002_t001", "A", "现在喜欢红茶", "喜欢红茶", "s001_t001")],
        [_event("remember", "s003_t001", "B", "秘密是蓝色", "秘密是蓝色")],
    ], system_log=prompts)
    adapter.handle(_session("s1", "private_chat", [("s001_t001", "A", "原来喜欢绿茶")]))
    adapter.handle(_session("s2", "private_chat", [("s002_t001", "A", "现在喜欢红茶")]))
    adapter.handle(_session("s3", "work_collaboration", [("s003_t001", "B", "秘密是蓝色")]))
    adapter.handle({"request_type": "query", "mode": "raw_native_cases",
                    "sample_id": "sample", "case_type": "provenance_recoverability",
                    "query": "A 现在喜欢什么茶？",
                    "allowed_spaces": ["timlmembench:private_chat"]})
    system = prompts[-1]
    assert "原话：现在喜欢红茶" in system
    assert "原话：原来喜欢绿茶" not in system
    assert "秘密是蓝色" not in system
