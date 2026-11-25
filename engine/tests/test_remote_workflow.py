import copy
import json
import time
import pytest
from aiws.core import Workspace, Error, put_json
from aiws.jobs import Jobs
from aiws.service import Service
from aiws.remote_store import RemoteStore
from aiws.feishu import Feishu
from aiws.maintenance import Maintenance

@pytest.fixture
def remote_workspace(tmp_path,monkeypatch):
    w=Workspace(tmp_path);w.init();p=w.project_create('演进')
    scope=dict(include_company=False,include_professional=False,project_ids=[p['project_id']],topic_ids=[])
    j=Jobs(w).create('任务','交付',scope,['内容可回读'])
    put_json(w.path('.workspace/feishu.json'),dict(schema_version=1,mode='staging',base_token='test',projects_table='projects',jobs_table='jobs'))
    rows={'projects':{},'jobs':{}}
    def read(self,entity,key=None,value=None,limit=20,offset=0,**kwargs):
        result=list(rows[entity].values())
        if key:result=[r for r in result if r.get(key)==value]
        return dict(items=copy.deepcopy(result[offset:offset+limit]),has_more=len(result)>offset+limit)
    def command(self,action,table,args):
        fields=json.loads(args[args.index('--json')+1])
        rid=args[args.index('--record-id')+1] if '--record-id' in args else 'rec'+str(len(rows[table])+1)
        rows[table][rid]={'record_id':rid,**fields}
        return dict(ok=True,data={'record':{'record_id_list':[rid]}})
    monkeypatch.setattr(Feishu,'rows',read);monkeypatch.setattr(Feishu,'command',command)
    Maintenance(w).backup()
    Service(tmp_path).call('migrate_feishu',dict(activate=True))
    return tmp_path,p,j,rows

def test_remote_authority_claim_publish_and_rollback(remote_workspace):
    root,p,j,rows=remote_workspace;s=Service(root);jid=j['job_id']
    with pytest.raises(Error,match='Claim'):s.call('checkpoint_job',dict(jid=jid,revision=1,patch={'status':'in_progress'}))
    claim=s.call('claim_job',dict(jid=jid,owner='client-A',revision=1));token=claim['lease']['token']
    with pytest.raises(Error):s.call('claim_job',dict(jid=jid,owner='client-B',revision=2))
    s.call('checkpoint_job',dict(jid=jid,revision=2,lease_token=token,patch={'status':'in_progress'}))
    with pytest.raises(Error):s.call('checkpoint_job',dict(jid=jid,revision=2,lease_token=token,patch={}))
    saved=s.call('write_work_file',dict(jid=jid,project_id=p['project_id'],name='方案.md',content='v1',lease_token=token))
    with pytest.raises(Error):s.call('publish_file',dict(jid=jid,project_id=p['project_id'],source=saved['path'],source_hash=saved['hash'],name='方案.md',lease_token=token))
    published=s.call('publish_file',dict(jid=jid,project_id=p['project_id'],source=saved['path'],source_hash=saved['hash'],name='方案.md',confirm=True,lease_token=token))
    saved2=s.call('write_work_file',dict(jid=jid,project_id=p['project_id'],name='方案.md',content='v2',expected_hash=saved['hash'],lease_token=token))
    s.call('publish_file',dict(jid=jid,project_id=p['project_id'],source=saved2['path'],source_hash=saved2['hash'],name='方案.md',expected_hash=published['hash'],confirm=True,lease_token=token))
    assert list((root/p['path']/'成果'/j['directory_name']/'历史版本').glob('*'))[0].read_text()=='v1'
    # Full authority survives local cached title tampering.
    path=Jobs(s.w).file(jid);cache=json.loads(path.read_text());cache['title']='stale';put_json(path,cache)
    assert Service(root).call('resume_job',dict(jid=jid))['title']=='任务'
    current=Jobs(s.w).get(jid)
    s.call('checkpoint_job',dict(jid=jid,revision=current['revision'],lease_token=token,patch={'status':'review'}))
    current=Jobs(s.w).get(jid)
    with pytest.raises(Error):s.call('checkpoint_job',dict(jid=jid,revision=current['revision'],lease_token=token,patch={'status':'completed'}))
    s.call('checkpoint_job',dict(jid=jid,revision=current['revision'],lease_token=token,patch={'status':'completed','verification':[dict(criterion='内容可回读',path=saved2['path'],hash=saved2['hash'])]}))
    Service(root).call('rollback_feishu',{})
    assert Service(root).call('resume_job',dict(jid=jid))['status']=='completed'

def test_remote_manual_drift_fails_closed(remote_workspace):
    root,p,j,rows=remote_workspace
    row=next(iter(rows['jobs'].values()));row['状态']=['已完成']
    with pytest.raises(Error,match='outside aiws'):Service(root).call('resume_job',dict(jid=j['job_id']))

