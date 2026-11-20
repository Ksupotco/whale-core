"""QQ table tools. Explicit commands bypass the model and the random group turn policy."""
from __future__ import annotations

import re
import hashlib
import secrets
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from whale_core.life.dicecho import SYSTEMS, find_modules, system_name
from whale_core.life.rulebooks import search_rules


HELP = (".r 2d6+3　掷骰（默认 d100）\n.r 2d20kh1+4　优势骰，kl1 为劣势\n"
        ".ra 侦查 60　CoC 7 版常规技能检定\n"
        ".模组 COC 黑水溪 / .模组 DND 秘野兔踪　按规则和标题搜索\n"
        ".推荐 DND　抽一张模组卡\n.规则 COC 急救　查本群导入的规则书\n"
        ".周荐 状态 / .周荐 预览 DND　查看排期或预览卡片\n"
        ".周荐 周五 20:00 轮换 / .周荐 关闭　主人设置本群每周推荐")
DIE = re.compile(r"(\d{0,2})d(\d{1,4})(?:k([hl])(\d{1,2}))?", re.I)
EXPRESSION = re.compile(rf"{DIE.pattern}(?:[+-](?:{DIE.pattern}|\d{{1,4}})){{0,7}}", re.I)


def roll(expression: str, randbelow=secrets.randbelow) -> str:
    expr = "".join(expression.lower().split()) or "d100"
    if not EXPRESSION.fullmatch(expr):
        return "骰式示例：.r d100、.r 2d6+3、.r 2d20kh1+4"
    total, breakdown = 0, []
    for sign, token in re.findall(r"([+-]?)([^+-]+)", expr):
        die = DIE.fullmatch(token)
        if die:
            count, faces = int(die[1] or 1), int(die[2])
            keep = int(die[4]) if die[4] is not None else count
            if not (1 <= keep <= count <= 30 and 2 <= faces <= 1000):
                return "每组可掷 1–30 颗 2–1000 面骰，保留数量须在骰数范围内"
            values = [randbelow(faces) + 1 for _ in range(count)]
            selected = sorted(values, reverse=die[3] == "h")[:keep]
            value = sum(selected)
            shown = "[" + ",".join(map(str, values)) + "]"
            if die[3]:
                shown += "取" + ",".join(map(str, selected))
        else:
            value, shown = int(token), token
        total += -value if sign == "-" else value
        breakdown.append(sign + shown)
    return f"{expr} = {''.join(breakdown)} = {total}"


