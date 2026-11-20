from aiws.core import Workspace, Error
from aiws.service import Service
from aiws.jobs import Jobs
from aiws.maintenance import Maintenance
from aiws.retrieval import Retrieval
import pytest

def test_backup_restore_new_path(tmp_path):
    root=tmp_path/'original';w=Workspace(root);w.init();p=w.project_create('采购')
    s={'include_company':False,'include_professional':False,'project_ids':[p['project_id']],'topic_ids':[]}
    (root/p['path']/'资料/a.txt').write_text('采购周期30天')
    j=Jobs(w).create('采购方案','形成改进建议',s)
    artifact=p['path']+'/工作/'+j['job_id']+'/draft.txt'
    saved=Service(root).call('save_artifact',{'jid':j['job_id'],'relative':artifact,'content':'原件事实：采购周期30天。建议：核实订单波动。'})
    b=Maintenance(w).backup();target=tmp_path/'restored'
    result=Maintenance(w).restore(b['path'],target)
    assert result['health']['healthy']
    restored=Workspace(target)
    assert Jobs(restored).resume(j['job_id'],detail='full')['artifact_integrity'][0]['integrity']=='ok'
    assert Retrieval(restored).search('采购周期',s)['matches']
    with pytest.raises(Error):Maintenance(w).restore(b['path'],target)

def test_internal_symlink_isolation(tmp_path):
    w=Workspace(tmp_path);w.init();a=w.project_create('A');b=w.project_create('B')
    (tmp_path/b['path']/'资料/b.txt').write_text('秘密')
    (tmp_path/a['path']/'资料/link.txt').symlink_to(tmp_path/b['path']/'资料/b.txt')
    s={'include_company':False,'include_professional':False,'project_ids':[a['project_id']],'topic_ids':[]}
    result=Retrieval(w).search('秘密',s)
    assert not result['matches'] and result['index']['failures'][0]['code']=='OUT_OF_ROOT'


def test_restore_preserves_and_reports_preexisting_stale_reference(tmp_path):
    root=tmp_path/'original';w=Workspace(root);w.init();p=w.project_create('旧资料')
    scope=dict(include_company=False,include_professional=False,project_ids=[p['project_id']],topic_ids=[])
    j=Jobs(w).create('任务','目标',scope)
    relative=p['path']+'/工作/'+j['job_id']+'/draft.txt'
    Service(root).call('save_artifact',dict(jid=j['job_id'],relative=relative,content='recorded'))
    w.path(relative).write_text('externally changed before backup')
    backup=Maintenance(w).backup()
    result=Maintenance(w).restore(backup['path'],tmp_path/'restored')
    assert result['health']['restored_fidelity']
    assert not result['health']['healthy']
    assert result['health']['preexisting_changed_artifacts']==[relative]
    assert (tmp_path/'restored'/relative).read_text()=='externally changed before backup'
