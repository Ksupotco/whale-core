import json
from types import SimpleNamespace
import sqlite3

from examples.build_group_digest import rebuild
from examples.group_keywords import extract
from whale_core.api.studio.group_digest import group_digest
from whale_core.memory.group_archive import GroupArchive, digest_context
from whale_core.memory.store import InMemoryStore
from whale_core.memory.types import MemorySpaceType


def test_keyword_phrases_are_grounded_and_fillers_do_not_create_topics():
    _,(empty,words) = extract(["我们 他们 这个 还有 哈哈哈 觉得 怎么 晚安", "调查员要做理智检定，再讨论《夜航船》"])
    assert not empty
    assert words['夜航船']=='作品名' and '理智检定' in words
    assert '调查员' in words


def test_group_segments_aliases_and_scoped_sources(tmp_path):
    path = tmp_path / 'group_archive.sqlite3'
    archive = GroupArchive(path)
    rows = [('a','001','小雨',0,'text','星图观测'),('b','001','小雪',10,'reply','观测星图'),
            ('c','002','小雪',1210,'text','准备晚餐'),('d','002','系统',1211,'system','操作通知')]
    source = tmp_path/'export.json'
    source.write_text(json.dumps(dict(chatInfo=dict(type='group',peerUid='123'),messages=[
        dict(id=mid,timestamp=(1700000000+t)*1000,sender=dict(uin=uid,name=name),type=k,content=dict(text=text))
        for mid,uid,name,t,k,text in rows])),encoding='utf-8')
    archive.import_qce([source],group_id='123')
    archive.close()
    counts = rebuild(path,'123',[source])
    assert counts == dict(messages=3,segments=2,members=2,with_keywords=2)
    assert rebuild(path,'123',[source]) == counts
    store = InMemoryStore()
    store.path = str(tmp_path/'memory.sqlite3')
    svc = SimpleNamespace(spaces=SimpleNamespace(ref=lambda _:SimpleNamespace(type=MemorySpaceType.GROUP_SPACE)),
                          bindings=SimpleNamespace(member_binding_for=lambda _:None))
    captured = []
    def read(**params):
        h = SimpleNamespace(query={k:[v] for k,v in dict(group_id='123',**params).items()},
                            _send=lambda status,body:captured.append(json.loads(body)['result']))
        group_digest(h,SimpleNamespace(store=store),svc)
        return captured[-1]
    overview = read()
    assert overview['members'][0]['aliases'] == ['小雨','小雪']
    assert overview['members'][0]['name'] == '小雪'
    assert len(read(sender_id='001')['items']) == 2
    assert read(sender_id='002',word='晚餐')['items'][0]['message_id']=='c'
    assert read(segment_id='a',sender_id='002')['items'] == []
    with sqlite3.connect(path) as db:
        assert '小雪：2 条' in digest_context(db,'123','群里小雨的画像')[0]
        assert digest_context(db,'456','群成员有哪些') == []
