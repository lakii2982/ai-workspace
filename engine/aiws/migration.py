"""Verified cutover with explicit manifests; no business file moves."""
import platform
from .core import Error, put_json, now
from .remote_store import RemoteStore, enabled
from .jobs import Jobs

class Migration:
    def __init__(self,w):self.w=w
    def run(self,assignments=None,activate=False):
        if enabled(self.w):raise Error('INVALID_STATE','Already active')
        assignments=assignments or {}
        projects=self.w.projects();known={p['project_id'] for p in projects}
        jobs=Jobs(self.w).list()
        unresolved=[]
        for j in jobs:
            if not j['scope']['project_ids']:
                pid=assignments.get(j['job_id'])
                if pid not in known:unresolved.append(j['job_id'])
                else:j['scope']['project_ids']=[pid]
        if unresolved:raise Error('UNASSIGNED_JOBS','Explicit project assignments required: '+','.join(unresolved))
        if not list(self.w.path('.workspace/backups').glob('backup-*.tar.gz')):raise Error('BACKUP_REQUIRED','Create backup before migration')
        remote=RemoteStore(self.w)
        results=[]
        for p in projects:
            data={**p,'status_note':'由aiws统一维护；文件保留原目录'}
            results.append(remote.write('projects',p['project_id'],data,bootstrap=True))
        for j in jobs:
            # Migration alone may adopt the known staging record, never an arbitrary record.
            row=remote.read_row('jobs',j['job_id'])
            if row and not row.get('执行数据'):
                if j['job_id']!='JOB-0012' or row.get('操作ID')!='stage-job0012-rev2':raise Error('CONFLICT','Unmanaged remote row')
            remote.write_job_migration(j)
            actual=remote.job(j['job_id'])
            if actual!=j:raise Error('INVALID_DATA','Migration roundtrip mismatch '+j['job_id'])
            results.append({'job_id':j['job_id'],'revision':j['revision'],'verified':True})
        manifest=dict(schema_version=1,time=now(),projects=len(projects),jobs=len(jobs),assignments=assignments,verified=True)
        put_json(self.w.path('.workspace/migration/latest.json'),manifest)
        if activate:
            for j in jobs:put_json(Jobs(self.w).file(j['job_id']),j)
            config=remote.config;config.update(mode='active',authority='feishu',coordinator=platform.node()+':'+str(self.w.root))
            put_json(self.w.path('.workspace/feishu.json'),config)
        return {**manifest,'activated':activate}
    def rollback(self):
        if not enabled(self.w):raise Error('INVALID_STATE','Not active')
        remote=RemoteStore(self.w)
        if list(self.w.path('.workspace/remote-pending').glob('*.json')):raise Error('REMOTE_UNCERTAIN','Resolve pending writes first')
        # Capture remote authority before switching to local; never resurrect stale cache.
        for project in remote.projects():
            put_json(self.w.path(project['path']+'/.project.json'),{k:v for k,v in project.items() if k!='path'})
        for path in self.w.path('.workspace/jobs').glob('JOB-*/job.json'):
            j=remote.job(path.parent.name);put_json(path,j)
        config=remote.config;config.update(mode='staging',authority='local')
        put_json(self.w.path('.workspace/feishu.json'),config)
        return {'authority':'local','remote_records_preserved':True}
