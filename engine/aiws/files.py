"""Folder-oriented outputs. No user-facing artifact table."""
from pathlib import Path
import hashlib
from .core import Error, atomic, digest
from .jobs import Jobs, Artifacts

class Files:
    def __init__(self,w):self.w=w
    def directories(self,jid):
        j=Jobs(self.w).get(jid)
        return [{'project_id':p['project_id'],'work':p['path']+'/工作/'+j.get('directory_name',jid),'deliverables':p['path']+'/成果/'+j.get('directory_name',jid)} for p in [self.w.project_for(pid) for pid in j['scope']['project_ids']]]
    def write(self,jid,project_id,name,content,kind='draft',expected_hash=None,lease_token=None):
        if kind not in ('draft','process'):raise Error('INVALID_ARGUMENT','kind must be draft or process')
        if not isinstance(name,str) or not name or Path(name).name!=name or name in ('.','..'):raise Error('INVALID_ARGUMENT','Single filename required')
        directory=next((d for d in self.directories(jid) if d['project_id']==project_id),None)
        if not directory:raise Error('INVALID_SCOPE','Project outside job')
        relative=directory['work']+'/'+('工作稿' if kind=='draft' else '过程材料')+'/'+name
        return Artifacts(self.w).save(jid,relative,content.encode(),expected_hash=expected_hash,lease_token=lease_token)
    def publish(self,jid,project_id,source,source_hash,name,expected_hash=None,confirm=False,lease_token=None):
        if not confirm:raise Error('CONFIRMATION_REQUIRED','Concrete version confirmation required')
        directory=next((d for d in self.directories(jid) if d['project_id']==project_id),None)
        if not directory:raise Error('INVALID_SCOPE','Project outside job')
        source_path=self.w.path(source)
        if not Path(source).is_relative_to(directory['work']):raise Error('INVALID_SCOPE','Source outside job')
        source_bytes=source_path.read_bytes()
        if hashlib.sha256(source_bytes).hexdigest()!=source_hash:raise Error('CONFLICT','Source version changed')
        if not name or Path(name).name!=name or name in ('.','..'):raise Error('INVALID_ARGUMENT','Single filename required')
        target=directory['deliverables']+'/'+name
        # Artifacts.save owns the target lock and archives the previous bytes there.
        return Artifacts(self.w).save(jid,target,source_bytes,expected_hash=expected_hash,confirm=True,purpose='deliverable',lease_token=lease_token,allow_closed=True)
