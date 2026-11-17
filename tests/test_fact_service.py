import pytest
from datetime import datetime, timedelta, timezone

from whale_core.core.memory_commands import handle_memory_command
from whale_core.memory.curation import MemoryCurator
from whale_core.memory.continuity import build_continuity_context
from whale_core.memory.facts import FactService
from whale_core.memory.hybrid import HybridCurator
from whale_core.memory.sqlite_store import SqliteMemoryStore
from whale_core.memory.store import InMemoryStore, MemoryKind


@pytest.mark.parametrize("store_factory", [InMemoryStore,
                                          lambda: SqliteMemoryStore(":memory:")])
def test_correction_retires_duplicate_old_facts_in_one_space(store_factory):
    store = store_factory()
    try:
        curator = MemoryCurator(store)
        first = curator.remember(
            "用户自述：我怕辣", source="old", evidence_id="m1",
            stable_fact=True, consent=True, space_id="owner")
        duplicate = curator.remember(
            "用户自述：我怕辣", source="old", evidence_id="m2",
            stable_fact=True, consent=True, space_id="owner")
        related = curator.remember(
            "用户自述：我不吃辣", source="old", evidence_id="m5",
            stable_fact=True, consent=True, space_id="owner")
        already_new = curator.remember(
            "用户自述：我不怕辣", source="old", evidence_id="m6",
            stable_fact=True, consent=True, space_id="owner")
        other_space = curator.remember(
            "用户自述：我怕辣", source="other", evidence_id="m3",
            stable_fact=True, consent=True, space_id="group")

        result = FactService(store).correct(
            first.key, "用户自述：我不怕辣",
            source="correction", evidence_id="m4",
            related_keys=(related.key,))

        assert result.action == "update"
        assert set(result.retired_keys) == {
            first.key, duplicate.key, related.key, already_new.key}
        assert result.record.content["version"] == 2
        assert "superseded" in store.get_record(first.key).tags
        assert "superseded" in store.get_record(duplicate.key).tags
        assert store.get_record(other_space.key).tags == other_space.tags
        owner_hits = curator.recall("辣", allowed_spaces=("owner",))
        assert [hit.record.key for hit in owner_hits] == [result.record.key]
    finally:
        if isinstance(store, SqliteMemoryStore):
            store.close()


def test_explicit_commands_preserve_evidence_then_correct_and_forget():
    store = InMemoryStore()
    first = handle_memory_command(
        store, "记住：我怕辣", source="qq", evidence_id="m1",
        space_id="owner", allowed_spaces=("owner",))
    second = handle_memory_command(
        store, "记住：我怕辣", source="qq", evidence_id="m2",
        space_id="owner", allowed_spaces=("owner",))
    assert first == "好，我记住啦：我怕辣"
    assert second == "这个我记着呢：我怕辣"
    old = store.list_by_kind(MemoryKind.PREFERENCE, space_ids=("owner",))[0]
    assert old.content["evidence_ids"] == ["m1", "m2"]

    reply = handle_memory_command(
        store, "更正：我怕辣 -> 我不怕辣",
        source="qq", evidence_id="m3",
        space_id="owner", allowed_spaces=("owner",))
    assert reply == "已更正：我不怕辣"
    current = MemoryCurator(store).recall("辣", allowed_spaces=("owner",))
    assert len(current) == 1
    assert current[0].record.content["summary"] == "用户自述：我不怕辣"
    assert current[0].record.content["family_id"] == old.content["family_id"]

    reply = handle_memory_command(
        store, "忘掉：辣", source="qq", evidence_id="m4",
        space_id="owner", allowed_spaces=("owner",))
    assert reply.startswith("已忘掉")
    assert MemoryCurator(store).recall("辣", allowed_spaces=("owner",)) == ()


def test_explicit_correction_does_not_call_semantic_retrieval():
    store = InMemoryStore()
    handle_memory_command(
        store, "记住：我怕辣", source="qq", evidence_id="m1",
        space_id="owner", allowed_spaces=("owner",))

    class NoRecall:
        def recall(self, *args, **kwargs):
            raise AssertionError("correction should use exact stored fact")

    reply = handle_memory_command(
        store, "更正：我怕辣 → 我不怕辣",
        source="qq", evidence_id="m2", curator=NoRecall(),
        space_id="owner", allowed_spaces=("owner",))
    assert reply == "已更正：我不怕辣"


