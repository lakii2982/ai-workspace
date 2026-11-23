#!/bin/sh
set -eu

usage() {
    echo "Usage: ./install.sh [WORKSPACE] [--agent codex|claude|cli]"
    echo "Default workspace: Workspace next to the source checkout."
}

if [ "${1:-}" = "--help" ]; then
    usage
    exit 0
fi
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
workspace="$source_dir/../Workspace"
agent=cli
if [ "$#" -gt 0 ] && [ "$1" != "--agent" ]; then
    [ -n "$1" ] || { usage >&2; exit 2; }
    workspace=$1
    shift
fi
if [ "$#" -ne 0 ]; then
    [ "$#" -eq 2 ] && [ "$1" = "--agent" ] || { usage >&2; exit 2; }
    agent=$2
fi
case "$agent" in
    codex|claude|cli) ;;
    *) usage >&2; exit 2 ;;
esac

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

engine_dir="$source_dir/engine"
runtime="$engine_dir/.venv"
if [ ! -x "$runtime/bin/python" ]; then
    "$uv_bin" venv --allow-existing --python 3.12 "$runtime"
fi
"$uv_bin" pip install --python "$runtime/bin/python" --no-deps -r "$engine_dir/requirements.lock" -e "$engine_dir"
"$uv_bin" pip check --python "$runtime/bin/python"
exec "$runtime/bin/python" "$engine_dir/install_workspace.py" "$workspace" "$agent"
