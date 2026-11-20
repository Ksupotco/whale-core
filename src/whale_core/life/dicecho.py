"""Read-only access to Dicecho's public module catalogue."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SYSTEMS = {"COC": "克苏鲁的呼唤", "DND": "龙与地下城"}


def system_name(value: str) -> str:
    key = value.upper().replace("D&D", "DND")
    return SYSTEMS.get(key, value)


@dataclass(frozen=True)
class Module:
    id: str
    title: str
    rule: str
    author: str
    players: str
    tags: str
    cover: str
    intro: str
    duration: str
    release_date: str = ""

    @property
    def url(self) -> str:
        return f"https://www.dicecho.com/zh/scenario/{self.id}"

    def card(self) -> str:
        fields = (("模组名称", f"《{self.title}》"), ("作者", self.author),
                  ("规则系统", self.rule), ("推荐人数", self.players), ("预计时长", self.duration),
                  ("发布日期", self.release_date), ("题材标签", self.tags), ("玩家导入", self.intro),
                  ("来源", f"骰声回响 {self.url}"))
        return "\n".join(f"【{label}】{' '.join(value.split()) or '未标注'}" for label, value in fields)


def _player_intro(description: str) -> str:
    marked = re.search(r"(?:玩家导入|玩家简介|公开导入|剧情导入|模组简介|(?:^|\n)[ ●■]*导入)[：:]\s*([^\r\n]+)", description)
    lines = [marked[1]] if marked else description.splitlines()[:4]
    for line in lines:
        for sentence in re.findall(r"[^。！？!?]+[。！？!?]?", line.strip()):
            if re.search(r"KP|DM|GM|守[密秘]人|剧透|真相|幕后|凶手|最终|结局|其实|实际上|https?://", sentence, re.I):
                break
            if (marked and len(sentence) >= 4) or (len(sentence) >= 12 and re.search(
                r"冒险者|调查员|探索者|故事发生|故事开始|你们|某天|某日", sentence
            )):
                return sentence if len(sentence) <= 140 else sentence[:139] + "…"
    return ""


def _module(row: dict) -> Module | None:
    ident = str(row.get("_id", ""))
    title = row.get("title")
    if not re.fullmatch("[a-f0-9]{24}", ident) or not isinstance(title, str) or not title.strip():
        return None
    players = [n for n in row.get("playerNumber") or [] if type(n) is int and n > 0]
    count = "–".join(map(str, sorted(set(players)))) + " 人" if players else ""
    cover = str(row.get("coverUrl") or "")
    if not cover.startswith("https://file.dicecho.com/"):
        cover = ""
    description = str(row.get("description") or "")
    intro = _player_intro(description)
    duration = re.search(r"(?:预计时长|预计时间|游戏时长|游玩时长|游戏时间|游玩时间)[：:][ \t]*([^\n\r]{1,30})", description)
    tags = "、".join(t[:24] for t in (row.get("tags") or [])[:4] if isinstance(t, str))
    date = re.match(r"\d{4}-\d{2}-\d{2}", str(row.get("releaseDate") or ""))
    return Module(ident, title.strip()[:100], str(row.get("moduleRule") or ""),
                  str((row.get("author") or {}).get("nickName") or "")[:60],
                  count, tags, cover, intro, duration[1].strip() if duration else "", date[0] if date else "")


def find_modules(keyword: str = "", system: str = "") -> list[Module]:
    params = {"pageSize": "20"}
    if keyword:
        params["keyword"] = keyword[:150]
    if system:
        params["filter[moduleRule]"] = system
    if not keyword:
        params["sort[rateAvg]"] = "-1"
    req = Request("https://api.dicecho.com/api/mod?" + urlencode(params),
                  headers={"Accept": "application/json", "User-Agent": "Whale-Jax/1.0"})
    with urlopen(req, timeout=6) as response:
        payload = json.load(response)
    if payload.get("success") is not True or not isinstance(payload.get("data"), dict):
        raise ValueError("catalogue unavailable")
    rows = payload["data"].get("data")
    if not isinstance(rows, list):
        raise ValueError("invalid catalogue response")
    modules = [mod for row in rows if isinstance(row, dict) and (mod := _module(row))]
    if system:
        modules = [mod for mod in modules if mod.rule == system]
    if keyword:
        modules.sort(key=lambda mod: mod.title.casefold() != keyword.casefold())
    return modules
