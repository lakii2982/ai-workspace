import hashlib
from pathlib import Path
from .core import Error, atomic, digest, load, now, put_json

STATES={'new','in_progress','review','completed','blocked','cancelled'}
TEXT_LISTS=['acceptance','completed','next_steps','open_issues','decisions']
REQUIRED=('job_id','title','goal','status','last_agent','updated_at','scope','revision',
          'acceptance','completed','next_steps','open_issues','decisions','artifacts','sources')
class Jobs:
    def __init__(self,w):self.w=w
    @staticmethod
    def validate_context(context):
        allowed={'working_on','resume_note','working_files','memory_refs','reviewed_files',
                 'base_commit','checkpoint_commit','checkpoint_ref'}
        if not isinstance(context,dict) or not set(context)<=allowed:
            raise Error('INVALID_ARGUMENT','Invalid context fields')
        for key in ('working_on','resume_note','checkpoint_commit','checkpoint_ref'):
            if key in context and not isinstance(context[key],str):
                raise Error('INVALID_ARGUMENT','Context text must be a string')
        if context.get('base_commit') is not None and not isinstance(context['base_commit'],str):
            raise Error('INVALID_ARGUMENT','Invalid base commit')
        for key in ('working_files','reviewed_files'):
            if key in context:
                if not isinstance(context[key],list):raise Error('INVALID_ARGUMENT','Expected file list')
                for item in context[key]:
                    if not isinstance(item,dict) or not isinstance(item.get('path'),str):
                        raise Error('INVALID_ARGUMENT','Expected file path')
                    if key=='reviewed_files' and not isinstance(item.get('observation_commit'),str):
                        raise Error('INVALID_ARGUMENT','Expected read version')
        if 'memory_refs' in context and (not isinstance(context['memory_refs'],list) or any(not isinstance(x,str) for x in context['memory_refs'])):
            raise Error('INVALID_ARGUMENT','Expected memory event IDs')
    def file(self,jid):
        if not isinstance(jid,str) or not jid.startswith('JOB-') or not jid[4:].isdigit(): raise Error('INVALID_ARGUMENT','Invalid job ID')
        return self.w.path(f'.workspace/jobs/{jid}/job.json')
    def validate(self,j):
        if not isinstance(j,dict):raise Error('INVALID_DATA','Job record must be an object')
        missing=[k for k in REQUIRED if k not in j]
        if missing:raise Error('INVALID_DATA','Missing job fields: '+', '.join(missing))
        self.validate_context(j.get('context',{}))
        if j.get('schema_version')!=1: raise Error('SCHEMA_UNSUPPORTED','Unsupported job schema')
        self.file(j['job_id']);self.w.scope(j['scope'])
        if j.get('status') not in STATES or type(j.get('revision')) is not int or j['revision']<1: raise Error('INVALID_DATA','Invalid state/revision')
        for k in ['title','goal','last_agent','updated_at']:
            if not isinstance(j.get(k),str) or not j[k]: raise Error('INVALID_DATA',f'Invalid {k}')
        for k in TEXT_LISTS:
            if not isinstance(j.get(k),list) or any(not isinstance(x,str) for x in j[k]):raise Error('INVALID_DATA',f'Invalid {k}')
        for key,fields in [('artifacts',['path','hash','purpose','status']),('sources',['source_id','locator','hash'])]:
            if not isinstance(j.get(key),list):raise Error('INVALID_DATA',f'Invalid {key}')
            for item in j[key]:
                if not isinstance(item,dict) or any(not isinstance(item.get(f),str) for f in fields):raise Error('INVALID_DATA',f'Invalid {key} record')
                if key=='artifacts':self.w.allowed(item['path'],j['scope'],drafts=True)
        return j
    def scan(self):
        """Load every job, isolating unreadable records instead of failing the caller."""
        jobs, issues = [], []
        for path in sorted(self.w.path('.workspace/jobs').glob('JOB-*/job.json')):
            jid = path.parent.name
            relative = path.relative_to(self.w.root).as_posix()
            try:
                jobs.append(self.get(jid))
            except Error as e:
                issues.append(dict(job_id=jid, path=relative, code=e.code, message=e.message))
            except Exception as e:
                issues.append(dict(job_id=jid, path=relative, code='INTERNAL_ERROR',
                                   message=type(e).__name__))
        return jobs, issues
    # Strict on purpose: migration and full dumps must fail loudly on corrupt data.
    def list(self): return [self.get(p.parent.name) for p in sorted(self.w.path('.workspace/jobs').glob('JOB-*/job.json'))]
    def get(self,jid):
        from .remote_store import enabled, RemoteStore
        if enabled(self.w):
            self.file(jid)
            return self.validate(RemoteStore(self.w).job(jid))
        p=self.file(jid)
        if not p.exists():raise Error('NOT_FOUND','Job not found')
        return self.validate(load(p))
    def persist(self,j):
        self.validate(j)
        from .remote_store import enabled, RemoteStore
        if enabled(self.w):RemoteStore(self.w).write_job(j,j['revision']-1)
        folder=self.file(j['job_id']).parent
        # Snapshot first; job.json is the commit point. Extra uncommitted snapshots are harmless.
        put_json(folder/'checkpoints'/f'{j["revision"]:08}.json',j)
        put_json(folder/'job.json',j)
        self.handoff(j)
        return j
    def handoff(self,j):
        import json
        view={k:j[k] for k in ('job_id','title','status','revision')}
        view['next_steps']=[x[:500] for x in j['next_steps'][:5]]
        view['open_issues']=[x[:500] for x in j['open_issues'][:5]]
        view['recover']='aiws job resume --params '+json.dumps({'jid':j['job_id']})
        text='# Task handoff\n\nDerived; use resume for current authority.\n\n'+json.dumps(view,ensure_ascii=False,indent=2)+'\n'
        atomic(self.file(j['job_id']).parent/'HANDOFF.md',text.encode())
    def create(self,title,goal,scope,acceptance=None,agent='codex',request_id=None):
        self.w.scope(scope)
        from .remote_store import enabled
        if enabled(self.w) and (not scope['project_ids'] or not request_id):raise Error('INVALID_ARGUMENT','Project scope and stable request_id required')
        with self.w.lock('jobs-create'):
            request_file=None
            if request_id is not None:
                if not isinstance(request_id,str) or not request_id:raise Error('INVALID_ARGUMENT','Invalid request ID')
                payload=dict(title=title,goal=goal,scope=scope,acceptance=acceptance or [],agent=agent)
                request_file=self.w.path('.workspace/create-requests/'+hashlib.sha256(request_id.encode()).hexdigest()+'.json')
                if request_file.exists():
                    prior=load(request_file)
                    if prior['payload']!=payload:raise Error('CONFLICT','Request ID reused')
                    return self.get(prior['jid'])
            ids=[int(p.name[4:]) for p in self.w.path('.workspace/jobs').glob('JOB-*') if p.is_dir() and p.name[4:].isdigit()]
            jid='JOB-'+str(max(ids+[0])+1).zfill(4)
            if request_file:put_json(request_file,dict(schema_version=1,payload=payload,jid=jid))
            j=dict(schema_version=1,job_id=jid,title=title,goal=goal,acceptance=acceptance or [],status='new',scope=scope,revision=1,last_agent=agent,completed=[],next_steps=[],open_issues=[],decisions=[],artifacts=[],sources=[],updated_at=now())
            j['directory_name']=jid+'_'+''.join(c if c not in '/\\\n\r' else '_' for c in title)[:60]
            # A remote row can become visible before persist() finishes the
            # local mirror. Guard the allocation's new task through that point.
            with self.w.lock('service-job:'+jid):
                return self.persist(j)
    def unpromoted(self,j):
        """工作稿有文件但成果目录为空的分组；完成前必须移交成果或降级为过程材料。"""
        pending=[]
        for pid in j['scope']['project_ids']:
            project=self.w.project_for(pid);name=j.get('directory_name',j['job_id'])
            drafts=self.w.path(project['path']+'/工作/'+name+'/工作稿')
            if not drafts.is_dir():continue
            files=[x for x in drafts.rglob('*') if x.is_file() and x.stat().st_size>0]
            if not files:continue
            done=self.w.path(project['path']+'/成果/'+name)
            delivered=[] if not done.is_dir() else [x for x in done.rglob('*') if x.is_file() and '历史版本' not in x.relative_to(done).parts]
            if delivered:continue
            pending.append({'project_id':pid,'work':drafts.relative_to(self.w.root).as_posix(),'deliverables':done.relative_to(self.w.root).as_posix(),'files':[x.relative_to(drafts).as_posix() for x in files[:10]],'count':len(files)})
        return pending
    @staticmethod
    def verification_shape(checks,acceptance):
        """Shape rules shared by every checkpoint; files are read only on completion."""
        if not isinstance(checks,list) or len(checks)!=len(acceptance):
            raise Error('ACCEPTANCE_REQUIRED','One evidence record per acceptance criterion required')
        for criterion,check in zip(acceptance,checks):
            if not isinstance(check,dict) or check.get('criterion')!=criterion:
                raise Error('ACCEPTANCE_REQUIRED','Evidence must name its acceptance criterion')
            path,hashed=check.get('path'),check.get('hash')
            if bool(path)!=bool(hashed):
                raise Error('ACCEPTANCE_REQUIRED','Evidence path and hash must be given together')
            if path is not None and (not isinstance(path,str) or not isinstance(hashed,str)):
                raise Error('ACCEPTANCE_REQUIRED','Invalid evidence reference')
        return checks
    def checkpoint(self,jid,revision,patch,reopen=False,lease_token=None):
        allowed={'title','goal','acceptance','status','last_agent','completed','next_steps','open_issues','decisions','verification','context'}
        if not isinstance(patch,dict) or not set(patch)<=allowed:
            managed=set(patch)&{'artifacts','sources'} if isinstance(patch,dict) else set()
            if managed:
                raise Error('INVALID_ARGUMENT',f'{", ".join(sorted(managed))} is managed by save_artifact/publish_file; remove it from the checkpoint patch')
            raise Error('INVALID_ARGUMENT','Unknown or immutable job fields')
        with self.w.lock('job:'+jid):
            j=self.get(jid)
            from .workflow import check_lease
            from .remote_store import enabled
            check_lease(j,lease_token,require=enabled(self.w))
            if j['revision']!=revision:raise Error('CONFLICT','Job revision changed')
            new=patch.get('status',j['status'])
            acceptance=patch.get('acceptance',j['acceptance'])
            if 'verification' in patch:
                self.verification_shape(patch['verification'],acceptance)
            transitions={'new':{'new','in_progress','blocked'},'in_progress':{'in_progress','review','blocked'},'review':{'review','in_progress','completed','blocked'},'blocked':{'blocked','in_progress','review'},'completed':{'completed'}}
            if new=='cancelled' and j['status'] not in ('completed','cancelled'):transitions[j['status']].add('cancelled')
            transitions['cancelled']={'cancelled'}
            if new not in transitions[j['status']] and not (reopen and j['status']=='completed' and new=='in_progress'):raise Error('INVALID_STATE','Invalid transition; completed jobs require explicit reopen')
            if new=='completed' and j['status']!='completed':
                if not acceptance or patch.get('open_issues',j['open_issues']):raise Error('ACCEPTANCE_REQUIRED','Acceptance criteria and resolved blockers required')
                pending=self.unpromoted(j)
                if pending:
                    p=pending[0]
                    raise Error('DELIVERABLE_REQUIRED',f"工作稿仍有{p['count']}份文件而成果目录为空：{p['work']}；定稿用 publish_file 发布到 {p['deliverables']}，不交付的移入过程材料")
                checks=patch.get('verification',j.get('verification',[]))
                self.verification_shape(checks,acceptance)
                for check in checks:
                    if not check.get('path'):continue
                    self.w.allowed(check['path'],j['scope'],drafts=True)
                    if digest(self.w.path(check['path']))!=check['hash']:raise Error('CONFLICT','Verification evidence changed')
                delivered={a['hash'] for a in j['artifacts'] if a['purpose']=='deliverable' and a['status']=='approved'}
                if delivered and not any(c.get('hash') in delivered for c in checks):
                    raise Error('ACCEPTANCE_REQUIRED','Evidence must reference a published deliverable')
            j.update(patch)
            if new in ('completed','cancelled'):j['lease']={}
            j['revision']+=1;j['updated_at']=now()
            return self.persist(j)

    def record_feedback(self, jid, feedback, lease_token=None):
        """Keep a user instruction on this task without promoting it to project memory."""
        if not isinstance(feedback, str) or not feedback.strip() or len(feedback) > 2000:
            raise Error('INVALID_ARGUMENT', 'Task feedback must be 1..2000 characters')
        with self.w.lock('job:'+jid):
            j=self.get(jid)
            from .workflow import check_lease
            from .remote_store import enabled
            check_lease(j, lease_token, require=enabled(self.w))
            text=feedback.strip()
            if text in j['decisions']:
                return dict(job_id=jid, feedback=text, scope='task', revision=j['revision'], replayed=True)
            j['decisions'].append(text)
            j['revision']+=1;j['updated_at']=now();self.persist(j)
            return dict(job_id=jid, feedback=text, scope='task', revision=j['revision'])
    def resume(self,jid,detail="brief"):
        return self.resume_record(self.get(jid), detail)

    def resume_record(self,j,detail="brief"):
        if detail not in ("brief","full"):raise Error("INVALID_ARGUMENT","detail must be brief or full")
        if detail=="brief":return self.brief_record(j)
        verified=[]
        for a in j['artifacts']:
            p=self.w.path(a['path'])
            try:
                matches=p.is_file() and digest(p)==a['hash']
            except FileNotFoundError:
                matches=False
            verified.append({**a,'integrity':'ok' if matches else 'changed_or_missing'})
        return {**j,'artifact_integrity':verified}

    def brief(self,jid):
        return self.brief_record(self.get(jid))

    def brief_record(self,j):
        jid=j['job_id']
        fields=['job_id','title','goal','status','revision','scope','updated_at','last_agent','acceptance','next_steps','open_issues','decisions']
        result={k:j[k] for k in fields}
        omitted={}
        for k,v in list(result.items()):
            if isinstance(v,str) and len(v)>800:
                result[k]=v[:800];omitted[k]='text truncated'
            elif isinstance(v,list):
                result[k]=[x[:500] for x in v[:8]]
                if len(v)>8 or any(len(x)>500 for x in v):omitted[k]='additional content available'
        result['completed']= [x[:300] for x in j['completed'][-3:]]
        if len(j['completed'])>3 or any(len(x)>300 for x in j['completed']):omitted['completed']='additional content available'
        result['lease']={k:v for k,v in j.get('lease',{}).items() if k!='token'}
        result['artifact_count']=len(j['artifacts'])
        result['integrity']='not_checked; use detail=full for full verification'
        result['omitted']=omitted
        result['expand']={'operation':'resume_job','jid':jid,'detail':'full'}
        result['directories']=[{'project_id':p['project_id'],'work':p['path']+'/工作/'+j.get('directory_name',jid),'deliverables':p['path']+'/成果/'+j.get('directory_name',jid)} for p in [self.w.project_for(pid) for pid in j['scope']['project_ids']]]
        return result

    def query(self,project_id=None,query='',status='active',limit=20,offset=0):
        from .remote_store import enabled, RemoteStore
        if enabled(self.w):return RemoteStore(self.w).query(project_id,query,status,limit,offset)
        if type(limit) is not int or not 1<=limit<=100 or type(offset) is not int or offset<0:raise Error('INVALID_ARGUMENT','Invalid pagination')
        if project_id is not None and project_id not in {p['project_id'] for p in self.w.projects()}:raise Error('INVALID_SCOPE','Unknown project')
        rows=[]; jobs, issues = self.scan()
        for j in jobs:
            if project_id and project_id not in j['scope']['project_ids']:continue
            if status=='active' and j['status'] in ('completed','cancelled'):continue
            if status and status!='active' and j['status']!=status:continue
            if query and query.casefold() not in (j['job_id']+' '+j['title']+' '+j['goal']).casefold():continue
            rows.append({k:j[k] for k in ['job_id','title','status','revision','updated_at']})
        page=rows[offset:offset+limit]
        for row in page:row['title']=row['title'][:160]
        return {'items':page,'has_more':offset+limit<len(rows),'next_offset':offset+limit if offset+limit<len(rows) else None,
                'skipped':[{'job_id':i['job_id'],'code':i['code']} for i in issues]}

