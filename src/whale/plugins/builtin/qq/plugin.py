"""QQ 渠道（OneBot v11）——插件只是托管外壳：实现已迁入 ``whale.channels.onebot``。

QQ 是同一个 AgentRuntime 的一等客户端，没有单独的工作流。插件提供配置/密钥/启停
表面（控制台「插件」页），渠道实现（入站 webhook/正向 WS、群聊 mention/smart、
气泡拆分、回执三态、昵称目录）住在 channels 层，也可以由 config.json 的
``channels`` 段直接托管。两者不要同时启用同一个协议端。

入站：POST ``/api/hooks/qq/onebot``（插件校验 access_token），或配置 ``ws_url`` 正向连接。
"""

from __future__ import annotations

from typing import Any

from whale.channels.base import PluginSink
from whale.channels.onebot import (GROUP_GUIDE, JOIN_NOTE, JUDGE_SYSTEM, PRIVATE_GUIDE,  # noqa: F401  (re-export)
                                   NameDirectory, OneBotChannel, clean_name, cq_unescape, meaningful,
                                   parse_cq, parse_message, plain, split_bubbles, validate_endpoints)

# 兼容旧引用：QQBridge 现在是 OneBotChannel（同一份行为契约，见 tests/test_plugins.py）
QQBridge = OneBotChannel


async def setup(p: Any) -> OneBotChannel | None:
    cfg = p.config
    api_base = str(cfg.get("api_base") or "").strip()
    ws_url = str(cfg.get("ws_url") or "").strip()
    if not api_base and not ws_url:
        p.log.info("qq not configured (api_base / ws_url missing)")
        return None
    channel = OneBotChannel(p.app, p.id, dict(cfg), token=p.secret("access_token"),
                            sink=PluginSink(p, hook_path="onebot"))
    await channel.open()   # 端点不合法在此抛错 → 插件如实进入 error 状态
    p.on_stop(channel.close)
    return channel
