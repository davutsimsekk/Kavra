from dataclasses import dataclass, field


@dataclass
class RawSection:
    breadcrumb: str
    title: str
    text: str
    code_blocks: list[str] = field(default_factory=list)
    level: int = 3


@dataclass
class WordTiming:
    text: str
    start: float
    end: float


@dataclass
class SynthResult:
    duration: float
    words: list[WordTiming] | None = None


@dataclass
class Slide:
    title: str
    bullets: list[str] = field(default_factory=list)
    code: str | None = None
    narration: str = ""
    level: str = "topic"  # "chapter" (divider slide) or "topic"

    def to_dict(self):
        return {
            "title": self.title,
            "bullets": self.bullets,
            "code": self.code,
            "narration": self.narration,
            "level": self.level,
        }

    @staticmethod
    def from_dict(d: dict) -> "Slide":
        return Slide(
            title=d.get("title", ""),
            bullets=d.get("bullets", []) or [],
            code=d.get("code"),
            narration=d.get("narration", ""),
            level=d.get("level", "topic"),
        )
