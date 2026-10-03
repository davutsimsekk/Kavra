"""Kavra stüdyosunun tamamını Google Colab'da veya Kaggle'da kurup cloudflared ile yayınlar.

``colab/Kavra_Studyo_Colab.ipynb`` ve ``colab/Kavra_Studyo_Kaggle.ipynb`` not defterleri
bu modülü kullanır; mantık burada
durur ki not defteri hücreleri kısa kalsın ve test edilebilsin. Yalnız standart
kütüphaneyi kullanır: Colab'ın kendi Python'unda çalışır, Kavra ise Dockerfile'daki
gibi ayrı bir Python 3.14 ortamında koşar.

Akış:
  install()  Python 3.14 + bağımlılıklar + Node 22 + arayüz derlemesi (tekrar
             çalıştırılırsa gereksinimler değişmedikçe atlanır)
  start()    önce tünel açılır (adres sunucunun izinli host listesine girmeli),
             sonra Kavra şifreli olarak başlatılır
  Studio.watch() sunucuyu ve tüneli izler; tünel düşerse yeni adresle ikisini de yeniler
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from tts_server.colab_tunnel import start_tunnel


def on_kaggle() -> bool:
    return bool(os.environ.get("KAGGLE_KERNEL_RUN_TYPE")) or Path("/kaggle/working").is_dir()


ROOT = Path(__file__).resolve().parent.parent
# Kaggle'da /kaggle/working 20 GB'lık "çıktı" alanıdır; ağır ortam (venv, önbellek,
# modeller) oraya sığmaz ve çıktıyı şişirir. Yalnız kullanıcı verisi (projeler,
# videolar) orada durur ki Output panelinden de indirilebilsin.
KAGGLE = on_kaggle()
BASE = Path("/root/kavra") if KAGGLE else Path("/content")
VENV = BASE / "kavra-venv"
VENV_PY = VENV / "bin" / "python"
NODE_DIR = BASE / "node"
CACHE_DIR = BASE / "kavra_cache"
LOCAL_DATA_DIR = Path("/kaggle/working/kavra_data") if KAGGLE else BASE / "kavra_data"
DRIVE_DATA_DIR = Path("/content/drive/MyDrive/Kavra")
LOG_PATH = BASE / "kavra_server.log"
PORT = 8768
PYTHON_VERSION = "3.14"
NODE_MAJOR = 22
INSTALL_STAMP = VENV / ".kavra_install"
SECRET_KEYS = ("GEMINI_API_KEY", "OPENAI_API_KEY", "ELEVENLABS_API_KEY")
# apt paketi -> kurulu olduğunu gösteren dosya. Colab'da ffmpeg hazır gelir ama slayt
# fontları (app/video/slide_renderer.py) gelmez; her biri ayrı denetlenmeli.
APT_PACKAGES = {
    "ffmpeg": "/usr/bin/ffmpeg",
    "fonts-dejavu-core": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "libsndfile1": "/usr/lib/x86_64-linux-gnu/libsndfile.so.1",
}


def run(command: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    """Komutu çalıştırır ve çıktısını hücreye aktarır (Colab alt süreç çıktısını kendiliğinden göstermez)."""
    print(">>>", " ".join(str(part) for part in command), flush=True)
    proc = subprocess.Popen([str(part) for part in command], cwd=cwd, env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    tail: list[str] = []
    for line in proc.stdout:
        tail = (tail + [line])[-40:]
        print(line, end="", flush=True)
    if proc.wait() != 0:
        raise RuntimeError(f"Komut başarısız ({proc.returncode}): {' '.join(map(str, command))}\n{''.join(tail)}")


def colab_secret(name: str) -> str | None:
    """Colab Secrets'tan ya da Kaggle'ın Add-ons → Secrets bölümünden okur."""
    try:
        if KAGGLE:
            from kaggle_secrets import UserSecretsClient  # type: ignore[import-not-found]
            return (UserSecretsClient().get_secret(name) or "").strip() or None
        from google.colab import userdata  # type: ignore[import-not-found]
        return (userdata.get(name) or "").strip() or None
    except Exception:  # sır yok, not defterine erişim izni verilmedi ya da not defteri dışında
        return None


def requirements_fingerprint(root: Path = ROOT, chatterbox: bool = False) -> str:
    names = ["requirements.txt", "requirements-tts.txt"]
    if chatterbox:
        names += ["requirements-chatterbox.txt", "install_chatterbox.py"]
    digest = hashlib.sha256(f"py{PYTHON_VERSION}".encode())
    for name in names:
        digest.update(name.encode() + b"\0" + (root / name).read_bytes())
    return digest.hexdigest()


def node_major(node: str = "node") -> int:
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
        return int(out.lstrip("v").split(".")[0])
    except (OSError, ValueError):
        return 0


def node_bin_dir() -> Path | None:
    if node_major(str(NODE_DIR / "bin" / "node")) >= NODE_MAJOR:
        return NODE_DIR / "bin"
    system = shutil.which("node")
    return Path(system).parent if system and node_major(system) >= NODE_MAJOR else None


def ensure_node() -> Path:
    found = node_bin_dir()
    if found:
        return found
    with urllib.request.urlopen("https://nodejs.org/dist/index.json", timeout=30) as response:
        releases = json.load(response)
    version = next(r["version"] for r in releases if r["version"].startswith(f"v{NODE_MAJOR}."))
    url = f"https://nodejs.org/dist/{version}/node-{version}-linux-x64.tar.xz"
    print("Node indiriliyor:", url, flush=True)
    archive, _ = urllib.request.urlretrieve(url)
    shutil.rmtree(NODE_DIR, ignore_errors=True)
    with tarfile.open(archive) as tar:
        top = tar.getnames()[0].split("/")[0]
        tar.extractall(NODE_DIR.parent, filter="data")
    (NODE_DIR.parent / top).rename(NODE_DIR)
    return NODE_DIR / "bin"


def build_webui(root: Path = ROOT) -> None:
    env = {**os.environ, "PATH": f"{ensure_node()}:{os.environ['PATH']}"}
    run(["npm", "ci", "--no-audit", "--no-fund"], cwd=root / "webui", env=env)
    run(["npm", "run", "build"], cwd=root / "webui", env=env)


def missing_apt_packages(packages: dict[str, str] = APT_PACKAGES) -> list[str]:
    return [name for name, marker in packages.items() if not Path(marker).exists()]


def install(chatterbox: bool = False, root: Path = ROOT) -> None:
    """Gerekenleri kurar; aynı gereksinimlerle ikinci kez çağrılırsa yalnız arayüzü yeniden derler."""
    BASE.mkdir(parents=True, exist_ok=True)
    missing = missing_apt_packages()
    if missing:
        run(["apt-get", "-qq", "update"])
        run(["apt-get", "-qq", "install", "-y", *missing])

    fingerprint = requirements_fingerprint(root, chatterbox)
    if INSTALL_STAMP.exists() and INSTALL_STAMP.read_text() == fingerprint:
        print("Python bağımlılıkları zaten kurulu, atlanıyor.", flush=True)
    else:
        run([sys.executable, "-m", "pip", "install", "-q", "uv"])
        run([sys.executable, "-m", "uv", "python", "install", PYTHON_VERSION])
        if not VENV_PY.exists():
            run([sys.executable, "-m", "uv", "venv", "--seed", "--python", PYTHON_VERSION, VENV])
        run([VENV_PY, "-m", "pip", "install", "-q", "--upgrade", "pip", "setuptools", "wheel"])
        run([VENV_PY, "-m", "pip", "install", "-q", "--prefer-binary",
             "-r", root / "requirements.txt", "-r", root / "requirements-tts.txt"], cwd=root)
        if chatterbox:
            # chatterbox_venv'i depo kökünde kurar; uygulama onu kendiliğinden bulur.
            run([VENV_PY, "install_chatterbox.py"], cwd=root)
        INSTALL_STAMP.write_text(fingerprint)

    # Arayüz kodu her git pull ile değişebilir; derleme ~1 dk sürer.
    build_webui(root)
    print("\nKurulum tamam.", flush=True)


def data_dir(use_drive: bool) -> Path:
    if not use_drive or KAGGLE:
        return LOCAL_DATA_DIR
    if not Path("/content/drive/MyDrive").is_dir():
        from google.colab import drive  # type: ignore[import-not-found]
        drive.mount("/content/drive")
    return DRIVE_DATA_DIR


# Bir XTTS kopyası yerelde ~2.1 GB VRAM ölçüldü; çıkarım tepesi ve CUDA bağlamı için
# pay bırakılır. Her kopya ayrı bir süreç olduğundan sistem RAM'i de sınırlar.
XTTS_VRAM_GIB = 3.5
XTTS_RAM_GIB = 4.0


def gpu_memories_mib() -> list[int]:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=20).stdout
        return [int(line.strip()) for line in out.splitlines() if line.strip().isdigit()]
    except (OSError, subprocess.TimeoutExpired):
        return []


def ram_gib() -> float:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
    except (ValueError, OSError, AttributeError):
        return 0.0


def xtts_max_workers(gpus_mib: list[int], ram: float) -> int:
    """Donanıma sığan en fazla XTTS kopyası: her GPU'ya VRAM'i kadar, toplamda RAM kadar."""
    by_vram = sum(int(mib / 1024 // XTTS_VRAM_GIB) for mib in gpus_mib)
    by_ram = int(ram // XTTS_RAM_GIB) if ram else by_vram
    return max(1, min(by_vram, by_ram))


def server_env(host: str, token: str, data: Path, root: Path = ROOT,
               base: dict[str, str] | None = None, coqui_max_workers: int | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    if coqui_max_workers:
        env["KAVRA_COQUI_MAX_WORKERS"] = str(coqui_max_workers)
    env.update({
        "KAVRA_ALLOWED_HOSTS": host,
        "KAVRA_ACCESS_TOKEN": token,
        "KAVRA_DATA_DIR": str(data),
        # Önbellek Drive'a yazılırsa render çok yavaşlar; modeller depoda gelen
        # Chatterbox referans seslerinin yanında durur.
        "KAVRA_CACHE_DIR": str(CACHE_DIR),
        "KAVRA_MODELS_DIR": str(root / "models"),
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    for key in ("TEMP", "TMP", "TMPDIR", "HF_HOME", "HUGGINGFACE_HUB_CACHE", "TORCH_HOME",
                "XDG_CACHE_HOME", "TTS_HOME", "VIRTUAL_ENV", "PYTHONPATH", "MPLBACKEND"):
        env.pop(key, None)  # Colab'ın değerleri yerine Kavra'nın önbellek düzeni kullanılsın
    for key in SECRET_KEYS:
        value = colab_secret(key)
        if value:
            env[key] = value
    return env


def wait_until_ready(proc: subprocess.Popen, token: str, timeout: float = 300) -> None:
    request = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/bootstrap",
                                     headers={"Authorization": f"Bearer {token}"})
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        try:
            with urllib.request.urlopen(request, timeout=3):
                return
        except OSError:
            time.sleep(2)
    print(LOG_PATH.read_text(encoding="utf-8", errors="replace")[-4000:])
    raise RuntimeError("Kavra başlamadı - yukarıdaki günlüğe bak.")


@dataclass
class Studio:
    token: str
    data: Path
    root: Path = ROOT
    url: str = ""
    coqui_max_workers: int | None = None
    server: subprocess.Popen | None = None
    tunnel: subprocess.Popen | None = None
    _log: object = field(default=None, repr=False)

    @property
    def link(self) -> str:
        return f"{self.url}/?key={self.token}"

    def start_server(self) -> None:
        self.stop_server()
        BASE.mkdir(parents=True, exist_ok=True)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.data.mkdir(parents=True, exist_ok=True)
        self._log = open(LOG_PATH, "a", encoding="utf-8")
        self.server = subprocess.Popen(
            [VENV_PY, "-m", "uvicorn", "studio_web.api:app", "--host", "127.0.0.1", "--port", str(PORT)],
            cwd=self.root, env=server_env(urlsplit(self.url).hostname or "", self.token, self.data, self.root,
                                          coqui_max_workers=self.coqui_max_workers),
            stdout=self._log, stderr=subprocess.STDOUT)
        wait_until_ready(self.server, self.token)

    def stop_server(self) -> None:
        if self.server and self.server.poll() is None:
            self.server.terminate()
            try:
                self.server.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.server.kill()
        self.server = None

    def start_tunnel(self) -> None:
        if self.tunnel and self.tunnel.poll() is None:
            self.tunnel.terminate()
        self.tunnel, self.url = start_tunnel(PORT)

    def show(self) -> None:
        print("=" * 70)
        print("Kavra hazır. Bu bağlantıyı aç (şifre içerir, kimseyle paylaşma):")
        print("  ", self.link)
        print("Adres:", self.url, "| Şifre:", self.token)
        print("Veriler:", self.data)
        if self.coqui_max_workers:
            print(f"XTTS paralel model sınırı: {self.coqui_max_workers} (Ses ayarları > Paralel model sayısı)")
        print("=" * 70, flush=True)

    def watch(self, interval: int = 60) -> None:
        """Sunucuyu ve tüneli canlı tutar; durdurmak için hücrenin ■ düğmesine bas."""
        try:
            while True:
                time.sleep(interval)
                if self.tunnel is None or self.tunnel.poll() is not None:
                    # Hızlı tünel adresi değişir; yeni host sunucunun izinli listesine girmeli.
                    print(time.strftime("%H:%M"), "| tünel düştü, yenileniyor...", flush=True)
                    self.start_tunnel()
                    self.start_server()
                    print("ADRES DEĞİŞTİ (yarım kalan render'ı yeniden başlat, bitenler korunur):")
                    self.show()
                elif self.server is None or self.server.poll() is not None:
                    print(time.strftime("%H:%M"), "| sunucu kapanmış, yeniden başlatılıyor...", flush=True)
                    self.start_server()
        except KeyboardInterrupt:
            print("İzleme durdu; sunucu ve tünel hâlâ açık. Kapatmak için studio.stop() çalıştır.")

    def stop(self) -> None:
        self.stop_server()
        if self.tunnel and self.tunnel.poll() is None:
            self.tunnel.terminate()
        print("Kavra ve tünel kapatıldı.")


def start(use_drive: bool = False, token: str | None = None, xtts_max: int | None = None) -> Studio:
    """xtts_max: Kaggle'da 0/None = donanıma göre otomatik; Colab'da verilmezse uygulamanın varsayılanı (3)."""
    token = token or colab_secret("KAVRA_ERISIM_SIFRESI") or secrets.token_urlsafe(18)
    if len(token) < 16:
        raise ValueError("KAVRA_ERISIM_SIFRESI en az 16 karakter olmalı.")
    if not xtts_max and KAGGLE:
        xtts_max = xtts_max_workers(gpu_memories_mib(), ram_gib())
    studio = Studio(token=token, data=data_dir(use_drive), coqui_max_workers=xtts_max or None)
    studio.start_tunnel()
    studio.start_server()
    studio.show()
    return studio
