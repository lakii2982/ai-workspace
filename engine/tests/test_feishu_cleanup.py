import copy
import json
import pytest
from aiws.core import Workspace, Error, put_json
from aiws.jobs import Jobs
from aiws.service import Service
from aiws.feishu import Feishu
from aiws.maintenance import Maintenance
from aiws.migration import Migration


@pytest.fixture
def remote_workspace(tmp_path,monkeypatch):
    w=Workspace(tmp_path);w.init();p=w.project_create('迁移')
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
    return w,j,rows


def test_feishu_stage_job_removed(tmp_path):
    w=Workspace(tmp_path);w.init()
    with pytest.raises(Error) as caught:
        Service(tmp_path).call('feishu_stage_job',dict(jid='JOB-0001',operation_id='operation-1'))
    assert caught.value.code=='INVALID_ARGUMENT'


def test_migration_requires_explicit_staging_adoption(remote_workspace):
    w,j,rows=remote_workspace
    rows['jobs']['rec-stage']={'record_id':'rec-stage','任务ID':j['job_id'],'操作ID':'stage-op-1'}
    with pytest.raises(Error,match='Unmanaged remote row'):Migration(w).run()
    result=Migration(w).run(adopt_staging={j['job_id']:'stage-op-1'})
    assert result['verified'] is True
    assert '执行数据' in rows['jobs']['rec-stage']
