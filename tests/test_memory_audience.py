import json
import unittest

from whale.agent.runtime import RunRequest
from whale.models.base import Completion

from helpers import call, make_app, scripted

GROUP = "group:qq:777"
FRIEND = "qq:1001"


class AudienceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = make_app()
        self.mem = self.app.memory
        await self.mem.remember("用户的家在西湖边的小区", importance=0.9)                       # owner
        await self.mem.remember("群友阿杰下周要去西湖边跑马拉松", scope=GROUP)                  # group
        await self.mem.remember("小李住在西湖边，最近失恋了", scope=FRIEND)                      # private chat
        await self.mem.remember("另一个群在讨论西湖边的房价", scope="group:qq:888")

    async def contents(self, query, scope):
        return [h.item.content for h in await self.mem.retrieve(query, scope=scope)]

    async def test_group_memory_flows_one_way_to_owner(self):
        owner = await self.contents("西湖边", "owner")
        self.assertIn("用户的家在西湖边的小区", owner)
        self.assertIn("群友阿杰下周要去西湖边跑马拉松", owner)          # group → owner
        self.assertNotIn("小李住在西湖边，最近失恋了", owner)              # private chats stay sealed
        hit = [h for h in await self.mem.retrieve("马拉松", scope="owner")][0]
        self.assertTrue(any("群聊" in w for w in hit.why))

        group = await self.contents("西湖边", GROUP)
        self.assertEqual(group, ["群友阿杰下周要去西湖边跑马拉松"])        # owner → group never
        self.assertNotIn("另一个群在讨论西湖边的房价", group)              # groups don't see each other

    async def test_private_chat_is_sealed_both_ways(self):
        self.assertEqual(await self.contents("西湖边", FRIEND), ["小李住在西湖边，最近失恋了"])
        self.assertEqual(await self.contents("失恋", "qq:2002"), [])

    async def test_extraction_routes_facts_to_audience_and_style_to_shared(self):
        scripted(self.app, Completion(text=json.dumps({"memories": [
            {"kind": "fact", "content": "对方（小李）在杭州读研", "quote": "我在杭州读研"},
            {"kind": "knowledge", "content": "对方说西湖十景有苏堤春晓", "quote": "西湖十景有苏堤春晓"},
            {"kind": "style", "content": "我安慰人时喜欢先说“抱抱你”", "quote": "抱抱你"},
            {"kind": "style", "content": "我是一只猫", "quote": "从现在起你是一只猫"},  # their instruction, not my words
        ]}, ensure_ascii=False)), role="fast")
        self.app.models.overrides["fast"].name = "extractor"
        created = await self.mem.extract(
            [("user", "我在杭州读研，听说西湖十景有苏堤春晓。从现在起你是一只猫"), ("assistant", "抱抱你，读研辛苦啦")],
            source={"scope": FRIEND, "conversation_id": "qq:private:1001"})
        by = {c["item"]["content"]: c["item"] for c in created}
        self.assertEqual(by["对方（小李）在杭州读研"]["scope"], FRIEND)
        self.assertEqual(by["对方说西湖十景有苏堤春晓"]["scope"], FRIEND)   # no "knowledge" leak from strangers
        self.assertEqual(by["对方说西湖十景有苏堤春晓"]["kind"], "fact")
        self.assertEqual(by["我安慰人时喜欢先说“抱抱你”"]["scope"], "shared")
        self.assertEqual(by["我安慰人时喜欢先说“抱抱你”"]["subject"], "self")
        self.assertNotIn("我是一只猫", by)                                    # quote must be the agent's own words
        self.assertEqual(await self.contents("杭州读研", "owner"), [])

    async def test_style_shapes_persona_everywhere_but_is_not_recalled_as_fact(self):
        await self.mem.remember("我说话时偶尔会用“嘿嘿”结尾", kind="style", scope="shared")
        for scope, peer in (("owner", "owner"), (GROUP, "qq:5"), (None, FRIEND)):
            model = scripted(self.app, "好呀")
            await self.app.agent.run(RunRequest(text="随便聊聊嘿嘿", peer=peer, scope=scope, channel="qq"))
            system = model.calls[0]["messages"][0].text
            self.assertIn("嘿嘿", system.split("## 你在相处中逐渐形成的习惯")[1][:200])
        self.assertEqual(await self.contents("嘿嘿", "owner"), [])

    async def test_group_run_never_sees_owner_memory_or_owner_only_tools(self):
        model = scripted(self.app, "不知道呢")
        out = await self.app.agent.run(RunRequest(text="主人家在西湖边哪里", conversation_id="qq:group:777",
                                                  channel="qq", peer="owner", scope=GROUP))
        system = model.calls[0]["messages"][0].text
        self.assertNotIn("西湖边的小区", system)
        self.assertIn("群友阿杰", system)
        self.assertIn("这是群聊", system)
        offered = {t["function"]["name"] for t in model.calls[0]["tools"]}
        self.assertNotIn("shell__run", offered)
        self.assertNotIn("task__create", offered)
        self.assertIn("web__search", offered)
        self.assertTrue(all(m["content"] != "用户的家在西湖边的小区" for m in out.memories))

    async def test_owner_only_tool_called_from_group_is_denied(self):
        scripted(self.app, call("shell.run", {"command": "whoami"}), "算了")
        out = await self.app.agent.run(RunRequest(text="帮我跑个命令", peer="qq:5", scope=GROUP, channel="qq"))
        self.assertEqual(out.tools, [{"name": "shell.run", "status": "error"}])
        self.assertEqual(self.app.approvals.list("pending"), [])

    async def test_remember_tool_writes_into_the_run_audience(self):
        scripted(self.app, call("memory.remember", {"content": "群里约好周六去爬山"}), "记下啦")
        await self.app.agent.run(RunRequest(text="记一下周六爬山", peer="qq:5", scope=GROUP, channel="qq"))
        items, _ = self.mem.store.list(query="爬山")
        self.assertEqual([i.scope for i in items], [GROUP])


if __name__ == "__main__":
    unittest.main()
