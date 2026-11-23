import json
import unittest

from starlette.testclient import TestClient

from whale.server.http import create_server

from helpers import call, make_app, scripted


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.server = create_server(self.app)
        self.client = TestClient(self.server)
        self.client.__enter__()
        self.h = {"Authorization": f"Bearer {self.app.config.api_token}"}

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def rpc(self, method, params=None, status=200):
        r = self.client.post(f"/api/rpc/{method}", json=params or {}, headers=self.h)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def test_health_is_public_and_rpc_requires_token(self):
        self.assertTrue(self.client.get("/api/health").json()["ok"])
        r = self.client.post("/api/rpc/system.status", json={})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["error"]["code"], "unauthorized")
        r = self.client.post("/api/rpc/system.status", json={}, headers={"Authorization": "Bearer nope"})
        self.assertEqual(r.status_code, 401)

    def test_status_schema_and_errors(self):
        st = self.rpc("system.status")["result"]
        self.assertEqual(st["agent"]["phase"], "idle")
        self.assertTrue(st["models"]["offline"])
        schema = self.client.get("/api/schema", headers=self.h).json()
        self.assertIn("chat.send", schema["methods"])
        self.assertIn("properties", schema["methods"]["chat.send"]["params"])
        err = self.rpc("memory.get", {"id": "mem_x"}, status=404)
        self.assertEqual(err["error"]["code"], "not_found")
        err = self.rpc("chat.send", {"text": ""}, status=400)
        self.assertEqual(err["error"]["code"], "invalid_params")
        err = self.rpc("no.such", {}, status=404)
        self.assertFalse(err["ok"])

    def test_chat_roundtrip_and_observability(self):
        scripted(self.app, call("memory.remember", {"content": "用户喜欢猫"}), "记住啦")
        res = self.rpc("chat.send", {"text": "记住我喜欢猫"})["result"]
        self.assertEqual(res["text"], "记住啦")
        run = self.rpc("runs.get", {"id": res["run_id"]})["result"]
        self.assertEqual([s["kind"] for s in run["steps"]], ["recall", "think", "tool", "think", "reply"])
        msgs = self.rpc("conversations.messages", {"id": res["conversation_id"]})["result"]["messages"]
        self.assertEqual(len(msgs), 2)
        mems = self.rpc("memory.list")["result"]
        self.assertEqual(mems["total"], 1)
        graph = self.rpc("memory.graph")["result"]
        self.assertEqual(len(graph["nodes"]), 1)

    def test_config_provider_and_secret_never_leak(self):
        self.rpc("models.upsert_provider", {"id": "ds", "base_url": "https://api.deepseek.com/v1", "api_key": "sk-secret"})
        self.rpc("config.update", {"patch": {"models": {"chat": {"provider": "ds", "model": "deepseek-chat"}}}})
        cfg = self.rpc("config.get")["result"]
        self.assertEqual(cfg["models"]["chat"]["model"], "deepseek-chat")
        self.assertTrue(cfg["secrets"]["provider.ds"])
        self.assertNotIn("sk-secret", json.dumps(cfg))
        st = self.rpc("models.status")["result"]
        self.assertFalse(st["offline"])
        bad = self.rpc("config.update", {"patch": {"server": {"port": "x"}}}, status=400)
        self.assertEqual(bad["error"]["code"], "invalid_params")

    def test_capabilities_policy_and_personas(self):
        caps = self.rpc("capabilities.list")["result"]["capabilities"]
        by = {c["name"]: c for c in caps}
        self.assertEqual(by["shell.run"]["policy"], "ask")
        self.rpc("policy.set", {"pattern": "shell.run", "verdict": "deny"})
        caps = {c["name"]: c for c in self.rpc("capabilities.list")["result"]["capabilities"]}
        self.assertEqual(caps["shell.run"]["policy"], "deny")
        self.rpc("policy.set", {"pattern": "shell.run", "verdict": None})
        caps = {c["name"]: c for c in self.rpc("capabilities.list")["result"]["capabilities"]}
        self.assertEqual(caps["shell.run"]["policy"], "ask")
        card = self.rpc("personas.import", {"card": {"spec": "chara_card_v2", "data": {
            "name": "露娜", "description": "一只月亮上的猫", "first_mes": "喵～"}}})["result"]
        self.rpc("personas.activate", {"id": card["id"]})
        st = self.rpc("system.status")["result"]
        self.assertEqual(st["agent"]["persona"]["name"], "露娜")

    def test_tasks_and_schedule_api(self):
        t = self.rpc("tasks.create", {"goal": "每天早上提醒我看日程", "schedule": {"cron": "0 8 * * *"}})["result"]
        self.assertEqual(t["status"], "scheduled")
        jobs = self.rpc("schedule.list")["result"]["jobs"]
        self.assertTrue(any(j["handler"] == "task.run" for j in jobs))
        self.assertTrue(any(j["id"] == "sys_memory_maintain" for j in jobs))
        bad = self.rpc("tasks.create", {"goal": "x", "schedule": {"cron": "99 * * * *"}}, status=400)
        self.assertEqual(bad["error"]["code"], "invalid_params")

    def test_link_websocket_hello_rpc_events_and_client_capability(self):
        token = self.app.config.api_token
        with self.client.websocket_connect("/api/link") as ws:
            ws.send_json({"v": "1.0", "id": "h1", "type": "hello", "payload": {
                "token": token, "role": "mobile", "name": "Pixel", "subscribe": ["run.*", "notify"],
                "capabilities": [{"name": "phone.vibrate", "description": "震动手机", "risk": "external",
                                  "params": {"type": "object", "properties": {"ms": {"type": "integer"}}}}]}})
            welcome = ws.receive_json()
            self.assertEqual(welcome["type"], "welcome")
            self.assertEqual(welcome["corr"], "h1")
            caps = {c["name"] for c in self.rpc("capabilities.list")["result"]["capabilities"]}
            self.assertIn("client.pixel.phone.vibrate", caps)
            scripted(self.app, "嗨")
            ws.send_json({"v": "1.0", "id": "r1", "type": "rpc.request",
                          "payload": {"method": "chat.send", "params": {"text": "你好"}}})
            seen, response = [], None
            for _ in range(60):
                msg = ws.receive_json()
                if msg["type"] == "event":
                    seen.append(msg["payload"]["topic"])
                elif msg["type"] == "rpc.response" and msg["corr"] == "r1":
                    response = msg["payload"]
                    break
            self.assertTrue(response["ok"])
            self.assertEqual(response["result"]["text"], "嗨")
            self.assertIn("run.started", seen)
            self.assertIn("run.step", seen)
            # server → client capability invocation (owner-approved via policy rule)
            self.app.config.update({"policy": {"rules": {"client.*": "allow"}}})
            import threading

            result = {}

            def invoke():
                result["r"] = self.client.post("/api/rpc/capabilities.invoke", headers=self.h, json={
                    "name": "client.pixel.phone.vibrate", "args": {"ms": 200}}).json()

            th = threading.Thread(target=invoke)
            th.start()
            for _ in range(20):
                msg = ws.receive_json()
                if msg["type"] == "rpc.request":
                    self.assertEqual(msg["payload"]["method"], "capability.invoke")
                    self.assertEqual(msg["payload"]["params"]["name"], "phone.vibrate")
                    ws.send_json({"v": "1.0", "id": "x", "type": "rpc.response", "corr": msg["id"],
                                  "payload": {"ok": True, "result": {"vibrated": True}}})
                    break
            th.join(5)
            self.assertEqual(result["r"]["result"]["data"], {"vibrated": True})
        caps = {c["name"] for c in self.rpc("capabilities.list")["result"]["capabilities"]}
        self.assertNotIn("client.pixel.phone.vibrate", caps)

    def test_artifacts_require_auth_and_hooks_dispatch(self):
        art = self.app.artifacts.save(b"\x89PNGdata", content_type="image/png", name="a.png")
        self.assertEqual(self.client.get(art["url"]).status_code, 401)
        r = self.client.get(art["url"], headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, b"\x89PNGdata")
        self.assertEqual(r.headers["content-type"], "image/png")
        self.assertIn("default-src 'self'", r.headers["content-security-policy"])
        from starlette.responses import JSONResponse

        async def handler(request):
            body = await request.json()
            return JSONResponse({"echo": body["x"]})

        self.app.hooks[("demo", "in")] = handler
        self.assertEqual(self.client.post("/api/hooks/demo/in", json={"x": 5}).json(), {"echo": 5})
        self.assertEqual(self.client.post("/api/hooks/demo/none", json={}).status_code, 404)

    def test_console_is_served_with_module_mime_types(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn('type="module"', r.text)
        self.assertNotIn("<script>", r.text)
        js = self.client.get("/app/main.js")
        self.assertEqual(js.status_code, 200)
        self.assertTrue(js.headers["content-type"].startswith("text/javascript"))
        self.assertEqual(js.headers["x-content-type-options"], "nosniff")
        css = self.client.get("/app/styles.css")
        self.assertTrue(css.headers["content-type"].startswith("text/css"))
        vendor = self.client.get("/vendor/preact-htm.js")
        self.assertTrue(vendor.headers["content-type"].startswith("text/javascript"))
        self.assertEqual(self.client.get("/memory").status_code, 200)  # SPA route → index.html

    def test_async_send_returns_run_id_and_extras(self):
        scripted(self.app, "好")
        res = self.rpc("chat.send", {"text": "你好", "wait": False})["result"]
        self.assertTrue(res["run_id"].startswith("run_"))
        import time

        for _ in range(100):
            run = self.client.post("/api/rpc/runs.get", json={"id": res["run_id"]}, headers=self.h).json()
            if run.get("ok") and run["result"]["run"]["status"] == "completed":
                break
            time.sleep(0.02)
        self.assertEqual(run["result"]["run"]["status"], "completed")
        convs = self.rpc("conversations.list")["result"]["conversations"]
        self.assertEqual(convs[0]["preview"], {"role": "assistant", "text": "好"})
        self.assertIn("queue_processed", self.rpc("system.status")["result"]["memory"])
        emb = self.rpc("models.test", {"role": "embedding"})["result"]
        self.assertTrue(emb["ok"])
        self.assertTrue(emb["offline"])

    def test_memory_list_scope_filter(self):
        import asyncio

        loop = asyncio.new_event_loop()
        for content, scope in (("主人喜欢猫", "owner"), ("群里说周六爬山", "group:qq:1"), ("小李在读研", "qq:9")):
            loop.run_until_complete(self.app.memory.remember(content, scope=scope))
        loop.close()
        get = lambda sc: [i["content"] for i in self.rpc("memory.list", {"scope": sc})["result"]["items"]]
        self.assertEqual(get("group"), ["群里说周六爬山"])
        self.assertEqual(get("person"), ["小李在读研"])
        self.assertEqual(get("owner"), ["主人喜欢猫"])
        self.assertEqual(len(self.rpc("memory.list", {"order": "access_count DESC"})["result"]["items"]), 3)

    def test_link_rejects_bad_token(self):
        with self.client.websocket_connect("/api/link") as ws:
            ws.send_json({"type": "hello", "id": "1", "payload": {"token": "bad"}})
            msg = ws.receive_json()
            self.assertEqual(msg["type"], "error")
            self.assertEqual(msg["payload"]["code"], "unauthorized")

    def test_chat_stream_sse(self):
        scripted(self.app, "流式回答来了")
        with self.client.stream("POST", "/api/chat/stream", headers=self.h, json={"text": "讲讲"}) as r:
            body = "".join(r.iter_text())
        events = [l[7:] for l in body.splitlines() if l.startswith("event: ")]
        self.assertIn("delta", events)
        self.assertEqual(events[-1], "done")
        done = json.loads([l for l in body.splitlines() if l.startswith("data: ")][-1][6:])
        self.assertEqual(done["text"], "流式回答来了")


if __name__ == "__main__":
    unittest.main()
