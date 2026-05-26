#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -n "${PKB_PYTHON:-}" ]; then
  PY="$PKB_PYTHON"
elif [ -n "${PYTHON:-}" ]; then
  PY="$PYTHON"
else
  PY="python3"
fi

PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/pkb-pycache}" exec "$PY" -m app.server
