import asyncio
import tempfile
import unittest
from datetime import datetime

from whale.kernel.config import Config
from whale.kernel.db import Database
from whale.kernel.errors import WhaleError
from whale.kernel.events import EventBus, EventLog
from whale.kernel.ids import new_id, ulid, ulid_ms
from whale.tasks.scheduler import cron_next


class KernelTests(unittest.IsolatedAsyncioTestCase):
    def test_ulid_monotonic_and_prefixed(self):
        ids = [ulid() for _ in range(2000)]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(set(ids)), len(ids))
        rid = new_id("run")
        self.assertTrue(rid.startswith("run_") and len(rid) == 30)
        self.assertGreater(ulid_ms(rid), 1_700_000_000_000)

    async def test_event_bus_handlers_streams_and_log(self):
        db = Database()
        db.migrate()
        bus = EventBus(EventLog(db))
        seen = []
        bus.on("memory.*", lambda e: seen.append(e.topic))
        sub = bus.stream(["memory.created"])
        await bus.emit("memory.created", {"x": 1})
        await bus.emit("run.started", {})
        await bus.emit("run.delta", {"d": "hi"})  # ephemeral: not logged
        self.assertEqual(seen, ["memory.created"])
        ev = await asyncio.wait_for(sub.get(), 1)
        self.assertEqual(ev.data, {"x": 1})
        topics = [e["topic"] for e in bus._log.query("*")]
        self.assertIn("run.started", topics)
        self.assertNotIn("run.delta", topics)

    async def test_failing_handler_does_not_break_emit(self):
        bus = EventBus()

        def boom(e):
            raise RuntimeError("x")

        bus.on("*", boom)
        ev = await bus.emit("a.b", {})
        self.assertEqual(ev.topic, "a.b")

    def test_kv_and_transactions(self):
        db = Database()
        db.migrate()
        db.kv_set("ns", "k", {"a": [1, 2]})
        self.assertEqual(db.kv_get("ns", "k"), {"a": [1, 2]})
        with self.assertRaises(ZeroDivisionError):
            with db.transaction() as conn:
                conn.execute("INSERT INTO kv VALUES ('ns','t','1',0)")
                1 / 0
        self.assertIsNone(db.kv_get("ns", "t"))

    def test_config_persists_validates_and_hides_secrets(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(d)
            token = cfg.api_token
            cfg.update({"agent": {"name": "小鲸"}})
            cfg.set_secret("provider.ds", "sk-123")
            with self.assertRaises(WhaleError):
                cfg.update({"agent": {"max_steps": "many"}})
            again = Config(d)
            self.assertEqual(again.value.agent.name, "小鲸")
            self.assertEqual(again.api_token, token)
            self.assertEqual(again.secret("provider.ds"), "sk-123")
            public = again.public()
            self.assertEqual(public["secrets"], {"provider.ds": True})
            self.assertNotIn("sk-123", str(public))

    def test_cron(self):
        base = datetime(2026, 11, 23, 7, 59)  # Monday
        self.assertEqual(cron_next("0 8 * * *", base), datetime(2026, 11, 23, 8, 0))
        self.assertEqual(cron_next("30 9 * * 6,0", base), datetime(2026, 11, 28, 9, 30))
        self.assertEqual(cron_next("*/15 * * * *", base), datetime(2026, 11, 23, 8, 0))
        with self.assertRaises(ValueError):
            cron_next("61 * * * *", base)


if __name__ == "__main__":
    unittest.main()
