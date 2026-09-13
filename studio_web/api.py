from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.asset_integrity import audit_assets
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
from app.pipeline import (
    list_snapshots,
    load_raw_sections,
    load_script,
    parse_and_cache,
    restore_snapshot,
    save_script,
    slugify,
    snapshot_script,
)
from app.chapters import export_all_chapters, identify_chapters, missing_render_indexes
from app.cost_ledger import record as record_cost, summarize_all_projects, summarize_project
from app.export import EXPORT_BUILDERS, write_export
from app.flashcards import (
    create_deck,
    deck_summary,
    delete_deck,
    get_deck,
    list_decks,
    regenerate_deck_cards,
    update_card,
)
from app.quality_gate import load_or_analyze_quality
from app.spaced_repetition import RATINGS, schedule_review
from app.regenerate import apply_regeneration, resolve_source_sections
from app.render_estimate import estimate_render
from app.tts.pronunciation import (
    PRONUNCIATION_MAP,
    load_overrides,
    normalize_pronunciation,
    save_overrides,
)
from app.tts import PROVIDER_LABELS, list_voices as list_tts_voices
from app.video.slide_renderer import render_slide
from app.video.themes import THEME_LABELS
from studio_web.batch_queue import BatchQueueStore
from studio_web.job_store import PersistentJobStore

WEB_DIST = ROOT / "webui" / "dist"
UPLOAD_DIR = CACHE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


jobs = PersistentJobStore(CACHE_DIR / "web_job_status")
queue = BatchQueueStore(CACHE_DIR / "batch_queue")
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


def _resolve_vision_api_key(vision_enrich: bool, provided_key: str) -> str | None:
    """Görsel anlama, anlatım sağlayıcısından bağımsız her zaman Gemini kullanır
    (app/vision_caption.py) — bu yüzden ayrı bir anahtar çözümlemesi gerekir.
    Kullanıcı yeni bir anahtar girdiyse kaydeder (generate_script'teki apiKey
    kaydetme mantığıyla aynı desen).
    """
    if not vision_enrich:
        return None
    provided_key = provided_key.strip()
    if provided_key:
        save_api_key("GEMINI_API_KEY", provided_key)
        return provided_key
    saved_key = get_api_key("GEMINI_API_KEY")
    if not saved_key:
        raise HTTPException(
            400,
            "Görsel anlama için bir Gemini API anahtarı gerekli (anlatım "
            "sağlayıcından bağımsız, sadece bu adım için kullanılır).",
        )
    return saved_key


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
        "pageMode": any(section.page_image for section in sections),
        "diagramsExtracted": sum(1 for section in sections if section.embedded_image),
        "sections": [asdict(section) for section in sections],
        "slides": [slide.to_dict() for slide in slides],
        "generation": generation_status(pdir, sections, slides),
        "quality": load_or_analyze_quality(pdir, slides),
        "assets": audit_assets(pdir, len(slides)),
        "outputs": {
            "video": (pdir / "ders.mp4").exists(),
            "audio": (pdir / "ders.mp3").exists(),
        },
        "costSummary": summarize_project(pdir),
    }


@app.get("/api/cost-summary")
def cost_summary():
    """Tüm projeler genelinde kullanım/maliyet özeti — bkz. app/cost_ledger.py.

    Yalnızca kendini bildiren sağlayıcıların (Claude Agent CLI) GERÇEK
    maliyeti toplanır; diğerleri için sadece kelime/istek sayısı gösterilir.
    """
    return summarize_all_projects(PROJECTS_DIR)


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
    page_mode = bool(payload.get("pageMode", False))
    vision_enrich = bool(payload.get("visionEnrich", False))
    extract_diagrams = bool(payload.get("extractDiagrams", False))
    if vision_enrich and source_path.suffix.lower() != ".pdf":
        raise HTTPException(400, "Görsel anlama şu an yalnızca PDF için destekleniyor.")
    if extract_diagrams and source_path.suffix.lower() != ".pdf":
        raise HTTPException(400, "Diyagram/görsel çıkarma şu an yalnızca PDF için destekleniyor.")
    vision_api_key = _resolve_vision_api_key(vision_enrich, str(payload.get("visionApiKey", "")))
    try:
        pdir, _sections = parse_and_cache(
            source_path, page_mode=page_mode, vision_enrich=vision_enrich,
            vision_api_key=vision_api_key, extract_diagrams=extract_diagrams,
        )
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    settings = load_settings()
    settings["last_source"] = str(source_path.resolve())
    save_settings(settings)
    return _project_payload(pdir)


