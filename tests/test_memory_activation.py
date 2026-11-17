from dataclasses import replace
from datetime import datetime, timedelta, timezone

from whale_core.memory.activation import activate
from whale_core.memory.context import build_memory_context
from whale_core.memory.curation import MemoryCurator, RecallHit, _bm25_scores
from whale_core.memory.facts import FactService
from whale_core.memory.store import InMemoryStore
from whale_core.memory.rerank import rerank
from whale_core.memory.task_plan import evidence_needs, plan_query
from whale_core.core.prompt_composer import compose_companion_system


def test_fact_relation_and_time_survive_correction():
    store = InMemoryStore()
    facts = FactService(store)
    old = facts.remember("冷藏件放玄关", source="chat", evidence_id="m1",
                         space_id="owner").record
    new = facts.correct(old.key, "冷藏件改放门口保温袋；如需签收先打电话",
                        source="chat", evidence_id="m2").record
    assert old.content["relations"][0]["entity"] == "delivery"
    assert {item["attribute"] for item in new.content["relations"]} == {
        "placement", "contact"}
    assert new.content["relations"][1]["condition"] == "签收"
    assert store.get_record(old.key).content["valid_to"]
    assert store.get_record(new.key).content["valid_from"]


def test_bm25_prefers_fact_covering_both_query_attributes():
    store = InMemoryStore()
    facts = FactService(store)
    both = facts.remember("冷藏件签收先打电话", source="chat",
                          evidence_id="both", space_id="owner").record
    cold = facts.remember("冷藏件放保温袋", source="chat",
                          evidence_id="cold", space_id="owner").record
    sign = facts.remember("签收文件", source="chat",
                          evidence_id="sign", space_id="owner").record
    scores = _bm25_scores("冷藏件签收", (both, cold, sign))
    assert scores[both.key] > scores[cold.key]
    assert scores[both.key] > scores[sign.key]


def test_activation_separates_query_topic_from_retrieval_score():
    store = InMemoryStore()
    facts = FactService(store)
    access = facts.remember("旧门码不能再开门", source="chat", evidence_id="a",
                            space_id="owner").record
    delivery = facts.remember("冷藏件放保温袋；如需签收先打电话", source="chat",
                              evidence_id="b", space_id="owner").record
    unrelated = facts.remember("快递员说门口的花很好看", source="chat",
                               evidence_id="c", space_id="owner").record
    hits = (RecallHit(unrelated, 0.9, ()), RecallHit(access, 0.6, ()),
            RecallHit(delivery, 0.5, ()))
    query = "冷藏件到了，能开门进屋吗，签收要联系谁？"
    ranked = rerank(query, hits)
    assert len(ranked) == 3  # ranking itself does not discard candidates
    activated = activate(query, ranked)
    assert [row.hit.record.key for row in activated] == [delivery.key]
    assert activated[0].role == "conditional"
    context = build_memory_context(
        curator=MemoryCurator(store), query=query, recent_episodes=(),
        conversation_id="private", allowed_spaces=("owner",))
    assert any("旧门码" in item for item in context.access)


def test_context_excludes_a_different_structured_topic():
    store = InMemoryStore()
    facts = FactService(store)
    facts.remember("A 的旧门码不能再开门", source="chat", evidence_id="a",
                   space_id="owner")
    facts.remember("B 的快递可以留在门口", source="chat", evidence_id="b",
                   space_id="owner")
    context = build_memory_context(
        curator=MemoryCurator(store), query="旧门码还能开门吗？",
        recent_episodes=(), conversation_id="", allowed_spaces=("owner",))
    assert any("旧门码" in item for item in context.access)
    assert all("快递" not in item for item in context.facts)
    assert context.access[0].startswith("[当前访问权限]")


def test_chilled_delivery_keeps_current_contact_rule_and_rejects_old_access():
    store = InMemoryStore()
    facts = FactService(store)
    old = facts.remember("B 可以用旧门码开门进屋", source="chat",
                         evidence_id="old", space_id="owner").record
    facts.revoke(old.key, source="chat", evidence_id="revoke")
    facts.remember("A 已更换门锁锁芯和备用钥匙位，旧门码不再使用",
                   source="chat", evidence_id="lock", space_id="owner")
    facts.remember("冷藏件应放在门口银色保温袋里；如需签收先给 A 打电话",
                   source="chat", evidence_id="current", space_id="owner")
    for index in range(8):
        facts.remember(f"A 的猫药和旧门附近物品编号{index}", source="chat",
                       evidence_id=f"noise:{index}", space_id="owner")
    context = build_memory_context(
        curator=MemoryCurator(store),
        query="冷藏件又提前到了，B 想按以前那样自己开门进屋。该激活什么？",
        recent_episodes=(), conversation_id="", allowed_spaces=("owner",))
    assert any("保温袋" in item and "打电话" in item for item in context.facts)
    assert all("可以用旧门码" not in item for item in context.facts)


