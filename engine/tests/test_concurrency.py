import multiprocessing as mp
from aiws.core import Workspace, Error
from aiws.jobs import Jobs, Artifacts

def writer(root,jid,path,content,queue):
    try: queue.put(Artifacts(Workspace(root)).save(jid,path,content.encode()))
    except Error as e:queue.put(e.code)

def test_real_processes(tmp_path):
    w=Workspace(tmp_path);w.init();p=w.project_create('并发')
    s={'include_company':True,'include_professional':False,'project_ids':[p['project_id']],'topic_ids':[]}
    j=Jobs(w).create('并发','保护成果',s);jid=j['job_id']
    base=p['path']+'/工作/'+jid+'/'
    ctx=mp.get_context('spawn');q=ctx.Queue()
    for paths in [[base+'a.txt',base+'b.txt'],[base+'shared.txt']*2]:
        ps=[ctx.Process(target=writer,args=(str(tmp_path),jid,path,str(n),q)) for n,path in enumerate(paths)]
        for proc in ps:proc.start()
        for proc in ps:proc.join(20);assert proc.exitcode==0
        results=[q.get(timeout=3) for _ in ps]
        if paths[0]==paths[1]:assert sum(x=='CONFLICT' for x in results)==1
        else:assert all(isinstance(x,dict) for x in results)
    assert (w.root/base/'a.txt').read_text()=='0'
    assert (w.root/base/'b.txt').read_text()=='1'
    try: Artifacts(w).save(jid,'公司知识/公司档案/制度.txt',b'draft')
    except Error as e:assert e.code=='CONFIRMATION_REQUIRED'
    else:assert False
    assert len(Jobs(w).get(jid)['artifacts'])==3
    assert list(w.path('.workspace/history/conflicts').rglob('*'))
