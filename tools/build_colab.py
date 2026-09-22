"""Colab paketini ve not defterini üretir.

  python tools/build_colab.py

Çıktılar (colab/ klasörü):
  kavra-tts-bundle.zip        Colab'a yüklenecek sunucu paketi
  Kavra_TTS_Sunucusu.ipynb    Colab'da açılacak not defteri
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "colab"

# Sunucunun ihtiyaç duyduğu en küçük dosya kümesi. Yeni bir import eklenirse
# tests/test_colab_bundle.py paketin tek başına çalışmadığını yakalar.
BUNDLE_FILES = [
    "app/__init__.py",
    "app/config.py",
    "app/models.py",
    "app/tts/__init__.py",
    "app/tts/base.py",
    "app/tts/coqui_provider.py",
    "app/tts/piper_provider.py",
    "tts_server/__init__.py",
    "tts_server/server.py",
    "tts_server/colab_tunnel.py",
    "tts_server/requirements.txt",
]

INTRO = """# Kavra TTS sunucusu (Colab)

Bu not defteri, GPU'su olmayan Kavra sunucunun ses üretimini (**XTTS v2** ve **Piper**) Colab GPU'suna devretmesini sağlar.

1. **Çalışma zamanı → Çalışma zamanı türünü değiştir → GPU** seç.
2. Aşağıdaki hücreleri sırayla çalıştır.
3. Son hücrenin yazdırdığı **Adres** ve **Token**'ı Kavra'da *Ses ayarları → Çalıştırma yeri: Uzak GPU* alanlarına gir.
4. Render sürerken **son hücreyi ve bu sekmeyi açık bırak.**

*İsteğe bağlı:* Token'ın her oturumda aynı kalması için Colab'ın sol menüsündeki **Secrets (🔑)** bölümüne `KAVRA_TTS_TOKEN` adıyla en az 16 karakterlik bir değer ekle.

> Not: Colab oturumu kapanır veya adres yenilenirse Kavra'da yeni adresi girip renderı yeniden başlat; biten sesler korunur."""

STEP_UPLOAD = '''# 1) GPU kontrolü ve paketi yükle
import os, shutil, subprocess, zipfile
from google.colab import files

gpu = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                     capture_output=True, text=True).stdout.strip()
print("GPU:", gpu or "YOK - Çalışma zamanı türünü GPU yap ve hücreyi yeniden çalıştır")

print("kavra-tts-bundle.zip dosyasını seç:")
uploaded = files.upload()
archive = next((name for name in uploaded if name.endswith(".zip")), None)
assert archive, "zip dosyası seçilmedi"
shutil.rmtree("/content/kavra", ignore_errors=True)
zipfile.ZipFile(archive).extractall("/content/kavra")
print("Paket açıldı:", sorted(os.listdir("/content/kavra")))'''

STEP_INSTALL = '''# 2) Bağımlılıkları kur (birkaç dakika sürer)
import subprocess, sys

result = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", "/content/kavra/tts_server/requirements.txt"])
assert result.returncode == 0, "Kurulum başarısız - yukarıdaki pip hatasını kontrol et"
print("Kurulum tamam")'''

STEP_SERVER = '''# 3) Sunucuyu başlat (modeller GPU'ya yüklenir, ~1-2 dk)
import os, secrets, subprocess, sys, time, requests

try:
    from google.colab import userdata
    TOKEN = userdata.get("KAVRA_TTS_TOKEN")
except Exception:
    TOKEN = None
TOKEN = TOKEN or secrets.token_urlsafe(24)

PORT = 8790
COQUI_MODELS = 2  # aynı anda yüklü XTTS kopyası; T4 (16 GB) için 2 uygundur, bellek yetmezse 1 yap

log = open("/content/kavra_tts.log", "w")
server = subprocess.Popen(
    [sys.executable, "-m", "tts_server.server", "--port", str(PORT), "--coqui-models", str(COQUI_MODELS),
     "--data-dir", "/content/kavra_tts_data"],
    cwd="/content/kavra", env={**os.environ, "KAVRA_TTS_TOKEN": TOKEN}, stdout=log, stderr=subprocess.STDOUT)

info = None
for _ in range(300):
    if server.poll() is not None:
        break
    try:
        info = requests.get(f"http://127.0.0.1:{PORT}/health", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=2).json()
        break
    except Exception:
        time.sleep(2)
if info is None:
    print(open("/content/kavra_tts.log").read()[-3000:])
    raise SystemExit("Sunucu başlamadı - yukarıdaki günlüğe bak")
print("GPU:", info["gpu"])
for name, state in info["engines"].items():
    print(f"  {name}: {'hazır' if state['available'] else 'KULLANILAMIYOR - ' + str(state['error'])}")'''

STEP_TUNNEL = '''# 4) İnternete aç (cloudflared, hesap gerekmez) ve bağlantı bilgilerini yazdır
import sys
sys.path.insert(0, "/content/kavra")
from tts_server.colab_tunnel import start_tunnel

tunnel, URL = start_tunnel(PORT)

def show():
    print("=" * 62)
    print("Kavra > Ses ayarları > Uzak GPU alanlarına gir:")
    print("  Adres:", URL)
    print("  Token:", TOKEN)
    print("=" * 62)
show()'''

STEP_KEEPALIVE = '''# 5) Bu hücreyi açık bırak: sunucu ve tüneli izler. Durdurmak için ■ düğmesine bas.
import time

try:
    while True:
        time.sleep(300)
        server_ok = server.poll() is None
        print(time.strftime("%H:%M"), "| sunucu:", "çalışıyor" if server_ok else "KAPANDI - 3. hücreyi yeniden çalıştır")
        if tunnel.poll() is not None:
            tunnel, URL = start_tunnel(PORT)
            print("Tünel yenilendi, ADRES DEĞİŞTİ:")
            show()
except KeyboardInterrupt:
    tunnel.terminate(); server.terminate()
    print("Durduruldu.")'''


def _cell(kind: str, source: str) -> dict:
    cell = {"cell_type": kind, "metadata": {}, "source": source.splitlines(keepends=True)}
    if kind == "code":
        cell.update(execution_count=None, outputs=[])
    return cell


def notebook() -> dict:
    cells = [_cell("markdown", INTRO)] + [
        _cell("code", src) for src in (STEP_UPLOAD, STEP_INSTALL, STEP_SERVER, STEP_TUNNEL, STEP_KEEPALIVE)
    ]
    return {
        "cells": cells,
        "metadata": {
            "accelerator": "GPU",
            "colab": {"name": "Kavra_TTS_Sunucusu.ipynb", "provenance": []},
            "kernelspec": {"display_name": "Python 3", "name": "python3"},
        },
        "nbformat": 4,
        "nbformat_minor": 0,
    }


def build_bundle(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative in BUNDLE_FILES:
            archive.write(ROOT / relative, relative)
    return target


def main() -> None:
    OUT.mkdir(exist_ok=True)
    bundle = build_bundle(OUT / "kavra-tts-bundle.zip")
    (OUT / "Kavra_TTS_Sunucusu.ipynb").write_text(json.dumps(notebook(), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Yazıldı: {bundle} ({bundle.stat().st_size // 1024} KB) ve Kavra_TTS_Sunucusu.ipynb")


if __name__ == "__main__":
    main()
