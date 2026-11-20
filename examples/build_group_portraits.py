"""Create one portrait per human on a private group roster, using QCE's authored text elements."""
import argparse
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3

from whale_core.memory.group_directory import atomic_write, account_names, load_directory, portrait_path, roster_members


from whale_core.memory.group_refresh import speech_row, status
from whale_core.memory.group_portraits import COMMAND, SHANGHAI, extract_profiles


def read_speech(files, gid, expected):
    rows = {}
    seen = set()
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if data["chatInfo"]["type"] != "group" or str(data["chatInfo"]["peerUid"]) != gid:
            raise ValueError("导出文件来自其他群")
        for m in data["messages"]:
            mid = str(m["id"])
            if mid not in expected:
                continue
            sender = m["sender"]
            uid = str(sender.get("uin") or sender.get("uid") or "")
            if uid != expected[mid]:
                raise ValueError("导出文件的发送者与归档不一致")
            if mid in seen:
                if m.get("recalled"):
                    rows.pop(mid, None)
                continue
            seen.add(mid)
            if m.get("recalled") or m["type"] not in ("text", "reply"):
                continue
            elems = m["content"].get("elements") or []
            text = "".join(str(e["data"].get("text") or "") for e in elems if e["type"] == "text").strip()
            if not text or COMMAND.match(text) or "://" in text:
                continue
            target = next((str(e["data"].get("referencedMessageId") or "") for e in elems if e["type"] == "reply"), "")
            moment = datetime.fromtimestamp(m["timestamp"] / 1000, SHANGHAI)
            rows[mid] = dict(message_id=mid, sender_id=uid, text=text, sent_at=moment.isoformat(),
                             day=moment.date().isoformat(), reply_to=target if target in expected else "")
    if expected.keys() - seen:
        raise ValueError("输入文件没有覆盖归档内的全部发言")
    return sorted(rows.values(), key=lambda r: (r["sent_at"], r["message_id"]))


def load_group_inputs(archive, gid, files):
    roster = load_directory(archive, gid)
    if not roster:
        raise ValueError("请先设置本群的群资料.json")
    accounts = account_names(roster)
    entries = roster["members"] + roster["bots"]
    if len(accounts) != sum(len(m["accounts"]) for m in entries) or len({m["name"] for m in entries}) != len(entries):
        raise ValueError("成员称呼和所属账号必须唯一")
    with closing(sqlite3.connect(archive.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        expected = dict(db.execute("SELECT message_id,sender_id FROM group_digest_messages WHERE group_id=?", (gid,)))
        indexed = db.execute("SELECT payload FROM group_digest WHERE group_id=?", (gid,)).fetchone()
        live = []
        if status(db,gid) is not None:
            live = db.execute("""SELECT message_id,sender_id,sent_at,authored,reply_to FROM messages
                JOIN group_features USING(group_id,message_id) WHERE group_id=? AND origin='onebot'
                AND terms IS NOT NULL""",(gid,)).fetchall()
    if not expected:
        raise ValueError("尚未导入该群的发言索引")
    live_ids = {r[0] for r in live}
    rows = read_speech(files, gid, {mid:uid for mid,uid in expected.items() if mid not in live_ids})
    rows.extend(s for r in live if (s := speech_row(*r,expected)) is not None)
    rows.sort(key=lambda r:(r["sent_at"],r["message_id"]))
    index = roster_members(json.loads(indexed[0])["members"], roster)
    return roster, rows, index, expected


def build(archive, gid, files):
    roster, rows, index, expected = load_group_inputs(archive, gid, files)
    total = 0
    for member, profile in extract_profiles(gid, roster, rows, index, expected):
        atomic_write(portrait_path(archive, gid, member), profile)
        total += profile["text_count"]
    return dict(members=len(roster["members"]), dice_accounts=sum(len(m["accounts"]) for m in roster["bots"]),
                archived=len(expected), human_authored_text=total)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--group-id", required=True)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    print(build(args.archive, args.group_id, args.files))
