"""供应商管理端点测试（2026-11-22 控制台重构 · 裁决③④）。

红线验收：
- 视图只回显 api_key_configured 布尔——任何响应不含明文 key；
- /providers/{id}/models 由服务端代取（测试用 mock urlopen 拦截，不发真请求）；
- 删除被 registry 引用的供应商 → 409；
- settings.model / models{} 现有契约不受影响（本文件不涉及）。
"""
from __future__ import annotations

import json
from http.client import HTTPConnection
from unittest import mock

import pytest

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


def _req(server, method, path, *, body=None, token=TOKEN):
    conn = HTTPConnection("127.0.0.1", server.port, timeout=15)
    headers = {"Authorization": f"Bearer {token}"}
    payload = json.dumps(body) if body is not None else None
    if payload:
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        parsed = {}
    return resp.status, parsed


def _create(api, name="测试供应商", base_url="http://127.0.0.1:9", key="sk-test-secret"):
    return _req(api, "POST", "/api/v1/providers",
                body={"name": name, "base_url": base_url, "api_key": key})


class TestCrud:
    def test_create_list_update_delete(self, api):
        status, body = _create(api)
        assert status == 200, body
        view = body["result"]
        assert view["name"] == "测试供应商"
        assert view["api_key_configured"] is True
        # 红线：明文 key 绝不出现在响应里
        assert "sk-test-secret" not in json.dumps(body)
        pid = view["id"]

        status, body = _req(api, "GET", "/api/v1/providers")
        assert status == 200
        ids = [p["id"] for p in body["result"]["providers"]]
        assert pid in ids
        assert "sk-test-secret" not in json.dumps(body)

        status, body = _req(api, "POST", f"/api/v1/providers/{pid}",
                            body={"name": "改名供应商"})
        assert status == 200 and body["result"]["name"] == "改名供应商"
        assert body["result"]["api_key_configured"] is True  # 未动密钥

        # 清除密钥
        status, body = _req(api, "POST", f"/api/v1/providers/{pid}",
                            body={"api_key": None})
        assert status == 200 and body["result"]["api_key_configured"] is False

        status, body = _req(api, "POST", f"/api/v1/providers/{pid}/delete")
        assert status == 200 and body["result"]["deleted"] is True
        status, body = _req(api, "GET", "/api/v1/providers")
        assert pid not in [p["id"] for p in body["result"]["providers"]]

    def test_create_validation(self, api):
        status, body = _req(api, "POST", "/api/v1/providers", body={"name": "", "base_url": "x"})
        assert status == 400
        status, body = _req(api, "POST", "/api/v1/providers",
                            body={"name": "x", "base_url": "ftp://bad"})
        assert status == 400

    def test_delete_unknown_404(self, api):
        status, _ = _req(api, "POST", "/api/v1/providers/nope/delete")
        assert status == 404

    def test_delete_referenced_409(self, api):
        status, body = _create(api)
        pid = body["result"]["id"]
        # 让 registry 引用该供应商名
        api.ctx.store.put("models.registry", [
            {"name": "m1", "provider": "测试供应商", "model": "x", "effort": ""},
        ])
        status, body = _req(api, "POST", f"/api/v1/providers/{pid}/delete")
        assert status == 409 and body["error"]["code"] == "conflict"


class TestModelsAndTest:
    def test_models_server_side_fetch(self, api):
        status, body = _create(api)
        pid = body["result"]["id"]
        payload = json.dumps({"data": [{"id": "m-b"}, {"id": "m-a"}]}).encode()

        class _Resp:
            def read(self):
                return payload
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        with mock.patch("urllib.request.urlopen", return_value=_Resp()) as m:
            status, body = _req(api, "GET", f"/api/v1/providers/{pid}/models")
            assert status == 200
            assert body["result"]["models"] == ["m-a", "m-b"]
            assert body["result"]["cached"] is False
            # 验证请求确实带了 key（服务端代取）但不回显
            req_obj = m.call_args[0][0]
            assert req_obj.get_header("Authorization") == "Bearer sk-test-secret"
            assert "sk-test-secret" not in json.dumps(body)
            # 第二次走缓存（不再请求）
            status, body = _req(api, "GET", f"/api/v1/providers/{pid}/models")
            assert status == 200 and body["result"]["cached"] is True
            assert m.call_count == 1

    def test_models_upstream_failure_502(self, api):
        status, body = _create(api)
        pid = body["result"]["id"]
        with mock.patch("urllib.request.urlopen", side_effect=TimeoutError()):
            status, body = _req(api, "GET", f"/api/v1/providers/{pid}/models")
            assert status == 502 and body["error"]["code"] == "upstream"

    def test_provider_test_ok(self, api):
        status, body = _create(api)
        pid = body["result"]["id"]
        payload = json.dumps({"data": [{"id": "m1"}]}).encode()

        class _Resp:
            def read(self):
                return payload
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        with mock.patch("urllib.request.urlopen", return_value=_Resp()):
            status, body = _req(api, "POST", f"/api/v1/providers/{pid}/test")
            assert status == 200
            assert body["result"]["ok"] is True
            assert body["result"]["models"] == 1

    def test_provider_test_failure(self, api):
        status, body = _create(api)
        pid = body["result"]["id"]
        with mock.patch("urllib.request.urlopen", side_effect=TimeoutError()):
            status, body = _req(api, "POST", f"/api/v1/providers/{pid}/test")
            assert status == 200 and body["result"]["ok"] is False
            assert body["result"]["error_kind"] == "timeout"


class TestAuth:
    def test_unauthenticated_401(self, api):
        status, _ = _req(api, "GET", "/api/v1/providers", token="wrong")
        assert status == 401
