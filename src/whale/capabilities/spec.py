"""Capabilities: everything the agent can do, from any provider.

A built-in tool, a plugin action, a paired phone's ``device.tap`` and a
client-declared ability all look the same to the agent: a name, a
description, a JSON-Schema of parameters and a risk class.
"""

from __future__ import annotations

import asyncio
import fnmatch
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

from ..kernel.errors import WhaleError
from ..kernel.events import Event, EventBus


class Risk(str, Enum):
    read = "read"            # no side effects outside the agent
    write = "write"          # changes the agent's own state (memory, tasks)
    external = "external"    # effects in the world: send a message, touch a device
    dangerous = "dangerous"  # shell, install, delete files


@dataclass(slots=True)
class CapabilitySpec:
    name: str
    description: str
    params: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    risk: Risk = Risk.read
    provider: str = "builtin"
    title: str = ""
    group: str = ""
    modes: tuple[str, ...] = ("chat", "goal", "explore")
    timeout_s: float = 60.0
    available: bool = True
    owner_only: bool = False  # only offered when the audience is the owner (never in groups / other people's chats)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "title": self.title or self.name, "description": self.description,
                "params": self.params, "risk": self.risk.value, "provider": self.provider,
                "group": self.group or self.name.split(".")[0], "modes": list(self.modes),
                "available": self.available, "owner_only": self.owner_only}

    def tool_schema(self) -> dict[str, Any]:
        # OpenAI function names cannot contain dots.
        return {"type": "function", "function": {"name": tool_name(self.name),
                                                 "description": self.description, "parameters": self.params}}


def tool_name(cap: str) -> str:
    return cap.replace(".", "__")


def cap_name(tool: str) -> str:
    return tool.replace("__", ".")


@dataclass(slots=True)
class ToolResult:
    status: str = "ok"           # ok | error | unknown
    data: Any = None
    summary: str = ""
    error: str = ""

    def to_model_text(self, limit: int = 6000) -> str:
        import json

        if self.status != "ok":
            body = {"status": self.status, "error": self.error, "data": self.data}
        else:
            body = self.data if self.data is not None else {"summary": self.summary}
        text = json.dumps(body, ensure_ascii=False, default=str)
        return text if len(text) <= limit else text[:limit] + "…(truncated)"

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "data": self.data, "summary": self.summary, "error": self.error}


@dataclass
class ToolContext:
    run_id: str = ""
    step_id: str = ""
    conversation_id: str = ""
    mode: str = "chat"
    principal: str = "owner"
    scope: str = "owner"      # memory audience of the run
    app: Any = None

    async def progress(self, message: str) -> None:
        if self.app is not None:
            await self.app.bus.emit("run.progress", {"run_id": self.run_id, "step_id": self.step_id,
                                                     "message": message})


Handler = Callable[[dict[str, Any], ToolContext], Awaitable[Any]]


