"""Initialize the incremental keyword cache for an already indexed group."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from examples.build_group_digest import qce_material
from examples.build_group_portraits import read_speech
from whale_core.memory.group_directory import load_directory
from whale_core.memory.group_keywords import extract
from whale_core.memory.group_refresh import create_tables, status


def prepare(archive, gid, owner, files):
    roster = load_directory(archive,gid)
    if not roster or owner not in {a for m in roster["members"] for a in m["accounts"]}:
        raise ValueError("主人账号不在本群名单内")
    bots = {a for b in roster["bots"] for a in b["accounts"]}
    with closing(sqlite3.connect(archive.resolve().as_uri() + "?mode=rw",uri=True)) as db:
        if status(db,gid) is not None:
            return {"already_enabled": True}
        expected = dict(db.execute("SELECT message_id,sender_id FROM group_digest_messages WHERE group_id=?", (gid,)))
        old = db.execute("SELECT payload FROM group_digest WHERE group_id=?", (gid,)).fetchone()
        if not old or not expected:
            raise ValueError("本群缺少归档索引")
        _, raw, recalled, seq = qce_material(gid,files)
        speech = {r["message_id"]:r for r in read_speech(files,gid,expected)}
        engine, features = extract([raw.get(mid,"") if uid not in bots and mid not in recalled else ""
                                    for mid,uid in expected.items()])
        if engine != json.loads(old[0]).get("keyword_extractor"):
            raise ValueError("请使用与已有群关键词索引相同的环境")
        with db:
            create_tables(db)
            db.executemany("INSERT INTO group_features VALUES(?,?,'qce',?,?,?,?)",
                           [(gid,mid,seq[mid],speech.get(mid,{}).get("text",""),
                             speech.get(mid,{}).get("reply_to",""),json.dumps(terms,ensure_ascii=False))
                            for mid,terms in zip(expected,features,strict=True)])
            db.execute("INSERT INTO group_refresh_settings VALUES(?,?,?)", (gid,owner,engine))
        return {"historical_rows":len(expected), "extractor":engine, "pending":status(db,gid)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--group-id", required=True)
    parser.add_argument("--owner-qq", required=True)
    parser.add_argument("files",type=Path,nargs="+")
    args = parser.parse_args()
    print(prepare(args.archive,args.group_id,args.owner_qq,args.files))
