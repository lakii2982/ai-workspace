"""open_work 入口契约：显式事实优先、候选零写入、创建必须显式且幂等。"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aiws.core import Error, Workspace, put_json
from aiws.jobs import Jobs
from aiws.service import Service


def scope(*pids):
    return dict(include_company=False, include_professional=False, project_ids=list(pids), topic_ids=[])


def workspace(tmp_path, names=()):
    w = Workspace(tmp_path)
    w.init()
    return w, [w.project_create(name) for name in names]


def snapshot(w):
    """User-visible work state: jobs, projects, sessions and creation requests."""
    return dict(jobs=[j['job_id'] for j in Jobs(w).list()],
                projects=[p['project_id'] for p in w.projects()],
                sessions=list_files(w, '.workspace/sessions'),
                requests=list_files(w, '.workspace/create-requests'))


def list_files(w, relative):
    folder = w.path(relative)
    return sorted(x.name for x in folder.glob('*')) if folder.is_dir() else []


def aliases(service, project, alias):
    current = service.call('get_project', dict(project_id=project['project_id']))
    service.call('update_project', dict(project_id=project['project_id'],
                                        expected_hash=current['state_hash'],
                                        patch=dict(aliases=[alias])))


def test_unbound_request_lists_candidates_without_any_write(tmp_path):
    w, (p,) = workspace(tmp_path, ['唯一项目'])
    Jobs(w).create('已有任务', '目标', scope(p['project_id']))
    before = snapshot(w)
    s = Service(tmp_path)
    result = s.call('open_work', dict(intent='写一份采购周报'))
    assert result['status'] == 'candidates'
    assert result['reason'] == 'no_project'
    assert result['match'] == 'none'
    assert result['intent'] == '写一份采购周报'
    # Never silently choose the sole project of the workspace.
    assert [x['project_id'] for x in result['projects']] == [p['project_id']]
    assert result['created'] == {'project': False, 'task': False}
    assert result['expand']['operation'] == 'list_projects'
    assert snapshot(w) == before


def test_candidates_scan_tasks_by_query_without_writing(tmp_path):
    w, (p, other) = workspace(tmp_path, ['项目甲', '项目乙'])
    Jobs(w).create('采购周报初稿', '汇总', scope(p['project_id']))
    Jobs(w).create('无关任务', '别的', scope(other['project_id']))
    before = snapshot(w)
    s = Service(tmp_path)
    result = s.call('open_work', dict(intent='写周报', query='周报'))
    assert result['status'] == 'candidates' and result['reason'] == 'no_project'
    assert [(x['job_id'], x['project_ids']) for x in result['tasks']] == \
        [('JOB-0001', [p['project_id']])]
    assert snapshot(w) == before


def test_explicit_project_query_opens_sole_matching_task_and_honors_alias(tmp_path):
    w, (p,) = workspace(tmp_path, ['周报项目'])
    s = Service(tmp_path)
    job = Jobs(w).create('采购周报', '输出周报初稿', scope(p['project_id']))
    aliases(s, p, 'weekly')
    for name in ('周报项目', 'weekly', 'PRJ-0001', 'prj-0001'):
        result = s.call('open_work', dict(intent='继续周报', project=name, query='采购周报'))
        assert result['status'] == 'opened', name
        assert result['match'] == 'unique'
        assert result['work']['job_id'] == job['job_id']
        assert result['work']['work_context']['task_context']['goal'] == '输出周报初稿'
        assert result['intent'] == '继续周报'
        assert result['created'] == {'project': False, 'task': False}


def test_ambiguous_tasks_require_choice_then_follow_through_first_draft(tmp_path):
    w, (p,) = workspace(tmp_path, ['选择项目'])
    s = Service(tmp_path)
    first = Jobs(w).create('周报 A', '目标A', scope(p['project_id']))
    second = Jobs(w).create('周报 B', '目标B', scope(p['project_id']))
    before = snapshot(w)
    result = s.call('open_work', dict(intent='写周报', project='选择项目'))
    assert result['status'] == 'candidates' and result['reason'] == 'ambiguous_tasks'
    assert result['match'] == 'ambiguous'
    assert {x['job_id'] for x in result['tasks']} == {first['job_id'], second['job_id']}
    assert all(x['project_ids'] == [p['project_id']] for x in result['tasks'])
    assert result['expand'] == dict(operation='list_jobs', project_id=p['project_id'], query='')
    assert snapshot(w) == before
    # The human picks one; the entry honors the exact task and follows through
    # with the existing service operations.
    opened = s.call('open_work', dict(intent='写周报', jid=second['job_id']))
    assert opened['status'] == 'opened' and opened['work']['job_id'] == second['job_id']
    assert opened['intent'] == '写周报'
    s.call('write_work_file', dict(jid=second['job_id'], project_id=p['project_id'],
                                   name='周报.md', content='# 周报草稿'))
    revision = Jobs(w).get(second['job_id'])['revision']
    saved = s.call('checkpoint_job', dict(jid=second['job_id'], revision=revision,
                                          patch=dict(status='in_progress',
                                                     context=dict(working_on='周报初稿'))))
    assert saved['status'] == 'in_progress'
    assert Jobs(w).get(second['job_id'])['context']['working_on'] == '周报初稿'
    resumed = s.call('resume_job', dict(jid=second['job_id']))
    assert resumed['work_context']['task_context']['context']['working_on'] == '周报初稿'


def test_query_narrows_tasks_inside_an_explicit_project(tmp_path):
    w, (p,) = workspace(tmp_path, ['检索项目'])
    s = Service(tmp_path)
    Jobs(w).create('周报任务', '目标', scope(p['project_id']))
    Jobs(w).create('台账任务', '目标', scope(p['project_id']))
    result = s.call('open_work', dict(intent='更新台账', project='检索项目', query='台账'))
    assert result['status'] == 'opened' and result['work']['title'] == '台账任务'


def test_new_task_creation_is_intentional_idempotent_and_distinct(tmp_path):
    w, (p,) = workspace(tmp_path, ['新工作项目'])
    s = Service(tmp_path)
    intent = '整理供应商台账并输出初稿'
    before = snapshot(w)
    preview = s.call('open_work', dict(intent=intent, project='新工作项目'))
    assert preview['status'] == 'candidates' and preview['reason'] == 'no_task'
    assert preview['create'] == dict(operation='open_work', intent=intent,
                                     project=p['project_id'], new_task=True,
                                     requires_request_id=True)
    assert snapshot(w) == before  # candidates stay read-only
    with pytest.raises(Error):
        s.call('open_work', dict(intent=intent, project='新工作项目', new_task=True))
    assert snapshot(w) == before  # the rejected creation left nothing behind
    opened = s.call('open_work', dict(intent=intent, project='新工作项目',
                                      new_task=True, request_id='chat-2026-01'))
    jid = opened['work']['job_id']
    assert opened['status'] == 'opened' and opened['created'] == {'project': False, 'task': True}
    assert opened['replayed'] is False and opened['request_id'] == 'chat-2026-01'
    assert opened['work']['goal'] == intent  # the original intent is preserved verbatim
    assert opened['work']['title'] == intent
    assert opened['work']['scope']['project_ids'] == [p['project_id']]
    assert opened['project_sources'] == dict(project_id=p['project_id'], folder='资料/',
                                             count=0, items=[], has_more=False)
    # Retry with the same request_id replays the same task, in this or another process.
    again = s.call('open_work', dict(intent=intent, project='新工作项目',
                                     new_task=True, request_id='chat-2026-01'))
    assert again['work']['job_id'] == jid and again['replayed'] is True
    assert len(Jobs(w).list()) == 1
    # A different request_id is distinct work, never a hijack of the active job.
    other = s.call('open_work', dict(intent='另一件事', project='新工作项目',
                                     new_task=True, request_id='chat-2026-02'))
    assert other['work']['job_id'] != jid and other['replayed'] is False
    assert len(Jobs(w).list()) == 2
    with pytest.raises(Error):
        s.call('open_work', dict(intent='复用被拒绝', project='新工作项目',
                                 new_task=True, request_id='chat-2026-01'))


def test_create_project_only_when_explicit_and_unmatched(tmp_path):
    w, _ = workspace(tmp_path)
    s = Service(tmp_path)
    with pytest.raises(Error):
        s.call('open_work', dict(intent='x', create_project=True))
    result = s.call('open_work', dict(intent='开一个审计项目',
                                      project='审计项目', create_project=True))
    assert result['status'] == 'candidates' and result['reason'] == 'no_task'
    assert result['created'] == {'project': True, 'task': False}
    created_id = result['projects'][0]['project_id']
    assert result['projects'][0]['name'] == '审计项目'
    assert len(w.projects()) == 1
    # Retrying reuses the existing project instead of duplicating it.
    retry = s.call('open_work', dict(intent='继续审计', project='审计项目', create_project=True))
    assert retry['created'] == {'project': False, 'task': False}
    assert len(w.projects()) == 1
    # Without the explicit flag a near miss stays a suggestion and writes nothing.
    near = s.call('open_work', dict(intent='接着做', project='审计'))
    assert near['reason'] == 'unknown_project'
    assert [x['project_id'] for x in near['projects']] == [created_id]
    assert len(w.projects()) == 1
    # An ID is never a creation name.
    miss = s.call('open_work', dict(intent='x', project='PRJ-9999', create_project=True))
    assert miss['reason'] == 'unknown_project' and miss['projects'] == []
    assert len(w.projects()) == 1


def test_project_creation_can_retry_the_same_request_after_task_creation_fails(tmp_path, monkeypatch):
    w, _ = workspace(tmp_path)
    original_create = Jobs.create
    attempts = 0

    def fail_once(self, *args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise Error('REMOTE_UNCERTAIN', 'Task creation interrupted; retry the request')
        return original_create(self, *args, **kwargs)

    monkeypatch.setattr(Jobs, 'create', fail_once)
    request = dict(intent='开始审计初稿', project='审计项目', create_project=True,
                   new_task=True, request_id='retry-project-1')
    with pytest.raises(Error) as raised:
        Service(tmp_path).call('open_work', request)
    assert raised.value.code == 'REMOTE_UNCERTAIN'
    assert [p['name'] for p in w.projects()] == ['审计项目']
    retry = Service(tmp_path).call('open_work', request)
    assert retry['status'] == 'opened'
    assert retry['created'] == {'project': False, 'task': True}
    assert retry['request_id'] == 'retry-project-1'
    assert len(w.projects()) == 1 and len(Jobs(w).list()) == 1


def test_alias_and_unknown_project_choices_never_write(tmp_path):
    w, (p1, p2) = workspace(tmp_path, ['项目甲', '项目乙'])
    s = Service(tmp_path)
    aliases(s, p1, '台账')
    before = snapshot(w)
    hit = s.call('open_work', dict(intent='整理台账', project='台账'))
    assert hit['status'] == 'candidates' and hit['reason'] == 'no_task'
    assert [x['project_id'] for x in hit['projects']] == [p1['project_id']]
    aliases(s, p2, '台账')
    ambiguous = s.call('open_work', dict(intent='整理台账', project='台账'))
    assert ambiguous['status'] == 'candidates' and ambiguous['reason'] == 'ambiguous_projects'
    assert {x['project_id'] for x in ambiguous['projects']} == {p1['project_id'], p2['project_id']}
    assert snapshot(w) == before


def test_session_binding_keeps_current_work_and_switches_after_explicit_choice(tmp_path):
    w, (p,) = workspace(tmp_path, ['会话项目'])
    s = Service(tmp_path)
    first = Jobs(w).create('任务一', '目标一', scope(p['project_id']))
    opened = s.call('open_work', dict(intent='做任务一', project='会话项目', query='任务一', session_id='chat-A'))
    assert opened['session_binding'] == dict(session_id='chat-A', state='bound', jid=first['job_id'])
    # A previously bound task proceeds on its own.
    again = s.call('open_work', dict(intent='接着做', session_id='chat-A'))
    assert again['status'] == 'opened' and again['work']['job_id'] == first['job_id']
    assert again['session_binding']['state'] == 'kept'
    second = Jobs(w).create('任务二', '目标二', scope(p['project_id']))
    switched = s.call('open_work', dict(intent='做任务二', session_id='chat-A', jid=second['job_id']))
    assert switched['work']['job_id'] == second['job_id']
    binding = switched['session_binding']
    assert binding == dict(session_id='chat-A', state='switched', jid=second['job_id'])
    # The explicit choice becomes the following turn's continuation.
    assert s.call('resume_session', dict(session_id='chat-A'))['job_id'] == second['job_id']
    # Repeating the selected project retains its exact session binding even
    # after another active task has appeared there.
    before = snapshot(w)
    retained = s.call('open_work', dict(intent='接着做', project='会话项目', session_id='chat-A'))
    assert retained['status'] == 'opened' and retained['work']['job_id'] == second['job_id']
    assert retained['session_binding'] == dict(session_id='chat-A', state='kept', jid=second['job_id'])
    assert snapshot(w) == before


def test_unbound_session_reports_state_without_binding(tmp_path):
    w, (p,) = workspace(tmp_path, ['候选项目'])
    s = Service(tmp_path)
    result = s.call('open_work', dict(intent='随便看看', session_id='chat-B'))
    assert result['session_binding'] == dict(session_id='chat-B', state='unbound')
    assert list_files(w, '.workspace/sessions') == []


def test_multi_project_task_requires_explicit_destination(tmp_path):
    w, (p1, p2) = workspace(tmp_path, ['甲项目', '乙项目'])
    job = Jobs(w).create('双项目任务', '目标', scope(p1['project_id'], p2['project_id']))
    s = Service(tmp_path)
    result = s.call('open_work', dict(intent='推进双项目', jid=job['job_id']))
    assert result['status'] == 'candidates'
    assert result['reason'] == 'choose_project_for_task'
    assert {p['project_id'] for p in result['projects']} == {p1['project_id'], p2['project_id']}
    opened = s.call('open_work', dict(intent='推进双项目', jid=job['job_id'], project=p2['project_id']))
    assert opened['status'] == 'opened' and opened['project_id'] == p2['project_id']
    assert opened['work']['work_context']['identity']['project_id'] == p2['project_id']


def test_explicit_task_must_belong_to_selected_project(tmp_path):
    w, (p1, p2) = workspace(tmp_path, ['甲项目', '乙项目'])
    job = Jobs(w).create('乙项目工作', '只属于乙', scope(p2['project_id']))
    with pytest.raises(Error) as raised:
        Service(tmp_path).call('open_work', dict(intent='继续', jid=job['job_id'], project=p1['project_id']))
    assert raised.value.code == 'INVALID_SCOPE'


def test_project_only_never_opens_an_unrelated_sole_task(tmp_path):
    w, (p,) = workspace(tmp_path, ['采购项目'])
    job = Jobs(w).create('历史盘点', '旧工作', scope(p['project_id']))
    result = Service(tmp_path).call('open_work', dict(intent='写采购周报', project=p['project_id']))
    assert result['status'] == 'candidates' and result['reason'] == 'ambiguous_tasks'
    assert result['tasks'][0]['job_id'] == job['job_id']


def test_legacy_job_and_session_still_open(tmp_path):
    w, (p,) = workspace(tmp_path, ['历史项目'])
    job = Jobs(w).create('历史任务', '目标', scope(p['project_id']))
    path = Jobs(w).file(job['job_id'])
    raw = json.loads(path.read_text())
    raw.pop('context', None)
    raw.pop('directory_name', None)
    raw.pop('verification', None)
    put_json(path, raw)
    old_session = w.path('.workspace/sessions/' +
                         hashlib.sha256('legacy-chat'.encode()).hexdigest() + '.json')
    put_json(old_session, dict(schema_version=1, session_id='legacy-chat', jid=job['job_id']))
    s = Service(tmp_path)
    opened = s.call('open_work', dict(intent='接续历史', jid=job['job_id']))
    assert opened['status'] == 'opened' and opened['work']['job_id'] == job['job_id']
    assert opened['work']['directories'][0]['work'].endswith('工作/' + job['job_id'])
    from_session = s.call('open_work', dict(intent='换个会话名接续', session_id='legacy-chat'))
    assert from_session['work']['job_id'] == job['job_id']


def test_remote_mode_open_work_uses_existing_contracts(tmp_path, monkeypatch):
    """Remote authority uses exact project filters and the normal request replay."""
    import copy

    from aiws.feishu import Feishu
    from aiws.maintenance import Maintenance

    w = Workspace(tmp_path)
    w.init()
    project = w.project_create('远端项目')
    put_json(w.path('.workspace/feishu.json'), dict(schema_version=1, mode='staging',
                                                    base_token='test', projects_table='projects',
                                                    jobs_table='jobs'))
    rows = {'projects': {}, 'jobs': {}}
    filters_seen = []

    def satisfies(row, condition):
        field, operator, expected = condition
        actual = row.get(field)
        if operator == '==':
            return actual == expected
        if operator == 'intersects':
            values = actual if isinstance(actual, list) else str(actual or '').split('\n')
            expected_values = expected if isinstance(expected, list) else [expected]
            return any(item in values for item in expected_values)
        if operator == 'disjoint':
            values = actual if isinstance(actual, list) else [actual]
            return not any(item in values for item in expected)
        raise AssertionError('unexpected filter: ' + repr(condition))

    def read(self, entity, key=None, value=None, limit=20, offset=0, conditions=None, **kwargs):
        filters = list(conditions or [])
        if key is not None:
            filters.append([key, '==', value])
        filters_seen.extend((entity, condition) for condition in filters)
        result = [row for row in rows[entity].values()
                  if all(satisfies(row, condition) for condition in filters)]
        return dict(items=copy.deepcopy(result[offset:offset + limit]),
                    has_more=len(result) > offset + limit)

    def command(self, action, table, args):
        fields = json.loads(args[args.index('--json') + 1])
        rid = args[args.index('--record-id') + 1] if '--record-id' in args else 'rec' + str(len(rows[table]) + 1)
        rows[table][rid] = {'record_id': rid, **fields}
        return dict(ok=True, data={'record': {'record_id_list': [rid]}})

    monkeypatch.setattr(Feishu, 'rows', read)
    monkeypatch.setattr(Feishu, 'command', command)
    Maintenance(w).backup()
    Service(tmp_path).call('migrate_feishu', dict(activate=True))
    s = Service(tmp_path)
    before = snapshot(w)
    candidates = s.call('open_work', dict(intent='远端工作', project='远端项目'))
    assert candidates['status'] == 'candidates' and candidates['reason'] == 'no_task'
    assert candidates['projects'][0]['project_id'] == project['project_id']
    assert ('projects', ['项目名称', '==', '远端项目']) in filters_seen
    assert snapshot(w) == before
    opened = s.call('open_work', dict(intent='远端工作', project='远端项目',
                                      new_task=True, request_id='remote-entry-1'))
    jid = opened['work']['job_id']
    assert opened['status'] == 'opened' and opened['created'] == {'project': False, 'task': True}
    assert opened['work']['goal'] == '远端工作'
    assert opened['work']['revision'] == 1
    remote_row = next(r for r in rows['jobs'].values() if r['任务ID'] == jid)
    assert remote_row['操作ID']
    replay = s.call('open_work', dict(intent='远端工作', project='远端项目',
                                      new_task=True, request_id='remote-entry-1'))
    assert replay['work']['job_id'] == jid and replay['replayed'] is True
    assert len(rows['jobs']) == 1


def test_cli_call_open_work(tmp_path):
    w, (p,) = workspace(tmp_path, ['命令行项目'])
    # Pin this worktree's engine so the subprocess cannot fall back to an
    # installed copy; relative PYTHONPATH entries break under a different cwd.
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
    params = json.dumps(dict(intent='盘点台账', project='命令行项目'), ensure_ascii=False)
    run = subprocess.run([sys.executable, '-m', 'aiws', '--workspace', str(w.root),
                          'call', 'open_work', '--params', params],
                         cwd=str(tmp_path), capture_output=True, text=True, env=env)
    out = json.loads(run.stdout)
    assert out['ok'], out
    assert out['result']['status'] == 'candidates'
    assert out['result']['reason'] == 'no_task'
    assert out['result']['projects'][0]['project_id'] == p['project_id']
    assert Jobs(w).list() == []
    params = json.dumps(dict(intent='盘点台账', project='命令行项目', new_task=True,
                             request_id='cli-entry-1'), ensure_ascii=False)
    run = subprocess.run([sys.executable, '-m', 'aiws', '--workspace', str(w.root),
                          'call', 'open_work', '--params', params],
                         cwd=str(tmp_path), capture_output=True, text=True, env=env)
    out = json.loads(run.stdout)
    assert out['ok'] and out['result']['status'] == 'opened'
    assert out['result']['work']['goal'] == '盘点台账'
    assert len(Jobs(w).list()) == 1
