"""models/ klasörünü (Piper modeli + klonladığın referans sesler) VPS'e kopyalar.

Uzak GPU (PC veya Colab) render sırasında sesi ÜRETİR, ama Kavra'nın ses listesi
(GET /api/voices/coqui, /api/voices/piper) ve dosya yükleme mantığı API'nin ÇALIŞTIĞI
makinedeki (VPS) models/ klasörüne bakar. Yeni bir ses klonladığında ya da Piper modeli
eklediğinde bu betiği tekrar çalıştır; VPS'teki Kavra'yı yeniden başlatman gerekmez
(ses listesi her istekte diskten okunur).

  python tools/sync_models_to_vps.py kullanici@vps-adresi
  python tools/sync_models_to_vps.py kullanici@vps.tailnet-adi.ts.net --remote-dir kavra
  python tools/sync_models_to_vps.py kullanici@1.2.3.4 --port 2222 --identity ~/.ssh/vps_key
  python tools/sync_models_to_vps.py kullanici@vps-adresi --dry-run   # yalnızca ne kopyalanacağını göster
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "models"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", help="kullanici@vps-adresi (Tailscale MagicDNS adı da olur)")
    parser.add_argument("--remote-dir", default="kavra",
                        help="VPS'teki Kavra klasörü, ev dizinine göre (varsayılan: kavra -> ~/kavra/models)")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--identity", "-i", help="Belirli bir SSH özel anahtarı (ssh -i)")
    parser.add_argument("--dry-run", action="store_true", help="Kopyalamadan yalnızca komutu ve içeriği göster")
    args = parser.parse_args()

    if not MODELS_DIR.is_dir():
        raise SystemExit(f"models/ klasörü bulunamadı: {MODELS_DIR}")
    entries = sorted(p.name for p in MODELS_DIR.iterdir() if not p.name.startswith("."))
    if not entries:
        raise SystemExit("models/ klasörü boş; kopyalanacak bir şey yok.")
    print("Kopyalanacak: " + ", ".join(entries))

    cmd = ["scp", "-r", "-P", str(args.port)]
    if args.identity:
        cmd += ["-i", args.identity]
    cmd += [str(MODELS_DIR), f"{args.target}:{args.remote_dir}/"]

    if args.dry_run:
        print("(deneme, kopyalanmadı) " + " ".join(cmd))
        return

    print("Bağlanılıyor: " + args.target)
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise SystemExit(
            f"Kopyalama başarısız (çıkış kodu {result.returncode}). "
            f"'~/{args.remote_dir}' klasörünün VPS'te var olduğundan ve SSH erişiminin çalıştığından emin ol."
        )
    print(f"Tamamlandı: ~/{args.remote_dir}/models VPS'te güncellendi. Kavra'yı yeniden başlatman gerekmez.")


if __name__ == "__main__":
    main()
