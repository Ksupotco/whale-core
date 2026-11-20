"""Durably collect group utterances and materialize them on an owner's explicit refresh."""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from threading import Lock

from whale_core.memory import group_keywords
from whale_core.memory.group_directory import load_directory, roster_members
from whale_core.memory.group_portraits import COMMAND, SHANGHAI
from whale_core.memory.group_replay import replay
from whale_core.memory.group_topics import publish, summarize
from whale_core.memory.pause import is_recording_paused


def create_tables(db):
    db.execute("""CREATE TABLE IF NOT EXISTS group_refresh_settings (
        group_id TEXT PRIMARY KEY, owner_account TEXT NOT NULL, extractor TEXT NOT NULL)""")
    # NULL terms = captured and awaiting the next manual refresh; '{}' = processed with no keywords.
    db.execute("""CREATE TABLE IF NOT EXISTS group_features (
        group_id TEXT NOT NULL, message_id TEXT NOT NULL, origin TEXT NOT NULL, seq TEXT NOT NULL,
        authored TEXT NOT NULL, reply_to TEXT NOT NULL, terms TEXT, PRIMARY KEY(group_id,message_id))""")
    db.execute("CREATE INDEX IF NOT EXISTS group_feature_seq ON group_features(group_id,seq)")


def status(db, gid):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='group_refresh_settings'").fetchone():
        return None
    if not db.execute("SELECT 1 FROM group_refresh_settings WHERE group_id=?", (gid,)).fetchone():
        return None
    return db.execute("SELECT count(*) FROM group_features WHERE group_id=? AND terms IS NULL", (gid,)).fetchone()[0]


def authored_event(event, incoming):
    message = event.get("message")
    reply = ""
    text = incoming.text
    if isinstance(message, list):
        reply = next((str(p["data"]["id"]) for p in message if isinstance(p, dict) and p.get("type") == "reply"
                      and isinstance(p.get("data"), dict) and p["data"].get("id") is not None), "")
    elif isinstance(message, str):
        hit = re.search(r"\[CQ:reply,id=(-?\d+)(?:,[^\]]*)?\]", message)
        reply = hit[1] if hit else ""
        text = re.sub(r"\[CQ:[^\]]+\]", "", text)
        text = text.replace("&#91;", "[").replace("&#93;", "]").replace("&amp;", "&")
    return text.strip()[:4000], reply


def capture(archive, gid, event, incoming):
    """Called after transport verification, before reply selection. Repeated delivery is a no-op."""
    text, reply = authored_event(event, incoming)
    mid = incoming.message_id
    if not text or not mid or not isinstance(event.get("time"), (int, float)):
        return False
    moment = datetime.fromtimestamp(event["time"], timezone.utc).isoformat()
    sender = event.get("sender") or {}
    name = str(sender.get("card") or sender.get("nickname") or "")[:80]
    seq = str(event.get("message_seq") or "")
    key = "ob-" + mid
    with closing(sqlite3.connect(archive, timeout=10)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        # Exporter IDs and the bridge's IDs need not agree. Match an export only with
        # native id/sequence AND the same author and second, never by wording.
        duplicate = db.execute("""SELECT 1 FROM group_features f JOIN messages m USING(group_id,message_id)
            WHERE f.group_id=? AND (f.message_id=? OR (f.origin='qce' AND
            (f.message_id=? OR (f.seq<>'' AND f.seq=?)) AND m.sender_id=?
            AND strftime('%s',m.sent_at)=strftime('%s',?))) LIMIT 1""",
            (gid, key, mid, seq, incoming.account, moment)).fetchone()
        if duplicate:
            return False
        db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)", (gid,key,moment,incoming.account,name,text))
        db.execute("INSERT INTO group_features VALUES(?,?,'onebot',?,?,?,NULL)",
                   (gid,key,seq,text,"ob-"+reply if reply else ""))
    return True


