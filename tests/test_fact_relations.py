from whale_core.memory.fact_relations import (
    explicit_change_sentences, revalidation_reason,
)


def test_new_access_and_placement_rules_quarantine_old_composite():
    old = ("如果真提前到，你就先用 8614 进门。要是密码没开，就拿备用钥匙。"
           "冷藏药放冰箱上层，阳台罗勒浇一杯水。")
    assert revalidation_reason(
        old, "冷藏药不要再拿进屋，放楼下前台储物柜 L-12。") == "access_replaced"
    assert revalidation_reason(
        old, "旧密码和备用钥匙继续算死的，别进屋。") == "access_replaced"


def test_related_topic_alone_does_not_invalidate_independent_fact():
    assert revalidation_reason("我喜欢燕麦奶", "冷藏药改放前台") == ""
    assert revalidation_reason("冷藏药放冰箱", "冷藏药再买一份") == ""
    assert revalidation_reason("快递放门口", "别在群里说我收快递") == ""
    assert revalidation_reason(
        "冷藏药不要再拿进屋，放楼下前台储物柜 L-12。",
        "旧密码和备用钥匙继续算死的，别进屋。") == ""


def test_delivery_replacement_detects_changed_placement_without_access():
    assert revalidation_reason(
        "快递放门口", "以后快递改放楼下柜机") == "placement_replaced"
    assert revalidation_reason(
        "冷藏药放前台储物柜 L-12",
        "现在冷藏药改放楼下药房冰柜 R2。") == "placement_replaced"


def test_explicit_withdrawal_is_scoped_to_named_rule():
    assert revalidation_reason(
        "冷藏药放前台储物柜 L-12",
        "L-12 那个临时药件规则也撤掉。") == "rule_revoked"
    assert revalidation_reason("我喜欢燕麦奶", "L-12 那个规则撤掉。") == ""
    assert explicit_change_sentences(
        "林澈回去了，L-12 那个临时药件规则撤掉。现在冷藏药改放药房冰柜 R2。"
    ) == ("林澈回去了，L-12 那个临时药件规则撤掉。",
          "现在冷藏药改放药房冰柜 R2。")
