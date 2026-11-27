import pytest

from aiws.core import Error, Workspace
from aiws.jobs import Artifacts, Jobs
from aiws.service import Service


def workspace(tmp_path):
    w=Workspace(tmp_path);w.init();p=w.project_create('恢复')
    scope=dict(include_company=False,include_professional=False,
               project_ids=[p['project_id']],topic_ids=[])
    return w,p,Jobs(w).create('稿件','恢复清单',scope)


def test_recover_backfills_committed_file_after_manifest_failure(tmp_path, monkeypatch):
    w,p,j=workspace(tmp_path)
    actual=Jobs.persist
    calls=[]
    def fail_once(self, record):
        calls.append(record['revision'])
        if len(calls)==1: raise Error('REMOTE_UNCERTAIN','manifest write interrupted')
        return actual(self,record)
    monkeypatch.setattr(Jobs,'persist',fail_once)
    relative=p['path']+'/工作/'+j['directory_name']+'/工作稿/方案.md'
    with pytest.raises(Error,match='interrupted'):
        Artifacts(w).save(j['job_id'],relative,b'v1')
    assert w.path(relative).read_bytes()==b'v1'
    repaired=Service(tmp_path).call('recover_artifacts',{'jid':j['job_id']})
    assert repaired['recovered']==[{'path':relative,'hash':repaired['recovered'][0]['hash'],
                                    'purpose':'draft','status':'draft'}]
    assert Jobs(w).get(j['job_id'])['artifacts']==repaired['recovered']


def test_recovery_never_restores_or_overwrites_manually_changed_file(tmp_path, monkeypatch):
    w,p,j=workspace(tmp_path)
    actual=Jobs.persist
    monkeypatch.setattr(Jobs,'persist',lambda self,record: (_ for _ in ()).throw(Error('REMOTE_UNCERTAIN','lost')))
    relative=p['path']+'/工作/'+j['directory_name']+'/工作稿/方案.md'
    with pytest.raises(Error): Artifacts(w).save(j['job_id'],relative,b'agent')
    monkeypatch.setattr(Jobs,'persist',actual)
    w.path(relative).write_bytes(b'human')
    result=Service(tmp_path).call('recover_artifacts',{'jid':j['job_id']})
    assert result['recovered']==[]
    assert result['pending']==[{'path':relative,'state':'changed_or_missing'}]
    assert w.path(relative).read_bytes()==b'human'


def test_recovery_ignores_forged_out_of_scope_journal(tmp_path):
    w,p,j=workspace(tmp_path)
    event=w.path('.workspace/history/events/forged.json')
    from aiws.core import put_json
    put_json(event,{'schema_version':1,'job_id':j['job_id'],'phase':'committed',
                    'path':'公司知识/公司档案/x.md','artifact':{'path':'公司知识/公司档案/x.md',
                    'hash':'0'*64,'purpose':'draft','status':'draft'}})
    result=Service(tmp_path).call('recover_artifacts',{'jid':j['job_id']})
    assert result['recovered']==[]
    assert result['pending']==[{'path':'公司知识/公司档案/x.md','state':'outside_scope'}]


def test_successful_save_cleans_path_intent_and_recovery_handles_overwrite(tmp_path, monkeypatch):
    w,p,j=workspace(tmp_path)
    relative=p['path']+'/工作/'+j['directory_name']+'/工作稿/方案.md'
    first=Artifacts(w).save(j['job_id'],relative,b'v1')
    assert not list(w.path('.workspace/recovery/'+j['job_id']).glob('*.json'))
    actual=Jobs.persist
    monkeypatch.setattr(Jobs,'persist',lambda self,record: (_ for _ in ()).throw(Error('REMOTE_UNCERTAIN','lost')))
    with pytest.raises(Error):
        Artifacts(w).save(j['job_id'],relative,b'v2',expected_hash=first['hash'])
    monkeypatch.setattr(Jobs,'persist',actual)
    repaired=Service(tmp_path).call('recover_artifacts',{'jid':j['job_id']})
    assert repaired['recovered'][0]['hash'] != first['hash']
    assert Jobs(w).get(j['job_id'])['artifacts']==repaired['recovered']
    assert not list(w.path('.workspace/recovery/'+j['job_id']).glob('*.json'))


def test_prepared_intent_after_disk_write_is_recoverable_but_manual_bytes_are_not(tmp_path):
    w,p,j=workspace(tmp_path)
    relative=p['path']+'/工作/'+j['directory_name']+'/工作稿/方案.md'
    target=w.path(relative);target.parent.mkdir(parents=True);target.write_bytes(b'new')
    from aiws.core import put_json, digest
    artifact={'path':relative,'hash':digest(target),'purpose':'draft','status':'draft'}
    put_json(w.path('.workspace/recovery/'+j['job_id']+'/one.json'),
             {'schema_version':1,'job_id':j['job_id'],'path':relative,'old_hash':None,
              'new_hash':artifact['hash'],'phase':'prepared','artifact':artifact})
    assert Service(tmp_path).call('resume_job',{'jid':j['job_id']})['recovery']['recoverable']
    target.write_bytes(b'human')
    status=Service(tmp_path).call('resume_job',{'jid':j['job_id']})['recovery']
    assert status['recoverable']==[] and status['pending']==[{'path':relative,'state':'changed_or_missing'}]
