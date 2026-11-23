"""Per-client setup. Only write selected, workspace-bound registrations."""
import asyncio
import io
import json
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

from aiws.core import atomic

CLIENTS = ('codex', 'claude-desktop', 'workbuddy', 'doubao', 'dsh')
BUNDLES = {'com.openai.codex': 'codex', 'com.anthropic.claudefordesktop': 'claude-desktop',
           'com.tencent.workbuddy.mac': 'workbuddy', 'com.deepseek.dsh': 'dsh'}
ROUTINE = ('list_projects', 'get_project', 'list_jobs', 'resolve_job', 'resume_job',
           'resume_session', 'prepare_work_context', 'search_sources', 'read_source',
           'read_project_file', 'list_decisions', 'list_scenarios', 'read_scenario',
           'write_work_file', 'checkout_draft', 'commit_draft', 'save_work_checkpoint')


def detect():
    found = {}
    for root in (Path('/Applications'), Path.home() / 'Applications'):
        for app in root.glob('*.app'):
            try:
                info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
            except (OSError, plistlib.InvalidFileException):
                continue
            bundle = info.get('CFBundleIdentifier', '')
            client = BUNDLES.get(bundle)
            if not client and ('doubaowork' in bundle.lower() or app.stem in ('豆包办公', '豆包工作', 'DoubaoWork')):
                client = 'doubao'
            if client:
                found[client] = {'path': str(app), 'version': info.get('CFBundleShortVersionString')}
    return found


def save(path, data):
    if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != Path('/tmp')):
        raise ValueError(f'Choose a regular configuration path: {path}')
    if not path.exists() or path.read_bytes() != data:
        atomic(path, data)


def config_json(path, entry):
    config = json.loads(path.read_text()) if path.exists() else {}
    servers = config.setdefault('mcpServers', {})
    existing = servers.get('aiws', {})
    if any(k in existing and existing[k] != value for k, value in entry.items()):
        raise ValueError(f'AIWS already bound to another configuration: {path}')
    servers['aiws'] = {**existing, **entry}
    save(path, encoded(config))


