import base64
from pathlib import Path
import pytest
from aiws.core import Error
from aiws.service import Service

def make(tmp_path):
    svc=Service(tmp_path);svc.call('init',{})
    a=svc.call('create_project',{'name':'A'});b=svc.call('create_project',{'name':'B'})
    scope=lambda pids:{'include_company':False,'include_professional':False,'project_ids':pids,'topic_ids':[]}
    job=svc.call('create_job',{'title':'收件','goal':'归档聊天附件','scope':scope([a['project_id']])})
    return svc,a,b,job,scope

def receive(svc,job,pid,**kw):
    return svc.call('receive_source',dict(jid=job['job_id'],project_id=pid,**kw))

def test_receive_search_read_and_stale_check(tmp_path):
    svc,a,b,job,scope=make(tmp_path)
    result=receive(svc,job,a['project_id'],name='报价.txt',content='聊天附件 采购周期45天',confirm=True,source_ref='feishu-file:boxcnABC')
    assert result['replayed'] is False and result['purpose']=='source' and result['status']=='approved'
    path=Path(result['path'])
    assert path.parts[-2:]==('资料','报价.txt') and path.parts[-3]==Path(a['path']).name
    assert svc.w.path(result['path']).read_text()=='聊天附件 采购周期45天'
    matches=svc.call('search_sources',{'query':'采购周期45天','scope':scope([a['project_id']])})['matches']
    assert [m['source_path'] for m in matches]==[result['path']]
    read=svc.call('read_source',{'source_id':matches[0]['source_id'],'scope':scope([a['project_id']])})
    assert '采购周期45天' in read['snippet'] and read['sha256']==result['hash']
    events=list(svc.w.path('.workspace/history/events').glob('*.json'))
    assert any('source_ref' in e.read_text() and 'boxcnABC' in e.read_text() for e in events)
    # 原件在入口外被改动后，按旧指纹回查必须诚实报 STALE_INDEX。
    svc.w.path(result['path']).write_text('被外部改动')
    with pytest.raises(Error) as e:
        svc.call('read_source',{'source_id':matches[0]['source_id'],'scope':scope([a['project_id']])})
    assert e.value.code=='STALE_INDEX'

def test_base64_and_exclusive_content(tmp_path):
    svc,a,b,job,scope=make(tmp_path)
    raw='附件正文 ✓'.encode()
    result=receive(svc,job,a['project_id'],name='说明.md',content_base64=base64.b64encode(raw).decode(),confirm=True)
    assert svc.w.path(result['path']).read_bytes()==raw
    for kw in ({},{'content':'x','content_base64':base64.b64encode(b'x').decode()}):
        with pytest.raises(Error) as e:receive(svc,job,a['project_id'],name='坏.md',confirm=True,**kw)
        assert e.value.code=='INVALID_ARGUMENT'

def test_same_content_retry_is_idempotent(tmp_path):
    svc,a,b,job,scope=make(tmp_path)
    first=receive(svc,job,a['project_id'],name='合同.txt',content='合同正文',confirm=True)
    revision=svc.call('resume_job',{'jid':job['job_id']})['revision']
    events=len(list(svc.w.path('.workspace/history/events').glob('*.json')))
    again=receive(svc,job,a['project_id'],name='合同.txt',content='合同正文')
    assert again['replayed'] is True and again['hash']==first['hash']
    assert svc.call('resume_job',{'jid':job['job_id']})['revision']==revision
    assert len(list(svc.w.path('.workspace/history/events').glob('*.json')))==events
    assert len(svc.call('resume_job',{'jid':job['job_id'],'detail':'full'})['artifacts'])==1

def test_different_content_never_silently_overwrites(tmp_path):
    svc,a,b,job,scope=make(tmp_path)
    first=receive(svc,job,a['project_id'],name='报价.txt',content='第一版',confirm=True)
    with pytest.raises(Error) as e:receive(svc,job,a['project_id'],name='报价.txt',content='第二版')
    assert e.value.code=='CONFIRMATION_REQUIRED'
    with pytest.raises(Error) as e:receive(svc,job,a['project_id'],name='报价.txt',content='第二版',confirm=True)
    assert e.value.code=='CONFLICT'
    assert svc.w.path(first['path']).read_text()=='第一版'
    saved=receive(svc,job,a['project_id'],name='报价.txt',content='第二版',confirm=True,expected_hash=first['hash'])
    assert saved['replayed'] is False and svc.w.path(first['path']).read_text()=='第二版'
    assert svc.w.path(f".workspace/history/files/{first['hash']}").read_text()=='第一版'

def test_confirmation_required_for_new_source(tmp_path):
    svc,a,b,job,scope=make(tmp_path)
    with pytest.raises(Error) as e:receive(svc,job,a['project_id'],name='新.txt',content='正文')
    assert e.value.code=='CONFIRMATION_REQUIRED'

def test_scope_isolation(tmp_path):
    svc,a,b,job,scope=make(tmp_path)
    receive(svc,job,a['project_id'],name='秘密.txt',content='A项目秘密条款',confirm=True)
    assert svc.call('search_sources',{'query':'秘密条款','scope':scope([b['project_id']])})['matches']==[]
    with pytest.raises(Error) as e:
        svc.call('read_source',{'source_id':svc.call('search_sources',{'query':'秘密条款','scope':scope([a['project_id']])})['matches'][0]['source_id'],'scope':scope([b['project_id']])})
    assert e.value.code=='NOT_FOUND'
    with pytest.raises(Error) as e:receive(svc,job,b['project_id'],name='越权.txt',content='x',confirm=True)
    assert e.value.code=='INVALID_SCOPE'

def test_invalid_names_rejected(tmp_path):
    svc,a,b,job,scope=make(tmp_path)
    for name in ('../出界.txt','a/b.txt','.hidden.txt','~$锁.txt',''):
        with pytest.raises(Error) as e:receive(svc,job,a['project_id'],name=name,content='x',confirm=True)
        assert e.value.code=='INVALID_ARGUMENT', name
