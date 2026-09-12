"""Coqui XTTS v2'yi (opsiyonel, yüksek kaliteli offline TTS + ses klonlama) kurar.
Tüm indirmeler D:\\proje\\ders_video\\_cache altına yönlendirilir, C sürücüsüne dokunulmaz.
Çalıştırma: venv\\Scripts\\python.exe install_coqui.py
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "_cache"

os.environ["TEMP"] = str(CACHE / "tmp")
os.environ["TMP"] = str(CACHE / "tmp")
os.environ["PIP_CACHE_DIR"] = str(CACHE / "pip")
os.environ["HF_HOME"] = str(CACHE / "hf")
os.environ["TORCH_HOME"] = str(CACHE / "torch")
(CACHE / "tmp").mkdir(parents=True, exist_ok=True)

PY = sys.executable


def has_nvidia_gpu() -> bool:
    try:
        subprocess.run(["nvidia-smi"], check=True, capture_output=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


GPU = has_nvidia_gpu()
TORCH_INDEX = "https://download.pytorch.org/whl/cu128" if GPU else "https://download.pytorch.org/whl/cpu"
print(f"NVIDIA GPU {'bulundu' if GPU else 'bulunamadı'} -> torch index: {TORCH_INDEX}")

steps = [
    # torchcodec, CUDA index'lerinde her zaman bulunamayabiliyor; bu yüzden
    # torch/torchaudio'dan ayrı, varsayılan PyPI'dan (CPU yeterli, sadece ses
    # I/O için kullanılıyor) kuruluyor.
    [PY, "-m", "pip", "install", "torch", "torchaudio", "--index-url", TORCH_INDEX],
    [PY, "-m", "pip", "install", "torchcodec"],
    [PY, "-m", "pip", "install", "coqui-tts"],
    # coqui-tts 0.27.5 son transformers/tokenizers/huggingface-hub sürümleriyle
    # (Eylül 2026 itibarıyla) uyumsuz; test edilip doğrulanmış sürümlere sabitliyoruz.
    [PY, "-m", "pip", "install", "transformers<5.0,>=4.57", "--force-reinstall", "--no-deps"],
    [PY, "-m", "pip", "install", "tokenizers>=0.22.0,<=0.23.0", "--force-reinstall", "--no-deps"],
    [PY, "-m", "pip", "install", "huggingface-hub>=0.34.0,<1.0", "--force-reinstall", "--no-deps"],
]

for cmd in steps:
    print(">>>", " ".join(cmd))
    subprocess.run(cmd, check=True)

print("\nKurulum tamamlandı. GUI'de TTS sağlayıcı olarak 'coqui' seçilebilir.")
print("İlk kullanımda model dosyaları (~2GB) otomatik indirilecek (D:/.../_cache/hf altına).")
print("\nNOT (lisans): XTTS v2 modeli Coqui'nin CPML lisansı ile dağıtılır — kişisel/")
print("akademik (ticari olmayan) kullanım serbesttir, ticari kullanım için Coqui'den")
print("ayrı bir lisans gerekir: https://coqui.ai/cpml")
