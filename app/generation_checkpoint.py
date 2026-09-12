"""Durable, source-aware checkpoints for narration generation.

The script itself cannot tell which source sections produced which slides.  This
module keeps that missing mapping outside ``script.json`` so manual slide edits
remain backwards compatible while interrupted LLM runs can safely continue.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models import RawSection, Slide

CHECKPOINT_FILE = "generation_checkpoint.json"
CHECKPOINT_VERSION = 1
_ORDINAL_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)\b")


def source_fingerprint(section: RawSection) -> str:
    payload = json.dumps(asdict(section), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _slide_fingerprint(slide: Slide) -> str:
    payload = json.dumps(slide.to_dict(), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _empty_checkpoint(*, disable_legacy_inference: bool = False) -> dict[str, Any]:
    return {
        "version": CHECKPOINT_VERSION,
        "completed": {},
        "sessions": {},
        "pending": None,
        "legacyInferenceDisabled": disable_legacy_inference,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }


def _checkpoint_path(pdir: Path) -> Path:
    return pdir / CHECKPOINT_FILE


def _write_checkpoint(pdir: Path, state: dict[str, Any]) -> None:
    state["version"] = CHECKPOINT_VERSION
    state["updatedAt"] = datetime.now(timezone.utc).isoformat()
    path = _checkpoint_path(pdir)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _read_checkpoint(pdir: Path) -> dict[str, Any] | None:
    path = _checkpoint_path(pdir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return _empty_checkpoint(disable_legacy_inference=True)
    if not isinstance(data, dict) or data.get("version") != CHECKPOINT_VERSION:
        return _empty_checkpoint(disable_legacy_inference=True)
    data.setdefault("completed", {})
    data.setdefault("sessions", {})
    data.setdefault("pending", None)
    return data


def infer_legacy_completed_prefix(
    sections: list[RawSection], slides: list[Slide]
) -> list[int]:
    """Infer only an unambiguous numbered prefix for pre-checkpoint projects.

    Old projects have no source/output mapping.  If a tail slide and a source
    section share the exact hierarchical number (for example ``4.4``), generation
    was ordered, so the preceding source prefix is recoverable.  We deliberately
    avoid fuzzy guesses for unnumbered material.
    """
    if not sections or not slides:
        return []
    ordinal_to_index: dict[str, int] = {}
    for index, section in enumerate(sections):
        match = _ORDINAL_RE.match(section.title or "")
        if match:
            ordinal_to_index[match.group(1)] = index

    # A chapter number repeated much earlier in the script must not win.  Inspect
    # only the tail, then take the last matching source index seen there.
    candidates: list[int] = []
    for slide in slides[-16:]:
        match = _ORDINAL_RE.match(slide.title or "")
        if match and match.group(1) in ordinal_to_index:
            candidates.append(ordinal_to_index[match.group(1)])
    if not candidates:
        return []
    last_index = max(candidates)
    return list(range(last_index + 1))


def _reconcile_pending(
    pdir: Path, state: dict[str, Any], slides: list[Slide]
) -> dict[str, Any]:
    pending = state.get("pending")
    if not isinstance(pending, dict):
        return state
    start = pending.get("insertionIndex")
    hashes = pending.get("slideFingerprints")
    sources = pending.get("sources")
    if not isinstance(start, int) or not isinstance(hashes, list) or not isinstance(sources, list):
        state["pending"] = None
        _write_checkpoint(pdir, state)
        return state

    actual = slides[start:start + len(hashes)]
    committed = len(actual) == len(hashes) and [
        _slide_fingerprint(slide) for slide in actual
    ] == hashes
    if committed:
        for item in sources:
            if isinstance(item, dict) and item.get("fingerprint"):
                state["completed"][item["fingerprint"]] = item
    state["pending"] = None
    _write_checkpoint(pdir, state)
    return state


def load_checkpoint(pdir: Path, slides: list[Slide] | None = None) -> dict[str, Any] | None:
    state = _read_checkpoint(pdir)
    if state is not None and state.get("pending") and slides is not None:
        state = _reconcile_pending(pdir, state, slides)
    return state


def ensure_checkpoint(
    pdir: Path, sections: list[RawSection], slides: list[Slide]
) -> dict[str, Any]:
    state = load_checkpoint(pdir, slides)
    if state is not None:
        return state
    state = _empty_checkpoint()
    for index in infer_legacy_completed_prefix(sections, slides):
        section = sections[index]
        fp = source_fingerprint(section)
        state["completed"][fp] = {
            "fingerprint": fp,
            "sourceIndex": index,
            "title": section.title,
            "legacyInferred": True,
        }
    _write_checkpoint(pdir, state)
    return state


def completed_indexes(
    state: dict[str, Any] | None, sections: list[RawSection]
) -> list[int]:
    if not state:
        return []
    completed = state.get("completed", {})
    return [
        index for index, section in enumerate(sections)
        if source_fingerprint(section) in completed
    ]


def generation_status(
    pdir: Path, sections: list[RawSection], slides: list[Slide]
) -> dict[str, Any]:
    state = load_checkpoint(pdir, slides)
    inferred = False
    if state is None:
        indexes = infer_legacy_completed_prefix(sections, slides)
        inferred = bool(indexes)
    else:
        indexes = completed_indexes(state, sections)
        inferred = any(
            bool(item.get("legacyInferred"))
            for item in state.get("completed", {}).values()
            if isinstance(item, dict)
        )
    return {
        "completedIndexes": indexes,
        "completedCount": len(indexes),
        "remainingCount": max(len(sections) - len(indexes), 0),
        "totalCount": len(sections),
        "inferred": inferred,
        "hasCheckpoint": state is not None,
        "hasClaudeSession": bool(state and state.get("sessions", {}).get("agent", {}).get("id")),
    }


def saved_agent_session(state: dict[str, Any], command: str) -> str | None:
    record = state.get("sessions", {}).get("agent", {})
    expected = hashlib.sha256(command.encode("utf-8")).hexdigest()
    if record.get("commandFingerprint") == expected and record.get("id"):
        return str(record["id"])
    return None


def begin_chunk_commit(
    pdir: Path,
    state: dict[str, Any],
    source_items: list[tuple[int, RawSection]],
    insertion_index: int,
    new_slides: list[Slide],
    *,
    agent_session_id: str | None = None,
    agent_command: str | None = None,
) -> None:
    sources = []
    for index, section in source_items:
        fp = source_fingerprint(section)
        sources.append({
            "fingerprint": fp,
            "sourceIndex": index,
            "title": section.title,
            "generatedSlideCount": len(new_slides),
        })
    if agent_session_id and agent_command:
        state.setdefault("sessions", {})["agent"] = {
            "id": agent_session_id,
            "commandFingerprint": hashlib.sha256(agent_command.encode("utf-8")).hexdigest(),
        }
    state["pending"] = {
        "sources": sources,
        "insertionIndex": insertion_index,
        "slideFingerprints": [_slide_fingerprint(slide) for slide in new_slides],
    }
    _write_checkpoint(pdir, state)


def finish_chunk_commit(pdir: Path, state: dict[str, Any]) -> None:
    pending = state.get("pending")
    if isinstance(pending, dict):
        for item in pending.get("sources", []):
            if isinstance(item, dict) and item.get("fingerprint"):
                state["completed"][item["fingerprint"]] = item
    state["pending"] = None
    _write_checkpoint(pdir, state)


def reset_checkpoint(pdir: Path) -> dict[str, Any]:
    state = _empty_checkpoint(disable_legacy_inference=True)
    _write_checkpoint(pdir, state)
    return state
