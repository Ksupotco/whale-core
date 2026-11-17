from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from whale_core.core.companion import WhaleCompanion
from whale_core.memory.emotion import EmotionMemory, emotion_signal
from whale_core.memory.sqlite_store import SqliteMemoryStore
from whale_core.memory.store import InMemoryStore


def test_explicit_emotion_is_saved_as_broad_owner_event():
    store = InMemoryStore()

    class Brain:
        def reply(self, text, *, persona="", memories=(), history=()):
            return "愿意说说吗？"

    context = SimpleNamespace(
        principal=SimpleNamespace(principal_id="owner", is_owner=True),
        allowed_memory_spaces=None, persona_id=None,
    )
    WhaleCompanion(store, Brain()).chat(
        "我和小张分手了，很难过", conversation_id="private:owner",
        context=context)
    events = EmotionMemory(store).list_events("owner")
    assert len(events) == 1
    assert events[0]["emotion"] == "难过"
    assert events[0]["trigger"] == "亲密关系变化"
    assert "小张" not in str(events[0])


def test_two_independent_events_with_feedback_form_one_pattern():
    store = InMemoryStore()
    memory = EmotionMemory(store)
    first = memory.record(
        "owner", source_id="m1", conversation_id="c1",
        emotion="焦虑", trigger="学习或工作", need="陪伴倾听",
        response_style="先听我说完")
    memory.record(
        "owner", source_id="m2", conversation_id="c2",
        emotion="焦虑", trigger="学习或工作", need="陪伴倾听",
        response_style="先听我说完")
    assert memory.consolidate("owner") == ()
    assert memory.record_feedback(
        "owner", first, response_style="先听我说完",
        effective=True, kind="implicit")
    patterns = memory.consolidate("owner")
    assert len(patterns) == 1
    assert patterns[0]["inferred"] is True
    assert memory.relevant_patterns("owner", "论文有点急") == (
        "在学习或工作、需要陪伴倾听时，先听我说完曾有效",)
    assert memory.relevant_patterns("owner", "聊聊科幻") == ()
    assert memory.record_feedback(
        "owner", first, response_style="先听我说完", effective=False)
    assert memory.relevant_patterns("owner", "论文有点急") == ()
    assert memory.delete_event("owner", first)
    assert len(memory.list_events("owner")) == 1


def test_two_independent_implicit_negative_feedback_suspend_pattern():
    memory = EmotionMemory(InMemoryStore())
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    first = memory.record(
        "owner", source_id="m1", conversation_id="c1", emotion="焦虑",
        trigger="学习或工作", need="陪伴倾听", response_style="先听我说完",
        now=yesterday)
    second = memory.record(
        "owner", source_id="m2", conversation_id="c2", emotion="焦虑",
        trigger="学习或工作", need="陪伴倾听", response_style="先听我说完")
    memory.record_feedback(
        "owner", first, response_style="先听我说完", effective=True,
        kind="explicit")
    assert len(memory.consolidate("owner")) == 1
    memory.record_feedback(
        "owner", first, response_style="先听我说完", effective=False,
        kind="implicit")
    assert memory.relevant_patterns("owner", "论文有点急")
    memory.record_feedback(
        "owner", second, response_style="先听我说完", effective=False,
        kind="implicit")
    assert memory.relevant_patterns("owner", "论文有点急") == ()


def test_corrected_event_invalidates_derived_pattern():
    memory = EmotionMemory(InMemoryStore())
    first = memory.record(
        "owner", source_id="m1", conversation_id="c1", emotion="焦虑",
        trigger="学习或工作", need="陪伴倾听", response_style="先听我说完")
    memory.record(
        "owner", source_id="m2", conversation_id="c2", emotion="焦虑",
        trigger="学习或工作", need="陪伴倾听", response_style="先听我说完")
    memory.record_feedback(
        "owner", first, response_style="先听我说完", effective=True)
    assert len(memory.consolidate("owner")) == 1
    assert memory.update_event("owner", first, need="具体建议")
    assert memory.relevant_patterns("owner", "论文有点急") == ()


