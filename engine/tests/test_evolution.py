import json
import pytest
from aiws.core import Workspace, Error
from aiws.jobs import Jobs
from aiws.service import Service


def test_bounded_discovery_and_resume(tmp_path):
    w=Workspace(tmp_path);w.init();p=w.project_create('演进')
    scope=dict(include_company=False,include_professional=False,project_ids=[p['project_id']],topic_ids=[])
    jobs=Jobs(w);j=jobs.create('测试恢复','目标',scope)
    j=jobs.checkpoint(j['job_id'],1,dict(decisions=['约束'*1000]*100,next_steps=['继续']))
    brief=jobs.resume(j['job_id'])
    assert len(json.dumps(brief,ensure_ascii=False))<8000
    assert 'decisions' in brief['omitted']
    assert jobs.resume(j['job_id'],detail='full')['decisions']==j['decisions']
    assert (w.root/p['path']/'记忆/TIMELINE.md').exists()
    # Synthetic scale fixtures avoid 1000 expensive business writes; validate public reads.
    original=jobs.file(j['job_id']).read_text()
    for i in range(2,1002):
        path=jobs.file(f'JOB-{i:04}');path.parent.mkdir()
        data=json.loads(original);data['job_id']=f'JOB-{i:04}';data['title']=f'任务{i}'
        path.write_text(json.dumps(data))
    page=Service(tmp_path).call('list_jobs',dict(project_id=p['project_id'],limit=20))
    assert len(page['items'])==20 and page['has_more']
    assert len(json.dumps(page))<8000
    assert jobs.resume(j['job_id'])==brief
    assert jobs.query(project_id=p['project_id'],query='JOB-1001')['items'][0]['job_id']=='JOB-1001'
    with pytest.raises(Error):jobs.query(project_id='PRJ-9999')
    with pytest.raises(Error):jobs.query(limit=1001)


def test_timeline_cas_and_replay(tmp_path):
    from aiws.timeline import Timeline
    from aiws.core import digest
    w=Workspace(tmp_path);w.init();p=w.project_create('项目')
    path=w.root/p['path']/'记忆/TIMELINE.md'
    args=dict(project_id=p['project_id'],event_id='decision-1',event_date='2026-11-12',record_date='2026-11-12',kind='decision',summary='决定',status='生效',impact='范围',owner='待确认',next_action='测试',sources=['用户授权'],expected_hash=digest(path))
    t=Timeline(w);t.append(**args)
    assert t.append(**args)['replayed']
    with pytest.raises(Error):t.append(**{**args,'event_id':'decision-2'})
    with pytest.raises(Error):t.append(**{**args,'summary':'篡改'})
    assert path.read_text().count('## 2026-11-12')==1


def test_thousand_projects_bounded_context(tmp_path):
    from aiws.core import put_json
    w=Workspace(tmp_path);w.init()
    for i in range(1,1001):
        pid=f'PRJ-{i:04}'
        put_json(w.path(f'项目/{pid}/.project.json'),dict(schema_version=1,project_id=pid,name=f'项目{i}',aliases=[],status='active'))
    s=Service(tmp_path)
    page=s.call('list_projects',{})
    assert len(page['items'])==20 and page['has_more']
    assert len(json.dumps(page,ensure_ascii=False))<6000
    j=Jobs(s.w).create('单任务','固定目标',dict(include_company=False,include_professional=False,project_ids=['PRJ-0500'],topic_ids=[]))
    response=s.call('resume_job',dict(jid=j['job_id']))
    assert len(json.dumps(response,ensure_ascii=False))<2500
    assert response['directories'][0]['project_id']=='PRJ-0500'
