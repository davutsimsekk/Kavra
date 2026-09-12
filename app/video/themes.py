from dataclasses import dataclass
import hashlib

from app.models import Slide

Color = tuple[int, int, int]


@dataclass(frozen=True)
class ThemePreset:
    key: str
    label: str
    bg_start: Color
    bg_end: Color
    chapter_bg_start: Color
    chapter_bg_end: Color
    surface: Color
    surface_alt: Color
    border: Color
    title: Color
    body: Color
    muted: Color
    accent: Color
    accent_alt: Color
    code_style: str
    decoration: str


THEMES = {
    "white": ThemePreset(
        key="white", label="Beyaz Minimal",
        bg_start=(255, 255, 255), bg_end=(255, 255, 255),
        chapter_bg_start=(246, 249, 255), chapter_bg_end=(232, 240, 255),
        surface=(255, 255, 255), surface_alt=(248, 250, 253), border=(220, 227, 237),
        title=(22, 31, 46), body=(48, 60, 78), muted=(103, 116, 136),
        accent=(47, 105, 220), accent_alt=(94, 150, 255),
        code_style="friendly", decoration="lines",
    ),
    "notebook": ThemePreset(
        key="notebook", label="Notebook Açık",
        bg_start=(247, 250, 255), bg_end=(229, 238, 250),
        chapter_bg_start=(222, 235, 255), chapter_bg_end=(243, 247, 255),
        surface=(255, 255, 255), surface_alt=(244, 248, 254), border=(206, 220, 240),
        title=(25, 43, 72), body=(53, 70, 96), muted=(102, 121, 150),
        accent=(55, 104, 210), accent_alt=(113, 164, 255),
        code_style="friendly", decoration="dots",
    ),
    "midnight": ThemePreset(
        key="midnight", label="Gece Mavisi",
        bg_start=(10, 17, 31), bg_end=(18, 31, 54),
        chapter_bg_start=(14, 27, 54), chapter_bg_end=(34, 61, 105),
        surface=(22, 33, 53), surface_alt=(27, 41, 65), border=(55, 74, 104),
        title=(247, 250, 255), body=(215, 225, 239), muted=(139, 156, 181),
        accent=(91, 158, 255), accent_alt=(79, 218, 196),
        code_style="monokai", decoration="grid",
    ),
    "warm": ThemePreset(
        key="warm", label="Sıcak Kağıt",
        bg_start=(255, 251, 242), bg_end=(244, 234, 217),
        chapter_bg_start=(255, 239, 213), chapter_bg_end=(246, 213, 180),
        surface=(255, 253, 248), surface_alt=(250, 244, 232), border=(226, 210, 184),
        title=(68, 48, 37), body=(91, 69, 54), muted=(139, 112, 91),
        accent=(210, 91, 62), accent_alt=(236, 161, 80),
        code_style="friendly", decoration="arcs",
    ),
    "mint": ThemePreset(
        key="mint", label="Mint Akademik",
        bg_start=(241, 252, 249), bg_end=(218, 241, 236),
        chapter_bg_start=(216, 246, 239), chapter_bg_end=(237, 252, 248),
        surface=(251, 255, 254), surface_alt=(235, 248, 244), border=(192, 224, 216),
        title=(27, 66, 61), body=(53, 88, 81), muted=(96, 133, 125),
        accent=(30, 145, 126), accent_alt=(83, 191, 166),
        code_style="friendly", decoration="dots",
    ),
    "aurora": ThemePreset(
        key="aurora", label="Aurora",
        bg_start=(33, 24, 78), bg_end=(16, 72, 100),
        chapter_bg_start=(50, 31, 112), chapter_bg_end=(15, 105, 122),
        surface=(37, 39, 84), surface_alt=(44, 47, 95), border=(87, 91, 145),
        title=(255, 255, 255), body=(226, 231, 247), muted=(167, 179, 213),
        accent=(128, 113, 255), accent_alt=(65, 225, 196),
        code_style="monokai", decoration="aurora",
    ),
}

THEME_LABELS = {
    "auto": "Otomatik — slayt içeriğine göre",
    **{key: theme.label for key, theme in THEMES.items()},
}


def resolve_theme_key(preset: str, slide: Slide, index: int = 1) -> str:
    if preset != "auto":
        return preset if preset in THEMES else "notebook"
    if slide.level == "chapter":
        return "aurora"
    if slide.code:
        return "midnight"

    # Aynı başlık her çalıştırmada aynı temayı alır; Python'un rastgele hash'i kullanılmaz.
    digest = hashlib.sha256(f"{index}:{slide.title}".encode("utf-8")).digest()[0]
    return ("notebook", "white", "mint", "warm")[digest % 4]


def resolve_theme(preset: str, slide: Slide, index: int = 1) -> ThemePreset:
    return THEMES[resolve_theme_key(preset, slide, index)]
