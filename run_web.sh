#!/usr/bin/env bash
set -euo pipefail

KAVRA_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$KAVRA_SCRIPT_DIR/d_env.sh"

if [[ ! -x "$VENV_PY" ]]; then
  echo "Python ortamı bulunamadı. Önce: python -m venv venv"
  exit 1
fi

if [[ ! -f "$KAVRA_ROOT/webui/dist/index.html" ]]; then
  npm --prefix "$KAVRA_ROOT/webui" ci
  npm --prefix "$KAVRA_ROOT/webui" run build
fi

echo "Kavra http://127.0.0.1:8768 adresinde başlatılıyor..."
exec "$VENV_PY" -m uvicorn studio_web.api:app --host 127.0.0.1 --port 8768
