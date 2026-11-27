from pathlib import Path
from aiws.core import Workspace, Error
from aiws.retrieval import Retrieval
from docx import Document
from openpyxl import Workbook
from reportlab.pdfgen.canvas import Canvas
import pytest

def test_xlsx_formula_cache_and_sparse_layout(tmp_path):
    # 多页签、稀疏单元格、空行、有/无缓存值的公式：定位符与文案必须逐项等价。
    import zipfile
    from aiws.retrieval import extract
    book=Workbook();data=book.active;data.title='数据'
    data['A1']='标题';data['B2']='=1+2';data['D3']='=2+3';data['C10']='尾注'
    book.create_sheet('汇总')['A1']='=SUM(数据!B2)'
    path=tmp_path/'缓存.xlsx';book.save(path)
    with zipfile.ZipFile(path) as src:items={n:src.read(n) for n in src.namelist()}
    # openpyxl 不计算公式；向 sheet XML 注入 <v> 模拟 Excel 保存的缓存值。
    items['xl/worksheets/sheet1.xml']=items['xl/worksheets/sheet1.xml'].replace(b'<f>1+2</f><v></v>',b'<f>1+2</f><v>3</v>')
    items['xl/worksheets/sheet2.xml']=items['xl/worksheets/sheet2.xml'].replace(b'<v></v>',b'<v>6</v>',1)
    with zipfile.ZipFile(path,'w') as dst:
        for n,blob in items.items():dst.writestr(n,blob)
    assert dict(extract(path))=={
        '数据!A1':'标题',
        '数据!B2':'=1+2 [cached=3; cache may be stale; not recalculated]',
        '数据!D3':'=2+3 [cached=None; cache may be stale; not recalculated]',
        '数据!C10':'尾注',
        '汇总!A1':'=SUM(数据!B2) [cached=6; cache may be stale; not recalculated]',
    }

def setup(tmp_path):
    w=Workspace(tmp_path);w.init();a=w.project_create('A');b=w.project_create('B')
    scope={'include_company':False,'include_professional':False,'project_ids':[a['project_id']],'topic_ids':[]}
    return w,a,b,scope

def test_formats_isolation_and_read(tmp_path):
    w,a,b,s=setup(tmp_path);folder=w.root/a['path']/'资料'
    (folder/'a.md').write_text('采购周期30天 编号A-2026')
    (folder/'a.txt').write_text('采购周期30天')
    d=Document(); d.add_paragraph('采购周期30天');d.add_table(rows=1,cols=1).cell(0,0).text='采购表格';d.save(folder/'a.docx')
    x=Workbook();x.active['A1']='采购周期30天';x.create_sheet('费用')['B2']='=1+2';x.save(folder/'a.xlsx')
    c=Canvas(str(folder/'a.pdf'));c.drawString(20,700,'PDF-MARKER');c.save()
    (w.root/b['path']/'资料/b.txt').write_text('采购周期90天 B秘密')
    r=Retrieval(w)
    assert len(r.search('采购',s)['matches'])>=5
    assert {Path(m['source_path']).suffix for m in r.search('采购周期30天',s)['matches']}=={'.md','.txt','.docx','.xlsx'}
    assert not r.search('B秘密',s)['matches']
    both={**s,'project_ids':[a['project_id'],b['project_id']]}
    assert r.search('B秘密',both)['matches']
    for q in ['编号A-2026','PDF-MARKER','采购周期30天']:
        hit=r.search(q,s)['matches'][0]; assert q in r.read(hit['source_id'],s)['snippet']
    hit=r.search('PDF-MARKER',s)['matches'][0]
    (folder/'a.pdf').unlink()
    with pytest.raises(Error) as e:r.read(hit['source_id'],s)
    assert e.value.code=='STALE_INDEX'
    assert not r.search('PDF-MARKER',s)['matches']
    (folder/'a.md').rename(folder/'moved.md')
    assert any('moved.md' in m['source_path'] for m in r.search('编号A-2026',s)['matches'])
    (folder/'broken.docx').write_bytes(b'bad')
    (folder/'old.doc').write_bytes(b'old')
    codes={x['code'] for x in r.update()['failures']}
    assert codes=={'PARSE_ERROR','PARSE_UNSUPPORTED'}
