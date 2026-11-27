import json

from aiws.context import Context
from aiws.core import Workspace, digest
from aiws.jobs import Jobs
from aiws.service import Service
from aiws.timeline import Timeline


def setup(tmp_path):
    w=Workspace(tmp_path);w.init();p=w.project_create('预算')
    scope=dict(include_company=False,include_professional=False,project_ids=[p['project_id']],topic_ids=[])
    j=Jobs(w).create('继续','控制默认上下文',scope)
    return w,p,j


def test_resume_budgets_long_memory_but_keeps_counts_and_progress(tmp_path):
    w,p,j=setup(tmp_path);timeline=w.path(p['path']+'/记忆/TIMELINE.md')
    t=Timeline(w)
    for number in range(30):
        t.append(p['project_id'],f'e{number}','2026-11-27','2026-11-27','decision',f'约束{number}',
                 '生效','范围','用户','继续',['用户'],digest(timeline),
                 memory=dict(category='constraint',state='established',reason='必须保留',supersedes=[]))
    draft=w.path(p['path']+'/工作/'+j['directory_name']+'/工作稿/主稿.md')
    draft.parent.mkdir(parents=True);draft.write_text('人工主稿')
    j=Jobs(w).checkpoint(j['job_id'],j['revision'],{'context':{'working_on':'第三章',
        'working_files':[{'path':draft.relative_to(w.path(p['path'])).as_posix(),'role':'主稿'}]},
        'next_steps':['补充引用'],'open_issues':['等待审核']})
    result=Service(tmp_path).call('resume_job',{'jid':j['job_id']})['work_context']
    assert result['primary_draft']['state']=='not_checked'
    assert result['progress']['working_on']=='第三章'
    assert result['attention']['unresolved_issue_total']==1
    assert len(result['memory']['items'])==Context.RESUME_MEMORY_ITEMS
    assert result['memory']['resume_budget']['totals']['items']==30
    assert result['memory']['resume_budget']['omitted']['items']==18
    assert result['memory']['resume_budget']['expand']['detail']=='full'


def test_overview_scans_jobs_once_without_query_pagination(tmp_path, monkeypatch):
    w,p,j=setup(tmp_path)
    original=Jobs.scan;calls=[]
    def tracked(self):
        calls.append(True)
        return original(self)
    monkeypatch.setattr(Jobs,'scan',tracked)
    view=Context(w).overview(p['project_id'])
    assert view['total_tasks']==1
    assert calls==[True]


def test_default_resume_serializes_under_mcp_budget_and_full_expands(tmp_path):
    w,p,j=setup(tmp_path);timeline=w.path(p['path']+'/记忆/TIMELINE.md');t=Timeline(w)
    for number in range(100):
        t.append(p['project_id'],f'long{number}','2026-11-27','2026-11-27','decision',
                 '约束'+str(number),'生效','范围','用户','继续',['用户'],digest(timeline),
                 memory=dict(category='constraint',state='established',reason='长记忆'*20,supersedes=[]))
    j=Jobs(w).checkpoint(j['job_id'],j['revision'],
        {'next_steps':['下一步'+str(i) for i in range(40)],
         'open_issues':['待审'+str(i) for i in range(40)]})
    service=Service(tmp_path)
    brief=service.call('resume_job',{'jid':j['job_id']})
    assert len(json.dumps({'ok':True,'result':brief},ensure_ascii=False).encode()) <= 16*1024
    full=service.call('resume_job',{'jid':j['job_id'],'detail':'full'})
    assert len(full['work_context']['memory']['items'])==100
    assert len(full['work_context']['task_context']['next_steps'])==40


def test_extreme_brief_keeps_blockers_draft_and_publication_identity(tmp_path):
    w,p,j=setup(tmp_path);service=Service(tmp_path)
    draft=w.path(p['path']+'/工作/'+j['directory_name']+'/工作稿/主稿.md')
    draft.parent.mkdir(parents=True);draft.write_text('主稿')
    huge='中文内容'*1200
    j=Jobs(w).checkpoint(j['job_id'],j['revision'],{'goal':huge,'context':{
        'working_files':[{'path':draft.relative_to(w.path(p['path'])).as_posix(),'role':'主稿'}],
        'resume_note':huge},'next_steps':[huge]*20,'open_issues':[huge]*20,
        'decisions':[huge]*20})
    decision=service.call('request_publication',{'jid':j['job_id'],'project_id':p['project_id'],
        'name':'主稿.md','target_name':'成果.md','reason':huge})
    result=service.call('resume_job',{'jid':j['job_id']})
    assert len(json.dumps({'ok':True,'result':result},ensure_ascii=False).encode()) <= 16*1024
    assert result['work_context']['primary_draft']['path'].endswith('主稿.md')
    assert result['work_context']['attention']['unresolved_issue_total']==20
    item=result['publication_decisions'][p['project_id']]['items'][0]
    assert item['decision_id']==decision['decision_id'] and item['state']=='pending'
    assert result['brief_budget']['expand']['detail']=='full'
