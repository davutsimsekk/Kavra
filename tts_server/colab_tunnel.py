"""Colab not defteri için cloudflared "quick tunnel" yardımcısı (hesap gerektirmez).

Tünel adresi her başlatmada değişir; bu yüzden adres cloudflared'in çıktısından okunur.
Not: hata mesajlarında geçen ``api.trycloudflare.com`` bir tünel adresi değildir.
"""
from __future__ import annotations

import os
import queue
import re
import stat
import subprocess
import threading
import urllib.request

CLOUDFLARED_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
CLOUDFLARED_BIN = "/usr/local/bin/cloudflared"
_URL_RE = re.compile(r"https://([a-z0-9-]+)\.trycloudflare\.com")


def find_url(line: str) -> str | None:
    for match in _URL_RE.finditer(line or ""):
        if match.group(1) != "api":
            return match.group(0)
    return None


def ensure_binary(path: str = CLOUDFLARED_BIN) -> str:
    if not os.path.exists(path):
        urllib.request.urlretrieve(CLOUDFLARED_URL, path)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


def start_tunnel(port: int, timeout: float = 60.0, binary: str | None = None) -> tuple[subprocess.Popen, str]:
    """cloudflared'i başlatır; (süreç, https adresi) döndürür."""
    proc = subprocess.Popen(
        [binary or ensure_binary(), "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    found: queue.Queue = queue.Queue()

    def pump() -> None:  # çıktıyı sürekli boşalt ki boru dolup süreci kilitlemesin
        for line in proc.stdout:
            url = find_url(line)
            if url and found.empty():
                found.put(url)

    threading.Thread(target=pump, daemon=True).start()
    try:
        return proc, found.get(timeout=timeout)
    except queue.Empty:
        proc.kill()
        raise RuntimeError("cloudflared tünel adresi üretmedi (60 sn). Hücreyi yeniden çalıştır.") from None
