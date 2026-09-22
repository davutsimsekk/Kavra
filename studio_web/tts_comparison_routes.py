"""API routes for a fair, isolated three-engine Turkish TTS comparison."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse


def register_tts_comparison_routes(app, jobs, *, cache_dir: Path, root: Path, normalize_text):
    comparison_root = cache_dir / "tts_comparisons"
    comparison_root.mkdir(parents=True, exist_ok=True)

    def comparison_dir(comparison_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", comparison_id):
            raise HTTPException(404, "Karşılaştırma bulunamadı.")
        candidate = (comparison_root / comparison_id).resolve()
        if candidate.parent != comparison_root.resolve():
            raise HTTPException(404, "Karşılaştırma bulunamadı.")
        return candidate

    def run_comparison(comparison_id: str) -> dict[str, Any]:
        folder = comparison_dir(comparison_id)
        process = subprocess.run(
            [sys.executable, "-m", "studio_web.tts_comparison_worker", str(folder / "spec.json")],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        result_path = folder / "results.json"
        if not result_path.exists():
            detail = (process.stdout or process.stderr or "Karşılaştırma worker'ı sonuç yazmadan kapandı.").strip()[-1200:]
            raise RuntimeError(detail)
        results = json.loads(result_path.read_text(encoding="utf-8")).get("results", [])
        for result in results:
            if result.get("audioFile"):
                result["audioUrl"] = f"/api/tts-comparison/{comparison_id}/audio/{result['provider']}?v={time.time_ns()}"
        return {"comparisonId": comparison_id, "results": results}

    @app.post("/api/tts-comparison")
    async def create_tts_comparison(
        reference: UploadFile = File(...), reference_text: str = Form(...), text: str = Form(...),
    ):
        if Path(reference.filename or "").suffix.lower() != ".wav":
            raise HTTPException(400, "Karşılaştırma için yalnızca WAV referans ses kabul edilir.")
        reference_text, text = reference_text.strip(), text.strip()
        if not reference_text:
            raise HTTPException(400, "Anka için referans sesin birebir transkriptini gir.")
        if not (1 <= len(text) <= 600):
            raise HTTPException(400, "Karşılaştırma metni 1 ile 600 karakter arasında olmalı.")

        comparison_id = uuid.uuid4().hex
        folder = comparison_dir(comparison_id)
        folder.mkdir(parents=True, exist_ok=False)
        reference_path = folder / "reference.wav"
        try:
            size = 0
            with reference_path.open("wb") as output:
                while chunk := await reference.read(1024 * 1024):
                    size += len(chunk)
                    if size > 30 * 1024 * 1024:
                        raise HTTPException(413, "Referans ses 30 MB sınırını aşıyor.")
                    output.write(chunk)
            if size == 0:
                raise HTTPException(400, "Referans ses dosyası boş.")
            # Coqui ve Chatterbox aynı WAV'ı kullanır; Anka aynı isimli .txt
            # çiftini ister. Böylece üç motor da tam olarak aynı sesi klonlar.
            reference_path.with_suffix(".txt").write_text(reference_text, encoding="utf-8")
            (folder / "spec.json").write_text(
                json.dumps({"text": normalize_text(text), "referencePath": str(reference_path)}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            if folder.exists():
                for item in folder.iterdir():
                    item.unlink(missing_ok=True)
                folder.rmdir()
            raise
        finally:
            await reference.close()

        return {"jobId": jobs.create("tts-comparison", lambda _job_id: run_comparison(comparison_id))}

    @app.get("/api/tts-comparison/{comparison_id}/audio/{provider_name}")
    def get_tts_comparison_audio(comparison_id: str, provider_name: str):
        if provider_name not in {"coqui", "anka", "chatterbox"}:
            raise HTTPException(404, "Ses bulunamadı.")
        folder = comparison_dir(comparison_id)
        path = (folder / f"{provider_name}.wav").resolve()
        if path.parent != folder.resolve() or not path.is_file():
            raise HTTPException(404, "Ses henüz hazır değil.")
        return FileResponse(path, media_type="audio/wav", filename=path.name, headers={"Cache-Control": "no-store"})