def speech_row(mid, uid, stamp, text, reply, expected):
    if not text or COMMAND.match(text) or "://" in text:
        return None
    at = datetime.fromisoformat(stamp).astimezone(SHANGHAI)
    return dict(message_id=mid, sender_id=uid, sent_at=at.isoformat(), day=at.date().isoformat(),
                text=text, reply_to=reply if reply in expected else "")


def tag_batch(archive, texts, expected):
    config = archive.with_name("group_nlp_python.txt")
    interpreter = Path(config.read_text(encoding="utf-8-sig").strip()) if config.is_file() else Path(sys.executable)
    if interpreter == Path(sys.executable):
        engine, terms = group_keywords.extract(texts)
    else:
        result = subprocess.run([str(interpreter), "-I", "-X", "utf8", group_keywords.__file__],
                                input=json.dumps(texts), encoding="utf-8", capture_output=True,
                                timeout=180, check=True)
        engine, terms = json.loads(result.stdout)
    if engine != expected or len(terms) != len(texts):
        raise ValueError("分词环境与现有群索引不一致，请核对 group_nlp_python.txt")
    return terms


# Studio runs one server per instance. Do not process a second snapshot while the first is publishing.
_refresh_lock = Lock()


class RefreshInProgress(Exception):
    pass


def refresh(store, archive, gid):
    if not _refresh_lock.acquire(blocking=False):
        raise RefreshInProgress()
    try:
        with closing(sqlite3.connect(archive, timeout=10)) as db:
            pending = status(db, gid)
            if pending is None:
                raise ValueError("本群尚未配置手动更新")
            if is_recording_paused(store, "group-" + gid):
                raise ValueError("群空间已暂停记录")
            if not pending:
                return {"processed": 0, "pending": 0}
            roster = load_directory(archive, gid)
            if not roster:
                raise ValueError("本群缺少成员名单")
            owner, engine = db.execute("SELECT owner_account,extractor FROM group_refresh_settings WHERE group_id=?",
                                       (gid,)).fetchone()
            # A single snapshot. Events collected while this runs remain pending for the next click.
            rows = db.execute("""SELECT m.message_id,m.sent_at,m.sender_id,m.sender_name,
                f.authored,f.reply_to,f.terms FROM messages m JOIN group_features f USING(group_id,message_id)
                WHERE m.group_id=? ORDER BY m.sent_at,m.message_id""", (gid,)).fetchall()
            fresh = [r for r in rows if r[6] is None]
            bots = {a for b in roster["bots"] for a in b["accounts"]}
            batch = dict(zip((r[0] for r in fresh), tag_batch(archive, [r[4] if r[2] not in bots else ""
                        for r in fresh], engine), strict=True))
            tokens = [batch[r[0]] if r[6] is None else json.loads(r[6]) for r in rows]
            payload, links = summarize(gid, [r[:5] for r in rows], tokens, engine)
            expected = {r[0]:r[2] for r in rows}
            speech = [s for mid,stamp,uid,_,text,reply,_ in rows
                      if (s := speech_row(mid,uid,stamp,text,reply,expected)) is not None]
            inputs = roster, speech, roster_members(payload["members"], roster), expected
            counts = replay(store,gid,owner,inputs,pending_ids=batch.keys(),portrait_archive=archive)
            if counts.get("group_paused"):
                raise ValueError("群空间已暂停记录")
            # Fact decisions use stable evidence IDs and snapshots use per-member keys.
            # An interruption before this checkpoint can safely replay the same batch.
            with db:
                publish(db, gid, payload, links)
                db.executemany("UPDATE group_features SET terms=? WHERE group_id=? AND message_id=? AND terms IS NULL",
                               [(json.dumps(t,ensure_ascii=False),gid,mid) for mid,t in batch.items()])
            return {"processed": len(batch), "pending": status(db,gid), "counts": counts}
    finally:
        _refresh_lock.release()