def test_old_permission_revoke_keeps_new_independent_rule_and_chain_is_text_free():
    store = InMemoryStore()
    facts = FactService(store)
    old = facts.remember("用户自述：旧门码可以开门", source="chat",
                         evidence_id="s1_t1", space_id="owner").record
    new_rule = facts.remember("用户自述：以后不能再用门码开门", source="chat",
                              evidence_id="s2_t1", space_id="owner").record
    corrected = facts.correct(old.key, "用户自述：旧门码已停用",
                              source="correction", evidence_id="s3_t1")
    assert corrected.action == "update"
    chain = facts.evidence_chain(corrected.record.key)
    assert [row["evidence_ids"] for row in chain] == [("s1_t1",), ("s3_t1",)]
    assert "旧门码" not in str(chain)
    assert store.get_record(new_rule.key) is not None
    facts.forget(corrected.record.key)
    assert [hit.record.key for hit in MemoryCurator(store).recall(
        "门码开门", allowed_spaces=("owner",))] == [new_rule.key]


@pytest.mark.parametrize("store_factory", [InMemoryStore,
                                          lambda: SqliteMemoryStore(":memory:")])
def test_slot_conflict_waits_for_correction_and_retires_aliases(store_factory):
    store = store_factory()
    try:
        facts = FactService(store)
        first = facts.remember("用户自述：我喜欢科幻", source="chat",
                               evidence_id="m1", space_id="owner",
                               slot_key="interest:科幻").record
        alias = facts.remember("用户自述：我挺喜欢科幻", source="chat",
                               evidence_id="m2", space_id="owner").record
        pending = facts.remember("用户自述：我不喜欢科幻", source="chat",
                                 evidence_id="m3", space_id="owner",
                                 slot_key="interest:科幻")
        assert pending.action == "pending"
        assert pending.record.key != first.key
        assert pending.record.content["status"] == "pending"
        assert pending.record.content["family_id"] == first.content["family_id"]
        assert pending.record.content["version"] == first.content["version"] + 1
        assert facts.remember("用户自述：我不喜欢科幻", source="chat",
                              evidence_id="m3", space_id="owner",
                              slot_key="interest:科幻").record.key == pending.record.key
        assert facts.decision_for(summary="用户自述：我不喜欢科幻",
                                  evidence_id="m3", space_id="owner")["record_key"] == pending.record.key
        assert [hit.record.key for hit in MemoryCurator(store).recall(
            "科幻", allowed_spaces=("owner",))] == [alias.key]
        assert [hit.record.key for hit in HybridCurator(store).recall(
            "科幻", allowed_spaces=("owner",))] == [alias.key]
        result = facts.correct(first.key, "用户自述：我不喜欢科幻",
                               source="correction", evidence_id="m4",
                               related_keys=(alias.key,))
        assert set(result.retired_keys) == {first.key, alias.key}
        assert store.get_record(first.key).content["valid_to"]
        assert store.get_record(alias.key).content["status"] == "superseded"
        assert store.get_record(pending.record.key).content["status"] == "superseded"
        assert result.record.content["version"] == pending.record.content["version"] + 1
        assert [row["version"] for row in facts.evidence_chain(result.record.key)] == [
            1, 1, 2, 3]
        assert [hit.record.key for hit in MemoryCurator(store).recall(
            "科幻", allowed_spaces=("owner",))] == [result.record.key]
        assert [hit.record.key for hit in HybridCurator(store).recall(
            "科幻", allowed_spaces=("owner",))] == [result.record.key]
        assert facts.decision_for(summary="用户自述：我不喜欢科幻",
                                  evidence_id="m4", space_id="owner")["action"] == "update"
    finally:
        if isinstance(store, SqliteMemoryStore):
            store.close()


@pytest.mark.parametrize("store_factory", [InMemoryStore,
                                          lambda: SqliteMemoryStore(":memory:")])
