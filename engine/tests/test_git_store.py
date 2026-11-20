import subprocess
import pytest
from aiws.core import Workspace, Error
from aiws.git_store import GitStore


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args])


def test_git_snapshot_mixed_changes(tmp_path):
    w = Workspace(tmp_path); w.init(); p = w.project_create('中文 项目')
    s = GitStore(w, p['project_id']); assert not s.enabled
    s.initialize(); root = s.root
    (root/'方案 空格.md').write_text('初稿')
    git(root, 'add', '.')
    git(root, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-m', 'baseline')
    head = git(root, 'rev-parse', 'HEAD'); index = (root/'.git/index').read_bytes()
    a = s.snapshot('first')
    assert git(root, 'rev-parse', 'HEAD') == head
    assert (root/'.git/index').read_bytes() == index
    assert s.read_version(a['commit'], '方案 空格.md').decode() == '初稿'
    (root/'方案 空格.md').write_text('暂存')
    git(root, 'add', '方案 空格.md')
    (root/'方案 空格.md').write_text('又修改')
    (root/'新资料.pdf').write_bytes(b'pdf')
    index = (root/'.git/index').read_bytes()
    b = s.observe(a['commit'])
    assert {x['path'] for x in b['changes']} == {'方案 空格.md','新资料.pdf'}
    assert (root/'.git/index').read_bytes() == index
    assert s.observe()['commit'] == b['commit']
    assert s.read_version(b['commit'],'方案 空格.md').decode() == '又修改'
    with pytest.raises(Error): s.snapshot('first')
    (root/'方案 空格.md').rename(root/'改名.md')
    c = s.observe(b['commit'])
    assert any(x['type']=='R' and x['old_path']=='方案 空格.md' for x in c['changes'])
    (root/'改名.md').unlink()
    assert any(x['type']=='D' for x in s.observe(c['commit'])['changes'])


def test_unborn_ignored_and_external_commits(tmp_path):
    w = Workspace(tmp_path); w.init(); p = w.project_create('初次')
    s = GitStore(w,p['project_id']); s.initialize(); root=s.root
    (root/'.gitignore').write_text('*.tmp\n')
    (root/'忽略.tmp').write_text('temporary')
    first=s.observe()
    assert first['head'] is None and '忽略.tmp' in first['ignored']
    assert '忽略.tmp' not in first['files']
    assert git(root,'symbolic-ref','HEAD')
    (root/'资料.md').write_text('new')
    git(root,'add','.')
    git(root,'-c','user.name=Test','-c','user.email=test@example.com','commit','-m','manual')
    assert s.inspect()['status']==[]
    assert '资料.md' in [c['path'] for c in s.observe(first['commit'])['changes']]


def test_ignored_already_tracked_and_nested_repo(tmp_path):
    w=Workspace(tmp_path);w.init();p=w.project_create('nested')
    s=GitStore(w,p['project_id']);s.initialize();root=s.root
    (root/'tracked.tmp').write_text('track me')
    git(root,'add','tracked.tmp')
    (root/'.gitignore').write_text('*.tmp\n')
    first=s.observe()
    assert 'tracked.tmp' in first['files']
    nested=root/'nested';nested.mkdir()
    git(nested,'init','--quiet')
    (nested/'doc.md').write_text('inner')
    git(nested,'add','.')
    second=s.observe(first['commit'])
    assert second['unsupported_repositories'] == ['nested']
    assert 'nested' not in second['files'] and 'nested/doc.md' not in second['files']


def test_ignored_parent_with_unborn_nested_repository(tmp_path):
    w=Workspace(tmp_path);w.init();p=w.project_create('ignored parent')
    s=GitStore(w,p['project_id']);s.initialize();root=s.root
    (root/'.gitignore').write_text('runtime/\n')
    nested=root/'runtime'/'sample';nested.mkdir(parents=True)
    git(nested,'init','--quiet')
    (nested/'doc.md').write_text('inner')
    snap=s.observe()
    assert snap['unsupported_repositories']==['runtime/sample']
    assert not any(path.startswith('runtime/') for path in snap['files'])
    assert s.observe()['commit']==snap['commit']
