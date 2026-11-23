"""Built-in capabilities: time, memory, web, notify, shell."""

from __future__ import annotations

import asyncio
import html
import ipaddress
import os
import re
import shlex
import socket
import sys
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from zoneinfo import ZoneInfo

import httpx

from ..kernel.errors import WhaleError
from ..memory.scopes import can_read, write_scope
from .spec import Risk, ToolContext, capability, i, obj, s

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"


# -- web helpers -----------------------------------------------------------------
def _public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "iframe"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr", "section", "article", "pre", "blockquote"}

    def reset(self) -> None:
        HTMLParser.reset(self)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        lines = [re.sub(r"[ \t\u3000]+", " ", l).strip() for l in raw.splitlines()]
        return "\n".join(l for l in lines if len(l) > 1)


def html_to_text(doc: str) -> tuple[str, str]:
    p = _TextExtractor()
    try:
        p.feed(doc)
    except Exception:
        pass
    return p.title.strip(), p.text()


async def _search_ddg(client: httpx.AsyncClient, q: str, n: int) -> list[dict[str, str]]:
    r = await client.post("https://html.duckduckgo.com/html/", data={"q": q}, headers={"User-Agent": UA})
    out = []
    for m in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
                         r'class="result__snippet"[^>]*>(.*?)</a>', r.text, re.S):
        href = m.group(1)
        if "uddg=" in href:
            href = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
        out.append({"title": html.unescape(re.sub("<.*?>", "", m.group(2))).strip(), "url": href,
                    "snippet": html.unescape(re.sub("<.*?>", "", m.group(3))).strip()})
        if len(out) >= n:
            break
    return out


async def _search_bing(client: httpx.AsyncClient, q: str, n: int) -> list[dict[str, str]]:
    r = await client.get(f"https://cn.bing.com/search?q={quote_plus(q)}", headers={"User-Agent": UA,
                                                                                    "Accept-Language": "zh-CN,zh;q=0.9"})
    out = []
    for block in re.findall(r'<li class="b_algo".*?</li>', r.text, re.S):
        m = re.search(r'<h2[^>]*><a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not m:
            continue
        snip = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        out.append({"title": html.unescape(re.sub("<.*?>", "", m.group(2))).strip(), "url": html.unescape(m.group(1)),
                    "snippet": html.unescape(re.sub("<.*?>", "", snip.group(1))).strip() if snip else ""})
        if len(out) >= n:
            break
    return out


async def _search_tavily(client: httpx.AsyncClient, q: str, n: int, key: str) -> list[dict[str, str]]:
    r = await client.post("https://api.tavily.com/search", json={"api_key": key, "query": q, "max_results": n})
    r.raise_for_status()
    return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("content", "")[:400]}
            for x in r.json().get("results", [])]


async def _search_brave(client: httpx.AsyncClient, q: str, n: int, key: str) -> list[dict[str, str]]:
    r = await client.get("https://api.search.brave.com/res/v1/web/search", params={"q": q, "count": n},
                         headers={"X-Subscription-Token": key, "Accept": "application/json"})
    r.raise_for_status()
    return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("description", "")}
            for x in r.json().get("web", {}).get("results", [])]