@app.post("/api/source/upload")
async def upload_source(file: UploadFile = File(...), page_mode: bool = Form(False),
                         vision_enrich: bool = Form(False), vision_api_key: str = Form(""),
                         extract_diagrams: bool = Form(False)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".md", ".pptx", ".pdf"}:
        raise HTTPException(400, "Yalnızca .md, .pptx ve .pdf dosyaları destekleniyor.")
    if vision_enrich and suffix != ".pdf":
        raise HTTPException(400, "Görsel anlama şu an yalnızca PDF için destekleniyor.")
    if extract_diagrams and suffix != ".pdf":
        raise HTTPException(400, "Diyagram/görsel çıkarma şu an yalnızca PDF için destekleniyor.")
    resolved_vision_key = _resolve_vision_api_key(vision_enrich, vision_api_key)
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
        pdir, _sections = parse_and_cache(
            target, project_name=file.filename or "kaynak", page_mode=page_mode,
            vision_enrich=vision_enrich, vision_api_key=resolved_vision_key,
            extract_diagrams=extract_diagrams,
        )
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(400, str(exc)) from exc
    return _project_payload(pdir)


@app.post("/api/queue")
def add_to_queue(payload: dict = Body(...)):
    """Toplu/kuyruk modu: yerel bir kaynak dosyasını (PDF/PPTX/MD) sıraya ekler.

    Ekleme anındaki anlatı/video ayarlarının bir anlık görüntüsünü (llmSettings/
    videoSettings) alır — kuyruk işlenirken ayarlar değişse bile bu öğe kendi
    anlık görüntüsüyle işlenir, sürpriz olmasın diye.
    """
    source_path = str(payload.get("sourcePath", "")).strip()
    if not source_path or not Path(source_path).is_file():
        raise HTTPException(400, "Kaynak dosya bulunamadı.")
    suffix = Path(source_path).suffix.lower()
    if suffix not in {".md", ".pptx", ".pdf"}:
        raise HTTPException(400, "Yalnızca .md, .pptx ve .pdf dosyaları destekleniyor.")
    page_mode = bool(payload.get("pageMode", False))
    vision_enrich = bool(payload.get("visionEnrich", False))
    if (page_mode or vision_enrich) and suffix != ".pdf":
        raise HTTPException(400, "Sayfa modu/görsel anlama şu an yalnızca PDF için destekleniyor.")
    item_id = queue.add(
        source_path=source_path,
        project_name=str(payload.get("projectName") or Path(source_path).stem),
        page_mode=page_mode,
        vision_enrich=vision_enrich,
        vision_api_key=str(payload.get("visionApiKey", "")),
        llm_settings=dict(payload.get("llmSettings") or {}),
        video_settings=dict(payload.get("videoSettings") or {}),
    )
    return {"id": item_id}


@app.get("/api/queue")
def list_queue():
    return {"items": queue.list()}


@app.delete("/api/queue/{item_id}")
def remove_from_queue(item_id: str):
    try:
        queue.remove(item_id)
    except KeyError as exc:
        raise HTTPException(404, "Kuyruk öğesi bulunamadı.") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True}


@app.put("/api/projects/{project_id}/slides")
def replace_slides(project_id: str, payload: dict = Body(...)):
    pdir = _project_dir(project_id)
    raw_slides = payload.get("slides")
    if not isinstance(raw_slides, list):
        raise HTTPException(400, "slides bir dizi olmalı.")
    slides = [Slide.from_dict(item) for item in raw_slides]
    save_script(pdir, slides)
    return {
        "slides": [slide.to_dict() for slide in slides],
        "quality": load_or_analyze_quality(pdir, slides),
        "assets": audit_assets(pdir, len(slides)),
    }


