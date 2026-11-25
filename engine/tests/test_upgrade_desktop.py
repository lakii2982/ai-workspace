import importlib.util
import io
import json
from pathlib import Path
from urllib.error import HTTPError
import pytest

from aiws.core import Workspace
from aiws.jobs import Jobs

engine = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('employee_upgrade', engine / 'upgrade.py')
upgrade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upgrade)


@pytest.mark.parametrize('failure', ['backup', 'migration', 'startup', None])
def test_activation_preserves_workspace(tmp_path, monkeypatch, failure):
    root = tmp_path / 'core'
    workspace = tmp_path / 'Workspace'
    w = Workspace(workspace)
    w.init()
    p = w.project_create('交付')
    Jobs(w).create('任务', '接续', dict(project_ids=[p['project_id']], include_company=False,
                                    include_professional=False, topic_ids=[]))
    sha = 'a' * 40
    releases, state = upgrade.directories(root, workspace)
    state.mkdir(parents=True)
    candidate = releases / sha
    for release in (root, candidate):
        runtime = release / 'engine/.venv'
        runtime.parent.mkdir(parents=True)
        runtime.symlink_to(engine / '.venv', target_is_directory=True)
    monkeypatch.setattr(upgrade, 'resolve_channel', lambda channel=None: (sha, 'stable'))

    def prepare(*args):
        if failure == 'migration':
            raise ValueError('simulated staging migration interrupted')
        return candidate

    monkeypatch.setattr(upgrade, 'prepare', prepare)
    real_cli = upgrade.cli
    def cli(release, workspace, *args):
        if failure == 'backup' and args == ('backup',):
            raise ValueError('simulated backup failure')
        if failure == 'startup' and release == candidate:
            raise ValueError('simulated startup failure')
        return real_cli(release, workspace, *args)
    monkeypatch.setattr(upgrade, 'cli', cli)
    if failure:
        with pytest.raises(ValueError, match='simulated'):
            upgrade.upgrade(root, workspace, state)
        assert upgrade.current(root, state) == root
        assert json.loads((state / 'upgrade.json').read_text())['phase'] == 'rolled_back'
    else:
        assert upgrade.upgrade(root, workspace, state)['phase'] == 'complete'
        assert upgrade.current(root, state) == candidate
    assert real_cli(upgrade.current(root, state), workspace, 'job', 'resume',
                    '--params', '{"jid":"JOB-0001"}')['title'] == '任务'


def test_stable_channel_falls_back_to_main_before_first_release(monkeypatch):
    seen = []

    def respond(url, timeout=None):
        seen.append(url.full_url)
        if url.full_url.endswith('/commits/stable'):
            raise HTTPError(url.full_url, 404, 'Not Found', None, None)
        return io.BytesIO(json.dumps({'sha': 'b' * 40}).encode())

    monkeypatch.delenv('AIWS_CHANNEL', raising=False)
    monkeypatch.setattr(upgrade, 'urlopen', respond)
    assert upgrade.resolve_channel() == ('b' * 40, 'main')
    assert seen == [
        'https://api.github.com/repos/lakii2982/ai-workspace/commits/stable',
        'https://api.github.com/repos/lakii2982/ai-workspace/commits/main',
    ]


def test_preview_channel_reads_main(monkeypatch):
    seen = []

    def respond(url, timeout=None):
        seen.append(url.full_url)
        return io.BytesIO(json.dumps({'sha': 'c' * 40}).encode())

    monkeypatch.setenv('AIWS_CHANNEL', 'preview')
    monkeypatch.setattr(upgrade, 'urlopen', respond)
    assert upgrade.resolve_channel() == ('c' * 40, 'main')
    assert seen == ['https://api.github.com/repos/lakii2982/ai-workspace/commits/main']


def test_unknown_channel_is_rejected(monkeypatch):
    monkeypatch.setattr(upgrade, 'urlopen', lambda *args, **kwargs: pytest.fail('should not fetch'))
    with pytest.raises(ValueError, match='channel'):
        upgrade.resolve_channel('nightly')
