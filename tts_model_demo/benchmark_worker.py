"""Gerçek slayt TTS benchmark'ında bir model worker'ını çalıştırır."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def main(spec_path: str) -> int:
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    provider_name = spec["provider"]
    started = time.perf_counter()
    try:
        if provider_name == "chatterbox":
            from app.tts.chatterbox_provider import ChatterboxTTSProvider

            provider = ChatterboxTTSProvider()
        elif provider_name == "coqui":
            from app.tts.coqui_provider import CoquiTTSProvider

            provider = CoquiTTSProvider(gpu=True)
        else:
            raise ValueError(f"Bilinmeyen benchmark sağlayıcısı: {provider_name}")
        ready = time.perf_counter()
        results = []
        for item in spec["items"]:
            item_started = time.perf_counter()
            synthesized = provider.synthesize(item["text"], item["voice"], Path(item["outPath"]))
            results.append(
                {
                    "index": item["index"],
                    "audioSeconds": round(synthesized.duration, 3),
                    "synthesisSeconds": round(time.perf_counter() - item_started, 3),
                }
            )
        finished = time.perf_counter()
        result = {"ok": True, "startedAt": started, "readyAt": ready, "finishedAt": finished, "results": results}
    except Exception as exc:
        result = {"ok": False, "startedAt": started, "finishedAt": time.perf_counter(), "error": str(exc)}
    Path(spec["resultPath"]).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
