import subprocess

import pytest

from aiws import mcp_server
from aiws.core import Workspace
from aiws.git_store import GitStore
from aiws.jobs import Jobs
from aiws.service import Service


def test_timeout_is_reported_as_timeout(monkeypatch):
    monkeypatch.setenv('AIWS_WORKSPACE', '/tmp/aiws-timeout-probe')

    def expired(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd='aiws', timeout=1)

    monkeypatch.setattr(subprocess, 'run', expired)
    result = mcp_server.invoke('list_projects', {})
    assert result['ok'] is False
    assert result['error']['code'] == 'TIMEOUT'
    assert 'exceeded' in result['error']['message']


def test_missing_workspace_is_an_entry_error(monkeypatch):
    monkeypatch.delenv('AIWS_WORKSPACE', raising=False)
    result = mcp_server.invoke('list_projects', {})
    assert result['error']['code'] == 'ENTRY_UNAVAILABLE'


def test_operation_timeouts_differ_by_cost():
    assert mcp_server.timeout_for('list_projects') == mcp_server.READ_TIMEOUT
    assert mcp_server.timeout_for('write_work_file') == mcp_server.WRITE_TIMEOUT
    assert mcp_server.timeout_for('backup') == 600
    assert mcp_server.timeout_for('upgrade_core') == 900


def test_mark_read_lists_the_tree_once(tmp_path, monkeypatch):
    w = Workspace(tmp_path); w.init()
    p = w.project_create('阅读')
    GitStore(w, p['project_id']).initialize()
    (tmp_path / p['path'] / '资料' / 'a.md').write_text('a')
    (tmp_path / p['path'] / '资料' / 'b.md').write_text('b')
    scope = dict(include_company=False, include_professional=False,
                 project_ids=[p['project_id']], topic_ids=[])
    j = Jobs(w).create('任务', '目标', scope, ['验收'])
    observation = GitStore(w, p['project_id']).observe()
    calls = []
    original = GitStore.file_oids

    def counted(self, commit):
        calls.append(commit)
        return original(self, commit)

    monkeypatch.setattr(GitStore, 'file_oids', counted)
    Service(tmp_path).call('mark_context_read', dict(
        jid=j['job_id'], observation_commit=observation['commit'],
        paths=['资料/a.md', '资料/b.md']))
    assert calls == [observation['commit']]
    reviewed = Jobs(w).get(j['job_id'])['context']['reviewed_files']
    assert sorted(item['path'] for item in reviewed) == ['资料/a.md', '资料/b.md']


def test_mark_read_rejects_paths_outside_the_observation(tmp_path):
    w = Workspace(tmp_path); w.init()
    p = w.project_create('阅读')
    GitStore(w, p['project_id']).initialize()
    scope = dict(include_company=False, include_professional=False,
                 project_ids=[p['project_id']], topic_ids=[])
    j = Jobs(w).create('任务', '目标', scope, ['验收'])
    observation = GitStore(w, p['project_id']).observe()
    with pytest.raises(Exception) as error:
        Service(tmp_path).call('mark_context_read', dict(
            jid=j['job_id'], observation_commit=observation['commit'], paths=['资料/不存在.md']))
    assert getattr(error.value, 'code', None) == 'NOT_FOUND'
