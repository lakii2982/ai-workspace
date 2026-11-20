import json
import subprocess
import sys
import pytest
from aiws.core import Workspace, Error
from aiws.jobs import Jobs

def test_job_checkpoint_new_process(tmp_path):
    w=Workspace(tmp_path);w.init();p=w.project_create('任务')
    s={'include_company':False,'include_professional':False,'project_ids':[p['project_id']],'topic_ids':[]}
    service=Jobs(w);j=service.create('方案','交付方案',s,['有出处'])
    j=service.checkpoint(j['job_id'],1,{'status':'in_progress','next_steps':['写第二稿']})
    code='from aiws.core import Workspace;from aiws.jobs import Jobs;import json;print(json.dumps(Jobs(Workspace('+repr(str(tmp_path))+')).resume('+repr(j['job_id'])+')))'
    r=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
    assert r.returncode==0 and json.loads(r.stdout)['next_steps']==['写第二稿']
    with pytest.raises(Error) as e:service.checkpoint(j['job_id'],1,{})
    assert e.value.code=='CONFLICT'
    j=service.checkpoint(j['job_id'],2,{'status':'review'})
    j=service.checkpoint(j['job_id'],3,{'status':'completed'})
    with pytest.raises(Error):service.checkpoint(j['job_id'],4,{'status':'in_progress'})

def _job_with_scope(w):
    p=w.project_create('交付')
    scope={'include_company':False,'include_professional':False,'project_ids':[p['project_id']],'topic_ids':[]}
    return p,Jobs(w).create('方案','交付方案',scope,['有出处'])

def _step(s,w,jid,patch):
    return s.call('checkpoint_job',dict(jid=jid,revision=Jobs(w).get(jid)['revision'],patch=patch))

def test_completion_blocked_while_draft_not_promoted(tmp_path):
    from aiws.service import Service
    w=Workspace(tmp_path);w.init();p,j=_job_with_scope(w);s=Service(tmp_path)
    _step(s,w,j['job_id'],{'status':'in_progress'})
    draft=s.call('write_work_file',dict(jid=j['job_id'],project_id=p['project_id'],name='方案_v1.0.md',content='# 定稿'))
    _step(s,w,j['job_id'],{'status':'review'})
    with pytest.raises(Error) as e:_step(s,w,j['job_id'],{'status':'completed'})
    assert e.value.code=='DELIVERABLE_REQUIRED'
    s.call('publish_file',dict(jid=j['job_id'],project_id=p['project_id'],source=draft['path'],source_hash=draft['hash'],name='方案_v1.0.md',confirm=True))
    done=_step(s,w,j['job_id'],{'status':'completed'})
    assert done['status']=='completed' and (tmp_path/p['path']/'成果'/j['directory_name']/'方案_v1.0.md').exists()

def test_process_material_or_no_file_needs_no_deliverable(tmp_path):
    from aiws.service import Service
    w=Workspace(tmp_path);w.init();p,j=_job_with_scope(w);s=Service(tmp_path)
    _step(s,w,j['job_id'],{'status':'in_progress'})
    s.call('write_work_file',dict(jid=j['job_id'],project_id=p['project_id'],name='证据.json',content='{}',kind='process'))
    _step(s,w,j['job_id'],{'status':'review'})
    done=_step(s,w,j['job_id'],{'status':'completed'})
    assert done['status']=='completed'

def test_empty_draft_folder_needs_no_deliverable(tmp_path):
    w=Workspace(tmp_path);w.init();p,j=_job_with_scope(w);s=None
    from aiws.service import Service
    s=Service(tmp_path)
    _step(s,w,j['job_id'],{'status':'in_progress'})
    _step(s,w,j['job_id'],{'status':'review'})
    assert _step(s,w,j['job_id'],{'status':'completed'})['status']=='completed'
