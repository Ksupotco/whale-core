import json
import unittest

from whale.kernel.ids import now_ms
from whale.memory.store import DAY_MS
from whale.models.base import Completion

from helpers import make_app, scripted


class MemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = make_app()
        self.mem = self.app.memory

    async def test_remember_and_recall_chinese_without_segmentation(self):
        await self.mem.remember("用户养了一只叫年糕的橘猫", kind="fact", importance=0.7)
        await self.mem.remember("用户在杭州做软件工程师", kind="fact")
        await self.mem.remember("用户喜欢喝冰美式", kind="preference")
        hits = await self.mem.retrieve("我的猫叫什么名字")
        self.assertTrue(hits)
        self.assertIn("年糕", hits[0].item.content)
        self.assertTrue(hits[0].why)
        coffee = await self.mem.retrieve("我平时喝什么咖啡")
        self.assertIn("美式", coffee[0].item.content)

    async def test_duplicate_reinforces_instead_of_duplicating(self):
        a = await self.mem.remember("用户的生日是3月14日")
        b = await self.mem.remember("用户的生日是3月14日。")
        self.assertEqual(b["action"], "reinforced")
        self.assertEqual(a["item"]["id"], b["item"]["id"])
        _, total = self.mem.store.list()
        self.assertEqual(total, 1)

    async def test_update_supersedes_old_fact(self):
        await self.mem.remember("用户的猫叫年糕")
        # fast model judges the relation as an update
        scripted(self.app, Completion(text=json.dumps({"relation": "update"})), role="fast")
        self.app.models.overrides["fast"].name = "judge"
        res = await self.mem.remember("用户的猫其实叫团子，之前说错了")
        self.assertEqual(res["action"], "created")
        self.assertEqual(len(res["superseded"]), 1)
        old = self.mem.store.get(res["superseded"][0])
        self.assertEqual(old.status, "superseded")
        hits = await self.mem.retrieve("猫叫什么")
        self.assertTrue(all(h.item.status == "active" for h in hits))
        self.assertIn("团子", hits[0].item.content)

    async def test_extraction_requires_verbatim_quote(self):
        scripted(self.app, Completion(text=json.dumps({"memories": [
            {"kind": "fact", "content": "用户在上海工作", "quote": "我在上海上班", "importance": 0.6},
            {"kind": "fact", "content": "用户是医生", "quote": "我是一名医生"},       # not said → dropped
            {"kind": "preference", "content": "用户讨厌香菜", "quote": ""},            # no quote → dropped
        ]}, ensure_ascii=False)), role="fast")
        self.app.models.overrides["fast"].name = "extractor"
        created = await self.mem.extract([("user", "我在上海上班，最近有点累"), ("assistant", "辛苦啦")],
                                         source={"conversation_id": "c1"})
        self.assertEqual([c["item"]["content"] for c in created], ["用户在上海工作"])
        self.assertEqual(created[0]["item"]["source"]["quote"], "我在上海上班")

    async def test_offline_extraction_heuristics(self):
        created = await self.mem.extract([("user", "我养了一只柯基叫豆豆"), ("assistant", "好可爱")], source={})
        self.assertTrue(created)

    async def test_scopes_isolate_people_but_share_agent_knowledge(self):
        await self.mem.remember("用户的银行卡尾号是 0423", importance=0.9)                 # owner
        await self.mem.remember("小李喜欢打羽毛球", scope="qq:1001")                       # a QQ friend
        await self.mem.remember("羽毛球比赛采用21分制", kind="knowledge")                    # shared
        owner = [h.item.content for h in await self.mem.retrieve("羽毛球")]
        self.assertIn("羽毛球比赛采用21分制", owner)
        self.assertNotIn("小李喜欢打羽毛球", owner)
        friend = [h.item.content for h in await self.mem.retrieve("羽毛球", scope="qq:1001")]
        self.assertIn("小李喜欢打羽毛球", friend)
        self.assertIn("羽毛球比赛采用21分制", friend)
        self.assertEqual(await self.mem.retrieve("银行卡尾号", scope="qq:1001"), [])
        everyone = [h.item.content for h in await self.mem.retrieve("羽毛球", scope=None)]
        self.assertEqual(len(everyone), 2)

    async def test_forget_is_a_tombstone(self):
        r = await self.mem.remember("用户的密码提示是小时候的街道")
        await self.mem.forget(r["item"]["id"])
        item = self.mem.store.get(r["item"]["id"])
        self.assertEqual(item.status, "forgotten")
        self.assertEqual(item.content, "")
        self.assertEqual(await self.mem.retrieve("密码提示"), [])

    async def test_decay_and_maintenance_archive_faded_episodes(self):
        r = await self.mem.remember("和用户闲聊了天气", kind="episode", importance=0.2, consolidate=False)
        old = now_ms() - 400 * DAY_MS
        self.app.db.execute("UPDATE memories SET last_access_at=?, updated_at=? WHERE id=?", (old, old, r["item"]["id"]))
        keep = await self.mem.remember("用户对花生过敏", kind="fact", importance=0.9)
        out = await self.mem.maintain()
        self.assertEqual(out["archived"], 1)
        self.assertEqual(self.mem.store.get(r["item"]["id"]).status, "archived")
        self.assertEqual(self.mem.store.get(keep["item"]["id"]).status, "active")

    async def test_graph_has_nodes_and_edges(self):
        a = await self.mem.remember("用户喜欢爬山")
        b = await self.mem.remember("用户周末常去爬山")
        g = self.mem.graph()
        ids = {n["id"] for n in g["nodes"]}
        self.assertIn(a["item"]["id"], ids)
        if b["action"] == "created":
            self.assertTrue(any({e["src"], e["dst"]} == {a["item"]["id"], b["item"]["id"]} for e in g["edges"]))


if __name__ == "__main__":
    unittest.main()