def _check_params(schema: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    """Small structural validator: required keys, primitive types, enums, defaults."""
    if not isinstance(args, dict):
        raise WhaleError("invalid_params", "arguments must be an object")
    props = schema.get("properties", {})
    out = dict(args)
    for key, spec in props.items():
        if key not in out and "default" in spec:
            out[key] = spec["default"]
    for key in schema.get("required", []):
        if key not in out or out[key] in (None, ""):
            raise WhaleError("invalid_params", f"missing required parameter: {key}")
    types = {"string": str, "integer": int, "number": (int, float), "boolean": bool,
             "array": list, "object": dict}
    for key, value in list(out.items()):
        spec = props.get(key)
        if not spec or value is None:
            continue
        expected = types.get(spec.get("type", ""))
        if expected is int and isinstance(value, str) and value.lstrip("-").isdigit():
            out[key] = value = int(value)
        if expected is not None and not isinstance(value, expected):
            raise WhaleError("invalid_params", f"parameter {key} must be {spec.get('type')}")
        if "enum" in spec and value not in spec["enum"]:
            raise WhaleError("invalid_params", f"parameter {key} must be one of {spec['enum']}")
    return out


class CapabilityRegistry:
    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self._caps: dict[str, tuple[CapabilitySpec, Handler]] = {}
        self.stats: dict[str, dict[str, Any]] = {}

    def register(self, spec: CapabilitySpec, handler: Handler) -> None:
        self._caps[spec.name] = (spec, handler)
        self.bus.publish(Event("capability.registered", {"name": spec.name, "provider": spec.provider}))

    def unregister_provider(self, provider: str) -> list[str]:
        gone = [n for n, (s, _) in self._caps.items() if s.provider == provider]
        for n in gone:
            del self._caps[n]
        if gone:
            self.bus.publish(Event("capability.unregistered", {"provider": provider, "names": gone}))
        return gone

    def get(self, name: str) -> CapabilitySpec:
        if name not in self._caps:
            raise WhaleError("not_found", f"unknown capability: {name}")
        return self._caps[name][0]

    def has(self, name: str) -> bool:
        return name in self._caps

    def list(self, *, mode: str | None = None, pattern: str = "*") -> list[CapabilitySpec]:
        specs = [s for s, _ in self._caps.values() if fnmatch.fnmatchcase(s.name, pattern)]
        if mode:
            specs = [s for s in specs if mode in s.modes]
        return sorted(specs, key=lambda s: s.name)

    async def invoke(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        spec, handler = self._caps.get(name, (None, None))
        if spec is None or handler is None:
            return ToolResult("error", error=f"unknown capability: {name}")
        if not spec.available:
            return ToolResult("error", error=f"capability {name} is currently unavailable")
        stat = self.stats.setdefault(name, {"calls": 0, "errors": 0, "total_ms": 0.0})
        stat["calls"] += 1
        t0 = time.perf_counter()
        try:
            clean = _check_params(spec.params, args)
            raw = await asyncio.wait_for(handler(clean, ctx), timeout=spec.timeout_s)
        except asyncio.TimeoutError:
            stat["errors"] += 1
            # A timed-out external action may still have happened.
            status = "unknown" if spec.risk in (Risk.external, Risk.dangerous) else "error"
            return ToolResult(status, error=f"timed out after {spec.timeout_s}s")
        except WhaleError as exc:
            stat["errors"] += 1
            return ToolResult("error", error=exc.message, data=exc.details or None)
        except Exception as exc:  # tool bugs become observable errors, never crash the run
            stat["errors"] += 1
            return ToolResult("error", error=repr(exc))
        finally:
            stat["total_ms"] += (time.perf_counter() - t0) * 1000
        if isinstance(raw, ToolResult):
            return raw
        if isinstance(raw, str):
            return ToolResult("ok", data={"text": raw}, summary=raw[:200])
        summary = raw.get("summary", "") if isinstance(raw, dict) else ""
        return ToolResult("ok", data=raw, summary=str(summary)[:200])


def capability(registry: CapabilityRegistry, name: str, description: str, *, params: dict[str, Any] | None = None,
               risk: Risk = Risk.read, provider: str = "builtin", title: str = "", group: str = "",
               modes: tuple[str, ...] = ("chat", "goal", "explore"), timeout_s: float = 60.0,
               owner_only: bool = False):
    """Decorator form of ``registry.register``."""

    def deco(fn: Handler) -> Handler:
        registry.register(CapabilitySpec(name, description, params or {"type": "object", "properties": {}},
                                         risk, provider, title, group, modes, timeout_s, owner_only=owner_only), fn)
        return fn

    return deco


def obj(required: list[str] | None = None, **props: dict[str, Any]) -> dict[str, Any]:
    """Shorthand JSON Schema builder: obj(["q"], q=s("query"))."""
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


def s(desc: str, **kw: Any) -> dict[str, Any]:
    return {"type": "string", "description": desc, **kw}


def i(desc: str, **kw: Any) -> dict[str, Any]:
    return {"type": "integer", "description": desc, **kw}


def b(desc: str, **kw: Any) -> dict[str, Any]:
    return {"type": "boolean", "description": desc, **kw}
