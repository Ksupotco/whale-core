"""同步上下文调用 async 设备面的桥（#77）。

管理 REST handler 线程与自主体执行都是同步上下文，而 DeviceGatewayV2 /
DeviceTransport 是 async 接口。transport 会缓存 loop 绑定状态（SSH 长连、
per-device asyncio.Lock），每请求 ``asyncio.run`` 会跨 loop 复用这些状态而
崩（"Future attached to a different loop"）。因此本模块维护**唯一的常驻
gateway 事件循环线程**，所有同步入口经 ``run_coroutine_threadsafe`` 提交到
该循环，保证 transport 状态始终在同一 loop 上。

纪律：绝不静默吞异常；gateway loop 内禁止再调 run_sync（死锁），异步上下文
请直接 await。
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Coroutine

_LOOP_LOCK = threading.Lock()
_LOOP: asyncio.AbstractEventLoop | None = None
_LOOP_THREAD: threading.Thread | None = None


def gateway_loop() -> asyncio.AbstractEventLoop:
    """返回常驻 gateway 事件循环（懒启动，线程名 device-gateway-loop）。"""
    global _LOOP, _LOOP_THREAD
    with _LOOP_LOCK:
        if _LOOP is not None and _LOOP.is_running():
            return _LOOP
        loop = asyncio.new_event_loop()
        ready = threading.Event()

        def _serve() -> None:
            asyncio.set_event_loop(loop)
            loop.call_soon(ready.set)
            loop.run_forever()

        thread = threading.Thread(
            target=_serve, name="device-gateway-loop", daemon=True)
        thread.start()
        if not ready.wait(timeout=5):
            raise RuntimeError("gateway loop 启动超时")
        _LOOP, _LOOP_THREAD = loop, thread
        return loop


def run_sync(coro: Coroutine[Any, Any, Any]) -> Any:
    """在同步上下文里跑一个协程并返回结果（异常原样上抛）。

    协程总是在共享 gateway loop 上执行；在 gateway loop 线程内调用会直接
    拒绝（防自死锁）。
    """
    loop = gateway_loop()
    running: asyncio.AbstractEventLoop | None = None
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        pass
    if running is loop:
        raise RuntimeError(
            "run_sync 不能在 gateway loop 内调用（请直接 await 该协程）")
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result()
