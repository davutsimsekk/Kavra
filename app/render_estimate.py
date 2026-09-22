"""Free, deterministic "what will happen if I hit render" preview.

No network calls, no LLM/TTS invocation — just arithmetic over what's already
on disk (word counts from the quality report, existing segment file sizes,
free space on the drive). Cloud provider prices are never hard-coded (they
drift and vary by plan); the user is told to check their own plan instead.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from app.asset_integrity import audit_assets
from app.config import WORDS_PER_MINUTE as _WORDS_PER_MINUTE
from app.models import Slide
_FALLBACK_MB_PER_SLIDE = 2.5  # hiç segment yokken kaba ilk tahmin (1080p, kısa slayt)

LOCAL_PROVIDERS = {"edge", "piper", "coqui", "anka", "chatterbox"}


def _existing_segment_sizes_mb(pdir: Path) -> list[float]:
    assets = pdir / "assets"
    if not assets.exists():
        return []
    return [
        path.stat().st_size / (1024 * 1024)
        for path in assets.glob("segment_*.mp4")
        if path.is_file() and path.stat().st_size > 0
    ]


def _provider_cost_note(provider_name: str) -> str:
    if provider_name in LOCAL_PROVIDERS:
        return "Yerel/ücretsiz — bu sağlayıcı için API maliyeti yok."
    if provider_name == "elevenlabs":
        return (
            "Ücretli bulut sağlayıcı (ElevenLabs). Kesin fiyat planına göre değişir; "
            "hesabındaki kalan karakter kotanı kontrol et."
        )
    return "Maliyeti bilinmiyor — bu sağlayıcının fiyatlandırmasını kendi hesabından kontrol et."


def estimate_render(pdir: Path, slides: list[Slide], provider_name: str) -> dict[str, Any]:
    word_total = sum(len((slide.narration or "").split()) for slide in slides)
    audit = audit_assets(pdir, len(slides))
    slides_to_render = audit["missingSegmentCount"]
    slides_reusable = audit["canonicalSegmentCount"]

    existing_sizes = _existing_segment_sizes_mb(pdir)
    avg_mb_per_slide = (
        sum(existing_sizes) / len(existing_sizes) if existing_sizes else _FALLBACK_MB_PER_SLIDE
    )
    estimated_disk_mb = round(slides_to_render * avg_mb_per_slide, 1)

    try:
        free_disk_mb = round(shutil.disk_usage(pdir).free / (1024 * 1024), 1)
    except OSError:
        free_disk_mb = None

    return {
        "wordsTotal": word_total,
        "estimatedMinutes": round(word_total / _WORDS_PER_MINUTE, 1) if word_total else 0,
        "slidesToRender": slides_to_render,
        "slidesReusable": slides_reusable,
        "estimatedDiskMb": estimated_disk_mb,
        "freeDiskMb": free_disk_mb,
        "diskWarning": bool(free_disk_mb is not None and estimated_disk_mb > free_disk_mb * 0.9),
        "providerCostNote": _provider_cost_note(provider_name),
        "providerIsLocal": provider_name in LOCAL_PROVIDERS,
    }
