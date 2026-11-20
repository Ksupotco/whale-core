"""send_to 跨渠道定向发送测试（2026-11-19 Tob 派薄层）。

门控：群∈allowed_groups 白名单（非白名单拒）、非 owner 私聊须 pending
两阶段批准、owner 直发、未知 target 诚实报错、回执回模型、fail-soft。
notify 三通道语义不动。
"""
from __future__ import annotations

from whale_core.api.glasses_agent import GlassesAgent


class FakeSend:
    def __init__(self, ok=True):
        self.calls = []
        self.ok = ok

    def __call__(self, peer_type, target, text):
        self.calls.append((peer_type, target, text))
        return self.ok, "sent" if self.ok else "send_failed"


def make_agent(_unused=None, **kw):
    records = []
    defaults = dict(
        chat=lambda text, cid: "回复",
        ledger=lambda r: records.append(r),
        workspace="/tmp/sendto-ws",
        send_target=FakeSend(),
        allowed_groups=("1124744261", "961459653"),
        owner_uid="u_owner",
    )
    defaults.update(kw)
    return GlassesAgent(**defaults), records


def test_owner_direct():
    agent, _ = make_agent(None)
    resp = agent.handle_turn("send_to:owner:今晚早点睡", "s1")
    assert resp["actions"][0]["status"] == "ok"
    assert agent.send_target.calls == [("c2c", "u_owner", "今晚早点睡")]


def test_group_whitelist_direct():
    agent, _ = make_agent(None)
    resp = agent.handle_turn("send_to:group:1124744261:大家好", "s1")
    assert resp["actions"][0]["status"] == "ok"
    assert agent.send_target.calls == [("group", "1124744261", "大家好")]


def test_group_not_whitelist_denied():
    agent, records = make_agent(None)
    resp = agent.handle_turn("send_to:group:9999:大家好", "s1")
    assert resp["actions"][0]["status"] == "denied"
    assert "白名单" in resp["reply"]
    assert agent.send_target.calls == []
    assert records[-1]["verdict"] == "denied"


def test_private_owner_direct():
    agent, _ = make_agent(None)
    resp = agent.handle_turn("send_to:private:u_owner:自言自语", "s1")
    assert resp["actions"][0]["status"] == "ok"
    assert agent.send_target.calls == [("c2c", "u_owner", "自言自语")]


def test_private_non_owner_pending_two_phase():
    """非 owner 私聊：先 pending（未发），同 sessionId+confirmId 确认才发。"""
    agent, _ = make_agent(None)
    resp = agent.handle_turn("send_to:private:u_friend:周末来吗", "s1")
    assert resp["actions"][0]["status"] == "held"
    assert resp["pending"]["kind"] == "send"
    assert agent.send_target.calls == []                     # 未确认未发
    cid = resp["pending"]["confirmId"]
    resp2 = agent.handle_turn("", "s1", confirm_id=cid)
    assert resp2["actions"][0]["status"] == "ok"
    assert agent.send_target.calls == [("c2c", "u_friend", "周末来吗")]


def test_unknown_target_honest_error():
    agent, _ = make_agent(None)
    resp = agent.handle_turn("send_to:mars:你好", "s1")
    assert resp["actions"][0]["status"] == "fail"
    assert "未知目标" in resp["reply"] or "支持" in resp["reply"]
    assert agent.send_target.calls == []


def test_send_failure_fail_soft():
    agent, _ = make_agent(None, send_target=FakeSend(ok=False))
    resp = agent.handle_turn("send_to:owner:在吗", "s1")
    assert resp["actions"][0]["status"] == "fail"
    assert "没发出去" in resp["reply"]


def test_receipt_summary_in_ledger():
    agent, records = make_agent(None)
    agent.handle_turn("send_to:owner:记一笔", "s1")
    assert records[-1]["intent"] == "send"
    assert records[-1]["verdict"] == "ok"
    assert len(str(records[-1]["result"])) <= 200
