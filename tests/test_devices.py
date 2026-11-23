import base64
import hashlib
import json
import os
import threading
import time
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from whale.devices.contract import CAPABILITIES, ERROR_CODES, ContractValidator, contracts_dir
from whale.kernel.ids import now_ms, ulid
from whale.server.http import create_server

from helpers import call, make_app, scripted

WS = "/api/devices/ws"
CAPS = ["device.observe", "device.tap", "device.wait", "device.swipe", "device.input_text", "device.key", "device.app.launch",
        "device.shell", "device.status", "device.output.read", "phone.notification.post", "screen.history.search"]
FEATURES = {"root_shell": True, "accessibility": True, "media_projection": False, "telecom": False,
            "ocr_mlkit": True}


class Phone:
    """A fake whale-mobile device speaking the whale device protocol (envelope v1)."""

    def __init__(self, name="Pixel"):
        self.key = Ed25519PrivateKey.generate()
        self.pubkey = self.key.public_key().public_bytes_raw().hex()
        self.device_id = hashlib.sha256(bytes.fromhex(self.pubkey)).digest()[:16].hex()
        self.name = name
        self.seq = 0
        self.ws = None

    def frame(self, msg_type, payload, **over):
        self.seq += 1
        msg = {"v": "1.0", "id": ulid(), "type": msg_type, "ts": now_ms(), "seq": self.seq, "dir": "uplink",
               "payload": payload}
        msg.update(over)
        return msg

    def send(self, msg_type, payload, **over):
        msg = self.frame(msg_type, payload, **over)
        self.ws.send_json(msg)
        return msg

    def hello(self, ws, caps=None, signer=None, features=None, challenge_override=None):
        self.ws = ws
        ch = ws.receive_json()
        assert ch["type"] == "auth.challenge", ch
        challenge = challenge_override or ch["payload"]["challenge"]
        sig = (signer or self.key).sign(challenge.encode("utf-8")).hex()
        self.send("device.hello", {
            "protocol_version": "1.0", "device_id": self.device_id, "capabilities": caps or CAPS,
            "features": features or FEATURES, "device": {"model": "Leaf5", "android_release": "13", "api_level": 33},
            "app_version": "1.0.0", "app_flavor": "root", "signature": sig, "challenge": challenge})
        return ws.receive_json()

    def heartbeat(self, status="online", battery=80, network="wifi"):
        self.send("device.heartbeat", {"device_id": self.device_id, "observed_at": now_ms(), "status": status,
                                       "battery": battery, "network": network, "modules": {"screen_memo": "on"}})

    def event(self, event_id=None, summary="看了一个视频"):
        eid = event_id or "evt_" + ulid()
        self.send("experience.event", {"event_id": eid, "kind": "screen.segment_ready", "occurred_at": now_ms(),
                                       "device_id": self.device_id, "source": "screen_memo", "summary": summary,
                                       "privacy_class": "owner_private", "schema_version": "1.0",
                                       "extras": {"package": "com.example.video"}})
        return eid

    def receipt(self, action_id, status="succeeded", summary="已点击", **extra):
        payload = {"action_id": action_id, "status": status, "started_at": now_ms(), "completed_at": now_ms()}
        if summary:
            payload["result"] = {"summary": summary, "verification": {"status": "skipped", "summary": "坐标点击"}}
        payload.update(extra)
        self.send("action.receipt", payload)

    def expect(self, msg_type, limit=30):
        for _ in range(limit):
            msg = self.ws.receive_json()
            if msg["type"] == msg_type:
                return msg
        raise AssertionError("no " + msg_type)


