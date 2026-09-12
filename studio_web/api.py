from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import (
    CACHE_DIR,
    PROJECTS_DIR,
    ROOT,
    VideoOptions,
    get_api_key,
    load_settings,
    save_api_key,
    save_settings,
)
from app.llm.gemini_provider import DEFAULT_MODEL as GEMINI_DEFAULT_MODEL, KNOWN_MODELS
from app.llm.openai_compatible_provider import DEFAULT_ENDPOINT, DEFAULT_MODEL as OPENAI_DEFAULT_MODEL
from app.generation_checkpoint import (
    begin_chunk_commit,
    completed_indexes,
    ensure_checkpoint,
    finish_chunk_commit,
    generation_status,
    reset_checkpoint,
    saved_agent_session,
)
from app.models import Slide
from app.pipeline import load_raw_sections, load_script, parse_and_cache, save_script
from app.tts import PROVIDER_LABELS, list_voices as list_tts_voices
from app.video.slide_renderer import render_slide
from app.video.themes import THEME_LABELS

WEB_DIST = ROOT / "webui" / "dist"
UPLOAD_DIR = CACHE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


class JobStore:
    def __init__(self):
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def create(self, kind: str, work: Callable[[str], dict[str, Any]]) -> str:
        job_id = uuid.uuid4().hex
        with self._lock:
            self._jobs[job_id] = {
                "id": job_id,
                "kind": kind,
                "status": "queued",
                "progress": 0,
                "current": 0,
                "total": 0,
                "message": "Sıraya alındı",
                "result": None,
                "error": None,
                "updatedAt": time.time(),
            }

        def runner():
            self.update(job_id, status="running", message="Başlatılıyor")
            try:
                result = work(job_id)
                self.update(
                    job_id,
                    status="complete",
                    progress=100,
                    message="Tamamlandı",
                    result=result,
                )
            except Exception as exc:
                self.update(job_id, status="failed", message="İşlem başarısız", error=str(exc))

        threading.Thread(target=runner, daemon=True).start()
        return job_id

    def update(self, job_id: str, **changes):
        with self._lock:
            if job_id not in self._jobs:
                return
            self._jobs[job_id].update(changes)
            self._jobs[job_id]["updatedAt"] = time.time()

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            return dict(job)


