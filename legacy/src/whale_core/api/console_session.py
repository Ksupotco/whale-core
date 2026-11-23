"""控制台同源会话：管理 token 一次性交换短时效 HttpOnly cookie。

设计依据（2026-11-18 控制台重构）：
- 现状：管理面全部 /api/ 强制 Bearer（hmac.compare_digest）；静态页不鉴权
  （SPA 需先加载才能走登录流程）。新控制台部署态默认连真实服务，不能
  把管理 token 交给 JS 长期持有。
- 机制：POST /api/v1/console/session 用现有管理凭据换短时效会话 cookie；
  会话只存服务端内存（进程重启即失效，fail-closed 方向），不落盘。
- cookie 属性：HttpOnly（JS 读不到）、SameSite=Strict（跨站带不出）、
  Path=/api（只随 API 请求，不上静态/HTML）、Max-Age=12h；https 请求
  （X-Forwarded-Proto=https，反代终结 TLS 的场景）追加 Secure。
- 防爆破：每客户端 IP 签发失败 5 次锁 60s（内存计数，不落库）。
- CSRF 纵深：SameSite=Strict 为主；cookie 认证的写操作（POST/PUT/DELETE）
  额外强制 ``X-Whale-Console: 1`` 自定义头——跨站表单与跨域 fetch 都带不
  动自定义头，且本站不开 CORS 预检。Bearer 通道不受影响（既有脚本零变化）。
- CSP 不变（cookie 是响应头，与 script-src 无关）；不开任何 CORS。
"""

from __future__ import annotations

import hmac
import json
import secrets
import threading
import time

from whale_core.api.server import _error, _json_bytes

#: 会话 cookie 名（不带 __Host- 前缀：生产可经 http 回环/tailscale 访问，
#: Secure 在 https 下自适应追加）
SESSION_COOKIE = "whale_console"
#: 会话时效（固定 12h，到期须重新认证）
SESSION_TTL_S = 12 * 3600
#: cookie 认证的写操作必须携带的自定义头（CSRF 纵深）
CSRF_HEADER = "X-Whale-Console"
#: 防爆破：连续失败上限与锁定时长
_MAX_FAILS = 5
_LOCKOUT_S = 60

_WRITE_METHODS = ("POST", "PUT", "DELETE")


