import subprocess

from aiws import mcp_server


def test_timeout_is_reported_as_timeout(monkeypatch):
    monkeypatch.setenv('AIWS_WORKSPACE', '/tmp/aiws-timeout-probe')

    def expired(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd='aiws', timeout=1)

    monkeypatch.setattr(subprocess, 'run', expired)
    result = mcp_server.invoke('list_projects', {})
    assert result['ok'] is False
    assert result['error']['code'] == 'TIMEOUT'
    assert 'exceeded' in result['error']['message']


def test_missing_workspace_is_an_entry_error(monkeypatch):
    monkeypatch.delenv('AIWS_WORKSPACE', raising=False)
    result = mcp_server.invoke('list_projects', {})
    assert result['error']['code'] == 'ENTRY_UNAVAILABLE'


def test_operation_timeouts_differ_by_cost():
    assert mcp_server.timeout_for('list_projects') == mcp_server.READ_TIMEOUT
    assert mcp_server.timeout_for('write_work_file') == mcp_server.WRITE_TIMEOUT
    assert mcp_server.timeout_for('backup') == 600
    assert mcp_server.timeout_for('upgrade_core') == 900
