import pytest
from aiws.core import Workspace, Error
from aiws.memories import Memories
from aiws.service import Service


def setup(tmp_path):
    w = Workspace(tmp_path); w.init(); p = w.project_create('记忆条目')
    return w, p['project_id'], Memories(w)


def user_note(m, pid, name='report-style', **extra):
    return m.save(pid, name, '用户偏好要点式汇报', 'user',
                  '# 汇报偏好\n\n先给结论，再列 3–5 条要点。', **extra)


def test_project_gets_an_index_and_entries_folder(tmp_path):
    w, pid, m = setup(tmp_path)
    project = w.project_for(pid)
    assert (w.root / project['path'] / '记忆/MEMORY.md').is_file()
    assert (w.root / project['path'] / '记忆/条目').is_dir()
    view = m.list(pid)
    assert view['index_initialized'] and view['items'] == []
    assert '记忆/条目/' in view['index_text']


def test_list_read_links_and_types(tmp_path):
    w, pid, m = setup(tmp_path)
    user_note(m, pid)
    m.save(pid, 'delivery-check', '交付前先核对验收条件', 'feedback',
           '# 交付核对\n\n见面之前逐条核对验收条件。\n\n**Why:** 曾因漏项返工。\n\n'
           '**How to apply:** 完成任务前重读 acceptance。\n\n前置要求见 [[report-style]]',
           source='JOB-0001')
    view = m.list(pid)
    assert [x['name'] for x in view['items']] == ['report-style', 'delivery-check']
    assert view['counts'] == {'user': 1, 'feedback': 1, 'project': 0, 'reference': 0}
    lines = [line for line in view['index_text'].splitlines() if line.startswith('- [')]
    assert lines == ['- [汇报偏好](条目/report-style.md) — 用户偏好要点式汇报',
                     '- [交付核对](条目/delivery-check.md) — 交付前先核对验收条件']
    assert view['index_drift']['in_sync']

    note = m.read(pid, 'report-style')
    assert note['content'].startswith('# 汇报偏好') and note['type'] == 'user'
    assert note['links'] == [] and [x['name'] for x in note['backlinks']] == ['delivery-check']
    linked = m.read(pid, 'delivery-check')
    assert linked['links'] == [{'name': 'report-style', 'exists': True,
                                'description': '用户偏好要点式汇报'}]

    assert [x['name'] for x in m.list(pid, query='返工')['items']] == ['delivery-check']
    assert [x['name'] for x in m.list(pid, query='"交付前先核对"')['items']] == ['delivery-check']
    assert [x['name'] for x in m.list(pid, query='返工 汇报')['items']] == ['report-style', 'delivery-check']
    assert m.list(pid, types=['user'])['total'] == 1
    assert m.list(pid, detail='full')['items'][0]['content'].startswith('# 汇报偏好')
    assert 'content' not in m.list(pid)['items'][0]

    broken = m.save(pid, 'placeholder', '仍待确认的外部资源', 'reference',
                    '# 资源\n\n见 [[not-written-yet]]。')
    assert broken['links'] == [{'name': 'not-written-yet', 'exists': False, 'description': None}]
    assert m.list(pid)['broken_links'] == [{'name': 'placeholder', 'link': 'not-written-yet'}]


def test_hash_gates_validation_and_delete(tmp_path):
    w, pid, m = setup(tmp_path)
    first = user_note(m, pid)
    with pytest.raises(Error) as creating:
        user_note(m, pid)
    assert creating.value.code == 'CONFLICT'
    with pytest.raises(Error):
        m.save(pid, 'report-style', '用户偏好要点式汇报', 'user',
               '# 汇报偏好\n\n先给结论。', expected_hash='0' * 64)
    updated = m.save(pid, 'report-style', '用户偏好要点式汇报', 'user',
                     '# 汇报偏好\n\n先给结论，再列 3–5 条要点。\n\n长段落拆开。',
                     expected_hash=first['hash'])
    assert updated['content'].startswith('# 汇报偏好') and updated['hash'] != first['hash']
    assert updated['created'] == first['created'] and not updated['created_memory']

    replay = m.save(pid, 'report-style', '用户偏好要点式汇报', 'user',
                    '# 汇报偏好\n\n先给结论，再列 3–5 条要点。\n\n长段落拆开。',
                    expected_hash=updated['hash'])
    assert replay['replayed'] and replay['hash'] == updated['hash']

    for kwargs in (dict(description='多行\n描述'), dict(type='memo'),
                   dict(name='Bad Name'), dict(name='a' * 65)):
        with pytest.raises(Error) as invalid:
            m.save(pid, kwargs.pop('name', 'report-style'), kwargs.pop('description', '描述'),
                   kwargs.pop('type', 'user'), '# 正文')
        assert invalid.value.code == 'INVALID_ARGUMENT'
    with pytest.raises(Error) as missing_why:
        m.save(pid, 'policy', '交付流程', 'feedback', '# 流程\n\n先自查。')
    assert missing_why.value.code == 'INVALID_ARGUMENT'

    with pytest.raises(Error) as stale:
        m.delete(pid, 'report-style', expected_hash=first['hash'])
    assert stale.value.code == 'CONFLICT'
    deleted = m.delete(pid, 'report-style', expected_hash=updated['hash'])
    assert deleted['deleted_hash'] == updated['hash'] and deleted['backlinks'] == []
    assert m.list(pid)['items'] == []
    with pytest.raises(Error) as gone:
        m.read(pid, 'report-style')
    assert gone.value.code == 'NOT_FOUND'


