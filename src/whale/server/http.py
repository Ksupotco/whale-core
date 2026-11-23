"""HTTP/WebSocket server (Starlette).

    GET  /api/health                     liveness (no auth)
    GET  /api/schema                     every RPC method + params schema
    POST /api/rpc/{method}               JSON params → {"ok": true, "result": …} | {"ok": false, "error": …}
    GET  /api/events?topics=a.*,b        Server-Sent Events stream of bus events
    POST /api/chat/stream                SSE: run one chat turn, stream deltas + steps
    WS   /api/link                       Link protocol v1 (hello → rpc/events)
    /                                    console (static, no build step)
"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import hmac
import json
import logging
import mimetypes
from pathlib import Path
from typing import Any, AsyncIterator

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from ..agent.runtime import RunRequest
from ..kernel.errors import WhaleError
from ..kernel.ids import new_id
from ..link.protocol import PROTOCOL, Client, envelope

log = logging.getLogger("whale.server")

# Windows often maps .js to text/plain in the registry; with nosniff the browser would refuse the console.
for _ext, _type in ((".js", "text/javascript"), (".mjs", "text/javascript"), (".css", "text/css"),
                     (".svg", "image/svg+xml"), (".json", "application/json"), (".wasm", "application/wasm")):
    mimetypes.add_type(_type, _ext)


def web_root() -> Path | None:
    here = Path(__file__).resolve()
    for cand in (here.parent.parent / "web", here.parents[3] / "web"):
        if (cand / "index.html").is_file():
            return cand
    return None


CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; "
       "connect-src 'self' ws: wss:; font-src 'self' data:; frame-ancestors 'none'; base-uri 'self'")

# Link 单帧上限（字节）：大负载一律走 artifacts/REST，不在控制通道串送。
LINK_MAX_FRAME_BYTES = 1 * 1024 * 1024


class SecurityHeaders:
    """Pure ASGI middleware: strict CSP and friends on every HTTP response."""

    HEADERS = ((b"content-security-policy", CSP.encode()), (b"x-content-type-options", b"nosniff"),
               (b"referrer-policy", b"no-referrer"))

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def wrapped(message: Any) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                names = {k.lower() for k, _ in headers}
                headers += [(k, v) for k, v in self.HEADERS if k not in names]
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, wrapped)


def _token_ok(app: Any, supplied: str | None) -> bool:
    return bool(supplied) and hmac.compare_digest(str(supplied), app.config.api_token)


def _bearer(request: Request) -> str | None:
    h = request.headers.get("authorization", "")
    if h.lower().startswith("bearer "):
        return h[7:].strip()
    return request.query_params.get("token")


def _err(exc: WhaleError) -> JSONResponse:
    return JSONResponse({"ok": False, "error": exc.to_dict()}, status_code=exc.http_status)


def _sse(event: str, data: Any) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n".encode()


def create_server(app: Any, *, manage_lifecycle: bool = True) -> Starlette:
    async def health(request: Request) -> Response:
        return JSONResponse({"ok": True, "version": app.version, "protocol": PROTOCOL})

    async def schema(request: Request) -> Response:
        if not _token_ok(app, _bearer(request)):
            return _err(WhaleError("unauthorized", "missing or invalid token"))
        return JSONResponse({"protocol": PROTOCOL, "methods": app.rpc.schema()})

    async def rpc(request: Request) -> Response:
        if not _token_ok(app, _bearer(request)):
            return _err(WhaleError("unauthorized", "missing or invalid token"))
        method = request.path_params["method"]
        try:
            body = await request.body()
            params = json.loads(body) if body else {}
            if not isinstance(params, dict):
                raise WhaleError("invalid_params", "params must be a JSON object")
        except json.JSONDecodeError:
            return _err(WhaleError("invalid_params", "body is not valid JSON"))
        try:
            result = await app.rpc.call(method, params, None)
        except WhaleError as exc:
            return _err(exc)
        except Exception as exc:
            log.exception("rpc %s failed", method)
            return _err(WhaleError("internal", repr(exc)))
        return JSONResponse({"ok": True, "result": result}, headers={"Cache-Control": "no-store"})

    async def events(request: Request) -> Response:
        if not _token_ok(app, _bearer(request)):
            return _err(WhaleError("unauthorized", "missing or invalid token"))
        topics = [t for t in request.query_params.get("topics", "*").split(",") if t]
        sub = app.bus.stream(topics)

        async def gen() -> AsyncIterator[bytes]:
            try:
                yield _sse("hello", {"protocol": PROTOCOL})
                while True:
                    try:
                        ev = await asyncio.wait_for(sub.get(), 20)
                        yield _sse("event", ev.to_dict())
                    except asyncio.TimeoutError:
                        yield b": keepalive\n\n"
            finally:
                sub.close()

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    async def chat_stream(request: Request) -> Response:
        """For HTTP-only clients (phones, scripts): one turn, streamed.

        F2：除 Bearer 外接受 Link ticket（X-Whale-Link-Ticket 头）——纯 C 部署下手机端
        没有主 token，SSE 退路必须能走票。票据来源的渠道默认 mobile（与 Link role 口径一致）。
        """
        auth = _link_auth(request)
        if auth is None:
            return _err(WhaleError("unauthorized", "missing or invalid credential"))
        try:
            body = json.loads(await request.body() or b"{}")
            text = str(body.get("text") or "").strip()
            if not text:
                raise WhaleError("invalid_params", "text is required")
        except (json.JSONDecodeError, WhaleError) as exc:
            return _err(exc if isinstance(exc, WhaleError) else WhaleError("invalid_params", "bad JSON"))
        queue: asyncio.Queue[tuple[str, Any] | None] = asyncio.Queue()
        req = RunRequest(text=text, conversation_id=body.get("conversation_id"),
                         channel=str(body.get("channel") or ("mobile" if auth else "http"))[:24],
                         images=list(body.get("images") or [])[:4],
                         on_delta=lambda d: queue.put_nowait(("delta", {"text": d})))
        sub = app.bus.stream(["run.step", "run.started"])

        async def runner() -> None:
            try:
                out = await app.agent.run(req)
                await queue.put(("done", {"run_id": out.run["id"], "status": out.status, "text": out.text,
                                          "conversation_id": out.run.get("conversation_id"),
                                          "error": out.run.get("error", "")}))
            finally:
                await queue.put(None)

        async def pump_steps() -> None:
            run_id = None
            while True:
                ev = await sub.get()
                if ev.topic == "run.started" and run_id is None and ev.data["run"]["input"] == text:
                    run_id = ev.data["run"]["id"]
                    await queue.put(("run", {"run_id": run_id,
                                             "conversation_id": ev.data["run"].get("conversation_id")}))
                elif ev.topic == "run.step" and ev.data["step"]["run_id"] == run_id:
                    await queue.put(("step", ev.data["step"]))

        async def gen() -> AsyncIterator[bytes]:
            task = asyncio.create_task(runner())
            pump = asyncio.create_task(pump_steps())
            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    yield _sse(item[0], item[1])
            finally:
                pump.cancel()
                sub.close()
                if not task.done():
                    task.cancel()

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    async def link(ws: WebSocket) -> None:
        await ws.accept()
        # One ordered outbox per connection: events published while a request is
        # being handled are delivered before that request's response.
        outbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=5000)

        async def send(env: dict[str, Any]) -> None:
            await outbox.put(env)

        async def writer() -> None:
            while True:
                env = await outbox.get()
                kick = env.get("__kick__")
                if kick:
                    # 服务端主动关闭（如设备撤销联动）：先告原因再关，不断无音信
                    await ws.send_text(json.dumps(
                        envelope("error", {"code": str(kick), "message": str(env.get("message") or "")}),
                        ensure_ascii=False))
                    await ws.close(code=4403)
                    break
                await ws.send_text(json.dumps(env, ensure_ascii=False, default=str))

        writer_task = asyncio.create_task(writer())

        try:
            first_raw = await asyncio.wait_for(ws.receive_text(), 10)
            if len(first_raw.encode("utf-8", "replace")) > LINK_MAX_FRAME_BYTES:
                await ws.send_json(envelope("error", {"code": "invalid_params", "message": "frame too large"}))
                await ws.close(code=4400)
                writer_task.cancel()
                return
            first = json.loads(first_raw)
        except Exception:
            writer_task.cancel()
            await ws.close(code=4400)
            return
        hello = first.get("payload") or {}
        if first.get("type") != "hello":
            await ws.send_json(envelope("error", {"code": "invalid_params", "message": "first frame must be hello"}))
            await ws.close(code=4400)
            writer_task.cancel()
            return
        token = hello.get("token") or ws.query_params.get("token")
        ticket = str(hello.get("ticket") or "").strip()
        device_id = ""
        if ticket:
            # C 鉴权：设备线签发的短期 Link 票；票据客户端固定 role=mobile、绑 device_id
            try:
                device_id = app.devices.validate_link_ticket(ticket)
            except WhaleError as exc:
                await ws.send_json(envelope("error", {"code": exc.code, "message": exc.message}, first.get("id")))
                await ws.close(code=4401)
                writer_task.cancel()
                return
            if str(hello.get("role") or "mobile") != "mobile":
                await ws.send_json(envelope("error", {"code": "invalid_params",
                                                      "message": "ticket clients must use role=mobile"},
                                            first.get("id")))
                await ws.close(code=4400)
                writer_task.cancel()
                return
        elif not _token_ok(app, token):
            await ws.send_json(envelope("error", {"code": "unauthorized", "message": "invalid token"}, first.get("id")))
            await ws.close(code=4401)
            writer_task.cancel()
            return
        client = Client(id=new_id("cli"), role=("mobile" if device_id else str(hello.get("role") or "console")[:16]),
                        name=str(hello.get("name") or "client")[:40], send=send,
                        subscriptions=[str(p) for p in (hello.get("subscribe") or ["*"])][:50],
                        capabilities=[c for c in (hello.get("capabilities") or []) if isinstance(c, dict)][:100],
                        meta={k: hello[k] for k in ("device", "version", "platform") if k in hello})
        if device_id:
            client.meta["device_id"] = device_id
        await app.hub.attach(client)
        await send(envelope("welcome", {"client_id": client.id, "protocol": PROTOCOL, "server": {
            "name": app.config.value.agent.name, "version": app.version}, "methods": sorted(app.rpc.methods)},
            first.get("id")))
        sub = app.bus.stream(["*"])

        def offer(ev: Any) -> None:
            if any(fnmatch.fnmatchcase(ev.topic, p) for p in client.subscriptions):
                try:
                    outbox.put_nowait(envelope("event", {"topic": ev.topic, "data": ev.data, "event_id": ev.id,
                                                         "ts": ev.ts}))
                except asyncio.QueueFull:
                    pass  # a stalled client loses events, never blocks the agent

        sub.offer = offer  # type: ignore[method-assign]

        async def handle(env: dict[str, Any]) -> None:
            payload = env.get("payload") or {}
            try:
                result = await app.rpc.call(str(payload.get("method", "")), payload.get("params") or {}, client)
                await send(envelope("rpc.response", {"ok": True, "result": result}, env.get("id")))
            except WhaleError as exc:
                await send(envelope("rpc.response", {"ok": False, "error": exc.to_dict()}, env.get("id")))
            except Exception as exc:
                log.exception("ws rpc failed")
                await send(envelope("rpc.response", {"ok": False, "error": WhaleError(
                    "internal", repr(exc)).to_dict()}, env.get("id")))

        inflight: set[asyncio.Task[Any]] = set()
        try:
            while True:
                raw = await ws.receive_text()
                if len(raw.encode("utf-8", "replace")) > LINK_MAX_FRAME_BYTES:
                    await send(envelope("error", {"code": "invalid_params", "message": "frame too large"}))
                    continue
                try:
                    env = json.loads(raw)
                except json.JSONDecodeError:
                    await send(envelope("error", {"code": "invalid_params", "message": "invalid JSON"}))
                    continue
                kind = env.get("type")
                if kind == "rpc.request":
                    t = asyncio.create_task(handle(env))
                    inflight.add(t)
                    t.add_done_callback(inflight.discard)
                elif kind == "rpc.response":
                    app.hub.resolve_response(client, env)
                elif kind == "ping":
                    await send(envelope("pong", {}, env.get("id")))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            for t in inflight:
                t.cancel()
            sub.close()
            await app.hub.detach(client.id)
            writer_task.cancel()

    async def hook(request: Request) -> Response:
        key = (request.path_params["plugin"], request.path_params["path"].strip("/"))
        handler = app.hooks.get(key)
        if handler is None:
            return _err(WhaleError("not_found", "no such hook"))
        try:
            return await handler(request)
        except WhaleError as exc:
            return _err(exc)
        except Exception as exc:
            log.exception("hook %s failed", key)
            return _err(WhaleError("internal", repr(exc)))

    def _link_auth(request: Request) -> "str | None":
        """Bearer token 或 Link ticket（X-Whale-Link-Ticket 头 / ?ticket=）。
        返回 ""=token 通过 / device_id=ticket 通过 / None=未授权（fail-closed）。"""
        if _token_ok(app, _bearer(request)):
            return ""
        ticket = request.headers.get("x-whale-link-ticket") or request.query_params.get("ticket") or ""
        if ticket:
            try:
                return app.devices.validate_link_ticket(ticket)
            except WhaleError:
                return None
        return None

    def _artifact_authed(request: Request) -> bool:
        return _link_auth(request) is not None

    async def artifact(request: Request) -> Response:
        if not _artifact_authed(request):
            return _err(WhaleError("unauthorized", "missing or invalid credential"))
        try:
            info, data = app.artifacts.read(request.path_params["id"])
        except WhaleError as exc:
            return _err(exc)
        return Response(data, media_type=info["content_type"], headers={
            "Cache-Control": "private, max-age=86400",
            "Content-Disposition": f'inline; filename="{info["name"]}"'})

    async def artifact_upload(request: Request) -> Response:
        """附件上行：raw body + X-Whale-Name / X-Whale-Content-Type 头 → 201 ArtifactRef（含 sha256）。"""
        if not _artifact_authed(request):
            return _err(WhaleError("unauthorized", "missing or invalid credential"))
        data = await request.body()
        if not data:
            return _err(WhaleError("invalid_params", "empty body"))
        import hashlib

        name = request.headers.get("x-whale-name", "")[:80]
        ctype = request.headers.get("x-whale-content-type") or "application/octet-stream"
        try:
            info = app.artifacts.save(data, content_type=ctype, name=name)
        except WhaleError as exc:
            return _err(exc)
        return JSONResponse({"ok": True, "result": {**info, "sha256": hashlib.sha256(data).hexdigest()}},
                            status_code=201, headers={"Cache-Control": "no-store"})

    async def artifact_delete(request: Request) -> Response:
        if not _artifact_authed(request):
            return _err(WhaleError("unauthorized", "missing or invalid credential"))
        try:
            app.artifacts.delete(request.path_params["id"])
        except WhaleError as exc:
            return _err(exc)
        return JSONResponse({"ok": True, "result": {"deleted": request.path_params["id"]}},
                            headers={"Cache-Control": "no-store"})

    root = web_root()

    async def index(request: Request) -> Response:
        if root is None:
            return Response("console not bundled; API is available at /api", media_type="text/plain")
        return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})

    routes: list[Any] = [
        Route("/api/health", health),
        Route("/api/schema", schema),
        Route("/api/rpc/{method}", rpc, methods=["POST"]),
        Route("/api/events", events),
        Route("/api/chat/stream", chat_stream, methods=["POST"]),
        WebSocketRoute("/api/link", link),
        Route("/api/hooks/{plugin}/{path:path}", hook, methods=["GET", "POST", "PUT"]),
        Route("/api/artifacts", artifact_upload, methods=["POST"]),
        Route("/api/artifacts/{id}", artifact),
        Route("/api/artifacts/{id}", artifact_delete, methods=["DELETE"]),
    ]
    routes += app.devices.routes() if hasattr(app.devices, "routes") else []
    if root is not None:
        routes.append(Mount("/app", StaticFiles(directory=root / "app"), name="app"))
        if (root / "vendor").is_dir():
            routes.append(Mount("/vendor", StaticFiles(directory=root / "vendor"), name="vendor"))
    routes.append(Route("/{path:path}", index))

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        if manage_lifecycle:
            await app.start()
        try:
            yield
        finally:
            if manage_lifecycle:
                await app.stop()

    middleware = [Middleware(SecurityHeaders)]
    if app.config.value.server.cors_origins:
        middleware.append(Middleware(CORSMiddleware, allow_origins=app.config.value.server.cors_origins,
                                     allow_methods=["*"], allow_headers=["*"]))
    server = Starlette(routes=routes, lifespan=lifespan, middleware=middleware)
    return server
