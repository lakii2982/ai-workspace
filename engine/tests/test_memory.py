import pytest
from aiws.core import Workspace, Error, digest
from aiws.timeline import Timeline


def setup(tmp_path):
    w = Workspace(tmp_path); w.init(); p = w.project_create('记忆')
    t = Timeline(w); path = w.root / p['path'] / '记忆/TIMELINE.md'
    def append(eid, state='established', refs=None, category='decision', **extra):
        return t.append(project_id=p['project_id'], event_id=eid, event_date='2026-11-17',
            record_date='2026-11-17', kind='decision', summary=eid, status='记录', impact='范围',
            owner='个人', next_action='继续', sources=['用户决定'], expected_hash=digest(path),
            memory=dict(category=category, state=state, reason='理由 ' + eid, supersedes=refs or []), **extra)
    return t, p['project_id'], path, append


def test_current_history_and_tentative(tmp_path):
    t, pid, path, add = setup(tmp_path)
    add('old'); add('proposal', 'tentative', ['old'])
    assert [x['event_id'] for x in t.list_memory(pid)['items']] == ['old']
    assert t.list_memory(pid)['tentative'][0]['event_id'] == 'proposal'
    add('new', refs=['old'])
    assert [x['event_id'] for x in t.list_memory(pid)['items']] == ['new']
    old = t.read_memory(pid, 'old')
    assert old['derived_state'] == 'superseded'
    assert old['history'][0]['event_id'] == 'new'
    assert t.list_memory(pid, view='history')['total'] == 3
    assert t.list_memory(pid, query='不存在')['items'] == []
    assert len(t.list_memory(pid, view='history', limit=1)['items']) == 1


def test_resolution_withdrawal_and_categories(tmp_path):
    t, pid, path, add = setup(tmp_path)
    add('question', 'tentative', category='question')
    assert t.list_memory(pid)['total'] == 1
    add('answer', 'resolved', ['question'], category='question')
    assert t.list_memory(pid)['total'] == 0
    add('constraint', category='constraint')
    add('withdraw', 'withdrawn', ['constraint'])
    assert t.read_memory(pid, 'constraint')['derived_state'] == 'withdrawn'
    add('experience', category='experience')
    assert t.list_memory(pid, categories=['experience'])['total'] == 1
    with pytest.raises(Error): add('bad', refs=['other-project'])


def test_manual_edit_and_legacy_are_not_established_memory(tmp_path):
    t, pid, path, add = setup(tmp_path)
    add('original')
    body = path.read_text()
    # Change only the readable event, leaving the structured comment untouched.
    path.write_text(body.replace('## 2026-11-17 | original', '## 2026-11-17 | 手工改变了结论'))
    result = t.list_memory(pid)
    assert result['items'] == []
    assert result['pending_review'][0]['derived_state'] == 'needs_review'
    assert '手工改变了结论' in result['pending_review'][0]['current_body']
    assert t.read_memory(pid, 'original')['derived_state'] == 'needs_review'
    path.write_text(path.read_text() + '\n## 人工笔记\n只是待验证的想法\n')
    assert '只是待验证' in t.list_memory(pid)['legacy'][0]['text']
    # An invalid JSON block must remain visible for review.
    path.write_text(path.read_text().replace('"format_version": 1', '"format_version": 99'))
    assert len(t.list_memory(pid)['pending_review']) == 1


def test_memory_replay_identity_includes_reason(tmp_path):
    t, pid, path, add = setup(tmp_path)
    add('choice')
    assert add('choice')['replayed']
    # Removing the record removes it from current state; Git supplies history separately.
    path.write_text('# empty\n')
    assert t.list_memory(pid)['items'] == []


def test_edited_replacement_does_not_revive_old_decision(tmp_path):
    t, pid, path, add = setup(tmp_path)
    add('old-choice')
    add('replacement', refs=['old-choice'])
    path.write_text(path.read_text().replace('## 2026-11-17 | replacement', '## 2026-11-17 | manually changed replacement'))
    result = t.list_memory(pid)
    assert result['items'] == []
    assert {x['event_id'] for x in result['pending_review']} == {'replacement', 'old-choice'}
    assert t.read_memory(pid, 'old-choice')['derived_state'] == 'needs_review'
    assert t.read_memory(pid, 'replacement')['history'][0]['derived_state'] == 'needs_review'


def test_search_alternative_terms_and_explicit_exact_phrase(tmp_path):
    t, pid, path, add = setup(tmp_path)
    add('equipment-choice')
    add('schedule-choice')
    assert t.list_memory(pid, query='equipment missing')['total'] == 1
    assert t.list_memory(pid, query='equipment schedule')['total'] == 2
    assert t.list_memory(pid, query='"equipment missing"')['total'] == 0
    assert t.list_memory(pid, query='"理由 equipment-choice"')['total'] == 1


def test_plain_free_text_is_visible_but_initial_metadata_is_not(tmp_path):
    t, pid, path, add = setup(tmp_path)
    assert t.list_memory(pid)['legacy'] == []
    path.write_text(path.read_text() + '\n选型暂时考虑B方案，尚未确定。\n')
    result = t.list_memory(pid)
    assert result['items'] == []
    assert '选型暂时考虑B方案' in result['legacy'][0]['text']
    assert result['legacy'][0]['state'] == 'unstructured'
    # Plain prose following a structured entry also stays visible.
    add('decided')
    assert '选型暂时考虑B方案' in t.list_memory(pid)['legacy'][0]['text']
