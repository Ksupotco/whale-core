"""Link protocol v1: one envelope, one method registry, every client.

HTTP (``POST /api/rpc/{method}``) and WebSocket (``/api/link``) dispatch to
the same handlers, so the console, the phone and an IO bridge see identical
data structures. ``GET /api/schema`` exports every method's parameter schema.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ValidationError

from ..capabilities.spec import CapabilitySpec, Risk, ToolContext
from ..kernel.errors import WhaleError
from ..kernel.ids import now_ms, ulid

log = logging.getLogger("whale.link")

PROTOCOL = "1.0"

# Per-role method allowlists (owner 令牌即全量权限，角色列管把「客户端令牌泄露 =
# 全管理面暴露」的残余风险收掉）。不在表内的角色不受限；表内角色只能调用列出的
# 方法（fnmatch  glob）。owner/client scope 检查独立于此、先生效。
ROLE_METHODS: dict[str, tuple[str, ...]] = {
    # 眼镜桥：对话 + 审批 + 上下文/日程/投递 + 观测回放。管理面（config/models/
    # plugins/memory 写等）一律不给。
    "glasses": (
        "chat.send", "chat.cancel",
        "conversations.list", "conversations.messages",
        "runs.get", "runs.list",
        "link.subscribe", "link.clients",
        "approvals.list", "approvals.resolve",
        "system.status", "system.schema",
        "events.query",
        "glasses.*",
    ),
    # 手机聊天面（全功能端）：对话/历史/审批/轨迹/任务只读/人格可见；
    # 管理面（config/models/plugins/memory 写/policy）一律不给。
    "mobile": (
        "chat.send", "chat.cancel",
        "conversations.*",
        "runs.get", "runs.list",
        "link.subscribe", "link.clients",
        "approvals.list", "approvals.resolve",
        "system.status", "system.schema",
        "events.query",
        "personas.list",
        "tasks.list", "tasks.get",
        "memory.search",
    ),
}


def envelope(type_: str, payload: dict[str, Any] | None = None, corr: str | None = None) -> dict[str, Any]:
    return {"v": PROTOCOL, "id": ulid(), "type": type_, "ts": now_ms(), "corr": corr, "payload": payload or {}}


class NoParams(BaseModel):
    pass


@dataclass
class Method:
    name: str
    handler: Callable[..., Awaitable[Any]]
    params: type[BaseModel]
    summary: str = ""
    scope: str = "owner"  # owner | client (callable by any authenticated client role)


class RpcRegistry:
    def __init__(self) -> None:
        self.methods: dict[str, Method] = {}

    def method(self, name: str, params: type[BaseModel] = NoParams, summary: str = "", scope: str = "owner"):
        def deco(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
            self.methods[name] = Method(name, fn, params, summary, scope)
            return fn
        return deco

    async def call(self, name: str, raw: dict[str, Any] | None, client: "Client | None" = None) -> Any:
        m = self.methods.get(name)
        if m is None:
            raise WhaleError("not_found", f"unknown method {name}")
        if client is not None and m.scope == "owner" and not client.owner:
            raise WhaleError("forbidden", f"{name} requires owner access")
        if client is not None and client.role in ROLE_METHODS and not any(
                fnmatch.fnmatchcase(name, p) for p in ROLE_METHODS[client.role]):
            raise WhaleError("forbidden", f"role {client.role} may not call {name}",
                             details={"role": client.role})
        try:
            params = m.params.model_validate(raw or {})
        except ValidationError as exc:
            raise WhaleError("invalid_params", f"invalid params for {name}",
                             details={"errors": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]})
        return await m.handler(params, client)

    def schema(self) -> dict[str, Any]:
        return {name: {"summary": m.summary, "scope": m.scope, "params": m.params.model_json_schema()}
                for name, m in sorted(self.methods.items())}


@dataclass
class Client:
    id: str
    role: str                       # console | mobile | device | bridge | cli
    name: str
    owner: bool = True
    send: Callable[[dict[str, Any]], Awaitable[None]] | None = None
    subscriptions: list[str] = field(default_factory=list)
    capabilities: list[dict[str, Any]] = field(default_factory=list)
    connected_at: int = field(default_factory=now_ms)
    meta: dict[str, Any] = field(default_factory=dict)
    pending: dict[str, asyncio.Future[Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "role": self.role, "name": self.name, "connected_at": self.connected_at,
                "subscriptions": self.subscriptions, "capabilities": [c.get("name") for c in self.capabilities],
                "meta": self.meta}


class LinkHub:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.clients: dict[str, Client] = {}
        self.notifiers: list[Callable[[str, str, dict[str, Any]], Awaitable[str | None]]] = []

    # -- clients ----------------------------------------------------------------
    async def attach(self, client: Client) -> None:
        self.clients[client.id] = client
        for cap in client.capabilities:
            self._register_client_capability(client, cap)
        await self.app.bus.emit("link.connected", client.to_dict())
        self.app.bus.publish_event("clients.changed", {"clients": [c.to_dict() for c in self.clients.values()]})

    async def detach(self, client_id: str) -> None:
        client = self.clients.pop(client_id, None)
        if not client:
            return
        for fut in client.pending.values():
            if not fut.done():
                fut.set_exception(WhaleError("unavailable", "client disconnected"))
        self.app.capabilities.unregister_provider(f"client:{client.id}")
        await self.app.bus.emit("link.disconnected", {"id": client_id, "name": client.name})
        self.app.bus.publish_event("clients.changed", {"clients": [c.to_dict() for c in self.clients.values()]})

    def _register_client_capability(self, client: Client, cap: dict[str, Any]) -> None:
        raw = str(cap.get("name") or "").strip()
        if not raw:
            return
        slug = "".join(ch if ch.isalnum() else "_" for ch in client.name.lower())[:24] or client.role
        full = f"client.{slug}.{raw}"
        try:
            risk = Risk(cap.get("risk", "external"))
        except ValueError:
            risk = Risk.external
        spec = CapabilitySpec(name=full, description=str(cap.get("description") or raw),
                              params=cap.get("params") or {"type": "object", "properties": {}},
                              risk=risk, provider=f"client:{client.id}", title=cap.get("title") or raw,
                              group=f"client:{client.name}", timeout_s=float(cap.get("timeout_s") or 30),
                              owner_only=True)

        async def handler(args: dict[str, Any], ctx: ToolContext, _client: Client = client, _cap: str = raw) -> Any:
            return await self.request(_client.id, "capability.invoke", {"name": _cap, "args": args,
                                                                         "run_id": ctx.run_id},
                                      timeout=spec.timeout_s)

        self.app.capabilities.register(spec, handler)

    async def request(self, client_id: str, method: str, params: dict[str, Any], timeout: float = 30) -> Any:
        """Server → client RPC (invoke a capability the client declared)."""
        client = self.clients.get(client_id)
        if not client or not client.send:
            raise WhaleError("unavailable", "client is not connected")
        env = envelope("rpc.request", {"method": method, "params": params})
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        client.pending[env["id"]] = fut
        try:
            await client.send(env)
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError as exc:
            raise WhaleError("timeout", f"client {client.name} did not answer {method}") from exc
        finally:
            client.pending.pop(env["id"], None)

    def resolve_response(self, client: Client, env: dict[str, Any]) -> None:
        fut = client.pending.get(env.get("corr") or "")
        if not fut or fut.done():
            return
        payload = env.get("payload") or {}
        if payload.get("ok"):
            fut.set_result(payload.get("result"))
        else:
            err = payload.get("error") or {}
            fut.set_exception(WhaleError(err.get("code", "internal"), err.get("message", "client error")))

    async def kick_client(self, client_id: str, code: str, message: str) -> bool:
        """Server-initiated close: error frame then the writer closes the socket."""
        client = self.clients.get(client_id)
        if not client or not client.send:
            return False
        await client.send({"__kick__": code, "message": message})
        return True

    # -- delivery -----------------------------------------------------------------
    def add_notifier(self, fn: Callable[[str, str, dict[str, Any]], Awaitable[str | None]]) -> None:
        self.notifiers.append(fn)

    async def notify(self, title: str, body: str, meta: dict[str, Any] | None = None) -> list[str]:
        """Push to every online owner endpoint. Returns where it was actually handed off."""
        meta = meta or {}
        delivered = [f"{c.role}:{c.name}" for c in self.clients.values()
                     if c.owner and any(p in ("notify", "*") for p in c.subscriptions)]
        for fn in self.notifiers:
            try:
                target = await fn(title, body, meta)
                if target:
                    delivered.append(target)
            except Exception:
                log.exception("notifier failed")
        await self.app.bus.emit("notify", {"title": title, "body": body, "meta": meta, "delivered_to": delivered})
        return delivered
