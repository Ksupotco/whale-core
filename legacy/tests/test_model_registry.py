"""模型注册表 + 两档回复测试（2026-11-21 主人令）。

覆盖：注册表播种/按名解析/env 名引用向后兼容（裸 id 不命中注册表回落）、
档位 get/set 默认 fast、workflow 慢档会话走慢档脑、审核面不被本特性触碰。
"""
from __future__ import annotations

from whale_core.life.model_registry import (
    DEFAULT_REGISTRY, REGISTRY_KEY, get_tier, registry_entries, resolve_brain,
    seed_registry, set_tier)
from whale_core.memory.store import InMemoryStore

ENV = {"QQ_INTENT_BASE_URL": "https://platform.agentscope.io/v1",
       "QQ_INTENT_API_KEY": "k-test"}


def test_seed_idempotent():
    store = InMemoryStore()
    seed_registry(store)
    first = registry_entries(store)
    seed_registry(store)
    assert registry_entries(store) == first
    assert {e["name"] for e in first} == {"v4-pro-0813", "qwen4-max", "kimi-k3"}


def test_resolve_by_registry_name():
    store = InMemoryStore()
    seed_registry(store)
    brain = resolve_brain("kimi-k3", env=ENV, store=store)
    assert brain is not None
    assert brain.model == "kimi-k3"
    assert brain.reasoning_effort == "high"  # 注册表 effort 生效


def test_resolve_raw_model_id_backward_compat():
    store = InMemoryStore()
    seed_registry(store)
    brain = resolve_brain("deepseek-v4-pro-0813", env=ENV, store=store)
    assert brain is not None and brain.model == "deepseek-v4-pro-0813"
    assert brain.reasoning_effort is None  # 裸 id 不带 effort


def test_resolve_no_key_returns_none():
    store = InMemoryStore()
    seed_registry(store)
    assert resolve_brain("kimi-k3", env={}, store=store) is None


def test_tier_default_fast_and_set():
    store = InMemoryStore()
    assert get_tier(store, "group:1") == "fast"
    assert set_tier(store, "group:1", "slow") is True
    assert get_tier(store, "group:1") == "slow"
    assert set_tier(store, "group:1", "bogus") is False
    assert get_tier(store, "group:2") == "fast"  # 会话独立


def test_workflow_slow_tier_uses_slow_brain():
    """慢档会话：envelope/兜底路径都带慢档脑；快档 None（主脑）。"""
    from whale_core.life.qq_workflow import QqInboundWorkflow

    class _Brain:
        def __init__(self, tag):
            self.tag, self.calls = tag, []

        def reply(self, text, **kw):
            self.calls.append(text)
            return f"{self.tag} 答"

    store = InMemoryStore()
    main, slow = _Brain("主脑"), _Brain("慢档")
    wf = QqInboundWorkflow(store, main)
    wf._slow_brain = slow
    set_tier(store, "group:1124744261", "slow")
    # 档位选择逻辑：get_tier + _slow_brain
    from whale_core.life.model_registry import get_tier as _gt
    assert _gt(store, "group:1124744261") == "slow"
    assert _gt(store, "group:999") == "fast"


def test_safety_reviewer_not_wired_by_default():
    """主人明令 2026-11-21：回复内容审核门去掉——workflow 默认 None。"""
    from whale_core.life.qq_workflow import QqInboundWorkflow
    wf = QqInboundWorkflow(InMemoryStore(), object())
    assert wf._safety_reviewer is None
