import re
from pathlib import Path

from app.models import RawSection

HEADING_RE = re.compile(r"^(#{1,4})\s+(.*)$")
CODE_FENCE_RE = re.compile(r"```[a-zA-Z0-9]*\n(.*?)```", re.DOTALL)


def parse(path: Path) -> list[RawSection]:
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")

    sections: list[RawSection] = []
    stack: list[str] = []  # heading text per level for breadcrumb
    cur_title = None
    cur_level = 3
    buf: list[str] = []

    def flush():
        nonlocal buf, cur_title
        if cur_title is None:
            return
        body = "\n".join(buf).strip()
        if not body and not stack:
            return
        code_blocks = [m.strip() for m in CODE_FENCE_RE.findall(body)]
        clean_text = CODE_FENCE_RE.sub("", body).strip()
        breadcrumb = " > ".join(stack[:-1]) if len(stack) > 1 else ""
        sections.append(RawSection(
            breadcrumb=breadcrumb,
            title=cur_title,
            text=clean_text,
            code_blocks=code_blocks,
            level=cur_level,
        ))
        buf = []

    for line in lines:
        m = HEADING_RE.match(line)
        if m:
            flush()
            level = len(m.group(1))
            title = m.group(2).strip()
            title = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", title)  # strip md links
            stack = stack[: level - 1] + [title]
            cur_title = title
            cur_level = level
        else:
            if line.strip().lower().startswith("| ---") or line.strip() == "---":
                continue
            buf.append(line)
    flush()

    skip_titles = {"i̇çindekiler", "içindekiler", "table of contents"}
    return [
        s for s in sections
        if (s.text or s.code_blocks) and s.title.strip().lower() not in skip_titles
    ]
