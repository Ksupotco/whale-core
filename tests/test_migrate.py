"""迁移器假库往返测试（DEVELOPMENT §3：全本地、无网络、无现网）。

造一个 v2 形状的假 qq_workflow.sqlite3 → 跑 migrate → 断言 v3 whale.db 内容；
再跑第二次验证幂等（映射表去重，不产生重复行）。
"""

import hashlib
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

MIGRATE = Path(__file__).resolve().parent.parent / "scripts" / "migrate_v2_to_v1.py"
spec = importlib.util.spec_from_file_location("migrate_v2_to_v1", MIGRATE)
mig = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mig)


def make_v2_db(path: Path) -> dict:
    key = Ed25519PrivateKey.generate()
    pub = key.public_key().public_bytes_raw().hex()
    did = hashlib.sha256(bytes.fromhex(pub)).digest()[:16].hex()
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT)")
    db.execute("CREATE TABLE records (key TEXT PRIMARY KEY, kind TEXT NOT NULL, content TEXT NOT NULL,"
               " record_id TEXT NOT NULL, created_at TEXT NOT NULL, tags TEXT NOT NULL,"
               " space_id TEXT NOT NULL DEFAULT '')")
    records = [
        ("k1", "preference", "主人喜欢清淡饮食", "rec1", "2026-11-01T10:00:00+00:00", '["饮食"]', "owner-working"),
        ("k2", "episode", "群里聊了周末团建", "rec2", "2026-11-02T10:00:00+00:00", "[]", "g-test-123456"),
        ("k3", "episode", "对方说他周三到", "rec3", "2026-11-03T10:00:00+00:00", "", "member-abcdef"),
        ("k4", "episode", "无主之地的一条", "rec4", "2026-11-04T10:00:00+00:00", "[]", "weird-space"),
        ("k5", "episode", "", "rec5", "2026-11-05T10:00:00+00:00", "[]", "owner-working"),
    ]
    db.executemany("INSERT INTO records VALUES (?,?,?,?,?,?,?)", records)
    kv = {
        "persona:card:wenrou": {"card_id": "wenrou", "name": "温柔鲸鱼", "title": "温柔版", "tone": "温和",
                                "style_rules": ["短句"], "extra_taboos": ["不熬夜"]},
        "persona:card:enxi": {"card_id": "enxi", "name": "恩汐"},
        "persona:active_card": {"card_id": "wenrou"},
        f"device:{did}": {"device_id": did, "pubkey_hex": pub, "label": "Nova",
                          "scopes": ["device.tap", "device.bogus"], "status": "active",
                          "role": "owner-phone", "created_at": "2026-11-02T00:00:00+00:00",
                          "updated_at": "2026-11-03T00:00:00+00:00"},
        "device:" + "f" * 32: {"device_id": "f" * 32, "pubkey_hex": pub, "label": "坏记录",
                               "scopes": [], "status": "active", "role": "root-phone",
                               "created_at": "2026-11-02T00:00:00+00:00"},
        "task:t1": {"task_id": "t1", "goal": "提醒主人周三交报告", "status": "succeeded",
                    "at": "2026-11-10T08:00:00+00:00", "res": "已提醒"},
        "task:t2": {"task_id": "t2", "goal": "一个失败的任务", "status": "failed",
                    "at": "2026-11-10T09:00:00+00:00"},
        "task:t3": {"task_id": "t3", "goal": "审批被拒的任务", "status": "denied",
                    "at": "2026-11-10T10:00:00+00:00"},
        "uidmap:uid:u_abc": "12345678",
        "identity:channel:qq:1000000001": {"owner_id": "owner", "channel": "qq",
                                           "channel_user_id": "1000000001"},
        "bind:persona:qq:qq_group:group:123456": {"persona_id": "wenrou"},
        "bind:persona:qq:qq_private:private:u_abc": {"persona_id": "enxi"},
        "bind:persona:qq:qq_private:private:u_nomap": {"persona_id": "enxi"},
        "chat:history:glasses:h1": [{"role": "user", "content": "你好"},
                                    {"role": "assistant", "content": "在的"},
                                    {"role": "tool", "content": "跳过我"}],
    }
    for k, v in kv.items():
        db.execute("INSERT INTO kv VALUES (?,?)", (k, json.dumps(v, ensure_ascii=False)))
    db.commit()
    db.close()
    return {"did": did, "pub": pub}


class MigrateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = Path(self.tmp.name) / "v2.db"
        self.fix = make_v2_db(self.src)
        self.data = Path(self.tmp.name) / "v1data"

    def tearDown(self):
        self.tmp.cleanup()

    def run_migrate(self):
        return mig.Migrator(self.src, self.data).run()

    def test_round_trip_and_idempotency(self):
        report = self.run_migrate()
        db = sqlite3.connect(self.data / "whale.db")
        db.row_factory = sqlite3.Row

        mems = db.execute("SELECT * FROM memories").fetchall()
        self.assertEqual(len(mems), 4)                      # rec5 空内容跳过
        self.assertTrue(all(m["status"] == "candidate" for m in mems))   # 无 quote 不直进 active
        self.assertTrue(all("legacy-import" in json.loads(m["tags"]) for m in mems))
        scopes = {m["content"]: m["scope"] for m in mems}
        self.assertEqual(scopes["主人喜欢清淡饮食"], "owner")
        self.assertEqual(scopes["群里聊了周末团建"], "group:qq:123456")
        self.assertEqual(scopes["对方说他周三到"], "member:abcdef")
        self.assertEqual(scopes["无主之地的一条"], "legacy:weird-space")   # 未知空间隔离封存
        pref = db.execute("SELECT * FROM memories WHERE kind='preference'").fetchone()
        from datetime import datetime, timezone
        expect_ms = int(datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc).timestamp() * 1000)
        self.assertEqual(pref["created_at"], expect_ms)                  # 2026-11-01T10:00Z 保留
        self.assertEqual(pref["last_access_at"], pref["created_at"])     # strength 由公式重算
        self.assertEqual(json.loads(pref["source"])["record_id"], "rec1")

        personas = {r["id"]: json.loads(r["card"]) for r in db.execute("SELECT * FROM personas")}
        self.assertEqual(set(personas), {"wenrou", "enxi"})
        self.assertEqual(personas["wenrou"]["style"], "温和；短句")
        self.assertEqual(personas["wenrou"]["boundaries"], "不熬夜")
        cfg = json.loads((self.data / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["agent"]["persona_id"], "wenrou")

        devs = db.execute("SELECT * FROM devices").fetchall()
        self.assertEqual(len(devs), 1)                                   # 坏记录（id 与公钥不符）跳过
        self.assertEqual(devs[0]["id"], self.fix["did"])
        self.assertEqual(devs[0]["name"], "Nova")
        self.assertEqual(json.loads(devs[0]["scopes"]), ["device.tap"])  # 未知 scope 被剔除
        self.assertEqual(devs[0]["role"], "personal")
        self.assertEqual(devs[0]["paired_via"], "legacy-migrate")

        tasks = {r["goal"]: r["status"] for r in db.execute("SELECT * FROM tasks")}
        self.assertEqual(tasks, {"提醒主人周三交报告": "completed", "一个失败的任务": "failed",
                                 "审批被拒的任务": "cancelled"})

        convs = {r["id"]: dict(r) for r in db.execute("SELECT * FROM conversations")}
        self.assertIn("qq:group:123456", convs)
        self.assertEqual(convs["qq:group:123456"]["persona_id"], "wenrou")
        self.assertIn("qq:private:12345678", convs)                      # uid→uin 经 uidmap
        self.assertEqual(convs["qq:private:12345678"]["peer"], "qq:12345678")
        glasses = [c for c in convs.values() if c["channel"] == "glasses"][0]
        msgs = db.execute("SELECT role, content FROM messages WHERE conversation_id=? ORDER BY ts",
                          (glasses["id"],)).fetchall()
        self.assertEqual([tuple(m) for m in msgs], [("user", "你好"), ("assistant", "在的")])
        self.assertEqual(glasses["turns"], 1)

        maps = db.execute("SELECT COUNT(*) FROM kv WHERE ns='migrate'").fetchone()[0]
        self.assertGreaterEqual(maps, 10)
        self.assertTrue(any(s["where"].endswith("u_nomap") for s in report["skipped"]))
        self.assertTrue(any("device_id mismatch" in s["why"] for s in report["skipped"]))
        db.close()

        again = self.run_migrate()                                       # 幂等：第二遍零新增
        self.assertEqual(again["sections"]["memories"]["migrated"], 0)
        db = sqlite3.connect(self.data / "whale.db")
        self.assertEqual(db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 4)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0], 3)
        db.close()

    def test_dry_run_writes_nothing(self):
        report = mig.Migrator(self.src, self.data, dry_run=True).run()
        self.assertFalse((self.data / "whale.db").exists())   # dry-run 只走临时目录
        self.assertEqual(report["sections"]["memories"]["migrated"], 4)  # 但报告照常给出


if __name__ == "__main__":
    unittest.main()
