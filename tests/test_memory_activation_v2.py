"""Activation acts only on candidates already approved by their source."""

from whale_core.memory.activation import MemoryCandidate, activate


def _candidate(key: str, summary: str, score: float = 0.5):
    return MemoryCandidate(key, summary, score, "owner_fact")


def test_multi_part_request_keeps_delivery_and_access_rule():
    query = "冷藏件到了，能进门吗，签收先联系谁？"
    rows = activate(query, (
        _candidate("noise", "今天猫粮在窗边", 0.99),
        _candidate("access", "旧门码不能再开门", 0.4),
        _candidate("delivery", "冷藏件放保温袋；签收先打电话", 0.3),
    ), limit=2)
    assert {row.candidate.key for row in rows} == {"access", "delivery"}
    assert next(row for row in rows if row.candidate.key == "access").decision == "CONSTRAIN"


def test_boundary_is_scoped_to_audience_and_topic():
    rule = _candidate("privacy", "不要在群里透露我的健康细节")
    assert activate("群里有人问我的身体怎么样", (rule,), is_group=True)[0].decision == "CONSTRAIN"
    assert activate("群里讨论门锁", (rule,), is_group=True) == ()
    assert activate("私聊里我的身体怎么样", (rule,)) == ()


def test_plain_fact_uses_retrieval_order_without_special_task():
    rows = activate("我的猫叫什么名字", (
        _candidate("weak", "我喜欢蓝色", 0.2),
        _candidate("name", "我养的猫叫年糕", 0.9),
    ), limit=1)
    assert [row.candidate.key for row in rows] == ["name"]


def test_duplicate_candidate_key_is_selected_once():
    fact = _candidate("name", "我养的猫叫年糕")
    assert len(activate("我的猫叫什么", (fact, fact))) == 1


def test_sharing_question_keeps_subject_fact_and_applicable_boundary():
    rows = activate("可以告诉群友我的猫叫什么吗", (
        _candidate("name", "我养的猫叫年糕"),
        _candidate("boundary", "不要在群里透露我的猫的名字"),
    ), is_group=True)
    assert {row.candidate.key for row in rows} == {"name", "boundary"}
    assert next(row for row in rows if row.candidate.key == "boundary").decision == "CONSTRAIN"
