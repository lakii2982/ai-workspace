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
