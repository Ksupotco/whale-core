"""Local, group-scoped rulebook passages with page or paragraph citations."""
from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import subprocess
from collections import Counter
from contextlib import closing
from pathlib import Path

from whale_core.life.chm_rules import chm_sections
from whale_core.life.dicecho import system_name


def _passages(path: Path, volumes: tuple[str, ...], seven_zip: str | None):
    if path.suffix.lower() == ".chm":
        units = chm_sections(path, volumes, seven_zip)
    elif path.suffix.lower() == ".pdf":
        tool = shutil.which("pdftotext")
        if not tool:
            raise ValueError("此机器缺少 pdftotext，可导入 UTF-8 的 TXT 或 Markdown")
        raw = subprocess.run([tool, "-enc", "UTF-8", str(path.resolve()), "-"],
                             capture_output=True, check=True, timeout=120).stdout.decode("utf-8")
        units = ((f"PDF 第 {n} 页", page) for n, page in enumerate(raw.split("\f"), 1))
    elif path.suffix.lower() in {".txt", ".md"}:
        raw = path.read_text(encoding="utf-8-sig")
        units = ((f"第 {n} 段", para) for n, para in enumerate(re.split(r"\n\s*\n", raw), 1))
    else:
        raise ValueError("支持 CHM、文字型 PDF、TXT 和 Markdown")
    for loc, unit in units:
        compact = re.sub(r"(?<=[\u3400-\u9fff])\s*\n\s*(?=[\u3400-\u9fff])", "", unit)
        compact = " ".join(compact.split())
        for start in range(0, len(compact), 500):
            yield loc, compact[start:start + 580]


def import_book(db_path: Path, group: str, system: str, edition: str, path: Path,
                title: str = "", *, volumes: tuple[str, ...] = (), seven_zip: str | None = None) -> int:
    if not group.isdecimal() or not edition.strip() or not system.strip():
        raise ValueError("请指定群号、规则系统和版本")
    if volumes and path.suffix.lower() != ".chm":
        raise ValueError("按卷导入仅适用于 CHM")
    pages = list(_passages(path, volumes, seven_zip))
    if not pages:
        raise ValueError("没有可检索文字，扫描版请先完成 OCR")
    book = title.strip() or path.stem
    key = (group, system_name(system), edition, book)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("""CREATE TABLE IF NOT EXISTS passages
            (group_id TEXT, system TEXT, edition TEXT, book TEXT, location TEXT, body TEXT)""")
        db.execute("CREATE INDEX IF NOT EXISTS book_scope ON passages(group_id,system)")
        db.execute("DELETE FROM passages WHERE group_id=? AND system=? AND edition=? AND book=?", key)
        db.executemany("INSERT INTO passages VALUES(?,?,?,?,?,?)",
                       ((*key, loc, body) for loc, body in pages))
    return len(pages)


def _terms(text: str) -> set[str]:
    terms = set()
    for word in re.findall(r"[\u3400-\u9fff]+|[a-z0-9]+", text.casefold()):
        if len(word) > 1 and ord(word[0]) >= 0x3400:
            terms.update(word[i:i + 2] for i in range(len(word) - 1))
        else:
            terms.add(word)
    return terms


def search_rules(path: Path, group: str, system: str, question: str) -> str:
    empty = "这个群还没有导入该系统的规则书"
    if not path.is_file():
        return empty
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        rows = db.execute("SELECT edition,book,location,body FROM passages WHERE group_id=? AND system=?",
                          (group, system_name(system))).fetchall()
    if not rows:
        return empty
    question = question.strip(" ？?")
    terms = _terms(question) - {"如何", "怎么", "什么", "时候", "规则", "解释", "可以", "进行"}
    if not terms:
        return "请加一个要查的术语，例如 .规则 DND 借机攻击"
    needle = question.casefold()
    # CHM chapter headings can contain the only mention of the term being defined.
    headings = [r[2].rsplit(" › ", 1)[-1].casefold() if " › " in r[2] else "" for r in rows]
    bodies = [r[3].casefold() for r in rows]
    matches = [{t for t in terms if t in body or t in head} for body, head in zip(bodies, headings)]
    frequencies = Counter(term for hits in matches for term in hits)
    # Prefer exact terms and terms that identify a specific passage over common words.
    ranking = sorted((i for i, hits in enumerate(matches) if hits),
                     key=lambda i: (re.split(r"[\s｜|（(]|(?<=[\u3400-\u9fff])(?=[a-z])", headings[i], maxsplit=1)[0] == needle,
                                    needle in headings[i], needle in bodies[i],
                                    sum(len(t) for t in matches[i] if t in headings[i]),
                                    sum(1 / frequencies[t] for t in matches[i])), reverse=True)
    if not ranking:
        return "导入的规则书中没有找到对应原文，试试书中的术语"
    lines, seen = [], set()
    for i in ranking:
        edition, book, loc, body = rows[i]
        if (edition, book, loc) in seen:
            continue
        seen.add((edition, book, loc))
        term = min(matches[i], key=lambda t: (frequencies[t], t))
        offset = 0 if needle in headings[i] else bodies[i].find(needle)
        if offset < 0:
            offset = bodies[i].find(term)
        start = max(0, offset - 40)
        snippet = ("…" if start else "") + body[start:start + 190]
        lines.append(f"《{book}》 · {edition} · {loc}\n{snippet}" + ("…" if start + 190 < len(body) else ""))
        if len(lines) == 2:
            break
    return "\n\n".join(lines)


def main() -> None:
    cli = argparse.ArgumentParser(description="为指定 QQ 群导入一本规则书")
    cli.add_argument("file", type=Path)
    cli.add_argument("--config-dir", required=True, type=Path)
    cli.add_argument("--group", required=True)
    cli.add_argument("--system", required=True)
    cli.add_argument("--edition", required=True)
    cli.add_argument("--title", default="")
    cli.add_argument("--volume", action="append", default=[], help="CHM 内的一级目录，可重复")
    cli.add_argument("--seven-zip", help="用于解包 CHM 的 7z 可执行文件")
    args = cli.parse_args()
    try:
        count = import_book(args.config_dir / "trpg-rules.sqlite3", args.group, args.system,
                            args.edition, args.file, args.title, volumes=tuple(args.volume),
                            seven_zip=args.seven_zip)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        cli.exit(1, f"导入失败：{exc}\n")
    print(f"已索引 {count} 段规则原文")


if __name__ == "__main__":
    main()
