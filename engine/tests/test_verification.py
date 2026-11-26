import pytest

from aiws.core import Error, Workspace
from aiws.jobs import Jobs
from aiws.service import Service


def _job(tmp_path, acceptance=('验收',)):
    w = Workspace(tmp_path); w.init()
    p = w.project_create('验收')
    scope = dict(include_company=False, include_professional=False,
                 project_ids=[p['project_id']], topic_ids=[])
    return w, p, Jobs(w).create('任务', '目标', scope, list(acceptance))


def _step(s, w, jid, patch):
    return s.call('checkpoint_job', dict(jid=jid, revision=Jobs(w).get(jid)['revision'], patch=patch))


def _review(s, w, jid):
    _step(s, w, jid, {'status': 'in_progress'})
    _step(s, w, jid, {'status': 'review'})


def test_local_completion_requires_verification(tmp_path):
    w, p, j = _job(tmp_path); s = Service(tmp_path)
    _review(s, w, j['job_id'])
    with pytest.raises(Error) as error:
        _step(s, w, j['job_id'], {'status': 'completed'})
    assert error.value.code == 'ACCEPTANCE_REQUIRED'
    assert Jobs(w).get(j['job_id'])['status'] == 'review'


def test_verification_allows_fileless_criterion(tmp_path):
    w, p, j = _job(tmp_path); s = Service(tmp_path)
    _review(s, w, j['job_id'])
    done = _step(s, w, j['job_id'], {'status': 'completed', 'verification': [dict(criterion='验收')]})
    assert done['status'] == 'completed'
    assert Jobs(w).get(j['job_id'])['verification'] == [dict(criterion='验收')]


def test_verification_shape_checked_before_completion(tmp_path):
    w, p, j = _job(tmp_path); s = Service(tmp_path)
    _step(s, w, j['job_id'], {'status': 'in_progress'})
    with pytest.raises(Error) as error:
        _step(s, w, j['job_id'], {'verification': [dict(criterion='别的条件')]})
    assert error.value.code == 'ACCEPTANCE_REQUIRED'
    with pytest.raises(Error) as error:
        _step(s, w, j['job_id'], {'verification': [dict(criterion='验收', path='x')]})
    assert error.value.code == 'ACCEPTANCE_REQUIRED'
    assert 'together' in error.value.message


def test_deliverable_requires_file_evidence(tmp_path):
    w, p, j = _job(tmp_path); s = Service(tmp_path)
    _review(s, w, j['job_id'])
    draft = s.call('write_work_file', dict(jid=j['job_id'], project_id=p['project_id'],
                                           name='方案.md', content='# 定稿'))
    s.call('publish_file', dict(jid=j['job_id'], project_id=p['project_id'],
                                source=draft['path'], source_hash=draft['hash'],
                                name='方案.md', confirm=True))
    with pytest.raises(Error) as error:
        _step(s, w, j['job_id'], {'status': 'completed', 'verification': [dict(criterion='验收')]})
    assert error.value.code == 'ACCEPTANCE_REQUIRED'
    assert 'published deliverable' in error.value.message
    done = _step(s, w, j['job_id'], {'status': 'completed', 'verification': [
        dict(criterion='验收', path=draft['path'], hash=draft['hash'])]})
    assert done['status'] == 'completed'


def test_verification_hash_must_match_the_file(tmp_path):
    w, p, j = _job(tmp_path); s = Service(tmp_path)
    _review(s, w, j['job_id'])
    draft = s.call('write_work_file', dict(jid=j['job_id'], project_id=p['project_id'],
                                           name='方案.md', content='# 定稿', kind='process'))
    with pytest.raises(Error) as error:
        _step(s, w, j['job_id'], {'status': 'completed', 'verification': [
            dict(criterion='验收', path=draft['path'], hash='0' * 64)]})
    assert error.value.code == 'CONFLICT'
