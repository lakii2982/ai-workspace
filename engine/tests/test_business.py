"""Synthetic business pipelines. No customer data; outputs are deterministic acceptance fixtures."""
import subprocess
import sys
from aiws.core import Workspace
from aiws.service import Service
from aiws.jobs import Jobs
from aiws.retrieval import Retrieval

def test_e2e_a_b_c_d(tmp_path):
    w=Workspace(tmp_path);w.init()
    a=w.project_create('供应链');b=w.project_create('销售');c=w.project_create('招标')
    (w.root/'公司知识/公司档案/简介.txt').write_text('公司产品额定功率10kW。未提供认证证书。')
    (w.root/a['path']/'资料/采购.txt').write_text('采购周期30天。采购付款100万元。')
    (w.root/b['path']/'资料/销售.txt').write_text('采购周期90天。销售回款150万元。销售专有标记B_ONLY。')
    (w.root/c['path']/'资料/需求.txt').write_text('要求产品额定功率10kW，要求提供认证证书。')
    base={'include_company':True,'include_professional':False,'project_ids':[a['project_id']],'topic_ids':[]}
    r=Retrieval(w)
    def facts(query,scope):
        return [r.read(m['source_id'],scope) for m in r.search(query,scope)['matches']]
    def citation(m):return f"{m['source_path']} [{m['locator']}] sha256={m['sha256']}"
    # A: one-project evidence and company context, no project B facts.
    procurement=facts('采购周期',base);company=facts('额定功率',base)
    assert len(procurement)==1 and '30天' in procurement[0]['snippet']
    j=Jobs(w).create('采购优化','采购周期优化方案',base,['可靠来源'])
    text='原件事实：'+procurement[0]['snippet']+'\n来源：'+citation(procurement[0])+'\n公司事实：'+company[0]['snippet']+'\n来源：'+citation(company[0])+'\n建议：核实需求波动后评估分批采购。\n分析假设：需求稳定，尚待确认。'
    assert '90天' not in text and 'B_ONLY' not in text
    service=Service(w.root);draft=a['path']+'/工作/'+j['job_id']+'/v1.md'
    service.call('save_artifact',dict(jid=j['job_id'],relative=draft,content=text))
    # B: explicitly authorized combined scope; facts, arithmetic, assumptions separated.
    both={**base,'project_ids':[a['project_id'],b['project_id']]}
    pay=facts('采购付款',both)[0];revenue=facts('销售回款',both)[0]
    report='原件事实：'+pay['snippet']+'\n'+citation(pay)+'\n原件事实：'+revenue['snippet']+'\n'+citation(revenue)+'\n计算结果：150 - 100 = 50万元。\n分析假设：收付款属于同一期间，待确认。'
    assert pay['project_id']!=revenue['project_id'] and '50万元' in report
    cross=Jobs(w).create('现金流','对比采购与销售',both)
    service.call('save_artifact',dict(jid=cross['job_id'],relative=a['path']+'/工作/'+cross['job_id']+'/cashflow.md',content=report))
    # C: product parameter cited; missing certification remains unresolved.
    tender={**base,'project_ids':[c['project_id']]}
    spec=facts('要求',tender)[0];product=facts('额定功率',tender)[0]
    response='原件事实：'+product['snippet']+'\n'+citation(product)+'\n客户要求：'+spec['snippet']+'\n'+citation(spec)+'\n认证证书：待确认，不声称已有资质。'
    t=Jobs(w).create('技术响应','逐项溯源',tender)
    service.call('save_artifact',dict(jid=t['job_id'],relative=c['path']+'/工作/'+t['job_id']+'/response.md',content=response))
    assert '认证证书：待确认' in response
    # D: a separate interpreter resumes, reads original, writes v2, keeps v1 intact.
    latest=Jobs(w).get(j['job_id'])
    Jobs(w).checkpoint(j['job_id'],latest['revision'],{'status':'in_progress','next_steps':['补充风险为第二稿']})
    code='''import sys
from aiws.service import Service
s=Service(sys.argv[1]);j=s.call('resume_job',{'jid':sys.argv[2],'detail':'full'})
a=j['artifacts'][0]
text=s.w.path(a['path']).read_text()+'\\n建议：补充库存积压风险评估。'
r=s.call('save_artifact',dict(jid=j['job_id'],relative=a['path'].replace('v1.md','v2.md'),content=text))
print(r['path'])
'''
    run=subprocess.run([sys.executable,'-c',code,str(w.root),j['job_id']],capture_output=True,text=True)
    assert run.returncode==0 and 'v2.md' in run.stdout
    assert w.path(draft).read_text()==text
    assert len(Jobs(w).resume(j['job_id'],detail='full')['artifacts'])==2
