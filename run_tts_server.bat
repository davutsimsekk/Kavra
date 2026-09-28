@echo off
rem Bu bilgisayarin GPU'sunu uzak Kavra TTS sunucusu olarak acar (XTTS v2 + Piper).
rem VPS'teki Kavra bu bilgisayari "Uzak GPU" olarak kullanabilir; bkz. REMOTE_TTS.md.
setlocal
set ROOT=%~dp0
set TEMP=%ROOT%_cache\tmp
set TMP=%ROOT%_cache\tmp
set HF_HOME=%ROOT%_cache\hf
set TORCH_HOME=%ROOT%_cache\torch
set PYTHONPYCACHEPREFIX=%ROOT%_cache\pycache
set PYTHONIOENCODING=utf-8
set COQUI_TOS_AGREED=1
set HF_HUB_DISABLE_XET=1
set HF_XET_CACHE=%ROOT%_cache\hf_xet
set TTS_HOME=%ROOT%_cache\tts_home
if not exist "%TEMP%" mkdir "%TEMP%"

rem Token ilk calistirmada uretilir ve sonraki calistirmalarda ayni kalir.
set TOKEN_FILE=%ROOT%tts_server_token.txt
rem Eski surumler token'i _cache\ altinda tutuyordu (bkz. KAVRA_PROJECT_HANDOFF.md); _cache
rem silinebilir/yeniden uretilebilir kabul edildigi icin oradaki token'i yeni konuma tasi ki
rem VPS'e zaten kaydedilmis token gecersiz kalip sessizce 401 vermesin.
set OLD_TOKEN_FILE=%ROOT%_cache\tts_server_token.txt
if not exist "%TOKEN_FILE%" if exist "%OLD_TOKEN_FILE%" move /y "%OLD_TOKEN_FILE%" "%TOKEN_FILE%" >nul
if not exist "%TOKEN_FILE%" "%ROOT%venv\Scripts\python.exe" -c "import secrets; open(r'%TOKEN_FILE%','w').write(secrets.token_urlsafe(24))"
set /p KAVRA_TTS_TOKEN=<"%TOKEN_FILE%"

rem Adres, token gibi sabit degil: her calistirmada Tailscale'den yeniden tespit edilir (bkz.
rem tts_server/detect_tailscale_address.py). Tespit basarisiz olursa (Tailscale kapali/giris yapilmamis)
rem eski dosyaya dokunulmaz, yalnizca konsolda uyari gosterilir. Dosyaya yazma islemi atomik
rem olarak python tarafinda yapilir (bkz. betigin docstring'i) - burada gecici dosya kullanilmiyor,
rem ayni anda iki ornek calissa bile birbirlerini ezmezler.
set ADDRESS_FILE=%ROOT%tts_server_address.txt
set TS_ADDRESS=
"%ROOT%venv\Scripts\python.exe" "%ROOT%tts_server\detect_tailscale_address.py" "%ADDRESS_FILE%" >nul 2>nul
if not errorlevel 1 set /p TS_ADDRESS=<"%ADDRESS_FILE%"

echo.
echo Kavra TTS sunucusu http://127.0.0.1:8790 adresinde aciliyor (modeller ilk render'da yuklenir, ~15-30 sn surer).
echo Token : %KAVRA_TTS_TOKEN%
if defined TS_ADDRESS (
  echo Adres : %TS_ADDRESS%
) else (
  echo Adres : tespit edilemedi - "tailscale status" ile Tailscale'in acik/giris yapilmis oldugundan emin ol.
)
echo.
echo VPS'ten kullanmak icin (bir kez, yonetici gerekmez):
echo     tailscale serve --bg 8790
echo Sonra VPS'teki Kavra ^> Ses ayarlari ^> Uzak GPU alanina yukaridaki Adres ve Token'i gir.
echo Ikisi de dosyaya da yazildi: tts_server_address.txt / tts_server_token.txt
echo Bu pencere acik kaldigi surece calisir. Kapatmak icin Ctrl+C.
echo.
rem --no-preload: model(ler) ilk render isteğinde yüklenir. Bu sunucu artık Windows açılışında
rem otomatik başlayıp sürekli arka planda beklediği için (bkz. install_tts_autostart.ps1) modelleri
rem baştan yükleyip VRAM'i boşuna işgal etmez; ilk render'da ~15-30 sn ekstra bekleme olur.
"%ROOT%venv\Scripts\python.exe" -m tts_server.server --port 8790 --coqui-models 2 --no-preload --data-dir "%ROOT%_cache\tts_server_data"