def register(app: Any) -> None:
    reg = app.capabilities

    # -- time ---------------------------------------------------------------------
    @capability(reg, "time.now", "获取当前日期、时间与星期。", title="当前时间", timeout_s=5)
    async def time_now(args: dict[str, Any], ctx: ToolContext) -> Any:
        tz = app.config.value.agent.timezone
        now = datetime.now(ZoneInfo(tz))
        return {"iso": now.isoformat(timespec="seconds"), "weekday": "一二三四五六日"[now.weekday()], "timezone": tz,
                "summary": now.strftime("%Y-%m-%d %H:%M")}

    # -- memory -------------------------------------------------------------------
    @capability(reg, "memory.remember", "把一条信息记入长期记忆。content 要自足完整（第三人称描述用户或事物）。",
                params=obj(["content"], content=s("要记住的内容"),
                           kind=s("类型", enum=["fact", "preference", "episode", "knowledge", "person", "skill"],
                                  default="fact"),
                           importance={"type": "number", "description": "0-1 重要度", "default": 0.6}),
                risk=Risk.write, title="记住", modes=("chat", "goal"))
    async def remember(args: dict[str, Any], ctx: ToolContext) -> Any:
        res = await app.memory.remember(args["content"], kind=args.get("kind", "fact"),
                                        importance=float(args.get("importance", 0.6)),
                                        source={"run_id": ctx.run_id, "conversation_id": ctx.conversation_id,
                                                "explicit": True},
                                        scope=write_scope(ctx.scope, args.get("kind", "fact")))
        verb = "已加强已有记忆" if res["action"] == "reinforced" else "已记住"
        return {"id": res["item"]["id"], "action": res["action"], "summary": f"{verb}：{res['item']['content']}"}

    @capability(reg, "memory.search", "在长期记忆中搜索。", params=obj(["query"], query=s("查询"),
                                                                 limit=i("数量", default=6)),
                title="搜索记忆")
    async def memory_search(args: dict[str, Any], ctx: ToolContext) -> Any:
        hits = await app.memory.retrieve(args["query"], limit=int(args.get("limit") or 6), scope=ctx.scope)
        return {"results": [{"id": h.item.id, "kind": h.item.kind, "content": h.item.content,
                             "score": round(h.score, 3)} for h in hits],
                "summary": f"找到 {len(hits)} 条"}

    @capability(reg, "memory.forget", "按 id 永久遗忘一条记忆（先用 memory.search 找到 id）。",
                params=obj(["id"], id=s("记忆 id")), risk=Risk.write, title="遗忘", modes=("chat",))
    async def memory_forget(args: dict[str, Any], ctx: ToolContext) -> Any:
        item = app.memory.store.get(args["id"])
        if ctx.scope != "owner" and item.scope != ctx.scope:
            raise WhaleError("forbidden", "this memory does not belong to this conversation")
        if not can_read(ctx.scope, item.scope):
            raise WhaleError("not_found", "memory not found")
        await app.memory.forget(args["id"])
        return {"summary": "已遗忘"}

    # -- web ----------------------------------------------------------------------
    @capability(reg, "web.search", "搜索互联网，返回标题、链接与摘要。", params=obj(["query"], query=s("搜索词"),
                                                                     limit=i("结果数", default=5)),
                title="网页搜索", group="web", timeout_s=30)
    async def web_search(args: dict[str, Any], ctx: ToolContext) -> Any:
        q, n = args["query"], max(1, min(int(args.get("limit") or 5), 10))
        cfg = app.config.value.plugins.get("web", {})
        provider = cfg.get("provider", "auto")
        errors = []
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            order = [provider] if provider != "auto" else ["tavily", "brave", "bing", "duckduckgo"]
            for name in order:
                try:
                    if name == "tavily" and app.config.secret("web.tavily"):
                        results = await _search_tavily(client, q, n, app.config.secret("web.tavily"))
                    elif name == "brave" and app.config.secret("web.brave"):
                        results = await _search_brave(client, q, n, app.config.secret("web.brave"))
                    elif name == "bing":
                        results = await _search_bing(client, q, n)
                    elif name == "duckduckgo":
                        results = await _search_ddg(client, q, n)
                    else:
                        continue
                except Exception as exc:
                    errors.append(f"{name}: {exc}")
                    continue
                if results:
                    return {"provider": name, "results": results[:n],
                            "summary": f"{name} 找到 {len(results[:n])} 条：" + "；".join(r["title"] for r in results[:3])}
        raise WhaleError("unavailable", "all search providers failed", details={"errors": errors})

    @capability(reg, "web.fetch", "读取一个网页的正文文本。", params=obj(["url"], url=s("http(s) 链接"),
                                                                max_chars=i("最多返回字符", default=6000)),
                title="阅读网页", group="web", timeout_s=30)
    async def web_fetch(args: dict[str, Any], ctx: ToolContext) -> Any:
        url = args["url"]
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise WhaleError("invalid_params", "only http(s) URLs")
        allow_private = bool(app.config.value.plugins.get("web", {}).get("allow_private", False))
        if not allow_private and not await asyncio.to_thread(_public_host, parsed.hostname):
            raise WhaleError("forbidden", "refusing to fetch a private/loopback address")
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": UA}) as client:
            async with client.stream("GET", url) as r:
                if r.status_code >= 400:
                    raise WhaleError("unavailable", f"HTTP {r.status_code}")
                body = b""
                async for chunk in r.aiter_bytes():
                    body += chunk
                    if len(body) > 3_000_000:
                        break
                ctype = r.headers.get("content-type", "")
                enc = r.encoding or "utf-8"
        text = body.decode(enc, "replace")
        title = ""
        if "html" in ctype or text.lstrip().startswith("<"):
            title, text = html_to_text(text)
        limit = int(args.get("max_chars") or 6000)
        return {"url": url, "title": title, "text": text[:limit], "truncated": len(text) > limit,
                "summary": f"{title or url}（{len(text)} 字）"}

    # -- notify -------------------------------------------------------------------
    @capability(reg, "notify.owner", "主动给主人发一条通知/消息（会推送到在线的手机、控制台与已接入的渠道）。",
                params=obj(["text"], text=s("消息内容"), title=s("标题", default="")),
                risk=Risk.external, title="通知主人", modes=("chat", "goal"))
    async def notify_owner(args: dict[str, Any], ctx: ToolContext) -> Any:
        receipt = await app.hub.notify(args.get("title") or app.config.value.agent.name, args["text"],
                                       {"run_id": ctx.run_id, "kind": "message"})
        return {"delivered_to": receipt, "summary": f"已推送到 {len(receipt)} 个端"}

    # -- shell --------------------------------------------------------------------
    @capability(reg, "shell.run", "在服务器的工作目录中执行一条命令并返回输出（有超时）。",
                params=obj(["command"], command=s("命令"), timeout_s=i("超时秒数", default=30)),
                risk=Risk.dangerous, title="执行命令", modes=("chat", "goal"), timeout_s=130, owner_only=True)
    async def shell_run(args: dict[str, Any], ctx: ToolContext) -> Any:
        workdir = Path(app.data_dir or ".") / "workspace"
        workdir.mkdir(parents=True, exist_ok=True)
        timeout = max(1, min(int(args.get("timeout_s") or 30), 120))
        if sys.platform == "win32":
            proc = await asyncio.create_subprocess_shell(args["command"], cwd=workdir,
                                                         stdout=asyncio.subprocess.PIPE,
                                                         stderr=asyncio.subprocess.STDOUT)
        else:
            proc = await asyncio.create_subprocess_exec("/bin/sh", "-c", args["command"], cwd=workdir,
                                                        stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.STDOUT,
                                                        env={**os.environ, "HOME": str(workdir)})
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return {"exit_code": None, "output": "", "summary": f"超时（{timeout}s）已终止"}
        text = out.decode("utf-8", "replace")
        return {"exit_code": proc.returncode, "output": text[-8000:],
                "summary": f"退出码 {proc.returncode}，输出 {len(text)} 字"}

    _ = shlex  # (kept for plugins that want quoting helpers)
