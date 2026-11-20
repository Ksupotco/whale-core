"""Build a local topic and member index for a complete, already imported QCE archive."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
if __package__:
    from .group_keywords import extract
else:
    from group_keywords import extract
from whale_core.memory.group_refresh import status
from whale_core.memory.group_topics import summarize, publish
from whale_core.memory.group_directory import load_directory


def qce_material(gid, files):
    kinds = {}
    authored = {}
    recalled = set()
    sequences = {}
    for path in files:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        if data['chatInfo']['type'] != 'group' or str(data['chatInfo']['peerUid']) != gid:
            raise ValueError('导出文件与所选群不符')
        for m in data['messages']:
            mid = str(m['id'])
            kinds[mid] = m['type']
            sequences[mid] = str(m.get('seq') or '')
            if m.get('recalled'):
                recalled.add(mid)
            if m['type'] in ('text','reply'):
                c = m['content']
                authored[mid] = (''.join(e['data'].get('text','') for e in c['elements'] if e['type']=='text')
                                 if 'elements' in c else c.get('text','') if m['type']=='text' else '')
    return kinds, authored, recalled, sequences


def rebuild(database, gid, files, node='node'):
    kinds, authored, recalled, _ = qce_material(gid, files)
    roster = load_directory(database,gid) or {}
    bots = {a for bot in roster.get("bots",[]) for a in bot["accounts"]}
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=rw', uri=True)) as db:
        rows = db.execute('SELECT message_id,sent_at,sender_id,sender_name,text FROM messages '
                          'WHERE group_id=? ORDER BY sent_at,message_id', (gid,)).fetchall()
        # Rebuilding an export must preserve published live conversations. New live
        # messages wait for the owner's refresh even when the offline CLI is used.
        if status(db,gid) is not None:
            live = dict(db.execute("SELECT message_id,terms FROM group_features WHERE group_id=? AND origin='onebot'", (gid,)))
        else:
            live = {}
        rows = [r for r in rows if r[0] not in live or live[r[0]] is not None]
        if not rows or any(row[0] not in kinds and row[0] not in live for row in rows):
            raise ValueError('请提供覆盖本群整个已归档范围的导出文件')
        speech = [r for r in rows if r[2] and kinds.get(r[0]) != 'system']
        texts = [authored.get(r[0],'') if r[2] not in bots and r[0] not in recalled else '' for r in speech]
        extractor,tokens = extract(texts)
        tokens = [json.loads(live[r[0]]) if r[0] in live else t for r,t in zip(speech,tokens,strict=True)]
        payload, links = summarize(gid, speech, tokens, extractor)
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS group_digest (group_id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS group_digest_messages (group_id TEXT,message_id TEXT,segment_id TEXT,sender_id TEXT,PRIMARY KEY(group_id,message_id))')
            db.execute('CREATE INDEX IF NOT EXISTS digest_by_segment ON group_digest_messages(group_id,segment_id)')
            db.execute('CREATE INDEX IF NOT EXISTS digest_by_person ON group_digest_messages(group_id,sender_id)')
            db.execute('DELETE FROM group_digest_messages WHERE group_id=?', (gid,))
            publish(db, gid, payload, links)
        segments = payload['segments']
        return dict(messages=len(speech),segments=len(segments),members=len(payload['members']),with_keywords=sum(bool(s['keywords']) for s in segments))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('--group-id', required=True)
    parser.add_argument('--node', default=None, help=argparse.SUPPRESS)  # Older launchers pass this.
    parser.add_argument('files', nargs='+', type=Path)
    args = parser.parse_args()
    print(rebuild(args.archive,args.group_id,args.files,args.node))
