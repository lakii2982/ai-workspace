from __future__ import annotations
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unicodedata

class Error(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)

def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.aiws-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
        d = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(d)
        finally: os.close(d)
    finally:
        if os.path.exists(name): os.unlink(name)

def put_json(path, value):
    atomic(path, (json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode())

def load(path):
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise Error('INVALID_DATA', f'Cannot read {Path(path).name}: {type(e).__name__}') from e
    if not isinstance(value, dict) or value.get('schema_version') != 1:
        raise Error('SCHEMA_UNSUPPORTED', 'Expected schema_version 1')
    return value

class Workspace:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.state = self.root / '.workspace'
        self._projects_cache = None
        self._project_lookup = {}

    def path(self, relative):
        p = Path(relative)
        if p.is_absolute() or '..' in p.parts:
            raise Error('OUT_OF_ROOT', 'Use a relative path without parent traversal')
        cursor = self.root
        for part in p.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise Error('OUT_OF_ROOT', 'Symbolic links are not allowed in managed paths')
        resolved = (self.root / p).resolve()
        if not resolved.is_relative_to(self.root):
            raise Error('OUT_OF_ROOT', 'Path escapes workspace')
        return resolved

    @contextlib.contextmanager
    def lock(self, key):
        folder = self.path('.workspace/locks')
        folder.mkdir(parents=True, exist_ok=True)
        name = hashlib.sha256(key.encode()).hexdigest()
        with open(folder / name, 'a') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try: yield
            finally: fcntl.flock(f, fcntl.LOCK_UN)

    def init(self):
        for p in ['公司知识/公司档案','公司知识/产品与技术','公司知识/经营管理','公司知识/规章制度','公司知识/公司资质','专业知识/项目管理','专业知识/供应链管理','专业知识/招投标','专业知识/经营分析','专业知识/文档模板','项目','跨项目专题','待整理'] + ['.workspace/'+p for p in ['registry','index','jobs','locks','history','logs','backups']]:
            self.path(p).mkdir(parents=True, exist_ok=True)
        with self.lock('config'):
            p = self.path('.workspace/config.json')
            if not p.exists(): put_json(p, {'schema_version':1,'name':'AI WorkSpace','created_at':now()})
            else: load(p)
        return {'workspace':str(self.root),'schema_version':1}

    def projects(self):
        if self._projects_cache is not None:return self._projects_cache
        from .remote_store import enabled, RemoteStore
        if enabled(self):
            self._projects_cache=RemoteStore(self).projects()
            return self._projects_cache
        result, seen = [], set()
        for meta in sorted(self.path('项目').rglob('.project.json')):
            meta = self.path(meta.relative_to(self.root))
            data = load(meta)
            pid = data.get('project_id')
            if not isinstance(pid,str) or not pid.startswith('PRJ-') or pid in seen:
                raise Error('DUPLICATE_PROJECT','Invalid or duplicate project identity')
            seen.add(pid)
            result.append({**data,'path':meta.parent.relative_to(self.root).as_posix()})
        self._projects_cache=result
        return result

    def project_for(self,project_id):
        if project_id in self._project_lookup:return self._project_lookup[project_id]
        from .remote_store import enabled,RemoteStore
        if enabled(self):
            remote=RemoteStore(self);row=remote.read_row('projects',project_id)
            if not row:raise Error('INVALID_SCOPE','Unknown project')
            value=remote.decode('projects',row)
        else:
            value=next((p for p in self.projects() if p['project_id']==project_id),None)
            if not value:raise Error('INVALID_SCOPE','Unknown project')
        self._project_lookup[project_id]=value
        return value

    def project_query(self,query='',limit=20,offset=0):
        if type(limit) is not int or not 1<=limit<=100 or type(offset) is not int or offset<0:raise Error('INVALID_ARGUMENT','Invalid pagination')
        from .remote_store import enabled,RemoteStore
        if enabled(self):
            remote=RemoteStore(self)
            conditions=[] if not query else [[('项目ID' if query.startswith('PRJ-') else '项目名称'),'intersects',query]]
            page=remote.transport.rows('projects',conditions=conditions,limit=limit,offset=offset,projection=['项目ID','项目名称','状态','项目目录'])
            return {'items':[{'project_id':r['项目ID'],'name':r['项目名称'],'status':{'待启动':'new','进行中':'active','暂停':'paused','已完成':'completed','已归档':'archived'}.get((r.get('状态') or [''])[0],'unknown'),'path':r['项目目录']} for r in page['items']], 'has_more':page['has_more'],'next_offset':offset+limit if page['has_more'] else None}
        rows=[p for p in self.projects() if not query or any(query.casefold() in x.casefold() for x in [p['project_id'],p['name']]+p.get('aliases',[]))]
        return {'items':[{k:p[k] for k in ('project_id','name','status','path')} for p in rows[offset:offset+limit]],'has_more':len(rows)>offset+limit,'next_offset':offset+limit if len(rows)>offset+limit else None}

    def project_create(self, name):
        if not isinstance(name,str) or not name.strip() or '/' in name or '\\' in name or name in ('.','..'):
            raise Error('INVALID_NAME','Project name must be one directory component')
        name = unicodedata.normalize('NFC',name.strip())
        with self.lock('projects'):
            projects = self.projects()
            if any(p['name']==name for p in projects):raise Error('CONFLICT','Project name exists')
            target = self.path('项目/'+name)
            if target.exists(): raise Error('CONFLICT','Project directory already exists')
            pid = 'PRJ-'+str(max([int(p['project_id'][4:]) for p in projects]+[0])+1).zfill(4)
            target = self.path('项目/'+pid+'_'+name)
            if target.exists():raise Error('CONFLICT','Project directory already exists')
            target.mkdir()
            for sub in ['资料','工作','成果','记忆']: (target/sub).mkdir()
            data = {'schema_version':1,'project_id':pid,'name':name,'aliases':[],'status':'active'}
            put_json(target/'.project.json',data)
            atomic(target/'记忆/TIMELINE.md',f'# {name} 时间线\n\n项目编号：{pid}\n'.encode())
            atomic(target/'PROJECT.md',f'# {name}\n\n项目编号：{pid}\n\n## 背景\n待补充\n\n## 目标\n待补充\n\n## 范围\n本项目资料与成果\n\n## 关键角色\n本地用户：确认与验收\n\n## 当前里程碑\n已立项\n\n## 有效决策\n暂无\n\n## 待确认事项\n暂无\n\n## 权威资料目录\n资料/\n'.encode())
        from .remote_store import enabled, RemoteStore
        if enabled(self):RemoteStore(self).write('projects',pid,{**data,'path':target.relative_to(self.root).as_posix()})
        self._projects_cache=None
        return next(p for p in self.projects() if p['project_id']==pid)

    def scope(self, scope):
        required = {'include_company','include_professional','project_ids','topic_ids'}
        if not isinstance(scope,dict) or set(scope)!=required:
            raise Error('INVALID_SCOPE','Explicit complete scope required')
        if any(type(scope[k]) is not bool for k in ['include_company','include_professional']) or any(not isinstance(scope[k],list) or any(not isinstance(x,str) for x in scope[k]) for k in ['project_ids','topic_ids']):
            raise Error('INVALID_SCOPE','Incorrect scope field types')
        for pid in scope['project_ids']:self.project_for(pid)
        if scope['topic_ids']: raise Error('INVALID_SCOPE','V1 topic registration not enabled; use explicit project scopes')
        return scope

    def classify(self, relative):
        parts = Path(relative).parts
        if parts[0]=='公司知识': return 'company',None,'original'
        if parts[0]=='专业知识': return 'professional',None,'original'
        if len(parts)>=3 and parts[0]=='项目':
            meta=self.path(str(Path(*parts[:2])/'.project.json'))
            if meta.exists():
                p=self.project_for(load(meta)['project_id'])
                if not Path(relative).is_relative_to(p['path']):raise Error('INVALID_SCOPE','Project path drift')
                status='draft' if '工作' in parts else 'archived' if '归档' in parts else 'original'
                return 'project',p['project_id'],status
        raise Error('INVALID_SCOPE','Not a registered knowledge location')

    def allowed(self, relative, scope, drafts=False):
        self.path(relative)
        kind,pid,status = self.classify(relative)
        ok = (kind=='company' and scope['include_company']) or (kind=='professional' and scope['include_professional']) or (kind=='project' and pid in scope['project_ids'])
        if not ok or (status=='draft' and not drafts): raise Error('INVALID_SCOPE','Source outside permitted scope')
        return kind,pid,status
