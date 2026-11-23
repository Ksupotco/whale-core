"""Model router: resolves roles (chat / fast / vision / embedding) to live clients.

Roles fall back sensibly: fast → chat, vision → chat, chat → offline brain.
Every call is metered (requests, latency, errors per provider) and counted
against the daily request budget.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..kernel.config import Config, ModelRoute
from ..kernel.db import Database
from ..kernel.errors import WhaleError
from ..kernel.events import EventBus
from .base import ChatModel, Completion, EmbeddingModel, Message, ModelError
from .openai_compat import OpenAICompatChat, OpenAICompatEmbedding
from .scripted import HashEmbedding, ScriptedModel

ROLES = ("chat", "fast", "vision", "embedding")


@dataclass
class ProviderStats:
    requests: int = 0
    errors: int = 0
    total_ms: float = 0.0
    last_error: str = ""
    last_ok_at: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0

    def to_dict(self) -> dict[str, Any]:
        avg = self.total_ms / self.requests if self.requests else 0.0
        return {"requests": self.requests, "errors": self.errors, "avg_ms": round(avg, 1),
                "last_error": self.last_error, "last_ok_at": self.last_ok_at,
                "tokens_in": self.tokens_in, "tokens_out": self.tokens_out}


class MeteredModel:
    """Wraps a ChatModel with metrics, budget and events."""

    def __init__(self, router: "ModelRouter", role: str, inner: ChatModel, route: ModelRoute) -> None:
        self.router = router
        self.role = role
        self.inner = inner
        self.route = route
        self.name = inner.name

    @property
    def offline(self) -> bool:
        return isinstance(self.inner, ScriptedModel) and self.inner.offline

    async def complete(self, messages: list[Message], **kw: Any) -> Completion:
        kw.setdefault("temperature", self.route.temperature)
        kw.setdefault("max_tokens", self.route.max_tokens)
        if not self.offline:
            self.router.reserve()
        stats = self.router.stats.setdefault(self.route.provider or "offline", ProviderStats())
        t0 = time.perf_counter()
        try:
            out = await self.inner.complete(messages, **kw)
        except ModelError as exc:
            stats.requests += 1
            stats.errors += 1
            stats.last_error = str(exc)[:300]
            raise WhaleError("unavailable", f"model {self.name} failed: {exc}", retryable=exc.retryable) from exc
        stats.requests += 1
        stats.total_ms += (time.perf_counter() - t0) * 1000
        stats.last_ok_at = time.time() * 1000
        stats.tokens_in += out.usage.prompt_tokens
        stats.tokens_out += out.usage.completion_tokens
        return out


class ModelRouter:
    def __init__(self, config: Config, db: Database, bus: EventBus) -> None:
        self.config = config
        self.db = db
        self.bus = bus
        self.stats: dict[str, ProviderStats] = {}
        self._cache: dict[tuple[Any, ...], Any] = {}
        self.overrides: dict[str, Any] = {}  # tests / plugins can inject models per role
        config.on_change(lambda patch: self._cache.clear() if "models" in patch or "secrets" in patch else None)

    def _route(self, role: str) -> ModelRoute:
        models = self.config.value.models
        route: ModelRoute = {"chat": models.chat, "fast": models.fast, "vision": models.vision,
                             "embedding": models.embedding}[role]
        if role in ("fast", "vision") and not (route.provider and route.model):
            return models.chat
        return route

    def _build_chat(self, route: ModelRoute) -> ChatModel:
        provider = next((p for p in self.config.value.models.providers if p.id == route.provider), None)
        if not provider or not route.model or provider.kind == "scripted":
            return ScriptedModel()
        key = ("chat", provider.id, provider.base_url, route.model, self.config.secret(f"provider.{provider.id}"))
        if key not in self._cache:
            self._cache[key] = OpenAICompatChat(provider.base_url, route.model,
                                                self.config.secret(f"provider.{provider.id}"))
        return self._cache[key]

    def chat(self, role: str = "chat") -> MeteredModel:
        if role in self.overrides:
            inner = self.overrides[role]
        elif role != "chat" and "chat" in self.overrides and not self._configured(role):
            inner = self.overrides["chat"]
        else:
            inner = self._build_chat(self._route(role))
        return MeteredModel(self, role, inner, self._route(role))

    def _configured(self, role: str) -> bool:
        m = self.config.value.models
        route = {"chat": m.chat, "fast": m.fast, "vision": m.vision, "embedding": m.embedding}[role]
        return bool(route.provider and route.model)

    def embedding(self) -> EmbeddingModel:
        if "embedding" in self.overrides:
            return self.overrides["embedding"]
        route = self.config.value.models.embedding
        provider = next((p for p in self.config.value.models.providers if p.id == route.provider), None)
        if not provider or not route.model:
            return self._cache.setdefault(("embed", "hash"), HashEmbedding())
        key = ("embed", provider.id, provider.base_url, route.model)
        if key not in self._cache:
            self._cache[key] = OpenAICompatEmbedding(provider.base_url, route.model,
                                                     self.config.secret(f"provider.{provider.id}"))
        return self._cache[key]

    # -- budget ---------------------------------------------------------------
    def _budget_key(self) -> str:
        return date.today().isoformat()

    def used_today(self) -> int:
        return int(self.db.kv_get("budget", self._budget_key(), 0))

    def reserve(self) -> None:
        limit = self.config.value.models.daily_request_budget
        used = self.used_today()
        if limit and used >= limit:
            raise WhaleError("rate_limited", f"daily model request budget reached ({limit})")
        self.db.kv_set("budget", self._budget_key(), used + 1)

    def status(self) -> dict[str, Any]:
        roles = {}
        for role in ROLES:
            route = self._route(role) if role != "embedding" else self.config.value.models.embedding
            configured = bool(route.provider and route.model)
            roles[role] = {"provider": route.provider, "model": route.model, "configured": configured}
        return {
            "roles": roles,
            "offline": not roles["chat"]["configured"] and "chat" not in self.overrides,
            "budget": {"used": self.used_today(), "limit": self.config.value.models.daily_request_budget},
            "providers": {k: v.to_dict() for k, v in self.stats.items()},
        }

    async def test(self, role: str = "chat") -> dict[str, Any]:
        if role == "embedding":
            emb = self.embedding()
            t0 = time.perf_counter()
            try:
                vec = (await emb.embed(["鲸鱼"]))[0]
            except Exception as exc:
                return {"ok": False, "error": repr(exc)}
            return {"ok": True, "model": emb.name, "dimensions": len(vec), "offline": emb.name == "hash-embed",
                    "latency_ms": round((time.perf_counter() - t0) * 1000)}
        model = self.chat(role)
        t0 = time.perf_counter()
        try:
            out = await model.complete([Message("user", "只回复两个字：在线")], max_tokens=16, temperature=0)
        except WhaleError as exc:
            return {"ok": False, "error": exc.message}
        return {"ok": True, "model": model.name, "reply": out.text[:40],
                "latency_ms": round((time.perf_counter() - t0) * 1000)}
