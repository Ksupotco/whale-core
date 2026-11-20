"""caption-first（Tob 2026-11-19）：入站图片一次视觉成文，回复默认用描述。

- 入站持久化时视觉模型看原图一次 → 全面文字描述（OCR 文字/对象/场景/
  布局/细节），按内容 sha256 缓存（imgcap:<sha>），同图不重复看；
- 回复默认把文字描述拼进回合文本（可检索、不依赖扫窗时序）；
- 文字描述不够回答时，模型可输出 [[看原图]] 标记升级看原图（每轮至多一次，
  由 companion 的 defer_images 机制执行——标记协议属必要工具注入）。

模型选型：生产 DeepSeek 回复脑（deepseek-flash，2026-11-19 实测带图调用
可读图内代码全文），零新增凭据；视觉能力以实测为准。
"""
from __future__ import annotations

import hashlib

CACHE_PREFIX = "imgcap:"
ESCALATE_MARK = "[[看原图]]"
MAX_CAPTION_CHARS = 2000

CAPTION_PROMPT = (
    "请详细描述这张图片，供看不到图的对话使用：图里的文字逐字念出；"
    "然后描述对象、场景、布局与值得注意的细节。平实陈述，是什么说什么。"
)


def caption_key(data: bytes) -> str:
    return CACHE_PREFIX + hashlib.sha256(bytes(data)).hexdigest()


def caption_for(store, brain, data: bytes, mime: str) -> str:
    """按内容哈希缓存的图片描述；生成失败/无脑返回空串（调用方回落原图）。"""
    key = caption_key(data)
    if store is not None:
        try:
            cached = store.get(key)
            if isinstance(cached, str) and cached.strip():
                return cached
        except Exception:
            pass
    if brain is None:
        return ""
    try:
        out = str(brain.reply(CAPTION_PROMPT, images=[(bytes(data), mime)])
                  or "").strip()
    except Exception:
        return ""
    out = out[:MAX_CAPTION_CHARS]
    if out and store is not None:
        try:
            store.put(key, out)
        except Exception:
            pass
    return out


def captions_note(captions) -> str:
    """把图片描述组装成回合文本注记（含升级协议一句——机制说明，非语气规定）。"""
    caps = [str(c).strip() for c in captions if str(c or "").strip()]
    if not caps:
        return ""
    lines = ["（刚发的图的内容描述："]
    lines.extend(f"图{i + 1}：{c}" for i, c in enumerate(caps))
    lines.append(f"描述不够回答时，整轮只输出 {ESCALATE_MARK} 就会给你看原图）")
    return "\n".join(lines)
