import io
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
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


def _draw_bullet_cards(draw: ImageDraw.ImageDraw, bullets: list[str], box,
                       theme: ThemePreset, compact: bool):
    x1, y1, x2, y2 = box
    items = bullets[:6]
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


def _draw_topic(img: Image.Image, draw: ImageDraw.ImageDraw, slide: Slide,
                index: int, total: int, breadcrumb: str, theme: ThemePreset):
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
    _rounded_panel(
        draw, (76, content_top, width - 76, content_bottom),
        theme.surface, theme.border, radius=34,
    )

    if slide.code:
        divider_x = int(width * 0.49)
        draw.line(
            (divider_x, content_top + 42, divider_x, content_bottom - 42),
            fill=theme.border, width=2,
        )
        _draw_bullet_cards(
            draw, slide.bullets,
            (106, content_top + 72, divider_x - 34, content_bottom - 34),
            theme, compact=True,
        )

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
        # "Diyagram/görsel çıkar" modu: kod örneğiyle aynı ikiye-bölünmüş
        # düzen, sağ tarafta kaynaktan çıkarılmış GERÇEK görsel var.
        divider_x = int(width * 0.49)
        draw.line(
            (divider_x, content_top + 42, divider_x, content_bottom - 42),
            fill=theme.border, width=2,
        )
        _draw_bullet_cards(
            draw, slide.bullets,
            (106, content_top + 72, divider_x - 34, content_bottom - 34),
            theme, compact=True,
        )
        draw.text(
            (divider_x + 40, content_top + 30), "KAYNAK GÖRSEL",
            font=label_font, fill=theme.accent,
        )
        image_area = (divider_x + 40, content_top + 76, width - 112, content_bottom - 36)
        _paste_fitted_image(img, slide.embedded_image, image_area)
    else:
        draw.text(
            (112, content_top + 30), "ANA FİKİRLER",
            font=label_font, fill=theme.accent,
        )
        _draw_bullet_cards(
            draw, slide.bullets,
            (106, content_top + 72, width - 106, content_bottom - 34),
            theme, compact=False,
        )

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
    img = _gradient(width, height, start, end)
    _decorate(img, theme)
    draw = ImageDraw.Draw(img, "RGBA")

    if slide.level == "chapter":
        _draw_chapter(img, draw, slide, index, total, theme)
    else:
        _draw_topic(img, draw, slide, index, total, breadcrumb, theme)

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, quality=95)
