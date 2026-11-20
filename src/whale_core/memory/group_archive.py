"""Local, group-scoped archive of imported QQ messages.

The archive keeps source messages separate from curated memory. A group may
search only its own archive, and only when the current question asks about
earlier conversation.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from whale_core.memory.group_directory import account_names, load_directory, name_hint, read_portrait, roster_members
from whale_core.memory.group_preferences import profile_key, saved_profile, scoped_claims

_PORTRAIT_CUES = ("画像", "风格", "口头禅", "说话习惯", "怎么说话", "常用语", "常用句式")
_LIKES = ("喜好","喜欢","偏好","爱好","感兴趣")
_ROSTER_CUES = ("群成员", "群里有谁", "哪些群友", "群里都有谁", "群里有几个人", "群里有多少人", "谁是骰子", "骰子账号")

_RECALL = ("之前", "以前", "上次", "昨天", "前天", "刚才", "聊过", "说过",
           "提过", "提到", "记得", "当时", "历史", "谁说", "什么时候")
_FILLER = ("我们", "你们", "他们", "这个", "那个", "什么", "怎么", "哪天",
           "之前", "以前", "上次", "昨天", "前天", "刚才", "聊过", "说过",
           "提过", "提到", "记得", "当时", "历史", "谁说", "什么时候",
           "群里", "本群", "消息", "对话", "内容", "的是", "了吗")


def _terms(text: str) -> set[str]:
    clean = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", " ", text.lower())
    for filler in _FILLER:
        clean = clean.replace(filler, " ")
    terms: set[str] = set()
    for run in clean.split():
        if re.fullmatch(r"[\u4e00-\u9fff]+", run):
            terms.update(run[i:i + 2] for i in range(len(run) - 1))
        elif len(run) >= 2:
            terms.add(run)
    return terms


class GroupArchive:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.db = sqlite3.connect(str(self.path))
        self.db.execute("""CREATE TABLE IF NOT EXISTS messages (
            group_id TEXT NOT NULL, message_id TEXT NOT NULL,
            sent_at TEXT NOT NULL, sender_id TEXT NOT NULL,
            sender_name TEXT NOT NULL, text TEXT NOT NULL,
            PRIMARY KEY (group_id, message_id))""")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_group_archive_time "
                        "ON messages(group_id, sent_at)")
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def import_qce(self, paths: list[str | Path], *, group_id: str) -> dict:
        """Import text from QCE JSON, preserving the original message IDs."""
        if not group_id.isdigit() or not paths:
            raise ValueError("A numeric group ID and source files are required")
        rows = []
        for path in paths:
            source = Path(path)
            data = json.loads(source.read_text(encoding="utf-8"))
            chat = data.get("chatInfo") or {}
            if chat.get("type") != "group" or str(chat.get("peerUid")) != group_id:
                raise ValueError(f"QCE file belongs to another conversation: {source}")
            for item in data.get("messages") or []:
                mid = str(item.get("id") or "")
                text = str((item.get("content") or {}).get("text") or "").strip()
                if not mid or not text:
                    continue
                sender = item.get("sender") or {}
                stamp = datetime.fromtimestamp(int(item["timestamp"]) / 1000,
                                               timezone.utc).isoformat()
                rows.append((group_id, mid, stamp,
                             str(sender.get("uin") or sender.get("uid") or ""),
                             str(sender.get("name") or sender.get("nickname") or "")[:80],
                             text[:4000]))
        with self.db:
            self.db.executemany("INSERT OR IGNORE INTO messages VALUES (?,?,?,?,?,?)",
                                rows)
        count = self.db.execute("SELECT COUNT(*) FROM messages WHERE group_id=?",
                                (group_id,)).fetchone()[0]
        return {"source_text_rows": len(rows), "archived": count}

    def search(self, group_id: str, query: str, *, now: datetime | None = None,
               limit: int = 5, require_recall: bool = True) -> tuple[str, ...]:
        if not group_id.isdigit() or (require_recall and not any(cue in query for cue in _RECALL)):
            return ()
        terms = _terms(query)
        if not terms or limit <= 0:
            return ()
        current = now or datetime.now(timezone.utc)
        day = current.astimezone().date()
        if "昨天" in query:
            day -= timedelta(days=1)
        elif "前天" in query:
            day -= timedelta(days=2)
        else:
            day = None
        ranked = []
        roster = load_directory(self.path, group_id)
        names = account_names(roster) if roster else {}
        for mid, stamp, uid, sender, body in self.db.execute(
                "SELECT message_id,sent_at,sender_id,sender_name,text FROM messages "
                "WHERE group_id=? AND text<>''", (group_id,)):
            when = datetime.fromisoformat(stamp)
            if day is not None and when.astimezone().date() != day:
                continue
            display = names.get(uid, sender)
            overlap = terms & _terms(display + " " + sender + " " + body[:1000])
            if not overlap:
                continue
            score = len(overlap) / max(1, len(terms))
            ranked.append((score, stamp, mid, display, body))
        ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
        return tuple(
            f"[本群历史原话 {datetime.fromisoformat(stamp).astimezone():%m-%d %H:%M} "
            f"{sender or '群友'} #{mid[-6:]}] {' '.join(body.split())[:240]}"
            for _, stamp, mid, sender, body in ranked[:limit])


def owner_group_context(store, context, conversation_id: str, query: str) -> tuple[str, ...]:
    """Explicit owner-only review grant; never opens member-private conversations."""
    principal = getattr(context, "principal", None)
    if not getattr(principal, "is_owner", False) or conversation_id.startswith("group:"):
        return ()
    grant = store.get("owner:group_review") or {}
    groups = grant.get("group_ids", []) if isinstance(grant, dict) else []
    if not groups or not any(cue in query for cue in ("群", *_PORTRAIT_CUES, *_LIKES)):
        return ()
    path = Path(getattr(store, "path", "")).with_name("group_archive.sqlite3")
    if not path.is_file():
        return ()
    from whale_core.memory.store import MemoryKind
    archive = GroupArchive(path)
    result = []
    try:
        for gid in groups:
            gid = str(gid)
            if not gid.isdigit():
                continue
            roster = load_directory(path, gid)
            matched = [p for p in (roster or {}).get("members", []) if any(
                n.casefold() in query.casefold() for n in [p["name"], *p["aliases"]])]
            if "群" not in query and not matched and (roster or not any(k in query for k in _PORTRAIT_CUES)):
                continue
            if matched and any(k in query for k in (*_LIKES,*_PORTRAIT_CUES)):
                loaded = []
                for member in matched:
                    uid = member["accounts"][0]
                    profile = saved_profile(store,gid,uid)
                    if not profile and not store.get(profile_key(gid,uid) + ":imported"):
                        continue
                    loaded.append(uid)
                    if not profile:
                        continue
                    name = member["name"]
                    p = dict(member,id=uid)
                    if any(k in query for k in (*_LIKES,"画像")):
                        claims = [c for _,c in scoped_claims(store,archive.db,gid,[p]) if c["current_preference"]]
                        result.extend(f"[{name}在本群的本人自述；{c['evidence_id']}] {c['summary']}" for c in claims[:4])
                        if not claims:
                            result.append(f"[本群喜好记忆] {name}尚无可核验的当前喜好自述。")
                    for kind, label in (("topics","反复提到的话题，仅作兴趣线索"),("expressions","常用表达")):
                        if kind == "expressions" and not any(k in query for k in _PORTRAIT_CUES):
                            continue
                        traits = [t for t in profile.content["traits"] if t["kind"]==kind][:3]
                        if traits:
                            result.append(f"[{name}的{label}] " + "、".join(
                                f"{t['label']}（{t['count']}条，依据 {t['evidence_ids'][0]}）" for t in traits))
                        if kind == "expressions" and any(k in query for k in ("举", "原话", "例子")):
                            original = read_portrait(path,gid,member) or {}
                            refs = {t["evidence_ids"][0] for t in traits[:2]}
                            for expr in original.get("expressions", []):
                                for e in expr["examples"]:
                                    if f"qq:group:{gid}:{e['message_id']}" in refs and e["sender_id"] in member["accounts"]:
                                        result.append(f"[{name}的原话 {e['sent_at']}] {e['text'][:150]}")
                if len(loaded) == len(matched):
                    continue
            if roster and any(k in query for k in _ROSTER_CUES) and not matched:
                result.append(name_hint(roster))
                continue
            # Only load the derived index for an explicit archive/profile question.
            if any(cue in query for cue in ("话题", "聊了什么", "讨论过", "关键词", "常聊", "群成员", "哪些群友", "骰子", *_PORTRAIT_CUES)):
                result.extend(digest_context(archive.db, gid, query, archive_path=path, roster=roster))
            result.extend(f"[授权查看群 {gid}] {quote}" for quote in
                          archive.search(gid, query, limit=4, require_recall=False))
            if not any(cue in query for cue in ("画像", "偏好", "喜欢", "性格")):
                continue
            people = archive.db.execute(
                "SELECT DISTINCT sender_id,sender_name FROM messages WHERE group_id=?", (gid,)).fetchall()
            for uid, name in people:
                canonical = next((p["name"] for p in matched if uid in p["accounts"]), "")
                if not canonical and (not name or (name not in query and uid not in query)):
                    continue
                binding = store.get(f"member:bind:anon:qq:{uid}") or {}
                sid = binding.get("space_id") if isinstance(binding, dict) else None
                if not sid:
                    continue
                for record in store.list_by_kind(MemoryKind.PREFERENCE, space_ids=(sid,)):
                    c = record.content
                    if (c.get("source") == "qce-group-import"
                            and str(c.get("evidence_id", "")).startswith(f"qq:group:{gid}:")
                            and c.get("status") == "active"):
                        result.append(f"[群 {gid} 导入画像候选；{canonical or name}；依据 {c.get('evidence_id')}] "
                                      f"{str(c.get('summary', ''))[:300]}")
    finally:
        archive.close()
    return tuple(result[:12])


def digest_context(db, gid, query, *, archive_path=None, roster=None):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='group_digest'").fetchone():
        return []
    row = db.execute("SELECT payload FROM group_digest WHERE group_id=?", (gid,)).fetchone()
    if not row:
        return []
    digest = json.loads(row[0])
    people = digest["members"]
    if roster:
        if any(term in query for term in _ROSTER_CUES) and not any(k in query for k in _PORTRAIT_CUES):
            return [name_hint(roster)]
        people = roster_members(people, roster)
    if "哪些群友" in query or "群成员" in query and any(k in query for k in ("谁", "哪些", "名单")):
        return ["[归档发送者昵称] " + "、".join(p["name"] or p["id"] for p in people)]
    named = [p for p in people if p["id"] == query or any(n and n.casefold() in query.casefold() for n in p["aliases"]) or
             re.search(r"(?<!\d)" + re.escape(p["id"]) + r"(?!\d)", query)]
    if named:
        result = []
        for p in named[:3]:
            result.append(f"[本群发言统计] {p['name']}：{p['count']} 条；发言关键词（仅代表讨论内容）："
                          + "、".join(p["keywords"]))
            if roster and any(k in query for k in _PORTRAIT_CUES):
                profile = read_portrait(archive_path, gid, p)
                if profile and profile["text_count"]:
                    terms = "、".join(f"{v['term']}（{v['count']}条/{v['days']}天）" for v in profile["expressions"][:4])
                    result.append(f"[群内写法，仅帮助理解] {p['name']} 的 {profile['text_count']} 条本人文字中，"
                                  f"中位长度 {profile['median_chars']} 字符；反复出现的表达：{terms or '样本不足'}。")
                    for expr in profile["expressions"][:2]:
                        example = expr["examples"][0]
                        result.append(f"[群内表达依据 {example['sent_at']} #{example['message_id']}] "
                                      f"{p['name']}：{example['text'][:120]}")
        return result
    if "画像" in query:
        return []
    ranked = sorted((s for s in digest["segments"] if s["keywords"]),
                    key=lambda s: sum(w.lower() in query.lower() for w in s["keywords"]), reverse=True)
    return [f"[归档片段 {s['start']}] 讨论关键词：" + "、".join(s["keywords"])
            + f"；参考消息 {s['example_id']}" for s in ranked[:3]]
