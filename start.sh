#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

mkdir -p "$ROOT_DIR/data"

PYTHON_BIN="python3"
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
fi

MODE="${1:-dev}"

if [ "$MODE" = "prod" ]; then
  if [ ! -d "$ROOT_DIR/frontend/dist" ]; then
    npm --prefix frontend install --no-audit --no-fund
    npm --prefix frontend run build
  fi
  PYTHONPATH="$ROOT_DIR/backend" exec "$PYTHON_BIN" -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
else
  # Development workflow: run FastAPI on :8000 and Vite dev server (with /api + WS proxy) on :5173
  PYTHONPATH="$ROOT_DIR/backend" "$PYTHON_BIN" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 &
  BACK_PID=$!
  trap 'kill $BACK_PID 2>/dev/null || true' EXIT INT TERM
  npm --prefix frontend run dev
fi