@app.post("/api/projects/{project_id}/slides/{index}/regenerate")
def regenerate_slide(project_id: str, index: int, payload: dict = Body(...)):
    """Regenerate only the slide(s) that came from the same source section(s).

    Unlike /generate, this is a single direct LLM call scoped to one small
    source group — no chunking, no session/checkpoint bookkeeping. A snapshot
    of script.json is taken first so a bad regeneration is always recoverable.
    """
    pdir = _project_dir(project_id)
    slides = _slides_or_empty(pdir)
    if not (0 <= index < len(slides)):
        raise HTTPException(404, "Slayt bulunamadı.")
    if not slides[index].source_section_ids:
        raise HTTPException(
            400,
            "Bu slaytın kayıtlı bir kaynağı yok (elle eklenmiş olabilir); yeniden üretilemez.",
        )

    provider_name = str(payload.get("provider", "agent"))
    style = str(payload.get("style", "")).strip()
    api_key = str(payload.get("apiKey", "")).strip()
    settings = load_settings()

    def work(job_id: str):
        current_slides = _slides_or_empty(pdir)
        if not (0 <= index < len(current_slides)):
            raise ValueError("Slayt listesi değişti; bu slayt artık mevcut değil.")
        target = current_slides[index]
        if not target.source_section_ids:
            raise ValueError("Bu slaytın kaynağı yok; yeniden üretilemez.")

        sections = load_raw_sections(pdir)
        source_sections = resolve_source_sections(sections, target.source_section_ids)
        if not source_sections:
            raise ValueError(
                "Kaynak bölüm(ler) artık bulunamıyor (kaynak dosya değişmiş olabilir)."
            )

        if provider_name == "agent":
            from app.llm.agent_cli_provider import AgentCliNarrationGenerator

            generator = AgentCliNarrationGenerator(
                command=str(payload.get("agentCommand", settings["agent_command"])),
                timeout=max(60, int(payload.get("timeout", settings["agent_timeout_sec"]))),
                # Bu tek seferlik, bağımsız bir çağrı; ana üretim oturumuna karışmasın.
                reuse_session=False,
            )
        elif provider_name == "gemini":
            from app.llm.gemini_provider import GeminiNarrationGenerator

            generator = GeminiNarrationGenerator(
                model=str(payload.get("geminiModel", settings["gemini_model"])),
                api_key=api_key or None,
            )
        elif provider_name == "openai":
            from app.llm.openai_compatible_provider import OpenAICompatibleNarrationGenerator

            generator = OpenAICompatibleNarrationGenerator(
                endpoint=str(payload.get("openaiEndpoint", settings["openai_endpoint"])),
                model=str(payload.get("openaiModel", settings["openai_model"])),
                api_key=api_key or None,
                timeout=max(60, int(payload.get("timeout", settings["agent_timeout_sec"]))),
            )
        else:
            raise ValueError("Bilinmeyen LLM sağlayıcısı.")

        fresh_slides = generator.generate(source_sections, style)
        snapshot_script(pdir, reason="before-regenerate")
        updated = apply_regeneration(current_slides, index, fresh_slides)
        save_script(pdir, updated)
        result = {
            "slides": [slide.to_dict() for slide in updated],
            "quality": load_or_analyze_quality(pdir, updated),
            "assets": audit_assets(pdir, len(updated)),
        }
        regenerated_words = sum(len((s.narration or "").split()) for s in fresh_slides)
        if provider_name == "agent":
            result["costTelemetryUsd"] = generator.total_cost_usd
            record_cost(pdir, provider="agent", kind="regenerate",
                        usd=generator.total_cost_usd, words=regenerated_words)
        elif fresh_slides:
            record_cost(pdir, provider=provider_name, kind="regenerate", words=regenerated_words)
        return result

    return {"jobId": jobs.create("regenerate", work)}


@app.get("/api/projects/{project_id}/snapshots")
def get_snapshots(project_id: str):
    pdir = _project_dir(project_id)
    return {"snapshots": list_snapshots(pdir)}


@app.post("/api/projects/{project_id}/snapshots/{filename}/restore")
def restore_project_snapshot(project_id: str, filename: str):
    pdir = _project_dir(project_id)
    try:
        slides = restore_snapshot(pdir, filename)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        "slides": [slide.to_dict() for slide in slides],
        "quality": load_or_analyze_quality(pdir, slides),
        "assets": audit_assets(pdir, len(slides)),
        "snapshots": list_snapshots(pdir),
    }


