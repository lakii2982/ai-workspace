import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tomllib


def test_install_keeps_core_and_workspace_separate(tmp_path):
    checkout = Path(__file__).resolve().parents[2]
    root = tmp_path / "安装 根目录"
    core = root / "aiws_core"
    runtime = core / "engine" / ".venv" / "bin"
    runtime.mkdir(parents=True)
    for path in ("aiws", "install.sh", "AGENTS.md", "WORKSPACE.md", "engine/install_workspace.py"):
        shutil.copy2(checkout / path, core / path)

    # Reuse the test interpreter while exercising the real installer and launchers.
    python = runtime / "python"
    python.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n')
    python.chmod(0o755)
    for name, module in (("aiws", "aiws"), ("aiws-mcp", "aiws.mcp_server_entry")):
        script = runtime / name
        script.write_text(
            "#!/bin/sh\n"
            f"export PYTHONPATH={shlex.quote(str(checkout / 'engine'))}\n"
            f'exec {shlex.quote(sys.executable)} -m {module} "$@"\n',
        )
        script.chmod(0o755)
    uv = runtime / "uv"
    uv.write_text('#!/bin/sh\ntest "$1" = pip\n')
    uv.chmod(0o755)
    env = {**os.environ, "PATH": f"{runtime}:{os.environ['PATH']}"}
    env.pop("AIWS_WORKSPACE", None)

    def run(script, *args):
        result = subprocess.run([core / script, *args], cwd=tmp_path, env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    default = root / "Workspace"
    install = run("install.sh", "--agent", "codex")
    assert install["core"] == str(core)
    assert install["workspace"] == str(default)
    assert install["doctor"] == "healthy" and install["mcp"] == "verified"
    config = tomllib.loads((default / ".codex" / "config.toml").read_text())
    server = {
        "command": str(core / "aiws"), "args": ["--mcp"], "env": {"AIWS_WORKSPACE": str(default)},
    }
    assert config["mcp_servers"]["aiws"] == server
    assert not (default / "aiws").exists()
    assert not (core / ".workspace").exists()

    notes = default / "START.md"
    notes.write_text("Existing user notes\n")
    run("aiws", "project", "create", "--name", "默认数据")
    run("install.sh", "--agent", "codex")
    assert notes.read_text() == "Existing user notes\n"

    custom = root / "另外一个工作空间"
    install = run("install.sh", str(custom), "--agent", "claude")
    assert install["workspace"] == str(custom)
    config = json.loads((custom / ".mcp.json").read_text())
    server["env"] = {"AIWS_WORKSPACE": str(custom)}
    assert config["mcpServers"]["aiws"] == {"type": "stdio", **server}
    assert len(run("aiws", "project", "list")["result"]["items"]) == 1
    assert run("aiws", "--workspace", str(custom), "project", "list")["result"]["items"] == []
