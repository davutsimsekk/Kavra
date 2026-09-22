"""Tek TTS sağlayıcısını ayrı süreçte çalıştıran comparison worker."""

from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path


def main(spec_path: str) -> int:
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    result_path = Path(spec["resultPath"])
    provider_name = str(spec["provider"])
    started = time.perf_counter()
    try:
        if provider_name == "chatterbox":
            from app.tts.chatterbox_provider import ChatterboxTTSProvider

            provider = ChatterboxTTSProvider()
        elif provider_name == "coqui":
            from app.tts.coqui_provider import CoquiTTSProvider

            provider = CoquiTTSProvider()
        elif provider_name == "anka":
            from app.tts.anka_provider import AnkaTTSProvider

            provider = AnkaTTSProvider()
        else:
            raise ValueError(f"Bilinmeyen karşılaştırma sağlayıcısı: {provider_name}")

        synthesized = provider.synthesize(
            str(spec["text"]),
            str(spec["referencePath"]),
            Path(spec["outputPath"]),
        )
        result = {
            "provider": provider_name,
            "status": "complete",
            "duration": round(synthesized.duration, 2),
            "generationSeconds": round(time.perf_counter() - started, 2),
        }
    except Exception as exc:
        result = {
            "provider": provider_name,
            "status": "failed",
            "error": str(exc),
            "traceback": traceback.format_exc(limit=4),
            "generationSeconds": round(time.perf_counter() - started, 2),
        }

    result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