class ConsoleSessionStore:
    """服务端内存会话表：{session_id: expires_at} + 每 IP 签发防爆破。"""

    def __init__(self, ttl_s: int = SESSION_TTL_S, time_fn=time.time) -> None:
        self._sessions: dict[str, float] = {}
        self._fails: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()
        self._ttl = ttl_s
        self._now = time_fn

    def _locked_remaining(self, ip: str) -> int:
        """（内部，调用方须持锁）签发锁定期剩余秒数（0 = 未锁定）。"""
        rec = self._fails.get(ip)
        if not rec:
            return 0
        count, first_at = rec
        if count < _MAX_FAILS:
            return 0
        return max(0, int(first_at + _LOCKOUT_S - self._now()))

    def locked_remaining(self, ip: str) -> int:
        with self._lock:
            return self._locked_remaining(ip)

    def issue(self, ip: str) -> str | None:
        """签发新会话；锁定中返回 None。会话 ID 为 32 字节随机。"""
        with self._lock:
            if self._locked_remaining(ip) > 0:
                return None
            session_id = secrets.token_urlsafe(32)
            self._sessions[session_id] = self._now() + self._ttl
            return session_id

    def note_failure(self, ip: str) -> None:
        with self._lock:
            count, first_at = self._fails.get(ip, (0, 0.0))
            if self._now() - first_at > _LOCKOUT_S:
                count, first_at = 0, self._now()
            self._fails[ip] = (count + 1, first_at or self._now())

    def note_success(self, ip: str) -> None:
        with self._lock:
            self._fails.pop(ip, None)

    def verify(self, session_id: str) -> dict | None:
        """有效 → {"session_id", "expires_at"}；无效/过期 → None（并顺手清除）。"""
        with self._lock:
            exp = self._sessions.get(session_id)
            if exp is None:
                return None
            if exp <= self._now():
                del self._sessions[session_id]
                return None
            return {"session_id": session_id, "expires_at": exp}

    def revoke(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None


# ---------------------------------------------------------------- cookie 编解码


def parse_session_cookie(cookie_header: str) -> str:
    """从 Cookie 头取会话 ID（只认 whale_console，忽略其余）。"""
    for part in (cookie_header or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == SESSION_COOKIE and value:
            return value
    return ""


def build_session_cookie(session_id: str, *, secure: bool) -> str:
    parts = [
        f"{SESSION_COOKIE}={session_id}",
        "Path=/api",
        "HttpOnly",
        "SameSite=Strict",
        f"Max-Age={SESSION_TTL_S}",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def build_clear_cookie(*, secure: bool) -> str:
    parts = [
        f"{SESSION_COOKIE}=",
        "Path=/api",
        "HttpOnly",
        "SameSite=Strict",
        "Max-Age=0",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def _is_secure_request(handler) -> bool:
    return (handler.headers.get("X-Forwarded-Proto", "").lower() == "https")


def _store(handler) -> ConsoleSessionStore:
    return handler.server.console_sessions  # type: ignore[attr-defined]


def cookie_authorized(handler) -> bool:
    """cookie 会话鉴权（写操作强制 CSRF 自定义头）。供 server._route 调用。"""
    store = getattr(handler.server, "console_sessions", None)
    if store is None:
        return False
    session_id = parse_session_cookie(handler.headers.get("Cookie", ""))
    if not session_id:
        return False
    if store.verify(session_id) is None:
        return False
    if handler.command in _WRITE_METHODS:
        # 纵深：自定义头跨站带不动；SameSite=Strict 之外的一道
        if handler.headers.get(CSRF_HEADER) != "1":
            return False
    return True


def handle_console_session(handler, method: str) -> None:
    """/api/v1/console/session 三端点（自鉴权，不经 Bearer 门）。

    - POST   {token}  用管理凭据换会话（每 IP 5 次失败锁 60s）；
    - GET             探测当前会话（前端首开据此自动连接）；
    - DELETE          注销并清 cookie（须 CSRF 头）。
    """
    send = handler._send
    store = _store(handler)
    secure = _is_secure_request(handler)
    ip = handler.client_address[0] if handler.client_address else "unknown"

    if method == "POST":
        locked = store.locked_remaining(ip)
        if locked > 0:
            send(429, _error("throttled", f"尝试过于频繁，请 {locked} 秒后重试"))
            return
        body = handler._body_json()
        token = str((body or {}).get("token") or "")
        expected = handler.server.token  # type: ignore[attr-defined]
        if not token or not hmac.compare_digest(token.encode(), expected.encode()):
            store.note_failure(ip)
            send(401, _error("unauthorized", "访问令牌无效"))
            return
        session_id = store.issue(ip)
        if session_id is None:
            send(429, _error("throttled", "尝试过于频繁，请稍后再试"))
            return
        store.note_success(ip)
        handler.ctx.log_event("console:session", "issued")
        body_out = _json_bytes({"result": {
            "ok": True, "expires_at": int(store.verify(session_id)["expires_at"]),
        }})
        # _send 不支持自定义头，此处手写响应（保持与 _send 相同的安全头）
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body_out)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.send_header("Referrer-Policy", "no-referrer")
        handler.send_header("Set-Cookie", build_session_cookie(session_id, secure=secure))
        handler.end_headers()
        try:
            handler.wfile.write(body_out)
        except (BrokenPipeError, ConnectionResetError):
            pass
        return

    if method == "GET":
        session_id = parse_session_cookie(handler.headers.get("Cookie", ""))
        sess = store.verify(session_id) if session_id else None
        if sess is None:
            send(401, _error("no_session", "无有效控制台会话"))
            return
        send(200, _json_bytes({"result": {
            "ok": True, "expires_at": int(sess["expires_at"]),
        }}))
        return

    if method == "DELETE":
        if handler.headers.get(CSRF_HEADER) != "1":
            send(401, _error("unauthorized", "missing console header"))
            return
        session_id = parse_session_cookie(handler.headers.get("Cookie", ""))
        sess = store.verify(session_id) if session_id else None
        if sess is None:
            send(401, _error("no_session", "no active session"))
            return
        store.revoke(session_id)
        handler.ctx.log_event("console:session", "revoked")
        body_out = _json_bytes({"result": {"ok": True, "revoked": True}})
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body_out)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.send_header("Referrer-Policy", "no-referrer")
        handler.send_header("Set-Cookie", build_clear_cookie(secure=secure))
        handler.end_headers()
        try:
            handler.wfile.write(body_out)
        except (BrokenPipeError, ConnectionResetError):
            pass
        return

    send(405, _error("method_not_allowed", "use GET/POST/DELETE"))
