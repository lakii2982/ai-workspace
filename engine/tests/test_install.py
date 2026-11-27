import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tomllib

from install_workspace import PREVIOUS_RULES


def test_install_keeps_core_and_workspace_separate(tmp_path):
    checkout = Path(__file__).resolve().parents[2]
    root = tmp_path / "安装 根目录"
    core = root / "aiws_core"
    runtime = core / "engine" / ".venv" / "bin"
    runtime.mkdir(parents=True)
    for path in ("aiws", "install.sh", "WORKSPACE.md", "engine/workspace_rules.md", "engine/install_workspace.py", "engine/desktop_install.py", "engine/upgrade.py"):
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
    assert install["clients"][0]["status"] == "awaiting_host_verification"
    assert install["clients"][0]["session_directories"] == [str(default), str(root)]
    root_market = json.loads((root / ".agents/plugins/marketplace.json").read_text())
    assert root_market["plugins"] == [{"name": "aiws", "source": {"source": "local", "path": "./Workspace/plugins/aiws"}, "category": "Productivity", "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}}]
    config = tomllib.loads((default / ".codex" / "config.toml").read_text())
    server = {
        "command": str(core / "aiws"), "args": ["--mcp"], "env": {"AIWS_WORKSPACE": str(default)},
    }
    registered = config["mcp_servers"]["aiws"].copy()
    approvals = registered.pop("tools")
    for tool in ("resume_job", "create_project", "create_job", "bind_session",
                 "append_timeline", "save_artifact", "save_work_checkpoint"):
        assert approvals[tool]["approval_mode"] == "approve"
    assert {"publish_file", "resolve_publication", "upgrade_core"}.isdisjoint(approvals)
    assert registered == {**server, "required": True}
    root_config = root / ".codex" / "config.toml"
    assert tomllib.loads(root_config.read_text())["mcp_servers"]["aiws"] == {**registered, "tools": approvals}
    skill = (default / "plugins" / "aiws" / "skills" / "aiws" / "SKILL.md").read_text()
    assert "AIWS 项目、任务和资料操作统一通过 MCP" in skill
    assert "先调用 `open_work` 概括本次意图" in skill
    assert "用户选定项目之前，不创建任务、不写文件" in skill
    assert "记忆/条目/" in skill
    assert "记忆/条目/" in (default / "AGENTS.md").read_text()
    assert "先调用 `open_work` 概括本次意图" in (default / "AGENTS.md").read_text()
    assert not (default / "aiws").exists()
    assert not (core / ".workspace").exists()

    notes = default / "START.md"
    notes.write_text("Existing user notes\n")
    rules = default / "AGENTS.md"
    rules.write_text("Existing custom rules\n")
    config_path = default / ".codex" / "config.toml"
    original = config_path.read_text()
    config_path.write_text(original.replace("required = true\n", "").split("[mcp_servers.aiws.tools.", 1)[0] + """
[mcp_servers.aiws.tools.resume_job]
approval_mode = "prompt"

[mcp_servers.other]
command = "existing"
""")
    root_config.unlink()
    run("aiws", "project", "create", "--name", "默认数据")
    run("install.sh", "--agent", "codex")
    assert notes.read_text() == "Existing user notes\n"
    assert rules.read_text() == "Existing custom rules\n"
    upgraded = tomllib.loads(config_path.read_text())["mcp_servers"]
    assert upgraded["other"] == {"command": "existing"}
    assert upgraded["aiws"]["tools"]["resume_job"]["approval_mode"] == "prompt"
    assert upgraded["aiws"]["tools"]["append_timeline"]["approval_mode"] == "approve"
    assert upgraded["aiws"]["tools"]["open_work"]["approval_mode"] == "approve"
    assert upgraded["aiws"]["tools"]["receive_source"]["approval_mode"] == "approve"
    assert upgraded["aiws"]["tools"]["read_sent_version"]["approval_mode"] == "approve"
    assert upgraded["aiws"]["required"] is True
    assert tomllib.loads(root_config.read_text())["mcp_servers"]["aiws"] == upgraded["aiws"]
    assert json.loads((root / ".agents/plugins/marketplace.json").read_text()) == root_market

    custom = root / "另外一个工作空间"
    install = run("install.sh", str(custom), "--agent", "claude")
    assert install["workspace"] == str(custom)
    config = json.loads((custom / ".mcp.json").read_text())
    server["env"] = {"AIWS_WORKSPACE": str(custom)}
    assert config["mcpServers"]["aiws"] == {"type": "stdio", **server}
    assert len(run("aiws", "project", "list")["result"]["items"]) == 1
    assert run("aiws", "--workspace", str(custom), "project", "list")["result"]["items"] == []


def test_known_workspace_rules_version_is_eligible_for_upgrade():
    assert "5c15bbe2d0d763e6706e0c5d0ab6c0717fefd1fcec3076b7e647c60565348e24" in PREVIOUS_RULES
    assert "441c79f9f7cbb9c2ea5acee97b5c4cbaa35820ccd2d53160afb084f5a091fbdc" in PREVIOUS_RULES
    assert "f7fe38b67f8f2133e25f9b9ab39ca8987e5f38aabd29742de9d23084f6874832" in PREVIOUS_RULES
