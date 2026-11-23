"""模型注册表（2026-11-21 主人令）：kv ``models.registry`` 驱动运行时装配。

注册表形态：[{name, provider, model, effort}]——运行时装配按 ``name`` 取。
env（WHALE_REPLY_MODEL / WHALE_VISION_MODEL 等）值为注册表 ``name`` 时按
注册表解析；未命中 = 裸模型 id（向后兼容旧行为）。

初始三条（agentscope 供应商；凭据走 env 回落零复制）：
- v4-pro-0813：直接回复脑（快档）
- qwen4-max：视觉脑
- kimi-k3：意图/任务规划脑（慢档思考）
"""
from __future__ import annotations

REGISTRY_KEY = "models.registry"

DEFAULT_REGISTRY = [
    {"name": "v4-pro-0813", "provider": "agentscope",
     "model": "deepseek-v4-pro-0813", "effort": ""},
    {"name": "qwen4-max", "provider": "agentscope",
     "model": "qwen4-max", "effort": ""},
    {"name": "kimi-k3", "provider": "agentscope",
     "model": "kimi-k3", "effort": "high"},
]

_PROVIDER_ENV = {
    # provider → (base_url env 链, api_key env 链)——零密钥复制：只引用键名
    "agentscope": (("WHALE_ASR_BASE_URL", "QQ_INTENT_BASE_URL"),
                   ("WHALE_ASR_API_KEY", "QQ_INTENT_API_KEY")),
    "deepseek": (("DEEPSEEK_BASE_URL",), ("DEEPSEEK_API_KEY",)),
}


def seed_registry(store) -> list:
    """注册表缺省播种（已有则不动）。"""
    existing = store.get(REGISTRY_KEY)
    if isinstance(existing, list) and existing:
        return existing
    store.put(REGISTRY_KEY, list(DEFAULT_REGISTRY))
    return list(DEFAULT_REGISTRY)


def registry_entries(store) -> list:
    seed_registry(store)
    entries = store.get(REGISTRY_KEY)
    return entries if isinstance(entries, list) else []


def _first_env(env, names) -> str:
    for n in names:
        v = (env.get(n) or "").strip()
        if v:
            return v
    return ""


def resolve_brain(name_or_model: str, *, env, store, timeout: float = 35.0,
                  max_tokens: int | None = None):
    """按注册表名/裸模型 id 装配脑。未配置凭据返回 None（调用方回落）。"""
    from whale_core.core.deepseek import DeepSeekCompanion

    raw = (name_or_model or "").strip()
    if not raw:
        return None
    for entry in registry_entries(store):
        if not isinstance(entry, dict) or entry.get("name") != raw:
            continue
        provider = str(entry.get("provider") or "agentscope").strip()
        base_chain, key_chain = _PROVIDER_ENV.get(provider, _PROVIDER_ENV["agentscope"])
        base = _first_env(env, base_chain)
        key = _first_env(env, key_chain)
        if not key:
            return None
        base = base or "https://platform.agentscope.io/v1"
        return DeepSeekCompanion(
            model=str(entry.get("model") or raw), base_url=base, api_key=key,
            timeout=timeout, max_tokens=max_tokens,
            reasoning_effort=str(entry.get("effort") or "").strip() or None)
    # 未命中注册表：裸模型 id（向后兼容）——走 agentscope 端点回落
    base = _first_env(env, ("WHALE_REPLY_BASE_URL", "QQ_INTENT_BASE_URL",
                            "DEEPSEEK_BASE_URL"))
    key = _first_env(env, ("WHALE_REPLY_API_KEY", "QQ_INTENT_API_KEY",
                           "DEEPSEEK_API_KEY"))
    if not key:
        return None
    return DeepSeekCompanion(model=raw, base_url=base or None, api_key=key,
                             timeout=timeout, max_tokens=max_tokens)


# ---- 两档回复档位（kv conversation_id→tier；默认快速） ---------------------

TIER_PREFIX = "replytier:"
TIERS = ("fast", "slow")


def get_tier(store, conversation_id: str) -> str:
    v = store.get(TIER_PREFIX + str(conversation_id))
    return str(v) if str(v) in TIERS else "fast"


def set_tier(store, conversation_id: str, tier: str) -> bool:
    tier = str(tier or "").strip()
    if tier not in TIERS:
        return False
    store.put(TIER_PREFIX + str(conversation_id), tier)
    return True
