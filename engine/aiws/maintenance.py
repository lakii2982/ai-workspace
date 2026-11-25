import json
from pathlib import Path
import shutil
import tarfile
import tempfile
from .core import Error, Workspace, digest, load, now
from .jobs import Jobs
from .retrieval import Retrieval

class Maintenance:
    def __init__(self,w):self.w=w
    def doctor(self):
        load(self.w.path('.workspace/config.json'))
        projects=self.w.projects();jobs=Jobs(self.w).list()
        with Retrieval(self.w).connect() as db: integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
        bad=[]
        for j in jobs:
            for a in Jobs(self.w).resume(j['job_id'],detail='full')['artifact_integrity']:
                if a['integrity']!='ok':bad.append(a['path'])
        return {'healthy':integrity=='ok' and not bad,'sqlite':integrity,'projects':len(projects),'jobs':len(jobs),'changed_artifacts':bad}
    def backup(self):
        # All mutating services take the shared workspace gate; backup takes exclusive gate.
        import fcntl
        with self.w.lock('backup'):
            gate=self.w.path('.workspace/gate.lock');gate.touch(exist_ok=True)
            with gate.open('r') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX)
                files={}
                project_git={p['path']+'/.git' for p in self.w.projects()
                             if (self.w.path(p['path'])/'.git').is_dir()}
                for p in self.w.root.rglob('*'):
                    rel=p.relative_to(self.w.root)
                    if any(x in rel.parts for x in ('.venv','__pycache__','.pytest_cache')):continue
                    if '.git' in rel.parts and not any(rel.as_posix().startswith(g+'/') for g in project_git):continue
                    if str(rel).startswith(('.workspace/backups/','.workspace/index/','.workspace/locks/')) or rel.as_posix()=='.workspace/gate.lock':continue
                    if p.is_symlink():raise Error('OUT_OF_ROOT','Backup refuses symlinks')
                    if p.is_file():files[rel.as_posix()]=digest(p)
                name='backup-'+now().replace(':','-')+'.tar.gz'
                target=self.w.path('.workspace/backups/'+name)
                with tempfile.NamedTemporaryFile(dir=target.parent,delete=False) as tmp:temp=Path(tmp.name)
                try:
                    with tarfile.open(temp,'w:gz') as tar:
                        for rel,sha in files.items():
                            p=self.w.path(rel)
                            tar.add(p,arcname=rel,recursive=False)
                            if digest(p)!=sha:raise Error('CONFLICT','File changed during backup; retry with writers idle')
                        import io
                        data=json.dumps({'schema_version':1,'files':files}).encode()
                        entry=tarfile.TarInfo('BACKUP_MANIFEST.json');entry.size=len(data);tar.addfile(entry,io.BytesIO(data))
                    temp.replace(target)
                finally:temp.unlink(missing_ok=True)
                return {'path':str(target),'sha256':digest(target),'files':len(files)}
    def restore(self,archive,destination):
        target=Path(destination).expanduser().absolute()
        if target.exists():raise Error('CONFLICT','Restore requires a new destination directory')
        with tarfile.open(archive,'r:gz') as tar:
            members=tar.getmembers()
            for m in members:
                p=Path(m.name)
                if p.is_absolute() or '..' in p.parts or not m.isfile():raise Error('OUT_OF_ROOT','Unsafe backup member')
            names=[m.name for m in members]
            if len(names)!=len(set(names)):raise Error('INVALID_DATA','Duplicate backup member')
            manifest=json.load(tar.extractfile('BACKUP_MANIFEST.json'))
            if manifest.get('schema_version')!=1 or set(names)!=(set(manifest['files'])|{'BACKUP_MANIFEST.json'}):raise Error('INVALID_DATA','Invalid backup manifest')
            target.parent.mkdir(parents=True,exist_ok=True)
            staging=Path(tempfile.mkdtemp(prefix='.aiws-restore-',dir=target.parent))
            try:
                for m in members:
                    p=staging/m.name;p.parent.mkdir(parents=True,exist_ok=True)
                    with tar.extractfile(m) as src,p.open('wb') as out:shutil.copyfileobj(src,out)
                for rel,sha in manifest['files'].items():
                    if digest(staging/rel)!=sha:raise Error('INVALID_DATA','Backup hash mismatch')
                remote_config=staging/'.workspace/feishu.json'
                if remote_config.exists():
                    from .core import put_json
                    config=load(remote_config);config.update(mode='staging',authority='local',restored_snapshot=True)
                    put_json(remote_config,config)
                w=Workspace(staging);w.init();index=Retrieval(w).update();health=Maintenance(w).doctor()
                baseline=set()
                for job_path in staging.glob('.workspace/jobs/JOB-*/job.json'):
                    archived_job=load(job_path)
                    for artifact in archived_job['artifacts']:
                        if manifest['files'].get(artifact['path'])!=artifact['hash']:baseline.add(artifact['path'])
                if health['sqlite']!='ok' or set(health['changed_artifacts'])!=baseline:raise Error('INVALID_DATA','Restore introduced integrity differences')
                health['preexisting_changed_artifacts']=sorted(baseline)
                health['restored_fidelity']=True
                staging.rename(target)
            finally:
                if staging.exists():shutil.rmtree(staging)
        return {'workspace':str(target),'health':health,'index':index}
