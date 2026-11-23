import asyncio
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
import zipfile

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Implementation
from aiws.service import Service

engine = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('desktop_install', engine / 'desktop_install.py')
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)


def test_clients_preserve_settings_and_office_binding(tmp_path, monkeypatch):
    home = tmp_path / 'employee'
    monkeypatch.setenv('HOME', str(home))
    workspace = tmp_path / '资料 & docs'
    Service(workspace).call('init', {})
    launcher = engine.parent / 'aiws'
    entry = dict(command=str(launcher), args=['--mcp'], env={'AIWS_WORKSPACE': str(workspace)})
    config = workspace / '.codex/config.toml'
    config.parent.mkdir()
    config.write_text('[mcp_servers.other]\ncommand="existing"\n')
    desktop.codex_config(config, entry)
    before = config.read_bytes()
    desktop.codex_config(config, entry)
    assert before == config.read_bytes() and tomllib.loads(before.decode())['mcp_servers']['other']
    with pytest.raises(ValueError, match='already bound'):
        desktop.codex_config(config, {**entry, 'command': 'different'})
    path = home / '.workbuddy/connectors/default/mcp.json'
    path.parent.mkdir(parents=True)
    path.write_text('{"mcpServers":{"old":{"command":"existing"}}}')
    desktop.configure('workbuddy', workspace, launcher)
    assert set(json.loads(path.read_text())['mcpServers']) == {'aiws', 'old'}
    result = desktop.configure('claude-desktop', workspace, launcher)
    with zipfile.ZipFile(result['package']) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        server = archive.extract(manifest['server']['entry_point'], tmp_path / 'extension')
    os.chmod(server, 0o755)

    async def probe():
        async with stdio_client(StdioServerParameters(command=server)) as (r, w):
            async with ClientSession(r, w, client_info=Implementation(name='test-client',version='1')) as session:
                await session.initialize()
                receipt = await session.call_tool('verify_desktop_binding', {})
                assert not receipt.isError, receipt.content
                payload = receipt.structuredContent or json.loads(receipt.content[0].text)
                assert payload['result']['workspace'] == str(workspace)
    asyncio.run(probe())
    assert Service(workspace).call('list_host_bindings', {})['items'][0]['client'] == 'test-client'
    skill = desktop.configure('doubao', workspace, launcher)
    with zipfile.ZipFile(skill['package']) as archive:
        assert str(workspace) in archive.read('aiws/SKILL.md').decode()
    plugin = desktop.configure('dsh', workspace, launcher)
    binding = json.loads((Path(plugin['package'])/'binding.json').read_text())
    assert 'commit_draft' in {tool['name'] for tool in binding['tools']}
    if shutil.which('node'):
        run = subprocess.run(['node', '--input-type=module', '-e', """
            import {apply} from './index.mjs';
            const tools = new Map();
            apply({tools:{register(tool){tools.set(tool.name,tool)}}});
            const response=JSON.parse(await tools.get('aiws_list_jobs').execute({}, {signal:new AbortController().signal}));
            if (!response.ok) throw Error('DSH bridge did not return a job list');
        """], cwd=plugin['package'], text=True, capture_output=True)
        assert run.returncode == 0, run.stderr
