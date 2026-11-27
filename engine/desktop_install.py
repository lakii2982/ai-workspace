"""Per-client setup. Only write selected, workspace-bound registrations."""
import asyncio
import io
import json
import plistlib
import re
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
ROUTINE = (
    'list_projects', 'get_project', 'create_project', 'update_project',
    'get_project_overview', 'list_jobs', 'resolve_job', 'create_job', 'resume_job',
    'claim_job', 'renew_claim', 'release_job', 'bind_session', 'resume_session',
    'prepare_work_context', 'search_sources', 'read_source', 'read_project_file',
    'compare_project_file', 'list_project_memory', 'read_project_memory',
    'mark_context_read', 'append_timeline', 'checkpoint_job', 'save_work_checkpoint',
    'write_work_file', 'save_artifact', 'checkout_draft', 'commit_draft',
    'request_publication', 'list_decisions', 'list_scenarios', 'read_scenario',
    'apply_scenario',
)


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


def codex_config(path, entry, legacy=False, tool_defaults=None):
    original = path.read_text() if path.exists() else ''
    config = tomllib.loads(original).get('mcp_servers', {})
    existing = config.get('aiws')
    extra = []
    if existing is None:
        extra += ['[mcp_servers.aiws]', 'command = ' + json.dumps(entry['command'], ensure_ascii=False),
                  'args = ["--mcp"]', 'required = true', 'env = { AIWS_WORKSPACE = ' +
                  json.dumps(entry['env']['AIWS_WORKSPACE'], ensure_ascii=False) + ' }']
    elif not legacy and (existing.get('command') != entry['command'] or
                         existing.get('args') != entry['args'] or
                         existing.get('env', {}).get('AIWS_WORKSPACE') != entry['env']['AIWS_WORKSPACE']):
        raise ValueError(f'AIWS already bound to a different installation: {path}')
    if existing is not None:
        if (existing.get('enabled') is False or existing.get('required') is False
                or existing.get('startup_readiness', 'connection') != 'connection'):
            raise ValueError(f'AIWS must be enabled and required with live startup readiness: {path}')
        if 'required' not in existing:
            header = re.search(r'(?m)^\s*\[mcp_servers\.aiws\][ \t]*(?:#[^\n]*)?\n', original)
            if header is None:
                raise ValueError(f'Use a [mcp_servers.aiws] section to set required = true: {path}')
            original = original[:header.end()] + 'required = true\n' + original[header.end():]
    modes = dict.fromkeys(ROUTINE, 'approve')
    modes.update(tool_defaults or {})
    for tool, mode in modes.items():
        if tool not in (existing or {}).get('tools', {}):
            extra.extend([f'[mcp_servers.aiws.tools.{json.dumps(tool)}]', f'approval_mode = {json.dumps(mode)}'])
    result = original
    if extra:
        result = (original.rstrip('\n') + '\n' if original else '') + '\n'.join(extra) + '\n'
    config = tomllib.loads(result)['mcp_servers']['aiws']
    if config.get('required') is not True:
        raise ValueError(f'Unable to set required = true in [mcp_servers.aiws]: {path}')
    save(path, result.encode())
    return config


def archive(path, files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for name, content in files.items():
            item = zipfile.ZipInfo(name)
            item.external_attr = (0o100755 if name == 'server/launch' else 0o100644) << 16
            bundle.writestr(item, content)
    save(path, buffer.getvalue())


def configure(client, workspace, launcher, *, activate=False, source=None):
    entry = {'command': str(launcher), 'args': ['--mcp'], 'env': {'AIWS_WORKSPACE': str(workspace)}}
    directory = workspace / '.workspace' / 'desktop' / client
    rules = ((source or launcher.parent) / 'engine/workspace_rules.md').read_text()
    skill = '---\nname: aiws\ndescription: 管理本机项目、接续任务、保存成果和复用工作场景。\n---\n\n' + rules
    result = dict(client=client, status='awaiting_host_verification')
    if client == 'codex':
        # The plugin supplies instructions; there is only one MCP registration.
        plugin = workspace / 'plugins' / 'aiws'
        save(plugin / '.codex-plugin/plugin.json', encoded(dict(name='aiws', version='0.1.3',
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
        result.update(marketplace=str(catalog),
                      action='信任实际打开的目录，新建 Codex 会话并运行 verify_desktop_binding 核对数据目录')
    elif client == 'workbuddy':
        path = Path.home() / '.workbuddy/mcp.json'
        package = directory / 'aiws-mcp.json'
        save(package, encoded({'mcpServers': {'aiws': {'type': 'stdio', **entry}}}))
        config_json(path, {'type': 'stdio', **entry})
        result.update(config=str(path), package=str(package),
                      action='在 WorkBuddy 自定义连接器中确认 AIWS')
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
             tools=[t.model_dump(mode='json', include={'name','description','inputSchema'}) for t in catalog if t.name not in ('upgrade_core', 'verify_desktop_binding')])))
        command = ['dsh', 'plugin', '--profile', 'desktop', 'add', 'link:' + str(directory)]
        result.update(package=str(directory), install_command=command)
        if activate:
            if not shutil.which('dsh'):
                raise ValueError('DeepSeek Harness desktop command is unavailable')
            install = subprocess.run(command, capture_output=True, text=True)
            if install.returncode:
                detail = (install.stderr or install.stdout).strip().splitlines()
                result.update(status='blocked', detail=detail[-1] if detail else 'DSH plugin installation failed',
                              action='首次打开 DSH Desktop 初始化；保存任务，完全退出后重新运行本安装入口。')
    return result
