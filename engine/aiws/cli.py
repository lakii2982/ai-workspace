import argparse
import json
import os
from .core import Error
from .service import Service

def main():
    p=argparse.ArgumentParser(prog='aiws',description='Local scoped knowledge and persistent tasks')
    p.add_argument('--workspace',default=os.environ.get('AIWS_WORKSPACE'))
    p.add_argument('--json',action='store_true',help='All output is structured JSON')
    p.add_argument('command',choices=['init','project','index','search','source','job','artifact','backup','restore','doctor','call'])
    p.add_argument('action',nargs='?')
    p.add_argument('--name');p.add_argument('--query');p.add_argument('--project',action='append',default=[])
    p.add_argument('--company',action='store_true');p.add_argument('--professional',action='store_true')
    p.add_argument('--params',default='{}',help='JSON operation arguments')
    a=p.parse_args()
    try:
        if not a.workspace:raise Error('NOT_FOUND','Specify --workspace or AIWS_WORKSPACE')
        args=json.loads(a.params)
        if not isinstance(args,dict):raise Error('INVALID_ARGUMENT','params must be an object')
        if a.command in ['init','backup','restore','doctor']:op=a.command
        elif a.command=='call':op=a.action
        elif a.command=='project':
            op={'create':'create_project','list':'list_projects'}.get(a.action)
            if a.name:args['name']=a.name
        elif a.command=='index':op='update_index' if a.action=='update' else None
        elif a.command=='search':
            op='search_sources';args.update(query=a.query,scope={'include_company':a.company,'include_professional':a.professional,'project_ids':a.project,'topic_ids':[]})
        elif a.command=='source':op='read_source' if a.action=='read' else None
        elif a.command=='artifact':op='save_artifact' if a.action=='save' else None
        else:
            op={'create':'create_job','list':'list_jobs','resume':'resume_job','checkpoint':'checkpoint_job'}.get(a.action)
        result=Service(a.workspace).call(op,args)
        print(json.dumps({'ok':True,'result':result},ensure_ascii=False,indent=2))
    except Error as e:
        print(json.dumps({'ok':False,'error':{'code':e.code,'message':e.message}},ensure_ascii=False));raise SystemExit(1)
    except (TypeError,ValueError) as e:
        print(json.dumps({'ok':False,'error':{'code':'INVALID_ARGUMENT','message':str(e)}},ensure_ascii=False));raise SystemExit(1)
    except Exception as e:
        print(json.dumps({'ok':False,'error':{'code':'INTERNAL_ERROR','message':type(e).__name__}},ensure_ascii=False));raise SystemExit(1)
