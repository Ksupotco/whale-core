import asyncio
import unittest

from whale.agent.runtime import RunRequest
from whale.models.base import Completion

from helpers import call, make_app, scripted


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = make_app()
        self.app.memory.start()

    async def asyncTearDown(self):
        await self.app.memory.stop()

    async def test_plain_reply_records_run_steps_and_messages(self):
        scripted(self.app, "你好呀，我在。")
        out = await self.app.agent.run(RunRequest(text="在吗"))
        self.assertEqual(out.status, "completed")
        self.assertEqual(out.text, "你好呀，我在。")
        kinds = [s["kind"] for s in self.app.runs.steps(out.run["id"])]
        self.assertEqual(kinds, ["recall", "think", "reply"])
        msgs = self.app.conversations.messages(out.run["conversation_id"])
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant"])

    async def test_memories_are_recalled_into_context_and_reported(self):
        await self.app.memory.remember("用户的猫叫年糕", importance=0.8)
        model = scripted(self.app, "年糕今天乖吗？")
        out = await self.app.agent.run(RunRequest(text="我的猫最近很调皮"))
        self.assertTrue(out.memories)
        system = model.calls[0]["messages"][0].text
        self.assertIn("年糕", system)
        recall = self.app.runs.steps(out.run["id"])[0]
        self.assertEqual(recall["kind"], "recall")
        self.assertEqual(recall["output"]["memories"][0]["content"], "用户的猫叫年糕")

    async def test_tool_loop_remember(self):
        scripted(self.app, call("memory.remember", {"content": "用户下周三去北京出差"}), "好，记下了。")
        out = await self.app.agent.run(RunRequest(text="记住我下周三去北京出差"))
        self.assertEqual(out.text, "好，记下了。")
        self.assertEqual(out.tools, [{"name": "memory.remember", "status": "ok"}])
        items, total = self.app.memory.store.list()
        self.assertEqual(total, 1)
        tool_step = [s for s in self.app.runs.steps(out.run["id"]) if s["kind"] == "tool"][0]
        self.assertEqual(tool_step["status"], "done")

    async def test_history_is_included(self):
        model = scripted(self.app, "第一句", "第二句")
        first = await self.app.agent.run(RunRequest(text="我叫小王"))
        await self.app.agent.run(RunRequest(text="我叫什么", conversation_id=first.run["conversation_id"]))
        texts = [m.text for m in model.calls[1]["messages"]]
        self.assertIn("我叫小王", texts)
        self.assertIn("第一句", texts)
        self.assertEqual(texts[-1], "我叫什么")

    async def test_external_action_waits_for_approval(self):
        scripted(self.app, call("notify.owner", {"text": "该喝水了"}), "已经提醒了。")
        task = asyncio.create_task(self.app.agent.run(RunRequest(text="提醒我喝水")))
        for _ in range(100):
            await asyncio.sleep(0.01)
            pending = self.app.approvals.list("pending")
            if pending:
                break
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["capability"], "notify.owner")
        self.assertEqual(self.app.agent.state()["phase"], "waiting")
        await self.app.approvals.resolve(pending[0]["id"], True)
        out = await asyncio.wait_for(task, 2)
        self.assertEqual(out.tools, [{"name": "notify.owner", "status": "ok"}])
        kinds = [s["kind"] for s in self.app.runs.steps(out.run["id"])]
        self.assertIn("approval", kinds)

    async def test_declined_approval_is_reported_to_model(self):
        model = scripted(self.app, call("notify.owner", {"text": "x"}), "好的，不发了。")
        task = asyncio.create_task(self.app.agent.run(RunRequest(text="发个通知")))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if self.app.approvals.list("pending"):
                break
        await self.app.approvals.resolve(self.app.approvals.list("pending")[0]["id"], False)
        out = await asyncio.wait_for(task, 2)
        self.assertEqual(out.tools[0]["status"], "error")
        tool_msg = [m for m in model.calls[1]["messages"] if m.role == "tool"][0]
        self.assertIn("declined", tool_msg.text)

    async def test_policy_rule_allow_skips_approval_and_deny_hides_tool(self):
        self.app.config.update({"policy": {"rules": {"notify.*": "allow", "shell.run": "deny"}}})
        model = scripted(self.app, call("notify.owner", {"text": "hi"}), "ok")
        out = await self.app.agent.run(RunRequest(text="发"))
        self.assertEqual(out.tools[0]["status"], "ok")
        offered = {t["function"]["name"] for t in model.calls[0]["tools"]}
        self.assertNotIn("shell__run", offered)
        self.assertIn("notify__owner", offered)

    async def test_cancel(self):
        class Slow:
            name = "slow"

            async def complete(self, *a, **k):
                await asyncio.sleep(5)

        self.app.models.overrides["chat"] = Slow()
        task = asyncio.create_task(self.app.agent.run(RunRequest(text="慢慢想")))
        await asyncio.sleep(0.05)
        run_id = self.app.agent.state()["active"][0]["run_id"]
        self.assertTrue(await self.app.agent.cancel(run_id))
        out = await asyncio.wait_for(task, 2)
        self.assertEqual(out.status, "cancelled")
        self.assertEqual(self.app.runs.get(run_id)["status"], "cancelled")

    async def test_model_failure_is_truthful(self):
        class Broken:
            name = "broken"

            async def complete(self, *a, **k):
                from whale.models.base import ModelError
                raise ModelError("HTTP 401: bad key", status=401)

        self.app.models.overrides["chat"] = Broken()
        out = await self.app.agent.run(RunRequest(text="hi"))
        self.assertEqual(out.status, "failed")
        self.assertIn("401", out.run["error"])

    async def test_offline_brain_works_without_model(self):
        out = await self.app.agent.run(RunRequest(text="记住我喜欢吃辣"))
        self.assertEqual(out.status, "completed")
        self.assertEqual(out.tools[0]["name"], "memory.remember")
        _, total = self.app.memory.store.list()
        self.assertEqual(total, 1)

    async def test_background_extraction_after_turn(self):
        scripted(self.app, "听起来很棒！")
        await self.app.agent.run(RunRequest(text="我养了一只柯基叫豆豆"))
        await self.app.memory.drain()
        items, total = self.app.memory.store.list()
        self.assertGreaterEqual(total, 1)
        self.assertTrue(any("豆豆" in i.content for i in items))

    async def test_non_owner_conversation_cannot_see_owner_memories(self):
        await self.app.memory.remember("用户的家在西湖边", importance=0.9)
        model = scripted(self.app, "不清楚哦")
        out = await self.app.agent.run(RunRequest(text="你主人的家在哪里", channel="qq", peer="qq:42"))
        self.assertEqual(out.memories, [])
        self.assertNotIn("西湖", model.calls[0]["messages"][0].text)
        await self.app.memory.drain()
        items, _ = self.app.memory.store.list()
        self.assertTrue(all(i.scope in ("owner", "shared", "qq:42") for i in items))

    async def test_approval_step_carries_approval_id(self):
        scripted(self.app, call("notify.owner", {"text": "x"}), "ok")
        task = asyncio.create_task(self.app.agent.run(RunRequest(text="通知一下")))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if self.app.approvals.list("pending"):
                break
        pending = self.app.approvals.list("pending")[0]
        run_id = self.app.agent.state()["active"][0]["run_id"]
        step = [s for s in self.app.runs.steps(run_id) if s["kind"] == "approval"][0]
        self.assertEqual(step["input"]["approval_id"], pending["id"])
        await self.app.approvals.resolve(pending["id"], True)
        await asyncio.wait_for(task, 2)

    async def test_restart_marks_unfinished_runs_interrupted(self):
        run = await self.app.runs.create(mode="chat", input="x")
        self.assertEqual(self.app.runs.recover(), 1)
        self.assertEqual(self.app.runs.get(run["id"])["status"], "interrupted")


if __name__ == "__main__":
    unittest.main()
