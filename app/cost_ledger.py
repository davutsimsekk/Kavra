"""Basit, kalıcı bir kullanım/maliyet kaydı.

Kesin ilke: hiçbir sağlayıcı için fiyat asla hardcode edilmez veya tahmin
edilmez (bkz. app/render_estimate.py'deki aynı ilke, cloud fiyatları
sürekli değişiyor). Sadece bir sağlayıcının KENDİSİ gerçek maliyeti
bildiriyorsa (şu an yalnızca Claude Agent CLI — `--output-format json`
zarfındaki total_cost_usd alanı) o gerçek sayı kaydedilir. Diğer
sağlayıcılar (Gemini, OpenAI-uyumlu, görsel anlama) için sadece kullanım
miktarı (kelime/istek sayısı) tutulur; kullanıcı kendi hesap panelinden
kontrol etmeli.

Her proje kendi `cost_ledger.json`'ını tutar; "tüm projeler" görünümü,
projects/*/cost_ledger.json dosyalarını okuma anında toplayarak elde
edilir — ayrı bir global dosya senkron tutma derdi yok.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

LEDGER_FILE = "cost_ledger.json"
# Dosyanın sınırsız büyümesini önlemek için — üretim/regenerate/görsel-anlama
# çağrıları biriktikçe en eski kayıtlar sessizce düşer, toplamlar etkilenmez
# (zaten kayıt anında `totalUsd`e eklenip bu listeden bağımsız saklanabilirdi,
# ama basitlik için toplam da bu listeden hesaplanıyor — pratikte 500 kayıt
# aylarca kullanım için yeterli).
_MAX_ENTRIES = 500


def _ledger_path(pdir: Path) -> Path:
    return pdir / LEDGER_FILE


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"entries": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data.get("entries"), list):
            return {"entries": []}
        return data
    except (OSError, ValueError, TypeError):
        return {"entries": []}


def _save(path: Path, data: dict[str, Any]) -> None:
    temp = path.with_suffix(".json.tmp")
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
    except OSError:
        temp.unlink(missing_ok=True)


def record(pdir: Path, *, provider: str, kind: str, usd: float | None = None,
           words: int = 0, requests: int = 1) -> None:
    """usd=None: sağlayıcı gerçek maliyeti bildirmiyor demektir (tahmin ÜRETİLMEZ)."""
    path = _ledger_path(pdir)
    data = _load(path)
    data["entries"].append({
        "at": time.time(), "provider": provider, "kind": kind,
        "usd": usd, "words": words, "requests": requests,
    })
    data["entries"] = data["entries"][-_MAX_ENTRIES:]
    _save(path, data)


def _summarize_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    total_usd = sum(e["usd"] for e in entries if e.get("usd") is not None)
    by_provider: dict[str, dict[str, Any]] = {}
    for e in entries:
        bucket = by_provider.setdefault(
            e["provider"], {"usd": 0.0, "words": 0, "requests": 0, "hasUnknownCost": False}
        )
        if e.get("usd") is not None:
            bucket["usd"] += e["usd"]
        else:
            bucket["hasUnknownCost"] = True
        bucket["words"] += e.get("words", 0)
        bucket["requests"] += e.get("requests", 0)
    for bucket in by_provider.values():
        bucket["usd"] = round(bucket["usd"], 4)
    return {
        "totalUsd": round(total_usd, 4),
        "hasUnknownCostProvider": any(e.get("usd") is None for e in entries),
        "byProvider": by_provider,
        "entryCount": len(entries),
    }


def summarize_project(pdir: Path) -> dict[str, Any]:
    return _summarize_entries(_load(_ledger_path(pdir))["entries"])


def summarize_all_projects(projects_dir: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    if projects_dir.exists():
        for ledger_path in projects_dir.glob(f"*/{LEDGER_FILE}"):
            entries.extend(_load(ledger_path)["entries"])
    return _summarize_entries(entries)
