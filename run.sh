#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ -f ".env" ]; then
  set -a
  source .env
  set +a
fi

PYTHON_BIN="python3"
if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
fi

PORT="${PORT:-9000}"
HOST="${HOST:-0.0.0.0}"

# Ensure initial output exists on disk before serving requests
if [ ! -f "output/entities.json" ] && [ -f "fuentes/record_index.json" ]; then
  echo "[run.sh] Generating initial output/ from local fuentes/..."
  "$PYTHON_BIN" -c '
from pathlib import Path
from app.sources import load_from_local_dir
from app.pipeline import run_full_pipeline
run_full_pipeline(load_from_local_dir(Path("fuentes")))
'
fi

echo "[run.sh] Starting Kipu Pay Anonymization Service on ${HOST}:${PORT}..."
exec "$PYTHON_BIN" -m uvicorn app.main:app --host "$HOST" --port "$PORT"
