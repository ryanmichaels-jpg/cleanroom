#!/usr/bin/env bash
#
# Cleanroom — one-shot environment setup.
#
# Creates a venv, installs the package in editable mode, applies the macOS
# hidden-flag workaround (same gotcha as Reply Guy + Signal Catcher: pip's
# .pth file inherits UF_HIDDEN under Documents/Claude/, and Python's site.py
# silently skips hidden .pth files), and runs the smoke test if one exists.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO="$(pwd)"
echo "→ repo: $REPO"

# --- 1. Python 3.11+ ---------------------------------------------------------

PY=""
for v in python3.13 python3.12 python3.11; do
  if command -v "$v" >/dev/null 2>&1; then PY="$v"; break; fi
done
if [ -z "$PY" ]; then
  echo "✗ need Python 3.11+ (not found: python3.11 / python3.12 / python3.13)"
  exit 1
fi
echo "→ python: $($PY --version) ($(command -v $PY))"

# --- 2. venv + install -------------------------------------------------------

if [ ! -d .venv ]; then
  echo "→ creating .venv"
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --quiet --upgrade pip
echo "→ installing cleanroom in editable mode + dev deps"
pip install --quiet -e ".[dev]"

# --- 3. macOS hidden-flag workaround -----------------------------------------

if [ "$(uname)" = "Darwin" ]; then
  echo "→ clearing macOS hidden flag from .venv (Python .pth load workaround)"
  chflags -R nohidden .venv
fi

# --- 4. .env scaffold --------------------------------------------------------

if [ ! -f .env ]; then
  cp .env.example .env
  echo "→ created .env from .env.example (fill in real keys before running --live)"
fi

# --- 5. Smoke test (only if any tests exist) ---------------------------------

if compgen -G "tests/test_*.py" >/dev/null; then
  echo "→ smoke test"
  if ! pytest -q --tb=short; then
    echo "✗ smoke test failed."
    exit 1
  fi
else
  echo "→ no tests yet — skipping smoke step"
fi

echo
echo "✓ cleanroom ready."
echo "  next: source .venv/bin/activate && python scripts/run_demo.py --size 1000"