def test_future_fact_does_not_enter_retrieval_or_activation():
    store = InMemoryStore()
    record = FactService(store).remember(
        "明天起可以用新门码开门", source="chat", evidence_id="later",
        space_id="owner").record
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    store.put_record(replace(record, content={**record.content,
                                             "valid_from": future}))
    assert MemoryCurator(store).recall("新门码开门", allowed_spaces=("owner",)) == ()
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    store.put_record(replace(record, content={**record.content,
                                             "valid_to": past}))
    assert MemoryCurator(store).recall("新门码开门", allowed_spaces=("owner",)) == ()


def test_group_health_boundary_has_its_own_slot_and_hard_space_filter():
    store = InMemoryStore()
    facts = FactService(store)
    boundary = facts.remember(
        "A不希望B在群里展开A晕或过载的事，也不希望B替A解释",
        source="chat", evidence_id="boundary", space_id="group:one",
        subject_id="A").record
    facts.remember("A不希望B在群里谈A的饭局座位", source="chat",
                   evidence_id="food", space_id="group:one", subject_id="A")
    facts.remember("A不希望B在群里公开A的病情", source="chat",
                   evidence_id="other_group", space_id="group:two", subject_id="A")
    for index in range(9):
        facts.remember(f"A那天胃不舒服，补充事实{index}", source="chat",
                       evidence_id=f"noise:{index}", space_id="group:one",
                       subject_id="A")
    assert boundary.content["memory_role"] == "boundary"
    context = build_memory_context(
        curator=MemoryCurator(store),
        query="群聊里有人问A为什么突然出去，是不是不舒服？",
        recent_episodes=(), conversation_id="group:one",
        allowed_spaces=("group:one",))
    assert len(context.boundaries) == 1
    assert "晕或过载" in context.boundaries[0]
    assert all("病情" not in item and "饭局" not in item
               for item in context.as_memories())


def test_boundary_requires_matching_audience_and_active_version():
    store = InMemoryStore()
    facts = FactService(store)
    old = facts.remember("A不希望B在群里汇报栗子的病情", source="chat",
                         evidence_id="old", space_id="group:one",
                         subject_id="A").record
    new = facts.correct(old.key, "A不希望B在群里透露栗子的复查结果",
                        source="chat", evidence_id="new").record
    group = build_memory_context(
        curator=MemoryCurator(store), query="群里有人问栗子最近怎么了，B想回复",
        recent_episodes=(), conversation_id="group:one",
        allowed_spaces=("group:one",))
    private = build_memory_context(
        curator=MemoryCurator(store), query="栗子最近怎么了？",
        recent_episodes=(), conversation_id="private",
        allowed_spaces=("group:one",))
    unauthorized = build_memory_context(
        curator=MemoryCurator(store), query="群里有人问栗子最近怎么了",
        recent_episodes=(), conversation_id="group:two",
        allowed_spaces=("group:two",))
    assert new.content["memory_role"] == "boundary"
    assert len(group.boundaries) == 1 and "复查结果" in group.boundaries[0]
    assert not private.boundaries and not unauthorized.boundaries
    assert all("汇报栗子" not in item for item in group.as_memories())


def test_boundary_does_not_follow_the_same_person_into_unrelated_action():
    store = InMemoryStore()
    facts = FactService(store)
    facts.remember("A不希望B把这件事讲得很严重，也不希望在别人面前替A科普",
                   source="chat", evidence_id="social", space_id="owner",
                   subject_id="A")
    facts.remember("A表示群里不要问阿青家里的原因", source="chat",
                   evidence_id="family", space_id="owner", subject_id="A")
    curator = MemoryCurator(store)
    access = build_memory_context(
        curator=curator, query="楼上说A家门口有水声，B想试旧码开门",
        recent_episodes=(), conversation_id="private", allowed_spaces=("owner",))
    food = build_memory_context(
        curator=curator, query="群里要给A点火锅菜单，要避什么？",
        recent_episodes=(), conversation_id="group:one", allowed_spaces=("owner",))
    assert not access.boundaries
    assert not food.boundaries