@app.post("/api/projects/{project_id}/quality")
def refresh_project_quality(project_id: str):
    pdir = _project_dir(project_id)
    slides = _slides_or_empty(pdir)
    return {"quality": load_or_analyze_quality(pdir, slides)}


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
    # "Sayfaları birebir slayt olarak kullan" modunda parse_and_cache her bölüme
    # bir sayfa görüntüsü yazmıştır — proje genelinde tek bir bayrak yerine bunu
    # doğrudan bölümlerden çıkarıyoruz (ayrı bir durum dosyasıyla senkron tutma
    # derdi olmadan).
    page_mode = any(section.page_image for section in sections)
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
    # Sayfa modunda her sayfa kendi başına tek bir slayt olmak ZORUNDA (görüntüsü
    # zaten o sayfa) — tek istekte tüm sayfaları birden göndermek bu varsayımı
    # kırar, o yüzden bu modda singleRequest isteği ne olursa olsun yok sayılır.
    single_request = bool(payload.get("singleRequest", False)) and not page_mode
    target_duration_minutes: float | None = None
    if bool(payload.get("durationLimitEnabled", False)):
        try:
            target_duration_minutes = float(payload.get("targetDurationMinutes"))
        except (TypeError, ValueError):
            raise HTTPException(400, "Süre hedefi sayısal bir dakika değeri olmalı.")
        if not (1 <= target_duration_minutes <= 1000):
            raise HTTPException(400, "Süre hedefi 1 ile 1000 dakika arasında olmalı.")
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
    # Sayfa modunda LLM çağrısı başına tam olarak 1 bölüm (1 sayfa) gider.
    max_sections = 1 if page_mode else max(1, min(int(payload.get("maxSections", 4)), 20))
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
                "quality": load_or_analyze_quality(pdir, working_slides),
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
            if page_mode and len(new_slides) == 1 and source_chunk:
                new_slides[0].background_image = source_chunk[0].page_image
            # Bir çıkarılmış diyagram/görseli hangi slayda bağlayacağımızı
            # güvenle sadece chunk TEK bir kaynak bölümden oluşuyorsa
            # biliyoruz (LLM'in çok bölümlü bir chunk'ta hangi slaytın hangi
            # bölümden geldiğini kesin olarak işaretlemesi yok) — belirsizse
            # yanlış slayda görsel koymak yerine hiç koymuyoruz.
            if len(source_chunk) == 1 and new_slides and source_chunk[0].embedded_image:
                new_slides[0].embedded_image = source_chunk[0].embedded_image
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
                    "quality": load_or_analyze_quality(pdir, working_slides),
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
            target_duration_minutes=target_duration_minutes,
            single_slide_per_section=page_mode,
        )
        result = {
            "slides": [slide.to_dict() for slide in working_slides],
            "generatedCount": len(generated),
            "skippedSectionCount": skipped_count,
            "generation": generation_status(pdir, sections, working_slides),
            "quality": load_or_analyze_quality(pdir, working_slides),
        }
        generated_words = sum(len((s.narration or "").split()) for s in generated)
        if provider_name == "agent":
            result["sessionId"] = generator.session_id
            result["costTelemetryUsd"] = generator.total_cost_usd
            record_cost(pdir, provider="agent", kind="generate",
                        usd=generator.total_cost_usd, words=generated_words)
        elif generated:
            record_cost(pdir, provider=provider_name, kind="generate", words=generated_words)
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


def _pronunciation_entries() -> list[dict[str, Any]]:
    overrides = load_overrides()
    merged = dict(PRONUNCIATION_MAP)
    merged.update(overrides)
    return [
        {"term": term, "phonetic": phonetic, "isOverride": term in overrides}
        for term, phonetic in sorted(merged.items())
    ]


@app.get("/api/pronunciation")
def get_pronunciation():
    return {"entries": _pronunciation_entries()}


@app.put("/api/pronunciation")
def put_pronunciation(payload: dict = Body(...)):
    overrides = payload.get("overrides")
    if not isinstance(overrides, dict):
        raise HTTPException(400, "overrides bir { terim: telaffuz } sözlüğü olmalı.")
    save_overrides(overrides)
    return {"entries": _pronunciation_entries()}


