"""End-to-end context contracts; actual Agent behavior is accepted separately."""
import pytest
from aiws.core import Workspace, Error
from aiws.service import Service
from aiws.jobs import Jobs
from aiws.git_store import GitStore
from aiws.maintenance import Maintenance


def fixture(tmp_path):
    w=Workspace(tmp_path/'workspace'); w.init()
    p=w.project_create('接续 示例')
    scope=dict(include_company=False,include_professional=False,project_ids=[p['project_id']],topic_ids=[])
    j=Jobs(w).create('完善方案','继续第三章',scope)
    root=w.path(p['path'])
    (root/'资料'/'补充要求.txt').write_text('必须提供验收依据')
    draft=root/'工作'/j['job_id']/'主稿.md'; draft.parent.mkdir(parents=True)
    draft.write_text('第一章：需求\n第三章：待完善')
    GitStore(w,p['project_id']).initialize()
    return w,p,j,draft


def test_active_context_manual_changes_and_read_tracking(tmp_path):
    w,p,j,draft=fixture(tmp_path); s=Service(w.root)
    prepared=s.call('prepare_work_context',dict(jid=j['job_id']))
    oid=prepared['observation']['commit']
    paths=[r['path'] for r in prepared['reading_queue']]
    draft_path=draft.relative_to(w.path(p['path'])).as_posix()
    assert draft_path in paths and '资料/补充要求.txt' in paths
    s.call('read_project_file',dict(project_id=p['project_id'],path=draft_path,commit=oid))
    s.call('mark_context_read',dict(jid=j['job_id'],observation_commit=oid,paths=[draft_path]))
    j=Jobs(w).get(j['job_id'])
    s.call('save_work_checkpoint',dict(jid=j['job_id'],revision=j['revision'],
           context_patch=dict(working_on='第三章',working_files=[dict(path=draft_path,role='主稿')]),
           patch=dict(status='in_progress',next_steps=['补充选型依据'])))
    context=s.call('resume_job',dict(jid=j['job_id']))['work_context']
    assert context['task_context']['context']['working_on']=='第三章'
    assert draft_path not in [x['path'] for x in context['reading_queue']]
    assert '资料/补充要求.txt' in [x['path'] for x in context['reading_queue']]
    draft.write_text('第一章：用户修改需求\n第三章：待完善')
    (w.path(p['path'])/'资料'/'刚加入.txt').write_text('新验收要求')
    context=s.call('resume_job',dict(jid=j['job_id']))['work_context']
    assert {draft_path,'资料/刚加入.txt'} <= {x['path'] for x in context['reading_queue']}
    assert draft_path in [x['path'] for x in context['git_changes']]
    with pytest.raises(Error):
        s.call('mark_context_read',dict(jid=j['job_id'],observation_commit=oid,paths=['不存在.md']))
    # Session recovery uses the same context entry, rather than a separate summary.
    s.call('bind_session',dict(session_id='new-session',jid=j['job_id']))
    assert s.call('resume_session',dict(session_id='new-session'))['work_context']['identity']['jid']==j['job_id']


def test_backup_restores_git_versions_and_task_context(tmp_path):
    w,p,j,draft=fixture(tmp_path); s=Service(w.root)
    prepared=s.call('prepare_work_context',dict(jid=j['job_id']))
    oid=prepared['observation']['commit']
    path=draft.relative_to(w.path(p['path'])).as_posix()
    s.call('mark_context_read',dict(jid=j['job_id'],observation_commit=oid,paths=[path]))
    j=Jobs(w).get(j['job_id'])
    s.call('save_work_checkpoint',dict(jid=j['job_id'],revision=j['revision'],context_patch=dict(working_on='第三章')))
    baseline=Jobs(w).get(j['job_id'])['context']['checkpoint_commit']
    draft.write_text('用户最新修改')
    archive=Maintenance(w).backup()['path']
    restored=tmp_path/'restored'
    Maintenance(w).restore(archive,str(restored))
    rw=Workspace(restored)
    assert Jobs(rw).get(j['job_id'])['context']['checkpoint_commit']==baseline
    assert GitStore(rw,p['project_id']).read_version(baseline,path).decode().endswith('待完善')
    resumed=Service(restored).call('resume_job',dict(jid=j['job_id']))
    assert path in [x['path'] for x in resumed['work_context']['reading_queue']]
