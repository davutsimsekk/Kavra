"""Anka TTS'i (opsiyonel, Türkçe'ye özel eğitilmiş, XTTS v2'den hızlı, ~1/3 VRAM
kullanan ses klonlama modeli) kurar. Tüm indirmeler D:\\proje\\ders_video\\_cache
altına yönlendirilir, C sürücüsüne dokunulmaz.
Çalıştırma: venv\\Scripts\\python.exe install_anka.py

LİSANS NOTU: model ağırlıkları CC-BY-NC-4.0 — yalnızca kişisel/araştırma kullanımı,
ticari kullanım için https://huggingface.co/krmkayabasi/Anka-TTS üzerinden ayrı
lisans gerekir.

ÖNEMLİ (Coqui uyumluluğu): anka-tts, f5-tts üzerinden transformers'ı 5.x'e
yükseltmeye çalışıyor — bu, coqui-tts'in (bkz. install_coqui.py) kullandığı eski
transformers API'sini kırıyor (ImportError: isin_mps_friendly). Bu depoda ikisinin
AYNI venv'de bir arada çalışabildiği doğrulandı, ama yalnızca anka-tts kurulumundan
SONRA transformers/tokenizers/huggingface-hub'ı coqui-tts'in beklediği sürümlere
--no-deps ile geri zorlarsan. Anka'nın kendi çalışma zamanı (yalnızca AnkaTTS
sınıfı üzerinden, f5-tts/gradio'nun demo arayüzü hiç import edilmeden) bu eski
sürümlerle sorunsuz test edildi.
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

steps = [
    [PY, "-m", "pip", "install", "anka-tts[tts]"],
    # coqui-tts (kurulu ise) eski transformers API'siyle çalışıyor; anka-tts'in
    # f5-tts bağımlılığı bunu 5.x'e yükseltiyor — geri zorla (bkz. install_coqui.py
    # ile aynı sabitlenmiş aralıklar).
    [PY, "-m", "pip", "install", "transformers<5.0,>=4.57", "--force-reinstall", "--no-deps"],
    [PY, "-m", "pip", "install", "tokenizers>=0.22.0,<=0.23.0", "--force-reinstall", "--no-deps"],
    [PY, "-m", "pip", "install", "huggingface-hub>=0.34.0,<1.0", "--force-reinstall", "--no-deps"],
]

for cmd in steps:
    print(">>>", " ".join(cmd))
    subprocess.run(cmd, check=True)

print("\nKurulum tamamlandı. GUI'de TTS sağlayıcı olarak 'anka' seçilebilir.")
print("İlk kullanımda model dosyaları otomatik indirilecek (D:/.../_cache/hf altına).")
print("\nNOT (lisans): Anka TTS model ağırlıkları CC-BY-NC-4.0 ile dağıtılır — kişisel/")
print("araştırma kullanımı serbesttir, ticari kullanım için ayrı lisans gerekir:")
print("https://huggingface.co/krmkayabasi/Anka-TTS")
