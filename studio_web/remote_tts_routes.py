"""Uzak GPU ayarları: iki sabit profil (Bu bilgisayar / Colab), adres + token, bağlantı testi.

Token, diğer API anahtarları gibi .env dosyasına profil başına ayrı bir değişkende yazılır ve
hiçbir yanıtta geri dönmez. Render her zaman ayarlardaki AKTİF profili kullanır (bkz.
app/tts/remote.py RemoteTTSConfig.load); bu modül profilleri kaydetmeye ve aralarında
geçiş yapmaya yarar.
"""
from __future__ import annotations

import os
import re

from fastapi import Body, HTTPException

from app.config import delete_api_key, load_settings, save_api_key, save_settings
from app.tts.remote import (
    PROFILES,
    RemoteTTSConfig,
    RemoteTTSError,
    check_connection,
    normalize_url,
    token_env_name,
)

_TOKEN_RE = re.compile(r"^[A-Za-z0-9._~-]{16,200}$")


def _profile_state(name: str, settings: dict) -> dict:
    url = str(((settings.get("remote_tts_profiles") or {}).get(name) or {}).get("url", "")).strip()
    return {"url": url, "tokenSet": bool(os.environ.get(token_env_name(name), "").strip())}


def remote_tts_state() -> dict:
    settings = load_settings()
    profiles = {name: _profile_state(name, settings) for name in PROFILES}
    active = str(settings.get("remote_tts_active_profile", "")).strip() or None
    if active not in (None, *PROFILES):
        active = None
    configured = bool(active and profiles[active]["url"] and profiles[active]["tokenSet"])
    return {"active": active, "profiles": profiles, "configured": configured}


def _require_profile(name: str) -> str:
    if name not in PROFILES:
        raise HTTPException(404, f"Bilinmeyen GPU profili: {name!r}.")
    return name


def register_remote_tts_routes(app) -> None:
    @app.get("/api/remote-tts")
    def get_remote_tts():
        return remote_tts_state()

    @app.put("/api/remote-tts/{profile}")
    def put_remote_tts_profile(profile: str, payload: dict = Body(...)):
        _require_profile(profile)
        try:
            url = normalize_url(str(payload.get("url", "")))
        except RemoteTTSError as exc:
            raise HTTPException(400, str(exc)) from exc
        token = str(payload.get("token", "")).strip()
        if token and not _TOKEN_RE.match(token):
            raise HTTPException(400, "Token en az 16 karakter olmalı; yalnızca harf, rakam ve . _ ~ - içerebilir.")
        settings = load_settings()
        profiles = dict(settings.get("remote_tts_profiles") or {})
        profiles[profile] = {"url": url}
        settings["remote_tts_profiles"] = profiles
        save_settings(settings)
        if token:  # boş token mevcut olanı korur
            save_api_key(token_env_name(profile), token)
        return remote_tts_state()

    @app.delete("/api/remote-tts/{profile}")
    def delete_remote_tts_profile(profile: str):
        _require_profile(profile)
        settings = load_settings()
        profiles = dict(settings.get("remote_tts_profiles") or {})
        profiles[profile] = {"url": ""}
        settings["remote_tts_profiles"] = profiles
        if settings.get("remote_tts_active_profile") == profile:
            settings["remote_tts_active_profile"] = ""
        save_settings(settings)
        delete_api_key(token_env_name(profile))
        return remote_tts_state()

    @app.post("/api/remote-tts/active")
    def set_active_remote_tts_profile(payload: dict = Body(...)):
        raw = payload.get("profile")
        profile = "" if raw in (None, "") else _require_profile(str(raw))
        settings = load_settings()
        settings["remote_tts_active_profile"] = profile
        save_settings(settings)
        return remote_tts_state()

    @app.post("/api/remote-tts/{profile}/test")
    def test_remote_tts_profile(profile: str):
        _require_profile(profile)
        try:
            info = check_connection(RemoteTTSConfig.for_profile(profile))
        except RemoteTTSError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "gpu": info.get("gpu"), "engines": info.get("engines", {}), "latencyMs": info["latencyMs"]}
