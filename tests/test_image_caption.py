"""caption-first 测试（2026-11-19 Tob 定）：入站一次成文、缓存不重复看、
回复默认文字描述、[[看原图]] 升级一轮、私聊/无描述路径不回归。"""
from __future__ import annotations

import time

from whale_core.life.image_caption import (
    ESCALATE_MARK, caption_for, caption_key, captions_note)
from whale_core.memory.store import InMemoryStore
from whale_core.memory.timeline import GroupTimelineStore

PNG = b"\x89PNG-fake"


class _VisionBrain:
    def __init__(self, out="图里是一只猫"):
        self.calls = []
        self.out = out

    def reply(self, text, **kw):
        self.calls.append(kw)
        return self.out


def test_caption_cached_by_hash():
    store = InMemoryStore()
    brain = _VisionBrain()
    a = caption_for(store, brain, PNG, "image/png")
    b = caption_for(store, brain, PNG, "image/png")
    assert a == b == "图里是一只猫"
    assert len(brain.calls) == 1  # 同图不重复看


def test_caption_failure_returns_empty():
    class _Boom:
        def reply(self, text, **kw):
            raise RuntimeError("x")
    store = InMemoryStore()
    assert caption_for(store, _Boom(), PNG, "image/png") == ""
    assert store.get(caption_key(PNG)) is None


def test_captions_note_shape():
    note = captions_note(["一只猫", ""])
    assert "一只猫" in note and ESCALATE_MARK in note
    assert captions_note([]) == "" and captions_note(["", None]) == ""


# ---------- companion defer/escalate ----------

from whale_core.core.companion import WhaleCompanion


class _DeferBrain:
    def __init__(self, out_no_img=None):
        self.calls = []
        self.out_no_img = out_no_img

    def reply(self, text, *, persona="", memories=(), history=(), images=()):
        self.calls.append({"text": text, "images": list(images)})
        if not images:
            return self.out_no_img or f"文字不够用 {ESCALATE_MARK}"
        return "看到了，图里是一只猫"


def test_defer_images_escalates_once_on_marker():
    store = InMemoryStore()
    brain = _DeferBrain()
    comp = WhaleCompanion(store, brain)
    out = comp.chat("这图是啥", conversation_id="c1",
                    images=[(PNG, "image/png")], defer_images=True)
    assert len(brain.calls) == 2
    assert brain.calls[0]["images"] == []            # 首轮不带图
    assert brain.calls[1]["images"] == [(PNG, "image/png")]  # 升级带图
    assert ESCALATE_MARK not in out and "看到了" in out  # 标记不进最终回复


def test_defer_images_no_marker_no_second_call():
    store = InMemoryStore()
    brain = _DeferBrain(out_no_img="描述够用了")  # 首轮不带标
    comp = WhaleCompanion(store, brain)
    out = comp.chat("这图是啥", conversation_id="c2",
                    images=[(PNG, "image/png")], defer_images=True)
    assert out == "描述够用了"
    assert len(brain.calls) == 1 and brain.calls[0]["images"] == []


def test_no_defer_images_pass_through():
    store = InMemoryStore()
    brain = _DeferBrain()
    brain.out = "直接看"
    comp = WhaleCompanion(store, brain)
    comp.chat("图", conversation_id="c3",
              images=[(PNG, "image/png")], defer_images=False)
    assert brain.calls[0]["images"] == [(PNG, "image/png")]


# ---------- workflow caption 上下文 ----------

def test_caption_context_defers_when_all_captioned():
    from whale_core.life.channels import OwnerEvent
    from whale_core.life.qq_workflow import QqInboundWorkflow
    store = InMemoryStore()
    store.put(caption_key(PNG), "一只猫")
    wf = QqInboundWorkflow(store, None)
    note, defer = wf._caption_context(((PNG, "image/png"),))
    assert defer is True and "一只猫" in note


def test_caption_context_passthrough_when_uncaptioned():
    from whale_core.life.qq_workflow import QqInboundWorkflow
    store = InMemoryStore()
    wf = QqInboundWorkflow(store, None)
    note, defer = wf._caption_context(((PNG, "image/png"),))
    assert (note, defer) == ("", False)  # 无描述 → 原图照常进（不降可见性）
