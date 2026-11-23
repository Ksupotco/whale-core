"""提示词去规定化回归（owner 2026-11-18 决策）。

口径：prompt 只保留性格注入 + 记忆 + 必要工具注入 + owner 明留红线
（诚实/表白防误认/数据风险确认）；一切规定回复方式/格式/语气的指令块
不再出现。人味由角色卡自然驱动，不做回复格式断言（群实测主观反馈兜底）。

黑名单逐条对应被删的 prescribe 句；白名单断言协议/事实/红线仍在。
"""
from __future__ import annotations

from whale_core.life.response_planner import PLANNING_HINT
import whale_core.life.response_planner as response_planner
from whale_core.life.expression import derive_policy
from whale_core.life.relationship import RelationshipState
from whale_core.life.activity import render_activity_hint
from whale_core.memory.store import InMemoryStore

#: 被删的 prescribe 句（黑名单——任何一条重现即回归）
BANNED = (
    "一个气泡说一件事",       # PLANNING_HINT 方式句
    "不要每轮都提问",         # 反模板纪律句
    "这轮气氛轻松",           # 语用意图 play
    "这轮适合认真回应",       # answer
    "对方在分享自己的事",     # share
    "别硬找话题",             # wrap
    "顺手接上",               # continue
    "先回应当下的处境",       # anxious 节奏
    "一次只给一个问题",       # one_step
    "大白话",                 # skill novice
    "完整技术解释",           # skill expert
    "共情一句就够",           # care_health
    "接话节奏参考",           # peer_median
    "当前适合简短回应",       # length short
    "自然处着",               # relationship 软引导
    "别主动汇报",             # activity 尾句
)


def _all_prompt_texts() -> dict:
    """汇总各注入面在强信号下的渲染文本。"""
    policy = derive_policy(
        "电脑卡死了！！我感冒了好难受，论文还没保存，怎么办？",
        is_group=True, self_facts=("我是电脑小白",), peer_median=24)
    stranger = RelationshipState(
        principal_id="u1", familiarity="stranger", interaction_count=0)
    familiar = RelationshipState(
        principal_id="u1", familiarity="familiar", interaction_count=42,
        shared_topics=("模拟电路",), address_as="老王")
    store = InMemoryStore()
    store.put("task:t1", {"task_id": "t1", "phase": "succeeded",
                          "goal": "画一只鲸鱼", "at": 1.0})
    store.put("runtime:task:t1", {"conversation_id": "c1"})
    store.put("tasks:recent", ["t1"])
    return {
        "PLANNING_HINT": PLANNING_HINT,
        "expression.render": policy.render(),
        "relationship.stranger": stranger.render(),
        "relationship.familiar": familiar.render(),
        "activity_hint": render_activity_hint(store, "c1"),
    }


def test_no_prescription_strings_anywhere():
    for name, text in _all_prompt_texts().items():
        for banned in BANNED:
            assert banned not in text, f"{name} 重现 prescribe 句：{banned}"


def test_marker_protocol_intact():
    """标记协议（解析器契约）保留：◆ 分泡 / [[沉默]] / [[稍后补充]]。"""
    assert "◆" in PLANNING_HINT
    assert "[[沉默]]" in PLANNING_HINT
    assert "[[稍后补充" in PLANNING_HINT


def test_pragmatic_intent_removed():
    assert not hasattr(response_planner, "derive_pragmatic_intent")
    assert not hasattr(response_planner, "PragmaticIntent")


def test_owner_redlines_kept():
    """owner 明留：表白防误认 + 数据风险确认仍渲染。"""
    confession = derive_policy("我爱你", is_group=True)
    assert "不要认领成对自己的表白" in confession.render()
    data_risk = derive_policy("电脑卡死了，论文还没保存", is_group=False)
    assert "保存重要文件" in data_risk.render()


def test_facts_kept():
    texts = _all_prompt_texts()
    assert "尚无互动记录" in texts["relationship.stranger"]
    assert "42 次互动记录" in texts["relationship.familiar"]
    assert "模拟电路" in texts["relationship.familiar"]
    assert "画一只鲸鱼" in texts["activity_hint"]
    assert "按真实状态说" in texts["activity_hint"]


def test_expression_metadata_fields_still_computed():
    """信号字段（元数据，供 trace/宿主）仍计算——只删 prompt 渲染。"""
    policy = derive_policy("我感冒了", is_group=True, peer_median=24)
    assert policy.care_health is True and policy.peer_median == 24
    assert policy.render() == ""
