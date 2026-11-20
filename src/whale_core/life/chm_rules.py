"""Read static rulebook chapters from compiled HTML Help archives."""
from __future__ import annotations

import codecs
import re
import shutil
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path


def _decode(raw: bytes) -> str:
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("gb18030", errors="replace")


class _Sections(HTMLParser):
    """Keep headings and standalone bold glossary terms attached to their definitions."""
    blocks = {"p", "div", "li", "tr", "dt", "dd", "br", "table", "section", "body"}
    heads = {"h1", "h2", "h3", "h4", "h5", "h6"}
    hidden = {"head", "script", "style", "object", "iframe"}

    def __init__(self):
        super().__init__()
        self.skip = self.bold = 0
        self.heading = self.plain = False
        self.buffer = []
        self.sections = []
        self.label = ""
        self.paragraphs = []

    def _finish_section(self):
        if self.paragraphs:
            self.sections.append((self.label, "\n".join(self.paragraphs)))
            self.paragraphs.clear()

    def _line(self):
        line = " ".join("".join(self.buffer).split())
        if line:
            if (self.heading or not self.plain) and len(line) <= 100 and not line.endswith("。"):
                self._finish_section()
                self.label = line
            else:
                self.paragraphs.append(line)
        self.buffer.clear()
        self.plain = self.heading = False

    def handle_starttag(self, tag, attrs):
        if tag in self.hidden:
            self.skip += 1
        if self.skip:
            return
        if tag in self.heads or tag in self.blocks:
            self._line()
            self.heading = tag in self.heads
        elif tag in {"strong", "b"}:
            self.bold += 1
        elif tag in {"td", "th"}:
            self.buffer.append(" ")

    def handle_endtag(self, tag):
        if self.skip:
            self.skip -= int(tag in self.hidden)
        elif tag in self.heads or tag in self.blocks:
            self._line()
        elif tag in {"strong", "b"}:
            self.bold = max(0, self.bold - 1)

    def handle_data(self, data):
        if not self.skip:
            self.buffer.append(data)
            if data.strip() and not self.bold:
                self.plain = True

    def close(self):
        super().close()
        self._line()
        self._finish_section()


def chapter_sections(path: Path, root: Path):
    parser = _Sections()
    parser.feed(_decode(path.read_bytes()))
    parser.close()
    chapter = " › ".join(path.relative_to(root).with_suffix("").parts)
    for heading, body in parser.sections:
        location = f"{chapter} › {heading}" if heading and heading != path.stem else chapter
        yield location, body


def chm_sections(book: Path, volumes: tuple[str, ...], seven_zip: str | None):
    tool = seven_zip or shutil.which("7z") or shutil.which("7zz")
    if not tool:
        raise ValueError("读取 CHM 需要 7-Zip，请用 --seven-zip 指定已有可执行文件的位置")
    if any(not v or re.search(r'[\\/:*?"<>|]', v) or v in (".", "..") or v[0] in "-@" for v in volumes):
        raise ValueError("卷名应是 CHM 内的一级目录名")
    with tempfile.TemporaryDirectory(prefix="jax-chm-") as tmp:
        root = Path(tmp)
        listing = subprocess.run([tool, "l", "-slt", "-sccUTF-8", str(book.resolve())],
                                 capture_output=True, check=True, timeout=60).stdout.decode("utf-8")
        members = []
        found = set()
        for line in listing.splitlines():
            if not line.startswith("Path = "):
                continue
            rel = line[7:].replace("\\", "/")
            parts = rel.split("/")
            if not rel.lower().endswith((".htm", ".html")) or (volumes and parts[0] not in volumes):
                continue
            if any(not p or p in (".", "..") or ":" in p for p in parts):
                raise ValueError("CHM 章节路径无效")
            members.append(rel)
            found.add(parts[0])
        missing = set(volumes) - found
        if missing or not members:
            raise ValueError("CHM 中找不到规则卷：" + "、".join(sorted(missing or volumes)))
        manifest = root / "members.txt"
        manifest.write_text("\n".join(members), encoding="utf-8")
        subprocess.run([tool, "x", "-y", "-spd", "-scsUTF-8", "-o" + tmp, str(book.resolve()),
                        "@" + str(manifest)], capture_output=True, check=True, timeout=180)
        for rel in members:
            path = root / rel
            if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
                raise ValueError(f"无法读取 CHM 章节：{rel}")
            yield from chapter_sections(path, root)
