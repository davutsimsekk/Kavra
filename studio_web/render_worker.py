from __future__ import annotations

import json
import sys
from pathlib import Path

# app.config, import edilir edilmez stdout/stderr'i koşulsuz UTF-8'e sabitliyor
# (bkz. app/config.py) — bu satır o modülün en üstte import edilmesini garanti eder,
# aksi halde Windows'ta pipe'a yönlendirilen stdout sistem ANSI kod sayfasına düşüp
# anlatım metnindeki Unicode karakterlerde (ör. '↔') UnicodeEncodeError verirdi.
from app.config import VideoOptions
from app.models import Slide
from app.pipeline import render_video


def emit(payload: dict):
    print("__DERS_JOB__" + json.dumps(payload, ensure_ascii=False), flush=True)


def main(spec_path: str) -> int:
    try:
        spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
        pdir = Path(spec["projectDir"])
        slides = [Slide.from_dict(item) for item in spec["slides"]]
        options = VideoOptions(**spec["options"])

        def on_progress(index: int, total: int, title: str):
            emit({"type": "progress", "current": index, "total": total, "title": title})

        def on_status(message: str):
            emit({"type": "status", "message": message, "total": len(slides)})

        on_status("Render planı hazırlanıyor")

        video, audio = render_video(
            pdir,
            slides,
            spec["provider"],
            spec["voice"],
            spec["rate"],
            options,
            progress_cb=on_progress,
            status_cb=on_status,
            force_audio=bool(spec.get("forceAudioRegeneration", False)),
        )
        api_base = spec.get("apiBase") or f"/api/projects/{pdir.name}"
        emit({
            "type": "complete",
            "result": {
                "videoUrl": f"{api_base}/output/video",
                "audioUrl": f"{api_base}/output/audio",
                "videoPath": str(video),
                "audioPath": str(audio),
            },
        })
        return 0
    except Exception as exc:
        emit({"type": "error", "message": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
