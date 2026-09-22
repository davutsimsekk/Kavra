"""Üç TTS motorunu sırayla ve birbirinden yalıtarak karşılaştırır."""

from __future__ import annotations

import gc
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from app.config import ROOT


LABELS = {
    "coqui": "Coqui XTTS v2",
    "anka": "Anka TTS",
    "chatterbox": "Chatterbox Multilingual",
}


def _chatterbox_python() -> Path:
    candidate = ROOT / "chatterbox_venv" / "Scripts" / "python.exe"
    if not candidate.exists():
        candidate = ROOT / "chatterbox_venv" / "bin" / "python"
    return candidate


def main(spec_path: str) -> int:
    spec_path_obj = Path(spec_path)
    spec = json.loads(spec_path_obj.read_text(encoding="utf-8"))
    base_dir = spec_path_obj.parent
    results: list[dict] = []
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

    for provider_name in ("coqui", "anka", "chatterbox"):
        output_path = base_dir / f"{provider_name}.wav"
        result_path = base_dir / f"{provider_name}.json"
        provider_spec = {
            "provider": provider_name,
            "text": spec["text"],
            "referencePath": spec["referencePath"],
            "outputPath": str(output_path),
            "resultPath": str(result_path),
        }
        provider_spec_path = base_dir / f"{provider_name}-spec.json"
        provider_spec_path.write_text(json.dumps(provider_spec, ensure_ascii=False), encoding="utf-8")

        python = _chatterbox_python() if provider_name == "chatterbox" else Path(sys.executable)
        if not python.exists():
            result = {
                "provider": provider_name,
                "status": "failed",
                "error": "Chatterbox için ayrı ortam bulunamadı. install_chatterbox.py çalıştırılmalı.",
            }
        else:
            started = time.perf_counter()
            process = subprocess.run(
                [str(python), "-m", "studio_web.tts_comparison_provider_worker", str(provider_spec_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creation_flags,
            )
            if result_path.exists():
                result = json.loads(result_path.read_text(encoding="utf-8"))
            else:
                detail = (process.stdout or process.stderr or "native süreç sonuç yazmadan kapandı").strip()[-900:]
                result = {"provider": provider_name, "status": "failed", "error": detail}
            result.setdefault("generationSeconds", round(time.perf_counter() - started, 2))

        result["label"] = LABELS[provider_name]
        if result.get("status") == "complete" and output_path.exists():
            result["audioFile"] = output_path.name
        results.append(result)
        gc.collect()

    (base_dir / "results.json").write_text(json.dumps({"results": results}, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"results": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
