import pytest

from aiws.core import Error, Workspace
from aiws.jobs import Jobs
from aiws.service import Service


def _job(tmp_path):
    w = Workspace(tmp_path); w.init()
    p = w.project_create('移交')
    scope = dict(include_company=False, include_professional=False,
                 project_ids=[p['project_id']], topic_ids=[])
    j = Jobs(w).create('任务', '目标', scope, ['验收'])
    s = Service(tmp_path)
    s.call('checkpoint_job', dict(jid=j['job_id'], revision=1, patch={'status': 'in_progress'}))
    return w, p, j, s


def _write(s, j, p, name='方案.md', content='# 定稿', kind='draft'):
    return s.call('write_work_file', dict(jid=j['job_id'], project_id=p['project_id'],
                                          name=name, content=content, kind=kind))


def test_move_draft_to_process_clears_deliverable_requirement(tmp_path):
    w, p, j, s = _job(tmp_path)
    draft = _write(s, j, p)
    s.call('checkpoint_job', dict(jid=j['job_id'], revision=Jobs(w).get(j['job_id'])['revision'],
                                  patch={'status': 'review'}))
    with pytest.raises(Error) as error:
        s.call('checkpoint_job', dict(jid=j['job_id'], revision=Jobs(w).get(j['job_id'])['revision'],
                                      patch={'status': 'completed', 'verification': [dict(criterion='验收')]}))
    assert error.value.code == 'DELIVERABLE_REQUIRED'
    moved = s.call('move_work_file', dict(jid=j['job_id'], project_id=p['project_id'],
                                          source=draft['path'], target_kind='process',
                                          expected_hash=draft['hash']))
    assert moved['to'].endswith('/过程材料/方案.md')
    assert not (tmp_path / draft['path']).exists()
    assert (tmp_path / moved['to']).exists()
    assert Jobs(w).get(j['job_id'])['artifacts'][0]['path'] == moved['to']
    done = s.call('checkpoint_job', dict(jid=j['job_id'], revision=moved['revision'],
                                         patch={'status': 'completed', 'verification': [dict(criterion='验收')]}))
    assert done['status'] == 'completed'


def test_move_rejects_when_checkout_is_open(tmp_path):
    w, p, j, s = _job(tmp_path)
    draft = _write(s, j, p)
    s.call('checkout_draft', dict(jid=j['job_id'], project_id=p['project_id'], name='方案.md'))
    with pytest.raises(Error) as error:
        s.call('move_work_file', dict(jid=j['job_id'], project_id=p['project_id'],
                                      source=draft['path'], target_kind='process',
                                      expected_hash=draft['hash']))
    assert error.value.code == 'INVALID_STATE'


def test_move_rejects_when_publication_is_pending(tmp_path):
    w, p, j, s = _job(tmp_path)
    draft = _write(s, j, p)
    s.call('request_publication', dict(jid=j['job_id'], project_id=p['project_id'], name='方案.md',
                                       target_name='方案.md', reason='准备发布'))
    with pytest.raises(Error) as error:
        s.call('move_work_file', dict(jid=j['job_id'], project_id=p['project_id'],
                                      source=draft['path'], target_kind='process',
                                      expected_hash=draft['hash']))
    assert error.value.code == 'INVALID_STATE'


def test_move_rejects_existing_target_and_stale_hash(tmp_path):
    w, p, j, s = _job(tmp_path)
    draft = _write(s, j, p)
    _write(s, j, p, name='方案.md', content='另一份', kind='process')
    with pytest.raises(Error) as error:
        s.call('move_work_file', dict(jid=j['job_id'], project_id=p['project_id'],
                                      source=draft['path'], target_kind='process',
                                      expected_hash=draft['hash']))
    assert error.value.code == 'CONFLICT'
    with pytest.raises(Error) as error:
        s.call('move_work_file', dict(jid=j['job_id'], project_id=p['project_id'],
                                      source=draft['path'], target_kind='process',
                                      name='别的.md', expected_hash='0' * 64))
    assert error.value.code == 'CONFLICT'


def test_delete_removes_the_artifact_record(tmp_path):
    w, p, j, s = _job(tmp_path)
    draft = _write(s, j, p)
    assert Jobs(w).get(j['job_id'])['artifacts']
    result = s.call('delete_work_file', dict(jid=j['job_id'], project_id=p['project_id'],
                                             relative=draft['path'], expected_hash=draft['hash']))
    assert result['deleted_hash'] == draft['hash']
    assert not (tmp_path / draft['path']).exists()
    assert Jobs(w).get(j['job_id'])['artifacts'] == []
    assert w.path(f'.workspace/history/files/{draft["hash"]}').is_file()


def test_delete_rejects_deliverable_paths(tmp_path):
    w, p, j, s = _job(tmp_path)
    draft = _write(s, j, p)
    published = s.call('publish_file', dict(jid=j['job_id'], project_id=p['project_id'],
                                            source=draft['path'], source_hash=draft['hash'],
                                            name='方案.md', confirm=True))
    with pytest.raises(Error) as error:
        s.call('delete_work_file', dict(jid=j['job_id'], project_id=p['project_id'],
                                        relative=published['path'], expected_hash=published['hash']))
    assert error.value.code == 'INVALID_SCOPE'
    assert (tmp_path / published['path']).is_file()
