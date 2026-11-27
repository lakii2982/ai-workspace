import base64
import fcntl
import json
from .core import Workspace, Error
from .entry import Entry
from .retrieval import Retrieval
from .jobs import Jobs, Artifacts
from .maintenance import Maintenance
from .timeline import Timeline
from contextlib import nullcontext


# These operations change workspace-wide allocation/authority, or reconcile
# pending rows across all projects. Exclude ordinary operations until complete.
_EXCLUSIVE_OPERATIONS = {
    'create_project', 'migrate_feishu', 'rollback_feishu', 'reconcile_remote',
}
# Serialize a same-task service transaction across its file check, Git snapshot
# and checkpoint. Resume and contextual reads acquire this same guard after
# resolving a bound session. Low-level file and revision locks remain in place.
_JOB_WRITES = {
    'checkpoint_job', 'claim_job', 'renew_claim', 'release_job', 'record_task_feedback', 'prepare_handoff', 'accept_handoff', 'acknowledge_handoff', 'mark_context_read',
    'request_publication', 'resolve_publication', 'checkout_draft', 'commit_draft',
    'save_scenario', 'apply_scenario', 'save_work_checkpoint', 'save_artifact', 'receive_source', 'write_work_file',
    'publish_file', 'move_work_file', 'delete_work_file', 'record_sent', 'recover_artifacts',
}
_PROJECT_OPERATIONS = {'get_project', 'update_project', 'append_timeline', 'initialize_project_git',
                       'save_memory_note', 'delete_memory_note'}

BRIEF_BYTES = 16 * 1024

def bound_brief_payload(payload):
    """Keep the normal brief response unchanged unless it exceeds MCP budget."""
    encoded=lambda value: json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()
    def size(value):
        return len(encoded({'ok':True,'result':value}))
    if size(payload) <= BRIEF_BYTES:return payload
    def text(value, limit=240):
        return value if not isinstance(value,str) or len(value)<=limit else value[:limit]+'…'
    jid=payload['job_id']
    expand={'operation':'resume_job','jid':jid,'detail':'full'}
    work=payload.get('work_context',{})
    task=work.get('task_context') or {}
    progress=work.get('progress',{})
    primary=work.get('primary_draft')
    if isinstance(primary,dict) and isinstance(primary.get('path'),str) and len(primary['path'])>500:
        primary={key:value for key,value in primary.items() if key!='path'}
        primary['expand']=expand
    publications={}
    for project_id,page in payload.get('publication_decisions',{}).items():
        items=page.get('items',[]) if isinstance(page,dict) else []
        # Identity and reviewed hash are operational fields. Do not truncate them.
        pending=next((item for item in items if item.get('state')=='pending'), items[0] if items else None)
        publications[project_id]={'total':page.get('total',len(items)) if isinstance(page,dict) else len(items),
                                  'pending_total':page['pending_total'],
                                  'items':([{key:pending[key] for key in ('decision_id','state','source_hash','name') if key in pending}]
                                           if pending else []), 'expand':{'operation':'list_decisions','project_id':project_id,'jid':jid}}
    compact={'job_id':jid,'title':text(payload.get('title')),'goal':text(payload.get('goal')),
             'status':payload.get('status'),'revision':payload.get('revision'),
             'scope':payload.get('scope'),
             'directories':payload.get('directories',[])[:2],
             'task':{'working_on':text((task.get('context') or {}).get('working_on','')),
                     'resume_note':text((task.get('context') or {}).get('resume_note','')),
                     'next_steps':[text(value) for value in progress.get('next_steps',[])[:3]],
                     'open_issues':[text(value) for value in progress.get('open_issues',[])[:3]],
                     'open_issue_total':work.get('attention',{}).get('unresolved_issue_total',len(task.get('open_issues',[])))},
             'primary_draft':primary,'publication_decisions':publications,
             'recovery':payload.get('recovery'),'expand':expand}
    compact['brief_budget']={'limit':BRIEF_BYTES,'expand':expand}
    if size(compact) > BRIEF_BYTES:
        # Last resort remains executable. Paths and prose are expandable while
        # stable task, decision and content-hash identities remain intact.
        first_project=next((pid for pid,data in publications.items() if data['pending_total']),next(iter(publications),None))
        compact={'job_id':jid,'status':payload.get('status'),'revision':payload.get('revision'),
                 'open_issue_total':compact['task']['open_issue_total'],
                 'pending_review_total':work.get('attention',{}).get('pending_review_total',0),
                 'pending_publications':sum(group['pending_total'] for group in publications.values()),
                 'publication_decision':publications.get(first_project) if first_project else None,
                 'expand':expand,'brief_budget':{'limit':BRIEF_BYTES,'expand':expand}}
    return compact

