import json
from threading import RLock
from types import SimpleNamespace

from whale_core.api.studio.portrait import _list as list_portrait

from whale_core.memory.continuity import remember_episode
from whale_core.memory.emotion import EmotionMemory
from whale_core.memory.person_model import (
    PersonModel, enqueue_review_if_due, run_pending_reviews,
)
from whale_core.memory.store import InMemoryStore


class ReviewModel:
    def __init__(self, answer):
        self.answer = answer
        self.calls = 0

    def reply(self, text, *, persona="", memories=(), history=()):
        self.calls += 1
        self.material = json.loads(text)
        return json.dumps(self.answer, ensure_ascii=False)


def test_weekly_review_uses_evidence_and_allows_correction():
    store = InMemoryStore()
    for key, text in (("ep:1", "科幻作品的世界观很有趣"),
                      ("ep:2", "继续讨论科幻设定")):
        remember_episode(store, summary=f"用户：{text}\nEnxi：我想到另一种设定",
                         key=key, tags=("chat",), space_id="owner")
    emotion = EmotionMemory(store)
    event_id = emotion.record("owner", source_id="ep:1", conversation_id="chat:1",
                              emotion="开心", trigger="作品讨论", need="共同讨论")
    model = ReviewModel({
        "feedback": [{"event_id": event_id, "response_style": "提出自己的观点",
                      "effective": True}],
        "profiles": [{"context": "科幻作品", "need": "共同讨论",
                      "effective_response": "提出自己的观点", "expression_preference": "自然",
                      "evidence_ids": ["ep:1", "ep:2"]}],
        "interests": [{"context": "科幻", "topic": "天文",
                       "evidence_ids": ["ep:1"]}],
    })
    assert enqueue_review_if_due(store, "owner", "owner")
    assert run_pending_reviews(store, model) == 1
    assert model.calls == 1
    assert len(model.material["dialogues"]) == 2
    assert emotion.list_events("owner")[0]["feedback_kind"] == "implicit"
    portrait = PersonModel(store)
    items = portrait.list_items("owner")
    assert len(items) == 2
    interaction = next(item for item in items if item["kind"] == "interaction")
    assert portrait.update_item("owner", interaction["item_id"],
                                effective_response="先一起分析")
    portrait.put_item("owner", kind="interaction", context="科幻作品",
                      need="共同讨论", effective_response="直接推荐",
                      evidence_ids=["ep:1", "ep:2"])
    assert next(item for item in portrait.list_items("owner")
                if item["kind"] == "interaction")["effective_response"] == "先一起分析"
    assert portrait.delete_item("owner", interaction["item_id"])
    assert not portrait.relevant_hints("owner", "科幻作品") or all(
        "先一起分析" not in hint for hint in portrait.relevant_hints("owner", "科幻作品"))
    assert not enqueue_review_if_due(store, "owner", "owner")


def test_sensitive_dialogue_not_sent_to_review_model():
    store = InMemoryStore()
    remember_episode(store, summary="用户：我和男朋友分手很难过\nEnxi：我在",
                     key="ep:sensitive", tags=("chat",), space_id="owner")
    remember_episode(store, summary="用户：喜欢科幻\nEnxi：我也喜欢",
                     key="ep:safe", tags=("chat",), space_id="owner")
    assert not enqueue_review_if_due(store, "owner", "owner")


def test_portrait_page_returns_evidence_and_owner_events():
    store = InMemoryStore()
    remember_episode(store, summary="用户：我好开心\nEnxi：真不错",
                     key="ep:joy", tags=("chat",), space_id="owner")
    EmotionMemory(store).record("owner", source_id="ep:joy",
                                conversation_id="private:owner", emotion="开心")
    PersonModel(store).put_item("owner", kind="interest", context="科幻",
                                topic="天文", evidence_ids=["ep:joy"])

    class Handler:
        def _send(self, status, payload):
            self.status = status
            self.payload = json.loads(payload)

    handler = Handler()
    list_portrait(handler, SimpleNamespace(store=store, lock=RLock()), None)
    assert handler.status == 200
    owner = handler.payload["result"]["owners"][0]
    assert owner["events"][0]["source_text"].startswith("用户：我好开心")
    assert owner["profiles"][0]["inferred"] is True