def test_group_question_about_anyone_checks_relevant_personal_boundary():
    store = InMemoryStore()
    FactService(store).remember(
        "A的边界是不要在群里聊A的身体细节", source="chat",
        evidence_id="health", space_id="group:one", subject_id="A")
    context = build_memory_context(
        curator=MemoryCurator(store),
        query="群里C问这次有没有人不能吃芒果，B要回复",
        recent_episodes=(), conversation_id="group:one",
        allowed_spaces=("group:one",))
    assert len(context.boundaries) == 1
    assert "身体细节" in context.boundaries[0]


def test_mango_answer_context_uses_state_and_keeps_history_on_request_only():
    store = InMemoryStore()
    facts = FactService(store)
    old = facts.remember("[A] A上次吃芒果后喉咙肿，需要避开芒果甜点",
                         source="chat", evidence_id="old", space_id="private",
                         subject_id="A").record
    facts.remember("[B] B会私下提醒A避开芒果甜点", source="chat",
                   evidence_id="reminder", space_id="private", subject_id="B")
    facts.remember("[A] A不要在群里聊身体细节", source="chat",
                   evidence_id="boundary", space_id="private", subject_id="A")
    facts.remember("[A] A偏好燕麦奶", source="chat", evidence_id="milk",
                   space_id="private", subject_id="A")
    facts.remember("[B] B表示私聊里只会在A主动提的时候接健康话题",
                   source="chat", evidence_id="private_rule",
                   space_id="private", subject_id="B")
    facts.revoke(old.key, source="chat", evidence_id="correction")
    normal = build_memory_context(
        curator=MemoryCurator(store), query="群里C问有没有人不能吃芒果",
        recent_episodes=(), conversation_id="group:one",
        allowed_spaces=("private",))
    assert any("身体细节" in item for item in normal.boundaries)
    assert any("已撤销" in item for item in normal.statuses)
    assert any("待重新核实" in item for item in normal.statuses)
    assert not normal.history
    assert all("上次吃芒果后" not in item for item in normal.as_memories())
    assert all("私下提醒A避开" not in item for item in normal.as_memories())
    assert all("私聊里只会" not in item for item in normal.as_memories())
    historical = build_memory_context(
        curator=MemoryCurator(store), query="以前为什么认为A需要避开芒果？",
        recent_episodes=(), conversation_id="private",
        allowed_spaces=("private",))
    assert any("上次吃芒果后" in item and "历史版本" in item
               for item in historical.history)


def test_access_permission_has_reserved_slot_outside_recall_top_k():
    store = InMemoryStore()
    facts = FactService(store)
    facts.remember("A的旧门码不能开门", source="chat", evidence_id="access",
                   space_id="owner")
    for index in range(12):
        facts.remember(f"门口快递物品编号{index}", source="chat",
                       evidence_id=f"noise:{index}", space_id="owner")
    context = build_memory_context(
        curator=MemoryCurator(store), query="B还能用旧门码开门吗？",
        recent_episodes=(), conversation_id="private", allowed_spaces=("owner",))
    assert any("旧门码不能开门" in item for item in context.access)
    unrelated = build_memory_context(
        curator=MemoryCurator(store), query="今晚喝什么茶？",
        recent_episodes=(), conversation_id="private", allowed_spaces=("owner",))
    assert not unrelated.access


def test_explicit_scenario_time_takes_priority_over_wall_clock_in_prompt():
    context = build_memory_context(
        curator=None, query="B 早上准备递咖啡，应该记起什么？",
        recent_episodes=(), conversation_id="private", allowed_spaces=())
    prompt = compose_companion_system(
        memory_context=context,
        now=datetime(2026, 11, 16, 2, 0, tzinfo=timezone.utc))
    assert context.scenario_time == "早上"
    assert "本轮所述情境时间：早上" in prompt
    assert "只有询问真实现在时才使用当前时钟" in prompt


