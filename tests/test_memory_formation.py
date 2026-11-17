from types import SimpleNamespace

from whale_core.core.companion import WhaleCompanion
from whale_core.core.memory_commands import handle_memory_command
from whale_core.memory.continuity import remember_episode
from whale_core.memory.curation import MemoryCurator
from whale_core.memory.formation import (
    JOB_PREFIX, eligible_message, enqueue, run_pending, _quotes_from_model, _expires_at,
    _slot_for_quote,
)
from whale_core.memory.sqlite_store import SqliteMemoryStore
from whale_core.memory.store import InMemoryStore, MemoryKind
from whale_core.memory.write_decision import apply_decision, decide_candidate
from whale_core.memory.facts import FactService, _normalized_claim


class Extractor:
    def __init__(self, answer='{"quotes":["我喜欢科幻"]}'):
        self.answer = answer
        self.calls = []

    def reply(self, text, *, persona="", memories=(), history=()):
        self.calls.append(text)
        return self.answer


def test_daily_habits_and_current_plans_are_distinct():
    assert eligible_message("我每周会跑步两次")
    assert _quotes_from_model('{"quotes":["我每周会跑步两次"]}',
                              "我每周会跑步两次") == ("我每周会跑步两次",)
    assert _expires_at("我每周会跑步两次") == ""
    assert _expires_at("我计划写一篇文章")
    assert _slot_for_quote("我喜欢科幻") == _slot_for_quote("我不喜欢科幻")
    assert _slot_for_quote("我很喜欢科幻") == _slot_for_quote("我讨厌科幻。")
    assert _slot_for_quote("我正在准备答辩") == ""
    assert eligible_message("我最近在准备答辩")
    assert _quotes_from_model('{"quotes":["我最近在准备答辩"]}',
                              "我最近在准备答辩") == ("我最近在准备答辩",)
    assert _expires_at("我最近在准备答辩")


def test_automatic_conflict_is_pending_until_explicit_correction():
    store = InMemoryStore()
    for index, text in enumerate(("我喜欢科幻", "我不喜欢科幻"), 1):
        source_id = f"ep:{index}"
        remember_episode(store, summary=f"用户：{text}\nEnxi：好",
                         key=source_id, tags=("chat",), space_id="owner")
        enqueue(store, owner_id="owner", space_id="owner",
                source_id=source_id, text=text)
        result = run_pending(store, Extractor(
            '{"quotes":["' + text + '"]}'))
        assert result["written"] == (1 if index == 1 else 0)
    records = store.list_by_kind(MemoryKind.PREFERENCE)
    assert [record.content["status"] for record in records].count("active") == 1
    assert [record.content["status"] for record in records].count("pending") == 1
    assert MemoryCurator(store).recall("科幻", allowed_spaces=("owner",)) == ()


def test_explicit_self_correction_updates_one_fact_family():
    store = InMemoryStore()
    for index, text in enumerate(("我喜欢科幻", "我以后不喜欢科幻"), 1):
        source_id = f"ep:{index}"
        remember_episode(store, summary=f"用户：{text}\nEnxi：好",
                         key=source_id, tags=("chat",), space_id="owner")
        assert enqueue(store, owner_id="owner", space_id="owner",
                       source_id=source_id, text=text)
        assert run_pending(store, Extractor(
            '{"quotes":["' + text + '"]}'))["written"] == 1
    records = store.list_by_kind(MemoryKind.PREFERENCE, space_ids=("owner",))
    assert sorted(row.content["status"] for row in records) == ["active", "superseded"]
    assert len({row.content["family_id"] for row in records}) == 1
    assert next(row for row in records if row.content["status"] == "active").content[
        "summary"] == "用户自述：我以后不喜欢科幻"


def test_same_fact_preserves_record_and_adds_source_evidence():
    store = InMemoryStore()
    for index in (1, 2):
        source_id = f"ep:{index}"
        remember_episode(store, summary="用户：我喜欢科幻\nEnxi：好",
                         key=source_id, tags=("chat",), space_id="owner")
        enqueue(store, owner_id="owner", space_id="owner",
                source_id=source_id, text="我喜欢科幻")
        assert run_pending(store, Extractor())["failed"] == 0
    records = store.list_by_kind(MemoryKind.PREFERENCE, space_ids=("owner",))
    assert len(records) == 1
    assert records[0].content["evidence_ids"] == ["ep:1", "ep:2"]


