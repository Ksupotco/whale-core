"""Channel 抽象：一个 IO 渠道（QQ、眼镜桥……）是把外部消息接进同一个 AgentRuntime 的适配器。

不变量（继承 ARCHITECTURE/DEVELOPMENT 的主干纪律）：
- 入站只负责把外部事件规范化，然后调用 ``app.agent.run(RunRequest(...))``；
  不得在渠道里另写"召回 → 拼提示词 → 调模型"的旁路。
- 出站动作是 Capability（risk=external），经统一 Policy/审批闸门；投递回执三态
  ``ok | error | unknown``，unknown 不自动重试。
- 渠道把对话落进 ``conversations``（channel=渠道名），让控制台能看到每一个渠道的会话。

渠道的表面（能力、webhook、主人通知出口）通过 **sink** 注册：
- ``CoreSink``：由 ``channels.Channels`` 管理器直接托管（``config.json`` 的 ``channels`` 段），
  能力 provider 为 ``channel:<id>``，webhook 在 ``/api/hooks/channel-<id>/<path>``；
- ``PluginSink``：由插件托管（如内置 QQ 插件），复用插件的配置/密钥/启停表面，
  能力 provider 为 ``plugin:<id>``，webhook 在 ``/api/hooks/<plugin>/<path>``。

渠道代码不依赖 plugins 层；插件可以复用 channels 里的实现（单向）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from ..capabilities.spec import CapabilitySpec, Risk
from ..kernel.ids import now_ms

log = logging.getLogger("whale.channels")


class Channel:
    """Base class: lifecycle + truthful status. Subclasses implement ``open``/``close``."""

    kind = "generic"

    def __init__(self, app: Any, channel_id: str, sink: "Sink | None" = None) -> None:
        self.app = app
        self.id = channel_id
        self.sink = sink
        self.log = logging.getLogger(f"whale.channel.{channel_id}")
        self.connected = False
        self.started_at: int | None = None
        self.last_error = ""
        self._tasks: set[asyncio.Task[Any]] = set()

    # -- lifecycle -------------------------------------------------------------
    async def open(self) -> None:
        """Register the channel's surface and start inbound listeners."""
        self.started_at = now_ms()

    async def close(self) -> None:
        """Stop listeners; idempotent."""

    def spawn(self, coro: Any) -> asyncio.Task[Any]:
        """Track a background task so ``close``/``drain`` can reach it; failures are logged, never silent."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)

        def done(t: asyncio.Task[Any]) -> None:
            self._tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                self.log.warning("channel %s task failed: %s", self.id, repr(t.exception())[:300])

        task.add_done_callback(done)
        return task

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # -- status ------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "connected": self.connected,
                "started_at": self.started_at, "last_error": self.last_error}


class Sink:
    """How a channel registers capabilities / webhooks / notifiers on its host."""

    hook_path = "inbound"

    def capability(self, name: str, description: str, *, params: dict[str, Any] | None = None,
                   risk: str | Risk = Risk.read, title: str = "", modes: tuple[str, ...] = ("chat", "goal"),
                   timeout_s: float = 60.0, owner_only: bool = False) -> Callable[[Any], Any]:
        raise NotImplementedError

    def webhook(self, path: str) -> Callable[[Any], Any]:
        raise NotImplementedError

    def notifier(self, fn: Callable[[str, str, dict[str, Any]], Awaitable[str | None]]) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        """Unregister everything this sink registered."""


class CoreSink(Sink):
    """Host = the core Channels manager (config ``channels`` 段直接驱动的渠道）。"""

    def __init__(self, app: Any, channel_id: str) -> None:
        self.app = app
        self.id = channel_id
        self.provider = f"channel:{channel_id}"
        self._stops: list[Callable[[], Awaitable[None] | None]] = []

    def capability(self, name: str, description: str, *, params: dict[str, Any] | None = None,
                   risk: str | Risk = Risk.read, title: str = "", modes: tuple[str, ...] = ("chat", "goal"),
                   timeout_s: float = 60.0, owner_only: bool = False) -> Callable[[Any], Any]:
        def deco(fn: Any) -> Any:
            spec = CapabilitySpec(name, description, params or {"type": "object", "properties": {}},
                                  Risk(risk), self.provider, title, f"channel:{self.id}", modes, timeout_s,
                                  owner_only=owner_only)
            self.app.capabilities.register(spec, fn)
            return fn
        return deco

    def webhook(self, path: str) -> Callable[[Any], Any]:
        key = (f"channel-{self.id}", path.strip("/"))

        def deco(fn: Any) -> Any:
            self.app.hooks[key] = fn

            def remove() -> None:
                self.app.hooks.pop(key, None)

            self._stops.append(remove)
            return fn
        return deco

    def notifier(self, fn: Callable[..., Any]) -> None:
        self.app.hub.add_notifier(fn)

        def remove() -> None:
            if fn in self.app.hub.notifiers:
                self.app.hub.notifiers.remove(fn)

        self._stops.append(remove)

    async def close(self) -> None:
        for stop in self._stops:
            try:
                result = stop()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                log.exception("channel sink stop hook failed")
        self._stops = []
        self.app.capabilities.unregister_provider(self.provider)


class PluginSink(Sink):
    """Host = a plugin (the channel keeps the plugin's config/secrets/enable surface)."""

    def __init__(self, ctx: Any, hook_path: str = "inbound") -> None:
        self.ctx = ctx
        self.hook_path = hook_path

    def capability(self, name: str, description: str, **kw: Any) -> Callable[[Any], Any]:
        return self.ctx.capability(name, description, **kw)

    def webhook(self, path: str) -> Callable[[Any], Any]:
        return self.ctx.webhook(path)

    def notifier(self, fn: Callable[..., Any]) -> None:
        self.ctx.notifier(fn)

    async def close(self) -> None:  # the plugin manager's own stop hooks do the cleanup
        return None
