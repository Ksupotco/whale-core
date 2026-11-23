"""云端 ASR（2026-11-20 主人令）：AgentScope 平台网关 qwen-audio-3.0-asr-flash。

契约（照 media.py 实测形态，勿改）：
- 模型钉 ``qwen-audio-3.0-asr-flash``（网关只放行 qwen-audio-3.0-* 系）；
- POST ``/v1/services/aigc/multimodal-generation/generation``，
  ``input.messages[0].content=[{"audio": <公网URL>}]``，
  ``parameters={"format": ..., "sample_rate": ...}``；
- **绝不** 传 ``result_format=message``（上游 400 被网关包装成误导性 502）；
- 文本在响应顶层 ``sentence.text``（兜底链：output.sentence → payload.sentence
  → output.choices[].message.content → output.text → payload.text）；
- 本地音频先 POST ``/v1/media/uploads``（Bearer 模型 key，multipart）换公网 URL。

护栏（Tob 2026-11-20）：
- 音频 ≤10MB；读超时默认 25s（20-30s 上限内）；
- 失败 fail-closed 抛 AsrError（诚实报错，不静默吞）；
- key 只经 env 引用，永不进日志/异常消息。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid

ASR_MODEL = "qwen-audio-3.0-asr-flash"
MAX_AUDIO_BYTES = 10 * 1024 * 1024
DEFAULT_TIMEOUT = 25.0


class AsrError(RuntimeError):
    """云端转写失败（fail-closed 诚实面）。kind: config/upload/http/empty。"""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _extract_text(payload: dict) -> str:
    """sentence.text 兜底链（照 media.py _extract_asr_text 语义）。"""
    output = payload.get("output") if isinstance(payload.get("output"), dict) else {}

    def _sentence_texts(container):
        sentence = container.get("sentence")
        items = sentence if isinstance(sentence, list) else (
            [sentence] if isinstance(sentence, dict) else [])
        return [str(i["text"]).strip() for i in items
                if isinstance(i, dict) and i.get("text")]

    # 顶层/输出层 sentence.text 优先（Token Plan 系在顶层）
    for container in (payload, output):
        texts = _sentence_texts(container)
        if texts:
            return " ".join(texts).strip()
    # choices 形态
    choices = output.get("choices") or []
    if choices:
        content = (choices[0].get("message") or {}).get("content") or []
        if isinstance(content, list):
            parts = [str(i["text"]).strip() for i in content
                     if isinstance(i, dict) and i.get("text")]
            parts += [i.strip() for i in content if isinstance(i, str) and i.strip()]
            if parts:
                return " ".join(parts).strip()
        elif isinstance(content, str) and content.strip():
            return content.strip()
    for container in (output, payload):
        direct = container.get("text")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
    return ""


def _upload(base: str, key: str, data: bytes, filename: str,
            mime: str, timeout: float) -> str:
    boundary = uuid.uuid4().hex
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{filename}\"\r\nContent-Type: {mime}\r\n\r\n").encode()
    body = head + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{base}/v1/media/uploads", data=body, method="POST",
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise AsrError("upload", f"upload HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AsrError("upload", f"upload network: {type(exc).__name__}") from exc
    items = payload.get("items") if isinstance(payload, dict) else None
    url = str((items[0] if items else {}).get("url") or "")
    if not url:
        raise AsrError("upload", "upload returned no url")
    return url


_MIME_BY_FMT = {"wav": "audio/wav", "mp3": "audio/mpeg", "m4a": "audio/mp4",
                "aac": "audio/aac", "flac": "audio/flac", "ogg": "audio/ogg",
                "opus": "audio/opus", "amr": "audio/amr", "webm": "audio/webm"}


def transcribe(data: bytes, *, fmt: str = "wav", sample_rate: str = "16000",
               base_url: str, api_key: str,
               timeout: float = DEFAULT_TIMEOUT) -> str:
    """音频字节 → 文本（云端 ASR）。失败抛 AsrError（fail-closed 诚实面）。"""
    if not api_key:
        raise AsrError("config", "asr api key not configured")
    if not data:
        raise AsrError("empty", "audio payload empty")
    if len(data) > MAX_AUDIO_BYTES:
        raise AsrError("too_large", "audio exceeds 10MB limit")
    fmt = (fmt or "wav").strip().lower()
    base = (base_url or "https://platform.agentscope.io").rstrip("/")
    # 契约路径自带 /v1——回落拿到的 chat 端点常带 /v1 尾，剥掉防 /v1/v1 404。
    if base.endswith("/v1"):
        base = base[:-3]
    timeout = min(max(float(timeout), 5.0), 30.0)  # 20-30s 上限纪律

    url = _upload(base, api_key, data, f"audio.{fmt}",
                  _MIME_BY_FMT.get(fmt, "audio/wav"), timeout)
    payload = {"model": ASR_MODEL,
               "input": {"messages": [{"role": "user", "content": [{"audio": url}]}]},
               "parameters": {"format": fmt, "sample_rate": str(sample_rate or "16000")}}
    req = urllib.request.Request(
        f"{base}/v1/services/aigc/multimodal-generation/generation",
        data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise AsrError("http", f"asr HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AsrError("http", f"asr network: {type(exc).__name__}") from exc
    text = _extract_text(result)
    if not text:
        raise AsrError("empty", "asr returned no text")
    return text
