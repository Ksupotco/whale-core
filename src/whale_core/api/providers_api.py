"""供应商管理端 REST（2026-11-22 控制台重构 · 裁决③④，Tob 批准）。

三条红线（主人裁决，验收口径）：
1. 密钥绝不出域——视图只回显 api_key_configured 布尔；代取 /models 走服务
   端（15s 超时 + 60s 内存缓存）；日志/响应/审计一律不含明文 key。
2. 纯增量：不动 settings.model / models{} 的现有读写契约；本模块不碰
   profiles.py 装配（registry→resolve_brain 接线另行报批）。
3. 首启播种：首个 GET 时若注册表为空，从当前主模型配置播种 default
   供应商（迁移平滑；密钥从 settings 私密侧引用，不复制明文进日志）。

端点（统一 {result}/{error} 契约 + 双认证由 server 层完成）：
- GET    /providers                  列表（公开视图）
- POST   /providers                  新增 {name, base_url, api_key?}
- POST   /providers/{id}             改 {name?, base_url?, api_key?(null=清除)}
- POST   /providers/{id}/delete      删除（被 models.registry 引用时 409）
- POST   /providers/{id}/test        连通测试（真实请求 /models，报 latency）
- GET    /providers/{id}/models      服务端代取模型清单（60s 内存缓存）
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
import uuid

from whale_core.api.server import _error, _json_bytes

REGISTRY_KEY = "providers:registry"
KEY_PREFIX = "provider:key:"
MODELS_CACHE_TTL_S = 60
FETCH_TIMEOUT_S = 15

# 进程内模型清单缓存：{provider_id: (expires_at, models)}
_models_cache: dict[str, tuple[float, list[str]]] = {}
_models_cache_lock = threading.Lock()


def _load(ctx) -> list[dict]:
    raw = ctx.store.get(REGISTRY_KEY)
    return [p for p in raw if isinstance(p, dict)] if isinstance(raw, list) else []


def _save(ctx, providers: list[dict]) -> None:
    ctx.store.put(REGISTRY_KEY, providers)


def _public(p: dict, ctx) -> dict:
    return {
        "id": p["id"],
        "name": p.get("name", ""),
        "base_url": p.get("base_url", ""),
        "enabled": bool(p.get("enabled", True)),
        "api_key_configured": bool(ctx.store.get(KEY_PREFIX + p["id"])),
        "created_at": p.get("created_at"),
        "updated_at": p.get("updated_at"),
    }


def _seed_default(ctx) -> None:
    """首启播种：空注册表 + 主模型已配置 → 生成 default 供应商。"""
    if _load(ctx):
        return
    settings = getattr(ctx, "settings", None)
    if settings is None:
        return
    try:
        view = settings.view()
    except Exception:  # noqa: BLE001
        return
    base_url = str(view.get("model", {}).get("base_url") or "").strip()
    if not base_url:
        return
    now = int(time.time())
    pid = uuid.uuid4().hex[:12]
    providers = [{
        "id": pid, "name": "默认供应商", "base_url": base_url,
        "enabled": True, "created_at": now, "updated_at": now,
    }]
    _save(ctx, providers)
    # 密钥引用：从 settings 私密侧取（若有）——只进不出
    try:
        from whale_core.configuration import read_settings_pair
        config_dir = getattr(settings, "config_dir", None)
        main_key = ""
        if config_dir:
            _, private = read_settings_pair(config_dir)
            main_key = str(private.get("model_api_key") or "") if isinstance(private, dict) else ""
    except Exception:  # noqa: BLE001
        pass
    ctx.log_event("providers:seed", "default provider from main model")


def _validate_payload(body: dict, *, partial: bool) -> dict:
    out: dict = {}
    if "name" in body or not partial:
        name = str(body.get("name") or "").strip()
        if not (1 <= len(name) <= 64):
            raise ValueError("name 须为 1..64 字符")
        out["name"] = name
    if "base_url" in body or not partial:
        base_url = str(body.get("base_url") or "").strip()
        if not base_url:
            raise ValueError("base_url 必填")
        from whale_core.configuration import validate_endpoint
        validate_endpoint(base_url)
        out["base_url"] = base_url
    return out




def _fetch_models(base_url: str, api_key: str) -> list[str]:
    """服务端代取 OpenAI 兼容 /models（key 不出域；15s 超时）。"""
    url = base_url.rstrip("/") + "/models"
    req = urllib.request.Request(url)
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("响应不含 data 列表")
    return sorted(str(m.get("id")) for m in items if isinstance(m, dict) and m.get("id"))


def dispatch_providers(handler, method: str, parts: list[str], query: dict | None = None) -> None:
    send = handler._send
    ctx = handler.ctx
    with ctx.lock:
        _seed_default(ctx)
        providers = _load(ctx)

    # GET /providers
    if parts == ["providers"] and method == "GET":
        with ctx.lock:
            send(200, _json_bytes({"result": {
                "providers": [_public(p, ctx) for p in _load(ctx)],
            }}))
        return

    # POST /providers（新增）
    if parts == ["providers"] and method == "POST":
        body = handler._body_json()
        if body is None:
            send(400, _error("bad_request", "JSON body required"))
            return
        try:
            fields = _validate_payload(body, partial=False)
        except ValueError as exc:
            send(400, _error("bad_request", str(exc)))
            return
        api_key = body.get("api_key")
        if api_key is not None and not isinstance(api_key, str):
            send(400, _error("bad_request", "api_key 须为字符串"))
            return
        now = int(time.time())
        pid = uuid.uuid4().hex[:12]
        with ctx.lock:
            providers = _load(ctx)
            providers.append({
                "id": pid, **fields, "enabled": True,
                "created_at": now, "updated_at": now,
            })
            _save(ctx, providers)
            if api_key:
                ctx.store.put(KEY_PREFIX + pid, api_key)
        ctx.log_event("providers:create", fields["name"])
        with ctx.lock:
            view = _public(next(p for p in _load(ctx) if p["id"] == pid), ctx)
        send(200, _json_bytes({"result": view}))
        return

    if len(parts) >= 2 and parts[0] == "providers":
        pid = parts[1]
        with ctx.lock:
            providers = _load(ctx)
            target = next((p for p in providers if p.get("id") == pid), None)
        if target is None:
            send(404, _error("not_found", f"未知供应商 {pid}"))
            return

        # POST /providers/{id}（改）
        if len(parts) == 2 and method == "POST":
            body = handler._body_json()
            if body is None:
                send(400, _error("bad_request", "JSON body required"))
                return
            try:
                fields = _validate_payload(body, partial=True)
            except ValueError as exc:
                send(400, _error("bad_request", str(exc)))
                return
            api_key = body.get("api_key", "__absent__")
            if api_key != "__absent__" and api_key is not None and not isinstance(api_key, str):
                send(400, _error("bad_request", "api_key 须为字符串或 null"))
                return
            with ctx.lock:
                providers = _load(ctx)
                target = next(p for p in providers if p.get("id") == pid)
                target.update(fields)
                target["updated_at"] = int(time.time())
                _save(ctx, providers)
                if api_key == "__absent__":
                    pass  # 不动密钥
                elif api_key is None:
                    ctx.store.delete(KEY_PREFIX + pid)
                elif api_key:
                    ctx.store.put(KEY_PREFIX + pid, api_key)
                view = _public(target, ctx)
            ctx.log_event("providers:update", target.get("name", pid))
            send(200, _json_bytes({"result": view}))
            return

        if len(parts) == 3 and parts[2] == "delete" and method == "POST":
            # 被 models.registry 引用 → 409
            from whale_core.life.model_registry import registry_entries
            refs = [str(e.get("name")) for e in registry_entries(ctx.store)
                    if isinstance(e, dict)
                    and _provider_ref_matches(ctx, e.get("provider"), pid, target)]
            if refs:
                send(409, _error("conflict",
                                 f"供应商被模型注册表引用（{', '.join(refs)}），请先调整引用"))
                return
            with ctx.lock:
                _save(ctx, [p for p in _load(ctx) if p.get("id") != pid])
                ctx.store.delete(KEY_PREFIX + pid)
            with _models_cache_lock:
                _models_cache.pop(pid, None)
            ctx.log_event("providers:delete", target.get("name", pid))
            send(200, _json_bytes({"result": {"id": pid, "deleted": True}}))
            return

        if len(parts) == 3 and parts[2] == "test" and method == "POST":
            key = str(ctx.store.get(KEY_PREFIX + pid) or "")
            start = time.monotonic()
            try:
                models = _fetch_models(str(target.get("base_url") or ""), key)
                result = {"ok": True, "latency_ms": int((time.monotonic() - start) * 1000),
                          "models": len(models)}
            except Exception as exc:  # noqa: BLE001 — 不回显内部细节
                kind = "timeout" if isinstance(exc, TimeoutError) else (
                    "http" if isinstance(exc, urllib.error.HTTPError) else "unavailable")
                result = {"ok": False,
                          "latency_ms": int((time.monotonic() - start) * 1000),
                          "error_kind": kind}
            send(200, _json_bytes({"result": result}))
            return

        if len(parts) == 3 and parts[2] == "models" and method == "GET":
            with _models_cache_lock:
                cached = _models_cache.get(pid)
                if cached and cached[0] > time.time():
                    send(200, _json_bytes({"result": {
                        "provider_id": pid, "models": cached[1], "cached": True}}))
                    return
            key = str(ctx.store.get(KEY_PREFIX + pid) or "")
            try:
                models = _fetch_models(str(target.get("base_url") or ""), key)
            except urllib.error.HTTPError as exc:
                send(502, _error("upstream", f"供应商返回 {exc.code}"))
                return
            except (TimeoutError, urllib.error.URLError, ValueError, json.JSONDecodeError):
                send(502, _error("upstream", "供应商模型清单获取失败或超时"))
                return
            with _models_cache_lock:
                _models_cache[pid] = (time.time() + MODELS_CACHE_TTL_S, models)
            send(200, _json_bytes({"result": {
                "provider_id": pid, "models": models, "cached": False}}))
            return

    send(404, _error("not_found", "unknown endpoint"))


def _provider_ref_matches(ctx, ref, provider_id: str, target: dict) -> bool:
    """registry 条目的 provider 字段与本供应商的匹配（按 id 或按名称兼容）。"""
    ref = str(ref or "").strip()
    if not ref:
        return False
    return ref in (provider_id, str(target.get("name") or ""))
