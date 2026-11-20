#!/bin/sh
set -eu

usage() {
    echo "Usage: ./install.sh WORKSPACE [--agent codex|claude|cli]"
}

if [ "${1:-}" = "--help" ]; then
    usage
    exit 0
fi
if [ "$#" -ne 1 ] && [ "$#" -ne 3 ]; then
    usage >&2
    exit 2
fi

workspace=$1
agent=cli
if [ "$#" -eq 3 ]; then
    [ "$2" = "--agent" ] || { usage >&2; exit 2; }
    agent=$3
fi
case "$agent" in
    codex|claude|cli) ;;
    *) usage >&2; exit 2 ;;
esac
[ -n "$workspace" ] || { usage >&2; exit 2; }

case "$(uname -s)" in
    Darwin|Linux) ;;
    *) echo "AI WorkSpace requires macOS or Linux." >&2; exit 1 ;;
esac
command -v git >/dev/null 2>&1 || { echo "Install Git and rerun this command." >&2; exit 1; }

if command -v uv >/dev/null 2>&1; then
    uv_bin=$(command -v uv)
elif [ -x "$HOME/.local/bin/uv" ]; then
    uv_bin="$HOME/.local/bin/uv"
else
    echo "Install uv from https://docs.astral.sh/uv/getting-started/installation/ and rerun." >&2
    exit 1
fi

source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
engine_dir="$source_dir/engine"
runtime="$engine_dir/.venv"
if [ ! -x "$runtime/bin/python" ]; then
    "$uv_bin" venv --allow-existing --python 3.12 "$runtime"
fi
"$uv_bin" pip install --python "$runtime/bin/python" --no-deps -r "$engine_dir/requirements.lock" -e "$engine_dir"
"$uv_bin" pip check --python "$runtime/bin/python"
exec "$runtime/bin/python" "$engine_dir/install_workspace.py" "$workspace" "$agent"
