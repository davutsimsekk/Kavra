from pathlib import Path
from dataclasses import dataclass, field
import json
import os
import sys
import time

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


def _configured_path(name: str, default: Path) -> Path:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser().resolve() if value else default


# Normal Windows kurulumunda tüm yollar eskisi gibi depo kökünde kalır. Docker
# ve taşınabilir kurulumlar KAVRA_DATA_DIR ile kullanıcı verisini uygulama
# kodundan ayırabilir; daha dar kapsamlı değişkenler gerektiğinde tek tek ezebilir.
DATA_DIR = _configured_path("KAVRA_DATA_DIR", ROOT)
CACHE_DIR = _configured_path("KAVRA_CACHE_DIR", DATA_DIR / "_cache")
MODELS_DIR = _configured_path("KAVRA_MODELS_DIR", DATA_DIR / "models")
PROJECTS_DIR = _configured_path("KAVRA_PROJECTS_DIR", DATA_DIR / "projects")
STUDY_DATA_DIR = _configured_path("KAVRA_STUDY_DATA_DIR", DATA_DIR / "study_data")
PROMPTS_DIR = ROOT / "prompts"
ENV_PATH = _configured_path("KAVRA_ENV_PATH", DATA_DIR / ".env")
SETTINGS_PATH = _configured_path("KAVRA_SETTINGS_PATH", DATA_DIR / "settings.json")

for d in (DATA_DIR, CACHE_DIR, MODELS_DIR, PROJECTS_DIR, STUDY_DATA_DIR, PROMPTS_DIR):
    d.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("TEMP", str(CACHE_DIR / "tmp"))
os.environ.setdefault("TMP", str(CACHE_DIR / "tmp"))
os.environ.setdefault("TMPDIR", str(CACHE_DIR / "tmp"))
os.environ.setdefault("HF_HOME", str(CACHE_DIR / "hf"))
os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(CACHE_DIR / "hf"))
os.environ.setdefault("TORCH_HOME", str(CACHE_DIR / "torch"))
os.environ.setdefault("XDG_CACHE_HOME", str(CACHE_DIR / "xdg"))
os.environ.setdefault("COQUI_TOS_AGREED", "1")  # bkz README: XTTS v2 CPML (ticari olmayan) lisansı
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")  # xet backend HF_HOME'u yok sayıp C'ye yazabiliyor
os.environ.setdefault("HF_XET_CACHE", str(CACHE_DIR / "hf_xet"))
os.environ.setdefault("TTS_HOME", str(CACHE_DIR / "tts_home"))  # coqui-tts model önbelleği (appdirs, LOCALAPPDATA'yı yok sayar)
(CACHE_DIR / "tmp").mkdir(parents=True, exist_ok=True)

# Compose env_file içinde örnek amaçlı boş bırakılan anahtarlar, kalıcı
# /data/runtime/.env dosyasındaki arayüzden kaydedilmiş değerleri gölgelememeli.
for _secret_name in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ELEVENLABS_API_KEY"):
    if not os.environ.get(_secret_name, "").strip():
        os.environ.pop(_secret_name, None)

load_dotenv(ENV_PATH)


def get_api_key(name: str) -> str | None:
    return os.environ.get(name) or None


def _atomic_write_text(path: Path, text: str) -> None:
    """Önce geçici dosyaya yaz, sonra yerine taşı: eşzamanlı okuma veya çökme dosyayı yarım/boş bırakmaz.

    settings.json yarım kalırsa ayarlar sessizce varsayılana döner; .env yarım kalırsa API anahtarları kaybolur."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:  # Windows: dosya o anda başka bir süreç tarafından okunuyor olabilir
                if attempt == 4:
                    raise
                time.sleep(0.05)
    finally:
        tmp.unlink(missing_ok=True)


def save_api_key(name: str, value: str):
    lines = []
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if not line.startswith(f"{name}="):
                lines.append(line)
    lines.append(f"{name}={value}")
    _atomic_write_text(ENV_PATH, "\n".join(lines) + "\n")
    os.environ[name] = value


def delete_api_key(name: str):
    if ENV_PATH.exists():
        kept = [line for line in ENV_PATH.read_text(encoding="utf-8").splitlines() if not line.startswith(f"{name}=")]
        _atomic_write_text(ENV_PATH, "\n".join(kept) + ("\n" if kept else ""))
    os.environ.pop(name, None)


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
    # Coqui seçiliyken kaç BAĞIMSIZ PROCESS'in aynı anda (her biri kendi
    # model kopyasıyla) slayt seslendireceği — bkz. app.tts.coqui_parallel.
    # 1 = kapalı/sıralı (varsayılan, her zaman güvenli). Üst sınır
    # (coqui_parallel.MAX_PARALLEL_WORKERS) bilinçli olarak düşük tutuluyor:
    # bu depoda 4 process'i aynı anda yüklemeye çalışmak gerçek bir Windows
    # mavi ekranına (BSOD) yol açtı — VRAM'e sığmayan bir şeyi otomatik
    # olarak "daha fazla dene" ile keşfetmek güvenli değil, kullanıcı N'yi
    # kendi belirlemeli.
    coqui_parallel_workers: int = 1
    # Chatterbox ayrı ortamda çalışır; 8GB RTX 4060 için iki GPU modelinden
    # fazlasına izin verilmez (OOM durumunda otomatik olarak 1'e iner).
    chatterbox_parallel_workers: int = 1
    # "local": seslendirme bu makinede; "remote": GPU'lu uzak Kavra TTS sunucusunda
    # (bkz. app/tts/remote.py). Yalnızca coqui ve piper uzakta çalışır. Cache
    # hash'lerine girmez: aynı motor/ses uzakta da yerelde de aynı sesi verir.
    tts_backend: str = "local"
    # Uzak GPU'ya aynı anda gönderilen slayt sayısı (sunucudaki model kopyasına göre).
    remote_tts_concurrency: int = 2


# Ortalama Türkçe ders anlatımı hızı — render_estimate (önizleme) ve
# generate_chunked'ın süre-hedefi bütçelemesi (app/llm/base.py) aynı sabiti
# paylaşır, aksi halde ikisi birbirinden sapan tahminler verir.
WORDS_PER_MINUTE = 132


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
    "coqui_parallel_workers": 1,
    "chatterbox_parallel_workers": 1,
    "tts_backend": "local",
    "remote_tts_concurrency": 2,
    # İki sabit GPU kaynağı (bkz. app/tts/remote.py PROFILES): "pc" (Tailscale üzerinden
    # kendi bilgisayarın) ve "colab". Token'lar burada değil .env'de tutulur.
    "remote_tts_profiles": {"pc": {"url": ""}, "colab": {"url": ""}},
    "remote_tts_active_profile": "",
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
    _atomic_write_text(SETTINGS_PATH, json.dumps(settings, ensure_ascii=False, indent=2))
