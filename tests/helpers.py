"""Shared helpers for tests."""

from __future__ import annotations

from whale.app import App
from whale.models.base import Completion, ToolCall
from whale.models.scripted import ScriptedModel


def make_app(**config):
    app = App(None, config or None)
    return app


def call(name, args=None, cid="call_1"):
    return Completion(tool_calls=[ToolCall(cid, name.replace(".", "__"), args or {})], finish_reason="tool_calls")


def scripted(app, *script, role="chat"):
    model = ScriptedModel(list(script))
    app.models.overrides[role] = model
    return model
