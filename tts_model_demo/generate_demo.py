"""Tek referans sesle Coqui, Anka ve Chatterbox dinleme demosunu üretir."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent / "public"
REFERENCE = ROOT / "_cache" / "hf" / "hub" / "models--krmkayabasi--Anka-TTS" / "snapshots" / "f1ce92d4eeb02ab0d57b537493b6dd365b24607b" / "eval" / "reference"

SAMPLES = [
    ("Kısa", "Merhaba! Bu ses, aynı referans konuşmacının klonlanmasıyla üretildi. Amacım hangi motorun daha doğal duyulduğunu karşılaştırman."),
    ("Orta", "Şimdi Java, Python, C ve C++'ı yan yana koyup genel bir tablo çıkaralım. Derleme şekli, bellek yönetimi ve çalışma zamanı davranışı açısından bu diller birbirinden oldukça farklı yaklaşımlar benimser."),
    ("Uzun", "İşte tam bu noktadan başlayıp, sıfırdan C öğreneceğiz. Gömülü sistemler dünyasına geldiğimizde tablo tamamen değişiyor. Java önce bayt koduna derlenir, sonra sanal makine üzerinde çalışır; Python ise çoğunlukla yorumlanarak çalıştırılır. C ise doğrudan makine koduna derlenir ve donanıma çok daha yakın bir katmanda çalışır, bu da onu gömülü sistemler için vazgeçilmez kılar."),
]


def main() -> None:
    if not (REFERENCE / "male.wav").exists():
        raise SystemExit("Anka örnek referans sesi henüz indirilmemiş.")
    OUT.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REFERENCE / "male.wav", OUT / "reference.wav")
    shutil.copy2(REFERENCE / "male.txt", OUT / "reference.txt")

    report = {"referenceText": (REFERENCE / "male.txt").read_text(encoding="utf-8").strip(), "samples": []}
    for number, (title, text) in enumerate(SAMPLES, start=1):
        folder = OUT / f"sample-{number}"
        folder.mkdir(exist_ok=True)
        ref = folder / "reference.wav"
        shutil.copy2(OUT / "reference.wav", ref)
        shutil.copy2(OUT / "reference.txt", ref.with_suffix(".txt"))
        spec = {"text": text, "referencePath": str(ref)}
        spec_path = folder / "spec.json"
        spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        subprocess.run([sys.executable, "-m", "studio_web.tts_comparison_worker", str(spec_path)], cwd=ROOT, check=True)
        result = json.loads((folder / "results.json").read_text(encoding="utf-8"))["results"]
        for item in result:
            if item.get("audioFile"):
                item["audioUrl"] = f"sample-{number}/{item['audioFile']}"
        report["samples"].append({"number": number, "title": title, "text": text, "results": result})
    (OUT / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
