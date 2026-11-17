"""Narrow, explainable relations between two evidenced fact statements.

The result is only a state-transition suggestion. The caller still verifies
speaker, space, chronology and current status before changing any record.
"""
from __future__ import annotations

import re


_ENTRY = ("进门", "进屋", "进去", "开门", "门码", "旧码", "密码", "钥匙", "代码")
_ENTRY_DENIAL = ("别进", "不要进", "不能进", "不再进", "先别进",
                 "别自己进", "不要自己进", "旧密码", "旧码", "作废", "无效", "停用")
_COLD_ITEM = ("冷藏", "冷链", "药件")
_PARCEL = ("包裹", "快递", "小盒子", "件到了")
_PLACEMENT = ("冰箱", "冰柜", "玄关", "前台", "柜机", "驿站", "门口", "保温袋")
_CHANGE = ("不要再", "别再", "不再", "改到", "改放", "改成", "以后", "现在", "作废", "无效")
_RULE_ID = re.compile(r"[A-Za-z]+-\d+")
_WITHDRAW = ("撤掉", "撤销", "取消", "作废", "不用了")


def explicit_change_sentences(text: str) -> tuple[str, ...]:
    """Return only self-contained, explicitly changed delivery rules.

    This is a small safety net for model omission. It does not infer an
    unstated destination or select ordinary facts from conversation text.
    """
    sentences = re.split(r"(?<=[。！？!?；;])", text)
    return tuple(s.strip() for s in sentences
                 if len(s.strip()) <= 300
                 and (any(w in s for w in _COLD_ITEM + _PARCEL)
                      and ("改放" in s or "改到" in s)
                      and any(w in s for w in _PLACEMENT)
                      or _RULE_ID.search(s) and "规则" in s
                      and any(w in s for w in _WITHDRAW)))


def _denies_entry(text: str) -> bool:
    return (any(word in text for word in _ENTRY_DENIAL)
            or ("进屋" in text and any(word in text for word in
                                     ("不要", "别", "不能", "不再"))))


def revalidation_reason(old: str, new: str) -> str:
    """Quarantine an older composite instruction when a new rule replaces it.

    A shared topic alone is insufficient: the new statement must explicitly
    change the action or placement. Independent details remain untouched.
    """
    old, new = str(old or ""), str(new or "")
    if not old or not new or old == new:
        return ""
    old_ids = set(_RULE_ID.findall(old))
    if (old_ids and old_ids.intersection(_RULE_ID.findall(new))
            and any(word in new for word in _WITHDRAW)):
        return "rule_revoked"
    if (any(word in old for word in _ENTRY)
            and any(word in new for word in _ENTRY)
            and _denies_entry(new) and not _denies_entry(old)):
        return "access_replaced"
    same_object = (
        any(word in old for word in _COLD_ITEM)
        and any(word in new for word in _COLD_ITEM)
        or any(word in old for word in _PARCEL)
        and any(word in new for word in _PARCEL)
    )
    old_places = {word for word in _PLACEMENT if word in old}
    new_places = {word for word in _PLACEMENT if word in new}
    if (same_object and old_places and new_places
            and old_places.isdisjoint(new_places)
            and any(word in new for word in _CHANGE)):
        return "placement_replaced"
    return ""
