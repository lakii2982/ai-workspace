from pathlib import Path
from aiws.core import Workspace, Error
from aiws.retrieval import Retrieval
from docx import Document
from openpyxl import Workbook
from reportlab.pdfgen.canvas import Canvas
import pytest

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
