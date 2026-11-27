import contextlib
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from aiws.core import Error, Workspace, digest, put_json
from aiws.git_store import GitStore
from aiws.retrieval import Retrieval


def project_scope(project):
    return {'include_company': False, 'include_professional': False,
            'project_ids': [project['project_id']], 'topic_ids': []}


@pytest.mark.parametrize('use_git', [False, True])
def test_update_only_changed_file_and_skip_empty_or_unsupported(tmp_path, monkeypatch, use_git):
    w=Workspace(tmp_path);w.init()
    project=w.project_create('Test')
    if use_git:GitStore(w,project['project_id']).initialize()
    scope=project_scope(project)
    folder=w.root/project['path']/'资料'
    a=folder/'a.txt';b=folder/'b.txt';empty=folder/'empty.txt';unsupported=folder/'unsupported.bin'
    a.write_text('original alpha');b.write_text('unchanged beta')
    empty.write_text('');unsupported.write_bytes(b'opaque')
    r=Retrieval(w);statements=[];parsed=[]
    connect=r.connect;extract=r._extract_bytes

    @contextlib.contextmanager
    def trace():
        with connect() as db:
            db.set_trace_callback(statements.append)
            yield db

    monkeypatch.setattr(r,'connect',trace)
    monkeypatch.setattr(r,'_extract_bytes',lambda data,suffix:(parsed.append(data),extract(data,suffix))[1])
    assert r.search('alpha',scope)['matches']
    before=r.search('beta',scope)['matches'][0]

    parsed.clear();statements.clear()
    report=r.update(scope)
    assert report['changed_files']==0
    assert [f['code'] for f in report['failures']]==['PARSE_UNSUPPORTED']
    assert not parsed
    def writes():
        return [sql for sql in statements if sql.startswith(('DELETE FROM terms','INSERT INTO terms'))]
    assert not writes()

    a.write_text('revised alpha')
    report=r.update(scope)
    assert report['changed_files']==1
    assert parsed==[b'revised alpha']
    assert len(writes())==2
    assert r.search('beta',scope)['matches'][0]==before
    assert not r.search('original',scope)['matches']
    assert r.search('revised',scope)['matches']

    b.unlink()
    assert not r.search('beta',scope)['matches']
    with r.connect() as db:
        assert not db.execute('SELECT 1 FROM terms WHERE source_id=?',(before['source_id'],)).fetchone()


def test_legacy_index_refresh_recovers_and_prunes(tmp_path):
    w=Workspace(tmp_path);w.init()
    p=w.project_create('Legacy')
    folder=w.root/p['path']/'资料'
    keep=folder/'keep.txt';deleted=folder/'deleted.txt'
    keep.write_text('live sample');deleted.write_text('expired sample')
    r=Retrieval(w);scope=project_scope(p)
    assert r.search('expired',scope)['matches']
    # Remove the cache and lookup index added by this version, leaving the
    # exact source/FTS tables shipped with the older index.
    with sqlite3.connect(w.path('.workspace/index/sources.sqlite')) as db:
        db.execute('DROP TABLE source_files')
        db.execute('DROP INDEX sources_by_path')
    deleted.unlink()
    assert not r.search('expired',scope)['matches']
    assert r.search('live',scope)['matches']
    assert r.update(scope)['changed_files']==0


def test_existing_unsupported_html_cache_reparses_after_html_support_upgrade(tmp_path, monkeypatch):
    w=Workspace(tmp_path);w.init();project=w.project_create('旧报价')
    scope=project_scope(project)
    quote=w.path(project['path']+'/资料/报价单.html')
    quote.write_text('<html><head><title>旧报价单</title></head><body><table>'
                     '<tr><td>含税金额</td><td>12800</td></tr>'
                     '<tr><td>交期</td><td>15个工作日</td></tr></table></body></html>')
    unchanged=quote.stat()
    r=Retrieval(w);extract=r._extract_bytes

    def old_parser(content, suffix):
        if suffix=='.html':
            raise Error('PARSE_UNSUPPORTED','Old index did not read HTML')
        return extract(content,suffix)

    monkeypatch.setattr(r,'_extract_bytes',old_parser)
    first=r.update(scope)
    assert first['failures']==[{'path':quote.relative_to(w.root).as_posix(),'code':'PARSE_UNSUPPORTED'}]
    monkeypatch.setattr(r,'_extract_bytes',extract)
    recovered=r.update(scope,strict=False)
    assert recovered['changed_files']==1
    assert quote.stat().st_size==unchanged.st_size and quote.stat().st_mtime_ns==unchanged.st_mtime_ns
    assert r.search('12800',scope)['matches']
    assert r.search('15个工作日',scope)['matches']