def test_manual_edits_are_review_and_save_rebuilds_index(tmp_path):
    w, pid, m = setup(tmp_path)
    project = w.project_for(pid)
    entries = w.root / project['path'] / '记忆/条目'
    (entries / 'bad.md').write_text('没有 frontmatter 的笔记\n')
    (entries / 'mismatch.md').write_text('---\nname: other\ndescription: 名称不符\n'
                                         'metadata:\n  type: user\n---\n\n正文\n')
    view = m.list(pid)
    assert view['items'] == []
    assert {x['path'].rsplit('/', 1)[-1] for x in view['warnings']} == {'bad.md', 'mismatch.md'}

    index = w.root / project['path'] / '记忆/MEMORY.md'
    index.write_text(index.read_text() + '- [幽灵](条目/ghost.md) — 不存在\n')
    drift = m.list(pid)['index_drift']
    assert drift['missing'] == ['ghost'] and not drift['in_sync']
    from aiws.maintenance import Maintenance
    assert pid in Maintenance(w).doctor()['memory_review']

    user_note(m, pid, name='real-note')
    rebuilt = index.read_text()
    assert 'ghost' not in rebuilt and '(条目/real-note.md) — 用户偏好要点式汇报' in rebuilt
    assert m.list(pid)['index_drift']['in_sync']


def test_prepare_context_surfaces_memory_index(tmp_path):
    w, pid, m = setup(tmp_path)
    user_note(m, pid)
    from aiws.context import Context
    prepared = Context(w).prepare(project_id=pid)
    assert prepared['memory_notes']['items'][0]['name'] == 'report-style'
    assert 'report-style' in prepared['memory_notes']['index_text']
    assert prepared['memory_notes']['counts']['user'] == 1
    assert 'content' not in prepared['memory_notes']['items'][0]
    full = Context(w).prepare(project_id=pid, detail='full')
    assert full['memory_notes']['items'][0]['content'].startswith('# 汇报偏好')
    overview = Context(w).overview(pid)
    assert overview['memory_notes']['total'] == 1


def test_memory_is_context_not_source_evidence(tmp_path):
    w, pid, m = setup(tmp_path)
    project = w.project_for(pid)
    (w.root / project['path'] / '资料/采购.txt').write_text('采购周期30天')
    user_note(m, pid, name='memory-only')
    (w.root / project['path'] / '记忆/条目/memory-only.md').write_text(
        (w.root / project['path'] / '记忆/条目/memory-only.md').read_text() + '\n唯一记忆词条。\n')
    scope = dict(include_company=False, include_professional=False,
                 project_ids=[pid], topic_ids=[])
    from aiws.retrieval import Retrieval
    result = Retrieval(w).search('唯一记忆词条', scope)
    assert result['matches'] == []
    assert Retrieval(w).search('采购周期', scope)['matches']
    assert not any('/记忆/' in match['source_path']
                   for match in Retrieval(w).search('采购周期', scope)['matches'])


def test_service_dispatch_keeps_project_lock_and_operations(tmp_path):
    w, pid, _ = setup(tmp_path)
    service = Service(w.root)
    saved = service.call('save_memory_note', dict(
        project_id=pid, name='goal', description='项目在 2027-03 前完成迁移',
        type='project', content='# 目标\n\n2027-03-31 前完成迁移。\n\n'
                               '**Why:** 合同截止。\n\n**How to apply:** 每周核对里程碑。'))
    assert saved['type'] == 'project'
    listed = service.call('list_memory_notes', dict(project_id=pid))
    assert listed['items'][0]['name'] == 'goal'
    read = service.call('read_memory_note', dict(project_id=pid, name='goal'))
    service.call('delete_memory_note', dict(project_id=pid, name='goal',
                                            expected_hash=read['hash']))
    assert service.call('list_memory_notes', dict(project_id=pid))['total'] == 0
    from aiws import service as service_module
    assert {'save_memory_note', 'delete_memory_note'} <= service_module._PROJECT_OPERATIONS
