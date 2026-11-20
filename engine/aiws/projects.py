"""Project metadata updates through the same coordinator."""
from .core import Error, digest, load, put_json
from .remote_store import enabled,RemoteStore

class Projects:
    def __init__(self,w):self.w=w
    def get(self,project_id):
        p=self.w.project_for(project_id)
        if not p:raise Error('NOT_FOUND','Project not found')
        if enabled(self.w):
            r=RemoteStore(self.w);row=r.read_row('projects',project_id);data=r.decode('projects',row)
            return {**data,'state_hash':row['数据指纹']}
        return {**p,'state_hash':digest(self.w.path(p['path']+'/.project.json'))}
    def update(self,project_id,expected_hash,patch):
        if not isinstance(patch,dict) or not set(patch)<= {'name','aliases','status','status_note','goal','owner'}:raise Error('INVALID_ARGUMENT','Unknown project field')
        p=self.get(project_id)
        if p.pop('state_hash')!=expected_hash:raise Error('CONFLICT','Project changed')
        if 'status' in patch and patch['status'] not in ('new','active','paused','completed','archived'):raise Error('INVALID_ARGUMENT','Invalid status')
        for k,v in patch.items():
            if k=='aliases':
                if not isinstance(v,list) or any(not isinstance(x,str) or not x for x in v):raise Error('INVALID_ARGUMENT','Invalid aliases')
            elif not isinstance(v,str) or (k=='name' and not v.strip()):raise Error('INVALID_ARGUMENT','Invalid text')
        p.update(patch)
        if enabled(self.w):RemoteStore(self.w).write('projects',project_id,p,expected_hash)
        put_json(self.w.path(p['path']+'/.project.json'),{k:v for k,v in p.items() if k!='path'})
        self.w._projects_cache=None
        self.w._project_lookup.pop(project_id,None)
        return self.get(project_id)