jobs = JobStore()
app = FastAPI(title="Ders Stüdyosu Local API", version="2.0")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
ALLOWED_BROWSER_ORIGINS = {
    "http://127.0.0.1:8765",
    "http://localhost:8765",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
}
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(ALLOWED_BROWSER_ORIGINS),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def protect_local_mutations(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin not in ALLOWED_BROWSER_ORIGINS:
        return JSONResponse({"detail": "Bu yerel API yalnızca Ders Stüdyosu arayüzünden kullanılabilir."}, status_code=403)
    return await call_next(request)


def _project_dir(project_id: str) -> Path:
    if not re.fullmatch(r"[\wığüşöçİĞÜŞÖÇ.-]+", project_id, re.UNICODE):
        raise HTTPException(400, "Geçersiz proje kimliği.")
    root = PROJECTS_DIR.resolve()
    candidate = (PROJECTS_DIR / project_id).resolve()
    if candidate.parent != root or not candidate.is_dir():
        raise HTTPException(404, "Proje bulunamadı.")
    return candidate


def _slides_or_empty(pdir: Path) -> list[Slide]:
    try:
        return load_script(pdir)
    except FileNotFoundError:
        return []


def _effective_agent_session(reuse_session: bool, single_request: bool) -> bool:
    return bool(reuse_session) and not bool(single_request)


def _single_request_is_safe(section_count: int, character_count: int) -> bool:
    return section_count <= 12 and character_count <= 24_000


def _project_payload(pdir: Path) -> dict[str, Any]:
    sections = load_raw_sections(pdir) if (pdir / "raw_sections.json").exists() else []
    slides = _slides_or_empty(pdir)
    source_path = ""
    if (pdir / "source_path.txt").exists():
        source_path = (pdir / "source_path.txt").read_text(encoding="utf-8").strip()
    return {
        "id": pdir.name,
        "sourcePath": source_path,
        "sections": [asdict(section) for section in sections],
        "slides": [slide.to_dict() for slide in slides],
        "generation": generation_status(pdir, sections, slides),
        "outputs": {
            "video": (pdir / "ders.mp4").exists(),
            "audio": (pdir / "ders.mp3").exists(),
        },
    }


@app.get("/api/bootstrap")
def bootstrap():
    settings = load_settings()
    projects = []
    for pdir in sorted(PROJECTS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not pdir.is_dir() or not (pdir / "raw_sections.json").exists():
            continue
        try:
            raw_count = len(json.loads((pdir / "raw_sections.json").read_text(encoding="utf-8")))
            slide_count = len(json.loads((pdir / "script.json").read_text(encoding="utf-8"))) if (pdir / "script.json").exists() else 0
        except (OSError, ValueError):
            continue
        projects.append({"id": pdir.name, "sections": raw_count, "slides": slide_count})
    return {
        "settings": settings,
        "keysConfigured": {
            "gemini": bool(get_api_key("GEMINI_API_KEY")),
            "openai": bool(get_api_key("OPENAI_API_KEY")),
            "elevenlabs": bool(get_api_key("ELEVENLABS_API_KEY")),
        },
        "themes": [{"id": key, "label": value} for key, value in THEME_LABELS.items()],
        "ttsProviders": [{"id": key, "label": value} for key, value in PROVIDER_LABELS.items()],
        "models": {
            "gemini": KNOWN_MODELS,
            "geminiDefault": GEMINI_DEFAULT_MODEL,
            "openaiDefault": OPENAI_DEFAULT_MODEL,
            "openaiEndpoint": DEFAULT_ENDPOINT,
        },
        "projects": projects[:20],
    }


@app.get("/api/projects/{project_id}")
def get_project(project_id: str):
    return _project_payload(_project_dir(project_id))


@app.post("/api/source/path")
def parse_source_path(payload: dict = Body(...)):
    source_path = Path(str(payload.get("path", ""))).expanduser()
    if not source_path.is_file():
        raise HTTPException(400, "Kaynak dosya bulunamadı.")
    try:
        pdir, _sections = parse_and_cache(source_path)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    settings = load_settings()
    settings["last_source"] = str(source_path.resolve())
    save_settings(settings)
    return _project_payload(pdir)


@app.post("/api/source/upload")
async def upload_source(file: UploadFile = File(...)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".md", ".pptx", ".pdf"}:
        raise HTTPException(400, "Yalnızca .md, .pptx ve .pdf dosyaları destekleniyor.")
    safe_stem = re.sub(r"[^\wığüşöçİĞÜŞÖÇ.-]+", "_", Path(file.filename or "kaynak").stem)
    target = UPLOAD_DIR / f"{safe_stem}_{uuid.uuid4().hex[:8]}{suffix}"
    size = 0
    with target.open("wb") as output:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > 100 * 1024 * 1024:
                output.close()
                target.unlink(missing_ok=True)
                raise HTTPException(413, "Dosya 100 MB sınırını aşıyor.")
            output.write(chunk)
    try:
        pdir, _sections = parse_and_cache(target, project_name=file.filename or "kaynak")
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(400, str(exc)) from exc
    return _project_payload(pdir)


@app.put("/api/projects/{project_id}/slides")
def replace_slides(project_id: str, payload: dict = Body(...)):
    pdir = _project_dir(project_id)
    raw_slides = payload.get("slides")
    if not isinstance(raw_slides, list):
        raise HTTPException(400, "slides bir dizi olmalı.")
    slides = [Slide.from_dict(item) for item in raw_slides]
    save_script(pdir, slides)
    return {"slides": [slide.to_dict() for slide in slides]}


@app.post("/api/projects/{project_id}/generation/reset")
def reset_generation_progress(project_id: str):
    pdir = _project_dir(project_id)
    sections = load_raw_sections(pdir)
    slides = _slides_or_empty(pdir)
    reset_checkpoint(pdir)
    return {"generation": generation_status(pdir, sections, slides)}


@app.post("/api/projects/{project_id}/generate")
def generate_script(project_id: str, payload: dict = Body(...)):
    pdir = _project_dir(project_id)
    sections = load_raw_sections(pdir)
    selected = payload.get("sectionIndexes", [])
    if not isinstance(selected, list) or not selected:
        raise HTTPException(400, "En az bir bölüm seçmelisin.")
    try:
        selected_indexes = sorted({int(index) for index in selected})
        if any(index < 0 or index >= len(sections) for index in selected_indexes):
            raise IndexError
        selected_items = [(index, sections[index]) for index in selected_indexes]
    except (ValueError, TypeError, IndexError) as exc:
        raise HTTPException(400, "Bölüm seçimi geçersiz.") from exc

    existing = _slides_or_empty(pdir)
    resume_completed = bool(payload.get("resumeCompleted", True))
    checkpoint = ensure_checkpoint(pdir, sections, existing)
    already_completed = set(completed_indexes(checkpoint, sections))
    skipped_count = 0
    if resume_completed:
        skipped_count = sum(index in already_completed for index, _section in selected_items)
        selected_items = [item for item in selected_items if item[0] not in already_completed]
    selected_sections = [section for _index, section in selected_items]
    provider_name = str(payload.get("provider", "agent"))
    style = str(payload.get("style", "")).strip()
    single_request = bool(payload.get("singleRequest", False))
    selected_characters = sum(
        len(section.title)
        + len(section.text)
        + sum(len(block) for block in section.code_blocks)
        for section in selected_sections
    )
    if single_request and not _single_request_is_safe(
        len(selected_sections), selected_characters
    ):
        raise HTTPException(
            400,
            "Tek istek seçimi bu kaynak için çok büyük. 12 bölüm veya 24.000 karakteri "
            "aşan seçimlerde chunk modunu kullan; Claude parçaları aynı oturumda sürdürecek.",
        )
    max_sections = max(1, min(int(payload.get("maxSections", 4)), 20))
    insert_after = payload.get("insertAfter")
    insertion_cursor = len(existing) if insert_after is None else max(0, min(int(insert_after) + 1, len(existing)))

    api_key = str(payload.get("apiKey", "")).strip()
    if api_key and provider_name == "gemini":
        save_api_key("GEMINI_API_KEY", api_key)
    elif api_key and provider_name == "openai":
        save_api_key("OPENAI_API_KEY", api_key)

    settings = load_settings()
    settings.update({
        "llm_provider": provider_name,
        "single_request": single_request,
        "agent_command": str(payload.get("agentCommand", settings["agent_command"])),
        "agent_reuse_session": bool(payload.get("reuseSession", True)),
        "agent_max_sections": max_sections,
        "agent_timeout_sec": max(60, int(payload.get("timeout", 900))),
        "gemini_model": str(payload.get("geminiModel", settings["gemini_model"])),
        "openai_endpoint": str(payload.get("openaiEndpoint", settings["openai_endpoint"])),
        "openai_model": str(payload.get("openaiModel", settings["openai_model"])),
    })
    save_settings(settings)

    def work(job_id: str):
        nonlocal insertion_cursor
        working_slides = list(existing)

        if not selected_items:
            return {
                "slides": [slide.to_dict() for slide in working_slides],
                "generatedCount": 0,
                "skippedSectionCount": skipped_count,
                "generation": generation_status(pdir, sections, working_slides),
            }

        if provider_name == "agent":
            from app.llm.agent_cli_provider import AgentCliNarrationGenerator

            generator = AgentCliNarrationGenerator(
                command=settings["agent_command"],
                timeout=settings["agent_timeout_sec"],
                # Tek çağrıda sürdürülecek ikinci bir turn yok; session bayrakları gereksizdir.
                reuse_session=_effective_agent_session(
                    settings["agent_reuse_session"], single_request
                ),
                session_id=saved_agent_session(checkpoint, settings["agent_command"])
                if _effective_agent_session(settings["agent_reuse_session"], single_request)
                else None,
            )
            interval = 0.25
        elif provider_name == "gemini":
            from app.llm.gemini_provider import GeminiNarrationGenerator

            generator = GeminiNarrationGenerator(
                model=settings["gemini_model"],
                api_key=api_key or None,
            )
            interval = 1.0
        elif provider_name == "openai":
            from app.llm.openai_compatible_provider import OpenAICompatibleNarrationGenerator

            generator = OpenAICompatibleNarrationGenerator(
                endpoint=settings["openai_endpoint"],
                model=settings["openai_model"],
                api_key=api_key or None,
                timeout=settings["agent_timeout_sec"],
            )
            interval = 1.0
        else:
            raise ValueError("Bilinmeyen LLM sağlayıcısı.")

        def on_progress(index: int, total: int, title: str):
            jobs.update(
                job_id,
                current=index,
                total=total,
                progress=round((index - 1) * 100 / max(total, 1)),
                message=f"{index}/{total} · {title}",
            )

        section_index_by_identity = {
            id(section): index for index, section in selected_items
        }

        def on_chunk_complete(
            index: int,
            total: int,
            source_chunk,
            new_slides: list[Slide],
        ):
            nonlocal insertion_cursor
            source_items = [
                (section_index_by_identity[id(section)], section)
                for section in source_chunk
            ]
            chunk_insertion_index = insertion_cursor
            begin_chunk_commit(
                pdir,
                checkpoint,
                source_items,
                chunk_insertion_index,
                new_slides,
                agent_session_id=generator.session_id if provider_name == "agent" else None,
                agent_command=settings["agent_command"] if provider_name == "agent" else None,
            )
            working_slides[insertion_cursor:insertion_cursor] = new_slides
            insertion_cursor += len(new_slides)
            save_script(pdir, working_slides)
            finish_chunk_commit(pdir, checkpoint)
            jobs.update(
                job_id,
                current=index,
                total=total,
                progress=round(index * 100 / max(total, 1)),
                message=f"{index}/{total} tamamlandı · {len(new_slides)} slayt",
                result={
                    "slides": [slide.to_dict() for slide in working_slides],
                    "generation": generation_status(pdir, sections, working_slides),
                    "skippedSectionCount": skipped_count,
                },
            )

        generated = generator.generate_chunked(
            selected_sections,
            style,
            progress_cb=on_progress,
            single_request=single_request,
            request_interval_sec=interval,
            max_sections_per_chunk=max_sections,
            chunk_completed_cb=on_chunk_complete,
            initial_context_slides=existing,
        )
        result = {
            "slides": [slide.to_dict() for slide in working_slides],
            "generatedCount": len(generated),
            "skippedSectionCount": skipped_count,
            "generation": generation_status(pdir, sections, working_slides),
        }
        if provider_name == "agent":
            result["sessionId"] = generator.session_id
            result["costTelemetryUsd"] = generator.total_cost_usd
        return result

    return {"jobId": jobs.create("script", work)}


@app.post("/api/projects/{project_id}/preview")
def preview_slide(project_id: str, payload: dict = Body(...)):
    pdir = _project_dir(project_id)
    slides = _slides_or_empty(pdir)
    raw_slide = payload.get("slide")
    if raw_slide:
        slide = Slide.from_dict(raw_slide)
    elif slides:
        index = max(0, min(int(payload.get("index", 0)), len(slides) - 1))
        slide = slides[index]
    else:
        slide = Slide(
            title="Modern Ders Deneyimi",
            bullets=["Net bir anlatı akışı", "İçeriğe uyumlu görsel sistem", "Tutarlı ve profesyonel çıktı"],
            narration="Tema önizlemesi",
        )
    index = max(0, int(payload.get("index", 0)))
    preview_path = pdir / "assets" / "web_preview.png"
    render_slide(
        slide,
        index + 1,
        max(len(slides), 1),
        "DERS STÜDYOSU",
        preview_path,
        theme_preset=str(payload.get("theme", "auto")),
    )
    return {"url": f"/api/projects/{project_id}/preview.png?v={time.time_ns()}"}


@app.get("/api/projects/{project_id}/preview.png")
def preview_image(project_id: str):
    path = _project_dir(project_id) / "assets" / "web_preview.png"
    if not path.exists():
        raise HTTPException(404, "Önizleme henüz oluşturulmadı.")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/api/voices/{provider_name}")
def list_voices(provider_name: str):
    try:
        return {"voices": list_tts_voices(provider_name)}
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


def _render_in_isolated_process(
    job_id: str,
    pdir: Path,
    slides: list[Slide],
    provider_name: str,
    voice: str,
    rate: str,
    options: VideoOptions,
) -> dict[str, Any]:
    """Keep native TTS/torch failures outside the long-running API process."""
    job_dir = CACHE_DIR / "web_jobs"
    job_dir.mkdir(parents=True, exist_ok=True)
    spec_path = job_dir / f"{job_id}.json"
    spec_path.write_text(
        json.dumps(
            {
                "projectDir": str(pdir),
                "slides": [slide.to_dict() for slide in slides],
                "provider": provider_name,
                "voice": voice,
                "rate": rate,
                "options": asdict(options),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(
        [sys.executable, "-m", "studio_web.render_worker", str(spec_path)],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creation_flags,
    )
    final_result = None
    diagnostic_lines: list[str] = []
    try:
        assert process.stdout is not None
        for line in process.stdout:
            line = line.rstrip()
            if not line.startswith("__DERS_JOB__"):
                if line:
                    diagnostic_lines.append(line[-500:])
                    diagnostic_lines = diagnostic_lines[-8:]
                continue
            event = json.loads(line.removeprefix("__DERS_JOB__"))
            if event.get("type") == "progress":
                index, total = int(event["current"]), int(event["total"])
                jobs.update(
                    job_id,
                    current=index,
                    total=total,
                    progress=round((index - 1) * 100 / max(total, 1)),
                    message=f"{index}/{total} · {event.get('title', '')}",
                )
            elif event.get("type") == "complete":
                final_result = event["result"]
            elif event.get("type") == "error":
                raise RuntimeError(str(event.get("message", "Render worker hata verdi.")))
        return_code = process.wait()
        if return_code != 0:
            detail = "\n".join(diagnostic_lines[-4:])
            if provider_name == "coqui":
                raise RuntimeError(
                    "Coqui/PyTorch izole render sürecinde native olarak kapandı; web arayüzü "
                    "çalışmaya devam ediyor. GPU/RAM baskısı veya torch uyumsuzluğu olabilir. "
                    "Şimdilik Edge-TTS ya da Piper seçebilirsin."
                    + (f"\n{detail}" if detail else "")
                )
            raise RuntimeError(f"Render süreci exit {return_code} ile kapandı.\n{detail}")
        if final_result is None:
            raise RuntimeError("Render süreci sonuç üretmeden kapandı.")
        return final_result
    finally:
        if process.poll() is None:
            process.kill()
        spec_path.unlink(missing_ok=True)


@app.post("/api/projects/{project_id}/render")
def start_render(project_id: str, payload: dict = Body(...)):
    pdir = _project_dir(project_id)
    slides = _slides_or_empty(pdir)
    if not slides:
        raise HTTPException(400, "Önce bir transkript oluşturmalısın.")
    provider_name = str(payload.get("ttsProvider", "edge"))
    voice = str(payload.get("voice", "tr-TR-AhmetNeural"))
    rate = str(payload.get("rate", "+0%"))
    eleven_key = str(payload.get("elevenlabsKey", "")).strip()
    if eleven_key:
        save_api_key("ELEVENLABS_API_KEY", eleven_key)
    options = VideoOptions(
        subtitles=bool(payload.get("subtitles", True)),
        fade_transitions=bool(payload.get("fadeTransitions", True)),
        ken_burns=bool(payload.get("kenBurns", False)),
        theme_preset=str(payload.get("theme", "auto")),
    )
    settings = load_settings()
    settings.update({
        "tts_provider": provider_name,
        "tts_voice": voice,
        "tts_rate": rate,
        "subtitles": options.subtitles,
        "fade_transitions": options.fade_transitions,
        "ken_burns": options.ken_burns,
        "theme_preset": options.theme_preset,
    })
    save_settings(settings)

    def work(job_id: str):
        return _render_in_isolated_process(
            job_id, pdir, slides, provider_name, voice, rate, options
        )

    return {"jobId": jobs.create("video", work)}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    try:
        return jobs.get(job_id)
    except KeyError as exc:
        raise HTTPException(404, "İş bulunamadı.") from exc


@app.get("/api/projects/{project_id}/output/{kind}")
def get_output(project_id: str, kind: str):
    pdir = _project_dir(project_id)
    if kind == "video":
        path, media_type = pdir / "ders.mp4", "video/mp4"
    elif kind == "audio":
        path, media_type = pdir / "ders.mp3", "audio/mpeg"
    else:
        raise HTTPException(404, "Bilinmeyen çıktı.")
    if not path.exists():
        raise HTTPException(404, "Çıktı henüz oluşturulmadı.")
    return FileResponse(path, media_type=media_type, filename=path.name)


@app.post("/api/projects/{project_id}/open-output")
def open_output(project_id: str):
    pdir = _project_dir(project_id)
    if os.name != "nt":
        raise HTTPException(400, "Klasör açma yalnızca Windows masaüstünde destekleniyor.")
    os.startfile(pdir)
    return {"ok": True}


if WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="webui")
