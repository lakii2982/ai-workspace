import pytest

from aiws.core import Error
from aiws.service import Service


def scope(project_id):
    return dict(include_company=False, include_professional=False, project_ids=[project_id], topic_ids=[])


def test_project_conventions_task_feedback_and_cross_project_reuse(tmp_path):
    service=Service(tmp_path);service.call('init', {})
    source=service.call('create_project', {'name':'来源项目'})
    target=service.call('create_project', {'name':'目标项目'})
    source_job=service.call('create_job', dict(title='报价', goal='完成报价', scope=scope(source['project_id'])))
    target_job=service.call('create_job', dict(title='新报价', goal='完成报价', scope=scope(target['project_id'])))

    convention=service.call('save_memory_note', dict(project_id=source['project_id'], name='quote-style',
        description='报价按产品和维保分项', type='project',
        content='**Why:** 便于客户比价。\n\n**How to apply:** 分列产品和维保。'))
    feedback=service.call('record_task_feedback', dict(jid=source_job['job_id'], feedback='本次不展示折扣'))
    assert feedback['scope'] == 'task'
    assert service.call('resume_job', {'jid':source_job['job_id']})['decisions'] == ['本次不展示折扣']
    assert service.call('resume_job', {'jid':target_job['job_id']})['decisions'] == []
    with pytest.raises(Error, match='not found'):
        service.call('read_memory_note', dict(project_id=target['project_id'], name=convention['name']))

    scenario=service.call('save_scenario', dict(jid=source_job['job_id'], title='报价流程',
        instructions='按产品和维保分项。', reason='用户认可的做法'))
    applied=service.call('apply_scenario', dict(jid=target_job['job_id'], scenario_id=scenario['scenario_id']))
    assert applied['version'] == 1


def test_handoff_is_not_delivered_until_a_bound_session_accepts(tmp_path):
    service=Service(tmp_path);service.call('init', {})
    project=service.call('create_project', {'name':'交接'})
    job=service.call('create_job', dict(title='复核报价', goal='独立复核', scope=scope(project['project_id'])))
    handoff=service.call('prepare_handoff', dict(jid=job['job_id'], note='核对总价和交期',
        requested_agent='reviewer', independent_review=True))
    assert handoff['delivery']['status'] == 'not_delivered'
    assert handoff['delivery']['fallback'] == 'wait_for_independent_reviewer'
    with pytest.raises(Error, match='not bound'):
        service.call('accept_handoff', dict(jid=job['job_id'], handoff_id=handoff['handoff_id'], session_id='other'))
    service.call('bind_session', dict(jid=job['job_id'], session_id='review-session'))
    accepted=service.call('accept_handoff', dict(jid=job['job_id'], handoff_id=handoff['handoff_id'], session_id='review-session'))
    assert accepted['status'] == 'received' and accepted['receiving_session'] == 'review-session'
