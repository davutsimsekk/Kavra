from pathlib import Path
from dataclasses import dataclass, field
import json
import os
import sys

from dotenv import load_dotenv

# Windows konsolunun/pipe'ının varsayılan ANSI kod sayfası (ör. Türkçe cp1254),
# İngilizce/Unicode olmayan karakterler içeren metinleri (narration, ↔ gibi semboller)
# print/log ederken UnicodeEncodeError'a yol açıyordu — hem GUI hem web API/render
# worker süreçlerinin TAMAMI bu modülü import ettiği için düzeltmeyi burada, tek yerde
# ve koşulsuz olarak (ortam değişkenine güvenmeden) uyguluyoruz.
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "_cache"
MODELS_DIR = ROOT / "models"
PROJECTS_DIR = ROOT / "projects"
PROMPTS_DIR = ROOT / "prompts"
ENV_PATH = ROOT / ".env"
SETTINGS_PATH = ROOT / "settings.json"

for d in (CACHE_DIR, MODELS_DIR, PROJECTS_DIR, PROMPTS_DIR):
    d.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("TEMP", str(CACHE_DIR / "tmp"))
os.environ.setdefault("TMP", str(CACHE_DIR / "tmp"))
os.environ.setdefault("HF_HOME", str(CACHE_DIR / "hf"))
os.environ.setdefault("TORCH_HOME", str(CACHE_DIR / "torch"))
os.environ.setdefault("COQUI_TOS_AGREED", "1")  # bkz README: XTTS v2 CPML (ticari olmayan) lisansı
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")  # xet backend HF_HOME'u yok sayıp C'ye yazabiliyor
os.environ.setdefault("HF_XET_CACHE", str(CACHE_DIR / "hf_xet"))
os.environ.setdefault("TTS_HOME", str(CACHE_DIR / "tts_home"))  # coqui-tts model önbelleği (appdirs, LOCALAPPDATA'yı yok sayar)
(CACHE_DIR / "tmp").mkdir(parents=True, exist_ok=True)

load_dotenv(ENV_PATH)


def get_api_key(name: str) -> str | None:
    return os.environ.get(name) or None


def save_api_key(name: str, value: str):
    lines = []
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if not line.startswith(f"{name}="):
                lines.append(line)
    lines.append(f"{name}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ[name] = value


@dataclass
class VideoOptions:
    width: int = 1920
    height: int = 1080
    fps: int = 25
    subtitles: bool = True
    fade_transitions: bool = True
    ken_burns: bool = False
    theme_preset: str = "auto"
    accent_rgb: tuple | None = None


DEFAULT_SETTINGS = {
    "last_source": "",
    "llm_provider": "gemini",
    "gemini_model": "gemini-3.5-flash-lite",
    "openai_endpoint": "https://api.openai.com/v1/chat/completions",
    "openai_model": "gpt-5.2",
    "single_request": False,
    "agent_command": "claude -p --output-format json --restricted",
    "agent_reuse_session": True,
    "agent_max_sections": 4,
    "agent_timeout_sec": 900,
    "tts_provider": "edge",
    "tts_voice": "tr-TR-AhmetNeural",
    "tts_rate": "+0%",
    "subtitles": True,
    "fade_transitions": True,
    "ken_burns": False,
    "theme_preset": "auto",
    "narration_style": "normal",
}


def load_settings() -> dict:
    if SETTINGS_PATH.exists():
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            merged = dict(DEFAULT_SETTINGS)
            merged.update(data)
            return merged
        except Exception:
            pass
    return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict):
    SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
