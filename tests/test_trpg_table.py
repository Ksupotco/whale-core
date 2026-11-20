from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest

from examples.qq_onebot_local import _OneLineOwnerPort, _process_owner, _send_table
from whale_core.life.chm_rules import chapter_sections
from whale_core.life.channels import OwnerEvent
from whale_core.life.dicecho import _module
from whale_core.life.initiative import ProactiveOutbox, ProactivePrefs
from whale_core.life.rulebooks import import_book
from whale_core.life.trpg import TableTools, coc_check, roll
from whale_core.memory.store import InMemoryStore


def module(ident="1", title="归途"):
    return _module({"_id": ident * 24, "title": title, "moduleRule": "龙与地下城",
                    "playerNumber": [2, 4], "author": {"nickName": "作者"},
                    "coverUrl": "https://file.dicecho.com/example.png",
                    "description": "玩家导入：一支队伍踏上归途\n幕后真相：不该发进群"})


def test_roll_keeps_high_low_and_modifiers():
    for mode, expected in (("h", 19), ("l", 5)):
        values = iter([1, 15])
        assert roll(f"2d20k{mode}1+3", lambda _: next(values)).endswith(f"= {expected}")
    assert roll("2d6-d4+2", lambda _: 0).endswith("= 3")
    assert "骰式示例" in roll("d6*4")
    assert "数量" in roll("4d6kh5")


@pytest.mark.parametrize("skill,value,grade", [
    (60, 1, "大成功"), (60, 12, "极难成功"), (60, 30, "困难成功"), (60, 60, "成功"),
    (49, 96, "大失败"), (50, 96, "失败"), (100, 100, "大失败")])
def test_coc_check(skill, value, grade):
    assert coc_check(skill, value) == grade


def test_catalogue_to_onebot_card(tmp_path):
    lookup = Mock(return_value=[module()])
    table = TableTools(tmp_path, lookup)
    assert table.command("群里有人玩 DND 吗？", "17") is None
    assert table.command(".rubbish", "17") is None
    assert "60" in table.command(".ra 侦查 60", "17").text
    assert "例如" in table.command(".模组", "17").text
    assert table.command(".推荐", "17").cover
    reply = table.command(".模组 D&D 归途", "17")
    lookup.assert_called_with("归途", "龙与地下城")
    assert "一支队伍" in reply.text and "幕后真相" not in reply.text
    assert "2–4 人" in reply.text and module().url in reply.text
    transport = Mock()
    transport.call.return_value = {"retcode": 0}
    assert _send_table(transport, "17", reply)
    transport.call.assert_called_once_with("send_group_msg", {"group_id": 17, "message": reply.segments()})
    assert reply.segments()[1]["type"] == "image"
    lookup.side_effect = OSError()
    assert "连接失败" in table.command(".推荐 COC", "17").text


def test_group_rulebook_replacement_and_citations(tmp_path):
    book = tmp_path / "团规.md"
    table = TableTools(tmp_path)
    book.write_text("第一段\n\n借机攻击的团内约定 A", encoding="utf-8")
    import_book(table.rules, "17", "DND", "2024", book)
    answer = table.command(".规则 DND 借机攻击", "17").text
    assert "《团规》 · 2024 · 第 2 段" in answer and "约定 A" in answer
    assert "还没有导入" in table.command(".规则 DND 借机攻击", "18").text
    assert "还没有导入" in table.command(".规则 COC 借机攻击", "17").text
    book.write_text("新版的团规", encoding="utf-8")
    import_book(table.rules, "17", "DND", "2024", book)
    assert "没有找到" in table.command(".规则 DND 借机攻击", "17").text


def test_schedule_optin_restart_group_scope_and_dedup(tmp_path):
    lookup = Mock(return_value=[module()])
    table = TableTools(tmp_path, lookup)
    now = datetime(2026, 11, 20, 20, 1)
    send = Mock(return_value=True)
    table.tick(now, {"17"}, send)
    assert "主人" in table.command(".周荐 周五 20:00 DND", "17").text
    lookup.assert_not_called()
    table.command(".周荐 周五 20:00 DND", "17", manage=True)
    table.tick(now, {"18"}, send)
    table.tick(now - timedelta(minutes=2), {"17"}, send)
    lookup.assert_not_called()
    table.tick(now, {"17"}, send)
    assert send.call_args.args[0] == "17"
    TableTools(tmp_path, lookup).tick(now, {"17"}, send)
    send.assert_called_once()
    table.tick(now + timedelta(weeks=1), {"17"}, send)
    send.assert_called_once()  # the only available module was already used
    lookup.return_value.append(module("2"))
    table.command(".周荐 关闭", "17", manage=True)
    table.tick(now + timedelta(weeks=2), {"17"}, send)
    send.assert_called_once()


def test_owner_can_preview_without_posting_to_group(tmp_path):
    table = TableTools(tmp_path, Mock(return_value=[module()]))
    workflow = Mock(outbox=ProactiveOutbox(InMemoryStore()))
    port = Mock()
    port.send.return_value = True
    event = OwnerEvent("owner", "qq", "private:1", "a", ".推荐 DND")
    prefs = ProactivePrefs(enabled=True, allowed_channels=frozenset({"direct_message"}),
                           auto_send=True, passive_reply_in_quiet=True)
    assert _process_owner(event, workflow, port, prefs, table=table, group="17") == "accepted"
    assert port.send.call_args.args[0].target_conversation == "private:1"
    workflow.handle_owner_event.assert_not_called()


