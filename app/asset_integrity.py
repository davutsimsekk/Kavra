"""Recoverable housekeeping and manifests for per-slide render assets."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ASSET_RE = re.compile(
    r"^(?:slide|segment)_(\d+)\.(?:mp3|png|ass|hash|mp4|narration_hash|words\.json)$",
    re.IGNORECASE,
)


def _indexed_assets(assets: Path) -> list[tuple[Path, int]]:
    if not assets.exists():
        return []
    found = []
    for path in assets.iterdir():
        if not path.is_file():
            continue
        match = _ASSET_RE.fullmatch(path.name)
        if match:
            found.append((path, int(match.group(1))))
    return found


def audit_assets(pdir: Path, slide_count: int) -> dict[str, Any]:
    assets = pdir / "assets"
    indexed = _indexed_assets(assets)

    def _canonical(suffix: str) -> list[int]:
        return sorted(
            index for path, index in indexed
            if path.suffix.lower() == suffix and index <= slide_count
        )

    active_audio = _canonical(".mp3")
    active_segments = _canonical(".mp4")
    missing_audio = [index for index in range(1, slide_count + 1) if index not in set(active_audio)]
    missing_segments = [index for index in range(1, slide_count + 1) if index not in set(active_segments)]
    orphan_files = sorted(path.name for path, index in indexed if index > slide_count)
    return {
        "slideCount": slide_count,
        "canonicalAudioCount": len(active_audio),
        "missingAudioCount": len(missing_audio),
        "canonicalSegmentCount": len(active_segments),
        "missingSegmentCount": len(missing_segments),
        "orphanCount": len(orphan_files),
        "orphanFiles": orphan_files,
        "manifestExists": (assets / "asset_manifest.json").exists(),
    }


def quarantine_orphan_assets(pdir: Path, slide_count: int) -> list[str]:
    """Move stale numbered files aside instead of deleting user-generated data."""
    assets = (pdir / "assets").resolve()
    pdir_resolved = pdir.resolve()
    if assets.parent != pdir_resolved:
        raise ValueError("Varlık klasörü proje dışında olamaz.")
    stale = [(path, index) for path, index in _indexed_assets(assets) if index > slide_count]
    if not stale:
        return []
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = assets / "_orphaned" / stamp
    destination.mkdir(parents=True, exist_ok=True)
    moved = []
    for path, _index in stale:
        target = destination / path.name
        suffix = 1
        while target.exists():
            target = destination / f"{path.stem}_{suffix}{path.suffix}"
            suffix += 1
        path.replace(target)
        moved.append(path.name)
    return sorted(moved)


def write_asset_manifest(
    pdir: Path, slide_count: int, quarantined: list[str] | None = None
) -> dict[str, Any]:
    assets = pdir / "assets"
    audit = audit_assets(pdir, slide_count)
    manifest = {
        "version": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        **audit,
        "quarantined": quarantined or [],
        "activeAudio": [f"slide_{index:03d}.mp3" for index in range(1, slide_count + 1)],
        "activeSegments": [f"segment_{index:03d}.mp4" for index in range(1, slide_count + 1)],
    }
    path = assets / "asset_manifest.json"
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return manifest
