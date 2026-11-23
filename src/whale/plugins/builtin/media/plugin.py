"""生图与语音合成：image.generate / speech.synthesize。

两类服务商：
- openai：``POST {base}/images/generations``（b64_json 或 url）与 ``POST {base}/audio/speech``（直接返回音频）；
- dashscope：``POST {base}/services/aigc/multimodal-generation/generation``（同步出图或 task_id 轮询
  ``GET {base}/tasks/{id}``）与 ``POST {base}/services/audio/tts/SpeechSynthesizer``（base64 或临时链接）。

结果一律落为附件（``<data_dir>/artifacts/``，``GET /api/artifacts/{id}`` 下载），返回附件 id 与路径；
服务商的临时签名链接不外泄给模型。缺 API Key（且不是本机/内网地址）时不注册能力。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import time
import wave
from typing import Any

import httpx

from whale.capabilities.spec import obj, s
from whale.kernel.errors import WhaleError
from whale.plugins.builtin._common import (check_base_url, download, error_detail, http_client, is_private_http,
                                           request_json, status_error, transport_error)

DEFAULTS = {
    "openai": {"base_url": "https://api.openai.com/v1", "image_model": "gpt-image-1",
               "tts_model": "gpt-4o-mini-tts", "voice": "alloy"},
    "dashscope": {"base_url": "https://dashscope.aliyuncs.com/api/v1", "image_model": "qwen-image-3.0",
                  "tts_model": "qwen-audio-3.0-tts-flash", "voice": "longanfengyue"},
}
MAX_TTS_CHARS = 4000
MAX_PROMPT = 2000
OPENAI_SIZES = {"1:1": "1024x1024", "16:9": "1536x1024", "4:3": "1536x1024", "3:2": "1536x1024",
                "9:16": "1024x1536", "3:4": "1024x1536", "2:3": "1024x1536"}
DASHSCOPE_SIZES = {"1:1": "1328*1328", "4:3": "1472*1104", "16:9": "1664*928", "3:4": "1104*1472",
                   "9:16": "928*1664", "3:2": "1472*976", "2:3": "976*1472"}


def sniff_mime(data: bytes, fallback: str = "") -> str:
    if data[:4] == b"\x89PNG":
        return "image/png"
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data[:3] == b"GIF":
        return "image/gif"
    if data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio/mpeg"
    if data[:4] == b"OggS":
        return "audio/ogg"
    return (fallback or "application/octet-stream").split(";")[0].strip()


def normalize_size(provider: str, size: str) -> str:
    table = DASHSCOPE_SIZES if provider == "dashscope" else OPENAI_SIZES
    size = (size or "1:1").strip().lower().replace("×", "x")
    if size in table:
        return table[size]
    if provider == "dashscope":
        if size in table.values():
            return size
        size = size.replace("*", "x")
    if "x" in size:
        w, _, h = size.partition("x")
        if w.isdigit() and h.isdigit() and int(w) and int(h):
            if provider != "dashscope":
                return f"{int(w)}x{int(h)}"
            ratio = int(w) / int(h)
            best = min(table, key=lambda k: abs(int(k.split(":")[0]) / int(k.split(":")[1]) - ratio))
            return table[best]
    raise WhaleError("invalid_params", "size 需要是比例（如 1:1、16:9）或 宽x高（如 1024x1024）")


def normalize_wav(data: bytes) -> bytes:
    """重写 WAV 头（部分上游的帧计数是占位符，会让严格的解码器出错）；读不了就原样返回。"""
    try:
        with wave.open(io.BytesIO(data)) as src:
            frames = src.readframes(src.getnframes())
            out = io.BytesIO()
            with wave.open(out, "wb") as dst:
                dst.setnchannels(src.getnchannels())
                dst.setsampwidth(src.getsampwidth())
                dst.setframerate(src.getframerate())
                dst.writeframes(frames)
            return out.getvalue()
    except Exception:
        return data


def _b64(data: str, service: str) -> bytes:
    try:
        return base64.b64decode(data, validate=False)
    except (binascii.Error, ValueError):
        raise WhaleError("unavailable", f"{service} 返回了无效的 base64 数据") from None


def _dashscope_images(output: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for choice in output.get("choices") or []:
        content = ((choice or {}).get("message") or {}).get("content") if isinstance(choice, dict) else None
        if isinstance(content, list):
            urls += [part["image"] for part in content if isinstance(part, dict) and isinstance(part.get("image"), str)]
    for item in output.get("results") or []:
        if isinstance(item, dict) and isinstance(item.get("url"), str):
            urls.append(item["url"])
    for key in ("image", "url"):
        if isinstance(output.get(key), str) and output[key].startswith("http"):
            urls.append(output[key])
    return urls


class MediaService:
    def __init__(self, p: Any, provider: str, base_url: str, key: str, cfg: dict[str, Any]) -> None:
        d = DEFAULTS[provider]
        self.p = p
        self.app = p.app
        self.provider = provider
        self.service = "DashScope" if provider == "dashscope" else "OpenAI 兼容接口"
        self.base = base_url
        self.key = key
        self.image_model = str(cfg.get("image_model") or "") or d["image_model"]
        self.tts_model = str(cfg.get("tts_model") or "") or d["tts_model"]
        self.voice = str(cfg.get("voice") or "") or d["voice"]
        self.poll_interval = 3.0
        self.poll_timeout = 200.0

    def _client(self, timeout: float, extra: dict[str, str] | None = None) -> Any:
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        return http_client(self.app, timeout=timeout, headers={**headers, **(extra or {})})

    def _save(self, data: bytes, mime: str, kind: str, meta: dict[str, Any]) -> dict[str, Any]:
        if not data:
            raise WhaleError("unavailable", f"{self.service} 返回了空文件")
        ext = mime.split("/")[-1].replace("mpeg", "mp3").replace("jpeg", "jpg")
        info = self.p.save_artifact(data, content_type=mime, name=f"{kind}-{int(time.time())}.{ext}",
                                    meta={"kind": kind, "provider": self.provider, **meta})
        return {"artifact_id": info["id"], "path": info.get("path"), "url": info["url"],
                "content_type": info["content_type"], "size": info["size"]}

    # -- images -------------------------------------------------------------------------------
    async def generate_image(self, prompt: str, size: str) -> dict[str, Any]:
        prompt = prompt.strip()[:MAX_PROMPT]
        if not prompt:
            raise WhaleError("invalid_params", "prompt 不能为空")
        size = normalize_size(self.provider, size)
        async with self._client(180) as client:
            if self.provider == "dashscope":
                blobs = await self._dashscope_image(client, prompt, size)
            else:
                blobs = await self._openai_image(client, prompt, size)
        meta = {"prompt": prompt[:300], "model": self.image_model, "size": size}
        saved = [self._save(data, sniff_mime(data, ctype or "image/png"), "image", meta) for data, ctype in blobs]
        first = saved[0]
        return {**first, "images": saved, "model": self.image_model, "size": size,
                "summary": f"已生成 {len(saved)} 张图片（附件 {first['artifact_id']}）"}

    async def _openai_image(self, client: Any, prompt: str, size: str) -> list[tuple[bytes, str]]:
        body = {"model": self.image_model, "prompt": prompt, "size": size, "n": 1}
        data = await request_json(client, "POST", f"{self.base}/images/generations", self.service, json=body)
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list) or not items:
            raise WhaleError("unavailable", f"{self.service} 没有返回图片")
        out: list[tuple[bytes, str]] = []
        for item in items[:4]:
            if not isinstance(item, dict):
                continue
            if item.get("b64_json"):
                out.append((_b64(item["b64_json"], self.service), "image/png"))
            elif item.get("url"):
                out.append(await download(client, str(item["url"]), self.service))
        if not out:
            raise WhaleError("unavailable", f"{self.service} 返回的图片无法读取")
        return out

    async def _dashscope_image(self, client: Any, prompt: str, size: str) -> list[tuple[bytes, str]]:
        body = {"model": self.image_model,
                "input": {"messages": [{"role": "user", "content": [{"text": prompt}]}]},
                "parameters": {"size": size, "watermark": False}}
        data = await request_json(client, "POST", f"{self.base}/services/aigc/multimodal-generation/generation",
                                  self.service, json=body)
        output = self._output(data)
        urls = _dashscope_images(output)
        if not urls:
            task_id = str(output.get("task_id") or "")
            if not task_id:
                raise WhaleError("unavailable", f"{self.service} 既没有返回图片也没有任务 id")
            urls = await self._poll(client, task_id)
        return [await download(client, u, self.service) for u in urls[:4]]

    def _output(self, data: Any) -> dict[str, Any]:
        output = data.get("output") if isinstance(data, dict) else None
        if not isinstance(output, dict):
            msg = data.get("message") if isinstance(data, dict) else ""
            raise WhaleError("unavailable", f"{self.service} 返回格式异常 {msg or ''}".strip())
        return output

    async def _poll(self, client: Any, task_id: str) -> list[str]:
        deadline = time.monotonic() + self.poll_timeout
        while True:
            output = self._output(await request_json(client, "GET", f"{self.base}/tasks/{task_id}", self.service))
            status = str(output.get("task_status") or "")
            if status == "SUCCEEDED":
                urls = _dashscope_images(output)
                if not urls:
                    raise WhaleError("unavailable", f"{self.service} 任务成功但没有图片")
                return urls
            if status in ("FAILED", "CANCELED", "UNKNOWN"):
                err = output.get("error")
                why = output.get("message") or (err.get("message", "") if isinstance(err, dict) else "")
                raise WhaleError("unavailable", f"{self.service} 生图任务失败 {why}".strip()[:200])
            if time.monotonic() > deadline:
                raise WhaleError("timeout", f"{self.service} 生图任务超时（任务 {task_id}）")
            await asyncio.sleep(self.poll_interval)

    # -- speech -------------------------------------------------------------------------------
    async def synthesize(self, text: str, voice: str) -> dict[str, Any]:
        text = text.strip()
        if not text:
            raise WhaleError("invalid_params", "text 不能为空")
        if len(text) > MAX_TTS_CHARS:
            raise WhaleError("invalid_params", f"文本过长（最多 {MAX_TTS_CHARS} 字）")
        voice = voice.strip() or self.voice
        async with self._client(90) as client:
            if self.provider == "dashscope":
                data, mime = await self._dashscope_tts(client, text, voice)
            else:
                data, mime = await self._openai_tts(client, text, voice)
        saved = self._save(data, mime, "speech", {"model": self.tts_model, "voice": voice, "chars": len(text)})
        return {**saved, "voice": voice, "model": self.tts_model,
                "summary": f"已合成语音（{len(text)} 字，附件 {saved['artifact_id']}）"}

    async def _openai_tts(self, client: Any, text: str, voice: str) -> tuple[bytes, str]:
        body = {"model": self.tts_model, "input": text, "voice": voice, "response_format": "mp3"}
        try:
            resp = await client.post(f"{self.base}/audio/speech", json=body)
        except httpx.HTTPError as exc:
            raise transport_error(self.service, exc) from None
        if resp.status_code >= 400:
            raise status_error(self.service, resp.status_code, error_detail(resp))
        ctype = resp.headers.get("content-type", "")
        if "json" in ctype:
            raise WhaleError("unavailable", f"{self.service} 没有返回音频")
        return resp.content, sniff_mime(resp.content, ctype or "audio/mpeg")

    async def _dashscope_tts(self, client: Any, text: str, voice: str) -> tuple[bytes, str]:
        body = {"model": self.tts_model, "input": {"text": text}, "parameters": {"voice": voice}}
        data = await request_json(client, "POST", f"{self.base}/services/audio/tts/SpeechSynthesizer",
                                  self.service, json=body)
        audio = self._output(data).get("audio")
        if not isinstance(audio, dict):
            raise WhaleError("unavailable", f"{self.service} 没有返回音频")
        if audio.get("data"):
            raw = _b64(str(audio["data"]), self.service)
        elif audio.get("url"):
            raw, _ = await download(client, str(audio["url"]), self.service, max_bytes=64 * 1024 * 1024)
        else:
            raise WhaleError("unavailable", f"{self.service} 音频既无数据也无链接")
        mime = sniff_mime(raw, "audio/wav")
        return (normalize_wav(raw) if mime == "audio/wav" else raw), mime


def setup(p: Any) -> MediaService | None:
    cfg = p.config
    provider = str(cfg.get("provider") or "openai")
    if provider not in DEFAULTS:
        raise WhaleError("invalid_params", f"未知服务商 {provider}（可选 openai / dashscope）")
    base = check_base_url(str(cfg.get("base_url") or "") or DEFAULTS[provider]["base_url"], "接口地址")
    key = p.secret("api_key")
    if not key and not is_private_http(base):
        p.log.info("media not configured (api_key missing)")
        return None
    svc = MediaService(p, provider, base, key, cfg)

    @p.capability("image.generate", "根据文字描述生成一张图片，保存为附件并返回附件 id。prompt 尽量具体（主体、风格、构图、光线）。",
                  params=obj(["prompt"], prompt=s("画面描述"),
                             size=s("比例或尺寸：1:1、16:9、9:16、4:3、3:4，或 1024x1024", default="1:1")),
                  risk="write", title="生成图片", modes=("chat", "goal"), timeout_s=240)
    async def image_generate(args: dict[str, Any], ctx: Any) -> Any:
        return await svc.generate_image(str(args["prompt"]), str(args.get("size") or "1:1"))

    @p.capability("speech.synthesize", "把一段文字合成为语音（音频附件），返回附件 id。",
                  params=obj(["text"], text=s("要朗读的文字"), voice=s("音色（留空用默认）", default="")),
                  risk="write", title="语音合成", modes=("chat", "goal"), timeout_s=120)
    async def speech_synthesize(args: dict[str, Any], ctx: Any) -> Any:
        return await svc.synthesize(str(args["text"]), str(args.get("voice") or ""))

    return svc
