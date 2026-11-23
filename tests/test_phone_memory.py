import argparse
import json
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from whale.agent.runtime import RunRequest
from whale.devices import privacy
from whale.kernel.ids import now_ms, ulid
from whale.models.base import Completion

from helpers import make_app, scripted


def event(summary="在 B 站看了一个关于抹香鲸的视频", package="tv.danmaku.bili", privacy_class="owner_private", **extras):
    return {"event_id": "evt_" + ulid(), "kind": "screen.segment_ready", "occurred_at": now_ms(),
            "device_id": "0" * 32, "source": "screen_memo", "summary": summary, "privacy_class": privacy_class,
            "schema_version": "2.0", "extras": {"package": package, **extras}}


class PhoneMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = make_app()
        self.dev = self.app.devices

    async def phone(self, name, flavor):
        key = Ed25519PrivateKey.generate()
        code = self.dev.pair_start(name)["code"]
        rec = await self.dev.redeem(code, key.public_key().public_bytes_raw().hex(), name)
        self.dev.registry.seen(rec["id"], flavor=flavor)
        return argparse.Namespace(device_id=rec["id"], name=name, foreground=None)

    def rows(self, sess):
        return self.app.db.query("SELECT kind, summary, data FROM device_events WHERE device_id=?", (sess.device_id,))

    async def test_personal_phone_drops_system_privacy_screens_without_storing_content(self):
        sess = await self.phone("日常机", "daily")
        self.assertEqual(self.dev.get(sess.device_id)["role"], "personal")
        private = [
            event("锁屏通知：工资到账 8000", package="com.android.systemui"),
            event("正在输入：hunter2", package="com.sohu.inputmethod.sogou"),
            event("输入支付密码", package="com.eg.android.AlipayGphone", secure=True),
            event("解锁界面", package="com.miui.home", keyguard=True),
            event("WLAN 密码页面", package="com.android.settings"),
            event("Bitwarden 密码库", package="com.x8bit.bitwarden"),
            event("体检报告结果", privacy_class="owner_sensitive"),
        ]
        for e in private:
            await self.dev.experience(sess, e)
        await self.dev.experience(sess, private[0])  # duplicate → still deduped by tombstone
        rows = self.rows(sess)
        self.assertEqual(len(rows), len(private))
        self.assertTrue(all(r["kind"] == "dropped" and r["summary"] == "" for r in rows))
        stored = " ".join(r["data"] for r in rows)
        for secret in ("8000", "hunter2", "支付", "WLAN", "Bitwarden", "体检", "com."):
            self.assertNotIn(secret, stored)
        stats = self.dev.ledger.drop_stats(sess.device_id)
        self.assertEqual(stats["system_private_app"], 4)
        self.assertEqual(stats["system_private_screen"], 2)
        self.assertEqual(stats["phone_marked_sensitive"], 1)

    async def test_personal_phone_keeps_everything_else_with_secrets_masked(self):
        sess = await self.phone("日常机", "daily")
        await self.dev.experience(sess, event())
        await self.dev.experience(sess, event("短信：您的验证码 384912，5 分钟内有效", package="com.android.mms"))
        await self.dev.experience(sess, event("付款卡 6222 0212 3456 7890 123", package="com.taobao.taobao"))
        rows = [r for r in self.rows(sess) if r["kind"] != "dropped"]
        text = " ".join(r["summary"] for r in rows)
        self.assertEqual(len(rows), 3)
        self.assertIn("抹香鲸", text)
        self.assertNotIn("384912", text)
        self.assertNotIn("3456 7890", text)

    async def test_user_excluded_apps_and_domains(self):
        sess = await self.phone("日常机", "daily")
        self.dev.registry.set_privacy(sess.device_id, {"excluded_apps": ["com.tencent.mm"],
                                                       "excluded_domains": ["*.example-private.com", "secret.cn"]})
        await self.dev.experience(sess, event("和老妈的微信聊天", package="com.tencent.mm"))
        await self.dev.experience(sess, event("浏览网页", package="com.android.chrome", url="https://a.example-private.com/x"))
        await self.dev.experience(sess, event("浏览网页", package="com.android.chrome", url="https://secret.cn/"))
        await self.dev.experience(sess, event("浏览新闻", package="com.android.chrome", url="https://news.example.com/"))
        kinds = [r["kind"] for r in self.rows(sess)]
        self.assertEqual(kinds.count("dropped"), 3)
        stats = self.dev.ledger.drop_stats(sess.device_id)
        self.assertEqual(stats, {"excluded_app": 1, "excluded_domain": 2})

    async def test_agent_phone_keeps_everything(self):
        sess = await self.phone("鲸鱼的手机", "root")
        self.assertEqual(self.dev.get(sess.device_id)["role"], "agent")
        for e in (event("系统界面", package="com.android.systemui"), event("验证码 384912", package="com.android.mms"),
                  event("设置页", package="com.android.settings", secure=True)):
            await self.dev.experience(sess, e)
        rows = self.rows(sess)
        self.assertEqual([r["kind"] for r in rows].count("dropped"), 0)
        self.assertIn("384912", " ".join(r["summary"] for r in rows))
        # roles can be changed explicitly
        self.dev.registry.set_role(sess.device_id, "personal")
        await self.dev.experience(sess, event("系统界面", package="com.android.systemui"))
        self.assertEqual([r["kind"] for r in self.rows(sess)].count("dropped"), 1)

    async def test_digest_turns_events_into_owner_memories(self):
        personal = await self.phone("日常机", "daily")
        agent = await self.phone("鲸鱼的手机", "root")
        await self.dev.experience(personal, event("在 B 站看了抹香鲸纪录片"))
        await self.dev.experience(personal, event("锁屏", package="com.android.systemui"))
        await self.dev.experience(agent, event("我在小红书搜索了深海探险", package="com.xingin.xhs"))
        made = await self.dev.digest.run()
        self.assertEqual(made["episodes"], 2)
        eps, _ = self.app.memory.store.list(kind="episode")
        by_subject = {e.subject: e for e in eps}
        self.assertIn("抹香鲸", by_subject["user"].content)
        self.assertNotIn("锁屏", by_subject["user"].content)
        self.assertIn("我在自己的手机上", by_subject["self"].content)
        self.assertTrue(all(e.scope == "owner" for e in eps))
        self.assertEqual(by_subject["user"].source["device"], "日常机")
        self.assertEqual((await self.dev.digest.run())["episodes"], 0)  # watermark: nothing new
        # phone memories never reach a group
        hits = await self.app.memory.retrieve("抹香鲸纪录片", scope="group:qq:1")
        self.assertEqual(hits, [])

    async def test_digest_facts_need_evidence(self):
        sess = await self.phone("日常机", "daily")
        await self.dev.experience(sess, event("12306：已购 11月28日 G7 杭州东→上海虹桥", package="com.MobileTicket"))
        scripted(self.app, Completion(text=json.dumps({
            "episodes": [{"content": "用户在 12306 买了去上海的高铁票", "importance": 0.5}],
            "facts": [{"kind": "fact", "content": "用户11月28日乘G7去上海", "quote": "11月28日 G7 杭州东→上海虹桥"},
                      {"kind": "fact", "content": "用户要去上海出差开会", "quote": "去上海出差开会"}]},
            ensure_ascii=False)), role="fast")
        self.app.models.overrides["fast"].name = "digest"
        made = await self.dev.digest.run()
        self.assertEqual((made["episodes"], made["facts"]), (1, 1))
        facts, _ = self.app.memory.store.list(kind="fact")
        self.assertEqual([f.content for f in facts], ["用户11月28日乘G7去上海"])

    async def test_phone_recall_is_owner_only(self):
        sess = await self.phone("日常机", "daily")
        await self.dev.experience(sess, event("在 B 站看了《深海之歌》"))
        await self.dev.experience(sess, event("深海 密码", package="com.x8bit.bitwarden"))
        found = self.dev.digest.search("深海")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["device"], "日常机")
        model = scripted(self.app, "嗯")
        await self.app.agent.run(RunRequest(text="主人手机上在看什么", peer="qq:9", scope="group:qq:1", channel="qq"))
        self.assertNotIn("phone__recall", {t["function"]["name"] for t in model.calls[0]["tools"]})
        model = scripted(self.app, "嗯")
        await self.app.agent.run(RunRequest(text="我刚才在手机上看的什么"))
        self.assertIn("phone__recall", {t["function"]["name"] for t in model.calls[0]["tools"]})

    def test_policy_helpers(self):
        self.assertTrue(privacy.is_system_private("com.google.android.inputmethod.latin"))
        self.assertTrue(privacy.is_system_private("com.iflytek.inputmethod"))
        self.assertFalse(privacy.is_system_private("tv.danmaku.bili"))
        self.assertTrue(privacy.domain_excluded("a.b.example.com", ["*.example.com"]))
        self.assertTrue(privacy.domain_excluded("example.com", ["*.example.com"]))
        self.assertFalse(privacy.domain_excluded("badexample.com", ["*.example.com"]))
        clean = privacy.clean_settings({"excluded_apps": ["com.ok", "bad app!"], "excluded_domains": ["https://X.com/a"]})
        self.assertEqual(clean["excluded_apps"], ["com.ok"])
        self.assertEqual(clean["excluded_domains"], ["x.com"])
        self.assertEqual(privacy.redact("身份证 33010219900101123X 已上传"), "身份证 [证件号已隐藏] 已上传")


if __name__ == "__main__":
    unittest.main()
