import pytest

from aiws.core import Error, put_json
from aiws.service import Service


def scope(project_id):
    return dict(include_company=False, include_professional=False, project_ids=[project_id], topic_ids=[])


def test_project_conventions_task_feedback_and_cross_project_reuse(tmp_path):
    service=Service(tmp_path);service.call('init', {})
    source=service.call('create_project', {'name':'来源项目'})
    target=service.call('create_project', {'name':'目标项目'})
    source_job=service.call('create_job', dict(title='报价', goal='完成报价', scope=scope(source['project_id'])))
    target_job=service.call('create_job', dict(title='新报价', goal='完成报价', scope=scope(target['project_id'])))

    convention=service.call('save_memory_note', dict(project_id=source['project_id'], name='price-floor',
        description='来源项目的最低报价', type='project',
        content='**Why:** 本项目的价格边界已确认。\n\n**How to apply:** 不低于 100。'))
    feedback=service.call('record_task_feedback', dict(jid=source_job['job_id'], feedback='本次不展示折扣'))
    assert feedback['scope'] == 'task'
    assert service.call('resume_job', {'jid':source_job['job_id']})['decisions'] == ['本次不展示折扣']
    assert service.call('resume_job', {'jid':target_job['job_id']})['decisions'] == []
    with pytest.raises(Error, match='not found'):
        service.call('read_memory_note', dict(project_id=target['project_id'], name=convention['name']))
    deleted=service.call('delete_memory_note', dict(project_id=source['project_id'], name=convention['name'],
        expected_hash=convention['hash']))
    assert deleted['name'] == 'price-floor'
    with pytest.raises(Error, match='not found'):
        service.call('read_memory_note', dict(project_id=source['project_id'], name=convention['name']))

    scenario=service.call('save_scenario', dict(jid=source_job['job_id'], title='报价流程',
        instructions='按产品和维保分项。', reason='用户认可的做法'))
    applied=service.call('apply_scenario', dict(jid=target_job['job_id'], scenario_id=scenario['scenario_id']))
    assert applied['version'] == 1


def test_handoff_distinguishes_session_claim_from_human_acknowledgement(tmp_path):
    service=Service(tmp_path);service.call('init', {})
    project=service.call('create_project', {'name':'交接'})
    job=service.call('create_job', dict(title='复核报价', goal='独立复核', scope=scope(project['project_id'])))
    draft=service.call('write_work_file',dict(jid=job['job_id'],project_id=project['project_id'],name='报价.md',content='演练报价'))
    handoff=service.call('prepare_handoff', dict(jid=job['job_id'], note='核对总价和交期',
        requested_agent='reviewer', independent_review=True))
    assert handoff['files']==[{'project_id':project['project_id'],'path':draft['path'],'hash':draft['hash']}]
    assert handoff['handoff']['status'] == 'prepared'
    assert handoff['handoff']['fallback'] == 'wait_for_independent_reviewer'
    with pytest.raises(Error, match='not bound'):
        service.call('accept_handoff', dict(jid=job['job_id'], handoff_id=handoff['handoff_id'], session_id='other'))
    service.call('bind_session', dict(jid=job['job_id'], session_id='review-session'))
    accepted=service.call('accept_handoff', dict(jid=job['job_id'], handoff_id=handoff['handoff_id'], session_id='review-session'))
    assert accepted['status'] == 'claimed_by_bound_session' and accepted['receiving_session'] == 'review-session'
    with pytest.raises(Error, match='Explicit human'):
        service.call('acknowledge_handoff', dict(jid=job['job_id'], handoff_id=handoff['handoff_id'], note='收到了'))
    acknowledged=service.call('acknowledge_handoff', dict(jid=job['job_id'], handoff_id=handoff['handoff_id'],
        note='用户确认已收到交接', confirm=True))
    assert acknowledged['status'] == 'human_acknowledged'


def test_pending_publication_is_prioritized_and_completed_work_remains_locatable(tmp_path):
    service=Service(tmp_path);service.call('init', {})
    project=service.call('create_project', {'name':'发布确认'})
    job=service.call('create_job', dict(title='报价', goal='发版', scope=scope(project['project_id'])))
    directory=service.w.path(f'.workspace/decisions/{project["project_id"]}/{job["job_id"]}')
    for number in range(12):
        state='pending' if number == 11 else 'returned'
        put_json(directory / f'{number:032x}.json', dict(schema_version=1, decision_id=f'{number:032x}',
            jid=job['job_id'], project_id=project['project_id'], reason='确认', source='draft.md',
            source_hash='a' * 64, name='报价.docx', expected_hash=None, state=state,
            created_at=f'2026-11-27T00:00:{number:02}Z'))
    decisions=service.call('list_decisions', dict(project_id=project['project_id'], jid=job['job_id'], limit=10))
    assert decisions['items'][0]['state'] == 'pending'
    completed=service.call('checkpoint_job', dict(jid=job['job_id'], revision=job['revision'],
        patch={'status':'in_progress'}))
    service.call('checkpoint_job', dict(jid=job['job_id'], revision=completed['revision'], patch={'status':'review'}))
    reviewed=service.call('resume_job', {'jid':job['job_id']})
    service.call('checkpoint_job', dict(jid=job['job_id'], revision=reviewed['revision'],
        patch={'status':'completed', 'acceptance':['已确认'], 'verification':[{'criterion':'已确认'}]}))
    found=service.call('list_jobs', dict(project_id=project['project_id'], status='completed'))
    assert [item['job_id'] for item in found['items']] == [job['job_id']]
