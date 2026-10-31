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

echo "[run.sh] Starting Kipu Pay Anonymization Service on ${HOST}:${PORT}..."
exec "$PYTHON_BIN" -m uvicorn app.main:app --host "$HOST" --port "$PORT"