def test_owner_reply_reads_relevant_pattern():
    store = InMemoryStore()
    memory = EmotionMemory(store)
    first = memory.record(
        "owner", source_id="m1", conversation_id="c1",
        emotion="焦虑", trigger="学习或工作", need="陪伴倾听",
        response_style="先听我说完")
    memory.record(
        "owner", source_id="m2", conversation_id="c2",
        emotion="焦虑", trigger="学习或工作", need="陪伴倾听",
        response_style="先听我说完")
    memory.record_feedback(
        "owner", first, response_style="先听我说完", effective=True)
    memory.consolidate("owner")

    class Brain:
        def reply(self, text, *, persona="", memories=(), history=(),
                  memory_context=None):
            self.context = memory_context
            return "继续说吧"

    brain = Brain()
    context = SimpleNamespace(
        principal=SimpleNamespace(principal_id="owner", is_owner=True),
        allowed_memory_spaces=None, persona_id=None,
    )
    WhaleCompanion(store, brain).chat(
        "论文有点急", conversation_id="private:owner", context=context)
    assert brain.context.patterns == (
        "在学习或工作、需要陪伴倾听时，先听我说完曾有效",)
    WhaleCompanion(store, brain).chat(
        "论文有点急", conversation_id="group:1", context=context)
    assert brain.context.patterns == ()


def test_same_conversation_and_expired_events_do_not_promote():
    store = InMemoryStore()
    memory = EmotionMemory(store)
    old = datetime.now(timezone.utc) - timedelta(days=91)
    first = memory.record(
        "owner", source_id="m1", conversation_id="c1",
        emotion="难过", trigger="学习或工作", need="陪伴倾听",
        response_style="先听我说完", now=old)
    memory.record_feedback(
        "owner", first, response_style="先听我说完", effective=True)
    memory.record(
        "owner", source_id="m2", conversation_id="c1",
        emotion="难过", trigger="学习或工作", need="陪伴倾听",
        response_style="先听我说完")
    assert len(memory.list_events("owner")) == 1
    assert memory.consolidate("owner") == ()
    assert emotion_signal("我朋友很难过") is None
    assert emotion_signal("他很难过") is None


def test_same_private_chat_on_different_days_counts_twice():
    memory = EmotionMemory(InMemoryStore())
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    first = memory.record(
        "owner", source_id="m1", conversation_id="private:owner",
        emotion="难过", trigger="学习或工作", need="陪伴倾听",
        response_style="先听我说完", now=yesterday)
    memory.record(
        "owner", source_id="m2", conversation_id="private:owner",
        emotion="难过", trigger="学习或工作", need="陪伴倾听",
        response_style="先听我说完")
    memory.record_feedback(
        "owner", first, response_style="先听我说完", effective=True)
    assert len(memory.consolidate("owner")) == 1


def test_repeated_messages_in_one_day_do_not_count_twice():
    memory = EmotionMemory(InMemoryStore())
    first = memory.record(
        "owner", source_id="m1", conversation_id="private:owner",
        emotion="难过", trigger="学习或工作", need="陪伴倾听",
        response_style="先听我说完")
    memory.record(
        "owner", source_id="m2", conversation_id="private:owner",
        emotion="难过", trigger="学习或工作", need="陪伴倾听",
        response_style="先听我说完")
    memory.record_feedback(
        "owner", first, response_style="先听我说完", effective=True)
    assert memory.consolidate("owner") == ()


def test_emotion_event_uses_existing_sqlite_store():
    store = SqliteMemoryStore(":memory:")
    try:
        memory = EmotionMemory(store)
        event_id = memory.record(
            "owner", source_id="m1", conversation_id="private:owner",
            emotion="开心", trigger="", need="")
        assert memory.list_events("owner")[0]["event_id"] == event_id
        assert memory.delete_event("owner", event_id)
        assert memory.list_events("owner") == ()
    finally:
        store.close()


def test_weekly_review_prunes_old_events_and_runs_once_per_week():
    store = InMemoryStore()
    memory = EmotionMemory(store)
    now = datetime.now(timezone.utc)
    memory.record(
        "owner", source_id="old", conversation_id="private:owner",
        emotion="难过", now=now - timedelta(days=91))
    first = memory.record(
        "owner", source_id="m1", conversation_id="private:owner",
        emotion="难过", trigger="学习或工作", need="陪伴倾听",
        response_style="先听我说完", now=now - timedelta(days=2))
    memory.record(
        "owner", source_id="m2", conversation_id="private:owner",
        emotion="难过", trigger="学习或工作", need="陪伴倾听",
        response_style="先听我说完", now=now - timedelta(days=1))
    memory.record_feedback(
        "owner", first, response_style="先听我说完", effective=True)

    assert memory.review_if_due("owner", now=now) == {
        "expired_events": 1, "formed_patterns": 1}
    assert memory.review_if_due("owner", now=now + timedelta(days=1)) is None
    assert memory.review_if_due("owner", now=now + timedelta(days=7)) == {
        "expired_events": 0, "formed_patterns": 0}
