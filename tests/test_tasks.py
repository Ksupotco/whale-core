import asyncio
import json
import unittest

from whale.kernel.ids import now_ms
from whale.models.base import Completion

from helpers import call, make_app, scripted


class TaskTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = make_app()
        self.app.memory.start()

    async def asyncTearDown(self):
        await self.app.memory.stop()

    async def test_goal_task_plans_and_completes(self):
        scripted(self.app,
                 call("plan.update", {"steps": [{"step": "搜索资料", "status": "doing"}, {"step": "总结", "status": "todo"}]}),
                 call("plan.update", {"steps": [{"step": "搜索资料", "status": "done"}, {"step": "总结", "status": "done"}]}),
                 "结论：三条要点……")
        task = await self.app.tasks.create("调研一下家用 NAS 方案", start=False)
        done = await self.app.tasks.execute(task["id"])
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["result"], "结论：三条要点……")
        self.assertEqual([p["status"] for p in done["plan"]], ["done", "done"])
        self.assertEqual(done["runs"], 1)

    async def test_delegate_spawns_child_run(self):
        scripted(self.app, call("agent.delegate", {"objective": "查一下 A 方案的价格"}), "子结果：999 元", "汇总：A 方案 999 元")
        task = await self.app.tasks.create("比较方案", start=False)
        done = await self.app.tasks.execute(task["id"])
        self.assertEqual(done["result"], "汇总：A 方案 999 元")
        parent = done["last_run_id"]
        children = self.app.runs.list(parent_id=parent)
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0]["output"], "子结果：999 元")

    async def test_agent_creates_scheduled_reminder(self):
        scripted(self.app, call("task.create", {"goal": "提醒用户喝水", "title": "喝水",
                                                "schedule": {"every_min": 60}}), "好的，每小时提醒你。")
        from whale.agent.runtime import RunRequest

        out = await self.app.agent.run(RunRequest(text="每小时提醒我喝水"))
        self.assertEqual(out.tools[0]["status"], "ok")
        tasks = self.app.tasks.list()
        self.assertEqual(tasks[0]["status"], "scheduled")
        jobs = [j for j in self.app.scheduler.list() if j["handler"] == "task.run"]
        self.assertEqual(len(jobs), 1)
        self.assertGreater(jobs[0]["next_at"], now_ms())

    async def test_one_shot_job_fires_once(self):
        fired = []

        async def handler(args):
            fired.append(args["x"])

        self.app.scheduler.handler("t.once", handler)
        job = self.app.scheduler.add("once", "t.once", {"at": now_ms() - 1000}, {"x": 7})
        await self.app.scheduler.tick()
        await asyncio.sleep(0.05)
        await self.app.scheduler.tick()
        await asyncio.sleep(0.05)
        self.assertEqual(fired, [7])
        job = self.app.scheduler.get(job["id"])
        self.assertFalse(job["enabled"])
        self.assertEqual(job["last_status"], "ok")

    async def test_failing_job_records_error_and_advances(self):
        async def bad(args):
            raise ValueError("nope")

        self.app.scheduler.handler("t.bad", bad)
        job = self.app.scheduler.add("bad", "t.bad", {"every_min": 5})
        await self.app.scheduler.run_now(job["id"])
        job = self.app.scheduler.get(job["id"])
        self.assertEqual(job["last_status"], "error")
        self.assertIn("nope", job["last_error"])
        self.assertGreater(job["next_at"], now_ms())

    async def test_exploration_writes_diary_and_candidate_knowledge(self):
        self.app.config.update({"autonomy": {"interests": ["深海生物"], "proactive": False}})
        scripted(self.app, json.dumps({"diary": "今天读到了关于抹香鲸的资料。",
                                       "findings": [{"content": "抹香鲸可以下潜到两千米深", "source": "https://x"}],
                                       "share": "我刚读到抹香鲸能潜到两千米！"}, ensure_ascii=False))
        res = await self.app.explorer.explore()
        self.assertEqual(res["status"], "completed")
        self.assertTrue(res["diary"])
        self.assertEqual(len(res["findings"]), 1)
        self.assertEqual(self.app.memory.store.get(res["findings"][0]).status, "candidate")
        self.assertFalse(res["proactive"]["sent"])  # proactive disabled → recorded, not sent

    async def test_explore_mode_only_offers_read_capabilities(self):
        model = scripted(self.app, json.dumps({"diary": "x", "findings": []}))
        await self.app.explorer.explore("任意")
        offered = {t["function"]["name"] for t in model.calls[0]["tools"] or []}
        self.assertTrue(offered)
        self.assertTrue(all(n.startswith(("web__", "memory__search", "time__")) for n in offered))


if __name__ == "__main__":
    unittest.main()