def test_ambiguous_same_slot_needs_validated_decision():
    store = InMemoryStore()
    old = FactService(store).remember(
        "用户自述：我喜欢科幻", source="chat", evidence_id="old",
        space_id="owner", slot_key="interest:科幻").record
    model = Extractor('{"action":"compatible_add","target_key":""}')
    decision = decide_candidate(
        store, model, summary="用户自述：我喜欢科幻电影",
        source_text="我喜欢科幻电影", space_id="owner", subject_id="owner",
        slot_key="interest:科幻")
    assert decision.action == "compatible_add" and len(model.calls) == 1
    result = apply_decision(
        store, decision, summary="用户自述：我喜欢科幻电影",
        evidence_id="new", space_id="owner", subject_id="owner",
        slot_key="interest:科幻")
    assert result.action == "add"
    assert store.get_record(old.key).content["status"] == "active"
    assert FactService(store).decision_for(
        summary="用户自述：我喜欢科幻电影", evidence_id="new",
        space_id="owner")["reason"] == "compatible_supplement"


def test_same_claim_with_punctuation_preserves_one_record():
    store = InMemoryStore()
    facts = FactService(store)
    old = facts.remember("用户自述：我喜欢科幻。", source="chat",
                         evidence_id="first", space_id="owner",
                         slot_key="interest:科幻").record
    model = Extractor()
    decision = decide_candidate(
        store, model, summary="用户自述：我喜欢科幻",
        source_text="我喜欢科幻", space_id="owner", subject_id="owner",
        slot_key="interest:科幻")
    assert decision.action == "preserve" and not model.calls
    result = apply_decision(
        store, decision, summary="用户自述：我喜欢科幻",
        evidence_id="second", space_id="owner", subject_id="owner",
        slot_key="interest:科幻")
    assert result.action == "preserve"
    records = store.list_by_kind(MemoryKind.PREFERENCE, space_ids=("owner",))
    assert len(records) == 1 and records[0].key == old.key
    assert records[0].content["evidence_ids"] == ["first", "second"]


def test_internal_punctuation_is_part_of_claim():
    assert _normalized_claim("用户自述：我喜欢猫，狗") != _normalized_claim(
        "用户自述：我喜欢猫狗")


def test_one_correction_does_not_update_another_candidate_in_same_message():
    store = InMemoryStore()
    old = FactService(store).remember(
        "用户自述：我喜欢科幻", source="chat", evidence_id="old",
        space_id="owner", slot_key="interest:科幻").record
    message = "我很喜欢科幻，我以后不喜欢咖啡"
    remember_episode(store, summary=f"用户：{message}\nEnxi：好",
                     key="ep:new", tags=("chat",), space_id="owner")
    enqueue(store, owner_id="owner", space_id="owner",
            source_id="ep:new", text=message)

    class TwoStageExtractor(Extractor):
        def reply(self, text, *, persona="", memories=(), history=()):
            self.calls.append(text)
            if "最多 3 条" in persona:
                return '{"quotes":["我很喜欢科幻","我以后不喜欢咖啡"]}'
            return '{"action":"compatible_add","target_key":""}'

    model = TwoStageExtractor()
    assert run_pending(store, model)["failed"] == 0
    assert store.get_record(old.key).content["status"] == "active"
    assert model.calls[1] == "我很喜欢科幻"


def test_write_decision_does_not_compare_another_person():
    store = InMemoryStore()
    facts = FactService(store)
    facts.remember("A喜欢科幻", source="chat", evidence_id="a",
                   space_id="group:one", subject_id="A",
                   slot_key="interest:科幻")
    decision = decide_candidate(
        store, Extractor(), summary="B不喜欢科幻",
        source_text="B不喜欢科幻", space_id="group:one", subject_id="B",
        slot_key="interest:科幻")
    assert decision.action == "add"


