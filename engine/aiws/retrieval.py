import contextlib
import hashlib
import sqlite3
from pathlib import Path
import tempfile
import difflib
from .core import Error, now, load

def extract(path):
    ext=path.suffix.lower()
    if ext in ('.md','.txt'):
        return [(f'line:{i}',line) for i,line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(),1) if line.strip()]
    if ext=='.docx':
        from docx import Document
        doc=Document(path)
        out=[(f'paragraph:{i}',p.text) for i,p in enumerate(doc.paragraphs,1) if p.text.strip()]
        out += [(f'table:{t}/row:{r}/cell:{c}',cell.text) for t,table in enumerate(doc.tables,1) for r,row in enumerate(table.rows,1) for c,cell in enumerate(row.cells,1) if cell.text.strip()]
        return out
    if ext=='.xlsx':
        from openpyxl import load_workbook
        from itertools import zip_longest
        formulas=load_workbook(path,read_only=True,data_only=False)
        values=load_workbook(path,read_only=True,data_only=True)
        out=[]
        try:
            # Cached values stream row-by-row alongside the formulas: random
            # access on a read-only sheet re-parses the XML up to the target
            # row, which turns formula-heavy sheets quadratic.
            for sheet in formulas:
                for row,cached_row in zip_longest(sheet.iter_rows(),values[sheet.title].iter_rows(),fillvalue=()):
                    cached={c.coordinate:c.value for c in cached_row if c.value is not None}
                    for cell in row:
                        if cell.value is not None:
                            text=str(cell.value)
                            if cell.data_type=='f': text+=f' [cached={cached.get(cell.coordinate)}; cache may be stale; not recalculated]'
                            out.append((f'{sheet.title}!{cell.coordinate}',text))
        finally: formulas.close();values.close()
        return out
    if ext=='.pdf':
        from pypdf import PdfReader
        out=[]
        for n,p in enumerate(PdfReader(path).pages,1):
            text=p.extract_text() or ''
            if not text.strip(): raise Error('PARSE_LIMITED',f'PDF page {n} has no extractable text; OCR required')
            out.append((f'page:{n}',text))
        return out
    raise Error('PARSE_UNSUPPORTED',f'Unsupported format {ext}')