class Artifacts:
    def __init__(self,w):self.w=w;self.jobs=Jobs(w)
    def save(self,jid,relative,content,expected_hash=None,confirm=False,purpose='draft',lease_token=None,allow_closed=False,receive=False,source_ref=None):
        j=self.jobs.get(jid)
        from .workflow import check_lease
        from .remote_store import enabled
        # 已关闭任务无人持有、也不会被并发编辑：仅允许有确认留痕的"事后补发布"（存量迁移）跳过租约。
        closed=allow_closed and j['status'] in ('completed','cancelled')
        check_lease(j,lease_token,require=enabled(self.w) and not closed)
        kind,pid,status=self.w.allowed(relative,j['scope'],drafts=True)
        parts=Path(relative).parts
        if kind=='project':
            project=self.w.project_for(pid)
            sub=Path(relative).relative_to(project['path']).parts
            if not sub or sub[0] not in ('资料','工作','成果'):raise Error('INVALID_SCOPE','Project metadata is tool-managed')
            if sub[0]=='工作' and (len(sub)<3 or sub[1] not in (jid,j.get('directory_name',jid))):raise Error('INVALID_SCOPE','Draft must belong to this job')
        formal=kind!='project' or '工作' not in parts
        if receive and (kind!='project' or sub[0]!='资料' or purpose!='source'):
            raise Error('INVALID_SCOPE','Attachments belong in the selected project sources')
        if formal and not receive and not confirm:raise Error('CONFIRMATION_REQUIRED','Explicit confirmation required to publish formal knowledge')
        relative=self.w.path(relative).relative_to(self.w.root).as_posix()
        if not isinstance(content,bytes):raise Error('INVALID_ARGUMENT','Artifact content must be bytes')
        with self.w.lock('file:'+relative):
            target=self.w.path(relative)
            actual=digest(target) if target.exists() else None
            incoming=hashlib.sha256(content).hexdigest()
            if receive and actual==incoming:
                original={'path':relative,'hash':incoming,'purpose':'source','status':'original'}
                with self.w.lock('job:'+jid):
                    latest=self.jobs.get(jid)
                    if original not in latest['artifacts']:
                        latest['artifacts']=[a for a in latest['artifacts'] if a['path']!=relative]+[original]
                        latest['revision']+=1;latest['updated_at']=now();self.jobs.persist(latest)
                return {**original,'replayed':True}
            if receive and actual is not None and not confirm:
                raise Error('CONFIRMATION_REQUIRED','Confirm replacement of the previous source version')
            if actual!=expected_hash:
                conflict=self.w.path(f'.workspace/history/conflicts/{jid}/{hashlib.sha256(content).hexdigest()}')
                atomic(conflict,content)
                raise Error('CONFLICT','File changed; incoming revision preserved in history/conflicts')
            artifact={'path':relative,'hash':incoming,'purpose':purpose,
                      'status':'original' if receive else 'approved' if formal else 'draft'}
            # Keep enough intent in the durable journal to repair a crash after
            # the bytes are committed but before job.json can be advanced.
            event={'schema_version':1,'job_id':jid,'path':relative,'old_hash':actual,'new_hash':artifact['hash'],
                   'confirmed':confirm and not receive,'actor':'local-user' if confirm else j['last_agent'],
                   'time':now(),'artifact':artifact}
            if receive and source_ref is not None:event['source_ref']=source_ref
            event_id=hashlib.sha256((relative+now()).encode()).hexdigest()
            if target.exists():
                atomic(self.w.path(f'.workspace/history/files/{actual}'),target.read_bytes())
                if kind=='project' and sub[0]=='成果':
                    archive=target.parent/'历史版本'/(target.stem+'_'+actual[:12]+target.suffix)
                    atomic(archive,target.read_bytes())
            put_json(self.w.path(f'.workspace/history/events/{event_id}.json'),{**event,'phase':'prepared'})
            recovery_key=hashlib.sha256(relative.encode()).hexdigest()
            recovery_record=self.w.path(f'.workspace/recovery/{jid}/{recovery_key}.json')
            put_json(recovery_record,{**event,'phase':'prepared'})
            atomic(target,content)
            put_json(self.w.path(f'.workspace/history/events/{event_id}.json'),{**event,'phase':'committed'})
            artifact={**artifact,'hash':digest(target)}
            with self.w.lock('job:'+jid):
                latest=self.jobs.get(jid)
                latest['artifacts']=[a for a in latest['artifacts']
                                     if Path(a['path']).as_posix()!=relative]+[artifact]
                latest['revision']+=1;latest['updated_at']=now();self.jobs.persist(latest)
            recovery_record.unlink(missing_ok=True)
            return {**artifact,'replayed':False} if receive else artifact

    def _classify_recovery(self, job, event):
        """Classify an intent without changing bytes or job state."""
        artifact=event.get('artifact')
        if not isinstance(artifact,dict) or set(artifact)!={'path','hash','purpose','status'} or artifact['path']!=event.get('path'):
            return None, 'invalid_journal'
        try:
            self.w.allowed(artifact['path'],job['scope'],drafts=True)
            target=self.w.path(artifact['path'])
            actual=digest(target) if target.is_file() else None
        except Error:
            return artifact, 'outside_scope'
        current=next((a for a in job['artifacts'] if a['path']==artifact['path']),None)
        if current == artifact:
            return artifact, 'completed'
        if actual == artifact['hash']:
            # The disk has the intended version.  A missing record or the
            # recorded old version are the only safe metadata repairs.
            if current is None or current.get('hash') == event.get('old_hash'):
                return artifact, 'recoverable'
            return artifact, 'metadata_conflict'
        if actual == event.get('old_hash') and (current is None or current.get('hash') == actual):
            return artifact, 'not_written'
        return artifact, 'changed_or_missing'

    def recovery_status(self, jid, job=None):
        """Read only this job's recovery journal for normal resume."""
        self.jobs.file(jid)
        folder=self.w.path(f'.workspace/recovery/{jid}')
        records=[] if not folder.is_dir() else [load(path) for path in sorted(folder.glob('*.json'))]
        job=job or self.jobs.get(jid)
        recoverable=[];pending=[]
        for event in records:
            if event.get('job_id')!=jid:
                continue
            artifact,state=self._classify_recovery(job,event)
            if state in ('completed','not_written'):continue
            item={'path':artifact.get('path') if artifact else event.get('path'),'state':state}
            (recoverable if state=='recoverable' else pending).append(item)
        return {'recoverable':recoverable,'pending':pending}

    def recover(self, jid, lease_token=None):
        """Backfill artifact metadata for a committed, intact local file.

        Recovery never writes business bytes.  It only accepts journal entries
        emitted by :meth:`save`, validates the current task scope, and leaves
        a manually changed file as an actionable pending item.
        """
        from .remote_store import enabled
        from .workflow import check_lease
        self.jobs.file(jid)
        with self.w.lock('job:'+jid):
            job=self.jobs.get(jid)
            check_lease(job,lease_token,require=enabled(self.w))
            recovered=[];pending=[]
            recovery_folder=self.w.path(f'.workspace/recovery/{jid}')
            # New saves have a job-local journal so resume is bounded.  The
            # historical fallback is only used by an explicit repair command.
            event_paths=(sorted(recovery_folder.glob('*.json')) if recovery_folder.is_dir()
                         else sorted(self.w.path('.workspace/history/events').glob('*.json')))
            for event_path in event_paths:
                event=load(event_path)
                if event.get('job_id')!=jid:
                    continue
                artifact,state=self._classify_recovery(job,event)
                if state in ('completed','not_written'):
                    if recovery_folder.is_dir() and event_path.is_relative_to(recovery_folder):event_path.unlink(missing_ok=True)
                    continue
                if state!='recoverable':
                    pending.append({'path':artifact.get('path') if artifact else event.get('path'),'state':state})
                    continue
                job['artifacts']=[a for a in job['artifacts'] if a['path']!=artifact['path']]+[artifact]
                recovered.append(artifact)
            if recovered:
                job['revision']+=1;job['updated_at']=now()
                self.jobs.persist(job)
                for event_path in event_paths:
                    if recovery_folder.is_dir() and event_path.is_relative_to(recovery_folder):
                        artifact,state=self._classify_recovery(job,load(event_path))
                        if state=='completed':event_path.unlink(missing_ok=True)
            return {'job_id':jid,'recovered':recovered,'pending':pending,
                    'revision':job['revision']}
