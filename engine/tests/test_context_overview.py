from aiws.core import Workspace, put_json, digest
from aiws.jobs import Jobs
from aiws.context import Context
from aiws.timeline import Timeline


def test_overview_paginates_and_full_memory_includes_explicit_history(tmp_path):
    w=Workspace(tmp_path);w.init();p=w.project_create('概览')
    scope=dict(include_company=False,include_professional=False,project_ids=[p['project_id']],topic_ids=[])
    jobs=Jobs(w);first=jobs.create('任务','目标',scope)
    for i in range(2,104):
        item={**first,'job_id':f'JOB-{i:04}','status':'completed' if i%2==0 else 'new'}
        put_json(jobs.file(item['job_id']),item)
    t=Timeline(w);timeline=w.path(p['path']+'/记忆/TIMELINE.md')
    for i in range(105):
        t.append(p['project_id'],f'm{i}','2026-11-17','2026-11-17','decision',f'约束{i}',
                 '生效','范围','用户','继续',['测试'],digest(timeline),
                 memory=dict(category='constraint',state='established',reason='明确要求',supersedes=[]))
    ctx=Context(w)
    view=ctx.overview(p['project_id'])
    assert view['total_tasks']==103 and sum(view['counts'].values())==103
    assert len(view['tasks'])==5 and view['coverage']['omitted']['tasks']>0
    assert len(ctx.overview(p['project_id'],'full')['memory_digest']['items'])==105
    j=jobs.checkpoint(first['job_id'],1,{'context':{'memory_refs':['m0']}})
    prepared=ctx.prepare(jid=j['job_id'])
    assert len(prepared['memory']['items'])==105
    assert prepared['memory_references'][0]['event_id']=='m0'


def test_multi_project_task_does_not_guess_project(tmp_path):
    w=Workspace(tmp_path);w.init();a=w.project_create('A');b=w.project_create('B')
    j=Jobs(w).create('跨项目任务','明确选择后继续',dict(include_company=False,include_professional=False,
       project_ids=[a['project_id'],b['project_id']],topic_ids=[]))
    response=Context(w).prepare(jid=j['job_id'])
    assert response['project_candidates']==[a['project_id'],b['project_id']]


def test_overview_keeps_review_work_ahead_of_recent_history(tmp_path):
    w=Workspace(tmp_path);w.init();p=w.project_create('待审优先')
    scope=dict(include_company=False,include_professional=False,project_ids=[p['project_id']],topic_ids=[])
    jobs=Jobs(w)
    review=jobs.create('请确认报价','等待用户确认',scope)
    jobs.checkpoint(review['job_id'], review['revision'], {'status':'in_progress'})
    review=jobs.get(review['job_id'])
    jobs.checkpoint(review['job_id'], review['revision'], {'status':'review'})
    seed=jobs.get(review['job_id'])
    for number in range(2, 14):
        put_json(jobs.file(f'JOB-{number:04}'), {**seed, 'job_id':f'JOB-{number:04}',
                 'title':f'历史{number}', 'status':'completed',
                 'updated_at':f'2026-12-{number:02}T00:00:00+00:00'})
    view=Context(w).overview(p['project_id'])
    assert view['tasks'][0]['job_id'] == review['job_id']
    assert view['tasks'][0]['status'] == 'review'
