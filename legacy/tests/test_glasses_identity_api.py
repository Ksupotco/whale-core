"""GET /v1/identity 端点测试（2026-11-19，Assignment 单屏绑定）。

口径：无 token / 未知 token → 401；已知但非 phone 角色凭据（管理面 token）
→ 403；phone token → 200 {role:"phone", device, label}，device 来自设备
注册表（WHALE_GLASSES_DEVICE_ID 可锁定，否则第一个 active 设备）。
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from whale_core.api.glasses import make_handler
from whale_core.memory.sqlite_store import SqliteMemoryStore


@contextmanager
def server(tmp_path):
    store = SqliteMemoryStore(str(tmp_path / "id.sqlite3"))
    store.put("device:" + "a" * 32, {
        "device_id": "a" * 32, "status": "active",
        "label": "Magic8 Pro（主人主用手机）",
        "scopes": ["phone.notification.post"]})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler({"store": store}))
    srv.glasses_token = "phone-tok"
    srv.glasses_api_token = "admin-tok"
    srv.glasses_env = {}
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()

    def get(token=None):
        headers = {"Authorization": "Bearer " + token} if token else {}
        req = Request(f"http://127.0.0.1:{srv.server_port}/v1/identity",
                      headers=headers, method="GET")
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as response:
            return response.code, json.load(response)
    try:
        yield get
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(5)
        store.close()


def test_no_token_401(tmp_path):
    with server(tmp_path) as get:
        status, body = get(None)
        assert status == 401 and body["error"] == "unauthorized"


def test_unknown_token_401(tmp_path):
    with server(tmp_path) as get:
        status, _ = get("wrong")
        assert status == 401


def test_known_non_phone_role_403(tmp_path):
    """管理面 token（已知凭据但非 phone 角色）→ 403。"""
    with server(tmp_path) as get:
        status, body = get("admin-tok")
        assert status == 403 and body["error"] == "forbidden"


def test_phone_token_200_shape(tmp_path):
    with server(tmp_path) as get:
        status, body = get("phone-tok")
        assert status == 200
        assert body["role"] == "phone"
        assert body["device"] == "a" * 32
        assert "Magic8" in body["label"]


def test_identity_in_health_and_openapi(tmp_path):
    """capabilities 与 openapi 都露出 /v1/identity（health/openapi 保持免鉴权既有口径）。"""
    store = SqliteMemoryStore(str(tmp_path / "id2.sqlite3"))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler({"store": store}))
    srv.glasses_token = "phone-tok"
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        for path, probe in (("/v1/health", "capabilities"),
                            ("/openapi.json", "paths")):
            with urlopen(f"http://127.0.0.1:{srv.server_port}{path}",
                     timeout=5) as resp:
                data = json.load(resp)
            container = data[probe] if probe == "capabilities" else list(data[probe])
            assert "/v1/identity" in container
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(5)
        store.close()
