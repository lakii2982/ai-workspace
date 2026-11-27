import base64
import fcntl
import json
from .core import Workspace, Error
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
    if len(encoded(payload)) <= BRIEF_BYTES:return payload
    result=json.loads(json.dumps(payload,ensure_ascii=False))
    def short(value,limit=240):
        return value if not isinstance(value,str) or len(value)<=limit else value[:limit]+'…'
    def compact(value, limit=240):
        if isinstance(value,str):return short(value,limit)
        if isinstance(value,list):return [compact(item,limit) for item in value[:8]]
        if isinstance(value,dict):return {key:compact(item,limit) for key,item in value.items()}
        return value
    for key in ('title','goal'):
        result[key]=short(result.get(key),400)
    work=result.get('work_context',{})
    task=work.get('task_context') or {}
    for key in ('title','goal'):
        task[key]=short(task.get(key),400)
    context=task.get('context')
    if isinstance(context,dict):
        task['context']={key:context[key] for key in ('working_on','resume_note','working_files') if key in context}
        task['context_omitted']=True
    decisions=result.get('publication_decisions',{})
    for page in decisions.values():
        if not isinstance(page,dict):continue
        items=page.get('items',[])
        page['items']=[{key:short(item.get(key),160) for key in
                        ('decision_id','state','name','source_hash','created_at','reason') if key in item}
                       for item in items[:8]]
        page['omitted']=max(0,len(items)-len(page['items']))
    # Memory evidence and publication history are expandable.  Keep a stable
    # indicator rather than silently dropping them when an extreme job remains.
    work['memory_references']=[{'event_id':item.get('event_id'),'state':item.get('state')}
                               for item in work.get('memory_references',[])[:4]]
    result=compact(result)
    if len(encoded(result)) > BRIEF_BYTES:
        result=compact(result,48)
    result['brief_budget']={'bytes':len(encoded(result)),'limit':BRIEF_BYTES,
                            'expand':{'operation':'resume_job','jid':result.get('job_id'),'detail':'full'}}
    return result

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
        with gate.open('a+') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX if operation in _EXCLUSIVE_OPERATIONS else fcntl.LOCK_SH)
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

        def resume(jid,detail='brief'):
            jobs.file(jid)  # Validate identity before using it as a lock key.
            with w.lock('service-job:'+jid):
                job=jobs.get(jid)
                result=jobs.resume_record(job,detail=detail)
                recovery=Artifacts(w).recovery_status(jid, job)
                if recovery['recoverable'] or recovery['pending']:
                    result['recovery']={**recovery,
                        'next_action':dict(operation='recover_artifacts',jid=jid)}
                result['work_context']=context.prepare_record(job,detail=detail)
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
        methods={'get_project':projects.get,'update_project':projects.update,'write_work_file':files.write,'publish_file':files.publish,'move_work_file':files.move,'delete_work_file':files.delete,'recover_artifacts':Artifacts(w).recover,'job_directories':files.directories,'claim_job':flow.claim,'renew_claim':flow.renew,'release_job':flow.release,'resolve_job':flow.resolve,'bind_session':flow.bind,'resume_session':resume_session,'prepare_handoff':flow.prepare_handoff,'read_handoff':flow.read_handoff,'accept_handoff':flow.accept_handoff,'acknowledge_handoff':flow.acknowledge_handoff,'append_timeline':timeline.append,'list_projects':w.project_query,'create_project':w.project_create,'update_index':retrieval.update,'search_sources':retrieval.search,'read_source':retrieval.read,'create_job':jobs.create,'list_jobs':jobs.query,'list_jobs_full':jobs.list,'resume_job':resume,'checkpoint_job':jobs.checkpoint,'record_task_feedback':jobs.record_feedback,'doctor':Maintenance(w).doctor,'report_issue':feedback.report_issue}
        methods.update(extra)
        for name in ('record_host_binding', 'list_host_bindings', 'request_publication', 'resolve_publication', 'list_decisions', 'record_sent', 'list_sent', 'read_sent_version',
                     'checkout_draft', 'commit_draft', 'list_scenarios',
                     'read_scenario', 'save_scenario', 'apply_scenario'):
            methods[name] = getattr(desktop, name)
        if operation in ('save_artifact','receive_source'):
            args=dict(args)
            content=args.pop('content',None);encoded=args.pop('content_base64',None)
            if (content is None)==(encoded is None):raise Error('INVALID_ARGUMENT','Supply text or base64, exclusively')
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
