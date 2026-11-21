import copy
import hashlib
import json
import multiprocessing as mp
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

from aiws.core import Error, Workspace, atomic, put_json
from aiws.feishu import Feishu
from aiws.jobs import Jobs
from aiws.service import Service


def scope(project):
    return dict(include_company=False, include_professional=False,
                project_ids=[project['project_id']], topic_ids=[])


def project_with_job(root, name):
    w = Workspace(root)
    w.init()
    project = w.project_create(name)
    job = Jobs(w).create('Draft', 'Review project material', scope(project))
    return project, job


def slow_index(root, query_scope, entered, proceed, reports):
    from aiws import retrieval
    real_extract = retrieval.extract

    def delayed_extract(path):
        entered.set()
        if not proceed.wait(20):
            raise RuntimeError('Index parser did not receive release')
        return real_extract(path)

    retrieval.extract = delayed_extract
    reports.put(Service(root).call('update_index', {'scope': query_scope}))


def independent_work(root, project, job, reports):
    service = Service(root)
    result = service.call('resume_job', {'jid': job['job_id']})
    saved = service.call('write_work_file', dict(jid=job['job_id'],
                         project_id=project['project_id'], name='draft.md', content='new draft'))
    matched = service.call('search_sources', dict(query='second project keyword',
                           scope=scope(project)))
    reports.put((result['job_id'], saved['path'], matched))


def test_independent_project_continues_while_other_project_is_parsing(tmp_path):
    project_a, _ = project_with_job(tmp_path, 'First project')
    project_b, job_b = project_with_job(tmp_path, 'Second project')
    for project, text in [(project_a, 'first project keyword'), (project_b, 'second project keyword')]:
        (tmp_path / project['path'] / '资料' / 'input.txt').write_text(text)
    ctx = mp.get_context('spawn')
    entered, proceed = ctx.Event(), ctx.Event()
    parsed, worked = ctx.Queue(), ctx.Queue()
    first = ctx.Process(target=slow_index,
                        args=(str(tmp_path), scope(project_a), entered, proceed, parsed))
    second = ctx.Process(target=independent_work,
                         args=(str(tmp_path), project_b, job_b, worked))
    first.start()
    try:
        assert entered.wait(10), 'project A must reach the index parser'
        second.start()
        resumed, saved, matches = worked.get(timeout=10)
        assert resumed == job_b['job_id']
        assert (tmp_path / saved).read_text() == 'new draft'
        assert [item['snippet'] for item in matches['matches']] == ['second project keyword']
        assert first.is_alive(), 'A must still be blocked when B finishes'
    finally:
        proceed.set()
        for process in (first, second):
            if process.pid is not None:
                process.join(15)
                if process.is_alive():
                    process.terminate()
                    process.join(5)
    assert (first.exitcode, second.exitcode) == (0, 0)
    assert not parsed.get(timeout=3)['failures']


def overwrite(root, job, path, expected, content, start, reports):
    assert start.wait(10)
    try:
        reports.put(Service(root).call('save_artifact', dict(jid=job['job_id'],
                    relative=path, content=content, expected_hash=expected, confirm=True)))
    except Error as error:
        reports.put(error.code)


def test_same_hash_writers_to_shared_path_have_one_winner(tmp_path):
    project, job_a = project_with_job(tmp_path, 'Shared project')
    job_b = Jobs(Workspace(tmp_path)).create('Second writer', 'Revise source', scope(project))
    service = Service(tmp_path)
    path = project['path'] + '/资料/shared.txt'
    saved = service.call('save_artifact', dict(jid=job_a['job_id'], relative=path,
                         content='original', confirm=True))
    ctx = mp.get_context('spawn')
    start, reports = ctx.Event(), ctx.Queue()
    alias = project['path'] + '/资料/./shared.txt'
    workers = [ctx.Process(target=overwrite,
                           args=(str(tmp_path), job, rel, saved['hash'], val, start, reports))
               for job, rel, val in [(job_a, path, 'writer A'), (job_b, alias, 'writer B')]]
    for p in workers:
        p.start()
    try:
        start.set()
        results = [reports.get(timeout=10) for _ in workers]
    finally:
        for p in workers:
            p.join(10)
            if p.is_alive():
                p.terminate()
                p.join(5)
    assert all(p.exitcode == 0 for p in workers)
    assert results.count('CONFLICT') == 1
    winner = next(r for r in results if isinstance(r, dict))
    assert winner['path'] == path
    assert (tmp_path / path).read_text() in ('writer A', 'writer B')