def test_expired_token_and_session(tmp_path):
    w=Workspace(tmp_path);w.init();p=w.project_create('项目')
    j=Jobs(w).create('任务','目标',dict(include_company=False,include_professional=False,project_ids=[p['project_id']],topic_ids=[]))
    s=Service(tmp_path)
    claimed=s.call('claim_job',dict(jid=j['job_id'],owner='A',revision=1))
    token=claimed['lease']['token'];current=Jobs(w).get(j['job_id']);current['lease']['expires']=time.time()-1
    Jobs(w).persist(current)
    with pytest.raises(Error):s.call('checkpoint_job',dict(jid=j['job_id'],revision=2,patch={},lease_token=token))
    s.call('claim_job',dict(jid=j['job_id'],owner='B',revision=2))
    with pytest.raises(Error):s.call('checkpoint_job',dict(jid=j['job_id'],revision=3,patch={},lease_token=token))
    s.call('bind_session',dict(session_id='tool-B',jid=j['job_id']))
    assert s.call('resume_session',dict(session_id='tool-B'))['revision']==3
    assert s.call('resolve_job',dict(project='项目',query='任务'))['match']=='unique'


def test_two_process_claim_conflict(tmp_path):
    import subprocess,sys
    w=Workspace(tmp_path);w.init();p=w.project_create('并发')
    j=Jobs(w).create('共同任务','目标',dict(include_company=False,include_professional=False,project_ids=[p['project_id']],topic_ids=[]))
    args=[sys.executable,'-m','aiws','--workspace',str(tmp_path),'call','claim_job','--params']
    processes=[subprocess.Popen(args+[json.dumps(dict(jid=j['job_id'],owner=owner,revision=1))],stdout=subprocess.PIPE,text=True) for owner in ('A','B')]
    results=[json.loads(p.communicate()[0]) for p in processes]
    assert sum(r['ok'] for r in results)==1
    assert [r['error']['code'] for r in results if not r['ok']]==['CONFLICT']


def test_create_request_replay(tmp_path):
    w=Workspace(tmp_path);w.init();p=w.project_create('幂等')
    scope=dict(include_company=False,include_professional=False,project_ids=[p['project_id']],topic_ids=[])
    j=Jobs(w).create('任务','目标',scope,request_id='same-request')
    assert Jobs(w).create('任务','目标',scope,request_id='same-request')['job_id']==j['job_id']
    with pytest.raises(Error):Jobs(w).create('另一个任务','目标',scope,request_id='same-request')


def test_ambiguous_remote_write_reconcile(remote_workspace,monkeypatch):
    root,p,j,rows=remote_workspace
    original=Feishu.command
    def commit_then_timeout(self,*args,**kwargs):
        original(self,*args,**kwargs)
        raise Error('REMOTE_UNCERTAIN','Simulated lost response after server commit')
    monkeypatch.setattr(Feishu,'command',commit_then_timeout)
    with pytest.raises(Error):Service(root).call('claim_job',dict(jid=j['job_id'],owner='A',revision=1))
    recovered=Service(root).call('reconcile_remote',{})
    assert recovered['recovered']==[j['job_id']] and not recovered['unresolved']
    assert Service(root).call('resume_job',dict(jid=j['job_id']))['revision']==2
    assert len(rows['jobs'])==1


def test_project_update_hash_and_alias(remote_workspace):
    root,p,j,rows=remote_workspace;s=Service(root)
    original=s.call('get_project',dict(project_id=p['project_id']))
    changed=s.call('update_project',dict(project_id=p['project_id'],expected_hash=original['state_hash'],patch={'aliases':['别名'],'status_note':'独立说明'}))
    assert changed['status_note']=='独立说明'
    with pytest.raises(Error):s.call('update_project',dict(project_id=p['project_id'],expected_hash=original['state_hash'],patch={'status':'paused'}))
    assert Service(root).call('resolve_job',dict(project='别名',query='任务'))['match']=='unique'


def test_datetime_normalization_and_drift(remote_workspace):
    from aiws.remote_store import date_value
    assert date_value(None) is None
    assert date_value('1794534802.487892') == date_value(1794534802.487892)
    assert date_value('2026-11-13T09:20:21.123456+08:00') == '2026-11-13T01:20:21.123+00:00'
    root,p,j,rows=remote_workspace
    row=next(iter(rows['jobs'].values()))
    import datetime as dt
    row['更新时间']=dt.datetime.fromisoformat(row['更新时间']).astimezone(dt.timezone(dt.timedelta(hours=8))).isoformat()
    RemoteStore(Workspace(root)).decode('jobs',row)
    row['更新时间']='2026-01-01T00:00:00+00:00'
    with pytest.raises(Error,match='outside aiws'):RemoteStore(Workspace(root)).decode('jobs',row)


def test_remote_git_context_round_trip(remote_workspace):
    root,p,j,rows=remote_workspace
    from aiws.git_store import GitStore
    GitStore(Workspace(root),p['project_id']).initialize()
    s=Service(root)
    claim=s.call('claim_job',dict(jid=j['job_id'],owner='context',revision=1))
    token=claim['lease']['token']
    saved=s.call('save_work_checkpoint',dict(jid=j['job_id'],revision=2,lease_token=token,
                 context_patch=dict(working_on='第三章',working_files=[])))
    assert saved['context']['checkpoint_commit']
    # New service reads remote authority, not a retained local Python object.
    restored=Service(root).call('resume_job',dict(jid=j['job_id']))
    assert restored['work_context']['task_context']['context']['working_on']=='第三章'
    assert restored['work_context']['task_context']['revision']==3
