#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"

git config core.hooksPath .githooks
chmod +x .githooks/* scripts/*.sh scripts/*.py 2>/dev/null || true

if [ ! -x engine/.venv/bin/python ]; then
    echo "No engine/.venv yet. Run ./install.sh <workspace> --agent cli first." >&2
    exit 1
fi

engine/.venv/bin/python -m pytest --version >/dev/null
echo "Hooks installed. Read CONTRIBUTING.md; run 'make check' before pushing."
