"""Kodu VPS'e göndermek için paket üretir (git uzak deposu olmadan).

  python tools/pack_for_vps.py

Çıktı: _cache/kavra-deploy.tgz. Yalnızca izin verilen dosyalar paketlenir (allowlist): sırlar
(.env, settings.json), kullanıcı verisi (projects, models, study_data), venv/node_modules ve
önbellekler asla girmez. Veri klasörleri ayrıca taşınır (bkz. DOCKER.md §8).
"""
from __future__ import annotations

import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "_cache" / "kavra-deploy.tgz"

FILES = [
    "Dockerfile", "compose.yaml", "compose.gpu.yaml", ".dockerignore", ".env.example",
    "requirements.txt", "requirements-tts.txt", "requirements-chatterbox.txt",
    "README.md", "DOCKER.md", "REMOTE_TTS.md",
    "webui/package.json", "webui/package-lock.json", "webui/index.html", "webui/vite.config.js",
]
DIRS = ["app", "studio_web", "prompts", "webui/src", "webui/public"]
SKIP_PARTS = {"__pycache__", "node_modules", "dist"}
SKIP_SUFFIXES = (".pyc", ".pyo", ".log")


def collect() -> list[Path]:
    paths = [ROOT / name for name in FILES]
    for directory in DIRS:
        paths.extend(p for p in (ROOT / directory).rglob("*") if p.is_file())
    return sorted(
        p for p in paths
        if not (SKIP_PARTS & set(p.relative_to(ROOT).parts)) and not p.name.endswith(SKIP_SUFFIXES)
    )


def build(target: Path = OUT) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(target, "w:gz") as archive:
        for path in collect():
            archive.add(path, arcname=f"kavra/{path.relative_to(ROOT).as_posix()}")
    return target


def main() -> None:
    target = build()
    print(f"Yazıldı: {target} ({target.stat().st_size // 1024} KB, {len(collect())} dosya)")
    print("VPS'e gönder:  scp _cache/kavra-deploy.tgz kullanici@vps-adresi:~/")
    print("VPS'te aç:     tar -xzf kavra-deploy.tgz && cd kavra")


if __name__ == "__main__":
    main()
