"""Product regression for recalling an overnight owner conversation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from whale_core.core.companion import WhaleCompanion
from whale_core.memory.continuity import remember_episode
from whale_core.memory.curation import MemoryCurator
from whale_core.memory.recent_chat import recall_recent_owner_chat
from whale_core.memory.store import InMemoryStore, MemoryKind, MemoryRecord


NOW = datetime(2026, 3, 13, 2, 0, tzinfo=timezone.utc)  # 10:00 Shanghai
SPACE = "owner-working"
CHAT = "private:owner"


def _chat(store, words: str, *, at: datetime, conversation: str = CHAT,
          space: str = SPACE, answer: str = "好的") -> None:
    record = remember_episode(
        store, summary=f"用户：{words}\n助手：{answer}", tags=("chat",),
        extra={"conversation_id": conversation}, space_id=space)
    store.put_record(MemoryRecord(
        kind=record.kind, key=record.key, content=record.content,
        record_id=record.record_id, created_at=at, tags=record.tags,
        space_id=record.space_id))


def test_overnight_course_survives_many_later_turns_without_assistant_noise():
    store = InMemoryStore()
    course = "我明天上午要上纸艺结构入门。课堂上会做一座纸桥"
    _chat(store, course, at=datetime(2026, 3, 12, 16, 35, tzinfo=timezone.utc),
          answer="这门课是模型制作吗")
    for index in range(30):
        _chat(store, f"后面聊的别的事情{index}",
              at=NOW - timedelta(hours=2) + timedelta(minutes=index),
              answer="我没记住凌晨的课名")
    quotes = recall_recent_owner_chat(
        store, "我昨晚说的课叫什么？", conversation_id=CHAT,
        space_ids=(SPACE,), now=NOW)
    assert quotes and course in quotes[0]
    assert all("我没记住" not in quote for quote in quotes)
    variant = recall_recent_owner_chat(
        store, "我昨天凌晨提到的课名是什么", conversation_id=CHAT,
        space_ids=(SPACE,), now=NOW)
    assert variant and course in variant[0]


def test_pen_name_question_finds_earlier_owner_words():
    store = InMemoryStore()
    _chat(store, "这里叫我纸舟吧，其实我还有个笔名叫云格，第一次试写童话",
          at=NOW - timedelta(hours=10))
    for index in range(15):
        _chat(store, f"后续聊天{index}",
              at=NOW - timedelta(hours=2) + timedelta(minutes=index),
              answer="我不知道你的笔名")
    _chat(store, "我的笔名呢", at=NOW - timedelta(minutes=2),
          answer="这个真没有")
    quotes = recall_recent_owner_chat(
        store, "我的笔名呢", conversation_id=CHAT,
        space_ids=(SPACE,), now=NOW)
    assert quotes and "笔名叫云格" in quotes[0]
    assert all("我不知道" not in quote for quote in quotes)


def test_short_term_expiry_and_private_scope():
    store = InMemoryStore()
    _chat(store, "我上纸艺结构入门", at=NOW - timedelta(hours=73))
    _chat(store, "我上编织课", at=NOW - timedelta(hours=2),
          conversation="private:someone-else")
    _chat(store, "我上摄影课", at=NOW - timedelta(hours=2), space="other-space")
    assert recall_recent_owner_chat(
        store, "我之前说过什么课", conversation_id=CHAT,
        space_ids=(SPACE,), now=NOW) == ()
    assert recall_recent_owner_chat(
        store, "我之前说过什么课", conversation_id=CHAT,
        space_ids=(), now=NOW) == ()


def test_expired_chat_does_not_enter_ordinary_recent_context():
    from whale_core.memory.context import build_memory_context

    store = InMemoryStore()
    _chat(store, "我上纸艺结构入门", at=NOW - timedelta(hours=73))
    recent = store.list_by_kind(MemoryKind.EPISODE)
    ctx = build_memory_context(
        curator=MemoryCurator(store), query="继续聊聊",
        recent_episodes=recent, conversation_id=CHAT,
        allowed_spaces=(SPACE,), now=NOW)
    assert ctx.recent == ()


def test_real_companion_passes_owner_quote_to_model():
    class Brain:
        def reply(self, text, *, persona="", memories=(), history=(),
                  memory_context=None):
            self.context = memory_context
            return "纸艺结构入门"

    store, brain = InMemoryStore(), Brain()
    _chat(store, "我明天要上纸艺结构入门", at=NOW - timedelta(hours=9))
    for index in range(8):
        _chat(store, f"别的聊天{index}", at=NOW - timedelta(hours=2)
              + timedelta(minutes=index))
    context = SimpleNamespace(
        principal=SimpleNamespace(principal_id="owner", is_owner=True),
        allowed_memory_spaces=(SimpleNamespace(space_id=SPACE),),
        is_owner=True, persona_id=None)
    companion = WhaleCompanion(store, brain, curator=MemoryCurator(store))
    companion.chat("我昨晚说的课叫什么？", conversation_id=CHAT,
                   context=context, episodic=False, memory_now=NOW)
    assert brain.context.recalled_chat
    assert "纸艺结构入门" in brain.context.recalled_chat[0]
