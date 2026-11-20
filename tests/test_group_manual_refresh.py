import json
import queue
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

import pytest

from examples.build_group_digest import rebuild
from examples.prepare_group_refresh import prepare
from examples.build_group_portraits import load_group_inputs
from examples.qq_onebot_local import _QueuedBridge
from whale_core.life.channels import OwnerChannelRegistry
from whale_core.memory import group_refresh as live
from whale_core.memory.group_archive import GroupArchive
from whale_core.memory.group_directory import directory_path, read_portrait
from whale_core.memory.group_preferences import saved_profile
from whale_core.memory.spaces import SpaceStore
from whale_core.memory.sqlite_store import SqliteMemoryStore
from whale_core.memory.store import MemoryKind
from whale_core.memory.types import MemorySpaceType


def test_manual_batch_replay_and_interrupted_publication(tmp_path, monkeypatch):
    gid = "123"
    archive = tmp_path / "group_archive.sqlite3"
    database = tmp_path / "memory.sqlite3"
    member = dict(name="甲",accounts=["101","102"],aliases=[])
    roster = dict(group_id=gid,name="测试群",description="",members=[member],
                  bots=[dict(name="骰子",accounts=["107"],aliases=[])])
    directory = directory_path(archive, gid)
    directory.mkdir(parents=True)
    (directory/"群资料.json").write_text(json.dumps(roster),encoding="utf-8")
    now = int(datetime.now(timezone.utc).timestamp())
    old = dict(id="8888",seq=77,timestamp=(now-86400)*1000,type="text",sender=dict(uin="101",name="甲"),
               content=dict(text="好耶",elements=[dict(type="text",data={"text":"好耶"})]))
    source = tmp_path/"export.json"
    source.write_text(json.dumps(dict(chatInfo=dict(type="group",peerUid=gid),messages=[old])),encoding="utf-8")
    with closing(GroupArchive(archive)) as history:
        history.import_qce([source],group_id=gid)
    rebuild(archive,gid,[source])
    assert prepare(archive,gid,"101",[source])["historical_rows"] == 1
    with closing(SqliteMemoryStore(database)) as store:
        SpaceStore(store).create(MemorySpaceType.GROUP_SPACE,name="群",owner="owner",space_id="group-"+gid)

    def bridge():
        return _QueuedBridge(OwnerChannelRegistry(owner_id="owner",bindings={}), queue.Queue(),
                             bot_qq="900",group_ids={gid},
                             archive_event=lambda e,i:live.capture(archive,gid,e,i))

    transport = bridge()
    def send(mid,text,uid="101",auth=True,**extra):
        event = dict(self_id="900",time=now,post_type="message",message_type="group",group_id=gid,
                     user_id=uid,message_id=mid,message=text,sender=dict(nickname="测试"))
        transport.handle_event(dict(event,**extra),authenticated=auth)

    def counts():
        with closing(sqlite3.connect(archive)) as db:
            return tuple(db.execute("SELECT count(*) FROM "+table).fetchone()[0]
                         for table in ("messages","group_digest_messages")),live.status(db,gid)

    send(12,"好耶",message_seq=77,time=now-86400)  # Overlap with native historical sequence
    send(13,"不属于本群",group_id="333")
    send(14,"无签名",auth=False)
    send(15,"自己的回复",uid="900")
    assert counts()==((1,1),0)
    send(16,"好耶",uid="102")
    transport = bridge()  # Reconnect with no process-local state.
    send(16,"好耶",uid="102")
    send(17,"好耶",uid="102")  # Identical text and time, genuinely distinct ID.
    send(18,[dict(type="reply",data=dict(id=16,text="我喜欢旧话题")),
             dict(type="text",data=dict(text="我喜欢《夜航船》"))],uid="102")
    send(19,"我喜欢《骰子暗号》",uid="107")
    assert counts()==((5,1),4)
    original = live.publish
    def fail(*args): raise sqlite3.OperationalError("simulated failure after memory write")
    monkeypatch.setattr(live,"publish",fail)
    with closing(SqliteMemoryStore(database)) as store:
        assert not saved_profile(store,gid,"101") and not store.list_by_kind(MemoryKind.PREFERENCE)
        with pytest.raises(sqlite3.OperationalError):
            live.refresh(store,archive,gid)
        assert counts()==((5,1),4)
        assert len(store.list_by_kind(MemoryKind.PREFERENCE))==1

    monkeypatch.setattr(live,"publish",original)
    with closing(SqliteMemoryStore(database)) as store:
        def arrival_during_batch(*args):
            with pytest.raises(live.RefreshInProgress): live.refresh(store,archive,gid)
            send(20,"补一条晚到的消息",time=now-7200)
            return original(*args)
        monkeypatch.setattr(live,"publish",arrival_during_batch)
        result=live.refresh(store,archive,gid)
        assert result["processed"]==4 and result["pending"]==1
        assert result["counts"]["already_decided"]==1
        monkeypatch.setattr(live,"publish",original)
        assert live.refresh(store,archive,gid)["processed"]==1
        assert counts()==((6,6),0)
        before=saved_profile(store,gid,"101").content
        assert before["text_count"]==5 and not saved_profile(store,gid,"107")
        assert any(t["label"]=="好耶" and t["count"]==3 for t in before["traits"])
        assert len(store.list_by_kind(MemoryKind.PREFERENCE))==1
        assert read_portrait(archive,gid,member)["text_count"]==5
        send(18,"同一投递的重试",uid="102")
        assert live.refresh(store,archive,gid)==dict(processed=0,pending=0)
        assert saved_profile(store,gid,"101").content==before
    with closing(sqlite3.connect(archive)) as db:
        digest=json.loads(db.execute("SELECT payload FROM group_digest").fetchone()[0])
        sources={w:ids for s in digest["segments"] for w,ids in s["keyword_sources"].items()}
        assert sources["夜航船"]==["ob-18"] and "骰子暗号" not in sources
    send(21,"留给下次手动更新")
    assert rebuild(archive,gid,[source])["messages"]==6
    loaded=load_group_inputs(archive,gid,[source])
    assert "ob-20" in loaded[3] and "ob-21" not in loaded[3]
    assert counts()==((7,6),1)
