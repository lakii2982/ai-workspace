import subprocess
import sys
from aiws.core import Workspace, Error
import pytest

def test_init_identity_and_external_cli(tmp_path):
    w=Workspace(tmp_path/'space'); w.init()
    config=w.state/'config.json'; original=config.read_bytes(); w.init()
    assert config.read_bytes()==original
    p=w.project_create('建设'); (w.root/p['path']).rename(w.root/'项目/改名')
    assert w.projects()[0]['project_id']==p['project_id']
    r=subprocess.run([sys.executable,'-m','aiws','--workspace',str(w.root),'project','list'],cwd='/tmp',capture_output=True,text=True)
    assert r.returncode==0 and p['project_id'] in r.stdout

def test_paths(tmp_path):
    w=Workspace(tmp_path);w.init()
    (tmp_path/'escape').symlink_to('/tmp')
    for p in ['../x','/tmp/x','escape/x']:
        with pytest.raises(Error,match='relative|escapes|Symbolic'): w.path(p)
