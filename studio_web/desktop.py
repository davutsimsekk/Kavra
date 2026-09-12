"""Native-looking desktop launcher for the shared React/FastAPI application.

The browser is started in Chromium app mode, so the desktop and web variants use
the exact same UI and API instead of slowly drifting into two different products.
The legacy Tk interface remains available through ``run_legacy_gui.bat``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Mapping

import uvicorn

from app.config import CACHE_DIR, ROOT


APP_URL = "http://127.0.0.1:8765"


def browser_candidates(environ: Mapping[str, str] | None = None) -> list[tuple[str, Path]]:
    env = os.environ if environ is None else environ
    roots = [
        env.get("PROGRAMFILES(X86)"),
        env.get("PROGRAMFILES"),
        env.get("LOCALAPPDATA"),
    ]
    relatives = [
        ("Microsoft Edge", Path("Microsoft/Edge/Application/msedge.exe")),
        ("Google Chrome", Path("Google/Chrome/Application/chrome.exe")),
    ]
    candidates: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for label, relative in relatives:
        for root in roots:
            if not root:
                continue
            path = Path(root) / relative
            key = str(path).casefold()
            if key not in seen:
                candidates.append((label, path))
                seen.add(key)
    return candidates


def find_app_browser(
    environ: Mapping[str, str] | None = None,
) -> tuple[str, Path] | None:
    return next(
        ((label, path) for label, path in browser_candidates(environ) if path.is_file()),
        None,
    )


def browser_app_args(executable: Path, url: str, profile_dir: Path) -> list[str]:
    return [
        str(executable),
        f"--app={url}",
        f"--user-data-dir={profile_dir}",
        "--window-size=1440,900",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
    ]


def server_healthy(url: str = APP_URL, timeout: float = 0.8) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/bootstrap", timeout=timeout) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def wait_for_server(url: str, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server_healthy(url):
            return True
        time.sleep(0.15)
    return False


def launch_desktop(url: str = APP_URL) -> int:
    frontend = ROOT / "webui" / "dist" / "index.html"
    if not frontend.exists():
        raise RuntimeError(
            "Modern arayüz build'i bulunamadı. run_gui.bat dosyasını kullanarak "
            "frontend'i otomatik hazırlayabilirsin."
        )

    owns_server = not server_healthy(url)
    server: uvicorn.Server | None = None
    server_thread: threading.Thread | None = None
    if owns_server:
        config = uvicorn.Config(
            "studio_web.api:app",
            host="127.0.0.1",
            port=8765,
            log_level="warning",
        )
        server = uvicorn.Server(config)
        server_thread = threading.Thread(target=server.run, name="ders-api", daemon=True)
        server_thread.start()
        if not wait_for_server(url):
            server.should_exit = True
            raise RuntimeError("Yerel Ders Stüdyosu servisi 20 saniyede başlayamadı.")

    try:
        browser = find_app_browser()
        if browser:
            label, executable = browser
            profile_dir = CACHE_DIR / "desktop_browser" / f"session_{os.getpid()}"
            profile_dir.mkdir(parents=True, exist_ok=True)
            creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            process = subprocess.Popen(
                browser_app_args(executable, url, profile_dir),
                cwd=ROOT,
                creationflags=creation_flags,
            )
            print(f"Ders Stüdyosu {label} uygulama penceresinde açıldı.")
            return process.wait()

        # App-mode capable bir tarayıcı bulunamazsa işlevsiz kalmak yerine normal
        # varsayılan tarayıcıya düş. Bu modda terminal Ctrl+C ile kapatılır.
        webbrowser.open(url)
        print("Edge/Chrome bulunamadı; Ders Stüdyosu varsayılan tarayıcıda açıldı.")
        if owns_server and server_thread:
            while server_thread.is_alive():
                time.sleep(0.5)
        return 0
    except KeyboardInterrupt:
        return 130
    finally:
        if owns_server and server is not None:
            server.should_exit = True
            if server_thread is not None:
                server_thread.join(timeout=5)


def diagnostics() -> dict:
    browser = find_app_browser()
    return {
        "frontendReady": (ROOT / "webui" / "dist" / "index.html").exists(),
        "serverRunning": server_healthy(),
        "browser": browser[0] if browser else None,
        "browserPath": str(browser[1]) if browser else None,
        "url": APP_URL,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ders Stüdyosu masaüstü başlatıcısı")
    parser.add_argument("--check", action="store_true", help="Pencere açmadan kurulumu denetle")
    args = parser.parse_args(argv)
    if args.check:
        print(json.dumps(diagnostics(), ensure_ascii=False, indent=2))
        return 0
    return launch_desktop()


if __name__ == "__main__":
    raise SystemExit(main())
