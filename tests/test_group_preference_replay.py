import sqlite3
from datetime import datetime, timedelta, timezone

from examples.store_group_profiles import replay
from whale_core.memory.consent import MemberConsentStore
from whale_core.memory.group_preferences import preference_clauses, public_profile_context, saved_profile, scoped_claims
from whale_core.memory.spaces import SpaceStore
from whale_core.memory.store import InMemoryStore, MemoryKind
from whale_core.memory.types import MemorySpaceType


def test_preferences_stay_scoped_and_replay_preserves_edits():
    store = InMemoryStore()
    gid = "123"
    SpaceStore(store).create(MemorySpaceType.GROUP_SPACE,name="跑团群",owner="owner",space_id="group-123")
    m = dict(name="阿青",accounts=["101","102"],aliases=["青"])
    owner = dict(name="团长",accounts=["103"],aliases=[])
    blocked = dict(name="乙",accounts=["104"],aliases=[])
    roster = dict(members=[m,owner,blocked], bots=[dict(name="骰子",accounts=["105"],aliases=[])])
    MemberConsentStore(store).opt_out("anon:qq:104")
    now = datetime.now(timezone.utc)
    rows = []
    for i,(account,text) in enumerate([("101","我喜欢调查本"),("101","好耶"),("102","好耶"),
            ("102","好耶"),("103","我喜欢剧情本"),("104","我喜欢快节奏"),("105","我喜欢骰子")]):
        at=now-timedelta(days=9-i)
        rows.append(dict(sender_id=account,message_id=str(i),sent_at=at.isoformat(),day=str(at.date()),text=text,reply_to=""))
    expected={r["message_id"]:r["sender_id"] for r in rows}
    index=[dict(id=p["accounts"][0],keywords=["调查本"]) for p in roster["members"]]
    inputs=roster,rows,index,expected
    first=replay(store,gid,"103",inputs)
    assert first["fact_add"]==2 and first["scanned"]==5 and first["profile_added"]==2
    p=saved_profile(store,gid,"101")
    assert p and p.content["accounts"]==["101","102"] and p.content["text_count"]==4
    assert saved_profile(store,"999","101") is None
    assert not saved_profile(store,gid,"104") and not saved_profile(store,gid,"105")
    assert all(not key.startswith("emotion:") for key in store.keys())
    assert all(r.space_id!="group-123" for r in store.list_by_kind(MemoryKind.PREFERENCE))
    assert "好耶" in " ".join(public_profile_context(store,gid,roster,"青的口头禅"))
    assert not public_profile_context(store,"999",roster,"青的口头禅")
    repeat=replay(store,gid,"103",inputs)
    assert repeat["already_decided"]==2 and repeat["profile_preserved"]==2
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE group_digest_messages(group_id,message_id,sender_id)")
        db.executemany("INSERT INTO group_digest_messages VALUES(?,?,?)",[(gid,k,v) for k,v in expected.items()])
        visible=list(scoped_claims(store,db,gid,[dict(m,id="101")]))
        assert len(visible)==1 and visible[0][1]["current_preference"]
        assert not list(scoped_claims(store,db,"999",[dict(m,id="101")]))
        store.delete(visible[0][1]["key"])
        store.delete(p.key)
        replay(store,gid,"103",inputs)
        assert not saved_profile(store,gid,"101") and not list(scoped_claims(store,db,gid,[dict(m,id="101")]))


def test_preference_excludes_quoted_conditional_and_role_speech():
    assert list(preference_clauses("我现在不喜欢熬夜"))==[("我现在不喜欢熬夜","interest:熬夜")]
    for text in ('他说，我喜欢侦探剧','如果我喜欢潜行','我喜欢跑团吗','我家角色说：我喜欢长剑','我喜欢这个','他喜欢推理'):
        assert not list(preference_clauses(text))
