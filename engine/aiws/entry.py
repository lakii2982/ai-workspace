"""A2/A3 chat entry: open_work resolves pertinent work without ever guessing.

The entry never chooses on the caller's behalf. Unknown or ambiguous requests
return bounded, human-distinguishable candidates and the original intent with
zero writes; only explicit facts (project, alias, task, previously bound
session) or an explicit create_project/new_task decision proceed further.
"""
import hashlib

from .core import Error
from .jobs import Jobs
from .workflow import Workflow

CANDIDATE_LIMIT = 8
TITLE_LIMIT = 80
SOURCE_PREVIEW_LIMIT = 8
CLOSED = ('completed', 'cancelled')


class Entry:
    def __init__(self, w, resume):
        self.w = w
        self.jobs = Jobs(w)
        self.flow = Workflow(w)
        self.resume = resume

    def open(self, intent, project=None, jid=None, session_id=None, query='',
             request_id=None, create_project=False, new_task=False):
        intent = self._text(intent, 'intent', required=True)
        project = self._text(project, 'project')
        jid = self._text(jid, 'jid')
        session_id = self._text(session_id, 'session_id')
        query = self._text(query, 'query', blank_ok=True)
        if type(create_project) is not bool or type(new_task) is not bool:
            raise Error('INVALID_ARGUMENT', 'create_project and new_task must be boolean')
        if request_id is not None and (not isinstance(request_id, str) or not request_id.strip()):
            raise Error('INVALID_ARGUMENT', 'request_id must be a non-empty string')
        if create_project and project is None:
            raise Error('INVALID_ARGUMENT', 'create_project needs the project name to create')
        if session_id is not None and not 1 <= len(session_id) <= 200:
            raise Error('INVALID_ARGUMENT', 'Session ID required')
        bound_jid = self.flow.session_job_id(session_id, missing_ok=True) if session_id else None

        # 1) An explicit task must agree with any explicit project.
        if jid is not None:
            return self._open_jid(intent, jid, project=project, session_id=session_id,
                                  bound_jid=bound_jid, switch_session=True)
        # 2) An explicit project name, alias or ID narrows the destination.
        if project is not None:
            matches = self.flow.project_matches(project)
            if len(matches) > 1:
                return self._choices(intent, 'ambiguous_projects', projects=self._project_rows(matches),
                                     has_more_projects=len(matches) > CANDIDATE_LIMIT,
                                     session_id=session_id, bound_jid=bound_jid)
            if matches:
                return self._project_work(intent, matches[0], query=query, request_id=request_id,
                                          new_task=new_task, session_id=session_id, bound_jid=bound_jid)
            if not create_project or project.upper().startswith('PRJ-'):
                # Creation never applies to an ID, and without the explicit
                # decision a near miss stays a readable suggestion, not a write.
                create = None if project.upper().startswith('PRJ-') else dict(
                    operation='open_work', intent=intent, project=project, create_project=True)
                return self._choices(intent, 'unknown_project', projects=self._suggestions(project),
                                     create=create, session_id=session_id, bound_jid=bound_jid)
            # Validate before creating so a malformed creation never leaves a
            # half-open project behind.
            if new_task and (not isinstance(request_id, str) or not request_id):
                raise Error('INVALID_ARGUMENT', 'new_task requires request_id; creation must be retry-safe through a stable request ID')
            created = self.w.project_create(project)
            return self._project_work(intent, created, query=query, request_id=request_id,
                                      new_task=new_task, session_id=session_id, bound_jid=bound_jid,
                                      created_project=True)
        # 3) A previously bound task proceeds as prior work.
        if bound_jid is not None:
            return self._open_jid(intent, bound_jid, session_id=session_id, bound_jid=bound_jid)
        # 4) Unknown and unbound: readable candidates only, nothing is chosen or created.
        page = self.w.project_query(limit=CANDIDATE_LIMIT)
        return self._choices(intent, 'no_project', projects=page['items'],
                             has_more_projects=page['has_more'], tasks=self._task_scan(query),
                             expand=dict(operation='list_projects', query='', limit=100),
                             session_id=session_id, bound_jid=bound_jid)

    def _project_work(self, intent, project, *, query, request_id, new_task, session_id, bound_jid,
                      created_project=False):
        pid = project['project_id']
        if new_task:
            return self._create_task(intent, pid, request_id, created_project,
                                     session_id=session_id)
        found = self.jobs.query(project_id=pid, query=query, limit=CANDIDATE_LIMIT)
        rows = self._task_rows(found['items'])
        bound = next((row for row in rows if row['job_id'] == bound_jid), None)
        if bound is not None:
            return self._open_jid(intent, bound['job_id'], project_id=pid, session_id=session_id,
                                  bound_jid=bound_jid)
        if len(rows) == 1 and not found['has_more'] and query:
            return self._open_jid(intent, rows[0]['job_id'], project_id=pid, session_id=session_id,
                                  bound_jid=bound_jid, switch_session=True)
        if rows:
            return self._choices(intent, 'ambiguous_tasks', projects=[self._row(project)], tasks=rows,
                                 expand=dict(operation='list_jobs', project_id=pid, query=query),
                                 session_id=session_id, bound_jid=bound_jid)
        return self._choices(intent, 'no_task', projects=[self._row(project)],
                             closed_tasks=self._closed_tasks(pid, query),
                             create=dict(operation='open_work', intent=intent, project=pid,
                                         new_task=True, requires_request_id=True),
                             session_id=session_id, bound_jid=bound_jid,
                             created=dict(project=created_project, task=False))

    def _create_task(self, intent, pid, request_id, created_project, *, session_id):
        if not isinstance(request_id, str) or not request_id:
            raise Error('INVALID_ARGUMENT', 'new_task requires request_id; creation must be retry-safe through a stable request ID')
        title = intent.splitlines()[0].strip()[:TITLE_LIMIT] or intent[:TITLE_LIMIT]
        scope = {'include_company': False, 'include_professional': False,
                 'project_ids': [pid], 'topic_ids': []}
        request_file = self.w.path('.workspace/create-requests/'
                                   + hashlib.sha256(request_id.encode()).hexdigest() + '.json')
        replayed = request_file.exists()
        job = self.jobs.create(title=title, goal=intent, scope=scope, acceptance=[],
                               agent='open_work', request_id=request_id)
        result = self._open_jid(intent, job['job_id'], project_id=pid, session_id=session_id,
                                created={'project': created_project, 'task': True},
                                request_id=request_id, switch_session=True)
        result['replayed'] = replayed
        result['project_sources'] = self._project_sources(pid)
        return result

    def _project_sources(self, project_id):
        """A bounded, non-recursive next read after explicit project selection."""
        project=self.w.project_for(project_id)
        folder=self.w.path(project['path']+'/资料')
        entries=sorted((item for item in folder.iterdir()
                        if not item.name.startswith(('.', '~$'))),key=lambda item:item.name.casefold())
        items=[]
        for entry in entries[:SOURCE_PREVIEW_LIMIT]:
            if entry.is_file():
                items.append(dict(name=entry.name, kind='file',
                                  read=dict(operation='read_project_file', project_id=project_id,
                                            path='资料/'+entry.name)))
            elif entry.is_dir():
                items.append(dict(name=entry.name, kind='directory', recursive=False,
                                  note='Not listed recursively; use read_project_file with an exact child path'))
        return dict(project_id=project_id, folder='资料/', count=len(entries), items=items,
                    has_more=len(entries)>SOURCE_PREVIEW_LIMIT)

    def _open_jid(self, intent, jid, *, project=None, project_id=None, session_id=None,
                  bound_jid=None, created=None, request_id=None, switch_session=False):
        job = self.jobs.get(jid)
        if project is not None:
            matches = self.flow.project_matches(project)
            if len(matches) != 1:
                return self._choices(intent, 'ambiguous_projects' if matches else 'unknown_project',
                                     projects=self._project_rows(matches), session_id=session_id,
                                     bound_jid=bound_jid)
            project_id = matches[0]['project_id']
        scope = job['scope']['project_ids']
        if project_id is not None and project_id not in scope:
            raise Error('INVALID_SCOPE', 'Task is outside the selected project')
        if project_id is None and len(scope) > 1:
            return self._choices(intent, 'choose_project_for_task',
                                 projects=self._project_rows([self.w.project_for(pid) for pid in scope]),
                                 session_id=session_id, bound_jid=bound_jid,
                                 expand=dict(operation='open_work', intent=intent, jid=jid))
        project_id = project_id or scope[0]
        # Service owns the lifecycle (recovery, scenarios, publication decisions
        # and bounded resume payload).  The selected project is explicit so a
        # multi-project task never leaks the wrong project's work context.
        work = self.resume(jid, 'brief', project_id=project_id)
        result = {'status': 'opened', 'match': 'unique', 'intent': intent, 'work': work,
                  'project_id': project_id, 'created': created or {'project': False, 'task': False}}
        if request_id is not None:
            result['request_id'] = request_id
        if session_id is not None:
            if bound_jid == jid:
                state = 'kept'
            elif bound_jid is None:
                self.flow.bind(session_id, jid)
                state = 'bound'
            elif switch_session:
                self.flow.bind(session_id, jid)
                state = 'switched'
            else:
                state = 'elsewhere'
            binding = {'session_id': session_id, 'state': state,
                       'jid': bound_jid if state == 'elsewhere' else jid}
            if state == 'elsewhere':
                binding['switch'] = dict(operation='bind_session', session_id=session_id, jid=jid)
                binding['note'] = 'Previous binding left untouched; bind explicitly to move this session'
            result['session_binding'] = binding
        return result

    def _choices(self, intent, reason, *, projects=None, tasks=None, closed_tasks=None,
                 has_more_projects=False, create=None, expand=None, session_id=None,
                 bound_jid=None, created=None):
        result = {'status': 'candidates',
                  'match': 'ambiguous' if reason.startswith('ambiguous') else 'none',
                  'reason': reason, 'intent': intent,
                  'created': created or {'project': False, 'task': False}}
        if projects is not None:
            result['projects'] = projects
            result['has_more_projects'] = has_more_projects
        if tasks:
            result['tasks'] = tasks
        if closed_tasks:
            result['closed_tasks'] = closed_tasks
        if create:
            result['create'] = create
        if expand:
            result['expand'] = expand
        if session_id is not None:
            binding = {'session_id': session_id, 'state': 'bound' if bound_jid else 'unbound'}
            if bound_jid:
                binding['jid'] = bound_jid
            result['session_binding'] = binding
        return result

    def _suggestions(self, name):
        key = name.casefold()
        matches = [p for p in self.w.projects()
                   if key in p['name'].casefold() or any(key in x.casefold() for x in p.get('aliases', []))]
        return self._project_rows(sorted(matches, key=lambda p: p['project_id']))

    def _task_scan(self, query):
        if not query:
            return None
        return self._task_rows(self.jobs.query(query=query, limit=CANDIDATE_LIMIT)['items'])

    def _closed_tasks(self, pid, query):
        found = self.jobs.query(project_id=pid, query=query, status=None, limit=100)
        closed = [row for row in found['items'] if row['status'] in CLOSED]
        return self._task_rows(closed)

    def _task_rows(self, items):
        rows = []
        for row in items[:CANDIDATE_LIMIT]:
            scope = self.jobs.get(row['job_id'])['scope']['project_ids']
            rows.append({**row, 'project_ids': scope})
        return rows

    @staticmethod
    def _row(project):
        return {k: project.get(k) for k in ('project_id', 'name', 'status', 'path')}

    def _project_rows(self, projects):
        return [self._row(p) for p in projects[:CANDIDATE_LIMIT]]

    @staticmethod
    def _text(value, name, required=False, blank_ok=False):
        if value is None:
            if required:
                raise Error('INVALID_ARGUMENT', f'{name} is required')
            return None
        if not isinstance(value, str) or not value.strip():
            if blank_ok and value == '':
                return ''
            raise Error('INVALID_ARGUMENT', f'{name} must be a non-empty string')
        return value.strip()
