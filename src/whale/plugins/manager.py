"""Plugins: a directory with ``plugin.json`` + a Python entry that registers capabilities.

    plugin.json  {"id": "qweather", "name": "和风天气", "version": "1.0.0", "entry": "plugin.py",
                  "description": "…", "config": {"host": {"type": "string", "title": "API Host"}},
                  "secrets": {"key": "API Key"}}
    plugin.py    def setup(p): @p.capability("weather.now", "…", params=…) async def now(args, ctx): …

Plugins are explicit: built-in plugins ship in ``whale/plugins/``, user
plugins live in ``<data_dir>/plugins/``. Enable/disable takes effect live by
registering/unregistering the plugin's capabilities.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..capabilities.spec import CapabilitySpec, Risk
from ..kernel.errors import WhaleError, not_found

log = logging.getLogger("whale.plugins")

BUILTIN_DIR = Path(__file__).parent / "builtin"


@dataclass
class PluginRecord:
    id: str
    path: Path
    manifest: dict[str, Any]
    enabled: bool = False
    status: str = "disabled"   # disabled | active | error | needs_config
    error: str = ""
    capabilities: list[str] = field(default_factory=list)
    module: Any = None
    stop_hooks: list[Callable[[], Awaitable[None]]] = field(default_factory=list)

    def to_dict(self, app: Any) -> dict[str, Any]:
        m = self.manifest
        secrets = {k: bool(app.config.secret(f"plugin.{self.id}.{k}")) for k in (m.get("secrets") or {})}
        return {"id": self.id, "name": m.get("name", self.id), "version": m.get("version", ""),
                "description": m.get("description", ""), "enabled": self.enabled, "status": self.status,
                "error": self.error, "capabilities": self.capabilities, "config_schema": m.get("config") or {},
                "secrets_schema": m.get("secrets") or {}, "secrets": secrets,
                "config": app.config.value.plugins.get(self.id, {}), "builtin": BUILTIN_DIR in self.path.parents,
                "icon": m.get("icon", "🧩")}


class PluginContext:
    """What a plugin's ``setup(p)`` receives."""

    def __init__(self, app: Any, record: PluginRecord) -> None:
        self.app = app
        self.record = record
        self.id = record.id
        self.log = logging.getLogger(f"whale.plugin.{record.id}")

    @property
    def config(self) -> dict[str, Any]:
        return self.app.config.value.plugins.get(self.id, {})

    def secret(self, key: str) -> str:
        return self.app.config.secret(f"plugin.{self.id}.{key}")

    def capability(self, name: str, description: str, *, params: dict[str, Any] | None = None,
                   risk: str | Risk = Risk.read, title: str = "", modes: tuple[str, ...] = ("chat", "goal", "explore"),
                   timeout_s: float = 60.0, owner_only: bool = False):
        def deco(fn: Any) -> Any:
            spec = CapabilitySpec(name, description, params or {"type": "object", "properties": {}},
                                  Risk(risk), f"plugin:{self.id}", title, self.record.manifest.get("name", self.id),
                                  modes, timeout_s, owner_only=owner_only)
            self.app.capabilities.register(spec, fn)
            self.record.capabilities.append(name)
            return fn
        return deco

    def webhook(self, path: str):
        """Expose ``async handler(request) -> starlette Response`` at /api/hooks/<plugin_id>/<path>.

        The core does not authenticate these requests; verify the caller (e.g. a shared token).
        """
        key = (self.id, path.strip("/"))

        def deco(fn: Any) -> Any:
            self.app.hooks[key] = fn

            async def remove() -> None:
                self.app.hooks.pop(key, None)

            self.record.stop_hooks.append(remove)
            return fn
        return deco

    def save_artifact(self, data: bytes, *, content_type: str, name: str = "",
                      meta: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.app.artifacts.save(data, content_type=content_type, name=name,
                                       meta={"plugin": self.id, **(meta or {})})

    def set_status(self, status: str, error: str = "") -> None:
        """Report live health (e.g. ``degraded`` when a channel connection drops)."""
        self.record.status = status
        self.record.error = error
        self.app.bus.publish_event("plugin.changed", self.record.to_dict(self.app))

    def on_stop(self, fn: Callable[[], Awaitable[None]]) -> None:
        self.record.stop_hooks.append(fn)

    def notifier(self, fn: Callable[[str, str, dict[str, Any]], Awaitable[str | None]]) -> None:
        """Register an outbound channel for owner notifications (e.g. QQ private chat)."""
        self.app.hub.add_notifier(fn)
        self.record.stop_hooks.append(lambda: self._remove_notifier(fn))

    async def _remove_notifier(self, fn: Any) -> None:
        if fn in self.app.hub.notifiers:
            self.app.hub.notifiers.remove(fn)


class PluginManager:
    def __init__(self, app: Any, extra_dirs: list[Path] | None = None) -> None:
        self.app = app
        self.dirs = [BUILTIN_DIR] + list(extra_dirs or [])
        if app.data_dir:
            self.dirs.append(Path(app.data_dir) / "plugins")
        self.records: dict[str, PluginRecord] = {}

    def discover(self) -> None:
        for base in self.dirs:
            if not base.is_dir():
                continue
            for d in sorted(base.iterdir()):
                mf = d / "plugin.json"
                if not mf.is_file():
                    continue
                try:
                    manifest = json.loads(mf.read_text(encoding="utf-8"))
                    pid = str(manifest["id"])
                except Exception as exc:
                    log.warning("bad plugin manifest %s: %s", mf, exc)
                    continue
                self.records.setdefault(pid, PluginRecord(pid, d, manifest))

    async def start(self) -> None:
        self.discover()
        for pid, rec in self.records.items():
            cfg = self.app.config.value.plugins.get(pid, {})
            enabled = cfg.get("enabled", bool(rec.manifest.get("default_enabled", False)))
            if enabled:
                await self.enable(pid, persist=False)

    async def stop(self) -> None:
        for pid in list(self.records):
            if self.records[pid].enabled:
                await self.disable(pid, persist=False)

    def get(self, pid: str) -> PluginRecord:
        if pid not in self.records:
            raise not_found("plugin", pid)
        return self.records[pid]

    def list(self) -> list[dict[str, Any]]:
        return [r.to_dict(self.app) for r in self.records.values()]

    async def enable(self, pid: str, persist: bool = True) -> dict[str, Any]:
        rec = self.get(pid)
        if rec.enabled:
            await self.disable(pid, persist=False)
        rec.capabilities = []
        rec.stop_hooks = []
        rec.error = ""
        try:
            entry = rec.path / rec.manifest.get("entry", "plugin.py")
            name = f"whale_plugin_{pid}"
            import importlib.util

            spec = importlib.util.spec_from_file_location(name, entry)
            if spec is None or spec.loader is None:
                raise WhaleError("internal", f"cannot load {entry}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            rec.module = module
            ctx = PluginContext(self.app, rec)
            result = module.setup(ctx)
            if asyncio.iscoroutine(result):
                await result
            rec.enabled = True
            rec.status = "active" if rec.capabilities or rec.stop_hooks else "needs_config"
        except Exception as exc:
            self.app.capabilities.unregister_provider(f"plugin:{pid}")
            rec.enabled = True
            rec.status = "error"
            rec.error = repr(exc)
            log.warning("plugin %s failed: %s", pid, rec.error)
        if persist:
            self.app.config.update({"plugins": {pid: {"enabled": True}}})
        await self.app.bus.emit("plugin.changed", rec.to_dict(self.app))
        return rec.to_dict(self.app)

    async def disable(self, pid: str, persist: bool = True) -> dict[str, Any]:
        rec = self.get(pid)
        for hook in rec.stop_hooks:
            try:
                await hook()
            except Exception:
                log.exception("plugin %s stop hook failed", pid)
        self.app.capabilities.unregister_provider(f"plugin:{pid}")
        rec.enabled = False
        rec.status = "disabled"
        rec.capabilities = []
        if persist:
            self.app.config.update({"plugins": {pid: {"enabled": False}}})
        await self.app.bus.emit("plugin.changed", rec.to_dict(self.app))
        return rec.to_dict(self.app)

    async def configure(self, pid: str, config: dict[str, Any], secrets: dict[str, str] | None = None) -> dict[str, Any]:
        rec = self.get(pid)
        allowed = set((rec.manifest.get("config") or {}).keys()) | {"enabled"}
        clean = {k: v for k, v in config.items() if k in allowed}
        if clean:
            self.app.config.update({"plugins": {pid: clean}})
        for key, value in (secrets or {}).items():
            if key in (rec.manifest.get("secrets") or {}):
                self.app.config.set_secret(f"plugin.{pid}.{key}", value)
        if rec.enabled:
            return await self.enable(pid, persist=False)
        return rec.to_dict(self.app)