def test_blocked_parser_does_not_block_another_root(tmp_path, monkeypatch):
    w=Workspace(tmp_path);w.init()
    a=w.project_create('A');b=w.project_create('B')
    (w.root/a['path']/'资料/a.txt').write_text('slow alpha')
    (w.root/b['path']/'资料/b.txt').write_text('quick beta')
    r=Retrieval(w);r.update()
    (w.root/a['path']/'资料/a.txt').write_text('slow changed alpha')
    (w.root/b['path']/'资料/b.txt').write_text('quick changed beta')
    entered=threading.Event();release=threading.Event()
    extract=r._extract_bytes

    def block(data,suffix):
        if data==b'slow changed alpha':
            entered.set()
            assert release.wait(10), 'timed out waiting to release parser'
        return extract(data,suffix)

    monkeypatch.setattr(r,'_extract_bytes',block)
    with ThreadPoolExecutor(max_workers=2) as pool:
        slow=pool.submit(r.update,project_scope(a))
        try:
            assert entered.wait(5), 'expected slow parser to start'
            quick=pool.submit(Retrieval(Workspace(tmp_path)).search,'changed beta',project_scope(b))
            assert len(quick.result(timeout=5)['matches'])==1
        finally:
            release.set()
        assert slow.result(timeout=5)['changed_files']==1


def test_transient_parse_failure_is_retried(tmp_path, monkeypatch):
    w=Workspace(tmp_path);w.init();p=w.project_create('Retry')
    (w.root/p['path']/'资料/input.txt').write_text('transient example')
    r=Retrieval(w);scope=project_scope(p)
    extract=r._extract_bytes

    def transient(data,suffix):
        if data==b'transient example':
            raise OSError('temporary parser failure')
        return extract(data,suffix)

    monkeypatch.setattr(r,'_extract_bytes',transient)
    assert [row['code'] for row in r.update(scope)['failures']]==['PARSE_ERROR']
    monkeypatch.setattr(r,'_extract_bytes',extract)
    assert r.search('transient example',scope)['matches']


def test_warm_search_skips_unchanged_originals_and_strict_search_rechecks(tmp_path, monkeypatch):
    w = Workspace(tmp_path); w.init()
    project = w.project_create('热搜')
    folder = w.path(project['path']) / '资料'
    for i in range(240):
        (folder / f'{i}.txt').write_text(f'item {i}')
    retrieval = Retrieval(w)
    scope = project_scope(project)
    hit = retrieval.search('item 239', scope)['matches'][0]
    reads = []
    read_bytes = type(folder).read_bytes

    def count(path):
        if path.parent == folder:
            reads.append(path.name)
        return read_bytes(path)

    monkeypatch.setattr(type(folder), 'read_bytes', count)
    assert retrieval.search('item 239', scope)['matches'] == [hit]
    assert reads == []
    (folder / '239.txt').write_text('replaced')
    (folder / '238.txt').unlink()
    (folder / 'new.txt').write_text('another new file')
    assert retrieval.search('replaced', scope)['matches']
    assert set(reads) == {'239.txt', 'new.txt'}
    reads.clear()
    assert not retrieval.search('item 238', scope, strict=True)['matches']
    assert len(reads) == 240
    assert not retrieval.search('item 239', scope)['matches']


@pytest.mark.parametrize('use_git', [False, True])
def test_approval_status_updates_without_replacing_unchanged_rows(tmp_path, use_git):
    w=Workspace(tmp_path);w.init();p=w.project_create('Approval')
    if use_git:GitStore(w,p['project_id']).initialize()
    rel=p['path']+'/成果/document.txt'
    path=w.path(rel);path.write_text('approval example')
    r=Retrieval(w);scope=project_scope(p)
    before=r.search('approval example',scope)['matches'][0]
    assert before['source_status']=='original'
    put_json(w.path('.workspace/history/events/test.json'),{
        'schema_version':1,'path':rel,'phase':'committed',
        'confirmed':True,'new_hash':digest(path),
    })
    result=r.search('approval example',scope)
    assert result['index']['changed_files']==0
    assert result['matches']==[{**before,'source_status':'approved'}]


def test_read_returns_the_bytes_it_validated_even_during_a_concurrent_replace(tmp_path, monkeypatch):
    w=Workspace(tmp_path);w.init()
    p=w.project_create('Snapshot')
    path=w.root/p['path']/'资料'/'source.txt'
    path.write_text('old document')
    r=Retrieval(w);scope=project_scope(p)
    hit=r.search('old document',scope)['matches'][0]
    path_type=type(path)
    read_bytes=path_type.read_bytes

    def read_and_replace(self):
        content=read_bytes(self)
        if self==path:
            updated=path.with_suffix('.replacement')
            updated.write_text('new document')
            updated.replace(path)
        return content

    monkeypatch.setattr(path_type,'read_bytes',read_and_replace)
    assert r.read(hit['source_id'],scope)['snippet']=='old document'
    assert path.read_text()=='new document'