def test_independent_project_continues_during_active_feishu_write(tmp_path, monkeypatch):
    project_a, job_a = project_with_job(tmp_path, 'First remote')
    project_b, job_b = project_with_job(tmp_path, 'Second remote')
    w = Workspace(tmp_path)
    put_json(w.path('.workspace/feishu.json'),
             dict(schema_version=1, mode='staging', base_token='test',
                  projects_table='projects', jobs_table='jobs'))
    rows = {'projects': {}, 'jobs': {}}
    entered, proceed = Event(), Event()
    pause = False

    def read(self, entity, key=None, value=None, limit=20, offset=0, **kwargs):
        items = list(rows[entity].values())
        if key:
            items = [row for row in items if row.get(key) == value]
        return dict(items=copy.deepcopy(items[offset:offset+limit]), has_more=len(items) > offset+limit)

    def write(self, action, table, args):
        fields = json.loads(args[args.index('--json')+1])
        if pause and fields.get('任务ID') == job_a['job_id']:
            entered.set()
            assert proceed.wait(20), 'Feishu write did not receive release'
        rid = args[args.index('--record-id')+1] if '--record-id' in args else 'rec'+str(len(rows[table])+1)
        rows[table][rid] = {'record_id': rid, **fields}
        return dict(ok=True, data={'record': {'record_id_list': [rid]}})

    monkeypatch.setattr(Feishu, 'rows', read)
    monkeypatch.setattr(Feishu, 'command', write)
    service = Service(tmp_path)
    service.call('backup', {})
    service.call('migrate_feishu', {'activate': True})

    def claim(job):
        return service.call('claim_job', dict(jid=job['job_id'], owner='tester',
                            revision=job['revision']))['lease']['token']

    tokens = [claim(job_a), claim(job_b)]
    pause = True

    def save(project, job, token):
        return service.call('write_work_file', dict(jid=job['job_id'],
                            project_id=project['project_id'], name='draft.md',
                            content='draft body', lease_token=token))

    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(save, project_a, job_a, tokens[0])
        try:
            assert entered.wait(10), 'project A must enter the remote write'
            checking = Event()

            def resume_inflight():
                checking.set()
                return service.call('resume_job', {'jid': job_a['job_id']})

            same_job = pool.submit(resume_inflight)
            assert checking.wait(10)

            def continue_second():
                result = service.call('resume_job', {'jid': job_b['job_id']})
                assert result['job_id'] == job_b['job_id']
                return save(project_b, job_b, tokens[1])

            second = pool.submit(continue_second)
            assert (tmp_path / second.result(timeout=10)['path']).read_text() == 'draft body'
            assert not first.done()
            assert not same_job.done(), 'same-job resume must take the write guard'
        finally:
            proceed.set()
        assert (tmp_path / first.result(timeout=10)['path']).read_text() == 'draft body'
        assert same_job.result(timeout=10)['artifact_count'] == 1


def test_timeline_records_reference_same_snapshot_as_their_hash(tmp_path, monkeypatch):
    from aiws.timeline import Timeline

    project, _ = project_with_job(tmp_path, 'Concurrent timeline')
    project_id = project['project_id']
    path = tmp_path / project['path'] / '记忆' / 'TIMELINE.md'
    original = path.read_bytes() + b'original timeline note\n'
    changed = original + b'another writer committed\n'
    atomic(path, original)
    real_read = Path.read_bytes
    real_read_text = Path.read_text

    def read_then_replace(candidate):
        contents = real_read(candidate)
        if candidate == path:
            atomic(path, changed)
        return contents

    def text_then_replace(candidate, *args, **kwargs):
        contents = real_read_text(candidate, *args, **kwargs)
        if candidate == path:
            atomic(path, changed)
        return contents

    monkeypatch.setattr(Path, 'read_bytes', read_then_replace)
    monkeypatch.setattr(Path, 'read_text', text_then_replace)
    legacy, = Timeline(Workspace(tmp_path)).list_memory(project_id)['legacy']
    assert 'another writer' not in legacy['text']
    assert legacy['hash'] == hashlib.sha256(original).hexdigest()
    assert real_read(path) == changed
