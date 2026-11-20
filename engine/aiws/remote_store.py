"""Remote authority, serialized by the single coordinator workspace.

Remote fields are validated against the canonical payload. Direct edits fail closed.
An ambiguous write is journalled and reconciled before another write is allowed.
"""
import hashlib
import json
import platform
import time
import datetime as dt
from .core import Error, load, put_json
from .feishu import Feishu, JOB_FIELDS, PROJECT_FIELDS


PROJECT_STATUS={'new':'待启动','active':'进行中','paused':'暂停','completed':'已完成','archived':'已归档'}
JOB_STATUS={'new':'待处理','in_progress':'进行中','blocked':'等待','review':'待验收','completed':'已完成','cancelled':'已取消'}

def packed(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))

def date_value(value):
    """Canonical millisecond instant; empty leases remain empty, never epoch zero."""
    if value in (None, ''):return None
    if isinstance(value,str) and value.replace('.','',1).isdigit():value=float(value)
    if isinstance(value,(int,float)):
        value=dt.datetime.fromtimestamp(value,dt.timezone.utc)
    else:
        value=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    if value.tzinfo is None:raise Error('INVALID_DATA','Datetime requires timezone')
    return value.astimezone(dt.timezone.utc).isoformat(timespec='milliseconds')

def enabled(w):
    p=w.path('.workspace/feishu.json')
    return p.exists() and load(p).get('mode')=='active'

