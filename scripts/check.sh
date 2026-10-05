#!/usr/bin/env bash
# Run every local quality check: legacy Python (ruff + pytest) then web/ (npm run check).
# Usage: bash scripts/check.sh   (needs .venv from requirements-dev.txt and Node 22)
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/Scripts/python
[ -x "$PY" ] || PY=.venv/bin/python
[ -x "$PY" ] || { echo "Missing .venv - run: python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt"; exit 1; }

echo "== ruff";   "$PY" -m ruff check .
echo "== pytest"; "$PY" -m pytest -q
echo "== web";    (cd web && npm run check)
