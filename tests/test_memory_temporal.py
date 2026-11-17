from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from whale_core.memory.curation import MemoryCurator
from whale_core.memory.facts import FactService
from whale_core.memory.store import InMemoryStore
from whale_core.memory.temporal import effective_interval


def test_explicit_relative_dates_use_source_local_day():
    observed = datetime(2026, 11, 15, 20, tzinfo=ZoneInfo("Asia/Shanghai"))
    start, end, phrase = effective_interval(
        "下周三开始使用新安排，到下周五为止", observed_at=observed)
    assert datetime.fromisoformat(start).astimezone(ZoneInfo("Asia/Shanghai")).isoformat() == \
        "2026-11-18T00:00:00+08:00"
    assert datetime.fromisoformat(end).astimezone(ZoneInfo("Asia/Shanghai")).isoformat() == \
        "2026-11-21T00:00:00+08:00"
    assert phrase == "下周三开始；到下周五为止"


def test_unspecified_time_is_not_invented():
    assert effective_interval("以后可以放柜机")[0:2] == ("", "")


def test_next_month_correction_keeps_old_fact_until_new_start():
    store = InMemoryStore()
    facts = FactService(store)
    old = facts.remember("包裹放玄关", source="chat", evidence_id="old",
                         space_id="owner").record
    result = facts.correct(old.key, "下个月起包裹改放楼下柜机",
                           source="chat", evidence_id="new")
    assert result.action == "update"
    current_old = store.get_record(old.key)
    assert current_old.content["status"] == "active"
    assert current_old.content["valid_to"] == result.record.content["valid_from"]
    assert result.record.content["time_expression"] == "下个月起"
    assert [hit.record.key for hit in MemoryCurator(store).recall(
        "包裹放哪里", allowed_spaces=("owner",))] == [old.key]
