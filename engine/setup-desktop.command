#!/bin/sh
set -eu
core=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
[ "$(uname -s)" = Darwin ] || { echo "Use install.sh on Linux."; exit 1; }
selection=$(osascript -l JavaScript "$core/engine/adapters/select-clients.js" "$core/../Workspace")
[ -n "$selection" ] || exit 0
clients=$(printf '%s\n' "$selection" | head -n 1)
workspace=$(printf '%s\n' "$selection" | tail -n +2)
if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
    case "$(uname -m)" in
        arm64) target=aarch64-apple-darwin ;;
        x86_64) target=x86_64-apple-darwin ;;
        *) exit 1 ;;
    esac
    stage=$(mktemp -d)
    trap 'rm -rf "$stage"' EXIT
    asset="uv-$target.tar.gz"
    endpoint=https://github.com/astral-sh/uv/releases/download/0.8.22
    curl -fsSL "$endpoint/$asset" -o "$stage/$asset"
    curl -fsSL "$endpoint/$asset.sha256" -o "$stage/checksum"
    (cd "$stage" && shasum -a 256 -c checksum && tar -xzf "$asset")
    mkdir -p "$HOME/.local/bin"
    install -m 755 "$stage/uv-$target/uv" "$HOME/.local/bin/uv"
fi
exec "$core/install.sh" "$workspace" --agent "$clients"
