import json

import pytest

from aiws.core import Error, Workspace
from aiws.service import Service


def fake_gh(tmp_path, url='https://github.com/owner/repo/issues/7', fail=False):
    binary = tmp_path / 'bin'
    binary.mkdir()
    log = tmp_path / 'gh.log'
    body = tmp_path / 'gh.body'
    outcome = 'exit 1' if fail else f'echo "{url}"'
    script = binary / 'gh'
    script.write_text(
        '#!/bin/sh\n'
        'if [ "$1" = "auth" ]; then exit 0; fi\n'
        f"cat > '{body}'\n"
        f"echo issue >> '{log}'\n"
        f'{outcome}\n'
    )
    script.chmod(0o755)
    return binary, log, body


def call(workspace, **args):
    return Service(workspace.root).call('report_issue', args)


def test_preview_has_no_side_effects(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path / 'ws')
    workspace.init()
    monkeypatch.setenv('PATH', '')
    result = call(workspace, title='创建任务失败', details='create_job 返回 INTERNAL_ERROR')
    assert result['requires_confirmation'] is True
    assert result['issue']['title'] == '[Agent 反馈] 创建任务失败'
    assert 'create_job 返回 INTERNAL_ERROR' in result['issue']['body']
    assert result['submission']['method'] == 'manual'
    assert str(tmp_path) not in result['issue']['body']
    assert not (workspace.root / '.workspace/feedback').exists()


def test_manual_submission_writes_record(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path / 'ws')
    workspace.init()
    monkeypatch.setenv('PATH', '')
    result = call(workspace, title='入口不可用', details='prepare_work_context 报错', confirm=True)
    assert result['status'] == 'manual'
    assert result['url'].startswith('https://github.com/lakii2982/ai-workspace/issues/new?')
    assert str(tmp_path) not in result['issue']['body']
    record = json.loads((workspace.root / result['record']).read_text())
    assert record['status'] == 'manual'
    assert record['issue']['body'] == result['issue']['body']
    assert (workspace.root / result['markdown']).read_text().startswith('# [Agent 反馈]')


def test_gh_submission(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path / 'ws')
    workspace.init()
    binary, log, body_file = fake_gh(tmp_path)
    monkeypatch.setenv('PATH', f'{binary}:/usr/bin:/bin')
    result = call(workspace, title='保存检查点失败', details='checkpoint_job 返回 INTERNAL_ERROR',
                  steps=['打开任务', '保存检查点'], client='claude-code 2.1.330', confirm=True)
    assert result['status'] == 'submitted'
    assert result['url'] == 'https://github.com/owner/repo/issues/7'
    submitted = body_file.read_text()
    assert '1. 打开任务' in submitted
    assert 'claude-code 2.1.330' in submitted
    record = json.loads((workspace.root / result['record']).read_text())
    assert record['submission']['method'] == 'gh'
    assert record['status'] == 'submitted'
    assert log.read_text().count('issue') == 1


def test_gh_failure_falls_back_to_link(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path / 'ws')
    workspace.init()
    binary, _, _ = fake_gh(tmp_path, fail=True)
    monkeypatch.setenv('PATH', f'{binary}:/usr/bin:/bin')
    result = call(workspace, title='任务无法恢复', details='resume_job 返回 INTERNAL_ERROR', confirm=True)
    assert result['status'] == 'manual'
    assert result['submission']['error'] == 'RuntimeError'
    assert result['url'].startswith('https://github.com/lakii2982/ai-workspace/issues/new?')


def test_repeat_submission_is_replayed(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path / 'ws')
    workspace.init()
    binary, log, _ = fake_gh(tmp_path)
    monkeypatch.setenv('PATH', f'{binary}:/usr/bin:/bin')
    first = call(workspace, title='重复问题', details='同一个错误出现了两次', confirm=True)
    second = call(workspace, title='重复问题', details='同一个错误出现了两次', confirm=True)
    assert second['replayed'] is True
    assert second['report_id'] == first['report_id']
    assert second['url'] == first['url']
    assert log.read_text().count('issue') == 1


def test_environment_can_be_omitted(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path / 'ws')
    workspace.init()
    monkeypatch.setenv('PATH', '')
    result = call(workspace, title='无环境', details='不带环境块', include_environment=False)
    assert '运行环境' not in result['issue']['body']
    assert '框架版本' not in result['issue']['body']


def test_repository_can_be_overridden(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path / 'ws')
    workspace.init()
    monkeypatch.setenv('AIWS_ISSUE_REPO', 'someone/elsewhere')
    assert call(workspace, title='覆盖仓库', details='测试')['repository'] == 'someone/elsewhere'
    monkeypatch.setenv('AIWS_ISSUE_REPO', 'not a repo')
    with pytest.raises(Error) as error:
        call(workspace, title='覆盖仓库', details='测试')
    assert error.value.code == 'INVALID_ARGUMENT'


@pytest.mark.parametrize('payload', [
    {'title': '', 'details': '内容'},
    {'title': '标题', 'details': ''},
    {'title': '标' * 121, 'details': '内容'},
    {'title': '标题', 'details': '内容', 'steps': ['步骤'] * 21},
    {'title': '标题', 'details': '内容', 'steps': '不是列表'},
    {'title': '标题', 'details': '内容', 'confirm': 'yes'},
])
def test_invalid_reports_are_rejected(tmp_path, payload):
    workspace = Workspace(tmp_path / 'ws')
    workspace.init()
    with pytest.raises(Error) as error:
        Service(workspace.root).call('report_issue', payload)
    assert error.value.code == 'INVALID_ARGUMENT'
