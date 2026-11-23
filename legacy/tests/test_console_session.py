"""控制台同源会话测试（2026-11-18 控制台重构）。

口径锚点：
- POST /api/v1/console/session 用管理 token 换短时效 HttpOnly cookie；
  错误 token → 401，每 IP 连续失败 5 次锁 60s（429）；
- cookie 属性：HttpOnly / SameSite=Strict / Path=/api / Max-Age=12h；
  http 请求不带 Secure，X-Forwarded-Proto=https 时带 Secure；
- cookie 认证的 GET 放行；写操作（POST/PUT/DELETE）必须带
  X-Whale-Console: 1，否则 401（CSRF 纵深）；
- 会话过期/注销后 401；Bearer 通道行为不变；静态页保持公开。
"""
from __future__ import annotations

import json
import time
from http.client import HTTPConnection

import pytest

from whale_core.api.console_session import (
    CSRF_HEADER,
    SESSION_COOKIE,
    SESSION_TTL_S,
    ConsoleSessionStore,
    build_session_cookie,
    parse_session_cookie,
)
from whale_core.api.server import ApiContext, WhaleApiServer

TOKEN = "mgmt-test-token"


@pytest.fixture()
def api():
    from whale_core.memory.store import InMemoryStore
    ctx = ApiContext(store=InMemoryStore(), profile="demo")
    server = WhaleApiServer(ctx, port=0, token=TOKEN)
    server.start()
    yield server
    server.stop()


def _raw(server, method, path, *, body=None, headers=None):
    conn = HTTPConnection("127.0.0.1", server.port, timeout=15)
    payload = json.dumps(body) if body is not None else None
    h = dict(headers or {})
    if payload:
        h["Content-Type"] = "application/json"
    conn.request(method, path, body=payload, headers=h)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        parsed = {}
    return resp.status, parsed, dict(resp.getheaders())


def _issue(server, token=TOKEN, headers=None):
    status, body, resp_headers = _raw(
        server, "POST", "/api/v1/console/session",
        body={"token": token}, headers=headers)
    return status, body, resp_headers


def _cookie_of(resp_headers) -> str:
    set_cookie = resp_headers.get("Set-Cookie", "")
    return parse_session_cookie(set_cookie.split(";", 1)[0])


class TestIssue:
    def test_issue_success_cookie_flags(self, api):
        status, body, headers = _issue(api)
        assert status == 200 and body["result"]["ok"] is True
        set_cookie = headers.get("Set-Cookie", "")
        assert set_cookie.startswith(f"{SESSION_COOKIE}=")
        assert "HttpOnly" in set_cookie
        assert "SameSite=Strict" in set_cookie
        assert "Path=/api" in set_cookie
        assert f"Max-Age={SESSION_TTL_S}" in set_cookie
        # http 直连不加 Secure
        assert "Secure" not in set_cookie
        assert 0 < body["result"]["expires_at"] <= int(time.time()) + SESSION_TTL_S

    def test_issue_secure_when_forwarded_https(self, api):
        status, _, headers = _issue(api, headers={"X-Forwarded-Proto": "https"})
        assert status == 200
        assert "Secure" in headers.get("Set-Cookie", "")

    def test_wrong_token_401(self, api):
        status, body, headers = _issue(api, token="wrong")
        assert status == 401
        assert body["error"]["code"] == "unauthorized"
        assert "Set-Cookie" not in headers

    def test_throttle_after_five_failures(self, api):
        for _ in range(5):
            status, _, _ = _issue(api, token="wrong")
            assert status == 401
        status, body, _ = _issue(api, token="wrong")
        assert status == 429 and body["error"]["code"] == "throttled"
        # 锁定期内连正确 token 也被挡
        status, _, _ = _issue(api)
        assert status == 429

    def test_success_clears_failures(self, api):
        for _ in range(4):
            _issue(api, token="wrong")
        status, _, _ = _issue(api)
        assert status == 200


