"""API'nin kabul edeceği Host ve Origin değerleri.

Varsayılan yalnızca yerel kullanımdır (127.0.0.1 / localhost). Kavra'yı bir sunucuda
çalıştırıp yalnızca özel bir ağdan (örn. Tailscale) açarken alan adı ortam
değişkenleriyle eklenir:

  KAVRA_ALLOWED_HOSTS    virgülle ayrılmış host adları, örn. "kavra.tailnet-adi.ts.net"
  KAVRA_ALLOWED_ORIGINS  virgülle ayrılmış tarayıcı origin'leri (isteğe bağlı);
                         verilmezse her ek host için http:// ve https:// türetilir.
  KAVRA_PUBLISHED_PORT   Docker'ın ana makinede yayımladığı port (compose KAVRA_PORT'tan gelir);
                         127.0.0.1/localhost için o portun origin'i de kabul edilir.

Bu değerler kimlik doğrulama yerine geçmez: API'nin kendi girişi yoktur, erişimi
ağ katmanı (Tailscale, güvenlik duvarı) kısıtlamalıdır. Bu yüzden joker ("*")
kabul edilmez.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

LOCAL_HOSTS = ("127.0.0.1", "localhost")
LOCAL_ORIGINS = (
    "http://127.0.0.1:8768",
    "http://localhost:8768",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
)


class AccessConfigError(ValueError):
    """Geçersiz KAVRA_ALLOWED_* değeri; sessizce yutulursa erişim kontrolü zayıflar."""


def _items(raw: str | None) -> list[str]:
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def _normalize_host(value: str) -> str:
    if "*" in value:
        raise AccessConfigError("KAVRA_ALLOWED_HOSTS içinde joker (*) kullanılamaz.")
    # "https://kavra.ts.net:443/" yapıştırılırsa yalnız host adını al.
    host = urlsplit(value if "://" in value else f"//{value}").hostname
    if not host:
        raise AccessConfigError(f"Geçersiz host adı: {value!r}")
    return host.lower()


def _normalize_origin(value: str) -> str:
    if "*" in value:
        raise AccessConfigError("KAVRA_ALLOWED_ORIGINS içinde joker (*) kullanılamaz.")
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise AccessConfigError(
            f"Origin http:// veya https:// ile başlamalı (örn. https://kavra.ts.net): {value!r}"
        )
    if parts.path not in {"", "/"} or parts.query or parts.fragment:
        raise AccessConfigError(f"Origin yol içeremez: {value!r}")
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{parts.hostname.lower()}{port}"


def extra_hosts(env: dict[str, str] | None = None) -> list[str]:
    env = os.environ if env is None else env
    return [_normalize_host(item) for item in _items(env.get("KAVRA_ALLOWED_HOSTS"))]


def allowed_hosts(env: dict[str, str] | None = None) -> list[str]:
    seen: dict[str, None] = dict.fromkeys(LOCAL_HOSTS)
    seen.update(dict.fromkeys(extra_hosts(env)))
    return list(seen)


def allowed_origins(env: dict[str, str] | None = None) -> set[str]:
    env = os.environ if env is None else env
    origins = set(LOCAL_ORIGINS)
    port = (env.get("KAVRA_PUBLISHED_PORT") or "").strip()
    if port.isdigit() and 0 < int(port) < 65536:
        origins.update({f"http://127.0.0.1:{port}", f"http://localhost:{port}"})
    explicit = _items(env.get("KAVRA_ALLOWED_ORIGINS"))
    if explicit:
        origins.update(_normalize_origin(item) for item in explicit)
    else:
        for host in extra_hosts(env):
            origins.update({f"https://{host}", f"http://{host}"})
    return origins
