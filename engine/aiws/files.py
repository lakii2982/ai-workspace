"""Folder-oriented outputs. No user-facing artifact table."""
from pathlib import Path
import hashlib
import os
from .core import Error, atomic, digest, load, now, put_json
from .jobs import Jobs, Artifacts

WORK_KINDS={'draft':'工作稿','process':'过程材料'}

class Files:
    def __init__(self,w):self.w=w
    def directories(self,jid):
        j=Jobs(self.w).get(jid)
        return [{'project_id':p['project_id'],'work':p['path']+'/工作/'+j.get('directory_name',jid),'deliverables':p['path']+'/成果/'+j.get('directory_name',jid)} for p in [self.w.project_for(pid) for pid in j['scope']['project_ids']]]
    def _directory(self,jid,project_id):
        directory=next((d for d in self.directories(jid) if d['project_id']==project_id),None)
        if not directory:raise Error('INVALID_SCOPE','Project outside job')
        return directory
    def _work_relative(self,jid,project_id,relative,action):
        directory=self._directory(jid,project_id)
        relative=Path(relative).as_posix()
        prefixes=tuple(directory['work']+'/'+folder+'/' for folder in WORK_KINDS.values())
        if not relative.startswith(prefixes):
            raise Error('INVALID_SCOPE',f'Only work drafts and process material can be {action}')
        return directory,relative
    def _pending_references(self,jid,project_id,relative):
        """An open checkout or pending decision still points at the bytes we are about to move."""
        edits=self.w.path(f'.workspace/edits/{jid}')
        if edits.is_dir():
            for meta in edits.glob('*/record.json'):
                record=load(meta)
                if record.get('source')==relative and 'committed' not in record:
                    raise Error('INVALID_STATE','An open checkout still points at this file; commit or discard it first')
        decisions=self.w.path(f'.workspace/decisions/{project_id}/{jid}')
        if decisions.is_dir():
            for meta in decisions.glob('*.json'):
                record=load(meta)
                if record.get('source')==relative and record.get('state')=='pending':
                    raise Error('INVALID_STATE','A pending publication decision still points at this file')
    def _event(self,jid,j,event):
        event_id=hashlib.sha256((event['path']+event.get('from_path','')+event['time']).encode()).hexdigest()
        path=self.w.path(f'.workspace/history/events/{event_id}.json')
        put_json(path,{**event,'phase':'prepared'})
        return path
    def _update(self,jid,transform):
        latest=Jobs(self.w).get(jid)
        latest['artifacts']=transform(latest['artifacts'])
        latest['revision']+=1;latest['updated_at']=now()
        Jobs(self.w).persist(latest)
        return latest['revision']
    def write(self,jid,project_id,name,content,kind='draft',expected_hash=None,lease_token=None):
        if kind not in WORK_KINDS:raise Error('INVALID_ARGUMENT','kind must be draft or process')
        if not isinstance(name,str) or not name or Path(name).name!=name or name in ('.','..'):raise Error('INVALID_ARGUMENT','Single filename required')
        directory=self._directory(jid,project_id)
        relative=directory['work']+'/'+WORK_KINDS[kind]+'/'+name
        return Artifacts(self.w).save(jid,relative,content.encode(),expected_hash=expected_hash,lease_token=lease_token)
    def publish(self,jid,project_id,source,source_hash,name,expected_hash=None,confirm=False,lease_token=None):
        if not confirm:raise Error('CONFIRMATION_REQUIRED','Concrete version confirmation required')
        directory=self._directory(jid,project_id)
        source_path=self.w.path(source)
        if not Path(source).is_relative_to(directory['work']):raise Error('INVALID_SCOPE','Source outside job')
        source_bytes=source_path.read_bytes()
        if hashlib.sha256(source_bytes).hexdigest()!=source_hash:raise Error('CONFLICT','Source version changed')
        if not name or Path(name).name!=name or name in ('.','..'):raise Error('INVALID_ARGUMENT','Single filename required')
        target=directory['deliverables']+'/'+name
        # Artifacts.save owns the target lock and archives the previous bytes there.
        return Artifacts(self.w).save(jid,target,source_bytes,expected_hash=expected_hash,confirm=True,purpose='deliverable',lease_token=lease_token,allow_closed=True)
    def move(self,jid,project_id,source,target_kind,name=None,expected_hash=None,lease_token=None):
        """Move a draft or process file inside its task folder without changing its bytes."""
        if target_kind not in WORK_KINDS:raise Error('INVALID_ARGUMENT','kind must be draft or process')
        j=Jobs(self.w).get(jid)
        from .workflow import check_lease
        from .remote_store import enabled
        check_lease(j,lease_token,require=enabled(self.w))
        directory,source_relative=self._work_relative(jid,project_id,source,'moved')
        target_name=name or Path(source_relative).name
        if not target_name or Path(target_name).name!=target_name or target_name in ('.','..'):
            raise Error('INVALID_ARGUMENT','Single filename required')
        target_relative=directory['work']+'/'+WORK_KINDS[target_kind]+'/'+target_name
        if target_relative==source_relative:raise Error('INVALID_ARGUMENT','Source and target are the same')
        self._pending_references(jid,project_id,source_relative)
        first,second=sorted([source_relative,target_relative])
        with self.w.lock('file:'+first),self.w.lock('file:'+second),self.w.lock('job:'+jid):
            source_path=self.w.path(source_relative);target_path=self.w.path(target_relative)
            if not source_path.is_file():raise Error('NOT_FOUND','Source file missing')
            actual=digest(source_path)
            if expected_hash!=actual:raise Error('CONFLICT','Source changed; pass the current hash')
            if target_path.exists():raise Error('CONFLICT','Target already exists')
            event=dict(schema_version=1,job_id=jid,path=target_relative,from_path=source_relative,
                       old_hash=actual,new_hash=actual,confirmed=False,action='move',
                       actor=j['last_agent'],time=now())
            record=self._event(jid,j,event)
            target_path.parent.mkdir(parents=True,exist_ok=True)
            os.replace(source_path,target_path)
            put_json(record,{**event,'phase':'committed'})
            revision=self._update(jid,lambda artifacts:[
                ({**a,'path':target_relative} if Path(a['path']).as_posix()==source_relative else a)
                for a in artifacts])
            return {'from':source_relative,'to':target_relative,'hash':actual,'revision':revision}
    def delete(self,jid,project_id,relative,expected_hash=None,lease_token=None):
        """Delete a draft or process file. Published deliverables are never deleted here."""
        j=Jobs(self.w).get(jid)
        from .workflow import check_lease
        from .remote_store import enabled
        check_lease(j,lease_token,require=enabled(self.w))
        _,relative=self._work_relative(jid,project_id,relative,'deleted')
        self._pending_references(jid,project_id,relative)
        with self.w.lock('file:'+relative),self.w.lock('job:'+jid):
            path=self.w.path(relative)
            if not path.is_file():raise Error('NOT_FOUND','File missing')
            actual=digest(path)
            if expected_hash!=actual:raise Error('CONFLICT','File changed; pass the current hash')
            atomic(self.w.path(f'.workspace/history/files/{actual}'),path.read_bytes())
            event=dict(schema_version=1,job_id=jid,path=relative,old_hash=actual,new_hash=actual,
                       confirmed=False,action='delete',actor=j['last_agent'],time=now())
            record=self._event(jid,j,event)
            path.unlink()
            put_json(record,{**event,'phase':'committed'})
            revision=self._update(jid,lambda artifacts:[
                a for a in artifacts if Path(a['path']).as_posix()!=relative])
            return {'path':relative,'deleted_hash':actual,'revision':revision}
