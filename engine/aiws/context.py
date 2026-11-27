"""Project-local context assembled from authoritative jobs, memory and Git."""
from collections import Counter
import re
from .core import Error, now
from .jobs import Jobs
from .memories import Memories


class Context:
    RESUME_MEMORY_ITEMS = 12
    RESUME_PENDING_REVIEW = 8
    RESUME_EVENTS = 8
    def __init__(self, workspace):
        self.w = workspace

    def tasks(self, project_id):
        jobs = Jobs(self.w)
        from .remote_store import enabled
        # Remote authority keeps its paged query semantics.  The local scan
        # optimization must not turn an overview into one remote read per job.
        if enabled(self.w):
            result, offset = [], 0
            while True:
                page=jobs.query(project_id=project_id,status=None,limit=100,offset=offset)
                result.extend(jobs.get(row['job_id']) for row in page['items'])
                if not page['has_more']:return result
                offset=page['next_offset']
        # An overview requires complete records. Repeated query pages would
        # rescan all job JSON files and then reread their selected records.
        records, _ = jobs.scan()
        return [job for job in records if project_id in job['scope']['project_ids']]

    def memories(self, project_id, **filters):
        from .timeline import Timeline
        items, events = [], []
        page=Timeline(self.w).list_memory(project_id,limit=100,**filters)
        while True:
            items.extend(page['items'])
            events.extend(page.get('events',[]))
            if not page['has_more']:break
            page=Timeline(self.w).list_memory(project_id,limit=100,offset=page['next_offset'],**filters)
        return {**page,'items':items,'events':events,'events_total':len(events),
                'has_more':False,'next_offset':None}

    @staticmethod
    def memory_view(data, detail):
        if detail=='full':
            return data
        result=dict(data)
        for key in ('items','tentative','pending_review','events'):
            result[key]=[{k:v for k,v in r.items() if k not in ('body','current_body','sources')}
                         for r in data.get(key,[])]
        result['legacy']=[{**r,'text':r['text'][:600],
                           'omitted_characters':max(0,len(r['text'])-600)} for r in data.get('legacy',[])]
        result['expand_note']='Use prepare_work_context(detail=full) or read_project_memory for original text and sources'
        return result

    @classmethod
    def resume_memory_view(cls, data):
        """Bound the default resume response and make every omission visible."""
        if not (data.get('total') or data.get('pending_review') or data.get('events_total') or data.get('legacy')):
            return {'items':[], 'resume_budget':{'totals':{},'omitted':{},
                    'expand':{'operation':'prepare_work_context','detail':'full'}}}
        result=cls.memory_view(data, 'brief')
        totals={'items':data.get('total', len(result.get('items', []))),
                'pending_review':len(result.get('pending_review', [])),
                'events':data.get('events_total', len(result.get('events', [])))}
        limits={'items':cls.RESUME_MEMORY_ITEMS,'pending_review':cls.RESUME_PENDING_REVIEW,
                'events':cls.RESUME_EVENTS}
        for key,limit in limits.items():
            result[key]=result.get(key, [])[:limit]
        result['resume_budget']={'limits':limits,'totals':totals,
            'omitted':{key:max(0, totals[key]-limit) for key,limit in limits.items()},
            'expand':{'operation':'prepare_work_context','detail':'full'}}
        return result

    def overview(self, project_id, detail='brief'):
        from .timeline import Timeline
        if detail not in ('brief', 'full'):
            raise Error('INVALID_ARGUMENT', 'detail must be brief or full')
        p = self.w.project_for(project_id)
        jobs = self.tasks(project_id)
        counts = dict(Counter(j['status'] for j in jobs))
        # A pending acceptance is more urgent than the newest ongoing work. Completed
        # history is excluded before this ordering, so it cannot hide a review item.
        order = {'review': 0, 'blocked': 1, 'in_progress': 2, 'new': 3}
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
        notes = Memories(self.w).overview(project_id, 'full' if detail=='full' else 'index')
        notes_summary = {k: notes[k] for k in ('total', 'index_path')}
        for key in ('counts', 'warnings', 'index_drift', 'expand', 'omitted'):
            if key in notes:
                notes_summary[key] = notes[key]
        if detail == 'full':
            notes_summary['items'] = notes['items']
            if 'index_text' in notes:
                notes_summary['index_text'] = notes['index_text']
        return dict(project={k: p.get(k) for k in ('project_id','name','goal','status','status_note','path')},
                    counts=counts, total_tasks=len(jobs), **sections, memory_digest=self.memory_view(memory,detail),
                    memory_notes=notes_summary,
                    coverage=dict(computed_at=now(), omitted=omitted,
                                  task_revisions={j['job_id']:j['revision'] for j in jobs}),
                    expand=dict(operation='get_project_overview', project_id=project_id, detail='full'))

    def prepare(self, project_id=None, jid=None, session_id=None, intent='', detail='brief'):
        if detail not in ('brief','full'):
            raise Error('INVALID_ARGUMENT', 'detail must be brief or full')
        if session_id and not jid:
            from .workflow import Workflow
            jid = Workflow(self.w).session_job_id(session_id, missing_ok=True)
        j = Jobs(self.w).get(jid) if jid else None
        return self._prepare(project_id, j, intent, detail, targeted=False)

    def prepare_record(self, j, detail='brief', project_id=None):
        """Resume directly from one authoritative job, without a project task rollup."""
        if detail not in ('brief', 'full'):
            raise Error('INVALID_ARGUMENT', 'detail must be brief or full')
        return self._prepare(project_id, j, '', detail, targeted=True)

    def _prepare(self, project_id, j, intent, detail, *, targeted):
        from .git_store import GitStore
        from .timeline import Timeline
        jid = j['job_id'] if j else None
        ctx = (j or {}).get('context', {})
        task_context = None
        if j:
            task_context = dict(jid=jid, title=j['title'], goal=j['goal'], status=j['status'],
                                revision=j['revision'], saved_at=j['updated_at'],
                                context=ctx, completed=j['completed'][-3:] if detail=='brief' else j['completed'],
                                next_steps=j['next_steps'], open_issues=j['open_issues'],
                                local_decisions=j['decisions'], artifacts=j['artifacts'])
            if detail=='brief':
                omitted={}
                for key in ('next_steps','open_issues','local_decisions','artifacts'):
                    values=task_context[key]
                    if len(values)>8:omitted[key]=len(values)-8
                    task_context[key]=values[:8]
                if omitted:task_context['omitted']=omitted
            ids = j['scope']['project_ids']
            if project_id and project_id not in ids:
                raise Error('INVALID_SCOPE', 'Project outside task')
            if not project_id:
                if len(ids) != 1:
                    return dict(identity=dict(jid=jid), project_candidates=ids,
                                task_context=task_context,
                                pending_updates=['Specify a project for this task'])
                project_id = ids[0]
        if not project_id:
            return dict(identity={}, pending_updates=['Locate the project and task first'])
        p = self.w.project_for(project_id)
        overview = (
            dict(project={k: p.get(k) for k in ('project_id','name','goal','status','status_note','path')},
                 expand=dict(operation='get_project_overview', project_id=project_id, detail='full'))
            if targeted else self.overview(project_id, detail)
        )
        git = GitStore(self.w, project_id)
        observation, changes, queue, changed_reviewed = None, [], [], []
        stale = set()
        index_report = None
        working = ctx.get('working_files', [])
        reviewed = {self.relative_file(p, r['path']):r['observation_commit']
                    for r in ctx.get('reviewed_files', [])}
        primary = {self.relative_file(p, x['path']) for x in working}
        linked = primary | reviewed.keys()
        if j:
            linked.update(self.relative_file(p, a['path']) for a in j['artifacts'])

        def visible(path):
            if not targeted or not path or path in linked:
                return True
            owner = re.match(r'(?:工作|成果)/(JOB-\d+)(?:_|/|$)', path)
            return not owner or owner[1] == jid

        if git.enabled:
            observation = git.observe(base=ctx.get('checkpoint_commit'), include_status=True)
            status = observation.pop('git_status')
            if not targeted:
                from .retrieval import Retrieval
                index_report=Retrieval(self.w).update(dict(include_company=False,include_professional=False,
                                                        project_ids=[project_id],topic_ids=[]))
            changes = [change for change in observation['changes']
                       if visible(change['path']) or
                       change.get('old_path') and visible(change['old_path'])]
            trees = {observation['commit']: observation['files']}
            for path, commit in reviewed.items():
                if commit not in trees:
                    try:
                        trees[commit] = git.file_oids(commit)
                    except Error as error:
                        if error.code not in ('INVALID_ARGUMENT', 'GIT_VERSION_MISSING'):
                            raise
                        trees[commit] = None
                previous = trees[commit]
                if previous is None:
                    state = 'baseline_unavailable'
                elif path not in observation['files']:
                    state = 'missing'
                elif previous.get(path) != observation['files'][path]:
                    state = 'changed'
                else:
                    continue
                changed_reviewed.append(dict(path=path, state=state, reviewed_commit=commit))
            stale = {r['path'] for r in changed_reviewed}
            for path in observation['files']:
                # Metadata is available through dedicated context/memory sections.
                if (not visible(path) or path.startswith(('.', '记忆/'))
                        or path == 'PROJECT.md'):
                    continue
                if path not in reviewed or path in stale:
                    queue.append(dict(path=path, observation_commit=observation['commit'],
                                      reason='new_or_changed_since_read',
                                      read=dict(operation='read_project_file', project_id=project_id,
                                                path=path, commit=observation['commit'])))
        else:
            status = git.inspect()
        # Resume is deliberately bounded. Broad project research remains an
        # explicit prepare_work_context call, so a long timeline cannot bloat
        # every new chat.
        memory = (Timeline(self.w).list_memory(project_id, limit=self.RESUME_MEMORY_ITEMS)
                  if targeted and detail=='brief' else self.memories(project_id))
        references=[]
        for event_id in ctx.get('memory_refs',[]):
            try:
                references.append(Timeline(self.w).read_memory(project_id,event_id))
            except Error as error:
                references.append(dict(event_id=event_id,state='unavailable',reason=error.message))
        terms=re.findall(r'[A-Za-z0-9_]+|[\u4e00-\u9fff]+',intent)
        keywords=[]
        for term in terms:
            if len(term)>6 and re.fullmatch(r'[\u4e00-\u9fff]+',term):
                keywords.extend(term[i:i+4] for i in range(len(term)-3))
            else:
                keywords.append(term)
        related = Timeline(self.w).list_memory(project_id,query=' '.join(keywords),limit=20) if keywords else None
        draft = self.primary_draft(p, j, observation) if j else None
        if draft and draft.get('path'):
            primary.add(draft['path'])
        pending_updates=[] if status.get('enabled') else ['Project Git is not initialized']
        if observation:
            for path in primary:
                if path not in observation['files']:
                    replacement=next((c['path'] for c in changes if c.get('old_path')==path),None)
                    pending_updates.append(dict(path=path,state='moved' if replacement else 'missing',
                                                candidate=replacement))
        queue.sort(key=lambda x: (x['path'] not in primary, x['path'] not in stale, x['path']))
        omitted = max(0, len(queue)-20) if detail=='brief' else 0
        public_observation=({k:v for k,v in observation.items() if k not in ('files','changes','ignored')}
                            if observation else None)
        coverage=dict(unread_files=len(queue),omitted_reading=omitted,
                      total_changes=len(changes),omitted_changes=0)
        status={**status,'status':[r for r in status['status'] if visible(r['path'])],
                'ignored':[path for path in status['ignored'] if visible(path)]}
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
        progress=dict(working_on=ctx.get('working_on',''), resume_note=ctx.get('resume_note',''),
                      next_steps=task_context['next_steps'] if task_context else [],
                      open_issues=task_context['open_issues'] if task_context else [])
        attention=dict(pending_review_total=len(memory.get('pending_review',[])),
                       unresolved_issue_total=len(j['open_issues']) if j else 0)
        lanes={}
        if any(progress.values()):lanes['progress']=progress
        if any(attention.values()):lanes['attention']=attention
        return dict(identity=dict(project_id=project_id, jid=jid), project_brief=overview,
                    task_context=task_context, git_status=status, observation=public_observation,
                    primary_draft=draft, changed_reviewed_files=changed_reviewed,
                    index=index_report,
                    **lanes,
                    git_changes=changes,
                    memory=(self.resume_memory_view(memory) if targeted and detail=='brief'
                            else self.memory_view(memory,detail)), memory_references=references,
                    memory_notes=Memories(self.w).overview(project_id, 'full' if detail=='full' else 'index'),
                    related_memory=self.memory_view(related,detail) if related else None,
                    reading_queue=queue[:20] if detail=='brief' else queue,
                    coverage=coverage,
                    pending_updates=pending_updates,
                    expand=dict(operation='prepare_work_context', project_id=project_id, jid=jid,
                                intent=intent, detail='full'))

    def primary_draft(self, project, job, observation):
        marked = [self.relative_file(project, r['path']) for r in job.get('context', {}).get('working_files', [])
                  if r.get('role') in ('主稿', '主文档', 'main', 'primary', 'main_draft', 'primary_draft')]
        if not marked:
            folder = '工作/' + job.get('directory_name', job['job_id']) + '/工作稿/'
            marked = [self.relative_file(project, a['path']) for a in job['artifacts']
                      if a['path'].startswith(project['path']+'/'+folder)]
        paths = set(marked)
        if not paths:
            return None
        if len(paths) > 1:
            return dict(state='ambiguous', candidates=[dict(path=path) for path in sorted(paths)],
                        hint='Several draft candidates are marked; choose one explicitly before resuming it')
        path = paths.pop()
        if observation:
            return dict(path=path, state='available' if path in observation['files'] else 'missing',
                        observation_commit=observation['commit'])
        return dict(path=path, state='not_checked')

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
        observed=git.file_oids(observation_commit)
        for path in paths:
            if observed.get(path) is None:
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
        if git.enabled or ctx.get('checkpoint_commit'):
            snap = git.snapshot(checkpoint_id=jid+'-'+str(revision+1))
            ctx.update(checkpoint_commit=snap['commit'], checkpoint_ref=snap['ref'],
                       base_commit=git.inspect().get('head'))
        return jobs.checkpoint(jid,revision,{**(patch or {}),'context':ctx},lease_token=lease_token)