class Service:
    def __init__(self,root):self.w=Workspace(root)
    def call(self,operation,args):
        # Metadata caches are scoped to a request, not a long-lived service.
        w=Workspace(self.w.root)
        if operation=='init':return w.init()
        if not w.path('.workspace/config.json').exists():raise Error('NOT_FOUND','Run init first')
        if operation=='backup':return Maintenance(w).backup()
        if operation=='restore':return Maintenance(w).restore(**args)
        gate=w.path('.workspace/gate.lock')
        # open_work creates a project only when the caller explicitly passes
        # create_project; that workspace-wide allocation needs the exclusive gate.
        exclusive=operation in _EXCLUSIVE_OPERATIONS or (operation=='open_work' and isinstance(args,dict) and bool(args.get('create_project')))
        with gate.open('a+') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                key=None
                if operation in _JOB_WRITES and isinstance(args.get('jid'),str):
                    key='service-job:'+args['jid']
                elif operation in _PROJECT_OPERATIONS and isinstance(args.get('project_id'),str):
                    key='service-project:'+args['project_id']
                with w.lock(key) if key is not None else nullcontext():
                    return self.dispatch(operation,args,workspace=w)
            finally:fcntl.flock(lock,fcntl.LOCK_UN)
    def dispatch(self,operation,args,*,workspace=None):
        w=workspace if workspace is not None else self.w
        if operation=='reconcile_remote':
            from .remote_store import RemoteStore
            return RemoteStore(w).reconcile()
        if operation in ('migrate_feishu','rollback_feishu'):
            from .migration import Migration
            return (Migration(w).run if operation=='migrate_feishu' else Migration(w).rollback)(**args)
        if operation=='feishu_query':
            from .feishu import Feishu
            args=dict(args)
            args.setdefault('projection',['项目ID','项目名称','状态'] if args.get('entity')=='projects' else ['任务ID','标题','所属项目ID','所属项目名称','状态','版本号'])
            return Feishu(w).rows(**args)
        from .workflow import Workflow
        flow=Workflow(w)
        from .files import Files
        files=Files(w)
        from .scenarios import Scenarios
        desktop = Scenarios(w)
        from .projects import Projects
        projects=Projects(w)
        from .memories import Memories
        memories=Memories(w)
        from .context import Context
        from .git_store import GitStore
        from .feedback import Feedback
        context=Context(w)
        jobs=Jobs(w)
        retrieval=Retrieval(w)
        timeline=Timeline(w)
        feedback=Feedback(w)

        def resume(jid,detail='brief',project_id=None):
            jobs.file(jid)  # Validate identity before using it as a lock key.
            with w.lock('service-job:'+jid):
                job=jobs.get(jid)
                result=jobs.resume_record(job,detail=detail)
                recovery=Artifacts(w).recovery_status(jid, job)
                if recovery['recoverable'] or recovery['pending']:
                    result['recovery']={**recovery,
                        'next_action':dict(operation='recover_artifacts',jid=jid)}
                result['work_context']=context.prepare_record(job,detail=detail,project_id=project_id)
                result['scenarios'] = desktop.bindings(jid)
                result['publication_decisions'] = {
                    pid: desktop.list_decisions(pid, jid=jid, limit=10)
                    for pid in job['scope']['project_ids']
                    if w.path(f'.workspace/decisions/{pid}/{jid}').is_dir()
                }
                if len(job['scope']['project_ids'])!=1 or not result['work_context'].get('observation'):
                    result['context_entry']=dict(operation='prepare_work_context',jid=jid)
                return bound_brief_payload(result) if detail=='brief' else result

        def resume_session(session_id):
            return resume(flow.session_job_id(session_id))

        def prepare_context(project_id=None,jid=None,session_id=None,intent='',detail='brief'):
            # Resolve the binding once; it could be rebound during this call.
            if session_id and not jid:
                jid=flow.session_job_id(session_id,missing_ok=True)
            if jid:
                jobs.file(jid)
                with w.lock('service-job:'+jid):
                    return context.prepare(project_id=project_id,jid=jid,intent=intent,detail=detail)
            return context.prepare(project_id=project_id,intent=intent,detail=detail)

        extra={'prepare_work_context':prepare_context,'get_project_overview':context.overview,
               'list_project_memory':timeline.list_memory,'read_project_memory':timeline.read_memory,
               'list_memory_notes':memories.list,'read_memory_note':memories.read,
               'save_memory_note':memories.save,'delete_memory_note':memories.delete,
               'mark_context_read':context.mark_read,'save_work_checkpoint':context.save,
               'read_project_file':retrieval.read_project_file,'compare_project_file':retrieval.compare_project_file}
        if operation=='initialize_project_git':
            return GitStore(w,args['project_id']).initialize()
        if operation=='read_memory_version':
            return retrieval.read_project_file(args['project_id'],'记忆/TIMELINE.md',args['commit'])
        methods={'get_project':projects.get,'update_project':projects.update,'write_work_file':files.write,'publish_file':files.publish,'move_work_file':files.move,'delete_work_file':files.delete,'recover_artifacts':Artifacts(w).recover,'job_directories':files.directories,'claim_job':flow.claim,'renew_claim':flow.renew,'release_job':flow.release,'resolve_job':flow.resolve,'bind_session':flow.bind,'resume_session':resume_session,'prepare_handoff':flow.prepare_handoff,'read_handoff':flow.read_handoff,'accept_handoff':flow.accept_handoff,'acknowledge_handoff':flow.acknowledge_handoff,'append_timeline':timeline.append,'list_projects':w.project_query,'create_project':w.project_create,'update_index':retrieval.update,'search_sources':retrieval.search,'read_source':retrieval.read,'create_job':jobs.create,'list_jobs':jobs.query,'list_jobs_full':jobs.list,'resume_job':resume,'checkpoint_job':jobs.checkpoint,'record_task_feedback':jobs.record_feedback,'doctor':Maintenance(w).doctor,'report_issue':feedback.report_issue,'open_work':Entry(w, resume).open}
        methods.update(extra)
        for name in ('record_host_binding', 'list_host_bindings', 'request_publication', 'resolve_publication', 'list_decisions', 'record_sent', 'list_sent', 'read_sent_version',
                     'checkout_draft', 'commit_draft', 'list_scenarios',
                     'read_scenario', 'save_scenario', 'apply_scenario'):
            methods[name] = getattr(desktop, name)
        if operation in ('save_artifact','receive_source'):
            args=dict(args)
            content=args.pop('content',None);encoded=args.pop('content_base64',None)
            copy_id=args.get('copy_id') if operation=='receive_source' else None
            if sum(value is not None for value in (content,encoded,copy_id))!=1:
                raise Error('INVALID_ARGUMENT','Supply text, base64, or controlled copy_id, exclusively')
            if copy_id is None:
                args['content']=base64.b64decode(encoded,validate=True) if encoded is not None else content.encode('utf-8')
            return (files.receive if operation=='receive_source' else Artifacts(w).save)(**args)
        if operation not in methods:raise Error('INVALID_ARGUMENT','Unknown operation')
        result=methods[operation](**args)
        if operation in ('get_project','create_job'):
            result['context_entry']=dict(operation='prepare_work_context',**({'jid':result['job_id']} if operation=='create_job' else {'project_id':result['project_id']}))
        if operation in ('checkpoint_job','release_job'):
            return jobs.brief(args['jid'])
        if operation in ('claim_job','renew_claim'):
            return {'job_id':result['job_id'],'revision':result['revision'],'lease':result['lease']}
        return result
