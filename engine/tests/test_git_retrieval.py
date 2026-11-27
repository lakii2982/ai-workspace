import pytest
from docx import Document
from aiws.core import Workspace, Error
from aiws.git_store import GitStore
from aiws.retrieval import Retrieval


def setup(tmp_path):
    w=Workspace(tmp_path);w.init();p=w.project_create('资料 空格')
    g=GitStore(w,p['project_id']);g.initialize()
    scope={'include_company':False,'include_professional':False,'project_ids':[p['project_id']],'topic_ids':[]}
    return w,p,g,scope


def test_git_index_untracked_changed_removed_and_scope(tmp_path,monkeypatch):
    w,p,g,s=setup(tmp_path);r=Retrieval(w)
    f=g.root/'资料/采购.txt';f.write_text('采购三十天')
    first=r.search('采购',s);hit=first['matches'][0]
    assert hit['sha256'].startswith('git:')
    assert '三十天' in r.read(hit['source_id'],s)['snippet']
    # No parsing and no scan of another unselected project on an unchanged search.
    other=w.project_create('其他');(w.root/other['path']/'资料/坏.docx').write_bytes(b'bad')
    calls=[];original=r._extract_bytes
    monkeypatch.setattr(r,'_extract_bytes',lambda *a:(calls.append(a) or original(*a)))
    assert r.search('采购',s)['index']['changed_files']==0
    assert not calls
    f.write_text('采购六十天')
    with pytest.raises(Error) as e:r.read(hit['source_id'],s)
    assert e.value.code=='STALE_INDEX'
    assert not r.search('三十天',s)['matches']
    assert r.search('六十天',s)['matches']
    f.rename(g.root/'资料/改名.txt')
    assert r.search('六十天',s)['matches'][0]['source_path'].endswith('改名.txt')
    (g.root/'资料/改名.txt').unlink()
    assert not r.search('六十天',s)['matches']


def test_git_document_versions_and_unregistered_work_file(tmp_path):
    w,p,g,s=setup(tmp_path);r=Retrieval(w)
    f=g.root/'资料/要求.docx'
    d=Document();d.add_paragraph('原要求三十天');d.save(f)
    before=g.observe()['commit']
    d=Document();d.add_paragraph('新要求六十天');d.save(f)
    result=r.compare_project_file(p['project_id'],'资料/要求.docx',before)
    assert '三十天' in result['before']['text']
    assert '六十天' in result['after']['text']
    assert '新要求' in result['diff']
    draft=g.root/'工作/手放入.txt';draft.write_text('尚未登记的主稿')
    value=r.read_project_file(p['project_id'],'工作/手放入.txt')
    assert value['text']=='尚未登记的主稿'
    import hashlib
    assert value['sha256']==hashlib.sha256('尚未登记的主稿'.encode()).hexdigest()
    assert not r.search('尚未登记',s)['matches']
    unsupported=g.root/'资料/附件.bin';unsupported.write_bytes(b'opaque')
    assert r.read_project_file(p['project_id'],'资料/附件.bin')['readability']=='unsupported'
    with pytest.raises(Error):r.read_project_file(p['project_id'],'../逃逸.txt')


def test_literal_root_and_publication_status(tmp_path):
    from aiws.jobs import Jobs, Artifacts
    w=Workspace(tmp_path);w.init()
    a=w.project_create('A_%');b=w.project_create('A_X')
    scope={'include_company':False,'include_professional':False,'project_ids':[a['project_id']],'topic_ids':[]}
    job=Jobs(w).create('publish','verify',scope)
    target=a['path']+'/成果/报告.md'
    Artifacts(w).save(job['job_id'],target,b'published',confirm=True)
    (w.root/b['path']/'资料/source.txt').write_text('other project')
    r=Retrieval(w);r.update()
    GitStore(w,a['project_id']).initialize()
    hit=r.search('published',scope)['matches'][0]
    assert hit['source_status']=='approved'
    both={**scope,'project_ids':[a['project_id'],b['project_id']]}
    assert r.search('other project',both)['matches']
    # Updating one literal root never removes a similarly named project's rows.
    r.update(scope)
    with r.connect() as db:
        assert db.execute('SELECT count(*) FROM sources WHERE project_id=?',(b['project_id'],)).fetchone()[0]


@pytest.mark.parametrize('extension',['xlsx','pdf'])
def test_binary_document_old_and_new_versions(tmp_path,extension):
    w,p,g,scope=setup(tmp_path)
    path=g.root/('资料/input.'+extension)
    def write(text):
        if extension=='xlsx':
            from openpyxl import Workbook
            book=Workbook();book.active['A1']=text;book.save(path)
        else:
            from reportlab.pdfgen import canvas
            page=canvas.Canvas(str(path));page.drawString(30,700,text);page.save()
    write('old maintenance 30 days')
    before=g.observe()['commit']
    write('new maintenance 90 days')
    compared=Retrieval(w).compare_project_file(p['project_id'],'资料/input.'+extension,before)
    assert '30 days' in compared['before']['text']
    assert '90 days' in compared['after']['text']
    assert compared['diff']