class Background:
    """Run a blocking HTTP call while the test thread plays the phone."""

    def __init__(self, fn):
        self.result = None
        self.error = None
        self.thread = threading.Thread(target=self._run, args=(fn,))
        self.thread.start()

    def _run(self, fn):
        try:
            self.result = fn()
        except Exception as exc:  # surfaced by join()
            self.error = exc

    def join(self, timeout=10):
        self.thread.join(timeout)
        if self.error is not None:
            raise self.error
        return self.result


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.server = create_server(self.app)
        self.client = TestClient(self.server)
        self.client.__enter__()
        self.h = {"Authorization": "Bearer " + self.app.config.api_token}
        self.contracts = ContractValidator()

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def rpc(self, method, params=None, status=200):
        r = self.client.post("/api/rpc/" + method, json=params or {}, headers=self.h)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def pair(self, phone, scopes=None):
        code = self.rpc("devices.pair_start", {"name": phone.name, "scopes": scopes})["result"]["code"]
        r = self.client.post("/api/devices/pair", json={"code": code, "pubkey_hex": phone.pubkey, "label": "x"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["result"]["device_id"], phone.device_id)
        return code

    def caps(self):
        return {c["name"]: c for c in self.rpc("capabilities.list")["result"]["capabilities"]}

    def wait_for(self, predicate, timeout=3.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return True
            time.sleep(0.05)
        return False

    def check_down(self, msg):
        self.assertIsNone(self.contracts.validate(msg["type"], msg), msg)

    # -- contract -------------------------------------------------------------------------------
    def test_contract_examples_and_negatives(self):
        root = contracts_dir()
        self.assertIsNotNone(root)
        self.assertTrue(self.contracts.full)
        names = sorted(n for n in os.listdir(str(root / "examples")) if n.endswith(".json"))
        self.assertGreaterEqual(len(names), 25)
        for name in names:
            ex = json.loads((root / "examples" / name).read_text(encoding="utf-8"))
            self.assertIsNone(self.contracts.validate(ex["type"], ex), name)
            self.assertEqual(name[:-5], ex["type"])
        hello = json.loads((root / "examples" / "device.hello.json").read_text(encoding="utf-8"))
        enum = self.contracts.messages["device.hello"]["properties"]["payload"]["properties"]["capabilities"]
        self.assertEqual(tuple(enum["items"]["enum"]), CAPABILITIES)
        codes = self.contracts.messages["error"]["properties"]["payload"]["properties"]["code"]["enum"]
        self.assertEqual(tuple(codes), ERROR_CODES)

        def broken(**patch):
            msg = json.loads(json.dumps(hello))
            for k, v in patch.items():
                if k.startswith("p_"):
                    if v is None:
                        msg["payload"].pop(k[2:])
                    else:
                        msg["payload"][k[2:]] = v
                else:
                    msg[k] = v
            return self.contracts.validate("device.hello", msg)

        self.assertIsNotNone(broken(v="3.0"))
        self.assertIsNotNone(broken(v="2.0"))   # clean break：旧代次信封同样拒绝
        self.assertIsNotNone(broken(dir="downlink"))
        self.assertIsNotNone(broken(ts=4102444800001))
        self.assertIsNotNone(broken(id="not-a-ulid"))
        self.assertIsNotNone(broken(p_device_id="BA4B"))
        self.assertIsNotNone(broken(p_signature=None))
        self.assertIsNotNone(broken(p_capabilities=["device.teleport"]))
        self.assertIsNotNone(broken(p_extra=1))
        ack = json.loads((root / "examples" / "event.ack.json").read_text(encoding="utf-8"))
        ack["payload"]["event_ids"] = ["01J9XK3EM8TQ2ZP0VN6W4RA8EE"]   # event_id without prefix
        self.assertIsNotNone(self.contracts.validate("event.ack", ack))

    def test_builtin_fallback_validator_without_schema_files(self):
        root = contracts_dir()
        v = ContractValidator(root / "does-not-exist")
        self.assertFalse(v.full)
        hello = json.loads((root / "examples" / "device.hello.json").read_text(encoding="utf-8"))
        self.assertIsNone(v.validate("device.hello", hello))
        hello["payload"].pop("signature")
        self.assertIn("signature", v.validate("device.hello", hello))
        hello["v"] = "3.1"
        self.assertIsNotNone(v.validate("device.hello", hello))
        hello["v"] = "2.0"                       # 旧代次信封在 fallback 校验器下同样拒绝
        self.assertIsNotNone(v.validate("device.hello", hello))

    def test_restart_marks_inflight_actions_unknown(self):
        led = self.app.devices.ledger
        self.app.devices.registry.db.execute(
            "INSERT INTO devices(id,pubkey,name,scopes,created_at) VALUES (?,?,?,?,?)",
            ("f" * 32, "0" * 64, "old", "[]", now_ms()))
        self.assertTrue(led.reserve_action({"action_id": "act_" + ulid(), "device_id": "f" * 32,
                                            "idempotency_key": "agent:restart:1", "capability": "device.tap",
                                            "arguments": {}, "expires_at": now_ms() + 1000}))
        self.assertFalse(led.reserve_action({"action_id": "act_" + ulid(), "device_id": "f" * 32,
                                             "idempotency_key": "agent:restart:1", "capability": "device.tap",
                                             "arguments": {}, "expires_at": now_ms() + 1000}))
        self.assertEqual(led.sweep_inflight(), 1)
        self.assertEqual(led.action_by_key("f" * 32, "agent:restart:1")["status"], "unknown")

    # -- pairing + handshake ----------------------------------------------------------------------
    def test_pair_connect_hello_ack_and_capabilities(self):
        phone = Phone()
        code = self.pair(phone)
        again = self.client.post("/api/devices/pair", json={"code": code, "pubkey_hex": phone.pubkey})
        self.assertEqual(again.status_code, 404)  # single use
        paired = [e for e in self.app.bus.recent("device.paired")]
        self.assertEqual(paired[-1].data["device"]["id"], phone.device_id)
        with self.client.websocket_connect(WS) as ws:
            ack = phone.hello(ws)
            self.assertEqual(ack["type"], "device.hello_ack")
            self.check_down(ack)
            self.assertEqual(ack["payload"]["heartbeat_interval"], 30)
            self.assertEqual(ack["seq"], 2)
            self.assertEqual(set(ack["payload"]["accepted_capabilities"]), set(CAPS))
            devs = self.rpc("devices.list")["result"]["devices"]
            self.assertEqual(len(devs), 1)
            self.assertTrue(devs[0]["online"])
            self.assertEqual(devs[0]["status"], "online")
            self.assertEqual(devs[0]["model"], "Leaf5")
            self.assertEqual(devs[0]["flavor"], "root")
            caps = self.caps()
            self.assertIn("device.pixel.device.tap", caps)
            self.assertEqual(caps["device.pixel.device.tap"]["risk"], "external")
            self.assertEqual(caps["device.pixel.device.shell"]["risk"], "dangerous")
            self.assertEqual(caps["device.pixel.device.observe"]["risk"], "read")
            self.assertEqual(caps["device.pixel.device.tap"]["provider"], "device:" + phone.device_id)
            st = self.rpc("system.status")["result"]
            self.assertEqual(st["devices"], {"total": 1, "online": 1})
        self.assertTrue(self.wait_for(lambda: "device.pixel.device.tap" not in self.caps()))
        self.assertFalse(self.rpc("devices.list")["result"]["devices"][0]["online"])
        self.assertTrue(self.app.bus.recent("device.disconnected"))

    def test_scopes_and_features_narrow_accepted_capabilities(self):
        phone = Phone()
        self.pair(phone, scopes=["device.tap", "device.observe", "device.shell", "device.status"])
        with self.client.websocket_connect(WS) as ws:
            feats = dict(FEATURES, root_shell=False)
            ack = phone.hello(ws, features=feats)
            self.assertEqual(sorted(ack["payload"]["accepted_capabilities"]),
                             ["device.observe", "device.status", "device.tap"])
            self.rpc("devices.set_scopes", {"id": phone.device_id, "scopes": ["device.status"]})
            caps = self.caps()
            self.assertNotIn("device.pixel.device.tap", caps)
            self.assertIn("device.pixel.device.status", caps)
            err = self.rpc("devices.action", {"id": phone.device_id, "capability": "device.tap",
                                              "arguments": {"x": 1, "y": 2}}, status=403)
            self.assertEqual(err["error"]["code"], "forbidden")

    def test_pair_endpoint_is_code_public_and_strict(self):
        """POST /api/devices/pair：兑换码即凭证（免 token），错码/坏公钥如实拒绝。"""
        phone = Phone("Leaf")
        r = self.client.post("/api/devices/pair", json={"code": "000000", "pubkey_hex": phone.pubkey})
        self.assertEqual(r.status_code, 404)
        code = self.rpc("devices.pair_start", {"name": "Leaf"})["result"]["code"]
        r = self.client.post("/api/devices/pair", json={"code": code, "pubkey_hex": "not-hex"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/devices/pair", json={"code": code, "pubkey_hex": phone.pubkey, "label": "Leaf"})
        self.assertEqual(r.status_code, 200, r.text)
        res = r.json()["result"]
        self.assertEqual(res["device_id"], phone.device_id)
        self.assertEqual(res["ws_path"], WS)
        with self.client.websocket_connect(WS) as ws:
            self.assertEqual(phone.hello(ws)["type"], "device.hello_ack")
        self.assertNotIn("pending_pairings", self.rpc("devices.list")["result"])  # device-first 流程已移除
        # 旧路径不复存在：POST 只剩控制台兜底（405/404），路由表里没有旧设备端点
        self.assertIn(self.client.post("/api/v2/devices/pair", json={}).status_code, (404, 405))
        self.assertIn(self.client.post("/api/v1/devices/pair", json={}).status_code, (404, 405))
        ws_routes = sorted(getattr(r, "path", "") for r in self.server.routes
                           if r.__class__.__name__ == "WebSocketRoute")
        self.assertEqual(ws_routes, [WS, "/api/link"])   # 设备面只剩无版本新路径（sorted 序）

    def test_bad_signature_and_unpaired_device_rejected(self):
        phone = Phone()
        self.pair(phone)
        with self.client.websocket_connect(WS) as ws:
            msg = phone.hello(ws, signer=Ed25519PrivateKey.generate())
            self.assertEqual(msg["type"], "error")
            self.check_down(msg)
            self.assertEqual(msg["payload"]["code"], "auth_failed")
            self.assertIn("signature", msg["payload"]["message"])
        with self.client.websocket_connect(WS) as ws:
            msg = phone.hello(ws, challenge_override="a" * 64)
            self.assertEqual(msg["payload"]["code"], "auth_failed")
        with self.client.websocket_connect(WS) as ws:
            msg = Phone("stranger").hello(ws)
            self.assertEqual(msg["payload"]["code"], "auth_failed")
            self.assertIn("not paired", msg["payload"]["message"])
        self.assertEqual(self.rpc("system.status")["result"]["devices"]["online"], 0)
        self.assertNotIn("device.pixel.device.tap", self.caps())

    # -- heartbeat ------------------------------------------------------------------------------
    def test_heartbeat_status_and_offline_watchdog(self):
        phone = Phone()
        self.pair(phone)
        with self.client.websocket_connect(WS) as ws:
            phone.hello(ws)
            phone.heartbeat(battery=57, network="cellular")
            self.assertTrue(self.wait_for(
                lambda: self.rpc("devices.get", {"id": phone.device_id})["result"]["battery"] == 57))
            dev = self.rpc("devices.get", {"id": "Pixel"})["result"]
            self.assertEqual(dev["network"], "cellular")
            phone.heartbeat(status="paused", battery=57, network="cellular")
            self.assertTrue(self.wait_for(
                lambda: self.rpc("devices.list")["result"]["devices"][0]["status"] == "paused"))
            self.app.devices.gateway.heartbeat_timeout = 0.2
            self.assertTrue(self.wait_for(
                lambda: self.rpc("devices.list")["result"]["devices"][0]["status"] == "offline", 4))
            self.assertFalse(self.caps()["device.pixel.device.tap"]["available"])
            err = self.rpc("devices.action", {"id": phone.device_id, "capability": "device.tap",
                                              "arguments": {"x": 1, "y": 2}}, status=503)
            self.assertEqual(err["error"]["code"], "unavailable")
            self.app.devices.gateway.heartbeat_timeout = 90
            phone.heartbeat()
            self.assertTrue(self.wait_for(lambda: self.rpc("devices.list")["result"]["devices"][0]["online"]))
            self.assertTrue(self.caps()["device.pixel.device.tap"]["available"])
            topics = [e.data["status"] for e in self.app.bus.recent("device.status")]
            self.assertIn("offline", topics)

    # -- experience events ------------------------------------------------------------------------
    def test_experience_events_dedupe_and_batched_ack(self):
        phone = Phone()
        self.pair(phone)
        with self.client.websocket_connect(WS) as ws:
            phone.hello(ws)
            a = phone.event()
            b = phone.event(summary="打开了设置")
            phone.event(event_id=a)          # duplicate of a
            acked = []
            while len(set(acked)) < 2:
                msg = phone.expect("event.ack")
                self.check_down(msg)
                acked += msg["payload"]["event_ids"]
            self.assertEqual(set(acked), {a, b})
            self.assertLessEqual(len(acked), 3)
            events = self.rpc("devices.events", {"id": phone.device_id})["result"]["events"]
            self.assertEqual(sorted(e["event_id"] for e in events), sorted([a, b]))
            self.assertEqual(len(self.app.bus.recent("device.event")), 2)
            # an event claiming another device_id is rejected, not stored
            phone.send("experience.event", {"event_id": "evt_" + ulid(), "kind": "x", "occurred_at": now_ms(),
                                            "device_id": "0" * 32, "source": "user", "summary": "spoof",
                                            "privacy_class": "public", "schema_version": "1.0"})
            err = phone.expect("error")
            self.assertEqual(err["payload"]["code"], "invalid_message")
            self.assertEqual(len(self.rpc("devices.events", {"id": phone.device_id})["result"]["events"]), 2)
            dev = self.rpc("devices.get", {"id": phone.device_id})["result"]
            self.assertEqual(dev["foreground"], "com.example.video")
            self.assertEqual(dev["event_count"], 2)

    # -- actions ----------------------------------------------------------------------------------
    def test_owner_action_receipt_and_idempotent_replay(self):
        phone = Phone()
        self.pair(phone)
        with self.client.websocket_connect(WS) as ws:
            phone.hello(ws)
            params = {"id": phone.device_id, "capability": "device.app.launch",
                      "arguments": {"package": "com.android.settings"}, "idempotency_key": "owner:test:0001"}
            job = Background(lambda: self.rpc("devices.action", params))
            req = phone.expect("action.request")
            self.check_down(req)
            p = req["payload"]
            self.assertEqual(p["capability"], "device.app.launch")
            self.assertEqual(p["idempotency_key"], "owner:test:0001")
            self.assertEqual(p["authorized_scope"], ["device.app.launch"])
            self.assertTrue(p["action_id"].startswith("act_"))
            phone.receipt(p["action_id"], "received", summary="")
            phone.receipt(p["action_id"], "succeeded", summary="已打开设置")
            out = job.join()["result"]
            self.assertEqual(out["status"], "succeeded")
            self.assertEqual(out["summary"], "已打开设置")
            self.assertEqual(out["verification"]["status"], "skipped")
            # same idempotency key → recorded state, never re-sent
            again = self.rpc("devices.action", params)["result"]
            self.assertTrue(again["duplicate"])
            self.assertEqual(again["action_id"], p["action_id"])
            self.assertEqual(again["status"], "succeeded")
            # a conflicting late terminal does not rewrite history (first terminal wins)
            phone.receipt(p["action_id"], "failed", summary="", error_code="internal")
            phone.heartbeat()
            time.sleep(0.2)
            actions = self.rpc("devices.actions", {"id": phone.device_id})["result"]["actions"]
            self.assertEqual(len(actions), 1)
            self.assertEqual(actions[0]["status"], "succeeded")
            phases = [e.data["phase"] for e in self.app.bus.recent("device.action")]
            self.assertEqual(phases, ["request", "receipt"])

    def test_agent_invokes_device_tap_capability(self):
        phone = Phone()
        self.pair(phone)
        self.app.config.update({"policy": {"rules": {"device.*": "allow"}}})
        with self.client.websocket_connect(WS) as ws:
            phone.hello(ws)
            scripted(self.app, call("device.pixel.device.tap", {"x": 540, "y": 1200}), "点好了")
            job = Background(lambda: self.rpc("chat.send", {"text": "帮我点一下屏幕中间"}))
            req = phone.expect("action.request")
            self.check_down(req)
            p = req["payload"]
            self.assertEqual(p["capability"], "device.tap")
            self.assertEqual(p["arguments"], {"x": 540, "y": 1200})
            self.assertTrue(p["run_id"].startswith("run_"))
            self.assertTrue(p["idempotency_key"].startswith("agent:run_"))
            phone.receipt(p["action_id"], "succeeded", summary="已点击屏幕中间")
            res = job.join()["result"]
            self.assertEqual(res["text"], "点好了")
            run = self.rpc("runs.get", {"id": res["run_id"]})["result"]
            tool = [s for s in run["steps"] if s["kind"] == "tool"][0]
            self.assertIn("已点击屏幕中间", json.dumps(tool, ensure_ascii=False))
            # owner quick action through the generic capability surface
            job = Background(lambda: self.client.post("/api/rpc/capabilities.invoke", headers=self.h, json={
                "name": "device.pixel.device.tap", "args": {"element_id": 3}}).json())
            out = job.join()
            self.assertEqual(out["result"]["status"], "error")      # element_id needs observation_id
            job = Background(lambda: self.client.post("/api/rpc/capabilities.invoke", headers=self.h, json={
                "name": "device.pixel.device.tap",
                "args": {"element_id": 3, "observation_id": "obs_" + ulid()}}).json())
            req = phone.expect("action.request")
            self.assertEqual(req["payload"]["arguments"], {"element_id": 3})
            self.assertTrue(req["payload"]["observation_id"].startswith("obs_"))
            phone.receipt(req["payload"]["action_id"], "failed", summary="元素已变化",
                          error_code="stale_observation")
            out = job.join()["result"]
            self.assertEqual(out["status"], "error")
            self.assertIn("stale_observation", out["error"])

    def test_timeout_is_unknown_never_retried_and_late_receipt_is_kept(self):
        phone = Phone()
        self.pair(phone)
        self.app.devices.receipt_grace_s = 0.1
        self.app.config.update({"policy": {"rules": {"device.*": "allow"}}})
        with self.client.websocket_connect(WS) as ws:
            phone.hello(ws)
            params = {"id": phone.device_id, "capability": "device.tap", "arguments": {"x": 1, "y": 2},
                      "timeout_s": 0.2, "idempotency_key": "owner:timeout:1"}
            job = Background(lambda: self.rpc("devices.action", params))
            req = phone.expect("action.request")
            out = job.join()["result"]
            self.assertEqual(out["status"], "unknown")
            self.assertEqual(out["outcome_source"], "core:wait-timeout")
            again = self.rpc("devices.action", params)["result"]     # no new request
            self.assertTrue(again["duplicate"])
            self.assertEqual(again["status"], "unknown")
            phone.receipt(req["payload"]["action_id"], "succeeded", summary="其实点到了")
            self.assertTrue(self.wait_for(lambda: self.rpc("devices.actions", {"id": phone.device_id})
                                          ["result"]["actions"][0]["status"] == "succeeded"))
            # capability path: ToolResult status unknown
            self.app.devices.receipt_grace_s = 0.1
            job = Background(lambda: self.client.post("/api/rpc/capabilities.invoke", headers=self.h, json={
                "name": "device.pixel.device.wait", "args": {"duration_ms": 1}}).json())
            phone.expect("action.request")
            res = job.join(15)["result"]
            self.assertEqual(res["status"], "unknown")
            self.assertEqual(len(self.rpc("devices.actions", {"id": phone.device_id})["result"]["actions"]), 2)

    def test_disconnect_while_waiting_is_unknown(self):
        phone = Phone()
        self.pair(phone)
        with self.client.websocket_connect(WS) as ws:
            phone.hello(ws)
            job = Background(lambda: self.rpc("devices.action", {"id": phone.device_id, "capability": "device.tap",
                                                                 "arguments": {"x": 1, "y": 2}, "timeout_s": 5}))
            phone.expect("action.request")
        out = job.join()["result"]
        self.assertEqual(out["status"], "unknown")
        self.assertEqual(out["outcome_source"], "core:connection-lost")

    def test_one_device_cannot_complete_another_devices_request(self):
        a, b = Phone("Alpha"), Phone("Beta")
        self.pair(a)
        self.pair(b)
        with self.client.websocket_connect(WS) as wa, self.client.websocket_connect(WS) as wb:
            a.hello(wa)
            b.hello(wb)
            self.assertEqual(self.rpc("system.status")["result"]["devices"]["online"], 2)
            job = Background(lambda: self.rpc("devices.action", {"id": a.device_id, "capability": "device.tap",
                                                                 "arguments": {"x": 9, "y": 9}}))
            req = a.expect("action.request")
            b.receipt(req["payload"]["action_id"], "failed", summary="", error_code="internal")   # forged
            b.heartbeat()
            time.sleep(0.2)
            self.assertIsNone(job.result)
            rec = self.rpc("devices.actions", {"id": a.device_id})["result"]["actions"][0]
            self.assertIn(rec["status"], ("pending", "received"))
            a.receipt(req["payload"]["action_id"], "succeeded", summary="ok")
            self.assertEqual(job.join()["result"]["status"], "succeeded")
            # same for sensor queries
            job = Background(lambda: self.rpc("devices.query", {"id": a.device_id, "operation": "device.status"}))
            q = a.expect("sensor.query")
            self.check_down(q)
            b.send("sensor.result", {"request_id": q["payload"]["request_id"], "status": "ok",
                                     "data": [{"battery": 1}], "truncated": False, "source": "forged"})
            b.heartbeat()
            time.sleep(0.2)
            self.assertIsNone(job.result)
            a.send("sensor.result", {"request_id": q["payload"]["request_id"], "status": "ok",
                                     "data": [{"battery": 64, "foreground_package": "com.x"}], "truncated": False,
                                     "source": "status"})
            res = job.join()["result"]
            self.assertEqual(res["source"], "status")
            self.assertEqual(res["data"][0]["battery"], 64)
            self.assertIn("device.alpha.device.tap", self.caps())
            self.assertIn("device.beta.device.tap", self.caps())

    def test_observe_fetches_output_pages(self):
        phone = Phone()
        self.pair(phone)
        with self.client.websocket_connect(WS) as ws:
            phone.hello(ws)
            obs = {"observation_id": "obs_" + ulid(), "foreground_package": "com.android.settings",
                   "elements": [{"element_id": i, "text": "item " + str(i), "class_name": "android.widget.TextView",
                                 "bounds": [0, i * 10, 100, i * 10 + 9], "clickable": True} for i in range(5)]}
            blob = json.dumps(obs).encode()
            job = Background(lambda: self.client.post("/api/rpc/capabilities.invoke", headers=self.h, json={
                "name": "device.pixel.device.observe", "args": {}}).json())
            req = phone.expect("action.request")
            self.assertEqual(req["payload"]["arguments"], {"ui_tree": True, "screenshot": False, "max_elements": 200})
            phone.receipt(req["payload"]["action_id"], "succeeded", summary="observed",
                          result={"summary": "observed", "output_ref": "agent:ui:" + req["payload"]["action_id"]})
            half = len(blob) // 2
            for start, end in ((0, half), (half, len(blob))):
                q = phone.expect("sensor.query")
                self.assertEqual(q["payload"]["operation"], "device.output.read")
                self.assertEqual(q["payload"]["filters"]["offset"], start)
                chunk = blob[start:end]
                phone.send("sensor.result", {"request_id": q["payload"]["request_id"], "status": "ok", "data": [{
                    "bytes_b64": base64.b64encode(chunk).decode(), "offset": start, "next_offset": end,
                    "total_bytes": len(blob), "truncated": end < len(blob)}], "truncated": end < len(blob),
                    "source": "output"})
            res = job.join()["result"]
            self.assertEqual(res["status"], "ok")
            ob = res["data"]["observation"]
            self.assertEqual(ob["foreground_package"], "com.android.settings")
            self.assertEqual(len(ob["elements"]), 5)
            self.assertEqual(ob["elements"][2]["flags"], ["clickable"])
            self.assertEqual(self.rpc("devices.get", {"id": phone.device_id})["result"]["foreground"],
                             "com.android.settings")

    # -- revoke -----------------------------------------------------------------------------------
    def test_revoke_closes_socket_and_removes_capabilities(self):
        phone = Phone()
        self.pair(phone)
        with self.client.websocket_connect(WS) as ws:
            phone.hello(ws)
            self.assertIn("device.pixel.device.tap", self.caps())
            res = self.rpc("devices.revoke", {"id": phone.device_id})["result"]
            self.assertTrue(res["kicked"])
            self.assertTrue(res["revoked"])
            err = phone.expect("error")
            self.assertEqual(err["payload"]["code"], "device_revoked")
            with self.assertRaises(WebSocketDisconnect):
                for _ in range(5):
                    ws.receive_json()
        self.assertNotIn("device.pixel.device.tap", self.caps())
        self.assertTrue(self.app.bus.recent("device.revoked"))
        with self.client.websocket_connect(WS) as ws:
            msg = phone.hello(ws)
            self.assertEqual(msg["payload"]["code"], "device_revoked")
        # re-pairing needs a fresh owner code
        self.pair(phone)
        with self.client.websocket_connect(WS) as ws:
            self.assertEqual(phone.hello(ws)["type"], "device.hello_ack")

    def test_rename_republishes_capabilities(self):
        phone = Phone()
        self.pair(phone)
        with self.client.websocket_connect(WS) as ws:
            phone.hello(ws)
            self.rpc("devices.rename", {"id": phone.device_id, "name": "Work Phone"})
            caps = self.caps()
            self.assertIn("device.work_phone.device.tap", caps)
            self.assertNotIn("device.pixel.device.tap", caps)
            self.rpc("devices.rename", {"id": phone.device_id, "name": "小米"})
            self.assertIn("device." + phone.device_id[:6] + ".device.tap", self.caps())

    # -- protocol hygiene ---------------------------------------------------------------------------
    def test_invalid_frames_unknown_types_and_major_version(self):
        phone = Phone()
        self.pair(phone)
        with self.client.websocket_connect(WS) as ws:
            phone.hello(ws)
            ws.send_text("{not json")
            err = phone.expect("error")
            self.check_down(err)
            self.assertEqual(err["payload"]["code"], "invalid_message")
            bad = phone.frame("device.heartbeat", {"device_id": phone.device_id, "observed_at": now_ms(),
                                                   "status": "sleepy"})
            ws.send_json(bad)
            err = phone.expect("error")
            self.assertEqual(err["payload"]["code"], "invalid_message")
            self.assertEqual(err["payload"]["ref_id"], bad["id"])
            phone.send("glasses.hologram", {"x": 1})      # unknown type: ignored silently
            phone.event()
            self.assertEqual(phone.expect("event.ack")["type"], "event.ack")   # connection still healthy
            phone.send("device.heartbeat", {"device_id": phone.device_id, "observed_at": now_ms(),
                                            "status": "online"}, v="3.0")
            err = phone.expect("error")
            self.assertEqual(err["payload"]["code"], "protocol_version")
            with self.assertRaises(WebSocketDisconnect):
                for _ in range(5):
                    ws.receive_json()
        # clean break：旧代次信封（v=2.0）同样 protocol_version 拒绝并关闭
        with self.client.websocket_connect(WS) as ws:
            phone.hello(ws)
            phone.send("device.heartbeat", {"device_id": phone.device_id, "observed_at": now_ms(),
                                            "status": "online"}, v="2.0")
            err = phone.expect("error")
            self.assertEqual(err["payload"]["code"], "protocol_version")
            with self.assertRaises(WebSocketDisconnect):
                for _ in range(5):
                    ws.receive_json()

    # -- notifications ------------------------------------------------------------------------------
    def test_owner_notifications_reach_the_phone(self):
        phone = Phone()
        self.pair(phone)
        self.app.config.update({"policy": {"rules": {"notify.owner": "allow"}}})
        with self.client.websocket_connect(WS) as ws:
            phone.hello(ws)
            job = Background(lambda: self.client.post("/api/rpc/capabilities.invoke", headers=self.h, json={
                "name": "notify.owner", "args": {"text": "晚饭做好了", "title": "提醒"}}).json())
            n = phone.expect("notification.event")
            self.check_down(n)
            self.assertEqual(n["payload"]["title"], "提醒")
            self.assertEqual(n["payload"]["body"], "晚饭做好了")
            self.assertEqual(n["payload"]["device_id"], phone.device_id)
            self.assertEqual(n["payload"]["category"], "chat.message")
            out = job.join()["result"]
            self.assertIn("device:Pixel", out["data"]["delivered_to"])
            phone.send("notification.receipt", {"event_id": n["payload"]["event_id"],
                                                "notification_id": n["payload"]["notification_id"],
                                                "device_id": phone.device_id, "status": "displayed",
                                                "received_at": now_ms()})
            # per-device notification capability waits for the receipt
            job = Background(lambda: self.client.post("/api/rpc/capabilities.invoke", headers=self.h, json={
                "name": "device.pixel.phone.notification.post", "args": {"title": "嗨", "body": "测试"}}).json())
            n2 = phone.expect("notification.event")
            phone.send("notification.receipt", {"event_id": n2["payload"]["event_id"],
                                                "notification_id": n2["payload"]["notification_id"],
                                                "device_id": phone.device_id, "status": "displayed",
                                                "received_at": now_ms()})
            out = job.join()
            self.assertEqual(out["result"]["status"], "ok", out)
            self.assertEqual(out["result"]["data"]["status"], "displayed")
            phone.send("notification.action", {"event_id": n2["payload"]["event_id"],
                                               "notification_id": n2["payload"]["notification_id"],
                                               "device_id": phone.device_id, "action_id": "open",
                                               "submitted_at": now_ms()})
            self.assertTrue(self.wait_for(lambda: bool(self.app.bus.recent("device.notification_action"))))
            self.assertTrue(self.app.bus.recent("device.notification"))
        # offline: queued, replayed on notification.sync after reconnect
        job = Background(lambda: self.client.post("/api/rpc/capabilities.invoke", headers=self.h, json={
            "name": "notify.owner", "args": {"text": "你不在线时的消息"}}).json())
        out = job.join()["result"]
        self.assertNotIn("device:Pixel", out["data"]["delivered_to"])
        with self.client.websocket_connect(WS) as ws:
            phone.hello(ws)
            phone.send("notification.sync", {"device_id": phone.device_id, "last_event_id": n2["payload"]["event_id"],
                                             "synced_at": now_ms()})
            n3 = phone.expect("notification.event")
            self.assertEqual(n3["payload"]["body"], "你不在线时的消息")


if __name__ == "__main__":
    unittest.main()
