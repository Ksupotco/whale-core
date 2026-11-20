"""Local OneBot host: paired inbound event reaches Whale and the reply port."""
from __future__ import annotations

import json
import hashlib
import hmac
import queue
from collections import deque

from examples.qq_onebot_local import (
    _OneLineOwnerPort, _QueuedBridge, _one_line, _plus_one_ready,
    _process_group, _process_owner,
)
from whale_core.life.channels import OwnerChannelRegistry
from whale_core.life.initiative import ProactiveDraft, ProactivePrefs
from whale_core.life.qq_onebot import OneBotOutboundPort
from whale_core.life.qq_workflow import QqInboundWorkflow
from whale_core.memory.store import InMemoryStore
from whale_core.profiles import assemble_runtime


def _event(sender: int, message_id: int = 10) -> dict:
    return {
        "post_type": "message", "message_type": "private", "user_id": sender,
        "self_id": 20002, "message_id": message_id,
        "message": [{"type": "text", "data": {"text": "你好"}}],
    }


def test_paired_onebot_message_reaches_reply_port():
    pending = queue.Queue()
    bridge = _QueuedBridge(
        OwnerChannelRegistry(owner_id="owner", bindings={("qq", "10001"): "owner"}),
        pending)
    headers = {"Authorization": "Bearer test-token"}
    for sender in (10001, 99999):
        status, _ = bridge.handle_http_request(
            headers, json.dumps(_event(sender)).encode(), expected_token="test-token")
        assert status == 204
    assert bridge.handle_http_request(
        {"Authorization": "Bearer wrong"}, json.dumps(_event(10001)).encode(),
        expected_token="test-token")[0] == 401
    assert pending.qsize() == 1

    class Brain:
        def reply(self, text, **kwargs):
            return "你好，我是鲸鱼。"

    class Transport:
        def __init__(self):
            self.calls = []

        def call(self, action, params):
            self.calls.append((action, params))
            return {"status": "ok", "retcode": 0}

    prefs = ProactivePrefs(
        enabled=True, allowed_channels=frozenset({"direct_message"}),
        auto_send=True, passive_reply_in_quiet=True)
    store = InMemoryStore()
    workflow = QqInboundWorkflow(store, Brain(), prefs=prefs)
    transport = Transport()
    port = OneBotOutboundPort(transport, user_id="10001", enabled=True)
    assert _process_owner(pending.get(), workflow, port, prefs) == "accepted"
    assert transport.calls[0][0] == "send_private_msg"
    assert transport.calls[0][1]["user_id"] == 10001


def test_openqq_signed_webhook_accepts_only_valid_signature():
    pending = queue.Queue()
    bridge = _QueuedBridge(
        OwnerChannelRegistry(owner_id="owner", bindings={("qq", "10001"): "owner"}),
        pending)
    body = json.dumps(_event(10001)).encode()
    signature = "sha1=" + hmac.new(b"test-token", body, hashlib.sha1).hexdigest()
    assert bridge.handle_http_request(
        {"X-Signature": signature}, body, expected_token="test-token",
        signature_secret="test-token")[0] == 204
    assert pending.qsize() == 1
    assert bridge.handle_http_request(
        {"X-Signature": "sha1=wrong"}, body, expected_token="test-token",
        signature_secret="test-token")[0] == 401
    assert pending.qsize() == 1


def test_onebot_rejects_events_for_wrong_logged_in_qq_account():
    pending = queue.Queue()
    bridge = _QueuedBridge(
        OwnerChannelRegistry(owner_id="owner", bindings={("qq", "10001"): "owner"}),
        pending, bot_qq="20002")
    correct = _event(10001)
    wrong = dict(correct, self_id=30003)
    assert bridge.handle_event(wrong, authenticated=True) is None
    assert pending.empty()
    assert bridge.handle_event(correct, authenticated=True) is not None
    assert pending.qsize() == 1


