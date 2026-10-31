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

# Ensure initial output exists on disk (or starts hydrating immediately in background)
if [ ! -f "output/entities.json" ]; then
  if [ -f "fuentes/record_index.json" ]; then
    echo "[run.sh] Generating initial output/ from local fuentes/..."
    "$PYTHON_BIN" -c '
from pathlib import Path
from app.sources import load_from_local_dir
from app.pipeline import run_full_pipeline
run_full_pipeline(load_from_local_dir(Path("fuentes")))
'
  elif [ -n "${SOURCES_URL:-}" ] && [ -n "${SOURCES_TOKEN:-}" ]; then
    echo "[run.sh] Launching initial warmup job from SOURCES_URL in background..."
    "$PYTHON_BIN" -c '
from app.config import SOURCES_URL, SOURCES_TOKEN
from app.jobs import create_and_launch_job
create_and_launch_job(SOURCES_URL, SOURCES_TOKEN)
' || true
  fi
fi

echo "[run.sh] Starting Kipu Pay Anonymization Service on ${HOST}:${PORT}..."
exec "$PYTHON_BIN" -m uvicorn app.main:app --host "$HOST" --port "$PORT"
