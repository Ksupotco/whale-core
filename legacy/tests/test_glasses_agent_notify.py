"""notify_user 工具测试（任务 B 定稿设计）。

覆盖：三渠道路由（mock 断言）、auto 路由（deliver 优先/回落 qq）、频控预算
（每回合≤3/每小时≤10/每日≤30，env 可调）、目标不可注入（文本里 target=…
一律忽略）、urgency 不提权（同路由只影响展示）、台账摘要 ≤200 字截断、
text>500 拒绝、空文本拒绝、自然语言形式（提醒我/通知我）、未装配 fail-closed。
全部离线：通道全 mock，store 用临时 sqlite。
"""
from __future__ import annotations

import json

import pytest

from whale_core.api.glasses_agent import (
    GlassesAgent, NotifyBudget, NotifyUserGateway, _parse_notify)
from whale_core.memory.sqlite_store import SqliteMemoryStore


@pytest.fixture()
def store(tmp_path):
    store = SqliteMemoryStore(str(tmp_path / "n.sqlite3"))
    yield store
    store.close()


class Channels:
    """三通道 mock：记录 (text)，按预设回执。"""

    def __init__(self, deliver_ok=True, qq_ok=True, phone_ok=True):
        self.calls = []
        self._ok = {"glasses": deliver_ok, "qq": qq_ok, "phone": phone_ok,
                    "auto": deliver_ok}

    def fn(self, name):
        def _send(text):
            self.calls.append((name, text))
            return self._ok[name], f"{name}-receipt"
        return _send

    def gateway(self):
        return NotifyUserGateway(deliver=self.fn("glasses"), qq=self.fn("qq"),
                                 phone=self.fn("phone"))


def make_agent(tmp_path, store, channels, **kw):
    records = []
    defaults = dict(
        store=store,
        chat=lambda text, cid: "回复",
        ledger=lambda r: records.append(r),
        workspace=str(tmp_path / "ws"),
        notify_gateway=channels.gateway(),
    )
    defaults.update(kw)
    agent = GlassesAgent(**defaults)
    return agent, records


# ---------- 网关路由 ----------

def test_three_channel_routing(store, tmp_path):
    ch = Channels()
    agent, _ = make_agent(tmp_path, store, ch)
    for channel in ("glasses", "qq", "phone"):
        resp = agent.handle_turn(f"notify[channel={channel}]:记得喝水", "s1")
        assert resp["actions"][0]["status"] == "ok"
    assert [name for name, _ in ch.calls] == ["glasses", "qq", "phone"]
    assert all(text == "记得喝水" for _, text in ch.calls)


def test_auto_routing_prefers_deliver(store, tmp_path):
    ch = Channels()
    agent, _ = make_agent(tmp_path, store, ch)
    resp = agent.handle_turn("notify:auto 默认路由", "s1")
    assert resp["actions"][0]["status"] == "ok"
    assert ch.calls == [("glasses", "auto 默认路由")]  # deliver_result 路径
    assert "auto:deliver_result" in resp["actions"][0]["output_digest"]


def test_auto_falls_back_to_qq(store, tmp_path):
    ch = Channels(deliver_ok=False)
    agent, _ = make_agent(tmp_path, store, ch)
    resp = agent.handle_turn("notify:deliver 挂了", "s1")
    assert resp["actions"][0]["status"] == "ok"
    assert [name for name, _ in ch.calls] == ["glasses", "qq"]


def test_channel_failure_reports_fail(store, tmp_path):
    ch = Channels(qq_ok=False)
    agent, _ = make_agent(tmp_path, store, ch)
    resp = agent.handle_turn("notify[channel=qq]:发不出", "s1")
    assert resp["actions"][0]["status"] == "fail"
    assert "没发出去" in resp["reply"]


def test_unknown_channel_rejected(store, tmp_path):
    ch = Channels()
    gw = ch.gateway()
    out = gw.send(text="x", channel="pigeon", urgency="normal")
    assert out["ok"] is False and "unknown channel" in out["detail"]


# ---------- 目标不可注入 / urgency 不提权 ----------

def test_target_param_ignored_not_injectable(store, tmp_path):
    """notify[target=someone-else,channel=qq]:… —— target 键一律忽略，
    仍只发宿主绑定对端（mock 断言通道 callable 只收到 text）。"""
    ch = Channels()
    agent, _ = make_agent(tmp_path, store, ch)
    resp = agent.handle_turn(
        "notify[target=10001,to=evil,channel=qq]:试试注入", "s1")
    assert resp["actions"][0]["status"] == "ok"
    assert ch.calls == [("qq", "试试注入")]  # 只有文本到通道，无 target


def test_urgency_no_privilege_same_route(store, tmp_path):
    ch = Channels()
    agent, _ = make_agent(tmp_path, store, ch)
    low = agent.handle_turn("notify[urgency=low]:低", "s1")
    high = agent.handle_turn("notify[urgency=high]:高", "s1")
    assert low["actions"][0]["status"] == high["actions"][0]["status"] == "ok"
    # urgency 出现在摘要展示，不改变通道与权限路径
    assert "high" in high["actions"][0]["summary"]
    assert [name for name, _ in ch.calls] == ["glasses", "glasses"]