@pytest.mark.parametrize("text", [".周鉴 预览COC", ".推荐COC", ".推荐coc", "．周荐预览coc"])
def test_owner_compact_commands_get_a_card_without_chat_model(tmp_path, text):
    lookup = Mock(return_value=[module()])
    table = TableTools(tmp_path, lookup)
    workflow = Mock(outbox=ProactiveOutbox(InMemoryStore()))
    transport = Mock()
    transport.call.return_value = {"retcode": 0}
    prefs = ProactivePrefs(enabled=True, allowed_channels=frozenset({"direct_message"}),
                           auto_send=True, passive_reply_in_quiet=True)
    event = OwnerEvent("owner", "qq", "private:1", "m", text)
    port = _OneLineOwnerPort(transport, user_id="1", enabled=True)
    assert _process_owner(event, workflow, port, prefs, table=table, group="17") == "accepted"
    lookup.assert_called_once_with("", "克苏鲁的呼唤")
    workflow.handle_owner_event.assert_not_called()
    assert transport.call.call_args.args[1]["message"][0]["data"]["text"] == module().card()


def test_compact_rule_command_uses_local_book(tmp_path):
    table = TableTools(tmp_path, Mock(side_effect=AssertionError("unexpected catalogue lookup")))
    book = tmp_path / "团规.txt"
    book.write_text("借机攻击的触发条件。", encoding="utf-8")
    import_book(table.rules, "17", "DND", "5R", book)
    assert "《团规》" in table.command(".规则DND借机攻击", "17").text
    assert table.command("推荐COC", "17") is None


def test_fixed_card_in_private_preview_and_group_schedule(tmp_path):
    mod = module()
    fields = ["模组名称", "作者", "规则系统", "推荐人数", "预计时长", "发布日期", "题材标签", "玩家导入", "来源"]
    assert [line.split("】")[0].lstrip("【") for line in mod.card().splitlines()] == fields
    assert "【预计时长】未标注" in mod.card()
    other = _module({"_id": "2" * 24, "title": "冒险\n篇", "releaseDate": "2025-07-01T12:00:00Z"})
    assert len(other.card().splitlines()) == len(fields)
    assert "【发布日期】2025-07-01" in other.card()
    table = TableTools(tmp_path, Mock(return_value=[mod]))
    assert "未开启" in table.command(".周荐 状态", "17").text
    workflow = Mock(outbox=ProactiveOutbox(InMemoryStore()))
    transport = Mock()
    transport.call.return_value = {"retcode": 0}
    port = _OneLineOwnerPort(transport, user_id="1", enabled=True)
    prefs = ProactivePrefs(enabled=True, allowed_channels=frozenset({"direct_message"}),
                           auto_send=True, passive_reply_in_quiet=True)
    event = OwnerEvent("owner", "qq", "private:1", "abc", ".周荐 预览 DND")
    assert _process_owner(event, workflow, port, prefs, table=table, group="17") == "accepted"
    parts = transport.call.call_args.args[1]["message"]
    assert parts[0]["data"]["text"] == mod.card() and parts[1]["data"]["file"] == mod.cover
    assert "未开启" in table.command(".周荐 状态", "17").text
    table.command(".周荐 周五 23:58 轮换", "17", manage=True)
    friday = datetime(2026, 11, 20, 23, 55)
    assert "23:58" in table.inspect_schedule("状态", "17", friday).text
    preview = table.inspect_schedule("预览", "17", friday)
    first_rule = table.lookup.call_args.args[1]
    send = Mock(return_value=True)
    table.tick(friday + timedelta(minutes=6), {"17"}, send)
    assert table.lookup.call_args.args[1] == first_rule
    assert send.call_args.args == ("17", preview)
    table.tick(friday + timedelta(minutes=7), {"17"}, send)
    send.assert_called_once()
    table.lookup.return_value = [module("2")]
    table.inspect_schedule("预览", "17", friday + timedelta(weeks=1))
    assert table.lookup.call_args.args[1] != first_rule


def test_chm_glossary_heading_retrieves_body_and_skips_markup(tmp_path):
    page = tmp_path / "玩家手册2024" / "术语.htm"
    page.parent.mkdir()
    page.write_bytes(("<html><head><title>索引</title></head><body><h2>术语</h2>"
                      "<p><strong>借机攻击 Opportunity Attacks</strong></p>"
                      "<p>目标离开触及范围时，查看触发条件。</p><script>ignore()</script>"
                      "<p><b>另一个术语</b></p><p>独立定义</p></body></html>").encode("gb18030"))
    sections = list(chapter_sections(page, tmp_path))
    assert sections == [
        ("玩家手册2024 › 术语 › 借机攻击 Opportunity Attacks", "目标离开触及范围时，查看触发条件。"),
        ("玩家手册2024 › 术语 › 另一个术语", "独立定义"),
    ]
