@echo off
setlocal
set ROOT=%~dp0
set TEMP=%ROOT%_cache\tmp
set TMP=%ROOT%_cache\tmp
set PIP_CACHE_DIR=%ROOT%_cache\pip
set NPM_CONFIG_CACHE=%ROOT%_cache\npm
set HF_HOME=%ROOT%_cache\hf
set TORCH_HOME=%ROOT%_cache\torch
set PYTHONPYCACHEPREFIX=%ROOT%_cache\pycache
set PYTHONIOENCODING=utf-8
set COQUI_TOS_AGREED=1
set HF_HUB_DISABLE_XET=1
set HF_XET_CACHE=%ROOT%_cache\hf_xet
set TTS_HOME=%ROOT%_cache\tts_home
if not exist "%TEMP%" mkdir "%TEMP%"
if not exist "%NPM_CONFIG_CACHE%" mkdir "%NPM_CONFIG_CACHE%"
if not exist "%ROOT%webui\dist\index.html" (
  echo Modern arayuz ilk kez hazirlaniyor...
  call npm --prefix "%ROOT%webui" install
  if errorlevel 1 goto :error
  call npm --prefix "%ROOT%webui" run build
  if errorlevel 1 goto :error
)
echo Ders Studyosu http://127.0.0.1:8765 adresinde aciliyor...
"%ROOT%venv\Scripts\python.exe" -m studio_web.main
goto :eof
:error
echo Arayuz hazirlanamadi. Yukaridaki hata mesajini kontrol et.
pause
