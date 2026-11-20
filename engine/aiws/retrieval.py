import contextlib
import hashlib
import sqlite3
from pathlib import Path
import tempfile
import difflib
from .core import Error, digest, now, load

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
        formulas=load_workbook(path,read_only=True,data_only=False)
        values=load_workbook(path,read_only=True,data_only=True)
        out=[]
        try:
            for sheet in formulas:
                for row in sheet:
                    for cell in row:
                        if cell.value is not None:
                            text=str(cell.value)
                            if cell.data_type=='f': text+=f' [cached={values[sheet.title][cell.coordinate].value}; cache may be stale; not recalculated]'
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
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE IF NOT EXISTS sources (source_id TEXT PRIMARY KEY, source_path TEXT, scope_type TEXT, project_id TEXT, title TEXT, locator TEXT, snippet TEXT, sha256 TEXT, indexed_at TEXT, source_status TEXT)')
        db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS terms USING fts5(source_id UNINDEXED, snippet, tokenize='trigram')")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
    def update(self, scope=None):
        """Refresh only selected roots; Git projects reuse parsed rows by blob identity."""
        if scope is not None:self.w.scope(scope)
        projects = [p for p in self.w.projects() if scope is None or p['project_id'] in scope['project_ids']]
        roots = []
        if scope is None or scope['include_company']:roots.append(('公司知识', None))
        if scope is None or scope['include_professional']:roots.append(('专业知识', None))
        roots += [(p['path'],p['project_id']) for p in projects]
        changed=0;failures=[]
        approvals={}
        for event in self.w.path('.workspace/history/events').glob('*.json'):
            record=load(event)
            if record.get('phase')=='committed' and record.get('confirmed'):
                approvals.setdefault(record['path'],set()).add(record['new_hash'])
        with self.w.lock('index'):
            with self.connect() as db:
                db.execute('CREATE TABLE IF NOT EXISTS git_files (project_id TEXT, path TEXT, oid TEXT, error TEXT, PRIMARY KEY(project_id,path))')
                for root,pid in roots:
                    git=None;observation=None
                    if pid:
                        from .git_store import GitStore
                        git=GitStore(self.w,pid)
                        if git.enabled:observation=git.observe()
                    old={}
                    for row in db.execute('SELECT * FROM sources WHERE substr(source_path,1,?)=?', (len(root)+1,root+'/',)):
                        old.setdefault(row['source_path'],[]).append(dict(row))
                    if observation is not None:
                        candidates=[(root+'/'+path,'git:'+oid,path) for path,oid in observation['files'].items()]
                    else:
                        candidates=[(p.relative_to(self.w.root).as_posix(),None,None) for p in sorted(self.w.path(root).rglob('*')) if p.is_file()]
                    rows=[];gitrows=[]
                    previous_git={r['path']:dict(r) for r in db.execute('SELECT * FROM git_files WHERE project_id=?',(pid,))} if pid else {}
                    for rel,version,local in candidates:
                        if any(x.startswith('.') or x.startswith('~$') for x in Path(rel).parts) or '工作' in Path(rel).parts:continue
                        try:
                            path=self.w.path(rel);kind,project_id,status=self.w.classify(rel)
                            version=version or digest(path)
                            previous=old.get(rel,[])
                            if rel in approvals:
                                # Existing publication approval is SHA-based; Git still decides changes.
                                approved_bytes=git.read_version(observation['commit'],local) if observation is not None else path.read_bytes()
                                if hashlib.sha256(approved_bytes).hexdigest() in approvals[rel]:status='approved'
                            saved=previous_git.get(local)
                            if saved and 'git:'+saved['oid']==version and saved['error']:
                                failures.append({'path':rel,'code':saved['error']});gitrows.append((pid,local,version[4:],saved['error']));continue
                            if previous and previous[0]['sha256']==version:
                                chunks=[(x['locator'],x['snippet']) for x in previous]
                            else:
                                changed+=1
                                if observation is not None:chunks=self._extract_bytes(git.read_version(observation['commit'],local),path.suffix)
                                else:chunks=extract(path)
                            for loc,body in chunks:
                                sid=hashlib.sha256(f'{kind}:{project_id}:{rel}:{loc}'.encode()).hexdigest()[:32]
                                rows.append((sid,rel,kind,project_id,path.name,loc,body,version,now(),status))
                            if local is not None:gitrows.append((pid,local,version[4:],None))
                        except Exception as e:
                            code=e.code if isinstance(e,Error) else 'PARSE_ERROR'
                            failures.append({'path':rel,'code':code})
                            if local is not None:gitrows.append((pid,local,version[4:],code))
                    ids=[r[0] for r in db.execute('SELECT source_id FROM sources WHERE substr(source_path,1,?)=?', (len(root)+1,root+'/',))]
                    db.executemany('DELETE FROM terms WHERE source_id=?',[(x,) for x in ids])
                    db.execute('DELETE FROM sources WHERE substr(source_path,1,?)=?', (len(root)+1,root+'/',))
                    db.executemany('INSERT INTO sources VALUES (?,?,?,?,?,?,?,?,?,?)',rows)
                    db.executemany('INSERT INTO terms VALUES (?,?)',[(r[0],r[6]) for r in rows])
                    if pid:
                        db.execute('DELETE FROM git_files WHERE project_id=?',(pid,))
                        db.executemany('INSERT INTO git_files VALUES (?,?,?,?)',gitrows)
                count=db.execute('SELECT count(*) FROM sources').fetchone()[0]
        return {'sources':count,'changed_files':changed,'failures':failures}

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
    def search(self,query,scope,limit=20):
        if not isinstance(query,str) or not query.strip() or len(query)>500: raise Error('INVALID_ARGUMENT','Query length must be 1–500')
        clause,args=self.clause(scope)
        report=self.update(scope)
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
            if not path.exists() or digest(path)!=value['sha256']: raise Error('STALE_INDEX','Original changed; update index and search again')
            chunks=dict(extract(path))
        if value['locator'] not in chunks: raise Error('STALE_INDEX','Locator no longer exists')
        value['snippet']=chunks[value['locator']]
        return value