class TestSessionAuth:
    def test_get_status_with_cookie(self, api):
        _, _, headers = _issue(api)
        sid = _cookie_of(headers)
        assert sid
        status, body, _ = _raw(api, "GET", "/api/v1/status",
                               headers={"Cookie": f"{SESSION_COOKIE}={sid}"})
        assert status == 200 and body["result"]["ok"] is True

    def test_probe_session(self, api):
        _, _, headers = _issue(api)
        sid = _cookie_of(headers)
        status, body, _ = _raw(api, "GET", "/api/v1/console/session",
                               headers={"Cookie": f"{SESSION_COOKIE}={sid}"})
        assert status == 200 and body["result"]["ok"] is True

    def test_probe_without_cookie_401(self, api):
        status, body, _ = _raw(api, "GET", "/api/v1/console/session")
        assert status == 401 and body["error"]["code"] == "no_session"

    def test_write_requires_csrf_header(self, api):
        _, _, headers = _issue(api)
        sid = _cookie_of(headers)
        # 写操作缺 CSRF 头 → 401
        status, _, _ = _raw(api, "POST", "/api/v1/chat",
                            body={"text": "hi", "conversation_id": "t"},
                            headers={"Cookie": f"{SESSION_COOKIE}={sid}"})
        assert status == 401
        # 带 CSRF 头 → 过了鉴权（路由本体可能因能力缺失回 4xx/5xx，但不再是 401）
        status, _, _ = _raw(api, "POST", "/api/v1/chat",
                            body={"text": "hi", "conversation_id": "t"},
                            headers={"Cookie": f"{SESSION_COOKIE}={sid}",
                                     CSRF_HEADER: "1"})
        assert status != 401

    def test_expired_session_401(self, api):
        store = api._httpd.console_sessions  # type: ignore[attr-defined]
        _, _, headers = _issue(api)
        sid = _cookie_of(headers)
        # 直接把会话改成已过期（不改全局 TTL）
        store._sessions[sid] = time.time() - 1
        status, _, _ = _raw(api, "GET", "/api/v1/status",
                            headers={"Cookie": f"{SESSION_COOKIE}={sid}"})
        assert status == 401

    def test_revoke_then_401(self, api):
        _, _, headers = _issue(api)
        sid = _cookie_of(headers)
        status, body, del_headers = _raw(
            api, "DELETE", "/api/v1/console/session",
            headers={"Cookie": f"{SESSION_COOKIE}={sid}", CSRF_HEADER: "1"})
        assert status == 200 and body["result"]["revoked"] is True
        assert "Max-Age=0" in del_headers.get("Set-Cookie", "")
        status, _, _ = _raw(api, "GET", "/api/v1/status",
                            headers={"Cookie": f"{SESSION_COOKIE}={sid}"})
        assert status == 401

    def test_bearer_unaffected(self, api):
        status, body, _ = _raw(api, "GET", "/api/v1/status",
                               headers={"Authorization": f"Bearer {TOKEN}"})
        assert status == 200 and body["result"]["ok"] is True
        # Bearer 无需 CSRF 头
        status, _, _ = _raw(api, "POST", "/api/v1/chat",
                            body={"text": "hi", "conversation_id": "t"},
                            headers={"Authorization": f"Bearer {TOKEN}"})
        assert status != 401

    def test_garbage_cookie_401(self, api):
        status, _, _ = _raw(api, "GET", "/api/v1/status",
                            headers={"Cookie": f"{SESSION_COOKIE}=nonsense"})
        assert status == 401


class TestStore:
    def test_parse_cookie(self):
        assert parse_session_cookie("a=1; whale_console=abc123; b=2") == "abc123"
        assert parse_session_cookie("a=1") == ""
        assert parse_session_cookie("") == ""

    def test_issue_and_verify(self):
        now = [1000.0]
        store = ConsoleSessionStore(time_fn=lambda: now[0])
        sid = store.issue("1.2.3.4")
        assert sid and store.verify(sid)["expires_at"] == 1000 + SESSION_TTL_S
        now[0] += SESSION_TTL_S + 1
        assert store.verify(sid) is None

    def test_revoke(self):
        store = ConsoleSessionStore()
        sid = store.issue("ip")
        assert store.revoke(sid) is True
        assert store.verify(sid) is None
        assert store.revoke(sid) is False

    def test_lockout_window_slides(self):
        now = [1000.0]
        store = ConsoleSessionStore(time_fn=lambda: now[0])
        for _ in range(5):
            store.note_failure("9.9.9.9")
        assert store.issue("9.9.9.9") is None
        now[0] += 61
        assert store.issue("9.9.9.9") is not None

    def test_cookie_build_secure_flag(self):
        assert "Secure" in build_session_cookie("x", secure=True)
        assert "Secure" not in build_session_cookie("x", secure=False)
