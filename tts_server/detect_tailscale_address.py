"""Bu makinenin Tailscale MagicDNS adresini `tailscale status --json`'dan okuyup dosyaya yazar.

Adres, `tailscale serve --bg 8790` ile yayımlanan URL'nin ana parçasıdır (bkz. REMOTE_TTS.md).

  python detect_tailscale_address.py <hedef-dosya>

Tespit başarılıysa adresi <hedef-dosya>'ya ATOMİK olarak yazar (geçici bir dosyaya yazıp
os.replace ile yerine koyar) ve stdout'a da basıp 0 ile çıkar. Tailscale çalışmıyorsa, giriş
yapılmamışsa, komut bulunamazsa veya hedef dosya verilmemişse hiçbir şey yazdırmaz/dokunmaz ve
1 ile çıkar — çağıran `run_tts_server.bat` bunu "tespit edilemedi" olarak yorumlar.

Atomik yazım kasıtlı: run_tts_server.bat bu betiği paylaşılan tek bir ara dosya üzerinden
(`%RANDOM%` gibi öngörülebilir bir adla) çağırırsa, aynı anda başlayan iki sunucu örneği
(elle çift tık + otomatik başlatma, ya da yanlışlıkla iki kez tıklama) birbirinin yarım yazdığı
veriyi okuyabiliyordu. Yazma işini tamamen buraya, tek bir os.replace çağrısına taşımak bu
yarışı ortadan kaldırıyor: her süreç kendi benzersiz geçici dosyasına yazar (tempfile modülü
PID+sayaç ile üretir), sonuç ya tam eski ya tam yeni içerik olur, asla yarım olmaz.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        return 1
    target = Path(sys.argv[1])
    try:
        result = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=5)
        name = json.loads(result.stdout)["Self"]["DNSName"].rstrip(".")
        if not name:
            return 1
        address = f"https://{name}"
    except Exception:
        return 1

    fd, tmp_path = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(address + "\n")
        os.replace(tmp_path, target)
    except OSError:
        os.unlink(tmp_path)
        return 1
    print(address)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
