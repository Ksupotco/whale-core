import json
from datetime import datetime, timezone

from whale_core.memory.group_archive import GroupArchive
from whale_core.memory.sqlite_store import SqliteMemoryStore
from whale_core.memory.store import MemoryKind
from examples.import_qce_group_memory import apply, candidates, read_messages
from types import SimpleNamespace
from whale_core.memory.group_archive import owner_group_context


def _export(path):
    path.write_text(json.dumps({
        "chatInfo": {"type": "group", "peerUid": "123"},
        "messages": [
            {"id": "a", "timestamp": 1795060800000,
             "sender": {"uin": "11", "name": "阿甲"}, "type": "text",
             "content": {"text": "我们决定周末讨论星图"}},
            {"id": "b", "timestamp": 1795060860000,
             "sender": {"uin": "22", "name": "阿乙"}, "type": "text",
             "content": {"text": "我喜欢科幻设定"}},
        ]}, ensure_ascii=False), encoding="utf-8")


def test_archive_keeps_source_and_cannot_cross_groups(tmp_path):
    source = tmp_path / "group.json"
    _export(source)
    archive = GroupArchive(tmp_path / "archive.sqlite3")
    try:
        assert archive.import_qce([source, source], group_id="123")["archived"] == 2
        now = datetime(2026, 11, 20, tzinfo=timezone.utc)
        hits = archive.search("123", "之前谁说喜欢科幻设定？", now=now)
        assert len(hits) == 1
        assert "阿乙" in hits[0] and "我喜欢科幻设定" in hits[0]
        assert archive.search("456", "之前谁说喜欢科幻设定？", now=now) == ()
        assert archive.search("123", "喜欢科幻设定", now=now) == ()
    finally:
        archive.close()


def test_import_rejects_wrong_group(tmp_path):
    source = tmp_path / "group.json"
    _export(source)
    archive = GroupArchive(tmp_path / "archive.sqlite3")
    try:
        try:
            archive.import_qce([source], group_id="456")
        except ValueError:
            pass
        else:
            raise AssertionError("wrong group accepted")
    finally:
        archive.close()


def test_group_and_person_memories_are_separate_and_idempotent(tmp_path):
    source = tmp_path / "group.json"
    _export(source)
    database = tmp_path / "memory.sqlite3"
    SqliteMemoryStore(database).close()
    rows = candidates(read_messages([source], "123"), "123")
    result = apply([source], "123", rows, tmp_path)
    assert result["group_public"] >= 1
    assert result["personal_profile"] == 1
    store = SqliteMemoryStore(database)
    try:
        group = store.list_by_kind(MemoryKind.EPISODE, space_ids=("group-123",))
        assert group and all("本群发言" in r.content["summary"] for r in group)
        private = [r for r in store.list_by_kind(MemoryKind.PREFERENCE)
                   if r.space_id.startswith("member-")]
        assert len(private) == 1
        assert store.get("bind:mem:qq:qq_group:group:123")[0]["space_id"] == "group-123"
    finally:
        store.close()
    repeated = apply([source], "123", rows, tmp_path)
    assert repeated["group_public"] == 0
    assert repeated["personal_profile"] == 0


def test_owner_review_requires_owner_grant_and_group_question(tmp_path):
    source = tmp_path / "group.json"
    _export(source)
    store = SqliteMemoryStore(tmp_path / "memory.sqlite3")
    archive = GroupArchive(tmp_path / "group_archive.sqlite3")
    archive.import_qce([source], group_id="123")
    archive.close()
    owner = SimpleNamespace(principal=SimpleNamespace(is_owner=True))
    stranger = SimpleNamespace(principal=SimpleNamespace(is_owner=False))
    try:
        assert not owner_group_context(store, owner, "private", "群里谁喜欢科幻设定")
        store.put("owner:group_review", {"group_ids": ["123"]})
        assert owner_group_context(store, owner, "private", "群里谁喜欢科幻设定")
        assert not owner_group_context(store, stranger, "private", "群里谁喜欢科幻设定")
        assert not owner_group_context(store, owner, "group:456", "群里谁喜欢科幻设定")
        assert not owner_group_context(store, owner, "private", "今天吃什么")
    finally:
        store.close()