def coc_check(skill: int, value: int) -> str:
    if value == 1:
        return "大成功"
    if value == 100 or (skill < 50 and value >= 96):
        return "大失败"
    for threshold, grade in ((skill // 5, "极难成功"), (skill // 2, "困难成功"), (skill, "成功")):
        if value <= threshold:
            return grade
    return "失败"


@dataclass(frozen=True)
class TableReply:
    text: str
    cover: str = ""

    def segments(self) -> list[dict]:
        parts = [{"type": "text", "data": {"text": self.text}}]
        if self.cover:
            parts.append({"type": "image", "data": {"file": self.cover}})
        return parts


class TableTools:
    def __init__(self, directory: Path, lookup=find_modules):
        self.rules = directory / "trpg-rules.sqlite3"
        self.weekly = directory / "trpg-weekly.sqlite3"
        self.lookup = lookup
        with closing(sqlite3.connect(self.weekly)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS schedules
                (group_id TEXT PRIMARY KEY, weekday INTEGER, minute INTEGER, system TEXT)""")
            db.execute("""CREATE TABLE IF NOT EXISTS deliveries
                (group_id TEXT, day TEXT, module TEXT, status TEXT, PRIMARY KEY(group_id,day))""")

    def command(self, text: str, group: str, *, manage: bool = False) -> TableReply | None:
        text = text.strip()
        if text.startswith(("。", "．")):
            text = "." + text[1:]
        if re.match(r"^\.r(?=\s|\d|d|$)", text, re.I):
            return TableReply(roll(text[2:]))
        if re.match(r"^\.ra(?:\s|$)", text, re.I):
            skill = re.fullmatch(r"\.ra\s+(?:(.{1,32}?)\s+)?(\d{1,3})", text, re.I)
            if not skill or not 1 <= int(skill[2]) <= 100:
                return TableReply("例如 .ra 侦查 60，技能值范围为 1–100")
            value = secrets.randbelow(100) + 1
            return TableReply(f"{skill[1] or '检定'}　{value}/{skill[2]}　{coc_check(int(skill[2]), value)}")
        if text == ".跑团":
            return TableReply(HELP)
        command = re.fullmatch(r"\.(模组|推荐|规则|周荐|周鉴)(.*)", text, re.S)
        if not command:
            return None
        cmd = "." + command[1]
        args = command[2].strip()
        if cmd in {".周荐", ".周鉴"}:
            preview = re.fullmatch(r"预览\s*(.*)", args, re.S)
            if preview:
                args = ("预览 " + preview[1]).strip()
            if args in {"", "状态", "预览"} or args.startswith("预览 "):
                return self.inspect_schedule(args, group, datetime.now(ZoneInfo("Asia/Shanghai")))
            return TableReply(self.schedule(args, group) if manage and group else "请由主人在目标群设置每周推荐")
        args = re.sub(r"^(COC|DND|D&D|龙与地下城|克苏鲁的呼唤)(?=[\u3400-\u9fff])", r"\1 ", args, flags=re.I)
        words = args.split(maxsplit=1)
        system = system_name(words[0]) if words else ""
        query = words[1] if len(words) == 2 else ""
        if system not in SYSTEMS.values():
            if cmd == ".模组":
                query, system = args, ""
            elif cmd == ".推荐" and not args:
                system = secrets.choice(list(SYSTEMS.values()))
            elif cmd != ".规则" or not query:
                return TableReply(HELP)
        if cmd == ".规则":
            if not group:
                return TableReply("请在导入规则书的群里查询")
            return TableReply(search_rules(self.rules, group, system, query) if query else "请输入规则术语")
        if cmd == ".模组" and not (system or query):
            return TableReply("例如 .模组 DND 秘野兔踪")
        try:
            mods = self.lookup(query, system)
        except (OSError, ValueError):
            return TableReply("骰声回响暂时连接失败，稍后可以再查")
        if not mods:
            return TableReply("没有查到匹配的模组")
        if cmd == ".模组" and len(mods) > 1 and mods[0].title.casefold() != query.casefold():
            return TableReply("\n".join(f"《{m.title}》 · {m.rule} · {m.players}\n{m.url}" for m in mods[:4]))
        mod = secrets.choice(mods) if cmd == ".推荐" else mods[0]
        return TableReply(mod.card(), mod.cover)

    def schedule(self, args: str, group: str) -> str:
        with closing(sqlite3.connect(self.weekly)) as db, db:
            if args == "关闭":
                db.execute("DELETE FROM schedules WHERE group_id=?", (group,))
                return "本群每周推荐已关闭"
            match = re.fullmatch(r"周([一二三四五六日天])\s+([01]?\d|2[0-3]):([0-5]\d)\s+(\S+)", args)
            if not match or system_name(match[4]) not in (*SYSTEMS.values(), "轮换"):
                return "用法：.周荐 周五 20:00 DND（可选 COC、DND 或轮换），或 .周荐 关闭"
            weekday = "一二三四五六日".index(match[1].replace("天", "日"))
            db.execute("INSERT OR REPLACE INTO schedules VALUES(?,?,?,?)",
                       (group, weekday, int(match[2]) * 60 + int(match[3]), system_name(match[4])))
        return f"本群周荐已设置为周{match[1]} {int(match[2]):02d}:{match[3]}（北京时间），系统为{system_name(match[4])}"

    @staticmethod
    def _slot(now: datetime, weekday: int, minute: int) -> datetime:
        slot = now.replace(hour=minute // 60, minute=minute % 60, second=0, microsecond=0)
        return slot - timedelta(days=(now.weekday() - weekday) % 7)

    @staticmethod
    def _system_for(setting: str, slot: datetime) -> str:
        if setting != "轮换":
            return setting
        week = (slot.date().toordinal() - 1) // 7
        return SYSTEMS[("COC", "DND")[week % 2]]

    def _pick(self, db, group, slot, system):
        recent = {r[0] for r in db.execute(
            "SELECT module FROM deliveries WHERE group_id=? ORDER BY day DESC LIMIT 10", (group,))}
        candidates = [m for m in self.lookup("", system) if m.id not in recent]
        # Same candidate pool and date give the same preview and scheduled card.
        seed = f"{group}/{slot.date()}/{system}/"
        return min(candidates, key=lambda m: hashlib.sha256((seed + m.id).encode()).digest(), default=None)

    def inspect_schedule(self, args: str, group: str, now: datetime) -> TableReply:
        if not group:
            return TableReply("请在目标跑团群查看周荐")
        with closing(sqlite3.connect(self.weekly)) as db:
            row = db.execute("SELECT weekday,minute,system FROM schedules WHERE group_id=?", (group,)).fetchone()
            slot, setting = now, ""
            if row:
                weekday, minute, setting = row
                slot = self._slot(now, weekday, minute)
                attempted = db.execute("SELECT 1 FROM deliveries WHERE group_id=? AND day=?",
                                       (group, slot.date().isoformat())).fetchone()
                if now >= slot + timedelta(minutes=10) or attempted:
                    slot += timedelta(weeks=1)
            if args in {"", "状态"}:
                if not row:
                    return TableReply("本群每周推荐未开启，可用 .周荐 预览 DND 查看样卡")
                return TableReply(f"本群周荐　{setting}\n下次　{slot:%Y-%m-%d %H:%M}（北京时间）"
                                  f"\n本期系统　{self._system_for(setting, slot)}")
            override = args.removeprefix("预览").strip()
            system = system_name(override) if override else self._system_for(setting, slot)
            if system not in SYSTEMS.values():
                return TableReply("请指定预览系统，例如 .周荐 预览 DND 或 .周荐 预览 COC")
            try:
                mod = self._pick(db, group, slot, system)
            except (ValueError, OSError):
                return TableReply("骰声回响暂时连接失败，稍后可以再查")
            return TableReply(mod.card(), mod.cover) if mod else TableReply("本期没有新的可推荐条目")

    def tick(self, now: datetime, allowed: set[str], send) -> None:
        """Local Beijing wall time. Claim each slot before sending to avoid duplicate posts."""
        with closing(sqlite3.connect(self.weekly)) as db:
            slots = db.execute("SELECT group_id,system,weekday,minute FROM schedules").fetchall()
            for group, setting, weekday, due in slots:
                if group not in allowed:
                    continue
                slot = self._slot(now, weekday, due)
                if not slot <= now < slot + timedelta(minutes=10):
                    continue
                day = slot.date().isoformat()
                if db.execute("SELECT 1 FROM deliveries WHERE group_id=? AND day=?", (group, day)).fetchone():
                    continue
                try:
                    mod = self._pick(db, group, slot, self._system_for(setting, slot))
                except (ValueError, OSError):
                    continue
                if not mod:
                    continue
                with db:
                    claimed = db.execute("""INSERT OR IGNORE INTO deliveries
                        SELECT group_id, ?, ?, 'pending' FROM schedules
                        WHERE group_id=? AND weekday=? AND minute=? AND system=?""",
                        (day, mod.id, group, weekday, due, setting)).rowcount
                if claimed:
                    try:
                        accepted = send(group, TableReply(mod.card(), mod.cover))
                    except (OSError, RuntimeError):
                        accepted = False
                    with db:
                        db.execute("UPDATE deliveries SET status=? WHERE group_id=? AND day=?",
                                   ("accepted" if accepted else "unknown", group, day))
