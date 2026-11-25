import pytest

from aiws.core import Error, Workspace, put_json
from aiws.jobs import Jobs
from aiws.service import Service


def _workspace(tmp_path):
    w = Workspace(tmp_path); w.init()
    p = w.project_create('容错')
    scope = dict(include_company=False, include_professional=False,
                 project_ids=[p['project_id']], topic_ids=[])
    j = Jobs(w).create('任务', '目标', scope, ['验收'])
    return w, p, j


def _corrupt(w):
    folder = w.path('.workspace/jobs/JOB-0099')
    folder.mkdir(parents=True, exist_ok=True)
    put_json(folder / 'job.json', {'schema_version': 1, 'job_id': 'JOB-0099'})


def test_doctor_reports_corrupt_job_instead_of_raising(tmp_path):
    w, p, j = _workspace(tmp_path); _corrupt(w)
    result = Service(tmp_path).call('doctor', {})
    assert result['healthy'] is False
    assert result['jobs'] == 1
    assert [i['job_id'] for i in result['issues']] == ['JOB-0099']
    assert result['issues'][0]['code'] == 'INVALID_DATA'
    assert result['issues'][0]['path'] == '.workspace/jobs/JOB-0099/job.json'


def test_list_jobs_skips_corrupt_record_and_reports_it(tmp_path):
    w, p, j = _workspace(tmp_path); _corrupt(w)
    page = Service(tmp_path).call('list_jobs', {'status': None})
    assert [row['job_id'] for row in page['items']] == [j['job_id']]
    assert page['skipped'] == [{'job_id': 'JOB-0099', 'code': 'INVALID_DATA'}]


def test_validate_missing_field_names_the_field(tmp_path):
    w, p, j = _workspace(tmp_path)
    with pytest.raises(Error) as error:
        Jobs(w).validate({'schema_version': 1, 'job_id': 'JOB-0001'})
    assert error.value.code == 'INVALID_DATA'
    assert 'Missing job fields' in error.value.message
    assert 'scope' in error.value.message
