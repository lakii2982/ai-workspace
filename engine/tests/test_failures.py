import json
import subprocess
import sys
from aiws.core import Workspace
from aiws.retrieval import Retrieval
from reportlab.pdfgen.canvas import Canvas

def test_scanned_page_and_index_failure(tmp_path):
    w=Workspace(tmp_path);w.init();p=w.project_create('失败样本')
    c=Canvas(str(w.root/p['path']/'资料/scan.pdf'));c.rect(20,20,100,100,fill=1);c.showPage();c.save()
    result=Retrieval(w).update()
    assert result['failures'][0]['code']=='PARSE_LIMITED'
    db=w.path('.workspace/index/sources.sqlite');db.write_bytes(b'broken database')
    run=subprocess.run([sys.executable,'-m','aiws','--workspace',str(tmp_path),'doctor'],capture_output=True,text=True)
    assert run.returncode==1 and json.loads(run.stdout)['error']['code']=='INTERNAL_ERROR'