# ---------- 文本校验 ----------

def test_text_over_500_rejected(store, tmp_path):
    ch = Channels()
    agent, records = make_agent(tmp_path, store, ch)
    resp = agent.handle_turn("notify:" + "长" * 501, "s1")
    assert resp["actions"][0]["status"] == "fail"
    assert ch.calls == []
    assert records[-1]["verdict"] == "fail" and "too long" in records[-1]["result"]


def test_text_500_accepted(store, tmp_path):
    ch = Channels()
    agent, _ = make_agent(tmp_path, store, ch)
    resp = agent.handle_turn("notify:" + "界" * 500, "s1")
    assert resp["actions"][0]["status"] == "ok"


def test_empty_text_rejected(store, tmp_path):
    ch = Channels()
    agent, _ = make_agent(tmp_path, store, ch)
    resp = agent.handle_turn("notify:", "s1")
    assert resp["actions"][0]["status"] == "fail"
    assert "空" in resp["reply"]
    assert ch.calls == []


def test_gateway_not_wired_fail_closed(store, tmp_path):
    records = []
    agent = GlassesAgent(store=store, ledger=lambda r: records.append(r),
                         workspace=str(tmp_path))
    resp = agent.handle_turn("notify:没接线", "s1")
    assert resp["actions"][0]["status"] == "fail"
    assert "未装配" in resp["reply"]


# ---------- 频控预算 ----------

def test_hour_quota_blocks_and_ledgers(store, tmp_path):
    ch = Channels()
    budget = NotifyBudget(store, per_turn=3, per_hour=2, per_day=30)
    agent, records = make_agent(tmp_path, store, ch, notify_budget=budget)
    assert agent.handle_turn("notify:一", "s1")["actions"][0]["status"] == "ok"
    assert agent.handle_turn("notify:二", "s1")["actions"][0]["status"] == "ok"
    resp = agent.handle_turn("notify:三", "s1")  # 第三发条撞小时窗
    assert resp["actions"][0]["status"] == "fail"
    assert "hour quota" in resp["actions"][0]["output_digest"]
    assert len(ch.calls) == 2
    assert records[-1]["verdict"] == "fail"


def test_day_quota_blocks(store, tmp_path):
    ch = Channels()
    budget = NotifyBudget(store, per_turn=3, per_hour=0, per_day=1)
    agent, _ = make_agent(tmp_path, store, ch, notify_budget=budget)
    assert agent.handle_turn("notify:首日一", "s1")["actions"][0]["status"] == "ok"
    resp = agent.handle_turn("notify:首日二", "s1")
    assert resp["actions"][0]["status"] == "fail"
    assert "day quota" in resp["actions"][0]["output_digest"]


def test_turn_quota_blocks(store, tmp_path):
    # 规则分类每回合至多 1 次 notify，per-turn 上限语义直接测预算对象
    budget = NotifyBudget(store, per_turn=1, per_hour=0, per_day=0)
    assert budget.check("s1", turn_used=0) is None
    assert "turn quota" in budget.check("s1", turn_used=1)


def test_env_tunable_budgets(store, tmp_path):
    ch = Channels()
    agent, _ = make_agent(tmp_path, store, ch,
                          env={"WHALE_NOTIFY_PER_DAY": "1",
                               "WHALE_NOTIFY_PER_HOUR": "0"})
    assert agent.handle_turn("notify:env 一", "s1")["actions"][0]["status"] == "ok"
    assert agent.handle_turn("notify:env 二", "s1")["actions"][0]["status"] == "fail"


def test_budget_per_session_isolated(store, tmp_path):
    ch = Channels()
    budget = NotifyBudget(store, per_turn=3, per_hour=1, per_day=0)
    agent, _ = make_agent(tmp_path, store, ch, notify_budget=budget)
    assert agent.handle_turn("notify:甲", "sA")["actions"][0]["status"] == "ok"
    # 另一会话独立窗口
    assert agent.handle_turn("notify:乙", "sB")["actions"][0]["status"] == "ok"


# ---------- 台账摘要截断 ----------

def test_ledger_summary_truncated(store, tmp_path):
    ch = Channels()
    agent, records = make_agent(tmp_path, store, ch)
    agent.handle_turn("notify:" + "x" * 400, "s1")
    blob = json.dumps(records, ensure_ascii=False)
    assert "x" * 200 not in blob  # 摘要不记全文
    assert records[-1]["intent"] == "notify"
    assert all(len(str(v)) <= 200 for v in records[-1].values())


# ---------- 解析与自然语言形式 ----------

@pytest.mark.parametrize("text,expected", [
    ("notify:买牛奶", ("auto", "normal", "买牛奶")),
    ("notify[channel=phone]:带伞", ("phone", "normal", "带伞")),
    ("notify[urgency=high]:看锅", ("auto", "high", "看锅")),
    ("notify[channel=qq,urgency=low]:低优", ("qq", "low", "低优")),
    ("提醒我下午三点开会", ("auto", "normal", "下午三点开会")),
    ("通知我快递到了", ("auto", "normal", "快递到了")),
    ("今天天气如何", None),
])
def test_parse_notify(text, expected):
    assert _parse_notify(text) == expected
