"""Model abstraction: one protocol for every chat provider."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

DeltaFn = Callable[[str], Awaitable[None] | None]


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

    def to_openai(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": json.dumps(self.arguments, ensure_ascii=False)},
        }


@dataclass(slots=True)
class Message:
    role: str  # system | user | assistant | tool
    content: str | list[dict[str, Any]] = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None

    def to_openai(self) -> dict[str, Any]:
        out: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            out["tool_calls"] = [c.to_openai() for c in self.tool_calls]
            if not self.content:
                out["content"] = None
        if self.tool_call_id:
            out["tool_call_id"] = self.tool_call_id
        if self.name and self.role != "tool":
            out["name"] = self.name
        return out

    @property
    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        return "".join(p.get("text", "") for p in self.content if p.get("type") == "text")


@dataclass(slots=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass(slots=True)
class Completion:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    model: str = ""
    usage: Usage = field(default_factory=Usage)
    reasoning: str = ""


class ChatModel(Protocol):
    name: str

    async def complete(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        on_delta: DeltaFn | None = None,
        json_mode: bool = False,
    ) -> Completion: ...


class EmbeddingModel(Protocol):
    name: str
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class ModelError(Exception):
    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None) -> None:
        self.args = (message,)
        self.retryable = retryable
        self.status = status


def tool_schema(name: str, description: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": params}}


def parse_json_loose(text: str) -> Any:
    """Parse JSON from a model reply that may wrap it in prose or code fences."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        text = text.rsplit("```", 1)[0]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start, end = text.find(open_ch), text.rfind(close_ch)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON found in model output")
