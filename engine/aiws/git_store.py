"""Project-local Git versions without changing the user's index or branch."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid

from .core import Error


class GitStore:
    def __init__(self, w, project_id):
        self.w = w
        self.project = w.project_for(project_id)
        self.root = w.path(self.project['path'])

    @property
    def enabled(self):
        return (self.root / '.git').is_dir()

    def _git(self, *args, env=None, check=True):
        r = subprocess.run(['git', '-C', str(self.root), *args], capture_output=True,
                           env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0', **(env or {})}, timeout=120)
        if check and r.returncode:
            raise Error('GIT_ERROR', r.stderr.decode(errors='replace').strip())
        return r

    def initialize(self):
        if (self.root / '.git').exists() and not self.enabled:
            raise Error('GIT_LAYOUT_UNSUPPORTED', 'Use a project-local Git directory')
        if not self.enabled:
            self._git('init', '--quiet')
            # Runtime environments are not business versions. Preserve existing rules.
            ignore = self.root / '.git' / 'info' / 'exclude'
            with ignore.open('a') as f:
                f.write('\n**/.venv/\n**/__pycache__/\n**/.pytest_cache/\n**/.DS_Store\n**/dist/\n**/build/\n')
        return self.inspect()

    def inspect(self):
        if not self.enabled:
            return dict(enabled=False, head=None, branch=None, status=[], ignored=[])
        head = self._git('rev-parse', '--verify', 'HEAD', check=False)
        branch = self._git('symbolic-ref', '--quiet', '--short', 'HEAD', check=False)
        status = self._git('status', '--porcelain=v1', '-z', '--untracked-files=all').stdout
        parts = status.split(b'\0')
        rows = []
        i = 0
        while i < len(parts):
            entry = parts[i]
            i += 1
            if not entry:
                continue
            code = entry[:2].decode()
            row = {'path': os.fsdecode(entry[3:]), 'index': code[0], 'worktree': code[1]}
            if 'R' in code or 'C' in code:
                row['old_path'] = os.fsdecode(parts[i]); i += 1
            rows.append(row)
        ignored = [os.fsdecode(p) for p in self._git('ls-files', '--others', '--ignored', '--exclude-standard', '-z').stdout.split(b'\0') if p]
        return dict(enabled=True, head=head.stdout.decode().strip() if head.returncode == 0 else None,
                    branch=branch.stdout.decode().strip() or None, status=rows, ignored=ignored)

    def _nested_repositories(self):
        roots = []
        for current, dirs, files in os.walk(self.root):
            relative = Path(current).relative_to(self.root)
            if relative.parts and ('.git' in dirs or '.git' in files):
                roots.append(relative.as_posix())
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if d not in ('.git', '.venv', '__pycache__', '.pytest_cache')]
        return roots

    def _resolve(self, commit):
        if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-fA-F]{40,64}', commit):
            raise Error('INVALID_ARGUMENT', 'A full Git commit object ID is required')
        result = self._git('rev-parse', '--verify', commit + '^{commit}', check=False)
        if result.returncode:
            raise Error('GIT_VERSION_MISSING', 'Git version is unavailable')
        return result.stdout.decode().strip()

    def _files(self, commit):
        result = {}
        for item in self._git('ls-tree', '-r', '-z', commit).stdout.split(b'\0'):
            if item:
                meta, path = item.split(b'\t', 1)
                mode, kind, oid = meta.split()
                if kind == b'blob':
                    result[os.fsdecode(path)] = oid.decode()
        return result

    def observe(self, base=None):
        if not self.enabled:
            raise Error('GIT_NOT_INITIALIZED', 'Initialize the project Git repository first')
        if base:
            base = self._resolve(base)
        with self.w.lock('git:' + self.project['project_id']):
            info = self.inspect()
            nested = self._nested_repositories()
            with tempfile.TemporaryDirectory(dir=self.root / '.git', prefix='aiws-index-') as folder:
                env = {'GIT_INDEX_FILE': str(Path(folder) / 'index')}
                if info['head']:
                    self._git('read-tree', info['head'], env=env)
                else:
                    self._git('read-tree', '--empty', env=env)
                # An ignored parent already removes its nested repository;
                # explicitly mentioning it as a pathspec makes git add fail.
                excludes = [':(literal,exclude)' + path for path in nested
                            if self._git('check-ignore', '--quiet', '--', path, check=False).returncode != 0]
                self._git('add', '-A', '--', '.', *excludes, env=env)
                indexed = [os.fsdecode(p) for p in self._git('ls-files', '-z', env=env).stdout.split(b'\0') if p]
                excluded = [p for p in indexed if any(p == n or p.startswith(n + '/') for n in nested)]
                for start in range(0, len(excluded), 256):
                    self._git('update-index', '--force-remove', '--', *excluded[start:start+256], env=env)
                # An ignored file can already be staged (including on an unborn
                # branch). Preserve tracking while snapshotting its disk content.
                tracked = [os.fsdecode(p) for p in self._git('ls-files', '-z').stdout.split(b'\0') if p]
                tracked = [path for path in tracked if (self.root / path).is_file()
                           and not any(path == n or path.startswith(n + '/') for n in nested)]
                for start in range(0, len(tracked), 256):
                    self._git('add', '-f', '--', *tracked[start:start+256], env=env)
                tree = self._git('write-tree', env=env).stdout.decode().strip()
            old = self._git('rev-parse', '--verify', 'refs/aiws/observations/latest', check=False)
            previous = old.stdout.decode().strip() if old.returncode == 0 else None
            old_tree = self._git('rev-parse', previous + '^{tree}').stdout.decode().strip() if previous else None
            if tree == old_tree:
                commit = previous
            else:
                author = {'GIT_AUTHOR_NAME': 'AIWS Snapshot', 'GIT_AUTHOR_EMAIL': 'snapshot@aiws.local',
                          'GIT_COMMITTER_NAME': 'AIWS Snapshot', 'GIT_COMMITTER_EMAIL': 'snapshot@aiws.local'}
                parent = previous or info['head']
                args = ['commit-tree', tree] + (['-p', parent] if parent else []) + ['-m', 'AIWS project state snapshot']
                commit = self._git(*args, env=author).stdout.decode().strip()
                # Keep every returned observation available for reading confirmations.
                self._git('update-ref', 'refs/aiws/observations/' + commit, commit)
                self._git('update-ref', 'refs/aiws/observations/latest', commit)
            files = self._files(commit)
            changes = []
            if base:
                tokens = self._git('diff', '--name-status', '-z', '-M', base, commit).stdout.split(b'\0')
                i = 0
                while i < len(tokens) and tokens[i]:
                    code = tokens[i].decode(); i += 1
                    old_path = None
                    path = os.fsdecode(tokens[i]); i += 1
                    if code.startswith(('R', 'C')):
                        old_path = path; path = os.fsdecode(tokens[i]); i += 1
                    changes.append(dict(path=path, old_path=old_path, type=code[0]))
            else:
                changes = [dict(path=p, old_path=None, type='A') for p in files]
            return dict(commit=commit, ref='refs/aiws/observations/' + commit,
                        files=files, changes=changes, ignored=info['ignored'], head=info['head'],
                        unsupported_repositories=nested)

    def snapshot(self, checkpoint_id=None):
        result = self.observe()
        name = checkpoint_id or uuid.uuid4().hex
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', name):
            raise Error('INVALID_ARGUMENT', 'Invalid checkpoint identity')
        ref = 'refs/aiws/checkpoints/' + name
        prior = self._git('rev-parse', '--verify', ref, check=False)
        if prior.returncode == 0 and prior.stdout.decode().strip() != result['commit']:
            raise Error('CONFLICT', 'Checkpoint identity already refers to another version')
        self._git('update-ref', ref, result['commit'])
        return {**result, 'ref': ref}

    def file_oid(self, commit, path):
        return self._files(self._resolve(commit)).get(path)

    def read_version(self, commit, path):
        commit = self._resolve(commit)
        if path not in self._files(commit):
            raise Error('NOT_FOUND', 'File not present in this Git version')
        return self._git('show', commit + ':' + path).stdout
