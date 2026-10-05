import io
import os
import re
from dataclasses import replace
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps, ImageStat
from pygments import highlight
from pygments.formatters import ImageFormatter
from pygments.lexers import CppLexer

from app.models import Slide
from app.video.themes import ThemePreset, resolve_theme

_CUSTOM_FONT_DIR = Path(os.environ["KAVRA_FONT_DIR"]).expanduser() if os.environ.get("KAVRA_FONT_DIR") else None


def _font_file(*relative_candidates: str) -> Path:
    roots = [
        *([_CUSTOM_FONT_DIR] if _CUSTOM_FONT_DIR else []),
        Path(r"C:\Windows\Fonts"),
        Path("/usr/share/fonts/truetype/dejavu"),
        Path("/usr/share/fonts/truetype/liberation2"),
    ]
    for root in roots:
        for candidate in relative_candidates:
            path = root / candidate
            if path.is_file():
                return path
    searched = ", ".join(str(root / name) for root in roots for name in relative_candidates)
    raise RuntimeError(
        "Kavra slayt fontları bulunamadı. KAVRA_FONT_DIR ayarla veya DejaVu Sans kur. "
        f"Aranan yollar: {searched}"
    )


FONT_TITLE = _font_file("segoeuib.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf")
FONT_BODY = _font_file("segoeui.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf")
FONT_SMALL = _font_file("segoeuisl.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf")
FONT_CODE = _font_file("consola.ttf", "DejaVuSansMono.ttf", "LiberationMono-Regular.ttf")


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size)


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], amount: float):
    return tuple(round(x + (y - x) * amount) for x, y in zip(a, b))


def _gradient(width: int, height: int, start, end) -> Image.Image:
    strip = Image.new("RGB", (1, height))
    px = strip.load()
    denom = max(height - 1, 1)
    for y in range(height):
        px[0, y] = _mix(start, end, y / denom)
    return strip.resize((width, height))


def _decorate(img: Image.Image, theme: ThemePreset):
    width, height = img.size
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    accent = (*theme.accent, 24)
    accent_alt = (*theme.accent_alt, 20)

    if theme.decoration == "grid":
        for x in range(0, width, 96):
            draw.line((x, 0, x, height), fill=accent, width=1)
        for y in range(0, height, 96):
            draw.line((0, y, width, y), fill=accent, width=1)
    elif theme.decoration == "dots":
        for x in range(42, width, 72):
            for y in range(42, height, 72):
                draw.ellipse((x, y, x + 4, y + 4), fill=accent)
    elif theme.decoration == "lines":
        for offset in range(-height, width, 170):
            draw.line((offset, height, offset + height, 0), fill=accent, width=2)
    elif theme.decoration == "arcs":
        draw.ellipse((-220, height - 430, 420, height + 210), outline=accent, width=10)
        draw.ellipse((width - 460, -280, width + 180, 360), outline=accent_alt, width=12)
    elif theme.decoration == "aurora":
        draw.ellipse((-260, -260, 760, 620), fill=accent)
        draw.ellipse((width - 720, height - 660, width + 240, height + 240), fill=accent_alt)

    composed = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    img.paste(composed)


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines = []
    for paragraph in (text.splitlines() or [""]):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if draw.textlength(candidate, font=font) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def _ellipsize(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    suffix = "…"
    while text and draw.textlength(text + suffix, font=font) > max_width:
        text = text[:-1]
    return text.rstrip() + suffix


def _rounded_panel(draw, box, fill, border, radius=28, shadow=True):
    x1, y1, x2, y2 = box
    if shadow:
        draw.rounded_rectangle(
            (x1 + 8, y1 + 10, x2 + 8, y2 + 10),
            radius=radius,
            fill=(0, 0, 0, 25),
        )
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=border, width=2)


def render_code_image(code: str, max_width_px: int, max_height_px: int,
                      style: str = "monokai") -> Image.Image:
    formatter = ImageFormatter(
        font_name=str(FONT_CODE), font_size=30, line_numbers=False,
        style=style, image_pad=24, line_pad=6,
    )
    png_bytes = highlight(code, CppLexer(), formatter)
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    img.thumbnail((max_width_px, max_height_px), Image.Resampling.LANCZOS)
    return img


def _draw_progress(draw, theme: ThemePreset, index: int, total: int, width: int, height: int):
    left, right, y = 96, width - 96, height - 43
    draw.rounded_rectangle((left, y, right, y + 8), radius=4, fill=theme.border)
    progress_right = left + round((right - left) * index / max(total, 1))
    draw.rounded_rectangle((left, y, progress_right, y + 8), radius=4, fill=theme.accent)


def _draw_chapter(img: Image.Image, draw: ImageDraw.ImageDraw, slide: Slide,
                  index: int, total: int, theme: ThemePreset):
    width, height = img.size
    pill_font = _font(FONT_TITLE, 24)
    title_font = _font(FONT_TITLE, 82 if len(slide.title) < 65 else 68)
    body_font = _font(FONT_BODY, 30)

    label = f"BÖLÜM  •  {index:02d}"
    label_w = int(draw.textlength(label, font=pill_font)) + 52
    label_x = (width - label_w) // 2
    draw.rounded_rectangle(
        (label_x, 150, label_x + label_w, 202), radius=26,
        fill=theme.surface_alt, outline=theme.border, width=2,
    )
    draw.text((label_x + 26, 161), label, font=pill_font, fill=theme.accent_alt)

    lines = _wrap_text(draw, slide.title, title_font, width - 300)[:3]
    line_h = title_font.size + 14
    title_y = 315 - max(len(lines) - 1, 0) * line_h // 2
    for line in lines:
        line = _ellipsize(draw, line, title_font, width - 300)
        line_w = draw.textlength(line, font=title_font)
        draw.text(((width - line_w) / 2, title_y), line, font=title_font, fill=theme.title)
        title_y += line_h

    if slide.bullets:
        chip_y = max(title_y + 72, 640)
        for bullet in slide.bullets[:3]:
            text = _ellipsize(draw, bullet, body_font, width - 560)
            text_w = int(draw.textlength(text, font=body_font))
            chip_w = text_w + 76
            chip_x = (width - chip_w) // 2
            draw.rounded_rectangle(
                (chip_x, chip_y, chip_x + chip_w, chip_y + 58), radius=29,
                fill=theme.surface_alt, outline=theme.border, width=1,
            )
            draw.ellipse((chip_x + 24, chip_y + 23, chip_x + 34, chip_y + 33),
                         fill=theme.accent)
            draw.text((chip_x + 48, chip_y + 9), text, font=body_font, fill=theme.body)
            chip_y += 72

    _draw_progress(draw, theme, index, total, width, height)


