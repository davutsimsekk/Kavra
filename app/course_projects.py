"""Course projects with immutable source imports and isolated video workspaces."""
from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from dataclasses import asdict, replace
from pathlib import Path

from app.models import Slide
from app.parsers import parse_source
from app.pipeline import load_raw_sections, slugify

COURSE_FILE = "course.json"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def is_course(pdir: Path) -> bool:
    return (pdir / COURSE_FILE).is_file()


def child_dir(pdir: Path, collection: str, item_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{12}", item_id):
        raise ValueError("Geçersiz öğe kimliği.")
    parent = (pdir / collection).resolve()
    if parent.parent != pdir.resolve():
        raise ValueError("Geçersiz koleksiyon yolu.")
    result = (parent / item_id).resolve()
    if result.parent != parent or not result.is_dir():
        raise FileNotFoundError("Öğe bulunamadı.")
    return result


def create_course(projects_dir: Path, name: str) -> Path:
    name = name.strip()
    if not name or len(name) > 160:
        raise ValueError("Ders adı 1–160 karakter olmalı.")
    pdir = projects_dir / (slugify(name) + "-" + uuid.uuid4().hex[:8])
    pdir.mkdir(parents=True, exist_ok=False)
    write_json(pdir / COURSE_FILE, {"version": 1, "name": name, "createdAt": time.time()})
    return pdir


def list_sources(pdir: Path) -> list[dict]:
    items = []
    for path in (pdir / "sources").glob("*/source.json"):
        try:
            item = read_json(path)
            child_dir(pdir, "sources", item["id"])
            items.append(item)
        except (OSError, ValueError, KeyError):
            continue
    return sorted(items, key=lambda item: (item["createdAt"], item["id"]))


def list_videos(pdir: Path) -> list[dict]:
    items = []
    for path in (pdir / "videos").glob("*/video.json"):
        try:
            item = read_json(path)
            vdir = child_dir(pdir, "videos", item["id"])
            item["slideCount"] = len(read_json(vdir / "script.json")) if (vdir / "script.json").exists() else 0
            item["videoReady"] = (vdir / "ders.mp4").is_file()
            item["audioReady"] = (vdir / "ders.mp3").is_file()
            items.append(item)
        except (OSError, ValueError, KeyError):
            continue
    return sorted(items, key=lambda item: item["createdAt"], reverse=True)


def course_payload(pdir: Path) -> dict:
    meta = read_json(pdir / COURSE_FILE)
    sources, videos = list_sources(pdir), list_videos(pdir)
    return {"id": pdir.name, "kind": "course", **meta, "sources": sources, "videos": videos,
            "sections": [], "slides": [], "outputs": {}}


def new_item_dir(pdir: Path, collection: str) -> Path:
    parent = (pdir / collection).resolve()
    if parent.parent != pdir.resolve():
        raise ValueError("Geçersiz koleksiyon yolu.")
    result = parent / uuid.uuid4().hex[:12]
    result.mkdir(parents=True, exist_ok=False)
    return result


def import_source(pdir: Path, source_path: Path, name: str, **options) -> dict:
    if not is_course(pdir):
        raise ValueError("Bu işlem bir ders projesi gerektirir.")
    suffix = source_path.suffix.lower()
    if suffix not in {".pdf", ".pptx", ".md"}:
        raise ValueError("Yalnızca PDF, PowerPoint ve Markdown destekleniyor.")
    if suffix != ".pdf" and any(options.get(key) for key in ("page_mode", "vision_enrich", "extract_diagrams")):
        raise ValueError("PDF seçenekleri yalnızca PDF kaynaklarında kullanılabilir.")
    if source_path.stat().st_size > 100 * 1024 * 1024:
        raise ValueError("Dosya 100 MB sınırını aşıyor.")
    sdir = new_item_dir(pdir, "sources")
    source_id = sdir.name
    try:
        document = sdir / ("document" + suffix)
        shutil.copy2(source_path, document)
        sections = parse_source(document, pdir=sdir, keep_empty_pages=suffix == '.pdf', **options)
        if not sections:
            raise ValueError("Dosyada kullanılabilir içerik bulunamadı.")
        write_json(sdir / "raw_sections.json", [asdict(section) for section in sections])
        meta = {"id": source_id, "name": name[:240], "filename": document.name,
                "createdAt": time.time(), "sectionCount": len(sections),
                "characterCount": sum(len(s.text) + len(s.title) + sum(map(len, s.code_blocks)) for s in sections),
                "pageMode": bool(options.get("page_mode")), "size": document.stat().st_size,
                "type": suffix[1:]}
        write_json(sdir / "source.json", meta)  # Publish only after parsing succeeds.
        return meta
    except Exception:
        verified = child_dir(pdir, "sources", source_id)
        shutil.rmtree(verified)
        raise


def select_sources(pdir: Path, source_ids) -> list[tuple[dict, list]]:
    if not isinstance(source_ids, list) or not source_ids or len(source_ids) > 50:
        raise ValueError("1–50 kaynak seçmelisin.")
    if any(not isinstance(item, str) for item in source_ids) or len(set(source_ids)) != len(source_ids):
        raise ValueError("Kaynak seçimi yinelenen veya geçersiz kimlik içeriyor.")
    selected = []
    for source_id in source_ids:
        sdir = child_dir(pdir, "sources", source_id)
        meta = read_json(sdir / "source.json")
        selected.append((meta, load_raw_sections(sdir)))
    return selected


def create_video(pdir: Path, name: str, source_ids, presentation_mode: str | None = None) -> Path:
    if not is_course(pdir):
        raise ValueError("Önce bir ders projesi oluşturmalısın.")
    selected = select_sources(pdir, source_ids)
    if presentation_mode not in (None, "pdf", "generated"):
        raise ValueError("Geçersiz sunum biçimi.")
    if presentation_mode == "pdf" and any(meta["type"] != "pdf" for meta, _ in selected):
        raise ValueError("PDF üzerinden anlatmak için yalnızca PDF kaynakları seçmelisin.")
    if presentation_mode is None and len({meta["pageMode"] for meta, _ in selected}) > 1:
        raise ValueError("PDF sayfa görünümünü koruyan ve yeniden tasarlanan kaynakları ayrı videolarda kullan.")
    name = name.strip() or " + ".join(meta["name"] for meta, _ in selected)
    if len(name) > 240:
        name = name[:240]
    vdir = new_item_dir(pdir, "videos")
    try:
        sections, refs = [], []
        for meta, source_sections in selected:
            original_sections = source_sections
            if presentation_mode == "pdf":
                sdir = child_dir(pdir, "sources", meta["id"])
                document = (sdir / meta["filename"]).resolve()
                if document.parent != sdir.resolve() or not document.is_file():
                    raise FileNotFoundError("Kaynak PDF bulunamadı.")
                # Rasterize locally, never run paid vision again. Each video owns
                # its page images; distinct PDFs cannot overwrite page_001.png.
                source_sections = parse_source(
                    document, pdir=vdir / meta["id"], page_mode=True,
                )
                by_title = {section.title: section for section in original_sections}
                source_sections = [
                    replace(section, text=by_title[section.title].text,
                            code_blocks=by_title[section.title].code_blocks)
                    if section.title in by_title else section
                    for section in source_sections
                ]
            for index, section in enumerate(source_sections):
                source_index = next((i for i, original in enumerate(original_sections)
                                     if original.title == section.title), None) if presentation_mode == "pdf" else index
                if presentation_mode == "generated":
                    section = replace(section, page_image=None)
                    if not section.text.strip() and not section.code_blocks and not section.embedded_image:
                        continue
                if len(selected) > 1:
                    section = replace(section, breadcrumb=meta["name"] + " / " + section.breadcrumb)
                sections.append(section)
                refs.append({"sourceId": meta["id"], "sourceName": meta["name"],
                             "sectionIndex": source_index,
                             "pageNumber": index + 1 if presentation_mode == "pdf" else None})
        if not sections:
            raise ValueError("Seçili kaynaklarda anlatılabilir metin yok. PDF üzerinden anlatmayı seçebilir veya görsel açıklamasıyla kaynağı yeniden ekleyebilirsin.")
        (vdir / "assets").mkdir()
        write_json(vdir / "raw_sections.json", [asdict(section) for section in sections])
        write_json(vdir / "source_refs.json", refs)
        write_json(vdir / "video.json", {"id": vdir.name, "name": name, "createdAt": time.time(),
                   "sourceIds": list(source_ids), "sourceNames": [meta["name"] for meta, _ in selected],
                   "presentationMode": presentation_mode or ("pdf" if any(s.page_image for s in sections) else "generated"),
                   "sectionCount": len(sections)})
        return vdir
    except Exception:
        shutil.rmtree(child_dir(pdir, "videos", vdir.name))
        raise



def source_slides(pdir: Path, source_ids) -> tuple[list[Slide], dict]:
    """Source-grounded cards need no paid narration/video generation first."""
    selected = select_sources(pdir, source_ids)
    slides, refs = [], []
    for meta, sections in selected:
        for index, section in enumerate(sections):
            text = section.text.strip()
            if section.code_blocks:
                text += "\n\n" + "\n\n".join(section.code_blocks)
            # Keep all content, but make static review cards readable.
            paragraphs = re.split(r"(?<=\.)\s+|\n\s*\n", text)
            chunks, current = [], ""
            for paragraph in paragraphs:
                if current and len(current) + len(paragraph) > 1400:
                    chunks.append(current)
                    current = ""
                current = (current + "\n" + paragraph).strip()
            if current:
                chunks.append(current)
            for number, chunk in enumerate(chunks):
                title = section.title or f"Bölüm {index + 1}"
                if len(chunks) > 1:
                    title += f" · {number + 1}/{len(chunks)}"
                slides.append(Slide(title=meta["name"] + " / " + title, narration=chunk))
                refs.append({"sourceId": meta["id"], "sourceName": meta["name"], "sectionIndex": index})
    return slides, {"sourceIds": list(source_ids), "sourceNames": [meta["name"] for meta, _ in selected], "sourceRefs": refs}


def save_video_settings(vdir: Path, key: str, payload: dict, fields: tuple[str, ...]):
    path = vdir / "video.json"
    if not path.is_file():
        return
    data = read_json(path)
    data[key] = {field: payload[field] for field in fields if field in payload}
    write_json(path, data)
