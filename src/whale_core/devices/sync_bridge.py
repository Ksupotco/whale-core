"""同步上下文调用 async 设备面的桥（#77）。

管理 REST handler 线程与自主体执行都是同步上下文，而 DeviceGatewayV2 /
DeviceTransport 是 async 接口。``run_sync`` 在无运行中事件循环的线程里直接
``asyncio.run``；万一被嵌进已有循环的线程（防御：不炸、不嵌套 loop），
放新线程跑并 join。绝不静默吞异常。
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Coroutine


def run_sync(coro: Coroutine[Any, Any, Any]) -> Any:
    """在同步上下文里跑一个协程并返回结果（异常原样上抛）。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box: dict[str, Any] = {}

    def _target() -> None:
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 — 原样转交调用线程
            box["error"] = exc

    worker = threading.Thread(target=_target, daemon=True)
    worker.start()
    worker.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")