def test_onebot_pairing_uses_owner_memory_scope():
    bundle = assemble_runtime(
        "local", store=InMemoryStore(), use_saved_config=False,
        env={"LOCAL_MODEL_NAME": "test"},
        pairing_bindings={("qq", "10001"): "owner"})
    resolver = bundle.orchestrator.context_resolver
    owner = resolver.resolve(channel="qq", sender_id="10001",
                             conversation_id="private:10001")
    stranger = resolver.resolve(channel="qq", sender_id="99999",
                                conversation_id="private:99999")
    owner_in_group = resolver.resolve(channel="qq", sender_id="10001",
                                      conversation_id="group:30003")
    assert owner.principal.is_owner
    assert owner.allowed_memory_spaces
    assert not stranger.principal.is_owner
    assert not stranger.allowed_memory_spaces
    assert not owner_in_group.allowed_memory_spaces


def test_group_messages_require_allowed_group_and_matching_bot():
    pending = queue.Queue()
    bridge = _QueuedBridge(
        OwnerChannelRegistry(owner_id="owner", bindings={("qq", "10001"): "owner"}),
        pending, bot_qq="20002", group_ids={"30003"})
    event = {
        "post_type": "message", "message_type": "group", "group_id": 30003,
        "user_id": 40004, "self_id": 20002, "message_id": 10,
        "message": [{"type": "at", "data": {"qq": "20002"}},
                    {"type": "text", "data": {"text": "Jax，你怎么看？"}}],
    }
    for changed in (dict(event, group_id=30004), dict(event, self_id=50005)):
        bridge.handle_event(changed, authenticated=True)
    bridge.handle_event(event, authenticated=False)
    assert pending.empty()
    bridge.handle_event(event, authenticated=True)
    incoming, mentioned = pending.get_nowait()
    assert incoming.conversation == "group:30003"
    assert incoming.account == "40004"
    assert mentioned is True


def test_group_reply_sends_to_same_group():
    class Workflow:
        def handle_stranger_event(self, event):
            assert event.account == "40004"
            return {"reply": "我觉得这个设定有意思。"}

    class Transport:
        def __init__(self):
            self.calls = []

        def call(self, action, params):
            self.calls.append((action, params))
            return {"retcode": 0}

    incoming = type("Event", (), {"account": "40004", "conversation": "group:30003"})()
    transport = Transport()
    assert _process_group(incoming, Workflow(), transport) == (
        "sent", "我觉得这个设定有意思。")
    assert transport.calls == [
        ("send_group_msg", {"group_id": 30003, "message": "我觉得这个设定有意思。"})]


def test_plus_one_requires_two_consecutive_distinct_group_members():
    timeline = deque(maxlen=12)
    timeline.append({"sender_id": "a", "text": "+1", "is_self": False})
    assert not _plus_one_ready(timeline)
    timeline.append({"sender_id": "a", "text": "＋1", "is_self": False})
    assert not _plus_one_ready(timeline)
    timeline.append({"sender_id": "b", "text": "+1", "is_self": False})
    assert _plus_one_ready(timeline)
    timeline.append({"sender_id": "bot", "text": "+1", "is_self": True})
    assert not _plus_one_ready(timeline)
    timeline.append({"sender_id": "c", "text": "别的话题", "is_self": False})
    assert not _plus_one_ready(timeline)


def test_owner_qq_reply_is_sent_without_line_breaks():
    class Transport:
        def __init__(self):
            self.calls = []

        def call(self, action, params):
            self.calls.append((action, params))
            return {"retcode": 0}

    transport = Transport()
    port = _OneLineOwnerPort(transport, user_id="10001", enabled=True)
    draft = ProactiveDraft("reply-1", "第一句\n第二句\r\n第三句", "reply",
                           ("msg-1",), channel="direct_message")
    assert port.send(draft)
    assert transport.calls[0][1]["message"][0]["data"]["text"] == (
        "第一句 第二句 第三句")
    assert _one_line("第一句\n\n第二句") == "第一句 第二句"


def test_group_qq_reply_is_sent_without_line_breaks():
    class Workflow:
        def handle_stranger_event(self, event):
            return {"reply": "第一句\n第二句"}

    class Transport:
        def call(self, action, params):
            assert action == "send_group_msg"
            assert params["message"] == "第一句 第二句"
            return {"retcode": 0}

    event = type("Event", (), {"conversation": "group:30003"})()
    assert _process_group(event, Workflow(), Transport()) == (
        "sent", "第一句 第二句")
