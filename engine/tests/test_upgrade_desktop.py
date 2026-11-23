import importlib.util
import json
from pathlib import Path
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
    monkeypatch.setattr(upgrade, 'resolve_main', lambda: sha)

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
