"""Shared outbound-HTTP discipline: injectable client + truthful error mapping.

Tests (and embedders) can set ``app.http_transport = httpx.MockTransport(...)``;
every component that talks to an outside service builds its clients through
:func:`http_client`, so nothing reaches the network in tests. Secrets travel
only in headers and are never part of log lines or error messages.
"""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlsplit

import httpx

from .errors import WhaleError

UA = "whale-core/3 (+plugins)"


def http_client(app: Any, *, timeout: float = 15.0, base_url: str = "",
                headers: dict[str, str] | None = None, follow_redirects: bool = False) -> httpx.AsyncClient:
    kw: dict[str, Any] = {"timeout": httpx.Timeout(timeout), "follow_redirects": follow_redirects,
                          "headers": {"User-Agent": UA, **(headers or {})}}
    if base_url:
        kw["base_url"] = base_url
    transport = getattr(app, "http_transport", None)
    if transport is not None:
        kw["transport"] = transport
    return httpx.AsyncClient(**kw)


def is_private_http(url: str) -> bool:
    """http:// is only acceptable for loopback / private literal addresses."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme != "http":
        return False
    host = (parts.hostname or "").lower()
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def check_base_url(url: str, what: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not (url.startswith("https://") or is_private_http(url)):
        raise WhaleError("invalid_params", f"{what} 需要 https 地址（http 仅限本机/内网）")
    return url


def status_error(service: str, status: int, detail: str = "") -> WhaleError:
    detail = " ".join(str(detail or "").split())[:200]
    suffix = f"：{detail}" if detail else ""
    if status in (401, 403):
        return WhaleError("unauthorized", f"{service} 鉴权失败（HTTP {status}），请检查密钥配置")
    if status == 429:
        return WhaleError("rate_limited", f"{service} 请求过于频繁或额度用尽（HTTP 429）", retryable=True)
    if status == 404:
        return WhaleError("not_found", f"{service} 返回 404{suffix}")
    if 400 <= status < 500:
        return WhaleError("invalid_params", f"{service} 拒绝了请求（HTTP {status}）{suffix}")
    return WhaleError("unavailable", f"{service} 服务异常（HTTP {status}）", retryable=True)


def transport_error(service: str, exc: Exception) -> WhaleError:
    if isinstance(exc, httpx.TimeoutException):
        return WhaleError("timeout", f"{service} 请求超时", retryable=True)
    kind = repr(exc).split("(", 1)[0]
    return WhaleError("unavailable", f"{service} 无法连接（{kind}）", retryable=True)


def error_detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:200]
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("title") or err.get("detail") or "")
        return str(body.get("message") or body.get("msg") or err or "")
    return ""


async def request_json(client: httpx.AsyncClient, method: str, url: str, service: str, **kw: Any) -> Any:
    try:
        resp = await client.request(method, url, **kw)
    except httpx.HTTPError as exc:
        raise transport_error(service, exc) from None
    if resp.status_code >= 400:
        raise status_error(service, resp.status_code, error_detail(resp))
    try:
        return resp.json()
    except ValueError:
        raise WhaleError("unavailable", f"{service} 返回了无法解析的内容") from None


async def download(client: httpx.AsyncClient, url: str, service: str, max_bytes: int = 32 * 1024 * 1024
                   ) -> tuple[bytes, str]:
    """Fetch a provider result URL (https only, size-capped)."""
    if not (url.startswith("https://") or is_private_http(url)):
        raise WhaleError("unavailable", f"{service} 返回了非 https 的结果链接")
    try:
        async with client.stream("GET", url) as resp:
            if resp.status_code >= 400:
                raise status_error(service, resp.status_code)
            body = bytearray()
            async for chunk in resp.aiter_bytes():
                body += chunk
                if len(body) > max_bytes:
                    raise WhaleError("unavailable", f"{service} 结果文件过大")
            return bytes(body), resp.headers.get("content-type", "")
    except httpx.HTTPError as exc:
        raise transport_error(service, exc) from None