def test_revoke_only_retires_matching_subject_and_space(store_factory):
    store = store_factory()
    try:
        facts = FactService(store)
        first = facts.remember("A 允许提醒", source="chat", evidence_id="m1",
                               space_id="group:1", subject_id="A",
                               slot_key="reminder:friday").record
        other = facts.remember("B 允许提醒", source="chat", evidence_id="m2",
                               space_id="group:1", subject_id="B",
                               slot_key="reminder:friday").record
        elsewhere = facts.remember("A 允许提醒", source="chat", evidence_id="m3",
                                   space_id="group:2", subject_id="A",
                                   slot_key="reminder:friday").record
        pending = facts.remember("A 不想被提醒", source="chat", evidence_id="m4",
                                 space_id="group:1", subject_id="A",
                                 slot_key="reminder:friday").record
        result = facts.revoke(first.key, source="chat", evidence_id="m4")
        assert result.retired_keys == (first.key,)
        assert store.get_record(first.key).content["status"] == "revoked"
        assert store.get_record(pending.key).content["status"] == "revoked"
        assert facts.evidence_chain(first.key)[0]["current"] is False
        assert store.get_record(other.key).content["status"] == "active"
        assert store.get_record(elsewhere.key).content["status"] == "active"
    finally:
        if isinstance(store, SqliteMemoryStore):
            store.close()


