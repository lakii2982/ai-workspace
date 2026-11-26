"""Feishu staging adapter. It never silently changes the authority of local jobs.

One coordinator workspace only. Local flock is NOT a multi-host lock.
"""
import json
import subprocess
import tempfile
from pathlib import Path
from .core import Error, load

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
