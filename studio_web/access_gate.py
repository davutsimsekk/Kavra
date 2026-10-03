"""KAVRA_ACCESS_TOKEN tanımlıysa arayüzü ve API'yi şifreyle kapatan ara katman.

Kavra herkese açık bir adresten (Colab + cloudflared gibi) yayınlanırken kullanılır.
Tarayıcı ``/?key=<şifre>`` bağlantısıyla ya da giriş formundan bir kez girer; şifre
adres çubuğundan silinip yerine HttpOnly çerez yazılır. Betikler ve sağlık kontrolü
``Authorization: Bearer <şifre>`` başlığını kullanabilir.
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from studio_web import access

SESSION_MAX_AGE = 7 * 24 * 3600

LOGIN_PAGE = """<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kavra girişi</title>
<style>
body{margin:0;min-height:100vh;display:grid;place-items:center;font-family:system-ui,sans-serif;background:#f4f4f5;color:#18181b}
form{background:#fff;padding:28px;border-radius:12px;box-shadow:0 4px 24px #0001;display:grid;gap:12px;width:min(340px,90vw)}
input,button{font:inherit;padding:10px 12px;border-radius:8px;border:1px solid #d4d4d8}
button{background:#18181b;color:#fff;border:0;cursor:pointer}
p{margin:0;color:#b91c1c;font-size:14px}
@media (prefers-color-scheme:dark){body{background:#18181b;color:#f4f4f5}form{background:#27272a}input{background:#18181b;color:#f4f4f5;border-color:#3f3f46}button{background:#f4f4f5;color:#18181b}}
</style></head>
<body><form method="get" action="/">
<strong>Kavra</strong>
<input type="password" name="key" placeholder="Erişim şifresi" autofocus required>
__ERROR__
<button type="submit">Giriş</button>
</form></body></html>"""


def _authorized(request: Request, token: str) -> bool:
    if access.session_matches(request.cookies.get(access.ACCESS_COOKIE), token):
        return True
    scheme, _, credential = request.headers.get("authorization", "").partition(" ")
    return scheme.lower() == "bearer" and access.token_matches(credential.strip(), token)


def install_access_gate(app: FastAPI, token: str) -> None:
    @app.middleware("http")
    async def require_access_token(request: Request, call_next):
        key = request.query_params.get("key")
        if key is not None:
            if not access.token_matches(key, token):
                return HTMLResponse(LOGIN_PAGE.replace("__ERROR__", "<p>Şifre hatalı.</p>"), status_code=401)
            # Şifreyi adres çubuğundan/geçmişten sil; oturumu çerez taşısın.
            rest = request.url.remove_query_params("key")
            response = RedirectResponse(rest.path + (f"?{rest.query}" if rest.query else ""), status_code=303)
            secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
            response.set_cookie(access.ACCESS_COOKIE, access.session_value(token), max_age=SESSION_MAX_AGE,
                                httponly=True, secure=secure, samesite="lax")
            return response
        if _authorized(request, token):
            return await call_next(request)
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Erişim şifresi gerekli."}, status_code=401)
        return HTMLResponse(LOGIN_PAGE.replace("__ERROR__", ""), status_code=401)
