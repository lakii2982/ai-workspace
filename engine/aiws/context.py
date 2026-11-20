"""Project-local context assembled from authoritative jobs, memory and Git."""
from pathlib import Path
from collections import Counter
from .core import Error, load, now
from .jobs import Jobs


class Context:
    def __init__(self, workspace):
        self.w = workspace

    def tasks(self, project_id):
        jobs = Jobs(self.w)
        result, offset = [], 0
        while True:
            page = jobs.query(project_id=project_id, status=None, limit=100, offset=offset)
            result.extend(jobs.get(row['job_id']) for row in page['items'])
            if not page['has_more']:
                return result
            offset = page['next_offset']

    def memories(self, project_id, **filters):
        from .timeline import Timeline
        page=Timeline(self.w).list_memory(project_id,limit=100,**filters)
        items=list(page['items'])
        while page['has_more']:
            page=Timeline(self.w).list_memory(project_id,limit=100,offset=page['next_offset'],**filters)
            items.extend(page['items'])
        return {**page,'items':items,'has_more':False,'next_offset':None}

    @staticmethod
    def memory_view(data, detail):
        if detail=='full':
            return data
        result=dict(data)
        for key in ('items','tentative','pending_review'):
            result[key]=[{k:v for k,v in r.items() if k not in ('body','current_body','sources')}
                         for r in data.get(key,[])]
        result['legacy']=[{**r,'text':r['text'][:600],
                           'omitted_characters':max(0,len(r['text'])-600)} for r in data.get('legacy',[])]
        result['expand_note']='Use prepare_work_context(detail=full) or read_project_memory for original text and sources'
        return result

    def overview(self, project_id, detail='brief'):
        from .timeline import Timeline
        if detail not in ('brief', 'full'):
            raise Error('INVALID_ARGUMENT', 'detail must be brief or full')
        p = self.w.project_for(project_id)
        jobs = self.tasks(project_id)
        counts = dict(Counter(j['status'] for j in jobs))
        order = {'in_progress': 0, 'review': 1, 'blocked': 2, 'new': 3}
        active = sorted((j for j in jobs if j['status'] in order),
                        key=lambda j: j['updated_at'], reverse=True)
        active.sort(key=lambda j: order[j['status']])
        results, issues, steps = [], [], []
        for j in sorted(jobs, key=lambda j: j['updated_at'], reverse=True):
            results.extend(dict(jid=j['job_id'], **a) for a in j['artifacts'])
            issues.extend(dict(jid=j['job_id'], text=x) for x in j['open_issues'])
            if j['status'] not in ('completed', 'cancelled'):
                steps.extend(dict(jid=j['job_id'], text=x) for x in j['next_steps'])
        sections = dict(tasks=[{k: j[k] for k in ('job_id','title','status','updated_at')} for j in active],
                        recent_results=results, open_items=issues, next_steps=steps)
        limits = dict(tasks=5, recent_results=3, open_items=5, next_steps=3)
        omitted = {}
        if detail == 'brief':
            for key, limit in limits.items():
                omitted[key] = max(0, len(sections[key])-limit)
                sections[key] = sections[key][:limit]
        memory = self.memories(project_id) if detail=='full' else Timeline(self.w).list_memory(project_id, limit=20)
        return dict(project={k: p.get(k) for k in ('project_id','name','goal','status','status_note','path')},
                    counts=counts, total_tasks=len(jobs), **sections, memory_digest=self.memory_view(memory,detail),
                    coverage=dict(computed_at=now(), omitted=omitted,
                                  task_revisions={j['job_id']:j['revision'] for j in jobs}),
                    expand=dict(operation='get_project_overview', project_id=project_id, detail='full'))

    def prepare(self, project_id=None, jid=None, session_id=None, intent='', detail='brief'):
        from .git_store import GitStore
        from .timeline import Timeline
        if detail not in ('brief','full'):
            raise Error('INVALID_ARGUMENT', 'detail must be brief or full')
        if session_id and not jid:
            import hashlib
            binding = self.w.path('.workspace/sessions/'+hashlib.sha256(session_id.encode()).hexdigest()+'.json')
            if binding.exists():
                jid = load(binding)['jid']
        j = Jobs(self.w).get(jid) if jid else None
        if j:
            ids = j['scope']['project_ids']
            if project_id and project_id not in ids:
                raise Error('INVALID_SCOPE', 'Project outside task')
            if not project_id:
                if len(ids) != 1:
                    return dict(identity=dict(jid=jid), project_candidates=ids,
                                pending_updates=['Specify a project for this task'])
                project_id = ids[0]
        if not project_id:
            return dict(identity={}, pending_updates=['Locate the project and task first'])
        p = self.w.project_for(project_id)
        overview = self.overview(project_id, detail)
        ctx = (j or {}).get('context', {})
        git = GitStore(self.w, project_id)
        status = git.inspect()
        observation, changes, queue = None, [], []
        if status.get('enabled'):
            observation = git.observe(base=ctx.get('checkpoint_commit'))
            from .retrieval import Retrieval
            index_report=Retrieval(self.w).update(dict(include_company=False,include_professional=False,
                                                    project_ids=[project_id],topic_ids=[]))
            changes = observation['changes']
            reviewed = {r['path']:r['observation_commit'] for r in ctx.get('reviewed_files', [])}
            for path, oid in observation['files'].items():
                # Metadata is available through dedicated context/memory sections.
                if path.startswith('.') or path in ('PROJECT.md','记忆/TIMELINE.md'):
                    continue
                seen = False
                if path in reviewed:
                    try:
                        seen = git.file_oid(reviewed[path], path) == oid
                    except Error:
                        pass
                if not seen:
                    queue.append(dict(path=path, observation_commit=observation['commit'],
                                      reason='new_or_changed_since_read',
                                      read=dict(operation='read_project_file', project_id=project_id,
                                                path=path, commit=observation['commit'])))
        # Constraints are never lost behind pagination or relevance ranking.
        memory = self.memories(project_id)
        references=[]
        for event_id in ctx.get('memory_refs',[]):
            try:
                references.append(Timeline(self.w).read_memory(project_id,event_id))
            except Error as error:
                references.append(dict(event_id=event_id,state='unavailable',reason=error.message))
        import re
        terms=re.findall(r'[A-Za-z0-9_]+|[\u4e00-\u9fff]+',intent)
        keywords=[]
        for term in terms:
            if len(term)>6 and re.fullmatch(r'[\u4e00-\u9fff]+',term):
                keywords.extend(term[i:i+4] for i in range(len(term)-3))
            else:
                keywords.append(term)
        related = Timeline(self.w).list_memory(project_id,query=' '.join(keywords),limit=20) if keywords else None
        working = ctx.get('working_files', [])
        primary = {self.relative_file(p, x['path']) for x in working}
        pending_updates=[] if status.get('enabled') else ['Project Git is not initialized']
        if observation:
            for path in primary:
                if path not in observation['files']:
                    replacement=next((c['path'] for c in changes if c.get('old_path')==path),None)
                    pending_updates.append(dict(path=path,state='moved' if replacement else 'missing',
                                                candidate=replacement))
        queue.sort(key=lambda x: (x['path'] not in primary, x['path']))
        omitted = max(0, len(queue)-20) if detail=='brief' else 0
        task_context = None
        if j:
            task_context = dict(jid=jid, title=j['title'], goal=j['goal'], status=j['status'],
                                revision=j['revision'], saved_at=j['updated_at'],
                                context=ctx, completed=j['completed'][-3:] if detail=='brief' else j['completed'],
                                next_steps=j['next_steps'], open_issues=j['open_issues'],
                                local_decisions=j['decisions'], artifacts=j['artifacts'])
        public_observation=({k:v for k,v in observation.items() if k not in ('files','changes','ignored')}
                            if observation else None)
        coverage=dict(unread_files=len(queue),omitted_reading=omitted,
                      total_changes=len(changes),omitted_changes=0)
        if detail=='brief':
            changes.sort(key=lambda x:(x['path'] not in primary,x['path']))
            coverage['omitted_changes']=max(0,len(changes)-20)
            changes=changes[:20]
            status={**status,'status':status['status'][:10],'ignored':status['ignored'][:10],
                    'total_status':len(status['status']),'total_ignored':len(status['ignored'])}
            if public_observation:
                nested=public_observation.get('unsupported_repositories',[])
                public_observation={**public_observation,'unsupported_repositories':nested[:3],
                                    'total_unsupported_repositories':len(nested)}
        return dict(identity=dict(project_id=project_id, jid=jid), project_brief=overview,
                    task_context=task_context, git_status=status, observation=public_observation,
                    index=index_report if status.get('enabled') else None,
                    git_changes=changes, memory=self.memory_view(memory,detail), memory_references=references,
                    related_memory=self.memory_view(related,detail) if related else None,
                    reading_queue=queue[:20] if detail=='brief' else queue,
                    coverage=coverage,
                    pending_updates=pending_updates,
                    expand=dict(operation='prepare_work_context', project_id=project_id, jid=jid,
                                intent=intent, detail='full'))

    @staticmethod
    def relative_file(project, path):
        prefix=project['path']+'/'
        return path[len(prefix):] if path.startswith(prefix) else path

    def mark_read(self, jid, observation_commit, paths, lease_token=None):
        from .git_store import GitStore
        jobs=Jobs(self.w)
        j=jobs.get(jid)
        if len(j['scope']['project_ids']) != 1:
            raise Error('INVALID_SCOPE', 'Reading acknowledgement requires a single-project task')
        if not isinstance(paths,list) or not paths or any(not isinstance(x,str) for x in paths):
            raise Error('INVALID_ARGUMENT', 'Supply paths actually read')
        git=GitStore(self.w,j['scope']['project_ids'][0])
        ctx=dict(j.get('context',{}))
        reviewed={r['path']:r for r in ctx.get('reviewed_files',[])}
        for path in paths:
            if git.file_oid(observation_commit,path) is None:
                raise Error('NOT_FOUND','Read path is absent from observation')
            reviewed[path]=dict(path=path,observation_commit=observation_commit)
        ctx['reviewed_files']=list(reviewed.values())
        return jobs.checkpoint(jid,j['revision'],dict(context=ctx),lease_token=lease_token)

    def save(self, jid, revision, context_patch=None, patch=None, lease_token=None):
        from .git_store import GitStore
        from .workflow import check_lease
        from .remote_store import enabled
        jobs=Jobs(self.w)
        j=jobs.get(jid)
        check_lease(j,lease_token,require=enabled(self.w))
        if revision != j['revision']:
            raise Error('CONFLICT', 'Task revision changed')
        if len(j['scope']['project_ids'])!=1:
            raise Error('INVALID_SCOPE','Checkpoint requires a single-project task')
        ctx={**j.get('context',{}),**(context_patch or {})}
        Jobs.validate_context(ctx)
        git=GitStore(self.w,j['scope']['project_ids'][0])
        snap=git.snapshot(checkpoint_id=jid+'-'+str(revision+1))
        ctx.update(checkpoint_commit=snap['commit'],checkpoint_ref=snap['ref'],base_commit=git.inspect().get('head'))
        return jobs.checkpoint(jid,revision,{**(patch or {}),'context':ctx},lease_token=lease_token)
