#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PYTHON_BIN=""
for cand in ".venv/bin/python" "python3" "python"; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" 2>/dev/null; then
    PYTHON_BIN="$cand"
    break
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  echo "Python 3.10+ not found — opening zero-install Standalone HTML edition..."
  open "Binance_Scanner_Standalone.html" 2>/dev/null || xdg-open "Binance_Scanner_Standalone.html" 2>/dev/null || true
  exit 0
fi

if [ ! -x ".venv/bin/python" ]; then
  "$PYTHON_BIN" -m venv .venv
fi
.venv/bin/python -c "import fastapi, uvicorn, pandas, numpy, aiohttp, aiosqlite" 2>/dev/null || .venv/bin/pip install -q -r requirements.txt

mkdir -p data
(sleep 2 && (open "http://127.0.0.1:8000" 2>/dev/null || xdg-open "http://127.0.0.1:8000" 2>/dev/null || true)) &
PYTHONPATH="$(pwd)/backend" exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
