"""Task ownership, deterministic resolution and durable session bindings."""
import hashlib
import time
import uuid
from .core import Error, now, put_json, load
from .jobs import Jobs

class Workflow:
    def __init__(self,w):self.w=w;self.jobs=Jobs(w)
    def claim(self,jid,owner,revision,ttl=1800):
        if not isinstance(owner,str) or not owner or type(ttl) is not int or not 30<=ttl<=7200:raise Error('INVALID_ARGUMENT','Owner and TTL 30..7200 required')
        with self.w.lock('job:'+jid):
            j=self.jobs.get(jid)
            if j['revision']!=revision:raise Error('CONFLICT','Revision changed')
            if j['status'] in ('completed','cancelled'):raise Error('INVALID_STATE','Closed task cannot be claimed')
            lease=j.get('lease',{})
            if lease.get('expires',0)>time.time():raise Error('CLAIMED','Task already claimed; renew or release explicitly')
            j['lease']={'owner':owner,'token':uuid.uuid4().hex,'expires':time.time()+ttl}
            j['last_agent']=owner;j['revision']+=1;j['updated_at']=now()
            return self.jobs.persist(j)
    def release(self,jid,revision,token,next_steps):
        if not isinstance(next_steps,list) or not next_steps:raise Error('INVALID_ARGUMENT','Handoff next steps required')
        with self.w.lock('job:'+jid):
            j=self.jobs.get(jid)
            if j['revision']!=revision:raise Error('CONFLICT','Revision changed')
            check_lease(j,token,require=True)
            j['lease']={};j['next_steps']=next_steps;j['revision']+=1;j['updated_at']=now()
            return self.jobs.persist(j)
    def renew(self,jid,revision,token,ttl=1800):
        if type(ttl) is not int or not 30<=ttl<=7200:raise Error('INVALID_ARGUMENT','Invalid TTL')
        with self.w.lock('job:'+jid):
            j=self.jobs.get(jid)
            if j['revision']!=revision:raise Error('CONFLICT','Revision changed')
            check_lease(j,token,require=True)
            j['lease']['expires']=time.time()+ttl;j['revision']+=1;j['updated_at']=now()
            return self.jobs.persist(j)
    def resolve(self,project=None,query='',jid=None):
        if jid:return {'match':'unique','task':self.jobs.brief(jid)}
        if not project:raise Error('INVALID_SCOPE','Project name or ID required')
        from .remote_store import enabled,RemoteStore
        if project.startswith('PRJ-'):
            matches=[self.w.project_for(project)]
        elif enabled(self.w):
            remote=RemoteStore(self.w);candidates={}
            for field,operator in [('项目名称','=='),('别名','intersects')]:
                page=remote.transport.rows('projects',conditions=[[field,operator,project]],limit=20,system=True)
                if page['has_more']:raise Error('AMBIGUOUS','Too many project matches; use project ID')
                for row in page['items']:
                    item=remote.decode('projects',row);candidates[item['project_id']]=item
            matches=[p for p in candidates.values() if project.casefold() in [x.casefold() for x in [p['name']]+p.get('aliases',[])]]
        else:
            matches=[p for p in self.w.projects() if project.casefold() in [x.casefold() for x in [p['project_id'],p['name']]+p.get('aliases',[])]]
        if len(matches)!=1:return {'match':'ambiguous' if matches else 'none','projects':[{'project_id':p['project_id'],'name':p['name']} for p in matches[:20]]}
        found=self.jobs.query(project_id=matches[0]['project_id'],query=query,limit=20)
        if len(found['items'])==1 and not found['has_more']:return {'match':'unique','task':self.jobs.brief(found['items'][0]['job_id'])}
        return {'match':'ambiguous' if found['items'] else 'none',**found}
    def bind(self,session_id,jid):
        if not isinstance(session_id,str) or not 1<=len(session_id)<=200:raise Error('INVALID_ARGUMENT','Session ID required')
        job=self.jobs.get(jid)
        path=self.w.path('.workspace/sessions/'+hashlib.sha256(session_id.encode()).hexdigest()+'.json')
        put_json(path,dict(schema_version=1,session_id=session_id,jid=jid,project_ids=job['scope']['project_ids']))
        return self.jobs.brief(jid)
    def session(self,session_id):
        return self.jobs.brief(self.session_job_id(session_id))
    def session_job_id(self,session_id,*,missing_ok=False):
        path=self.w.path('.workspace/sessions/'+hashlib.sha256(session_id.encode()).hexdigest()+'.json')
        if missing_ok and not path.exists():
            return None
        return load(path)['jid']

def check_lease(j,token,require=False):
    lease=j.get('lease',{})
    if not require and not lease:return
    if not lease or lease.get('token')!=token or lease.get('expires',0)<=time.time():raise Error('LEASE_REQUIRED','Claim required, token stale, or lease expired')
