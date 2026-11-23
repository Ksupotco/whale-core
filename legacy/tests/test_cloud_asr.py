"""云端 ASR 测试（2026-11-20 主人令 + Tob 护栏）。

覆盖：上传→转写链、sentence.text 兜底链、fail-closed 错误分类、
体积上限、模型钉死、拒 result_format、/v1/chat audio 字段（成功/错误/
缺省兼容——缺省纯文本旧行为不变）。
"""
from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from whale_core.life.cloud_asr import ASR_MODEL, AsrError, transcribe


class _AsrServer(BaseHTTPRequestHandler):
    seen = {}

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        if self.path == "/v1/media/uploads":
            type(self).seen["uploaded"] = True
            self._json({"items": [{"url": "https://cdn.example/a.wav"}]})
            return
        if self.path == "/v1/services/aigc/multimodal-generation/generation":
            payload = json.loads(body)
            type(self).seen["gen"] = payload
            self._json({"sentence": [{"text": "别玩手机了"}]})
            return
        self._json({"error": "not found"}, 404)

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture()
def server():
    _AsrServer.seen = {}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _AsrServer)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown(); srv.server_close(); t.join(5)


def test_transcribe_contract_shape(server):
    text = transcribe(b"RIFFxxxx", fmt="wav", base_url=server, api_key="k")
    assert text == "别玩手机了"
    gen = _AsrServer.seen["gen"]
    assert gen["model"] == ASR_MODEL
    assert gen["parameters"] == {"format": "wav", "sample_rate": "16000"}
    assert "result_format" not in gen["parameters"]
    assert gen["input"]["messages"][0]["content"] == [
        {"audio": "https://cdn.example/a.wav"}]


def test_transcribe_empty_audio_fails_closed(server):
    with pytest.raises(AsrError) as ei:
        transcribe(b"", base_url=server, api_key="k")
    assert ei.value.kind == "empty"


def test_transcribe_too_large_fails_closed(server):
    with pytest.raises(AsrError) as ei:
        transcribe(b"x" * (10 * 1024 * 1024 + 1), base_url=server, api_key="k")
    assert ei.value.kind == "too_large"


def test_transcribe_no_key_fails_closed(server):
    with pytest.raises(AsrError) as ei:
        transcribe(b"x", base_url=server, api_key="")
    assert ei.value.kind == "config"


def test_sentence_text_fallback_chain(server):
    """顶层 sentence.text 优先；output.choices 文本兜底。"""
    from whale_core.life.cloud_asr import _extract_text
    assert _extract_text({"sentence": [{"text": "甲"}, {"text": "乙"}]}) == "甲 乙"
    assert _extract_text({"output": {"sentence": {"text": "丙"}}}) == "丙"
    assert _extract_text({"output": {"choices": [{"message": {"content": [{"text": "丁"}]}}]}}) == "丁"
    assert _extract_text({"output": {"text": "戊"}}) == "戊"
    assert _extract_text({}) == ""


# ---- /v1/chat audio 字段 ----

def test_chat_audio_field_success(tmp_path, monkeypatch):
    from whale_core.api.glasses import make_handler
    import whale_core.life.cloud_asr as asr_mod
    from whale_core.memory.store import InMemoryStore

    calls = []

    def fake_transcribe(data, **kw):
        calls.append(kw)
        return "转写文本"

    monkeypatch.setattr(asr_mod, "transcribe", fake_transcribe)
    # handler 内 import 走的是模块属性——patch 模块属性即可
    delegates = {"store": InMemoryStore(),
                 "companion_chat": lambda text, **kw: ("收到：" + text, {"totalMs": 5})}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(delegates))
    srv.glasses_token = "tok"
    srv.glasses_env = {"QQ_INTENT_API_KEY": "k", "QQ_INTENT_BASE_URL": "https://x"}
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        import urllib.request
        body = json.dumps({"audio": {"base64": base64.b64encode(b"RIFF").decode(),
                                     "format": "wav"}}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{srv.server_port}/v1/chat",
                                     data=body, method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer tok"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.load(r)
        assert "转写文本" in d["reply"]
        assert calls
    finally:
        srv.shutdown(); srv.server_close()


def test_chat_without_audio_unchanged(tmp_path):
    """护栏 b：audio 缺省 = 纯文本旧行为完全兼容。"""
    from whale_core.api.glasses import make_handler
    from whale_core.memory.store import InMemoryStore
    delegates = {"store": InMemoryStore(),
                 "companion_chat": lambda text, **kw: ("回：" + text, {"totalMs": 5})}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(delegates))
    srv.glasses_token = "tok"
    srv.glasses_env = {}
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        import urllib.request
        body = json.dumps({"text": "你好"}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{srv.server_port}/v1/chat",
                                     data=body, method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer tok"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.load(r)
        assert d["reply"] == "回：你好"
    finally:
        srv.shutdown(); srv.server_close()


def test_chat_audio_invalid_base64_400(tmp_path):
    from whale_core.api.glasses import make_handler
    from whale_core.memory.store import InMemoryStore
    delegates = {"store": InMemoryStore(),
                 "companion_chat": lambda text, **kw: ("x", {"totalMs": 1})}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(delegates))
    srv.glasses_token = "tok"
    srv.glasses_env = {}
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        import urllib.request
        body = json.dumps({"audio": {"base64": "!!!not-base64!!!"}}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{srv.server_port}/v1/chat",
                                     data=body, method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer tok"})
        try:
            urllib.request.urlopen(req, timeout=10)
            raise AssertionError("should 400")
        except urllib.error.HTTPError as e:
            assert e.code == 400
    finally:
        srv.shutdown(); srv.server_close()