@app.post("/api/pronunciation/preview")
def preview_pronunciation(payload: dict = Body(...)):
    text = str(payload.get("text", "")).strip()
    if not text:
        raise HTTPException(400, "Önizlenecek bir metin gir.")
    if len(text) > 300:
        raise HTTPException(400, "Önizleme metni en fazla 300 karakter olabilir.")
    normalized = normalize_pronunciation(text)
    from app.tts.edge_provider import EdgeTTSProvider

    preview_path = CACHE_DIR / "pronunciation_preview.mp3"
    try:
        EdgeTTSProvider().synthesize(normalized, "tr-TR-AhmetNeural", preview_path, "+0%")
    except Exception as exc:
        raise HTTPException(502, f"Önizleme sesi üretilemedi: {exc}") from exc
    return {"normalizedText": normalized, "audioUrl": f"/api/pronunciation/preview.mp3?v={time.time_ns()}"}


@app.get("/api/pronunciation/preview.mp3")
def get_pronunciation_preview():
    path = CACHE_DIR / "pronunciation_preview.mp3"
    if not path.exists():
        raise HTTPException(404, "Önizleme henüz oluşturulmadı.")
    return FileResponse(path, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})


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


@app.get("/api/projects/{project_id}/render-estimate")
def render_estimate(project_id: str, provider: str = "edge"):
    pdir = _project_dir(project_id)
    slides = _slides_or_empty(pdir)
    if not slides:
        raise HTTPException(400, "Önce bir transkript oluşturmalısın.")
    return estimate_render(pdir, slides, provider)


@app.post("/api/projects/{project_id}/render")
def start_render(project_id: str, payload: dict = Body(...)):
    pdir = _project_dir(project_id)
    slides = _slides_or_empty(pdir)
    if not slides:
        raise HTTPException(400, "Önce bir transkript oluşturmalısın.")
    quality = load_or_analyze_quality(pdir, slides)
    if not quality["renderAllowed"]:
        first_issue = next(
            (item["message"] for item in quality["issues"] if item["severity"] == "error"),
            "Kritik anlatı sorunu bulundu.",
        )
        raise HTTPException(409, f"Kalite kapısı renderı durdurdu: {first_issue}")
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


@app.get("/api/projects/{project_id}/export/{kind}")
def export_study_material(project_id: str, kind: str):
    """Deterministic study-material export (no LLM call): notes/transcript/anki/quiz."""
    if kind not in EXPORT_BUILDERS:
        raise HTTPException(404, "Bilinmeyen dışa aktarım türü.")
    pdir = _project_dir(project_id)
    slides = _slides_or_empty(pdir)
    if not slides:
        raise HTTPException(400, "Önce bir transkript oluşturmalısın.")
    filename, media_type, _builder = EXPORT_BUILDERS[kind]
    path = write_export(pdir, kind, slides)
    return FileResponse(path, media_type=media_type, filename=filename)


def _deck_payload(deck: dict) -> dict:
    return {**deck, "summary": deck_summary(deck["cards"])}


@app.get("/api/projects/{project_id}/flashcards/decks")
def list_flashcard_decks(project_id: str):
    """Bu projedeki tüm desteleri (kartlarıyla birlikte) listeler.

    Gerçek Anki'deki gibi bir projede birden fazla deste olabilir — hiçbiri
    otomatik oluşturulmaz, kullanıcı "Yeni Deste" ile kendi açar (bkz.
    create_deck). Bu, ileride eklenecek YZ ile üretim seçeneğinin de aynı
    listeye "kind": "llm" olarak sorunsuz oturmasını sağlıyor.
    """
    pdir = _project_dir(project_id)
    return {"decks": [_deck_payload(d) for d in list_decks(pdir)]}