# Kart/adım panelinde okunaklı kalması için hem madde kartlarının (_draw_bullet_cards)
# hem numaralı akışın (_draw_process_layout, çağıran yerde uygulanır) gösterdiği azami
# öğe sayısı — app.quality_gate bu sınırı aşan içeriğin render'da SESSİZCE kırpılacağını
# render'dan ÖNCE uyarabilmek için bu sabiti aynen içe aktarır (bkz. o modüldeki yorum).
MAX_VISIBLE_BULLET_CARDS = 6


def _draw_bullet_cards(draw: ImageDraw.ImageDraw, bullets: list[str], box,
                       theme: ThemePreset, compact: bool):
    x1, y1, x2, y2 = box
    items = bullets[:MAX_VISIBLE_BULLET_CARDS]
    if not items:
        items = ["Bu bölümün temel fikirleri anlatımla birlikte ele alınacak."]
    gap = 14
    available_h = y2 - y1
    card_h = min(150, int((available_h - gap * (len(items) - 1)) / len(items)))
    used_h = card_h * len(items) + gap * (len(items) - 1)
    font_size = 27 if compact or len(items) >= 5 else 31
    bullet_font = _font(FONT_BODY, font_size)
    number_font = _font(FONT_TITLE, 20)

    y = y1 + max((available_h - used_h) // 2, 0)
    for number, bullet in enumerate(items, start=1):
        card_bottom = min(y + card_h, y2)
        _rounded_panel(
            draw, (x1, y, x2, card_bottom), theme.surface_alt, theme.border,
            radius=22, shadow=False,
        )
        badge_size = 44
        badge_y = y + (card_bottom - y - badge_size) // 2
        draw.ellipse(
            (x1 + 20, badge_y, x1 + 20 + badge_size, badge_y + badge_size),
            fill=theme.accent,
        )
        number_text = str(number)
        nw = draw.textlength(number_text, font=number_font)
        draw.text(
            (x1 + 42 - nw / 2, badge_y + 8), number_text,
            font=number_font, fill=(255, 255, 255),
        )

        text_x = x1 + 84
        max_width = x2 - text_x - 24
        lines = _wrap_text(draw, bullet, bullet_font, max_width)
        if len(lines) > 2:
            lines = lines[:2]
            lines[-1] = _ellipsize(draw, lines[-1], bullet_font, max_width)
        line_h = font_size + 8
        text_y = y + max((card_bottom - y - len(lines) * line_h) // 2, 8)
        for line in lines:
            line = _ellipsize(draw, line, bullet_font, max_width)
            draw.text((text_x, text_y), line, font=bullet_font, fill=theme.body)
            text_y += line_h
        y = card_bottom + gap


# LLM'in "layout" alanına yazabileceği, içerik formatına göre pedagojik olarak farklı bir
# görsel üreten değerler — bkz. app/models.py Slide.layout ve prompts/lecture_script_prompt.md.
# Tanınmayan/eksik bir değer "bullets"a düşer (bkz. _draw_topic'in sonundaki dispatch).
_CONTENT_LAYOUTS = {"bullets", "emphasis", "definition", "comparison", "process", "formula", "callout"}


def _draw_emphasis_text(draw: ImageDraw.ImageDraw, text: str, box, theme: ThemePreset):
    """"emphasis" layout: madde madde bölünmesi zorlama olan tek bir kavramsal cümleyi
    büyük ve ortalanmış gösterir (alıntı/quote kartı gibi), numaralı liste yok."""
    x1, y1, x2, y2 = box
    box_w = x2 - x1
    size = 56 if len(text) < 60 else 46 if len(text) < 110 else 38
    font = _font(FONT_TITLE, size)
    max_width = box_w - 160
    lines = _wrap_text(draw, text, font, max_width)[:4]
    line_h = size + 16
    block_h = len(lines) * line_h
    start_y = y1 + max((y2 - y1 - block_h) // 2, 0)
    for i, line in enumerate(lines):
        line = _ellipsize(draw, line, font, max_width)
        line_w = draw.textlength(line, font=font)
        draw.text(((x1 + x2 - line_w) / 2, start_y + i * line_h), line, font=font, fill=theme.title)
    rule_w = 96
    rule_y = min(start_y + block_h + 28, y2 - 10)
    rule_x = (x1 + x2 - rule_w) // 2
    draw.rounded_rectangle((rule_x, rule_y, rule_x + rule_w, rule_y + 6), radius=3, fill=theme.accent)


def _parse_definition_pairs(bullets: list[str]) -> list[tuple[str, str]] | None:
    """"definition" layout'un beklediği "Terim: Tanım" yapısını doğrular. LLM formatı
    tutturamazsa (kolon yok, boş terim/tanım, 3'ten fazla öğe) None döner — çağıran yer
    bunu "bullets" formatına düşmesi gerektiğinin sinyali olarak kullanır."""
    if not bullets or len(bullets) > 3:
        return None
    pairs = []
    for item in bullets:
        if ":" not in item:
            return None
        term, definition = item.split(":", 1)
        term, definition = term.strip(), definition.strip()
        if not term or not definition:
            return None
        pairs.append((term, definition))
    return pairs


def _draw_definition_layout(draw: ImageDraw.ImageDraw, pairs: list[tuple[str, str]], box, theme: ThemePreset):
    x1, y1, x2, y2 = box
    available_h = y2 - y1
    row_h = available_h / len(pairs)
    term_font = _font(FONT_TITLE, 40 if len(pairs) == 1 else 32)
    def_font = _font(FONT_BODY, 28 if len(pairs) == 1 else 25)
    max_width = x2 - x1 - 40
    for i, (term, definition) in enumerate(pairs):
        term_lines = _wrap_text(draw, term, term_font, max_width)[:1]
        def_lines = _wrap_text(draw, definition, def_font, max_width)[:3]
        block_h = len(term_lines) * (term_font.size + 6) + 10 + len(def_lines) * (def_font.size + 8)
        row_top = y1 + row_h * i
        ty = row_top + max((row_h - block_h) / 2, 0)
        for line in term_lines:
            draw.text((x1 + 20, ty), line, font=term_font, fill=theme.accent)
            ty += term_font.size + 6
        ty += 10
        for line in def_lines:
            line = _ellipsize(draw, line, def_font, max_width)
            draw.text((x1 + 20, ty), line, font=def_font, fill=theme.body)
            ty += def_font.size + 8
        if i < len(pairs) - 1:
            divider_y = row_top + row_h - 14
            draw.line((x1 + 20, divider_y, x2 - 20, divider_y), fill=theme.border, width=1)


def _draw_formula_layout(draw: ImageDraw.ImageDraw, formula: str, explanation: str, box, theme: ThemePreset):
    """"formula" layout: bir matematik/kod ifadesini monospace fontla büyük ve ortalanmış
    gösterir (accent renkte, "bu bir ifade" hissi versin diye), altında kısa bir açıklama."""
    x1, y1, x2, y2 = box
    max_width = (x2 - x1) - 160
    formula_size = 64 if len(formula) < 30 else 50 if len(formula) < 60 else 40
    formula_font = _font(FONT_CODE, formula_size)
    formula_lines = _wrap_text(draw, formula, formula_font, max_width)[:2]

    explanation_font = _font(FONT_BODY, 28)
    explanation_lines = _wrap_text(draw, explanation, explanation_font, max_width)[:3] if explanation else []

    formula_block_h = len(formula_lines) * (formula_size + 14)
    explanation_block_h = (24 + len(explanation_lines) * (explanation_font.size + 10)) if explanation_lines else 0
    start_y = y1 + max((y2 - y1 - formula_block_h - explanation_block_h) // 2, 0)

    ty = start_y
    for line in formula_lines:
        line = _ellipsize(draw, line, formula_font, max_width)
        line_w = draw.textlength(line, font=formula_font)
        draw.text(((x1 + x2 - line_w) / 2, ty), line, font=formula_font, fill=theme.accent)
        ty += formula_size + 14
    if explanation_lines:
        ty += 24
        for line in explanation_lines:
            line = _ellipsize(draw, line, explanation_font, max_width)
            line_w = draw.textlength(line, font=explanation_font)
            draw.text(((x1 + x2 - line_w) / 2, ty), line, font=explanation_font, fill=theme.body)
            ty += explanation_font.size + 10


_CALLOUT_WARNING_LABELS = {"UYARI", "DİKKAT", "TEHLİKE"}
_CALLOUT_TIP_LABELS = {"İPUCU", "TAVSİYE", "ÖNERİ"}
# Uyarı rengi kasıtlı olarak temadan bağımsız sabit bir kırmızı: "dikkat" evrensel bir
# sinyal olmalı, koyu/açık her temada aynı aciliyeti taşımalı.
_CALLOUT_WARNING_COLOR = (196, 68, 68)


def _callout_style(label: str, theme: ThemePreset) -> tuple[tuple[int, int, int], str]:
    upper = label.strip().upper()
    if upper in _CALLOUT_WARNING_LABELS:
        return _CALLOUT_WARNING_COLOR, "!"
    if upper in _CALLOUT_TIP_LABELS:
        return theme.accent_alt, "i"
    return theme.accent, "i"


def _draw_callout_layout(draw: ImageDraw.ImageDraw, label: str, message: str, box, theme: ThemePreset):
    """"callout" layout: "UYARI:"/"İPUCU:"/"NOT:" gibi bir etiketle gelen tek bir notu, sol
    kenarı renkli, ikonlu bir kutu içinde vurgular — gerçek bir hocanın "dikkat, burada
    şuna dikkat edin" dediği anları düz madde listesinden ayırt eder."""
    x1, y1, x2, y2 = box
    color, icon = _callout_style(label, theme)
    label_font = _font(FONT_TITLE, 26)
    msg_font = _font(FONT_BODY, 30)
    icon_font = _font(FONT_TITLE, 26)

    card_x1, card_x2 = x1 + 20, x2 - 20
    text_x = card_x1 + 60 + 44
    max_width = card_x2 - 30 - text_x
    msg_lines = _wrap_text(draw, message, msg_font, max_width)[:5]

    card_h = max(140, 30 + label_font.size + 14 + len(msg_lines) * (msg_font.size + 10) + 20)
    card_top = y1 + max((y2 - y1 - card_h) // 2, 0)
    card_box = (card_x1, card_top, card_x2, card_top + card_h)

    tint = _mix(color, theme.surface_alt, 0.82)
    draw.rounded_rectangle(card_box, radius=20, fill=tint, outline=color, width=2)
    draw.rounded_rectangle((card_x1, card_top, card_x1 + 8, card_top + card_h), radius=4, fill=color)

    icon_cx, icon_cy = card_x1 + 60, card_top + 46
    draw.ellipse((icon_cx - 20, icon_cy - 20, icon_cx + 20, icon_cy + 20), fill=color)
    iw = draw.textlength(icon, font=icon_font)
    draw.text((icon_cx - iw / 2, icon_cy - icon_font.size / 2 - 2), icon, font=icon_font, fill=(255, 255, 255))

    draw.text((text_x, card_top + 30), label.strip().upper(), font=label_font, fill=color)
    ty = card_top + 30 + label_font.size + 14
    for line in msg_lines:
        line = _ellipsize(draw, line, msg_font, max_width)
        draw.text((text_x, ty), line, font=msg_font, fill=theme.body)
        ty += msg_font.size + 10


def _parse_comparison_columns(bullets: list[str]) -> tuple[list[str], list[str]] | None:
    """"comparison" layout'un beklediği iki-sütun yapısını ayırır: LLM tam olarak "---"
    içeren TEK bir öğeyle iki tarafı ayırmış olmalı, her iki tarafta da en az bir öğe
    (sütun başlığı) bulunmalı. Uymuyorsa None -> "bullets"a düşülür."""
    if bullets.count("---") != 1:
        return None
    idx = bullets.index("---")
    left, right = bullets[:idx], bullets[idx + 1:]
    if not left or not right:
        return None
    return left, right


def _draw_comparison_layout(draw: ImageDraw.ImageDraw, left: list[str], right: list[str],
                            box, theme: ThemePreset):
    x1, y1, x2, y2 = box
    mid = (x1 + x2) // 2
    gap = 24
    header_font = _font(FONT_TITLE, 30)
    point_font = _font(FONT_BODY, 25)
    draw.line((mid, y1 + 8, mid, y2 - 8), fill=theme.border, width=2)

    def _column(items: list[str], cx1: int, cx2: int, accent: tuple[int, int, int]):
        header, *points = items
        max_width = cx2 - cx1 - 20
        header_lines = _wrap_text(draw, header, header_font, max_width)[:2]
        point_lines = [
            _wrap_text(draw, f"•  {point}", point_font, max_width)[:2] for point in points[:5]
        ]
        block_h = len(header_lines) * (header_font.size + 8) + 12 + \
            sum(len(lines) * (point_font.size + 10) for lines in point_lines)
        y = y1 + max((y2 - y1 - block_h) // 2, 8)
        for line in header_lines:
            line = _ellipsize(draw, line, header_font, max_width)
            draw.text((cx1 + 10, y), line, font=header_font, fill=accent)
            y += header_font.size + 8
        y += 12
        for lines in point_lines:
            for line in lines:
                line = _ellipsize(draw, line, point_font, max_width)
                draw.text((cx1 + 10, y), line, font=point_font, fill=theme.body)
                y += point_font.size + 10

    _column(left, x1, mid - gap, theme.accent)
    _column(right, mid + gap, x2, theme.accent_alt)


_LEADING_ORDINAL_RE = re.compile(r"^\s*\d+\s*[.)\-]\s+")


def _strip_leading_ordinal(text: str) -> str:
    """LLM prompt'a uymayıp adımın başına kendi numarasını da yazarsa ("1. Şunu yap"),
    _draw_process_layout zaten kendi numaralı düğümünü çizdiğinden çift numaralanma
    ("① 1. Şunu yap") olmasın diye bu öneki temizler."""
    return _LEADING_ORDINAL_RE.sub("", text, count=1)


def _draw_process_layout(draw: ImageDraw.ImageDraw, steps: list[str], box, theme: ThemePreset):
    """"process" layout: bullet kartları yerine numaralı düğümleri dikey bir çizgiyle
    birbirine bağlayan bir zaman çizelgesi/akış görünümü — sıralı bir prosedürü bir liste
    gibi değil bir AKIŞ gibi hissettirir."""
    x1, y1, x2, y2 = box
    n = len(steps)
    row_h = (y2 - y1) / n
    node_x = x1 + 30
    node_r = 22
    step_font = _font(FONT_BODY, 27 if n <= 4 else 24)
    number_font = _font(FONT_TITLE, 20)
    text_x = node_x + node_r + 34
    max_width = x2 - text_x - 20

    if n > 1:
        draw.line((node_x, y1 + row_h / 2, node_x, y2 - row_h / 2), fill=theme.border, width=3)

    for i, step in enumerate(steps):
        cy = y1 + row_h * i + row_h / 2
        draw.ellipse((node_x - node_r, cy - node_r, node_x + node_r, cy + node_r), fill=theme.accent)
        num = str(i + 1)
        nw = draw.textlength(num, font=number_font)
        draw.text((node_x - nw / 2, cy - number_font.size / 2 - 2), num, font=number_font, fill=(255, 255, 255))
        lines = _wrap_text(draw, _strip_leading_ordinal(step), step_font, max_width)[:2]
        ty = cy - len(lines) * (step_font.size + 6) / 2
        for line in lines:
            line = _ellipsize(draw, line, step_font, max_width)
            draw.text((text_x, ty), line, font=step_font, fill=theme.body)
            ty += step_font.size + 6


def _draw_code_output_layout(img: Image.Image, draw: ImageDraw.ImageDraw, code: str, output_text: str,
                             area, theme: ThemePreset, label_font):
    """"code_output" layout: kod solda, o kodun ürettiği terminal/konsol çıktısı sağda —
    "bu kodu çalıştırırsan bunu görürsün" öğretim deseni. Terminal paneli kasıtlı olarak
    temadan bağımsız koyu/yeşil renklendirilir (evrensel terminal görünümü)."""
    x1, y1, x2, y2 = area
    divider_x = int(x1 + (x2 - x1) * 0.52)
    gap = 24
    draw.text((x1, y1 - 40), "KOD", font=label_font, fill=theme.accent)
    draw.text((divider_x + gap, y1 - 40), "ÇIKTI", font=label_font, fill=theme.accent)

    code_w = divider_x - gap - x1
    code_img = render_code_image(code, code_w, y2 - y1, theme.code_style)
    img.paste(code_img, (x1 + (code_w - code_img.width) // 2, y1 + (y2 - y1 - code_img.height) // 2))

    term_box = (divider_x + gap, y1, x2, y2)
    draw.rounded_rectangle(term_box, radius=18, fill=(24, 26, 32))
    term_font = _font(FONT_CODE, 25)
    pad = 28
    max_width = (x2 - pad) - (divider_x + gap + pad)
    lines = []
    for paragraph in (output_text.splitlines() or [""]):
        lines.extend(_wrap_text(draw, paragraph, term_font, max_width) or [""])
    lines = lines[:10]
    block_h = len(lines) * (term_font.size + 10)
    ty = y1 + max(((y2 - y1) - block_h) // 2, pad)
    for line in lines:
        line = _ellipsize(draw, line, term_font, max_width)
        draw.text((divider_x + gap + pad, ty), line, font=term_font, fill=(150, 230, 170))
        ty += term_font.size + 10


def _draw_content_layout(draw: ImageDraw.ImageDraw, slide: Slide, x1: int, x2: int,
                         content_top: int, content_bottom: int, theme: ThemePreset, label_font,
                         compact: bool = False):
    """Metin formatlarının (bullets/emphasis/definition/comparison/process/formula/callout)
    ortak dispatch'i — hem tam genişlikte (kod/görsel yokken, ya da görsel ALT şeritte iken —
    bkz. _draw_topic'in "elif slide.embedded_image:" dalı) hem de kodla birlikte dar bir sol
    panelde (bkz. _draw_topic'in "if slide.code:" dalı) çağrılabilir; `compact=True` SADECE
    kod dalında verilir. "comparison" iki alt-sütun gerektirdiğinden dar panelde okunaksız
    kalır — bu yüzden compact modda hiç denenmez, doğrudan madde listesine düşülür (görsel
    dalı artık compact=False verdiğinden bu kısıtlama görsel için geçerli DEĞİL).
    Tanınmayan bir "layout" değeri ya da o formatın beklediği yapıya uymayan içerik her zaman
    güvenli varsayılan olan madde listesine düşer, render asla bu yüzden kırılmaz."""
    layout = slide.layout if slide.layout in _CONTENT_LAYOUTS else "bullets"
    box = (x1, content_top + 72, x2, content_bottom - 34)
    rendered = False

    if layout == "emphasis" and len(slide.bullets) == 1:
        _draw_emphasis_text(draw, slide.bullets[0], (x1, content_top + 40, x2, content_bottom - 40), theme)
        rendered = True
    elif layout == "definition":
        pairs = _parse_definition_pairs(slide.bullets)
        if pairs:
            draw.text((x1 + 6, content_top + 30), "TANIMLAR", font=label_font, fill=theme.accent)
            _draw_definition_layout(draw, pairs, box, theme)
            rendered = True
    elif layout == "comparison" and not compact:
        columns = _parse_comparison_columns(slide.bullets)
        if columns:
            draw.text((x1 + 6, content_top + 30), "KARŞILAŞTIRMA", font=label_font, fill=theme.accent)
            _draw_comparison_layout(draw, columns[0], columns[1], box, theme)
            rendered = True
    elif layout == "process" and slide.bullets:
        draw.text((x1 + 6, content_top + 30), "ADIMLAR", font=label_font, fill=theme.accent)
        _draw_process_layout(draw, slide.bullets[:MAX_VISIBLE_BULLET_CARDS], box, theme)
        rendered = True
    elif layout == "formula":
        pairs = _parse_definition_pairs(slide.bullets)
        if pairs and len(pairs) == 1:
            draw.text((x1 + 6, content_top + 30), "FORMÜL", font=label_font, fill=theme.accent)
            _draw_formula_layout(draw, pairs[0][0], pairs[0][1], box, theme)
            rendered = True
    elif layout == "callout":
        pairs = _parse_definition_pairs(slide.bullets)
        if pairs and len(pairs) == 1:
            _draw_callout_layout(draw, pairs[0][0], pairs[0][1], box, theme)
            rendered = True

    if not rendered:
        # "---" yalnızca "comparison"ın sütun ayracı olarak anlamlıdır (bkz.
        # _parse_comparison_columns); comparison denenmediği (compact mod) ya da
        # başarısız olduğu durumlarda düz madde listesine düşerken bu öğe süzülmezse
        # ekranda anlamsız, çıplak bir "---" kartı olarak görünür.
        clean_bullets = [b for b in slide.bullets if b.strip() != "---"]
        draw.text((x1 + 6, content_top + 30), "ANA FİKİRLER", font=label_font, fill=theme.accent)
        _draw_bullet_cards(draw, clean_bullets, box, theme, compact=compact)


def _draw_topic(img: Image.Image, draw: ImageDraw.ImageDraw, slide: Slide,
                index: int, total: int, breadcrumb: str, theme: ThemePreset,
                has_ai_background: bool = False, ai_full: bool = False):
    width, height = img.size
    small_font = _font(FONT_SMALL, 25)
    title_font = _font(FONT_TITLE, 58 if len(slide.title) < 70 else 50)
    label_font = _font(FONT_TITLE, 20)

    breadcrumb_text = (breadcrumb or "DERS STÜDYOSU").upper()
    breadcrumb_text = _ellipsize(draw, breadcrumb_text, small_font, width - 520)
    draw.text((96, 54), breadcrumb_text, font=small_font, fill=theme.muted)

    count_text = f"{index:02d}  /  {total:02d}"
    count_w = int(draw.textlength(count_text, font=small_font)) + 42
    count_x = width - 96 - count_w
    draw.rounded_rectangle(
        (count_x, 42, width - 96, 88), radius=23,
        fill=theme.surface_alt, outline=theme.border, width=1,
    )
    draw.text((count_x + 21, 51), count_text, font=small_font, fill=theme.muted)

    title_lines = _wrap_text(draw, slide.title, title_font, width - 192)[:2]
    title_y = 112
    for line in title_lines:
        line = _ellipsize(draw, line, title_font, width - 192)
        draw.text((96, title_y), line, font=title_font, fill=theme.title)
        title_y += title_font.size + 8

    content_top = max(250, title_y + 28)
    content_bottom = height - 82
    # Tam AI arka planda büyük içerik paneli HİÇ çizilmez: tuvalin tamamı yapay zeka görselidir,
    # yalnızca metin ve kartlar (bkz. _GlassDraw) üstüne biner.
    if not ai_full:
        _rounded_panel(
            draw, (76, content_top, width - 76, content_bottom),
            (*theme.surface, 204) if has_ai_background else theme.surface,
            (*theme.border, 235) if has_ai_background else theme.border,
            radius=34,
        )

    if slide.code:
        if slide.layout == "code_output" and slide.bullets:
            _draw_code_output_layout(
                img, draw, slide.code, slide.bullets[0],
                (106, content_top + 76, width - 106, content_bottom - 36), theme, label_font,
            )
        else:
            # Sol panelde madde listesi ZORUNLU değil — LLM'in seçtiği metin formatı (definition/
            # callout/formula/process/emphasis) burada da denenir (bkz. _draw_content_layout),
            # sadece "comparison" dar panelde okunaksız kalacağından denenmez. Bu, "tanım + kod
            # örneği" gibi kodlama derslerinde çok doğal olan bir kombinasyonun eskiden olduğu
            # gibi düz madde listesine zorlanmasını engeller.
            divider_x = int(width * 0.49)
            draw.line(
                (divider_x, content_top + 42, divider_x, content_bottom - 42),
                fill=theme.border, width=2,
            )
            _draw_content_layout(draw, slide, 106, divider_x - 34, content_top, content_bottom,
                                 theme, label_font, compact=True)

            draw.text(
                (divider_x + 40, content_top + 30), "KOD ÖRNEĞİ",
                font=label_font, fill=theme.accent,
            )
            code_area = (divider_x + 40, content_top + 76, width - 112, content_bottom - 36)
            code_img = render_code_image(
                slide.code,
                code_area[2] - code_area[0],
                code_area[3] - code_area[1],
                theme.code_style,
            )
            code_x = code_area[0] + (code_area[2] - code_area[0] - code_img.width) // 2
            code_y = code_area[1] + (code_area[3] - code_area[1] - code_img.height) // 2
            img.paste(code_img, (code_x, code_y))
    elif slide.embedded_image and Path(slide.embedded_image).exists():
        # "Diyagram/görsel çıkar" modu (kaynaktan) VEYA app/image_enrichment.py
        # (internetten bulunan/yapay zeka ile üretilen görsel). Dar içerikli düzenlerde
        # boş sağ sütunu kullanır; "comparison" gibi genişlik gerektiren formatlarda ise
        # görsel ALT bantta kalır ve metin tam genişlikte render edilir (bkz.
        # KAVRA_PROJECT_HANDOFF.md §10.0.15 ve §10.0.18). Etiket,
        # görselin NEREDEN geldiğine göre değişir (bkz. Slide.image_source) — kullanıcı
        # bunun kaynağın kendi gerçek görseli mi, internetten bulunmuş bir fotoğraf mı,
        # yoksa yapay zeka illüstrasyonu mu (dolayısıyla gerçeği birebir yansıtmayabilir)
        # olduğunu görebilsin.
        image_label = {
            "search": "İNTERNETTEN GÖRSEL",
            "generated": "YAPAY ZEKA GÖRSELİ",
        }.get(slide.image_source, "KAYNAK GÖRSEL")
        if _prefers_side_image(slide):
            # Tanım/vurgu/formül/çağrı düzenleri tek bir dar metin bloğu kullandığı için
            # sağ yarı çoğunlukla boşa çıkıyordu. Bu düzenlerde görseli o boşluğa al;
            # karşılaştırma/süreç/madde listesi gibi genişlik isteyen düzenler aşağıdaki
            # yatay bant davranışını korur.
            divider_x = int(width * 0.61)
            draw.line((divider_x, content_top + 42, divider_x, content_bottom - 42),
                      fill=theme.border, width=2)
            _draw_content_layout(draw, slide, 106, divider_x - 34, content_top, content_bottom,
                                 theme, label_font, compact=True)
            image_x1, image_x2 = divider_x + 38, width - 106
            draw.text((image_x1, content_top + 30), image_label,
                      font=label_font, fill=theme.accent)
            image_area = (image_x1, content_top + 76, image_x2, content_bottom - 36)
            draw.rounded_rectangle(image_area, radius=24, fill=theme.surface_alt,
                                   outline=theme.border, width=2)
            _paste_fitted_image(
                img, slide.embedded_image,
                (image_area[0] + 12, image_area[1] + 12,
                 image_area[2] - 12, image_area[3] - 12),
            )
        else:
            image_band_height = int((content_bottom - content_top) * 0.34)
            image_top = content_bottom - image_band_height

            draw.line((106, image_top - 10, width - 106, image_top - 10), fill=theme.border, width=2)
            _draw_content_layout(draw, slide, 106, width - 106, content_top, image_top - 10,
                                 theme, label_font, compact=False)

            draw.text((106, image_top + 6), image_label, font=label_font, fill=theme.accent)
            label_h = label_font.size + 20
            # Kare/dikey görsellerin çok ince, iki yanında büyük boşluklu görünmesini
            # engellemek için genişliği de sınırlıyoruz — sadece gerçekten geniş
            # görseller tam genişliği kullanır.
            max_image_width = min(width - 212, int((image_band_height - label_h) * 2.6))
            image_x1 = 106 + (width - 212 - max_image_width) // 2
            image_area = (image_x1, image_top + label_h, image_x1 + max_image_width, content_bottom - 4)
            _paste_fitted_image(img, slide.embedded_image, image_area)
    else:
        _draw_content_layout(draw, slide, 106, width - 106, content_top, content_bottom,
                             theme, label_font, compact=False)

    _draw_progress(draw, theme, index, total, width, height)


def _render_source_page(image_path: str, out_path: Path, width: int, height: int):
    """"Sayfaları birebir slayt olarak kullan" modu: kaynak sayfa görüntüsünü
    olduğu gibi kullan, kendi temamızı/başlık-madde çizimimizi hiç yapma.

    Sayfa oranı genelde 16:9 değildir (A4, 4:3 sunu vb.) — kırpmadan, siyah
    kenar boşluğuyla (letterbox/pillarbox) tam kareye ortalanmış sığdırıyoruz.
    """
    with Image.open(image_path) as source:
        page = source.convert("RGB")
    page_ratio = page.width / page.height
    frame_ratio = width / height
    if page_ratio > frame_ratio:
        new_width, new_height = width, round(width / page_ratio)
    else:
        new_height, new_width = height, round(height * page_ratio)
    resized = page.resize((max(new_width, 1), max(new_height, 1)), Image.LANCZOS)
    frame = Image.new("RGB", (width, height), (0, 0, 0))
    frame.paste(resized, ((width - new_width) // 2, (height - new_height) // 2))
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    frame.save(out_path, quality=95)


def _paste_fitted_image(img: Image.Image, image_path: str, area: tuple[int, int, int, int]):
    """"Diyagram/görsel çıkar" modu: kaynaktan çıkarılmış GERÇEK görseli, oranını
    koruyarak (kırpmadan) verilen alana ortalanmış sığdırıp yapıştırır. Görselin
    kendisi hiç değiştirilmiyor/yeniden çizilmiyor — sadece boyutlandırılıyor.
    """
    x1, y1, x2, y2 = area
    box_w, box_h = x2 - x1, y2 - y1
    if box_w <= 0 or box_h <= 0:
        return
    with Image.open(image_path) as source:
        picture = source.convert("RGB")
    ratio = min(box_w / picture.width, box_h / picture.height)
    new_w = max(int(picture.width * ratio), 1)
    new_h = max(int(picture.height * ratio), 1)
    resized = picture.resize((new_w, new_h), Image.LANCZOS)
    img.paste(resized, (x1 + (box_w - new_w) // 2, y1 + (box_h - new_h) // 2))


def _prefers_side_image(slide: Slide) -> bool:
    """Yalnızca dar içerik yapısı doğrulanmış düzenleri yan yana göster.

    Bozuk bir `definition`/`formula`/`callout` girdisi renderer'da madde listesine
    düşer; o durumda listeyi daraltmak yerine alttaki görsel bandını korumak daha
    okunaklıdır.
    """
    if slide.layout == "emphasis":
        return len(slide.bullets) == 1
    if slide.layout == "definition":
        return _parse_definition_pairs(slide.bullets) is not None
    if slide.layout in {"formula", "callout"}:
        pairs = _parse_definition_pairs(slide.bullets)
        return bool(pairs and len(pairs) == 1)
    return False


def _render_ai_background(image_path: str, width: int, height: int,
                          theme: ThemePreset) -> Image.Image:
    """Üretilen görseli tam ekran kapla; metin kontrastını temaya göre güvenceye al."""
    with Image.open(image_path) as source:
        picture = ImageOps.exif_transpose(source).convert("RGB")
    picture = ImageOps.fit(
        picture, (width, height), method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )
    picture = picture.filter(ImageFilter.GaussianBlur(radius=max(width / 1600, 0.8)))
    picture = ImageEnhance.Color(picture).enhance(0.72)
    picture = ImageEnhance.Contrast(picture).enhance(0.88).convert("RGBA")

    # Başlık rengi koyuysa açık tema, açıksa koyu tema kabul edilir. Böylece tema
    # renkleri değiştirilmeden görüntü üzerine doğru yönde bir perde uygulanır.
    title_luma = 0.2126 * theme.title[0] + 0.7152 * theme.title[1] + 0.0722 * theme.title[2]
    veil = (255, 255, 255, 148) if title_luma < 128 else (7, 12, 24, 128)
    return Image.alpha_composite(picture, Image.new("RGBA", picture.size, veil)).convert("RGB")


# ---------------------------------------------------------------------------
# "Tam AI arka plan" (Slide.ai_background_full): tüm slayt tuvali yapay zeka görseli,
# büyük beyaz içerik paneli YOK. Yalnızca metin ve küçük kartlar görselin ÜSTÜNE biner.
# Okunabilirlik üç katmanla güvenceye alınır (hepsi görselin kendisine göre otomatik):
#   1) görselin metin bölgesi hafifçe yumuşatılıp açık/koyuya doğru kaydırılır (kenar/köşe
#      sanatı canlı kalsın diye yalnızca YUMUŞAK KENARLI bir bölgede),
#   2) yazı rengi görselin parlaklığına göre açık/koyu seçilir,
#   3) her yazının arkasına zıt renkte yumuşak bir hale, her karta buzlu cam (arka plan
#      bulanıklaştırılıp yarı saydam renkle karıştırılır) uygulanır.
# ---------------------------------------------------------------------------

_FULL_LIGHT_TARGET = 178   # açık sanatta metin bölgesinin hedef ortalama parlaklığı (0-255)
_FULL_DARK_TARGET = 62     # koyu sanatta
_FULL_MAX_TONE_SHIFT = 0.62
_GLASS_MIN_SIDE = 30       # bundan küçük dikdörtgenler (ilerleme çubuğu, vurgu şeridi) cam OLMAZ
_GLASS_ALPHA = 0.60


def _content_feather_mask(size: tuple[int, int], strength: float) -> Image.Image:
    """Metin alanını (başlık altı → alt kenar) kaplayan, kenarları yumuşak bir maske."""
    width, height = size
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (int(width * 0.035), int(height * 0.03), width - int(width * 0.035), height - int(height * 0.045)),
        radius=int(height * 0.07), fill=round(255 * strength),
    )
    return mask.filter(ImageFilter.GaussianBlur(radius=max(width / 32, 12)))


def _render_ai_full_background(image_path: str, width: int, height: int) -> tuple[Image.Image, bool]:
    """Görseli tam ekran kapla ve metnin bineceği bölgeyi okunabilir kıl.

    Dönüş: (görsel, dark_art). dark_art=True ise sanat koyudur ve AÇIK renkli yazı kullanılmalı.
    Görselin KENARLARI/KÖŞELERİ dokunulmadan kalır — sanatın görünür olması istenen şey."""
    with Image.open(image_path) as source:
        picture = ImageOps.exif_transpose(source).convert("RGB")
    picture = ImageOps.fit(
        picture, (width, height), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5),
    )

    region = picture.crop((
        int(width * 0.055), int(height * 0.08), width - int(width * 0.055), int(height * 0.924),
    )).convert("L")
    mean = ImageStat.Stat(region).mean[0]
    need_light = max(0.0, (_FULL_LIGHT_TARGET - mean) / max(255 - mean, 1))
    need_dark = max(0.0, (mean - _FULL_DARK_TARGET) / max(mean, 1))
    dark_art = need_dark < need_light
    amount = min(need_dark if dark_art else need_light, _FULL_MAX_TONE_SHIFT)

    # İnce detay metnin arkasında gürültü yaratır: yalnızca metin bölgesini bulanıklaştır.
    softened = picture.filter(ImageFilter.GaussianBlur(radius=max(width / 190, 3)))
    picture = Image.composite(softened, picture, _content_feather_mask(picture.size, 0.85))
    if amount > 0:
        target = (0, 0, 0) if dark_art else (255, 255, 255)
        shifted = Image.blend(picture, Image.new("RGB", picture.size, target), amount)
        picture = Image.composite(shifted, picture, _content_feather_mask(picture.size, 1.0))
    return picture, dark_art


def _ai_full_theme(theme: ThemePreset, dark_art: bool) -> ThemePreset:
    """Tema renklerini görselin parlaklığına göre okunur bir yazı/cam paletiyle değiştir.
    Vurgu rengi (accent) temadan gelir ama kontrast için bir miktar açılır/koyulaştırılır."""
    if dark_art:
        return replace(
            theme,
            surface=(10, 16, 30), surface_alt=(10, 18, 34), border=(165, 186, 216),
            title=(255, 255, 255), body=(236, 242, 250), muted=(196, 208, 224),
            accent=_mix(theme.accent, (255, 255, 255), 0.38),
            accent_alt=_mix(theme.accent_alt, (255, 255, 255), 0.30),
        )
    return replace(
        theme,
        surface=(255, 255, 255), surface_alt=(255, 255, 255), border=(140, 160, 188),
        title=(16, 27, 44), body=(32, 46, 66), muted=(70, 86, 108),
        accent=_mix(theme.accent, (0, 0, 0), 0.30),
        accent_alt=_mix(theme.accent_alt, (0, 0, 0), 0.40),
    )


class _GlassDraw:
    """ImageDraw sarmalayıcı: panelsiz tam-AI arka planda tüm yerleşim kodunu DEĞİŞTİRMEDEN
    okunabilirlik sağlar. 3 değerli (opak) dolgulu dikdörtgenler buzlu cama, her yazı da
    zıt renkli yumuşak bir haleyle çizilir; 4 değerli (zaten yarı saydam) dolgular, küçük
    şeritler ve diğer tüm çizimler olduğu gibi geçer."""

    def __init__(self, img: Image.Image, base: ImageDraw.ImageDraw):
        self._img = img
        self._base = base

    def __getattr__(self, name):
        return getattr(self._base, name)

    def rounded_rectangle(self, xy, radius=0, fill=None, outline=None, width=1, **kwargs):
        x1, y1, x2, y2 = (int(round(v)) for v in xy)
        glass = (
            fill is not None and len(fill) == 3
            and min(x2 - x1, y2 - y1) >= _GLASS_MIN_SIDE
        )
        if not glass:
            return self._base.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width, **kwargs)
        box = (max(x1, 0), max(y1, 0), min(x2, self._img.width), min(y2, self._img.height))
        w, h = box[2] - box[0], box[3] - box[1]
        if w > 0 and h > 0:
            backdrop = self._img.crop(box).filter(ImageFilter.GaussianBlur(radius=14))
            frosted = Image.blend(backdrop, Image.new("RGB", (w, h), tuple(fill)), _GLASS_ALPHA)
            scale = 3
            mask = Image.new("L", (w * scale, h * scale), 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                (0, 0, w * scale - 1, h * scale - 1), radius=radius * scale, fill=255,
            )
            self._img.paste(frosted, (box[0], box[1]), mask.resize((w, h), Image.Resampling.LANCZOS))
        if outline is not None:
            edge = (*outline, 150) if len(outline) == 3 else outline
            self._base.rounded_rectangle(xy, radius=radius, outline=edge, width=width)

    def text(self, xy, text, fill=None, font=None, **kwargs):
        if not (text and font is not None and str(text).strip() and isinstance(fill, tuple) and len(fill) >= 3):
            return self._base.text(xy, text, fill=fill, font=font, **kwargs)
        text = str(text)
        left, top, right, bottom = (int(v) for v in self._base.textbbox(xy, text, font=font, **kwargs))
        x1, y1 = max(left, 0), max(top, 0)
        x2, y2 = min(right, self._img.width), min(bottom, self._img.height)
        if x2 <= x1 or y2 <= y1:
            # Tuvalin tamamen dışına taşan yazı: analiz edilecek zemin yok, düz çiz.
            return self._base.text(xy, text, fill=fill, font=font, **kwargs)
        region = self._img.crop((x1, y1, x2, y2))
        stat = ImageStat.Stat(region)
        background = tuple(stat.mean[:3])
        busyness = sum(stat.stddev[:3]) / 3
        color = tuple(fill[:3])
        contrast = _contrast_ratio(color, background)
        if contrast < _MIN_TEXT_CONTRAST:
            # Bu yazının ARKASINDAKİ görsel, tema rengiyle okunamayacak kadar yakın: yerel olarak
            # en çok kontrast veren açık/koyu renge geç (ör. koyu köşedeki küçük üst yazı).
            color = max(_LIGHT_TEXT, _DARK_TEXT, key=lambda candidate: _contrast_ratio(candidate, background))
            fill = (*color, *fill[3:])
            contrast = _contrast_ratio(color, background)
        need = _halo_need(contrast, busyness)
        if need > 0:
            light_text = _relative_luminance(color) > _relative_luminance(background)
            self._halo_text(xy, text, font, (0, 0, 0) if light_text else (255, 255, 255), need, **kwargs)
        return self._base.text(xy, text, fill=fill, font=font, **kwargs)

    def _halo_text(self, xy, text: str, font, halo: tuple[int, int, int], strength: float, **kwargs) -> None:
        left, top, right, bottom = (int(v) for v in self._base.textbbox(xy, text, font=font, **kwargs))
        pad = max(10, int(font.size * 0.5))
        size = (right - left + 2 * pad, bottom - top + 2 * pad)
        if size[0] <= 0 or size[1] <= 0:
            return
        layer = Image.new("L", size, 0)
        ImageDraw.Draw(layer).text(
            (xy[0] - left + pad, xy[1] - top + pad), text, font=font, fill=255, **kwargs,
        )
        glow = layer.filter(ImageFilter.MaxFilter(3)).filter(
            ImageFilter.GaussianBlur(radius=max(2.0, font.size * 0.10)),
        )
        gain = 2.0 * strength
        glow = glow.point(lambda v: min(255, int(v * gain)))
        self._img.paste(Image.new("RGB", size, halo), (left - pad, top - pad), glow)


_LIGHT_TEXT = (248, 251, 255)
_DARK_TEXT = (14, 24, 40)
_MIN_TEXT_CONTRAST = 3.0


def _relative_luminance(rgb) -> float:
    r, g, b = (max(min(c, 255), 0) / 255 for c in rgb[:3])
    return 0.2126 * r ** 2.2 + 0.7152 * g ** 2.2 + 0.0722 * b ** 2.2


def _contrast_ratio(a, b) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _halo_need(contrast: float, busyness: float) -> float:
    """0..1: yazının halesinin ne kadar güçlü olacağı. Düz bir zeminde (rozet, cam kart)
    halo yazıyı yalnızca bulandırır — gerçek bir kullanıcı çıktısında rozet rakamlarını ve küçük
    etiketleri lekeledi. Bu yüzden yalnızca kontrast sınırda ya da zemin yoğunken kullanılır."""
    if busyness < 8 and contrast >= _MIN_TEXT_CONTRAST:
        return 0.0
    if contrast < 4.5:
        return 1.0
    if contrast < 7:
        return 0.5 if busyness > 10 else 0.0
    return 0.3 if busyness > 22 else 0.0


def render_slide(slide: Slide, index: int, total: int, breadcrumb: str,
                 out_path: Path, width: int = 1920, height: int = 1080,
                 accent: tuple[int, int, int] | None = None,
                 theme_preset: str = "auto"):
    if slide.background_image:
        if not Path(slide.background_image).is_file():
            raise FileNotFoundError("PDF sayfa görseli bulunamadı: " + slide.background_image)
        _render_source_page(slide.background_image, out_path, width, height)
        return

    theme = resolve_theme(theme_preset, slide, index)
    if accent:
        theme = ThemePreset(**{**theme.__dict__, "accent": accent})

    start = theme.chapter_bg_start if slide.level == "chapter" else theme.bg_start
    end = theme.chapter_bg_end if slide.level == "chapter" else theme.bg_end
    has_ai_background = bool(
        slide.ai_background_image and Path(slide.ai_background_image).is_file()
    )
    ai_full = has_ai_background and bool(slide.ai_background_full)
    if ai_full:
        img, dark_art = _render_ai_full_background(slide.ai_background_image, width, height)
        theme = _ai_full_theme(theme, dark_art)
    elif has_ai_background:
        img = _render_ai_background(slide.ai_background_image, width, height, theme)
    else:
        img = _gradient(width, height, start, end)
        _decorate(img, theme)
    draw = ImageDraw.Draw(img, "RGBA")
    if ai_full:
        draw = _GlassDraw(img, draw)

    if slide.level == "chapter":
        _draw_chapter(img, draw, slide, index, total, theme)
    else:
        _draw_topic(img, draw, slide, index, total, breadcrumb, theme,
                    has_ai_background=has_ai_background, ai_full=ai_full)

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, quality=95)
