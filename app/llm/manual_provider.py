import json
from pathlib import Path

from app.llm.base import NarrationGenerator, extract_json_array
from app.llm.prompt_template import build_prompt
from app.models import RawSection, Slide


class ManualNarrationGenerator(NarrationGenerator):
    """LLM API kullanmadan: kullanıcı prompt'u kendi başına bir sohbet arayüzüne
    (Gemini web, ChatGPT, Claude...) yapıştırır, dönen JSON'u burada dosyadan
    veya doğrudan metinden okuruz."""

    def __init__(self, json_text: str | None = None, json_path: str | Path | None = None):
        self.json_text = json_text
        self.json_path = Path(json_path) if json_path else None

    @staticmethod
    def build_prompt_for(sections: list[RawSection], style_note: str = "") -> str:
        return build_prompt(sections, style_note)

    def generate(self, sections: list[RawSection], style_note: str = "") -> list[Slide]:
        if self.json_path and self.json_path.exists():
            text = self.json_path.read_text(encoding="utf-8")
        elif self.json_text:
            text = self.json_text
        else:
            raise RuntimeError("Manuel modda önce bir JSON dosyası ya da metni sağlanmalı.")
        data = extract_json_array(text) if not text.strip().startswith("[") else json.loads(text)
        return [Slide.from_dict(d) for d in data]
