"""owner 显式记忆指令（记住/忘掉）的共享处理：QQ 工作流与 Runtime 共用。

owner 直聊的显式指令即 consent（单 owner 陪伴场景，
docs/archive/2026-10-30/gap-analysis-2026-10-30.md §4 R4）；证据链指向触发消息，
记忆可经「忘掉」或删除语义移除。
"""
from __future__ import annotations

from whale_core.memory.curation import MemoryCurator
from whale_core.memory.facts import FactService
from whale_core.memory.store import MemoryStore

REMEMBER_PREFIXES: tuple[str, ...] = ("记住：", "记住:", "记住 ")
FORGET_PREFIXES: tuple[str, ...] = ("忘掉：", "忘掉:", "忘记：", "忘记:", "忘掉 ", "忘记 ")
CORRECT_PREFIXES: tuple[str, ...] = ("更正：", "更正:", "更正 ")


def handle_memory_command(
    store: MemoryStore,
    text: str,
    *,
    source: str,
    evidence_id: str,
    curator: MemoryCurator | None = None,
    space_id: str = "",
    allowed_spaces=None,
) -> str | None:
    """识别并执行记忆指令，返回确定性回复文本；非指令返回 None。

    - 记住：<事实> → curated 稳定事实（consent=True；stable_fact=True 不衰减）；
    - 忘掉/忘记：<线索> → 词项召回最相关的一条 curated 记忆并删除；
      只删 curated 记录（curator.forget 语义），聊天情节不受影响。

    S2：``space_id`` 为写入目标空间（调用方按 ResolvedContext 授权决定；
    空串 = 未分区兼容行为）。``allowed_spaces`` 限定「忘掉」的检索范围
    （不能跨授权空间匹配删除目标）。
    """
    text = (text or "").strip()
    if not text:
        return None
    cur = curator or MemoryCurator(store)
    facts = FactService(store)
    for prefix in REMEMBER_PREFIXES:
        if text.startswith(prefix):
            fact = text[len(prefix):].strip()
            if not fact:
                return "想让我记住什么？"
            # 主体归属（生产实证 2026-11-01：裸文本「我最喜欢的…」被模型
            # 误读成鲸鱼自己的偏好）。摘要带说话人，模型才能正确归因。
            summary = f"用户自述：{fact}"
            result = facts.remember(
                summary, source=source, evidence_id=evidence_id, space_id=space_id)
            if result.action == "preserve":
                return f"这个我记着呢：{fact[:50]}"
            return f"好，我记住啦：{fact[:50]}"
    for prefix in CORRECT_PREFIXES:
        if text.startswith(prefix):
            body = text[len(prefix):].strip()
            separator = next((mark for mark in ("->", "→", "改成", "改为")
                              if mark in body), "")
            if not separator:
                return "请写成：更正：旧内容 -> 新内容"
            old, new = (part.strip() for part in body.split(separator, 1))
            if not old or not new:
                return "请写成：更正：旧内容 -> 新内容"
            target = facts.find_exact(
                f"用户自述：{old}", allowed_spaces=allowed_spaces)
            if target is None:
                return "没有找到要更正的记忆诶"
            result = facts.correct(
                target.key, f"用户自述：{new}",
                source=source, evidence_id=evidence_id)
            if result.action == "preserve":
                return f"这个我记着呢：{new[:50]}"
            return f"已更正：{new[:50]}"
    for prefix in FORGET_PREFIXES:
        if text.startswith(prefix):
            clue = text[len(prefix):].strip()
            if not clue:
                return "想让我忘掉什么？"
            from whale_core.memory.formation import cancel_pending
            cancelled = cancel_pending(
                store, clue, allowed_spaces=allowed_spaces)
            from whale_core.core.companion import _recall
            hits = _recall(cur, clue, allowed_spaces, limit=1)
            if hits and (facts.forget(hits[0].record.key)
                         if hits[0].record.content.get("stable_fact")
                         else cur.forget(hits[0].record.key)):
                summary = str(hits[0].record.content.get("summary", ""))
                return f"已忘掉：{summary[:50]}"
            if cancelled:
                return "已忘掉待写入的记忆"
            return "没有找到相关的记忆诶"
    return None