def test_model_candidate_is_grounded_and_written_once():
    store = SqliteMemoryStore(":memory:")
    try:
        remember_episode(
            store, summary="用户：我喜欢科幻\nEnxi：聊聊哪部？",
            key="ep:1", tags=("chat",), space_id="owner")
        job = enqueue(
            store, owner_id="owner", space_id="owner",
            source_id="ep:1", text="我喜欢科幻")
        assert job and job.startswith(JOB_PREFIX)
        extractor = Extractor()
        assert run_pending(store, extractor) == {
            "written": 1, "skipped": 0, "failed": 0}
        assert len(extractor.calls) == 1
        assert enqueue(
            store, owner_id="owner", space_id="owner",
            source_id="ep:1", text="我喜欢科幻") is None
        records = store.list_by_kind(MemoryKind.PREFERENCE, space_ids=("owner",))
        assert len(records) == 1
        assert records[0].content["summary"] == "用户自述：我喜欢科幻"
        assert records[0].content["evidence_id"] == "ep:1"
    finally:
        store.close()


def test_model_must_quote_the_source_and_sensitive_chat_is_skipped():
    assert not eligible_message("我和男朋友分手了，我喜欢独处")
    assert not eligible_message("他说“我喜欢科幻”")
    store = InMemoryStore()
    remember_episode(
        store, summary="用户：我喜欢科幻\nEnxi：好",
        key="ep:1", tags=("chat",), space_id="owner")
    enqueue(store, owner_id="owner", space_id="owner",
            source_id="ep:1", text="我喜欢科幻")
    extractor = Extractor('{"quotes":["我喜欢天文"]}')
    assert run_pending(store, extractor)["written"] == 0
    assert store.list_by_kind(MemoryKind.PREFERENCE) == []
    remember_episode(
        store, summary="用户：我看他说喜欢科幻\nEnxi：好",
        key="ep:2", tags=("chat",), space_id="owner")
    enqueue(store, owner_id="owner", space_id="owner",
            source_id="ep:2", text="我看他说喜欢科幻")
    extractor.answer = '{"quotes":["我看他说喜欢科幻"]}'
    assert run_pending(store, extractor)["written"] == 0


def test_owner_chat_enqueues_after_reply_and_new_companion_resumes(monkeypatch):
    import whale_core.memory.formation as formation

    scheduled = []
    monkeypatch.setattr(formation, "schedule",
                        lambda store, model: scheduled.append(model))
    store = InMemoryStore()
    extractor = Extractor()

    class ChatModel:
        def reply(self, text, *, persona="", memories=(), history=()):
            return "你最喜欢哪部？"

    context = SimpleNamespace(
        principal=SimpleNamespace(principal_id="owner", is_owner=True),
        allowed_memory_spaces=None, persona_id=None)
    WhaleCompanion(
        store, ChatModel(), curator=MemoryCurator(store),
        memory_extractor=extractor).chat(
            "我喜欢科幻", conversation_id="private:owner",
            context=context, write_space="owner")
    assert len(scheduled) == 1
    assert any(key.startswith(JOB_PREFIX) for key in store.keys())

    WhaleCompanion(
        store, ChatModel(), curator=MemoryCurator(store),
        memory_extractor=extractor)
    assert len(scheduled) == 2
    assert run_pending(store, extractor)["written"] == 1


def test_forget_cancels_a_fact_still_waiting_for_extraction():
    store = InMemoryStore()
    remember_episode(
        store, summary="用户：我喜欢科幻\nEnxi：好",
        key="ep:1", tags=("chat",), space_id="owner")
    enqueue(store, owner_id="owner", space_id="owner",
            source_id="ep:1", text="我喜欢科幻")
    reply = handle_memory_command(
        store, "忘掉：科幻", source="owner", evidence_id="m2",
        space_id="owner", allowed_spaces=("owner",))
    assert reply == "已忘掉待写入的记忆"
    assert run_pending(store, Extractor())["written"] == 0


def test_current_study_fact_gets_expiry_and_role_fact_is_stable():
    store = InMemoryStore()
    text = "我是研究生，我正在准备答辩"
    remember_episode(
        store, summary=f"用户：{text}\nEnxi：好",
        key="ep:study", tags=("chat",), space_id="owner")
    enqueue(store, owner_id="owner", space_id="owner",
            source_id="ep:study", text=text)
    extractor = Extractor(
        '{"quotes":["我是研究生","我正在准备答辩"]}')
    assert run_pending(store, extractor)["written"] == 2
    records = {
        record.content["summary"]: record
        for record in store.list_by_kind(MemoryKind.PREFERENCE)
    }
    assert "expires_at" not in records["用户自述：我是研究生"].content
    assert records["用户自述：我正在准备答辩"].content["expires_at"]
