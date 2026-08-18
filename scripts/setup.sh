#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

PYTHON_BIN=
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && \
    "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
    PYTHON_BIN=$candidate
    break
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  echo "需要 Python 3.11 或更高版本。" >&2
  exit 1
fi

PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" \
  exec "$PYTHON_BIN" -m codex_configurator setup "$@"