class Retrieval:
    def __init__(self,w): self.w=w
    @contextlib.contextmanager
    def connect(self):
        db=sqlite3.connect(self.w.path('.workspace/index/sources.sqlite'),timeout=30)
        db.row_factory=sqlite3.Row
        try:
            if db.execute('PRAGMA journal_mode').fetchone()[0] != 'wal':
                db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS sources (source_id TEXT PRIMARY KEY, source_path TEXT, scope_type TEXT, project_id TEXT, title TEXT, locator TEXT, snippet TEXT, sha256 TEXT, indexed_at TEXT, source_status TEXT)')
            db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS terms USING fts5(source_id UNINDEXED, snippet, tokenize='trigram')")
            db.execute('CREATE INDEX IF NOT EXISTS sources_by_path ON sources(source_path)')
            db.execute("""
                CREATE TABLE IF NOT EXISTS source_files (
                    root TEXT NOT NULL, path TEXT NOT NULL, version TEXT,
                    status TEXT, error TEXT, PRIMARY KEY(root,path)
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS source_file_stats (
                    root TEXT NOT NULL, path TEXT NOT NULL, signature TEXT NOT NULL,
                    PRIMARY KEY(root,path)
                )
            """)
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
    def update(self, scope=None, *, strict=True):
        """Refresh selected roots and only replace rows for changed files."""
        if scope is not None:self.w.scope(scope)
        projects = self.w.projects() if scope is None else [
            self.w.project_for(pid) for pid in dict.fromkeys(scope['project_ids'])
        ]
        roots = []
        if scope is None or scope['include_company']:roots.append(('公司知识', None))
        if scope is None or scope['include_professional']:roots.append(('专业知识', None))
        roots += [(p['path'],p['project_id']) for p in projects]
        changed=0;failures=[]
        for root,pid in roots:
            # A slow file parser may delay another search in this root, but
            # does not exclude work in unrelated roots or hold a SQLite writer.
            with self.w.lock('index-root:'+root):
                n,errors=self._update_root(root,pid,strict)
            changed+=n
            failures.extend(errors)
        with self.connect() as db:
            count=db.execute('SELECT count(*) FROM sources').fetchone()[0]
        return {'sources':count,'changed_files':changed,'failures':failures}

    def _update_root(self, root, pid, strict):
        git=None;observation=None
        if pid:
            from .git_store import GitStore
            git=GitStore(self.w,pid)
            if git.enabled:observation=git.observe()
        if observation is not None:
            candidates=((root+'/'+path,'git:'+oid,path) for path,oid in observation['files'].items())
        else:
            candidates=((p.relative_to(self.w.root).as_posix(),None,None)
                        for p in sorted(self.w.path(root).rglob('*')) if p.is_file())
        with self.connect() as db:
            cached = {r['path']: dict(r) for r in db.execute(
                'SELECT f.*, s.signature FROM source_files AS f '
                'LEFT JOIN source_file_stats AS s USING(root,path) WHERE f.root=?', (root,))}
            # Also see paths from pre-incremental indexes. A first refresh
            # rebuilds those paths individually and prunes deleted ones.
            old={r[0] for r in db.execute(
                'SELECT DISTINCT source_path FROM sources WHERE source_path>=? AND source_path<?',
                (root+'/',root+'0'))}

        approvals={}
        for event in self.w.path('.workspace/history/events').glob('*.json'):
            record=load(event)
            if (record.get('phase')=='committed' and record.get('confirmed')
                    and record['path'].startswith(root+'/')):
                approvals.setdefault(record['path'],set()).add(record['new_hash'])

        seen=set(); replacements=[]; status_updates=[]; failures=[]; changed=0
        signatures = []
        for rel,version,local in candidates:
            parts=Path(rel).parts
            # Memory is recalled through its own index and is never source evidence.
            if (any(x.startswith('.') or x.startswith('~$') for x in parts)
                    or '工作' in parts or '记忆' in parts):
                continue
            seen.add(rel)
            before=cached.get(rel)
            rows=[];status=None;code=None
            try:
                path=self.w.path(rel)
                kind,project_id,status=self.w.classify(rel)
                content=None
                if local is None:
                    stat = path.stat()
                    signature = f'{stat.st_dev}/{stat.st_ino}/{stat.st_size}/{stat.st_mtime_ns}/{stat.st_ctime_ns}'
                    if (not strict and before and before['signature'] == signature
                            and before['version'] and not before['version'].startswith('git:')
                            and before['error'] in (None, 'PARSE_UNSUPPORTED', 'PARSE_LIMITED')):
                        version = before['version']
                    else:
                        content = path.read_bytes()
                        version = hashlib.sha256(content).hexdigest()
                        if not before or before['signature'] != signature:
                            signatures.append((root, rel, signature))
                    if version in approvals.get(rel, ()):
                        status = 'approved'
                elif rel in approvals:
                    content = git.read_version(observation['commit'], local)
                    if hashlib.sha256(content).hexdigest() in approvals[rel]:
                        status = 'approved'
                if before and before['version']==version:
                    if before['error'] is None:
                        if before['status']!=status:status_updates.append((status,rel))
                        continue
                    if before['error'] in ('PARSE_UNSUPPORTED','PARSE_LIMITED'):
                        failures.append({'path':rel,'code':before['error']})
                        continue
                    # Transient I/O or runtime failures should not hide a
                    # document forever just because its bytes did not change.
                changed+=1
                if content is None:content=git.read_version(observation['commit'],local)
                for loc,body in self._extract_bytes(content,path.suffix):
                    sid=hashlib.sha256(f'{kind}:{project_id}:{rel}:{loc}'.encode()).hexdigest()[:32]
                    rows.append((sid,rel,kind,project_id,path.name,loc,body,version,now(),status))
            except Exception as e:
                code=e.code if isinstance(e,Error) else 'PARSE_ERROR'
                failures.append({'path':rel,'code':code})
                if before and before['version']==version and before['error']==code:continue
            replacements.append((rel,version,status,code,rows))
        removed=(old|cached.keys())-seen
        if not (replacements or removed or status_updates or signatures):return changed,failures
        with self.connect() as db:
            # No file access or parsing inside this write transaction. A path
            # and its FTS entries are replaced together even if a write fails.
            for rel in sorted(removed):self._delete_path(db,root,rel,rel in old)
            for rel,version,status,code,rows in replacements:
                self._delete_path(db,root,rel,rel in old)
                db.executemany('INSERT INTO sources VALUES (?,?,?,?,?,?,?,?,?,?)',rows)
                db.executemany('INSERT INTO terms VALUES (?,?)',[(r[0],r[6]) for r in rows])
                db.execute('INSERT INTO source_files VALUES (?,?,?,?,?)',(root,rel,version,status,code))
            for status,rel in status_updates:
                db.execute('UPDATE sources SET source_status=? WHERE source_path=?',(status,rel))
                db.execute('UPDATE source_files SET status=? WHERE root=? AND path=?',(status,root,rel))
            db.executemany('INSERT OR REPLACE INTO source_file_stats VALUES (?,?,?)', signatures)
        return changed,failures

    @staticmethod
    def _delete_path(db,root,rel,has_sources):
        if has_sources:
            db.execute('DELETE FROM terms WHERE source_id IN (SELECT source_id FROM sources WHERE source_path=?)',(rel,))
            db.execute('DELETE FROM sources WHERE source_path=?',(rel,))
        db.execute('DELETE FROM source_files WHERE root=? AND path=?',(root,rel))
        db.execute('DELETE FROM source_file_stats WHERE root=? AND path=?',(root,rel))

    def _extract_bytes(self, content, suffix):
        folder=self.w.path('.workspace/index/parse-temp');folder.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix=suffix,dir=folder) as tmp:
            tmp.write(content);tmp.flush()
            return extract(Path(tmp.name))

    def read_project_file(self,project_id,path,commit=None):
        from .git_store import GitStore
        project=self.w.project_for(project_id)
        relative=Path(path)
        if relative.is_absolute() or '..' in relative.parts:raise Error('OUT_OF_ROOT','Project-relative path required')
        target=self.w.path(project['path']+'/'+relative.as_posix())
        git=GitStore(self.w,project_id)
        version=commit
        if commit is not None or git.enabled:
            if version is None:version=git.observe()['commit']
            content=git.read_version(version,relative.as_posix())
        else:
            if not target.is_file():raise Error('NOT_FOUND','Project file missing')
            content=target.read_bytes()
        result={'project_id':project_id,'path':relative.as_posix(),'source_path':project['path']+'/'+relative.as_posix(),'commit':version}
        try:
            chunks=self._extract_bytes(content,target.suffix)
            return {**result,'readability':'readable','chunks':[{'locator':loc,'text':text} for loc,text in chunks],'text':'\n'.join(text for _,text in chunks)}
        except Exception as e:
            return {**result,'readability':'limited' if isinstance(e,Error) and e.code=='PARSE_LIMITED' else 'unsupported' if isinstance(e,Error) and e.code=='PARSE_UNSUPPORTED' else 'error','code':e.code if isinstance(e,Error) else 'PARSE_ERROR','chunks':[],'text':''}

    def compare_project_file(self,project_id,path,before,after=None):
        old=self.read_project_file(project_id,path,before)
        new=self.read_project_file(project_id,path,after)
        return {'before':old,'after':new,'diff':'\n'.join(difflib.unified_diff(old['text'].splitlines(),new['text'].splitlines(),fromfile=before,tofile=new['commit'] or 'working')) if old['readability']==new['readability']=='readable' else None}

    def clause(self,scope):
        self.w.scope(scope)
        clauses=[]; args=[]
        if scope['include_company']: clauses.append("scope_type='company'")
        if scope['include_professional']: clauses.append("scope_type='professional'")
        if scope['project_ids']:
            clauses.append('(scope_type=\'project\' AND project_id IN ('+','.join('?' for _ in scope['project_ids'])+'))')
            args.extend(scope['project_ids'])
        return '('+' OR '.join(clauses or ['0'])+')',args
    def search(self,query,scope,limit=20,strict=False):
        if not isinstance(query,str) or not query.strip() or len(query)>500: raise Error('INVALID_ARGUMENT','Query length must be 1–500')
        clause,args=self.clause(scope)
        if type(strict) is not bool: raise Error('INVALID_ARGUMENT','strict must be a boolean')
        report=self.update(scope, strict=strict)
        with self.connect() as db:
            # Literal matching keeps punctuation and SQL/FTS operators as data.
            if len(query)>=3:
                literal='"'+query.replace('"','""')+'"'
                rows=db.execute('SELECT * FROM sources WHERE '+clause+' AND (source_id IN (SELECT source_id FROM terms WHERE terms MATCH ?) OR instr(lower(title),lower(?))>0) LIMIT ?',args+[literal,query,min(max(int(limit),1),100)]).fetchall()
            else:
                rows=db.execute('SELECT * FROM sources WHERE '+clause+' AND (instr(lower(snippet),lower(?))>0 OR instr(lower(title),lower(?))>0) LIMIT ?',args+[query,query,min(max(int(limit),1),100)]).fetchall()
        return {'matches':[dict(r) for r in rows],'index':report}
    def read(self,source_id,scope):
        clause,args=self.clause(scope)
        with self.connect() as db:
            row=db.execute('SELECT * FROM sources WHERE source_id=? AND '+clause,[source_id]+args).fetchone()
        if row is None: raise Error('NOT_FOUND','Source not found in authorized scope')
        value=dict(row)
        self.w.allowed(value['source_path'],scope)
        path=self.w.path(value['source_path'])
        if value['sha256'].startswith('git:'):
            from .git_store import GitStore
            git=GitStore(self.w,value['project_id']);observation=git.observe()
            project=self.w.project_for(value['project_id'])
            local=Path(value['source_path']).relative_to(project['path']).as_posix()
            if observation['files'].get(local)!=value['sha256'][4:]:raise Error('STALE_INDEX','Original changed; search again')
            chunks=dict(self._extract_bytes(git.read_version(observation['commit'],local),path.suffix))
        else:
            try:
                content=path.read_bytes()
            except FileNotFoundError:
                raise Error('STALE_INDEX','Original changed; update index and search again') from None
            if hashlib.sha256(content).hexdigest()!=value['sha256']:
                raise Error('STALE_INDEX','Original changed; update index and search again')
            chunks=dict(self._extract_bytes(content,path.suffix))
        if value['locator'] not in chunks: raise Error('STALE_INDEX','Locator no longer exists')
        value['snippet']=chunks[value['locator']]
        return value
