import base64
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from aiws import mcp_server
from aiws.core import Error, Workspace, digest
from aiws.jobs import Jobs
from aiws.service import Service


def fixture(tmp_path):
    workspace=Workspace(tmp_path);workspace.init();project=workspace.project_create('附件')
    scope=dict(include_company=False,include_professional=False,project_ids=[project['project_id']],topic_ids=[])
    job=Jobs(workspace).create('接收附件','保存大文件',scope)
    return workspace,project,job


def test_cli_stdin_and_mcp_invoke_round_trip_two_megabytes(tmp_path, monkeypatch):
    workspace,project,job=fixture(tmp_path)
    payload=(b'large-office-payload\n' * ((2 * 1024 * 1024) // 21 + 1))[:2 * 1024 * 1024]
    relative=project['path']+'/工作/'+job['directory_name']+'/工作稿/附件.bin'
    params={'jid':job['job_id'],'relative':relative,
            'content_base64':base64.b64encode(payload).decode(),'expected_hash':None}
    env={**os.environ,'PYTHONPATH':str(Path(__file__).parents[1])}
    command=[sys.executable,'-m','aiws','--workspace',str(tmp_path),'call','save_artifact','--params','-']
    raw=subprocess.run(command,input=json.dumps(params),text=True,capture_output=True,env=env,check=True)
    result=json.loads(raw.stdout)
    assert result['ok'] and result['result']['hash']==digest(workspace.path(relative))
    assert workspace.path(relative).read_bytes()==payload

    monkeypatch.setenv('AIWS_WORKSPACE',str(tmp_path))
    second=relative.replace('附件.bin','附件-二.bin')
    response=mcp_server.invoke('save_artifact',dict(jid=job['job_id'],relative=second,
        content_base64=base64.b64encode(payload).decode(),expected_hash=None))
    assert response['ok'] and response['result']['hash']==digest(workspace.path(second))
    assert workspace.path(second).read_bytes()==payload


def test_checkout_commit_reads_only_controlled_copy_id(tmp_path):
    workspace,project,job=fixture(tmp_path);service=Service(tmp_path)
    checkout=service.call('checkout_draft',dict(jid=job['job_id'],project_id=project['project_id'],name='附件.bin',create=True))
    Path(workspace.path(checkout['path'])).write_bytes(b'employee attachment')
    saved=service.call('commit_draft',dict(jid=job['job_id'],copy_id=checkout['copy_id']))
    assert workspace.path(saved['path']).read_bytes()==b'employee attachment'
    with pytest.raises(Error) as error:
        service.call('commit_draft',dict(jid=job['job_id'],copy_id='0'*32))
    assert error.value.code == 'NOT_FOUND'
