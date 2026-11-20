from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

from examples.build_group_digest import rebuild
from examples.build_group_portraits import build
from whale_core.api.studio.group_digest import group_digest
from whale_core.memory.group_archive import GroupArchive, owner_group_context
from whale_core.memory.group_directory import directory_path, read_portrait
from whale_core.memory.store import InMemoryStore
from whale_core.memory.types import MemorySpaceType


def test_roster_merges_accounts_and_learns_only_authored_group_text(tmp_path):
    dbpath = tmp_path / "group_archive.sqlite3"
    group = "123"
    member = dict(name="阿青", accounts=["101", "102"], aliases=["旧称"])
    roster = dict(group_id=group, name="测试群", description="跑团群", members=[member],
                  bots=[dict(name="骰子", accounts=["103", "104"], aliases=["甲骰", "乙骰"])])
    directory = directory_path(dbpath, group)
    directory.mkdir(parents=True)
    (directory / "群资料.json").write_text(json.dumps(roster), encoding="utf-8")
    messages = []
    for i, (sender, body, kind) in enumerate([("101","好耶","reply"),("102","好耶","text"),
            ("101","好耶","text"),("103","骰点","text"),("104","骰点","text"),("101","旧消息","recalled")]):
        t = datetime(2026,1,1,tzinfo=timezone.utc) + timedelta(days=i)
        els = [dict(type="text",data={"text":body})]
        if kind == "reply":
            els.insert(0,dict(type="reply",data={"content":"别人的话","referencedMessageId":"2"}))
        messages.append(dict(id=str(i),sender=dict(uid=sender,name="原昵称"),timestamp=int(t.timestamp()*1000),
                             type="reply" if kind=="reply" else "text",recalled=kind=="recalled",
                             content=dict(text=body,elements=els)))
    file = tmp_path / "export.json"
    file.write_text(json.dumps(dict(chatInfo=dict(type="group",peerUid=group),messages=messages)),encoding="utf-8")
    archive = GroupArchive(dbpath)
    archive.import_qce([file], group_id=group)
    archive.close()
    rebuild(dbpath, group, [file])
    stats = build(dbpath,group,[file,file])
    assert stats == build(dbpath,group,[file])
    profile = read_portrait(dbpath,group,member)
    assert profile["text_count"]==3 and profile["archived_count"]==4
    marker = next(x for x in profile["expressions"] if x["term"]=="好耶")
    assert marker["count"]==3 and marker["days"]==3 and marker["other_total"]==0
    assert marker["examples"][0]["text"]=="好耶" and marker["examples"][0]["reply_to"]=="2"
    assert len(list((directory/"members").glob("*.json"))) == 1

    store=InMemoryStore()
    store.path=str(tmp_path/"memory.sqlite3")
    store.put("owner:group_review",{"group_ids":[group]})
    ctx=SimpleNamespace(store=store, principal=SimpleNamespace(is_owner=True))
    svc=SimpleNamespace(spaces=SimpleNamespace(ref=lambda _:SimpleNamespace(type=MemorySpaceType.GROUP_SPACE)),
                        bindings=SimpleNamespace(member_binding_for=lambda _:None))
    def request(**params):
        output=[]
        handler=SimpleNamespace(query={k:[v] for k,v in dict(group_id=group,**params).items()},
                                _send=lambda _,body:output.append(json.loads(body)["result"]))
        group_digest(handler,ctx,svc)
        return output[0]
    overview=request()
    assert overview["group"]["people"]==1 and overview["group"]["bots"][0]["count"]==2
    assert [p["name"] for p in overview["members"]]==["阿青"]
    assert {r["sender_id"] for r in request(sender_id="101")["items"]}=={"101","102"}
    assert all(r["display_name"]=="阿青" and r["sender_name"]=="原昵称" for r in request(sender_id="101")["items"])
    assert [v["message_id"] for v in request(around="2")["items"]]==["1","2","3"]
    assert "1 位成员" in owner_group_context(store,ctx,"private:1","这个群里有几个人")[0]
    assert any("好耶" in s for s in owner_group_context(store,ctx,"private:1","旧称的口头禅"))
    assert not owner_group_context(store,ctx,"group:999","旧称的口头禅")
