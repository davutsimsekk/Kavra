from pathlib import Path

from app.config import PROMPTS_DIR
from app.models import RawSection

TEMPLATE_PATH = PROMPTS_DIR / "lecture_script_prompt.md"


def raw_sections_to_text(sections: list[RawSection]) -> str:
    parts = []
    for s in sections:
        header = f"## {s.title}" if not s.breadcrumb else f"## {s.breadcrumb} > {s.title}"
        parts.append(header)
        if s.text:
            parts.append(s.text)
        for cb in s.code_blocks:
            parts.append(f"```\n{cb}\n```")
        parts.append("")
    return "\n".join(parts)


def build_prompt(sections: list[RawSection], style_note: str = "") -> str:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    raw = raw_sections_to_text(sections)
    prompt = template.replace("{RAW_CONTENT}", raw)
    if style_note:
        prompt += f"\n\nEk üslup notu: {style_note}\n"
    return prompt
