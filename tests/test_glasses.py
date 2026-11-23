"""眼镜面（role=glasses）：Link 列管、帧上限、context/qq_reply/deliver/schedule、轻量档、提醒。

假名纪律：测试内 QQ 号/会话/文本全为假名。
"""
import json
import unittest

from starlette.testclient import TestClient

from whale.channels.qq import QQChannel
from whale.server.http import LINK_MAX_FRAME_BYTES, create_server

from helpers import make_app, scripted


class FakeQQ(QQChannel):
    """记录 outbound 的假 QQ backend（owner=假名 10001）。"""

    backend_name = "fake"

    def __init__(self, app):
        super().__init__(app, "qq", {"owner_qq": "10001"})
        self.connected = True
        self.sent: list[tuple] = []

    async def send_text(self, kind, target_uin, target_uid, text, reply_to="", at=None):
        self.sent.append(("text", target_uin, text))
        return {"status": "ok", "sent": 1, "total": 1, "message_ids": ["m1"], "error": ""}

    async def send_image(self, kind, target, data, mime="image/png"):
        self.sent.append(("image", target, len(data), mime))
        return {"status": "ok", "sent": 1, "total": 1, "message_ids": ["m2"], "error": ""}


class GlassesTests(unittest.TestCase):
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

    def _ws_hello(self, ws, role="glasses", name="fake-glasses", subscribe=None):
        ws.send_json({"v": "1.0", "id": "h1", "type": "hello", "payload": {
            "token": self.app.config.api_token, "role": role, "name": name,
            "subscribe": subscribe or ["run.*"]}})
        welcome = ws.receive_json()
        self.assertEqual(welcome["type"], "welcome")

    # -- C2：role 列管 ------------------------------------------------------------
    def test_glasses_role_allowlist(self):
        with self.client.websocket_connect("/api/link") as ws:
            self._ws_hello(ws)
            scripted(self.app, "你好呀")
            ws.send_json({"v": "1.0", "id": "r1", "type": "rpc.request",
                          "payload": {"method": "chat.send", "params": {"text": "你好"}}})
            ws.send_json({"v": "1.0", "id": "r2", "type": "rpc.request",
                          "payload": {"method": "config.get", "params": {}}})
            ws.send_json({"v": "1.0", "id": "r3", "type": "rpc.request",
                          "payload": {"method": "glasses.schedule.list", "params": {}}})
            got = {}
            for _ in range(60):
                msg = ws.receive_json()
                if msg["type"] == "rpc.response":
                    got[msg["corr"]] = msg["payload"]
                if len(got) == 3:
                    break
            self.assertTrue(got["r1"]["ok"])
            self.assertEqual(got["r1"]["result"]["text"], "你好呀")
            self.assertFalse(got["r2"]["ok"])
            self.assertEqual(got["r2"]["error"]["code"], "forbidden")
            self.assertTrue(got["r3"]["ok"])

    def test_console_role_is_not_restricted(self):
        with self.client.websocket_connect("/api/link") as ws:
            self._ws_hello(ws, role="console", name="fake-console")
            ws.send_json({"v": "1.0", "id": "r1", "type": "rpc.request",
                          "payload": {"method": "config.get", "params": {}}})
            for _ in range(20):
                msg = ws.receive_json()
                if msg["type"] == "rpc.response" and msg["corr"] == "r1":
                    self.assertTrue(msg["payload"]["ok"])
                    break
            else:
                self.fail("no rpc.response for console config.get")

    # -- C3：帧上限 ----------------------------------------------------------------
    def test_oversize_frame_rejected_connection_stays(self):
        with self.client.websocket_connect("/api/link") as ws:
            self._ws_hello(ws)
            ws.send_text(" " * (LINK_MAX_FRAME_BYTES + 16))
            msg = ws.receive_json()
            self.assertEqual(msg["type"], "error")
            self.assertEqual(msg["payload"]["code"], "invalid_params")
            ws.send_json({"v": "1.0", "id": "p1", "type": "ping", "payload": {}})
            msg = ws.receive_json()
            self.assertEqual(msg["type"], "pong")

    # -- context.sync 契约 ----------------------------------------------------------
    def _today(self, offset=0):
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        return (datetime.now(ZoneInfo(self.app.config.value.agent.timezone)).date()
                + timedelta(days=offset)).isoformat()

    def test_context_sync_contract_and_idempotency(self):
        day = self._today()
        body = {"calendar": {day: [{"id": "e1", "title": "组会", "start": "10:00",
                                    "end": "11:00", "allDay": False}]},
                "source": "fake.calendar"}
        r1 = self.rpc("glasses.context.sync", body)["result"]
        self.assertTrue(r1["ok"])
        self.assertEqual(r1["stored"], 1)
        self.assertFalse(r1["idempotent"])
        r2 = self.rpc("glasses.context.sync", body)["result"]
        self.assertTrue(r2["idempotent"])
        self.assertEqual(r2["receivedAt"], r1["receivedAt"])  # 幂等重放不刷新时间
        # null = 未读（三态之一，语义保留）
        r3 = self.rpc("glasses.context.sync", {"calendar": {day: None, self._today(1): []}})["result"]
        self.assertEqual(sorted(r3["dates"]), sorted([day, self._today(1)]))

    def test_context_sync_contract_errors(self):
        day = self._today()
        cases = [
            ({"calendar": {"not-a-date": []}}, "invalid_date"),
            ({"calendar": {self._today(9): []}}, "invalid_date"),           # 出窗口
            ({"calendar": {day: [{"title": "x"}] * 13}}, "too_many_entries"),
            ({"calendar": {day: [{"title": "长" * 121}]}}, "title_too_long"),
            ({"calendar": {day: [{"title": ""}]}}, "invalid_json"),
            ({"calendar": {day: [{"title": "x", "allDay": "yes"}]}}, "invalid_json"),
            ({}, "invalid_json"),                                            # calendar 缺失
        ]
        for body, code in cases:
            r = self.rpc("glasses.context.sync", body, status=400)
            self.assertEqual(r["error"]["code"], "invalid_params")
            self.assertEqual(r["error"]["details"]["code"], code, body)

    def test_context_injected_into_glasses_chat_only(self):
        self.rpc("glasses.context.sync", {
            "calendar": {self._today(): [{"title": "复诊", "start": "15:00", "allDay": False}]},
            "location": {"city": "假名市"}, "weather": {"text": "晴 26°C"}})
        model = scripted(self.app, "收到")
        with self.client.websocket_connect("/api/link") as ws:
            self._ws_hello(ws)
            ws.send_json({"v": "1.0", "id": "r1", "type": "rpc.request",
                          "payload": {"method": "chat.send", "params": {"text": "今天怎么样"}}})
            for _ in range(60):
                msg = ws.receive_json()
                if msg["type"] == "rpc.response" and msg["corr"] == "r1":
                    self.assertTrue(msg["payload"]["ok"])
                    break
        system = model.calls[0]["messages"][0].text
        self.assertIn("当轮手机事实资料", system)
        self.assertIn("复诊", system)
        self.assertIn("假名市", system)
        self.assertIn("晴 26°C", system)
        # 别的渠道不注入
        model2 = scripted(self.app, "好")
        self.rpc("chat.send", {"text": "hi"})
        self.assertNotIn("当轮手机事实资料", model2.calls[0]["messages"][0].text)

    # -- qq_reply / deliver ----------------------------------------------------------
    def test_qq_reply_fixed_owner_target(self):
        fake = FakeQQ(self.app)
        self.app.channels.channels["qq"] = fake
        r = self.rpc("glasses.qq_reply", {"text": "我晚点回你"})["result"]
        self.assertTrue(r["ok"])
        self.assertEqual(fake.sent, [("text", "10001", "我晚点回你")])

    def test_qq_reply_without_channel_is_unavailable(self):
        r = self.rpc("glasses.qq_reply", {"text": "hi"}, status=503)
        self.assertEqual(r["error"]["code"], "unavailable")
        self.assertTrue(r["error"]["retryable"])

    def test_deliver_text_and_image_and_unsupported_audio(self):
        fake = FakeQQ(self.app)
        self.app.channels.channels["qq"] = fake
        img = self.app.artifacts.save(b"\x89PNGfakedata", content_type="image/png", name="fake.png")
        aud = self.app.artifacts.save(b"fakeaudio", content_type="audio/mpeg", name="fake.mp3")
        r = self.rpc("glasses.deliver", {"text": "看图", "artifact_ids": [img["id"], aud["id"]],
                                         "summary": "fake"})["result"]
        self.assertFalse(r["ok"])  # 音频如实 unsupported → 整体不谎报
        by_detail = json.dumps(r["receipts"], ensure_ascii=False)
        self.assertIn("unsupported_audio", by_detail)
        self.assertEqual(fake.sent[0], ("text", "10001", "看图"))
        self.assertEqual(fake.sent[1][0], "image")

    @staticmethod
    def _notifier(got: list):
        async def notify(title, body, meta):
            got.append((title, body))
            return "fake:notifier"

        return notify

    def test_deliver_falls_back_to_notify_when_qq_offline(self):
        got = []
        self.app.hub.add_notifier(self._notifier(got))
        r = self.rpc("glasses.deliver", {"text": "离线投递"})["result"]
        self.assertTrue(r["ok"])
        self.assertEqual(r["receipts"][0]["channel"], "notify")
        self.assertEqual(got, [("鲸鱼", "离线投递")])

    def test_deliver_requires_content(self):
        r = self.rpc("glasses.deliver", {}, status=400)
        self.assertEqual(r["error"]["code"], "invalid_params")

    # -- schedule --------------------------------------------------------------------
    def test_schedule_full_flow_and_chat_extraction(self):
        # chat 钩子确定性抽取（眼镜渠道）
        with self.client.websocket_connect("/api/link") as ws:
            self._ws_hello(ws)
            scripted(self.app, "记下了")
            ws.send_json({"v": "1.0", "id": "r1", "type": "rpc.request",
                          "payload": {"method": "chat.send",
                                      "params": {"text": "明天下午三点开组会"}}})
            for _ in range(60):
                msg = ws.receive_json()
                if msg["type"] == "rpc.response" and msg["corr"] == "r1":
                    break
        cands = self.rpc("glasses.schedule.candidates")["result"]["candidates"]
        self.assertEqual(len(cands), 1)
        cand = cands[0]
        self.assertEqual(cand["status"], "pending")
        self.assertEqual(cand["time"], "15:00")
        self.assertTrue(cand["source"]["conversation"].startswith("conv_") or
                        cand["source"]["conversation"])
        # 溯源：±10 条上下文含原话
        src = self.rpc("glasses.schedule.source", {
            "conversation": cand["source"]["conversation"],
            "messageId": cand["source"]["messageId"]})["result"]
        self.assertTrue(any("组会" in m["text"] for m in src["context"]))
        # approve → 正式条目
        r = self.rpc("glasses.schedule.candidate_action", {"id": cand["cand_id"], "action": "add"})["result"]
        self.assertTrue(r["ok"])
        self.assertEqual(len(self.rpc("glasses.schedule.list")["result"]["items"]), 1)
        # 重复裁决 → 404
        self.rpc("glasses.schedule.candidate_action", {"id": cand["cand_id"], "action": "add"}, status=404)
        # 手动增删
        item = self.rpc("glasses.schedule.add_item", {"title": "体检", "date": self._today(2)})["result"]["item"]
        self.assertTrue(self.rpc("glasses.schedule.delete_item", {"id": item["item_id"]})["result"]["ok"])
        bad = self.rpc("glasses.schedule.add_item", {"title": "x", "date": "2026-13-40"}, status=400)
        self.assertEqual(bad["error"]["code"], "invalid_params")

    def test_schedule_items_merge_into_context_hint(self):
        self.rpc("glasses.schedule.add_item", {"title": "取快递", "date": self._today(), "time": "18:00"})
        self.rpc("glasses.context.sync", {"calendar": {self._today(): []}})
        hint = self.app.glasses.context_hint()
        self.assertIn("取快递", hint)
        # null 未读语义不被条目覆盖
        self.rpc("glasses.context.sync", {"calendar": {self._today(1): None}})
        self.rpc("glasses.schedule.add_item", {"title": "明日事", "date": self._today(1)})
        hint = self.app.glasses.context_hint()
        self.assertIn("未能读取", hint)
        self.assertNotIn("明日事", hint)

    # -- 轻量档（D1） ---------------------------------------------------------------
    def test_fast_role_and_remember_false(self):
        fast = scripted(self.app, "快答", role="fast")
        chat = scripted(self.app, "慢答", role="chat")
        r = self.rpc("chat.send", {"text": "我养了一只猫", "model_role": "fast",
                                   "remember": False})["result"]
        self.assertEqual(r["text"], "快答")
        self.assertEqual(len(fast.calls), 1)
        self.assertEqual(len(chat.calls), 0)
        run = self.rpc("runs.get", {"id": r["run_id"]})["result"]
        recall = run["steps"][0]
        self.assertEqual(recall["kind"], "recall")
        self.assertIn("skipped", recall["output"])
        # 不进记忆管道
        self.assertEqual(self.rpc("memory.list")["result"]["total"], 0)

    # -- 提醒循环（D3） --------------------------------------------------------------
    def test_remind_tick_judge_and_notify(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        hour = datetime.now(ZoneInfo(self.app.config.value.agent.timezone)).hour
        self.app.config.update({"autonomy": {"quiet_hours": [(hour + 1) % 24, (hour + 2) % 24]}})  # 当前必不在静默窗
        self.rpc("glasses.schedule.add_item", {"title": "复试", "date": self._today(), "time": "23:59"})
        got = []
        self.app.hub.add_notifier(self._notifier(got))
        scripted(self.app, "弹提醒", role="fast")

        import asyncio

        loop = asyncio.new_event_loop()
        out = loop.run_until_complete(self.app.glasses.remind_tick({}))
        loop.close()
        self.assertEqual(out["verdict"], "弹提醒")
        self.assertTrue(any("复试" in body for _title, body in got))

    def test_remind_tick_quiet_hours_and_empty(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        hour = datetime.now(ZoneInfo(self.app.config.value.agent.timezone)).hour
        self.app.config.update({"autonomy": {"quiet_hours": [hour, (hour + 1) % 24]}})  # 当前必在静默窗
        self.rpc("glasses.schedule.add_item", {"title": "复试", "date": self._today()})
        import asyncio

        loop = asyncio.new_event_loop()
        out = loop.run_until_complete(self.app.glasses.remind_tick({}))
        self.assertEqual(out["verdict"], "沉默")
        self.assertEqual(out["reason"], "quiet_hours")
        self.app.config.update({"autonomy": {"quiet_hours": [(hour + 1) % 24, (hour + 2) % 24]}})  # 当前必不在窗
        self.rpc("glasses.schedule.delete_item",
                 {"id": self.rpc("glasses.schedule.list")["result"]["items"][0]["item_id"]})
        out = loop.run_until_complete(self.app.glasses.remind_tick({}))
        loop.close()
        self.assertEqual(out["reason"], "no_items")

    def test_remind_job_registered_disabled_by_default(self):
        job = next(j for j in self.rpc("schedule.list")["result"]["jobs"] if j["id"] == "sys_glasses_remind")
        self.assertFalse(job["enabled"])
        self.app.config.update({"glasses": {"schedule_remind": True}})
        job = next(j for j in self.rpc("schedule.list")["result"]["jobs"] if j["id"] == "sys_glasses_remind")
        self.assertTrue(job["enabled"])


if __name__ == "__main__":
    unittest.main()