def test_dropoff_and_pet_subtasks_both_have_evidence():
    store = InMemoryStore()
    facts = FactService(store)
    facts.remember("A要求B不要把东西在门口放太久；若顺路可放到一楼书吧取物架并拍照",
                   source="chat", evidence_id="dropoff", space_id="owner")
    facts.remember("A表示猫粮由自己来，除非A明确说，B不用进门也不用看猫粮",
                   source="chat", evidence_id="pet", space_id="owner")
    facts.remember("栗子吃多了罐头会吐，罐头不要开",
                   source="chat", evidence_id="can", space_id="owner")
    context = build_memory_context(
        curator=MemoryCurator(store),
        query="B 做多了汤，想给 A 放过去，并顺手看栗子的猫粮。",
        recent_episodes=(), conversation_id="private", allowed_spaces=("owner",))
    assert any("书吧取物架" in item for item in context.as_memories())
    assert any("猫粮" in item for item in context.constraints)
    assert all("罐头" not in item for item in context.constraints)


def test_explicit_pet_help_keeps_task_facts_and_access_rule_separate():
    store = InMemoryStore()
    facts = FactService(store)
    facts.remember("A养了乌冬；乌冬吃蓝色收纳箱里的白鱼罐头，每次半罐",
                   source="chat", evidence_id="food", space_id="owner")
    facts.remember("旧门码不能再用；要进门须等A给一次性码",
                   source="chat", evidence_id="access", space_id="owner")
    context = build_memory_context(
        curator=MemoryCurator(store),
        query="A明确请求B帮忙喂乌冬，但没有重新给入口码。",
        recent_episodes=(), conversation_id="private", allowed_spaces=("owner",))
    assert any("白鱼罐头" in item for item in context.as_memories())
    assert any("一次性码" in item for item in context.access)
    prompt = compose_companion_system(memory_context=context)
    assert "任务内容与执行权限分别判断" in prompt


def test_task_plan_keeps_invitation_separate_from_menu():
    plan = plan_query("B 回复朋友是否邀请 A，并安排座位和说法")
    assert "social_invite" in plan.needs
    assert not evidence_needs("A 最近少吃辣，别默认点辣汤粉", plan)
    assert "social_invite" in evidence_needs("A 想参加小范围饭局，不想被追问近况", plan)


def test_group_drink_context_removes_health_reason():
    store = InMemoryStore()
    facts = FactService(store)
    facts.remember("A偏好燕麦奶，普通牛奶喝多了胃不舒服", source="chat",
                   evidence_id="drink", space_id="owner")
    context = build_memory_context(
        curator=MemoryCurator(store), query="群聊里讨论饮料，A 喝什么？",
        recent_episodes=(), conversation_id="group:one",
        allowed_spaces=("owner",))
    assert any("偏好燕麦奶" in item for item in context.as_memories())
    assert all("胃不舒服" not in item for item in context.as_memories())


def test_group_absence_does_not_activate_naming_boundary():
    store = InMemoryStore()
    facts = FactService(store)
    facts.remember("A在群里不要被叫小柚，可以叫阿青", source="chat",
                   evidence_id="name", space_id="owner", subject_id="A")
    facts.remember("别人问起A为什么临时走开时，只说私事或日程冲突",
                   source="chat", evidence_id="absence", space_id="owner",
                   subject_id="A")
    context = build_memory_context(
        curator=MemoryCurator(store), query="群聊里有人问 A 为什么突然少参加活动",
        recent_episodes=(), conversation_id="group:one",
        allowed_spaces=("owner",))
    assert all("小柚" not in item for item in context.as_memories())
    assert any("私事" in item for item in context.as_memories())


def test_frontdesk_question_applies_no_proactive_arrangement_boundary():
    store = InMemoryStore()
    facts = FactService(store)
    facts.remember("A不希望B替自己对外安排，也不希望B主动对外说A家有考生",
                   source="chat", evidence_id="proactive", space_id="owner",
                   subject_id="A")
    facts.remember("A要求B不要向邻居和前台提及林栀的名字和学校",
                   source="chat", evidence_id="name", space_id="owner",
                   subject_id="A")
    context = build_memory_context(
        curator=MemoryCurator(store),
        query="B 想主动帮 A 去前台打招呼、解释家里有考生，还顺手安排物资",
        recent_episodes=(), conversation_id="private", allowed_spaces=("owner",))
    assert any("不希望B替自己对外安排" in item for item in context.boundaries)
    assert any("不要向邻居和前台提及" in item for item in context.as_memories())
