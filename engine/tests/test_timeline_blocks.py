import pytest

from aiws.core import Error, Workspace, digest
from aiws.timeline import Timeline


def setup(tmp_path):
    w = Workspace(tmp_path); w.init(); p = w.project_create('时间线')
    t = Timeline(w); path = w.root / p['path'] / '记忆/TIMELINE.md'

    def add(eid, summary='事件', memory=None, **extra):
        return t.append(project_id=p['project_id'], event_id=eid, event_date='2026-11-25',
                        record_date='2026-11-25', kind='fact', summary=summary, status='有效',
                        impact='范围', owner='我', next_action='继续', sources=['来源'],
                        expected_hash=digest(path) if path.exists() else None,
                        memory=memory, **extra)
    return t, p['project_id'], path, add


def memory(category='decision', state='established', **extra):
    return dict(category=category, state=state, reason='理由', **extra)


def test_event_without_memory_is_plain_not_legacy(tmp_path):
    t, pid, path, add = setup(tmp_path)
    add('evt-1')
    result = t.list_memory(pid)
    assert result['legacy'] == []
    assert result['items'] == []
    assert [event['event_id'] for event in result['events']] == ['evt-1']
    assert result['events_total'] == 1


def test_summary_containing_event_terminator_stays_established(tmp_path):
    t, pid, path, add = setup(tmp_path)
    add('evt-1', summary='含 <!-- /aiws-event --> 终止符', memory=memory())
    result = t.list_memory(pid)
    assert [row['event_id'] for row in result['items']] == ['evt-1']
    assert result['pending_review'] == []
    assert result['legacy'] == []


def test_escaped_body_keeps_the_original_summary_in_json(tmp_path):
    t, pid, path, add = setup(tmp_path)
    add('evt-1', summary='含 <!-- 注释', memory=memory())
    item = t.read_memory(pid, 'evt-1')
    assert item['summary'] == '含 <!-- 注释'
    assert '<!--' not in item['current_body']
    assert '<\\!--' in item['current_body']


def test_v0_block_is_reported_as_unstructured(tmp_path):
    t, pid, path, add = setup(tmp_path)
    path.write_text('# 项目时间线\n\n<!-- event:evt-old hash:' + 'a' * 64
                    + ' -->\n## 2026-01-01 | 旧事件\n\n- 类型：fact\n')
    result = t.list_memory(pid)
    assert result['items'] == []
    assert result['legacy'] and '旧事件' in result['legacy'][0]['text']
    assert t.unstructured_count(pid) == 1


def test_replay_after_format_change_raises_conflict(tmp_path):
    t, pid, path, add = setup(tmp_path)
    path.write_text('# 项目时间线\n\n<!-- event:evt-old hash:' + 'a' * 64
                    + ' -->\n旧正文\n')
    with pytest.raises(Error) as error:
        add('evt-old', summary='新内容')
    assert error.value.code == 'CONFLICT'


def test_replay_of_the_same_event_is_idempotent(tmp_path):
    t, pid, path, add = setup(tmp_path)
    first = add('evt-1')
    again = add('evt-1')
    assert again['replayed'] is True
    assert path.read_text().count('<!-- event:evt-1 ') == 1
    assert first['hash'] == again['hash']


def test_doctor_counts_unstructured_blocks(tmp_path):
    from aiws.service import Service
    t, pid, path, add = setup(tmp_path)
    path.write_text('# 项目时间线\n\n<!-- event:evt-old hash:' + 'a' * 64
                    + ' -->\n旧正文\n')
    health = Service(tmp_path).call('doctor', {})
    assert health['timeline_unstructured'] == {pid: 1}
