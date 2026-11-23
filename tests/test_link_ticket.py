"""C 鉴权（Link ticket）+ M1 mobile 白名单 + M4 artifacts 上行/删除。

假名纪律：设备/号码/标签全为假名。
"""
import time
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from starlette.testclient import TestClient

from whale.server.http import create_server

from helpers import make_app, scripted

TICKET_PATH = "/api/devices/link_ticket"


def _keypair():
    sk = Ed25519PrivateKey.generate()
    pub_hex = sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    return sk, pub_hex


class LinkTicketTests(unittest.TestCase):
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

    def _pair(self, label="fake-phone"):
        sk, pub_hex = _keypair()
        code = self.rpc("devices.pair_start", {"name": label})["result"]["code"]
        r = self.client.post("/api/devices/pair",
                             json={"code": code, "pubkey_hex": pub_hex, "label": label})
        self.assertEqual(r.status_code, 200, r.text)
        return sk, r.json()["result"]["device_id"]

    def _signed_headers(self, sk, device_id, *, ts=None, nonce="fake-nonce-000001", bad_sig=False):
        ts_raw = str(ts if ts is not None else int(time.time()))
        message = f"{device_id}\n{ts_raw}\n{nonce}\nPOST\n{TICKET_PATH}"
        sig = sk.sign(message.encode("utf-8")).hex()
        if bad_sig:
            sig = "0" * 128
        return {"X-Whale-Device-Id": device_id, "X-Whale-Timestamp": ts_raw,
                "X-Whale-Nonce": nonce, "X-Whale-Signature": sig}

    def _mint(self, sk, device_id, nonce="fake-nonce-000001"):
        r = self.client.post(TICKET_PATH, headers=self._signed_headers(sk, device_id, nonce=nonce))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["result"]

    # -- 签发与验签 ----------------------------------------------------------------
    def test_mint_ticket_happy_path(self):
        sk, device_id = self._pair()
        out = self._mint(sk, device_id)
        self.assertEqual(out["device_id"], device_id)
        self.assertEqual(out["expires_in"], 900)
        self.assertTrue(len(out["ticket"]) > 30)

    def test_mint_rejects_bad_signature_stale_ts_replay_unknown(self):
        sk, device_id = self._pair()
        other_sk, _ = _keypair()
        # 错签名
        r = self.client.post(TICKET_PATH, headers=self._signed_headers(sk, device_id, bad_sig=True))
        self.assertEqual(r.status_code, 401)
        # 别的钥匙签（公钥不符）
        r = self.client.post(TICKET_PATH, headers=self._signed_headers(other_sk, device_id))
        self.assertEqual(r.status_code, 401)
        # 过期时间戳
        r = self.client.post(
            TICKET_PATH, headers=self._signed_headers(sk, device_id, ts=int(time.time()) - 600))
        self.assertEqual(r.status_code, 401)
        # nonce 重放
        self._mint(sk, device_id, nonce="fake-nonce-replay01")
        r = self.client.post(
            TICKET_PATH, headers=self._signed_headers(sk, device_id, nonce="fake-nonce-replay01"))
        self.assertEqual(r.status_code, 401)
        # 未知设备（id 形如 32-hex 但未配对）
        sk2, pk2 = _keypair()
        import hashlib

        ghost = hashlib.sha256(pk2.encode()).hexdigest()[:32]
        r = self.client.post(TICKET_PATH, headers=self._signed_headers(sk2, ghost))
        self.assertEqual(r.status_code, 401)

    # -- hello 用票 -----------------------------------------------------------------
    def _hello_with_ticket(self, ws, ticket, role="mobile"):
        ws.send_json({"v": "1.0", "id": "h1", "type": "hello", "payload": {
            "ticket": ticket, "role": role, "name": "fake-phone", "subscribe": ["run.*"]}})
        return ws.receive_json()

    def test_hello_with_ticket_forces_mobile_and_binds_device(self):
        sk, device_id = self._pair()
        ticket = self._mint(sk, device_id)["ticket"]
        with self.client.websocket_connect("/api/link") as ws:
            msg = self._hello_with_ticket(ws, ticket)
            self.assertEqual(msg["type"], "welcome")
            clients = self.rpc("link.clients")["result"]["clients"]
            me = [c for c in clients if c["name"] == "fake-phone"]
            self.assertEqual(me[0]["role"], "mobile")
            self.assertEqual(me[0]["meta"]["device_id"], device_id)

    def test_hello_ticket_rejects_non_mobile_role_and_bad_ticket(self):
        sk, device_id = self._pair()
        ticket = self._mint(sk, device_id)["ticket"]
        with self.client.websocket_connect("/api/link") as ws:
            msg = self._hello_with_ticket(ws, ticket, role="console")
            self.assertEqual(msg["type"], "error")
            self.assertEqual(msg["payload"]["code"], "invalid_params")
        with self.client.websocket_connect("/api/link") as ws:
            msg = self._hello_with_ticket(ws, "fake-ticket")
            self.assertEqual(msg["payload"]["code"], "unauthorized")

    def test_expired_ticket_rejected(self):
        sk, device_id = self._pair()
        ticket = self._mint(sk, device_id)["ticket"]
        rec = self.app.db.kv_get("link_ticket", ticket)
        rec["expires_at"] = 1  # 强行过期
        self.app.db.kv_set("link_ticket", ticket, rec)
        with self.client.websocket_connect("/api/link") as ws:
            msg = self._hello_with_ticket(ws, ticket)
            self.assertEqual(msg["payload"]["code"], "unauthorized")

    # -- M1：mobile 白名单 ----------------------------------------------------------
    def test_mobile_role_allowlist(self):
        sk, device_id = self._pair()
        ticket = self._mint(sk, device_id)["ticket"]
        scripted(self.app, "手机你好")
        with self.client.websocket_connect("/api/link") as ws:
            self._hello_with_ticket(ws, ticket)
            ws.send_json({"v": "1.0", "id": "r1", "type": "rpc.request",
                          "payload": {"method": "chat.send", "params": {"text": "你好"}}})
            ws.send_json({"v": "1.0", "id": "r2", "type": "rpc.request",
                          "payload": {"method": "config.get", "params": {}}})
            ws.send_json({"v": "1.0", "id": "r3", "type": "rpc.request",
                          "payload": {"method": "memory.remember", "params": {"content": "x"}}})
            ws.send_json({"v": "1.0", "id": "r4", "type": "rpc.request",
                          "payload": {"method": "approvals.list", "params": {}}})
            got = {}
            for _ in range(60):
                msg = ws.receive_json()
                if msg["type"] == "rpc.response":
                    got[msg["corr"]] = msg["payload"]
                if len(got) == 4:
                    break
            self.assertTrue(got["r1"]["ok"])
            self.assertEqual(got["r1"]["result"]["text"], "手机你好")
            self.assertEqual(got["r2"]["error"]["code"], "forbidden")   # 管理面不给
            self.assertEqual(got["r3"]["error"]["code"], "forbidden")   # memory 写不给
            self.assertTrue(got["r4"]["ok"])                            # 审批可见

    # -- 撤销联动 -------------------------------------------------------------------
    def test_revoke_kicks_link_and_invalidates_ticket(self):
        sk, device_id = self._pair()
        ticket = self._mint(sk, device_id)["ticket"]
        with self.client.websocket_connect("/api/link") as ws:
            self._hello_with_ticket(ws, ticket)
            r = self.rpc("devices.revoke", {"id": device_id})
            self.assertEqual(r["result"]["link_kicked"], 1)
            msg = ws.receive_json()
            self.assertEqual(msg["type"], "error")
            self.assertEqual(msg["payload"]["code"], "device_revoked")
        # 旧票立即失效
        with self.client.websocket_connect("/api/link") as ws:
            msg = self._hello_with_ticket(ws, ticket)
            self.assertEqual(msg["payload"]["code"], "unauthorized")

    # -- M4：artifacts 上行/删除 ------------------------------------------------------
    def test_artifacts_upload_get_delete_roundtrip(self):
        data = b"\x89PNG fake image bytes"
        r = self.client.post("/api/artifacts", content=data,
                             headers={**self.h, "X-Whale-Name": "fake.png",
                                      "X-Whale-Content-Type": "image/png"})
        self.assertEqual(r.status_code, 201, r.text)
        art = r.json()["result"]
        self.assertTrue(art["id"].startswith("art_"))
        import hashlib

        self.assertEqual(art["sha256"], hashlib.sha256(data).hexdigest())
        got = self.client.get(art["url"], headers=self.h)
        self.assertEqual(got.content, data)
        self.assertEqual(got.headers["content-type"], "image/png")
        d = self.client.delete(art["url"], headers=self.h)
        self.assertTrue(d.json()["ok"])
        self.assertEqual(self.client.get(art["url"], headers=self.h).status_code, 404)

    def test_artifacts_accept_link_ticket_and_reject_anonymous(self):
        sk, device_id = self._pair()
        ticket = self._mint(sk, device_id)["ticket"]
        r = self.client.post("/api/artifacts", content=b"fake-bytes",
                             headers={"X-Whale-Link-Ticket": ticket,
                                      "X-Whale-Content-Type": "text/plain"})
        self.assertEqual(r.status_code, 201)
        url = r.json()["result"]["url"]
        self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.get(url, headers={"X-Whale-Link-Ticket": ticket}).status_code, 200)
        self.assertEqual(self.client.get(f"{url}?ticket={ticket}").status_code, 200)

    # -- F2：/api/chat/stream 接票（纯 C 部署 SSE 退路） ------------------------------
    def test_chat_stream_accepts_ticket_channel_mobile(self):
        sk, device_id = self._pair()
        ticket = self._mint(sk, device_id)["ticket"]
        scripted(self.app, "流式票通")
        with self.client.stream("POST", "/api/chat/stream",
                                headers={"X-Whale-Link-Ticket": ticket},
                                json={"text": "说一句"}) as r:
            self.assertEqual(r.status_code, 200)
            body = "".join(r.iter_text())
        self.assertIn("event: delta", body)
        self.assertIn("event: done", body)
        # 票据来源渠道默认 mobile：会话落在 mobile 渠道
        convs = self.rpc("conversations.list", {"channel": "mobile"})["result"]["conversations"]
        self.assertTrue(any(c["title"] == "说一句" for c in convs))

    def test_chat_stream_ticket_auth_matrix(self):
        sk, device_id = self._pair()
        scripted(self.app, "x")
        # 无票无 token → 401
        r = self.client.post("/api/chat/stream", json={"text": "hi"})
        self.assertEqual(r.status_code, 401)
        # 过期票 → 401
        ticket = self._mint(sk, device_id)["ticket"]
        rec = self.app.db.kv_get("link_ticket", ticket)
        rec["expires_at"] = 1
        self.app.db.kv_set("link_ticket", ticket, rec)
        r = self.client.post("/api/chat/stream", json={"text": "hi"},
                             headers={"X-Whale-Link-Ticket": ticket})
        self.assertEqual(r.status_code, 401)
        # 撤销设备的票 → 401
        sk2, dev2 = self._pair("fake-phone-2")
        ticket2 = self._mint(sk2, dev2, nonce="fake-nonce-000002")["ticket"]
        self.rpc("devices.revoke", {"id": dev2})
        r = self.client.post("/api/chat/stream", json={"text": "hi"},
                             headers={"X-Whale-Link-Ticket": ticket2})
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()
