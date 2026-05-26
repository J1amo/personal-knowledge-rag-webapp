#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

LOCAL_MODELS_DIR="${LOCAL_MODELS_DIR:-${PROJECT_ROOT}/local_models}"
mkdir -p logs run data/raw db indexes cache backups "$LOCAL_MODELS_DIR"

if [ -n "${PKB_PYTHON:-}" ]; then
  PY="$PKB_PYTHON"
elif [ -n "${PYTHON:-}" ]; then
  PY="$PYTHON"
else
  PY="$(command -v python3)"
fi

export HOST="${HOST:-127.0.0.1}"
export PORT="${PORT:-8765}"
export LOCAL_MODELS_DIR
export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/pkb-pycache}"
export PYTHONUNBUFFERED=1

exec "$PY" -m app.server
