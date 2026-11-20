"""Feishu staging adapter. It never silently changes the authority of local jobs.

One coordinator workspace only. Local flock is NOT a multi-host lock.
"""
import json
import subprocess
import tempfile
import uuid
from pathlib import Path
from .core import Error, load, put_json

PROJECT_FIELDS=['项目ID','项目名称','别名','目标与边界','状态','项目目录','时间线入口','负责人','状态说明']
JOB_FIELDS=['任务ID','所属项目ID','标题','关键词','目标','验收条件','状态','等待原因','进度摘要','下一动作','有效约束','工作目录','成果目录','执行者','认领有效期','更新时间','操作ID','版本号','所属项目名称']

class Feishu:
    def __init__(self,w):
        self.w=w
        self.config=load(w.path('.workspace/feishu.json'))
    def command(self,action,table,args):
        argv=['lark-cli','base',action,'--base-token',self.config['base_token'],'--table-id',table,'--as','user']+args
        try:
            r=subprocess.run(argv,cwd=self.w.root,capture_output=True,text=True,timeout=45)
            value=json.loads(r.stdout or r.stderr)
        except (OSError,ValueError,subprocess.TimeoutExpired) as e:
            raise Error('REMOTE_UNCERTAIN','Remote result unknown; reconcile before retry') from e
        if r.returncode or value.get('ok') is False:
            raise Error('REMOTE_ERROR',value.get('error',{}).get('message','Remote operation failed'))
        return value
    def rows(self,entity,key=None,value=None,limit=20,offset=0,system=False,conditions=None,projection=None):
        if entity not in ('projects','jobs'):raise Error('INVALID_ARGUMENT','Invalid entity')
        if type(limit) is not int or not 1<=limit<=100 or type(offset) is not int or offset<0:raise Error('INVALID_ARGUMENT','Invalid pagination')
        fields=list(PROJECT_FIELDS if entity=='projects' else JOB_FIELDS)
        if system:fields+=['执行数据','数据指纹']
        if projection is not None:
            if not set(projection)<=set(fields):raise Error('INVALID_ARGUMENT','Invalid projection')
            fields=projection
        if key is not None and key not in fields:raise Error('INVALID_ARGUMENT','Unknown field')
        folder=self.w.path('.workspace/remote-cache');folder.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=folder) as d:
            dest=Path(d)/'rows.ndjson'
            args=['--limit',str(limit),'--offset',str(offset),'--format','ndjson','--output',str(dest.relative_to(self.w.root))]
            for field in fields:args+=['--field-id',field]
            filters=list(conditions or [])
            if key is not None:filters.append([key,'==',value])
            if filters:args+=['--filter-json',json.dumps({'logic':'and','conditions':filters},ensure_ascii=False)]
            result=self.command('+record-list',self.config[entity+'_table'],args)
            if not dest.exists():raise Error('REMOTE_ERROR','Missing record file')
            rows=[json.loads(line) for line in dest.read_text().splitlines() if line.strip()]
            return {'items':rows,'has_more':result.get('has_more',False),'next_offset':result.get('next_offset')}
    def stage_job(self,jid,operation_id):
        from .jobs import Jobs
        from .remote_store import date_value
        if not isinstance(operation_id,str) or not operation_id or len(operation_id)>128:raise Error('INVALID_ARGUMENT','Operation ID required')
        with self.w.lock('feishu-writer'):
            j=Jobs(self.w).get(jid)
            projects=[p for p in self.w.projects() if p['project_id'] in j['scope']['project_ids']]
            fields={'任务ID':jid,'所属项目ID':','.join(j['scope']['project_ids']),'标题':j['title'],'关键词':'','目标':j['goal'],'验收条件':'\n'.join(j['acceptance']),'状态':j['status'],'等待原因':'\n'.join(j['open_issues']),'进度摘要':'\n'.join(j['completed']),'下一动作':'\n'.join(j['next_steps']),'有效约束':'\n'.join(j['decisions']),'工作目录':'\n'.join(p['path']+'/工作/'+jid for p in projects),'成果目录':'\n'.join(p['path']+'/成果/'+jid for p in projects),'执行者':j['last_agent'],'认领有效期':None,'更新时间':date_value(j['updated_at']),'操作ID':operation_id,'版本号':j['revision']}
            marker=self.w.path('.workspace/remote-operations/'+__import__('hashlib').sha256(operation_id.encode()).hexdigest()+'.json')
            prior=load(marker) if marker.exists() else None
            if prior and prior['fields']!=fields:raise Error('CONFLICT','Operation ID reused for a different revision')
            found=self.rows('jobs','任务ID',jid,limit=2)
            if found['has_more'] or len(found['items'])>1:raise Error('CONFLICT','Duplicate remote job identity')
            row=found['items'][0] if found['items'] else None
            if row and row.get('操作ID')==operation_id:
                if any(row.get(k)!=v for k,v in fields.items() if v!=''):raise Error('CONFLICT','Remote content changed outside operation')
                put_json(marker,dict(schema_version=1,phase='verified',fields=fields,record_id=row['record_id']))
                return {'record_id':row['record_id'],'phase':'verified','replayed':True}
            if prior:raise Error('REMOTE_UNCERTAIN','Previous write not visible; do not blindly repeat')
            if row:raise Error('CONFLICT','Existing row requires explicit reconciliation; staging does not overwrite')
            put_json(marker,dict(schema_version=1,phase='prepared',fields=fields))
            result=self.command('+record-upsert',self.config['jobs_table'],['--json',json.dumps(fields,ensure_ascii=False)])
            ids=result.get('data',{}).get('record',{}).get('record_id_list',[])
            if len(ids)!=1:raise Error('REMOTE_UNCERTAIN','Write returned no unique receipt')
            put_json(marker,dict(schema_version=1,phase='acknowledged',fields=fields,record_id=ids[0]))
            return {'record_id':ids[0],'phase':'acknowledged','authority':'local; staging only'}