def encoded(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode()


def codex_config(path, entry, legacy=False):
    original = path.read_text() if path.exists() else ''
    config = tomllib.loads(original).get('mcp_servers', {})
    existing = config.get('aiws')
    extra = []
    if existing is None:
        extra += ['[mcp_servers.aiws]', 'command = ' + json.dumps(entry['command'], ensure_ascii=False),
                  'args = ["--mcp"]', 'env = { AIWS_WORKSPACE = ' +
                  json.dumps(entry['env']['AIWS_WORKSPACE'], ensure_ascii=False) + ' }']
    elif not legacy and (existing.get('command') != entry['command'] or
                         existing.get('args') != entry['args'] or
                         existing.get('env', {}).get('AIWS_WORKSPACE') != entry['env']['AIWS_WORKSPACE']):
        raise ValueError(f'AIWS already bound to a different installation: {path}')
    for tool in ROUTINE:
        if tool not in (existing or {}).get('tools', {}):
            extra.extend([f'[mcp_servers.aiws.tools.{tool}]', 'approval_mode = "approve"'])
    if extra:
        result = original.rstrip('\n') + '\n' + '\n'.join(extra) + '\n'
        tomllib.loads(result)  # Catch inline or dotted-key table collisions before writing.
        save(path, result.encode())


def archive(path, files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for name, content in files.items():
            item = zipfile.ZipInfo(name)
            item.external_attr = (0o100755 if name == 'server/launch' else 0o100644) << 16
            bundle.writestr(item, content)
    save(path, buffer.getvalue())


def configure(client, workspace, launcher, *, activate=False):
    entry = {'command': str(launcher), 'args': ['--mcp'], 'env': {'AIWS_WORKSPACE': str(workspace)}}
    directory = workspace / '.workspace' / 'desktop' / client
    directory.mkdir(parents=True, exist_ok=True)
    rules = (launcher.parent / 'AGENTS.md').read_text()
    skill = '---\nname: aiws\ndescription: 管理本机项目、接续任务、保存成果和复用工作场景。\n---\n\n' + rules
    result = dict(client=client, status='awaiting_host_verification')
    if client == 'codex':
        # The plugin supplies instructions; there is only one MCP registration.
        plugin = workspace / 'plugins' / 'aiws'
        save(plugin / '.codex-plugin/plugin.json', encoded(dict(name='aiws', version='0.1.0',
             description='在本地工作空间接续任务、管理资料和成果', skills='./skills/')))
        save(plugin / 'skills/aiws/SKILL.md', skill.encode())
        catalog = workspace / '.agents/plugins/marketplace.json'
        market = json.loads(catalog.read_text()) if catalog.exists() else {'name': 'workspace', 'plugins': []}
        source = {'source': 'local', 'path': './plugins/aiws'}
        existing = next((p for p in market['plugins'] if p['name'] == 'aiws'), None)
        if existing and existing.get('source') != source:
            raise ValueError(f'Another aiws plugin already exists: {catalog}')
        if not existing:
            market['plugins'].append({'name': 'aiws', 'source': source, 'category': 'Productivity',
                                      'policy': {'installation': 'AVAILABLE', 'authentication': 'ON_INSTALL'}})
            save(catalog, encoded(market))
        result.update(marketplace=str(catalog), action='在 Codex 打开此工作空间，启用 AIWS')
    elif client == 'workbuddy':
        preferred = Path.home() / '.workbuddy/connectors/default/mcp.json'
        path = preferred if preferred.parent.is_dir() else Path.home() / '.workbuddy/mcp.json'
        config_json(path, {'type': 'stdio', **entry})
        result['config'] = str(path)
    elif client == 'claude-desktop':
        path = directory / 'AIWS.mcpb'
        manifest = dict(manifest_version='0.3', name='aiws', version='0.1.0', description='本地工作空间',
                        author={'name': 'AI WorkSpace'}, server=dict(type='binary', entry_point='server/launch',
                        mcp_config={'command': '${__dirname}/server/launch'}))
        script = '#!/bin/sh\nexport AIWS_WORKSPACE=' + shlex.quote(str(workspace)) + '\nexec ' + shlex.quote(str(launcher)) + ' --mcp\n'
        archive(path, {'manifest.json': encoded(manifest), 'server/launch': script})
        result.update(package=str(path), action='在 Claude Desktop 打开扩展并确认安装')
        if activate and sys.platform == 'darwin':
            subprocess.run(['open', '-b', 'com.anthropic.claudefordesktop', str(path)], check=True)
    elif client == 'doubao':
        path = directory / 'AIWS-skill.zip'
        command = shlex.join([str(launcher), '--workspace', str(workspace)])
        archive(path, {'aiws/SKILL.md': skill + '\n本机执行前缀：' + command + '\n使用 call 操作名 --params 参数。\n'})
        result.update(package=str(path), action='豆包工作 → 技能 → 上传技能，选择本 ZIP')
        if activate and sys.platform == 'darwin':
            subprocess.run(['open', '-R', str(path)], check=True)
    elif client == 'dsh':
        from aiws.mcp_server import server
        catalog = asyncio.run(server.list_tools())
        save(directory / 'package.json', encoded({'name': '@local/aiws-desktop', 'private': True,
             'version': '0.1.0', 'type': 'module', 'main': './index.mjs',
             'dsh': {'bundle': {'patch': './cordis.patch.yml'}}}))
        save(directory / 'index.mjs', (Path(__file__).parent / 'adapters/dsh.mjs').read_bytes())
        save(directory / 'cordis.patch.yml', b'- insert:\n    - id: aiws-desktop\n      name: "@local/aiws-desktop"\n')
        save(directory / 'binding.json', encoded(dict(command=str(launcher), workspace=str(workspace),
             tools=[t.model_dump(mode='json', include={'name','description','inputSchema'}) for t in catalog if t.name != 'upgrade_core'])))
        command = ['dsh', 'plugin', '--profile', 'desktop', 'add', 'link:' + str(directory)]
        result.update(package=str(directory), install_command=command)
        if activate:
            if not shutil.which('dsh'):
                raise ValueError('DeepSeek Harness desktop command is unavailable')
            subprocess.run(command, check=True, stdout=sys.stderr)
    return result