@app.post("/api/projects/{project_id}/flashcards/decks")
def create_flashcard_deck(project_id: str, payload: dict = Body(...)):
    pdir = _project_dir(project_id)
    slides = _slides_or_empty(pdir)
    if not slides:
        raise HTTPException(400, "Önce bir transkript oluşturmalısın.")
    name = str(payload.get("name", "")).strip()
    kind = str(payload.get("kind", "static"))

    if kind == "static":
        try:
            deck = create_deck(pdir, name, kind, slides)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return _deck_payload(deck)

    if kind != "llm":
        raise HTTPException(400, f"Bilinmeyen deste türü: {kind!r}")

    provider_name = str(payload.get("provider", "agent"))
    focus_prompt = str(payload.get("focusPrompt", "")).strip()
    raw_count = payload.get("count")
    try:
        count = int(raw_count) if raw_count not in (None, "") else None
    except (TypeError, ValueError):
        raise HTTPException(400, "Kart sayısı sayısal olmalı.")
    if count is not None and not (1 <= count <= 60):
        raise HTTPException(400, "Kart sayısı 1 ile 60 arasında olmalı.")

    api_key = str(payload.get("apiKey", "")).strip()
    if api_key and provider_name == "gemini":
        save_api_key("GEMINI_API_KEY", api_key)
    elif api_key and provider_name == "openai":
        save_api_key("OPENAI_API_KEY", api_key)

    settings = load_settings()

    def work(job_id: str):
        if provider_name == "agent":
            from app.llm.agent_cli_provider import AgentCliNarrationGenerator

            generator = AgentCliNarrationGenerator(
                command=str(payload.get("agentCommand", settings["agent_command"])),
                timeout=max(60, int(payload.get("timeout", 300))),
                reuse_session=False,
            )
        elif provider_name == "gemini":
            from app.llm.gemini_provider import GeminiNarrationGenerator

            generator = GeminiNarrationGenerator(
                model=str(payload.get("geminiModel", settings["gemini_model"])),
                api_key=api_key or None,
            )
        elif provider_name == "openai":
            from app.llm.openai_compatible_provider import OpenAICompatibleNarrationGenerator

            generator = OpenAICompatibleNarrationGenerator(
                endpoint=str(payload.get("openaiEndpoint", settings["openai_endpoint"])),
                model=str(payload.get("openaiModel", settings["openai_model"])),
                api_key=api_key or None,
                timeout=max(60, int(payload.get("timeout", 300))),
            )
        else:
            raise ValueError("Bilinmeyen LLM sağlayıcısı.")

        try:
            deck = create_deck(
                pdir, name, "llm", slides,
                generator=generator, count=count, focus_prompt=focus_prompt,
            )
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc

        card_words = sum(len(c["front"].split()) + len(c["back"].split()) for c in deck["cards"])
        if provider_name == "agent":
            record_cost(pdir, provider="agent", kind="flashcards",
                        usd=generator.total_cost_usd, words=card_words)
        else:
            record_cost(pdir, provider=provider_name, kind="flashcards", words=card_words)
        return {"deck": _deck_payload(deck)}

    return {"jobId": jobs.create("flashcards", work)}


@app.get("/api/projects/{project_id}/flashcards/decks/{deck_id}")
def get_flashcard_deck(project_id: str, deck_id: str):
    pdir = _project_dir(project_id)
    deck = get_deck(pdir, deck_id)
    if deck is None:
        raise HTTPException(404, "Deste bulunamadı.")
    return _deck_payload(deck)


@app.post("/api/projects/{project_id}/flashcards/decks/{deck_id}/generate")
def regenerate_flashcard_deck(project_id: str, deck_id: str):
    """Bu destenin kartlarını güncel slaytlardan yeniden üretir; İÇERİĞİ
    DEĞİŞMEMİŞ kartların aralıklı tekrar ilerlemesi (ease/interval/due)
    korunur — sadece değişen/silinen slaytların kartları düşer, yenileri
    taze eklenir. Diğer desteler etkilenmez.
    """
    pdir = _project_dir(project_id)
    slides = _slides_or_empty(pdir)
    if not slides:
        raise HTTPException(400, "Önce bir transkript oluşturmalısın.")
    try:
        deck = regenerate_deck_cards(pdir, deck_id, slides)
    except KeyError as exc:
        raise HTTPException(404, "Deste bulunamadı.") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _deck_payload(deck)


@app.delete("/api/projects/{project_id}/flashcards/decks/{deck_id}")
def delete_flashcard_deck(project_id: str, deck_id: str):
    pdir = _project_dir(project_id)
    try:
        delete_deck(pdir, deck_id)
    except KeyError as exc:
        raise HTTPException(404, "Deste bulunamadı.") from exc
    return {"ok": True}


