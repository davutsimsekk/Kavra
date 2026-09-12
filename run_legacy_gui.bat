@echo off
setlocal
set ROOT=%~dp0
set TEMP=%ROOT%_cache\tmp
set TMP=%ROOT%_cache\tmp
set PIP_CACHE_DIR=%ROOT%_cache\pip
set HF_HOME=%ROOT%_cache\hf
set TORCH_HOME=%ROOT%_cache\torch
set PYTHONPYCACHEPREFIX=%ROOT%_cache\pycache
set PYTHONIOENCODING=utf-8
set COQUI_TOS_AGREED=1
set HF_HUB_DISABLE_XET=1
set HF_XET_CACHE=%ROOT%_cache\hf_xet
set TTS_HOME=%ROOT%_cache\tts_home
if not exist "%TEMP%" mkdir "%TEMP%"
"%ROOT%venv\Scripts\python.exe" "%ROOT%gui\app_gui.py"
pause
