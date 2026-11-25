"""Stable launcher and reversible, per-workspace core activation for local installs."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.error import HTTPError
from urllib.request import Request, urlopen

# Keep bootstrap dependencies in the original installation. A candidate never
# replaces or moves its predecessor's interpreter or in-use work files.
from aiws.core import atomic, put_json

REPOSITORY = 'lakii2982/ai-workspace'
SCHEMA = 'employee-additive-v1'


def run(argv, env=None):
    result = subprocess.run(argv, env=env, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout)[-1500:])
    return result.stdout


def directories(root, workspace):
    key = hashlib.sha256(os.fsencode(workspace)).hexdigest()
    return root / '.aiws-releases', root / '.aiws-releases' / 'workspaces' / key


def current(root, state):
    marker = state / 'active'
    if not marker.exists():
        return root
    version = marker.read_text().strip()
    if not re.fullmatch(r'[0-9a-f]{40}', version):
        raise ValueError('Invalid active release')
    return root / '.aiws-releases' / version


def get_archive(sha, archive):
    url = f'https://codeload.github.com/{REPOSITORY}/tar.gz/{sha}'
    with urlopen(url, timeout=90) as response, archive.open('wb') as output:
        shutil.copyfileobj(response, output)
    with tarfile.open(archive, 'r:gz') as package:
        for item in package:
            parts = Path(item.name).parts
            if not parts or parts[0] != 'ai-workspace-' + sha or '..' in parts:
                raise ValueError('Unexpected release archive path')
            if item.isdir():
                continue
            if not item.isfile() or len(parts) < 2:
                raise ValueError('Unexpected release archive entry')
            path = archive.parent / 'source' / Path(*parts[1:])
            path.parent.mkdir(parents=True, exist_ok=True)
            with package.extractfile(item) as content, path.open('xb') as destination:
                shutil.copyfileobj(content, destination)
            path.chmod(0o755 if item.mode & 0o111 else 0o644)


def resolve_channel(channel=None):
    """Resolve the release channel to a commit SHA.

    ``stable`` is the default and follows the published release branch.
    ``preview`` follows main. Until the first release creates ``stable``,
    the stable channel falls back to main so existing installs can upgrade.
    """
    channel = (channel or os.environ.get('AIWS_CHANNEL') or 'stable').strip()
    if channel not in ('stable', 'preview'):
        raise ValueError(f'Unknown upgrade channel: {channel}')
    refs = ('stable', 'main') if channel == 'stable' else ('main',)
    for ref in refs:
        req = Request(f'https://api.github.com/repos/{REPOSITORY}/commits/{ref}',
                      headers={'User-Agent': 'aiws-local-upgrade'})
        try:
            with urlopen(req, timeout=20) as response:
                sha = json.load(response)['sha']
        except HTTPError as error:
            if error.code == 404:
                continue
            raise
        if not re.fullmatch(r'[0-9a-f]{40}', sha):
            raise ValueError('Invalid release SHA')
        return sha, ref
    raise ValueError('No release channel is available')


def prepare(root, sha, scratch):
    releases = root / '.aiws-releases'
    target = releases / sha
    # Different workspaces may upgrade to the same release concurrently.
    with (releases / 'build.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ready = target / '.ready'
        if ready.is_file() and ready.read_text() == sha:
            return target
        get_archive(sha, scratch / 'release.tar.gz')
        source = scratch / 'source'
        if (source / 'engine/upgrade-schema.txt').read_text().strip() != SCHEMA:
            raise ValueError('Release requires a dedicated data migration')
        # A venv embeds its absolute installation path. Build at its final path.
        if target.exists():
            if target.is_symlink() or not (target / '.building').is_file():
                raise ValueError('Unrecognized release directory')
            shutil.rmtree(target)
        (source / '.building').touch()
        shutil.copytree(source, target)
        run([target / 'install.sh', scratch / 'build-test', '--agent', 'cli'])
        atomic(ready, sha.encode())
        (target / '.building').unlink()
        return target


def cli(release, workspace, *args):
    env = {**os.environ, 'AIWS_WORKSPACE': str(workspace)}
    raw = run([release / 'engine/.venv/bin/aiws', '--workspace', workspace, *args], env)
    result = json.loads(raw)
    if not result['ok']:
        raise RuntimeError(str(result['error']))
    return result['result']


def verify(release, workspace, expected):
    actual = cli(release, workspace, 'doctor')
    for key in ('sqlite', 'projects', 'jobs'):
        if actual[key] != expected[key]:
            raise ValueError(f'Workspace verification failed: {key}')
    if sorted(actual['changed_artifacts']) != sorted(expected['changed_artifacts']):
        raise ValueError('Workspace verification failed: file fingerprints')
    tasks = cli(release, workspace, 'job', 'list', '--params', '{"limit":1,"status":null}')
    if tasks['items']:
        cli(release, workspace, 'job', 'resume', '--params', json.dumps({'jid': tasks['items'][0]['job_id']}))


def upgrade(root, workspace, state):
    previous = current(root, state)
    journal = state / 'upgrade.json'
    marker = state / 'active'
    # An interrupted activation can only be pending in the short interval
    # between the atomic pointer write and the final journal update.
    record = json.loads(journal.read_text()) if journal.exists() else {}
    if record.get('phase') == 'activating':
        restore_pointer(marker, record['previous'])
        record.update(phase='recovered')
        put_json(journal, record)
        previous = current(root, state)
    sha, channel = resolve_channel()
    if previous.name == sha:
        return {'phase': 'current', 'version': sha, 'channel': channel}
    prior = None if previous == root else previous.name
    record = dict(schema_version=1, phase='preparing', target=sha, previous=prior, channel=channel)
    put_json(journal, record)
    try:
        backup = cli(previous, workspace, 'backup')
        record.update(phase='backup_created', backup=backup)
        put_json(journal, record)
        with tempfile.TemporaryDirectory(dir=state) as stage:
            scratch = Path(stage)
            trial = scratch / 'trial'
            cli(previous, workspace, 'restore', '--params', json.dumps(dict(archive=backup['path'], destination=str(trial))))
            baseline = cli(previous, trial, 'doctor')
            candidate = prepare(root, sha, scratch)
            verify(candidate, trial, baseline)
            live = cli(previous, workspace, 'doctor')
            verify(candidate, workspace, live)
            record.update(phase='activating')
            put_json(journal, record)
            atomic(marker, sha.encode())
            verify(current(root, state), workspace, live)
        record.update(phase='complete')
        put_json(journal, record)
        return record
    except Exception as error:
        restore_pointer(marker, prior)
        record.update(phase='rolled_back', detail=str(error))
        put_json(journal, record)
        raise


def restore_pointer(marker, prior):
    if prior is None:
        marker.unlink(missing_ok=True)
    else:
        atomic(marker, prior.encode())


def adopt_legacy(root, workspace):
    """Bootstrap the previous split-directory release in place, retaining its old runtime."""
    launcher = root / 'aiws'
    if not launcher.is_file() or launcher.is_symlink():
        return False
    original = launcher.read_bytes()
    legacy_hash = '13b268aceef2424dfdf025366b91db325a24382cd0235ac6ec08ae8e034e189d'
    shim = (b'#!/bin/sh\nset -eu\nbase=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
            b'exec "$base/engine/.venv/bin/python" "$base/.aiws-releases/bootstrap.py" "$@"\n')
    if original == shim:
        return True
    if hashlib.sha256(original).hexdigest() != legacy_hash:
        return False
    releases, state = directories(root, workspace)
    state.mkdir(parents=True, exist_ok=True)
    with (releases / 'adopt.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if launcher.read_bytes() == shim:
            return True
        upgrade(root, workspace, state)
        atomic(releases / 'legacy-launcher', original)
        atomic(releases / 'bootstrap.py', Path(__file__).read_bytes())
        # Replace only after checking the candidate. Preserve the executable bit
        # across rename; a power loss leaves either launcher runnable.
        with tempfile.NamedTemporaryFile(dir=root, delete=False) as temp:
            temp.write(shim)
            temp.flush()
            os.fchmod(temp.fileno(), 0o755)
            os.fsync(temp.fileno())
        Path(temp.name).replace(launcher)
    return True


def main():
    root = Path(__file__).resolve().parents[1]
    args = sys.argv[1:]
    workspace = Path(os.environ.get('AIWS_WORKSPACE', root.parent / 'Workspace'))
    # argparse also accepts --workspace after the operation.
    for index, arg in enumerate(args):
        if arg == '--workspace' and index + 1 < len(args):
            workspace = Path(args[index + 1])
        elif arg.startswith('--workspace='):
            workspace = Path(arg.partition('=')[2])
    workspace = workspace.expanduser().resolve()
    os.environ.update(AIWS_WORKSPACE=str(workspace), AIWS_LAUNCHER=str(root / 'aiws'))
    _, state = directories(root, workspace)
    state.mkdir(parents=True, exist_ok=True)
    operation = [arg for i, arg in enumerate(args) if arg != '--workspace' and
                 not arg.startswith('--workspace=') and (i == 0 or args[i-1] != '--workspace')]
    upgrading = operation == ['upgrade']
    with (state / 'session.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX if upgrading else fcntl.LOCK_SH)
        journal = state / 'upgrade.json'
        if journal.exists() and json.loads(journal.read_text()).get('phase') == 'activating':
            fcntl.flock(lock, fcntl.LOCK_EX)
            interrupted = json.loads(journal.read_text())
            if interrupted['phase'] == 'activating':
                restore_pointer(state / 'active', interrupted['previous'])
                put_json(journal, {**interrupted, 'phase': 'recovered'})
            if not upgrading:
                fcntl.flock(lock, fcntl.LOCK_SH)
        if upgrading:
            result = upgrade(root, workspace, state)
            print(json.dumps({'ok': True, 'result': result}, ensure_ascii=False))
            return
        release = current(root, state)
        if args == ['--mcp']:
            # Each tool call goes through this launcher; do not hold a read lock
            # for an idle desktop server's entire lifetime.
            executable = release / 'engine/.venv/bin/aiws-mcp'
            args = []
        else:
            os.set_inheritable(lock.fileno(), True)
            executable = release / 'engine/.venv/bin/aiws'
        os.execv(executable, [str(executable), *args])


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'ok': False, 'error': {'code': 'UPGRADE_OR_LAUNCH_FAILED', 'message': str(error)}}))
        sys.exit(1)
