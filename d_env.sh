#!/usr/bin/env bash
# Kavra'nın tüm önbelleklerini bu dosyanın bulunduğu disk altında tutar.
# Kullanım: source ./d_env.sh

KAVRA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

export TEMP="$KAVRA_ROOT/_cache/tmp"
export TMP="$KAVRA_ROOT/_cache/tmp"
export TMPDIR="$KAVRA_ROOT/_cache/tmp"
export PIP_CACHE_DIR="$KAVRA_ROOT/_cache/pip"
export NPM_CONFIG_CACHE="$KAVRA_ROOT/_cache/npm"
export HF_HOME="$KAVRA_ROOT/_cache/hf"
export HUGGINGFACE_HUB_CACHE="$KAVRA_ROOT/_cache/hf"
export TORCH_HOME="$KAVRA_ROOT/_cache/torch"
export XDG_CACHE_HOME="$KAVRA_ROOT/_cache/xdg"
export PYTHONPYCACHEPREFIX="$KAVRA_ROOT/_cache/pycache"
export PYTHONIOENCODING="utf-8"
export COQUI_TOS_AGREED="1"
export HF_HUB_DISABLE_XET="1"
export HF_XET_CACHE="$KAVRA_ROOT/_cache/hf_xet"
export TTS_HOME="$KAVRA_ROOT/_cache/tts_home"

mkdir -p "$TEMP" "$PIP_CACHE_DIR" "$NPM_CONFIG_CACHE"

if [[ -x "$KAVRA_ROOT/venv/bin/python" ]]; then
  VENV_PY="$KAVRA_ROOT/venv/bin/python"
  VENV_PIP="$KAVRA_ROOT/venv/bin/pip"
else
  # Git Bash / MSYS üzerinde Windows venv düzeni.
  VENV_PY="$KAVRA_ROOT/venv/Scripts/python.exe"
  VENV_PIP="$KAVRA_ROOT/venv/Scripts/pip.exe"
fi
export VENV_PY VENV_PIP KAVRA_ROOT