class RemoteStore:
    def __init__(self,w):
        self.w=w;self.transport=Feishu(w);self.config=self.transport.config
        if self.config.get('mode')=='active' and self.config.get('coordinator')!=platform.node()+':'+str(w.root):
            raise Error('WRONG_COORDINATOR','Use the original coordinator; restored copies are not writers')
    def read_row(self,entity,identity):
        key='任务ID' if entity=='jobs' else '项目ID'
        for delay in (0,.3,.7,1.5):
            if delay:time.sleep(delay)
            found=self.transport.rows(entity,key,identity,limit=2,system=True)
            if found['has_more'] or len(found['items'])>1:raise Error('CONFLICT','Duplicate remote identity')
            row=found['items'][0] if found['items'] else None
            if self.config.get('mode')!='active' or (row and row.get('执行数据')):return row
        return row
    def decode(self,entity,row):
        if not row or not row.get('执行数据'):raise Error('REMOTE_NOT_READY','Missing canonical remote data')
        try: data=json.loads(row['执行数据'])
        except (ValueError,TypeError):raise Error('REMOTE_DRIFT','Invalid canonical data')
        if row.get('数据指纹')!=hashlib.sha256(packed(data).encode()).hexdigest():raise Error('REMOTE_DRIFT','Remote payload fingerprint changed')
        expected=self.fields(entity,data)
        for k,v in expected.items():
            actual=row.get(k)
            if entity=='jobs' and k in ('更新时间','认领有效期'):
                actual=date_value(actual)
            if (actual or '')!=(v or ''):raise Error('REMOTE_DRIFT','Remote fields were edited outside aiws; reconcile explicitly')
        return data
    def fields(self,entity,data):
        if entity=='projects':
            return {'项目ID':data['project_id'],'项目名称':data['name'],'别名':'\n'.join(data.get('aliases',[])),'目标与边界':data.get('goal','待确认'),'状态':[PROJECT_STATUS[data['status']]],'状态说明':data.get('status_note',''),'项目目录':data['path'],'时间线入口':data['path']+'/记忆/TIMELINE.md','负责人':data.get('owner','待确认')}
        dirs=data.get('directories',[])
        return {'任务ID':data['job_id'],'所属项目ID':data.get('project_links',[]),'标题':data['title'],'关键词':'\n'.join(data.get('keywords',[])),'目标':data['goal'],'验收条件':'\n'.join(data['acceptance']),'状态':[JOB_STATUS[data['status']]],'等待原因':'\n'.join(data['open_issues']),'进度摘要':'\n'.join(data['completed']),'下一动作':'\n'.join(data['next_steps']),'有效约束':'\n'.join(data['decisions']),'工作目录':'\n'.join(x['work'] for x in dirs),'成果目录':'\n'.join(x['deliverables'] for x in dirs),'执行者':data.get('lease',{}).get('owner') or data['last_agent'],'认领有效期':date_value(data.get('lease',{}).get('expires')),'更新时间':date_value(data['updated_at']),'版本号':data['revision']}
    def job(self,jid):
        data=self.decode('jobs',self.read_row('jobs',jid))
        manifest=self.w.path('.workspace/manifests/'+data.pop('manifest_hash')+'.json')
        content=load(manifest)
        if hashlib.sha256(packed(content).encode()).hexdigest()!=manifest.stem:raise Error('INVALID_DATA','Manifest changed')
        data.update(artifacts=content['artifacts'],sources=content['sources'])
        data.pop('directories',None)
        data.pop('project_links',None)
        return data
    def write_job(self,j,expected_revision,bootstrap=False):
        manifest=dict(schema_version=1,artifacts=j['artifacts'],sources=j['sources'])
        sha=hashlib.sha256(packed(manifest).encode()).hexdigest()
        put_json(self.w.path('.workspace/manifests/'+sha+'.json'),manifest)
        data={k:v for k,v in j.items() if k not in ('artifacts','sources')}
        data['manifest_hash']=sha
        links=[]
        for pid in j['scope']['project_ids']:
            project=self.read_row('projects',pid)
            if not project:raise Error('REMOTE_NOT_READY','Project must be registered first')
            links.append({'id':project['record_id']})
        data['project_links']=links
        data['directories']=j.get('directories') or [{'project_id':p['project_id'],'work':p['path']+'/工作/'+j.get('directory_name',j['job_id']),'deliverables':p['path']+'/成果/'+j.get('directory_name',j['job_id'])} for p in [self.w.project_for(pid) for pid in j['scope']['project_ids']]]
        return self.write('jobs',j['job_id'],data,expected_revision,bootstrap=bootstrap)
    def write(self,entity,identity,data,expected_revision=None,bootstrap=False):
        fields=self.fields(entity,data)
        sha=hashlib.sha256(packed(data).encode()).hexdigest()
        fields.update({'执行数据':packed(data),'数据指纹':sha})
        op=hashlib.sha256((entity+identity+sha).encode()).hexdigest()
        if entity=='jobs':fields['操作ID']=op
        pending=self.w.path('.workspace/remote-pending/'+entity+'-'+identity+'.json')
        row=self.read_row(entity,identity)
        if pending.exists():
            previous=load(pending)
            if row and row.get('数据指纹')==previous['hash']:
                self.decode(entity,row);pending.unlink()
            else:raise Error('REMOTE_UNCERTAIN','Previous operation awaits reconciliation; no automatic repeated write')
        if row and row.get('数据指纹')==sha:
            self.decode(entity,row);return {'record_id':row['record_id'],'verified':True,'replayed':True}
        if row and not bootstrap:
            old=self.decode(entity,row)
            if entity=='jobs' and old['revision']!=expected_revision:raise Error('CONFLICT','Remote revision changed')
            if entity=='projects' and row['数据指纹']!=expected_revision:raise Error('CONFLICT','Project changed')
        elif not row and expected_revision not in (None,0) and not bootstrap:raise Error('CONFLICT','Remote record missing')
        put_json(pending,dict(schema_version=1,hash=sha,fields=fields,identity=identity))
        args=['--json',json.dumps(fields,ensure_ascii=False)]
        if row:args+=['--record-id',row['record_id']]
        self.transport.command('+record-upsert',self.config[entity+'_table'],args)
        # Feishu can acknowledge before reads reflect the update.
        for delay in (0,.5,1,2,4):
            if delay:time.sleep(delay)
            latest=self.read_row(entity,identity)
            if latest and latest.get('数据指纹')==sha:
                self.decode(entity,latest);pending.unlink()
                return {'record_id':latest['record_id'],'verified':True}
        raise Error('REMOTE_UNCERTAIN','Write acknowledged but not yet visible; reconcile before continuing')

    def query(self,project_id=None,query='',status='active',limit=20,offset=0):
        conditions=[]
        if project_id:
            p=self.read_row('projects',project_id)
            if not p:raise Error('INVALID_SCOPE','Unknown project')
            conditions.append(['所属项目ID','intersects',[{'id':p['record_id']}]])
        if query:conditions.append(['任务ID','==',query] if query.startswith('JOB-') else ['标题','intersects',query])
        if status=='active':conditions.append(['状态','disjoint',['已完成','已取消']])
        elif status:
            if status not in JOB_STATUS:raise Error('INVALID_ARGUMENT','Unknown status')
            conditions.append(['状态','intersects',[JOB_STATUS[status]]])
        page=self.transport.rows('jobs',conditions=conditions,limit=limit,offset=offset,projection=['任务ID','标题','状态','版本号','更新时间'])
        reverse={v:k for k,v in JOB_STATUS.items()}
        return {'items':[dict(job_id=r['任务ID'],title=(r.get('标题') or '')[:160],status=reverse.get((r.get('状态') or [''])[0],'unknown'),revision=r.get('版本号'),updated_at=r.get('更新时间')) for r in page['items']], 'has_more':page['has_more'],'next_offset':offset+limit if page['has_more'] else None}

    def write_job_migration(self,j):return self.write_job(j,None,bootstrap=True)

    def projects(self):
        result=[];offset=0
        while True:
            page=self.transport.rows('projects',limit=100,offset=offset,system=True)
            result.extend(self.decode('projects',row) for row in page['items'])
            if not page['has_more']:break
            offset+=100
        return result

    def reconcile(self):
        recovered=[];unresolved=[]
        for path in self.w.path('.workspace/remote-pending').glob('*.json'):
            item=load(path);entity='jobs' if path.name.startswith('jobs-') else 'projects'
            row=self.read_row(entity,item['identity'])
            if not row or row.get('数据指纹')!=item['hash']:
                unresolved.append(item['identity']);continue
            self.decode(entity,row)
            if entity=='jobs':
                from .jobs import Jobs
                data=self.job(item['identity']);put_json(Jobs(self.w).file(item['identity']),data)
            path.unlink();recovered.append(item['identity'])
        return {'recovered':recovered,'unresolved':unresolved}
