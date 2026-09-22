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
set TOKEN_FILE=%ROOT%_cache\tts_server_token.txt
if not exist "%TOKEN_FILE%" "%ROOT%venv\Scripts\python.exe" -c "import secrets; open(r'%TOKEN_FILE%','w').write(secrets.token_urlsafe(24))"
set /p KAVRA_TTS_TOKEN=<"%TOKEN_FILE%"

echo.
echo Kavra TTS sunucusu http://127.0.0.1:8790 adresinde aciliyor (modeller ilk render'da yuklenir, ~15-30 sn surer).
echo Token: %KAVRA_TTS_TOKEN%
echo.
echo VPS'ten kullanmak icin (bir kez, yonetici gerekmez):
echo     tailscale serve --bg 8790
echo Sonra VPS'teki Kavra ^> Ses ayarlari ^> Uzak GPU alanina https://BU-PC-ADI.TAILNET-ADI.ts.net adresini ve yukaridaki token'i gir.
echo Bu pencere acik kaldigi surece calisir. Kapatmak icin Ctrl+C.
echo.
rem --no-preload: model(ler) ilk render isteğinde yüklenir. Bu sunucu artık Windows açılışında
rem otomatik başlayıp sürekli arka planda beklediği için (bkz. install_tts_autostart.ps1) modelleri
rem baştan yükleyip VRAM'i boşuna işgal etmez; ilk render'da ~15-30 sn ekstra bekleme olur.
"%ROOT%venv\Scripts\python.exe" -m tts_server.server --port 8790 --coqui-models 2 --no-preload --data-dir "%ROOT%_cache\tts_server_data"
