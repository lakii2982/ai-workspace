#!/bin/sh
# Install the checkout into a throwaway workspace and verify the result.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
stage=$(mktemp -d "${TMPDIR:-/tmp}/aiws-verify.XXXXXX")
trap 'rm -rf "$stage"' EXIT INT TERM
workspace="$stage/Workspace"

"$root/install.sh" "$workspace" --agent cli,claude >"$stage/install.log" 2>&1
grep -q '"doctor": "healthy"' "$stage/install.log"
grep -q '"mcp": "verified"' "$stage/install.log"
"$root/aiws" --workspace "$workspace" doctor >"$stage/doctor.json"

"$root/engine/.venv/bin/python" - "$stage" "$workspace" <<'PY'
import json
import sys
from pathlib import Path

stage, workspace = Path(sys.argv[1]), Path(sys.argv[2])
doctor = json.loads((stage / "doctor.json").read_text())
assert doctor["ok"] is True and doctor["result"]["healthy"] is True, doctor
rules = (workspace / "AGENTS.md").read_text()
assert "AIWS 项目" in rules, "workspace AGENTS.md does not contain the product rules"
assert "AI WorkSpace 开发规范" not in rules, "workspace AGENTS.md received the repository contract"
assert (workspace / "CLAUDE.md").read_text().strip() == "@AGENTS.md"
assert (workspace / ".mcp.json").is_file()
print("install smoke: ok")
PY
