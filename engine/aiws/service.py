import base64
import fcntl
from .core import Workspace, Error
from .retrieval import Retrieval
from .jobs import Jobs, Artifacts
from .maintenance import Maintenance
from .timeline import Timeline

class Service:
    def __init__(self,root):self.w=Workspace(root)
    def call(self,operation,args):
        if operation=='init':return self.w.init()
        if not self.w.path('.workspace/config.json').exists():raise Error('NOT_FOUND','Run init first')
        if operation=='backup':return Maintenance(self.w).backup()
        if operation=='restore':return Maintenance(self.w).restore(**args)
        gate=self.w.path('.workspace/gate.lock')
        with gate.open('a+') as lock:
            fcntl.flock(lock,fcntl.LOCK_SH)
            try:
                with self.w.lock('coordinator'):
                    return self.dispatch(operation,args)
            finally:fcntl.flock(lock,fcntl.LOCK_UN)
    def dispatch(self,operation,args):
        if operation=='reconcile_remote':
            from .remote_store import RemoteStore
            return RemoteStore(self.w).reconcile()
        if operation in ('migrate_feishu','rollback_feishu'):
            from .migration import Migration
            return (Migration(self.w).run if operation=='migrate_feishu' else Migration(self.w).rollback)(**args)
        if operation in ('feishu_query','feishu_stage_job'):
            from .feishu import Feishu
            remote=Feishu(self.w)
            if operation=='feishu_query':
                args=dict(args)
                args.setdefault('projection',['项目ID','项目名称','状态'] if args.get('entity')=='projects' else ['任务ID','标题','所属项目ID','所属项目名称','状态','版本号'])
            return (remote.rows if operation=='feishu_query' else remote.stage_job)(**args)
        from .workflow import Workflow
        flow=Workflow(self.w)
        from .files import Files
        files=Files(self.w)
        from .projects import Projects
        projects=Projects(self.w)
        from .context import Context
        from .git_store import GitStore
        context=Context(self.w)
        extra={'prepare_work_context':context.prepare,'get_project_overview':context.overview,
               'list_project_memory':Timeline(self.w).list_memory,'read_project_memory':Timeline(self.w).read_memory,
               'mark_context_read':context.mark_read,'save_work_checkpoint':context.save,
               'read_project_file':Retrieval(self.w).read_project_file,
               'compare_project_file':Retrieval(self.w).compare_project_file}
        if operation=='initialize_project_git':
            return GitStore(self.w,args['project_id']).initialize()
        if operation=='read_memory_version':
            return Retrieval(self.w).read_project_file(args['project_id'],'记忆/TIMELINE.md',args['commit'])
        methods={'get_project':projects.get,'update_project':projects.update,'write_work_file':files.write,'publish_file':files.publish,'job_directories':files.directories,'claim_job':flow.claim,'renew_claim':flow.renew,'release_job':flow.release,'resolve_job':flow.resolve,'bind_session':flow.bind,'resume_session':flow.session,'append_timeline':Timeline(self.w).append,'list_projects':self.w.project_query,'create_project':self.w.project_create,'update_index':Retrieval(self.w).update,'search_sources':Retrieval(self.w).search,'read_source':Retrieval(self.w).read,'create_job':Jobs(self.w).create,'list_jobs':Jobs(self.w).query,'list_jobs_full':Jobs(self.w).list,'resume_job':Jobs(self.w).resume,'checkpoint_job':Jobs(self.w).checkpoint,'doctor':Maintenance(self.w).doctor}
        methods.update(extra)
        if operation=='save_artifact':
            args=dict(args)
            content=args.pop('content',None);encoded=args.pop('content_base64',None)
            if (content is None)==(encoded is None):raise Error('INVALID_ARGUMENT','Supply text or base64, exclusively')
            args['content']=base64.b64decode(encoded,validate=True) if encoded is not None else content.encode('utf-8')
            return Artifacts(self.w).save(**args)
        if operation not in methods:raise Error('INVALID_ARGUMENT','Unknown operation')
        result=methods[operation](**args)
        if operation in ('resume_job','resume_session'):
            jid=result['job_id']
            ids=result['scope']['project_ids']
            if len(ids)==1 and GitStore(self.w,ids[0]).enabled:
                result['work_context']=context.prepare(jid=jid,detail=args.get('detail','brief'))
            else:
                result['context_entry']=dict(operation='prepare_work_context',jid=jid)
        if operation in ('get_project','create_job'):
            result['context_entry']=dict(operation='prepare_work_context',**({'jid':result['job_id']} if operation=='create_job' else {'project_id':result['project_id']}))
        if operation in ('checkpoint_job','release_job'):
            return Jobs(self.w).brief(args['jid'])
        if operation in ('claim_job','renew_claim'):
            return {'job_id':result['job_id'],'revision':result['revision'],'lease':result['lease']}
        return result