def test_skip_and_expired_fact_do_not_block_new_candidate():
    store = InMemoryStore()
    facts = FactService(store)
    assert facts.skip_candidate("今晚喝茶", source="chat", evidence_id="m1",
                                space_id="owner", reason="one_time").action == "skip"
    assert facts.decision_for(summary="今晚喝茶", evidence_id="m1",
                              space_id="owner")["reason"] == "one_time"
    assert store.list_by_kind(MemoryKind.PREFERENCE) == []
    old = facts.remember("计划 A", source="chat", evidence_id="m2",
                         space_id="owner", slot_key="plan:weekend",
                         expires_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
    assert old.action == "add"
    current = facts.remember("计划 B", source="chat", evidence_id="m3",
                             space_id="owner", slot_key="plan:weekend")
    assert current.action == "add"
    assert facts.decision_for(summary="计划 A", evidence_id="m2",
                              space_id="owner")["action"] == "ephemeral"


def test_forget_removes_conflicting_pending_candidate():
    store = InMemoryStore()
    facts = FactService(store)
    old = facts.remember("喜欢茶", source="chat", evidence_id="m1",
                         space_id="owner", slot_key="drink:tea").record
    pending = facts.remember("不喜欢茶", source="chat", evidence_id="m2",
                             space_id="owner", slot_key="drink:tea").record
    facts.forget(old.key)
    assert store.get_record(pending.key) is None
    fresh = facts.remember("喜欢咖啡", source="chat", evidence_id="m3",
                           space_id="owner", slot_key="drink:tea")
    assert [hit.record.key for hit in MemoryCurator(store).recall(
        "咖啡", allowed_spaces=("owner",))] == [fresh.record.key]


@pytest.mark.parametrize("store_factory", [InMemoryStore,
                                          lambda: SqliteMemoryStore(":memory:")])
def test_same_summary_in_distinct_context_slots_stays_independent(store_factory):
    store = store_factory()
    try:
        facts = FactService(store)
        home = facts.remember("我喜欢安静", source="chat", evidence_id="m1",
                              space_id="owner", slot_key="preference:home").record
        office = facts.remember("我喜欢安静", source="chat", evidence_id="m2",
                                space_id="owner", slot_key="preference:office").record
        assert home.key != office.key
        changed = facts.correct(home.key, "我喜欢热闹", source="chat", evidence_id="m3")
        assert changed.retired_keys == (home.key,)
        assert store.get_record(office.key).content["status"] == "active"
        assert set(hit.record.key for hit in MemoryCurator(store).recall(
            "喜欢", allowed_spaces=("owner",))) == {office.key, changed.record.key}
    finally:
        if isinstance(store, SqliteMemoryStore):
            store.close()


def test_candidate_operation_rejects_cross_subject_update():
    store = InMemoryStore()
    facts = FactService(store)
    old = facts.apply_candidate("A 喜欢绿茶", operation="add",
                                source="chat", evidence_id="m1",
                                space_id="group:1", subject_id="A").record
    with pytest.raises(KeyError):
        facts.apply_candidate("B 喜欢红茶", operation="update",
                              source="chat", evidence_id="m2",
                              space_id="group:1", subject_id="B",
                              target_key=old.key)
    assert store.get_record(old.key).content["status"] == "active"


def test_same_slot_compatible_supplement_does_not_become_conflict():
    store = InMemoryStore()
    facts = FactService(store)
    first = facts.remember("A 喜欢科幻", source="chat", evidence_id="m1",
                           space_id="owner", subject_id="A",
                           slot_key="interest:科幻").record
    extra = facts.remember("A 喜欢科幻电影", source="chat", evidence_id="m2",
                           space_id="owner", subject_id="A",
                           slot_key="interest:科幻")
    assert extra.action == "add"
    assert store.get_record(first.key).content["status"] == "active"
    assert extra.record.content["status"] == "active"
    assert extra.record.content["family_id"] != first.content["family_id"]
    facts.revoke(first.key, source="chat", evidence_id="m3")
    assert store.get_record(extra.record.key).content["status"] == "active"


@pytest.mark.parametrize("store_factory", [InMemoryStore,
                                          lambda: SqliteMemoryStore(":memory:")])
def test_mango_revocation_revalidates_dependent_reminder_only(store_factory):
    store = store_factory()
    try:
        facts = FactService(store)
        allergy = facts.remember("[A] A上次吃芒果后喉咙肿，需要避开芒果甜点",
                                 source="chat", evidence_id="old",
                                 space_id="private", subject_id="A").record
        reminder = facts.remember("[B] B会私下提醒A避开芒果甜点，不在群里展开",
                                  source="chat", evidence_id="reminder",
                                  space_id="private", subject_id="B").record
        boundary = facts.remember("[A] A不要在群里聊身体细节", source="chat",
                                  evidence_id="boundary", space_id="private",
                                  subject_id="A").record
        milk = facts.remember("[A] A偏好燕麦奶", source="chat",
                              evidence_id="milk", space_id="private",
                              subject_id="A").record
        result = facts.revoke(allergy.key, source="chat", evidence_id="correction")
        assert set(result.retired_keys) == {allergy.key, reminder.key}
        assert store.get_record(allergy.key).content["status"] == "revoked"
        assert store.get_record(reminder.key).content["status"] == "needs_revalidation"
        assert store.get_record(reminder.key).content["revalidation_premise_keys"] == [allergy.key]
        assert store.get_record(boundary.key).content["status"] == "active"
        assert store.get_record(milk.key).content["status"] == "active"
        assert all(hit.record.key != reminder.key for hit in MemoryCurator(store).recall(
            "芒果甜点", allowed_spaces=("private",)))
        assert facts.history_for(allergy.key, allowed_spaces=("private",))["versions"][0]["summary"]
    finally:
        if isinstance(store, SqliteMemoryStore):
            store.close()


def test_explicit_dependency_can_be_revalidated_with_new_evidence():
    store = InMemoryStore()
    facts = FactService(store)
    premise = facts.remember("A计划周五出门", source="chat", evidence_id="m1",
                             space_id="owner", subject_id="A").record
    dependent = facts.remember("B周五提前叫车", source="chat", evidence_id="m2",
                               space_id="owner", subject_id="B",
                               depends_on_keys=(premise.key,)).record
    facts.revoke(premise.key, source="chat", evidence_id="m3")
    assert store.get_record(dependent.key).content["status"] == "needs_revalidation"
    confirmed = facts.correct(dependent.key, "B周五提前叫车",
                              source="confirmation", evidence_id="m4")
    assert confirmed.action == "update"
    assert store.get_record(dependent.key).content["status"] == "superseded"
    assert confirmed.record.content["status"] == "active"
    assert [row["status"] for row in facts.evidence_chain(confirmed.record.key)] == [
        "superseded", "active"]


def test_ephemeral_without_expiry_does_not_enter_long_term_facts():
    store = InMemoryStore()
    facts = FactService(store)
    result = facts.apply_candidate("今晚喝红茶", operation="ephemeral",
                                   source="chat", evidence_id="m1",
                                   space_id="owner")
    assert result.action == "ephemeral"
    assert result.record is None
    assert store.list_by_kind(MemoryKind.PREFERENCE) == []
    assert facts.decision_for(summary="今晚喝红茶", evidence_id="m1",
                              space_id="owner")["action"] == "ephemeral"


@pytest.mark.parametrize("store_factory", [InMemoryStore,
                                          lambda: SqliteMemoryStore(":memory:")])
def test_unclassified_candidate_is_retained_but_not_recalled(store_factory):
    store = store_factory()
    try:
        facts = FactService(store)
        pending = facts.apply_candidate(
            "用户自述：我喜欢绿茶", operation="pending", source="chat",
            evidence_id="m1", space_id="owner", slot_key="interest:tea")
        assert pending.record.content["status"] == "pending"
        assert MemoryCurator(store).recall("绿茶", allowed_spaces=("owner",)) == ()
        assert HybridCurator(store).recall("绿茶", allowed_spaces=("owner",)) == ()
        assert build_continuity_context(
            store, allowed_space_ids=("owner",)).preferences == ()
        assert facts.decision_for(summary="用户自述：我喜欢绿茶",
                                  evidence_id="m1", space_id="owner")["record_key"] == pending.record.key
        assert facts.apply_candidate(
            "用户自述：我喜欢绿茶", operation="pending", source="chat",
            evidence_id="m1", space_id="owner").record.key == pending.record.key
    finally:
        if isinstance(store, SqliteMemoryStore):
            store.close()


@pytest.mark.parametrize("store_factory", [InMemoryStore,
                                          lambda: SqliteMemoryStore(":memory:")])
def test_decision_view_keeps_historical_actions_and_filters_spaces(store_factory):
    store = store_factory()
    try:
        facts = FactService(store)
        old = facts.remember("A 喜欢绿茶", source="chat", evidence_id="t1",
                             space_id="private", subject_id="A").record
        facts.remember("A 喜欢绿茶", source="chat", evidence_id="t2",
                       space_id="private", subject_id="A")
        facts.skip_candidate("今晚喝茶", source="chat", evidence_id="t3",
                             space_id="private", subject_id="A")
        facts.correct(old.key, "A 喜欢红茶", source="chat", evidence_id="t4")
        facts.remember("B 喜欢咖啡", source="chat", evidence_id="t5",
                       space_id="other", subject_id="B")
        rows = facts.decisions_for_evidence(
            ("t4", "t3", "t2", "t1", "t5"), allowed_spaces=("private",))
        assert [(row["evidence_id"], row["action"]) for row in rows] == [
            ("t4", "update"), ("t3", "skip"), ("t2", "preserve"), ("t1", "add")]
        assert facts.decisions_for_evidence(("t1",), allowed_spaces=()) == ()
        assert rows[-1]["record_key"] == old.key
        assert "summary" not in rows[-1]
    finally:
        if isinstance(store, SqliteMemoryStore):
            store.close()


def test_correction_decision_retains_support_anchors_without_quotes():
    store = InMemoryStore()
    facts = FactService(store)
    old = facts.remember("旧安排", source="chat", evidence_id="s1_t1",
                         space_id="owner").record
    facts.apply_candidate("新安排", operation="update", target_key=old.key,
                          source="chat", evidence_id="s2_t1", space_id="owner",
                          support_evidence_ids=("s2_t3", "s2_t5"))
    decision = facts.decisions_for_evidence(("s2_t1",), allowed_spaces=("owner",))[0]
    assert decision["action"] == "update"
    assert decision["support_evidence_ids"] == ["s2_t3", "s2_t5"]
    assert "新安排" not in str(decision)


def test_history_uses_same_fact_family_and_only_live_source_quotes():
    from whale_core.memory.continuity import remember_episode

    store = InMemoryStore()
    remember_episode(store, summary="用户：我喜欢绿茶\nEnxi：好",
                     key="ep:1", tags=("chat",), space_id="owner")
    facts = FactService(store)
    old = facts.remember("用户自述：我喜欢绿茶", source="chat",
                         evidence_id="ep:1", space_id="owner").record
    new = facts.correct(old.key, "用户自述：我喜欢红茶", source="chat",
                        evidence_id="ep:2").record
    assert facts.history_for(new.key, allowed_spaces=("other",)) is None
    history = facts.history_for(old.key, allowed_spaces=("owner",))
    assert history["current_summary"] == "用户自述：我喜欢红茶"
    assert [row["status"] for row in history["versions"]] == [
        "superseded", "active"]
    assert history["versions"][0]["sources"][0]["quote"] == "我喜欢绿茶"
    assert history["versions"][1]["sources"][0]["quote"] == ""
    assert [row["action"] for row in history["operations"]] == ["add", "update"]
    store.delete("ep:1")
    assert facts.history_for(new.key, allowed_spaces=("owner",))[
        "versions"][0]["sources"][0]["quote"] == ""
    facts.revoke(new.key, source="chat", evidence_id="ep:3")
    revoked_history = facts.history_for(old.key, allowed_spaces=("owner",))
    assert revoked_history["current_summary"] == ""
    assert [row["action"] for row in revoked_history["operations"]] == [
        "add", "update", "revoke"]


def test_legacy_correction_chain_survives_sqlite_transaction():
    from whale_core.memory.governance import (
        correct_legacy_record, legacy_correction_history,
    )
    from whale_core.memory.sqlite_store import SqliteMemoryStore
    from whale_core.memory.store import MemoryKind, MemoryRecord

    store = SqliteMemoryStore(":memory:")
    try:
        store.put_record(MemoryRecord(
            kind=MemoryKind.EPISODE, key="old",
            content={"summary": "明天交稿"}, tags=("curated",),
            space_id="owner"))
        new = correct_legacy_record(
            store, "old", "下周交稿", source="studio-correct",
            key_prefix="mem:corr-")
        assert store.get_record("old").content["status"] == "superseded"
        assert legacy_correction_history(
            store, "old", allowed_spaces=("owner",))["current_summary"] == "下周交稿"
        assert legacy_correction_history(
            store, new.key, allowed_spaces=("other",)) is None
    finally:
        store.close()


def test_explicit_delivery_replacement_retires_old_rule_but_keeps_other_context():
    store = InMemoryStore()
    facts = FactService(store)
    old = facts.remember(
        "如果有小盒子到，先放玄关鞋柜上", source="chat",
        evidence_id="m1", space_id="private", subject_id="A",
        slot_key="model:小盒子").record
    other = facts.remember(
        "漏水时先找物业开门，不要自己用旧码", source="chat",
        evidence_id="m2", space_id="private", subject_id="A").record
    new = facts.remember(
        "以后快递改放楼下柜机，不要进屋", source="chat",
        evidence_id="m3", space_id="private", subject_id="A",
        slot_key="model:快递放置")
    assert new.action == "update"
    assert old.key in new.retired_keys
    assert new.record.content["family_id"] == old.content["family_id"]
    assert store.get_record(old.key).content["status"] == "superseded"
    assert [hit.record.key for hit in MemoryCurator(store).recall(
        "包裹放哪里", allowed_spaces=("private",))] == [new.record.key]
    assert store.get_record(other.key).content["status"] == "active"


def test_uncertain_delivery_change_stays_pending():
    store = InMemoryStore()
    facts = FactService(store)
    old = facts.remember("包裹放玄关", source="chat", evidence_id="m1",
                         space_id="private", subject_id="A").record
    candidate = facts.remember("包裹放楼下柜机", source="chat",
                               evidence_id="m2", space_id="private",
                               subject_id="A")
    assert candidate.action == "pending"
    assert store.get_record(old.key).content["status"] == "active"
    assert MemoryCurator(store).recall("包裹放哪里", allowed_spaces=("private",)) == ()


def test_delivery_correction_retires_legacy_alias_without_slot():
    store = InMemoryStore()
    curator = MemoryCurator(store)
    alias = curator.remember(
        "如果有小盒子到，先放玄关鞋柜上", source="old",
        evidence_id="m1", stable_fact=True, consent=True, space_id="private")
    target = curator.remember(
        "包裹先放玄关", source="old", evidence_id="m2",
        stable_fact=True, consent=True, space_id="private")
    result = FactService(store).correct(
        target.key, "快递改放楼下柜机，不要进屋",
        source="chat", evidence_id="m3")
    assert alias.key in result.retired_keys
    assert store.get_record(alias.key).content["status"] == "superseded"
    assert [hit.record.key for hit in curator.recall(
        "包裹放哪里", allowed_spaces=("private",))] == [result.record.key]