@app.post("/api/projects/{project_id}/flashcards/decks/{deck_id}/cards/{card_id}/review")
def review_flashcard(project_id: str, deck_id: str, card_id: str, payload: dict = Body(...)):
    rating = str(payload.get("rating", ""))
    if rating not in RATINGS:
        raise HTTPException(400, f"Geçersiz değerlendirme; beklenen: {', '.join(RATINGS)}")
    pdir = _project_dir(project_id)
    deck = get_deck(pdir, deck_id)
    if deck is None:
        raise HTTPException(404, "Deste bulunamadı.")
    card = next((c for c in deck["cards"] if c["id"] == card_id), None)
    if card is None:
        raise HTTPException(404, "Kart bulunamadı.")
    try:
        card = update_card(pdir, deck_id, card_id, schedule_review(card, rating))
    except KeyError as exc:
        raise HTTPException(404, "Kart bulunamadı.") from exc
    return {"card": card, "summary": deck_summary(get_deck(pdir, deck_id)["cards"])}


@app.post("/api/projects/{project_id}/flashcards/decks/{deck_id}/cards/{card_id}/suspend")
def toggle_flashcard_suspend(project_id: str, deck_id: str, card_id: str, payload: dict = Body(...)):
    pdir = _project_dir(project_id)
    deck = get_deck(pdir, deck_id)
    if deck is None:
        raise HTTPException(404, "Deste bulunamadı.")
    card = next((c for c in deck["cards"] if c["id"] == card_id), None)
    if card is None:
        raise HTTPException(404, "Kart bulunamadı.")
    suspended = bool(payload.get("suspended", not card.get("suspended", False)))
    try:
        card = update_card(pdir, deck_id, card_id, {"suspended": suspended})
    except KeyError as exc:
        raise HTTPException(404, "Kart bulunamadı.") from exc
    return {"card": card, "summary": deck_summary(get_deck(pdir, deck_id)["cards"])}


@app.get("/api/projects/{project_id}/chapters")
def list_chapters(project_id: str):
    """Read-only chapter breakdown of the current script, and whether it's exportable.

    Splitting only becomes possible once every slide has a rendered segment —
    this reuses those files rather than re-rendering anything.
    """
    pdir = _project_dir(project_id)
    slides = _slides_or_empty(pdir)
    if not slides:
        return {"chapters": [], "allRendered": False, "missingCount": 0, "youtubeChaptersReady": False}
    chapters = identify_chapters(slides)
    missing = missing_render_indexes(pdir, len(slides))
    chapters_dir = pdir / "chapters"
    existing = {p.name for p in chapters_dir.glob("*.mp4")} if chapters_dir.exists() else set()
    payload = []
    for chapter in chapters:
        slug = slugify(chapter.title) or f"bolum-{chapter.index}"
        video_name = f"{chapter.index:02d}-{slug}.mp4"
        audio_name = f"{chapter.index:02d}-{slug}.mp3"
        payload.append({
            "index": chapter.index,
            "title": chapter.title,
            "slideRange": [chapter.start + 1, chapter.end],
            "slideCount": chapter.slide_count,
            "exported": video_name in existing,
            "videoFile": video_name,
            "audioFile": audio_name,
        })
    return {
        "chapters": payload,
        "allRendered": not missing,
        "missingCount": len(missing),
        "youtubeChaptersReady": (chapters_dir / "youtube-chapters.txt").exists(),
    }


@app.post("/api/projects/{project_id}/chapters/export")
def export_chapters(project_id: str):
    pdir = _project_dir(project_id)
    slides = _slides_or_empty(pdir)
    if not slides:
        raise HTTPException(400, "Önce bir transkript oluşturmalısın.")

    def work(job_id: str):
        try:
            return export_all_chapters(pdir, slides)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc

    return {"jobId": jobs.create("chapters", work)}


@app.get("/api/projects/{project_id}/chapters/download/{filename}")
def download_chapter_file(project_id: str, filename: str):
    pdir = _project_dir(project_id)
    chapters_dir = (pdir / "chapters").resolve()
    candidate = (chapters_dir / filename).resolve()
    if candidate.parent != chapters_dir or not candidate.is_file():
        raise HTTPException(404, "Dosya bulunamadı.")
    media_type = (
        "video/mp4" if candidate.suffix == ".mp4"
        else "audio/mpeg" if candidate.suffix == ".mp3"
        else "text/plain"
    )
    return FileResponse(candidate, media_type=media_type, filename=candidate.name)


@app.post("/api/projects/{project_id}/open-output")
def open_output(project_id: str):
    pdir = _project_dir(project_id)
    if os.name != "nt":
        raise HTTPException(400, "Klasör açma yalnızca Windows masaüstünde destekleniyor.")
    os.startfile(pdir)
    return {"ok": True}


if WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="webui")
