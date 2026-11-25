import pytest

from aiws.core import Error, Workspace
from aiws.jobs import Jobs
from aiws.service import Service


def _workspace(tmp_path):
    w = Workspace(tmp_path); w.init()
    p = w.project_create('闸门')
    scope = dict(include_company=False, include_professional=False,
                 project_ids=[p['project_id']], topic_ids=[])
    j = Jobs(w).create('任务', '目标', scope, ['验收'])
    return w, p, j


def test_checkpoint_rejects_artifact_forgery(tmp_path):
    w, p, j = _workspace(tmp_path)
    forged = [dict(path=p['path'] + '/资料/伪造.md', hash='0' * 64,
                   purpose='deliverable', status='approved')]
    with pytest.raises(Error) as error:
        Service(tmp_path).call('checkpoint_job', dict(
            jid=j['job_id'], revision=1,
            patch={'status': 'in_progress', 'artifacts': forged}))
    assert error.value.code == 'INVALID_ARGUMENT'
    assert 'save_artifact' in error.value.message
    assert Jobs(w).get(j['job_id'])['artifacts'] == []


def test_checkpoint_rejects_source_forgery(tmp_path):
    w, p, j = _workspace(tmp_path)
    with pytest.raises(Error) as error:
        Service(tmp_path).call('checkpoint_job', dict(
            jid=j['job_id'], revision=1,
            patch={'sources': [dict(source_id='x', locator='y', hash='z')]}))
    assert error.value.code == 'INVALID_ARGUMENT'
    assert 'sources' in error.value.message


def test_list_projects_does_not_write_registry(tmp_path):
    w, p, j = _workspace(tmp_path)
    registry = w.path('.workspace/registry/projects.json')
    assert not registry.exists()
    Service(tmp_path).call('list_projects', {})
    assert not registry.exists()
