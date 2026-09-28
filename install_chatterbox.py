"""Chatterbox Multilingual V3'ü Coqui/Anka ortamını bozmadan kurar.

Kullanım: venv\\Scripts\\python.exe install_chatterbox.py

PyPI'daki Chatterbox 0.1.7 V3 seçimini içermediği için, V3'ü içeren resmî
GitHub commit'i sabitlenmiştir. Chatterbox transformers 5.2.0 ister; Coqui ise
4.x ile sabitlidir.
Bu yüzden ``chatterbox_venv`` ana venv'in paketlerini yalnızca taban olarak
görür ama Chatterbox'ın çakışan paketlerini kendi içine kurar.
"""

from __future__ import annotations

import os
import site
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "_cache"
VENV = ROOT / "chatterbox_venv"
CHATTERBOX_COMMIT = "5de7a54aa4e5e2baadb0182dde554908b48b85c2"
CHATTERBOX_SOURCE = f"git+https://github.com/resemble-ai/chatterbox.git@{CHATTERBOX_COMMIT}"

for key, value in {
    "TEMP": CACHE / "tmp",
    "TMP": CACHE / "tmp",
    "PIP_CACHE_DIR": CACHE / "pip",
    "HF_HOME": CACHE / "hf",
    "TORCH_HOME": CACHE / "torch",
}.items():
    os.environ[key] = str(value)

(CACHE / "tmp").mkdir(parents=True, exist_ok=True)

if not VENV.exists():
    subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", str(VENV)], check=True)

python = VENV / "Scripts" / "python.exe"
if not python.exists():
    python = VENV / "bin" / "python"

# Python venv'leri birbirinin site-packages klasörünü devralmaz. Çalıştırılan
# ana Python'ın paket yolu .pth ile paylaşılır; site.getsitepackages kullanımı
# hem Windows (Lib/...) hem Linux/macOS (lib/pythonX.Y/...) üzerinde çalışır.
parent_site = Path(site.getsitepackages()[0])
child_site = subprocess.run(
    [str(python), "-c", "import site; print(site.getsitepackages()[0])"],
    check=True,
    capture_output=True,
    text=True,
).stdout.strip()
Path(child_site).mkdir(parents=True, exist_ok=True)
(Path(child_site) / "kavra_parent_venv.pth").write_text(str(parent_site), encoding="utf-8")

# CUDA Torch ana ortamda tek kopya olarak tutulur. Yukarıdaki .pth dosyası
# sayesinde Chatterbox onu doğrudan kullanır; buraya ikinci bir 4 GB Torch
# kurulmaz. Ayrı ortam yalnızca transformers/Chatterbox çakışmasını yalıtır.
subprocess.run(
    [
        str(python), "-c",
        "import torch, torchaudio; "
        "assert torch.cuda.is_available(), 'Ana ortamda CUDA Torch bulunamadı'; "
        "print(f'Ortak CUDA Torch: {torch.__version__} / {torch.cuda.get_device_name(0)}')",
    ],
    check=True,
)

steps = [
    [str(python), "-m", "pip", "install", "--upgrade", "-r", str(ROOT / "requirements-chatterbox.txt")],
    # Chatterbox bağımlılık çözümlemesi CPU Torch'u indirebilir; ortak CUDA
    # Torch yukarıda doğrulandığı için paketi bağımlılıkları çözmeden kuruyoruz.
    [str(python), "-m", "pip", "install", "--upgrade", "--force-reinstall", "--no-deps", CHATTERBOX_SOURCE],
]
for command in steps:
    print(">>>", " ".join(command))
    subprocess.run(command, check=True)

subprocess.run(
    [
        str(python), "-c",
        "import inspect, torch; assert torch.cuda.is_available(), 'CUDA kullanılamıyor'; "
        "from chatterbox.mtl_tts import ChatterboxMultilingualTTS; "
        "assert 't3_model' in inspect.signature(ChatterboxMultilingualTTS.from_pretrained).parameters, "
        "'Kurulan Chatterbox V3 seçimini desteklemiyor'; "
        "print(f'Chatterbox Multilingual V3 GPU hazır: {torch.cuda.get_device_name(0)}')",
    ],
    check=True,
)
print(f"\nKurulum tamamlandı: {python} (resmî commit {CHATTERBOX_COMMIT})")
print("İlk karşılaştırma üretiminde model ağırlıkları _cache/hf altına indirilecektir.")
