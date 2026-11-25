"""Agent-initiated problem reports to the framework repository.

The Agent asks the user first; only an explicit confirmation reaches GitHub.
Reports carry the problem description and a minimal environment block, never
workspace paths, project names or file contents.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
from urllib.parse import urlencode

from .core import Error, atomic, now, put_json

DEFAULT_REPOSITORY = 'lakii2982/ai-workspace'
REPOSITORY_ENV = 'AIWS_ISSUE_REPO'
MAX_TITLE = 120
MAX_DETAILS = 8000
MAX_STEPS = 20
MAX_STEP = 500
MAX_EXPECTED = 2000
MAX_COMPONENT = 200
MAX_CLIENT = 100
URL_LIMIT = 6000
ISSUE_URL = re.compile(r'https://github\.com/[\w.-]+/[\w.-]+/issues/\d+')
NAME = re.compile(r'[\w.-]+/[\w.-]+')


def repository():
    value = os.environ.get(REPOSITORY_ENV, DEFAULT_REPOSITORY).strip()
    if not NAME.fullmatch(value):
        raise Error('INVALID_ARGUMENT', f'{REPOSITORY_ENV} must be owner/name')
    return value


def text_value(value, name, limit, required=True):
    if value is None:
        value = ''
    if not isinstance(value, str):
        raise Error('INVALID_ARGUMENT', f'{name} must be a string')
    if '\x00' in value:
        raise Error('INVALID_ARGUMENT', f'{name} contains control characters')
    value = value.strip()
    if required and not value:
        raise Error('INVALID_ARGUMENT', f'{name} is required')
    if len(value) > limit:
        raise Error('INVALID_ARGUMENT', f'{name} exceeds {limit} characters')
    return value


def one_line(value, name, limit, required=True):
    return ' '.join(text_value(value, name, limit, required).split())


def framework_version():
    try:
        return importlib.metadata.version('aiws')
    except importlib.metadata.PackageNotFoundError:
        return 'unknown'


class Feedback:
    def __init__(self, workspace):
        self.w = workspace

    def reports_dir(self):
        folder = self.w.path('.workspace/feedback/reports')
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def environment(self, client, stamp=True):
        lines = [
            f'- 框架版本: {framework_version()}',
            f'- 系统: {platform.system()} {platform.release()} ({platform.machine()})',
            f'- Python: {platform.python_version()}',
        ]
        if client:
            lines.append(f'- 客户端: {client}')
        try:
            schema = json.loads(self.w.path('.workspace/config.json').read_text()).get('schema_version')
        except (OSError, ValueError):
            schema = None
        if isinstance(schema, int):
            lines.append(f'- 工作空间格式: {schema}')
        if stamp:
            lines.append(f'- 提交时间: {dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}')
        return '\n'.join(lines)

    def body(self, details, steps, expected, component, client, include_environment, stamp=True):
        parts = ['## 问题现象', '', details]
        if steps:
            parts += ['', '## 复现步骤', '']
            parts += [f'{index}. {step}' for index, step in enumerate(steps, 1)]
        if expected:
            parts += ['', '## 预期与实际', '', expected]
        if component:
            parts += ['', '## 涉及组件', '', component]
        if include_environment:
            parts += ['', '## 运行环境', '', self.environment(client, stamp)]
        parts += ['', '---', '', '由工作空间内的 Agent 在用户同意后提交。']
        return '\n'.join(parts)

    def method(self):
        if shutil.which('gh') is None:
            return 'manual', '本机未安装 GitHub CLI，返回预填链接由用户点开提交。'
        try:
            checked = subprocess.run(['gh', 'auth', 'status'], capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            return 'manual', 'GitHub CLI 不可用，返回预填链接由用户点开提交。'
        if checked.returncode:
            return 'manual', 'GitHub CLI 未登录，返回预填链接由用户点开提交。'
        return 'gh', '使用本机 GitHub CLI 直接提交。'

    def submit_with_gh(self, repo, title, body):
        result = subprocess.run(
            ['gh', 'issue', 'create', '--repo', repo, '--title', title, '--body-file', '-'],
            input=body, capture_output=True, text=True, timeout=90,
            env={**os.environ, 'GH_PROMPT_DISABLED': '1', 'NO_COLOR': '1'},
        )
        if result.returncode:
            raise RuntimeError(f'gh exited {result.returncode}')
        match = ISSUE_URL.search(result.stdout)
        if not match:
            raise RuntimeError('gh did not return an issue URL')
        return match.group(0)

    def manual_url(self, repo, title, body):
        prefix = f'https://github.com/{repo}/issues/new?'
        text = body
        note = '\n\n（正文过长，链接中已截断；完整正文见工具返回的 issue.body 或本地报告文件。）'
        if len(prefix) + len(urlencode({'title': title, 'body': text})) > URL_LIMIT:
            text = body[:2000] + note
            while len(prefix) + len(urlencode({'title': title, 'body': text})) > URL_LIMIT and len(text) > 800:
                text = text[:len(text) // 2] + note
        return prefix + urlencode({'title': title, 'body': text})

    def find_submitted(self, fingerprint):
        folder = self.reports_dir()
        for path in sorted(folder.glob('ISS-*.json'), reverse=True)[:200]:
            try:
                record = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if record.get('fingerprint') == fingerprint and record.get('status') == 'submitted':
                return {'status': 'submitted', 'report_id': record['report_id'],
                        'repository': record['repository'], 'issue': record['issue'],
                        'url': record.get('url', ''), 'submission': record.get('submission', {}),
                        'record': path.relative_to(self.w.root).as_posix()}
        return None

    def report_issue(self, title, details, steps=None, expected='', component='', confirm=False,
                     include_environment=True, client=''):
        title = one_line(title, 'title', MAX_TITLE)
        details = text_value(details, 'details', MAX_DETAILS)
        expected = text_value(expected, 'expected', MAX_EXPECTED, required=False)
        component = one_line(component, 'component', MAX_COMPONENT, required=False)
        client = one_line(client, 'client', MAX_CLIENT, required=False)
        if steps is None:
            steps = []
        if not isinstance(steps, list) or len(steps) > MAX_STEPS:
            raise Error('INVALID_ARGUMENT', f'steps must be a list of at most {MAX_STEPS} strings')
        if any(not isinstance(step, str) for step in steps):
            raise Error('INVALID_ARGUMENT', 'steps must contain strings')
        steps = [step.strip() for step in steps if step.strip()]
        if any(len(step) > MAX_STEP for step in steps):
            raise Error('INVALID_ARGUMENT', f'each step must be at most {MAX_STEP} characters')
        if not isinstance(confirm, bool) or not isinstance(include_environment, bool):
            raise Error('INVALID_ARGUMENT', 'confirm and include_environment must be booleans')
        repo = repository()
        issue_title = f'[Agent 反馈] {title}'
        issue_body = self.body(details, steps, expected, component, client, include_environment)
        # The timestamp changes between preview and submit; leave it out of the identity.
        identity = self.body(details, steps, expected, component, client, include_environment, stamp=False)
        fingerprint = hashlib.sha256((repo + '\n' + issue_title + '\n' + identity).encode()).hexdigest()

        if not confirm:
            method, detail = self.method()
            return {
                'requires_confirmation': True,
                'repository': repo,
                'issue': {'title': issue_title, 'body': issue_body},
                'submission': {'method': method, 'detail': detail},
                'next_step': '把 issue 的标题和正文展示给用户；用户同意后再带 confirm=true 调用一次。',
            }

        with self.w.lock('service-feedback'):
            existing = self.find_submitted(fingerprint)
            if existing:
                existing['replayed'] = True
                return existing
            stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d-%H%M%S')
            report_id = f'ISS-{stamp}-{fingerprint[:6]}'
            folder = self.reports_dir()
            record_path = folder / f'{report_id}.json'
            record = {'schema_version': 1, 'report_id': report_id, 'fingerprint': fingerprint,
                      'created_at': now(), 'repository': repo, 'status': 'pending',
                      'issue': {'title': issue_title, 'body': issue_body}, 'submission': {}}
            put_json(record_path, record)

            method, detail = self.method()
            error = None
            if method == 'gh':
                try:
                    url = self.submit_with_gh(repo, issue_title, issue_body)
                    status = 'submitted'
                except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                    method, status, error = 'manual', 'manual', type(exc).__name__
                    detail = 'GitHub CLI 提交失败，返回预填链接由用户点开提交。'
                    url = self.manual_url(repo, issue_title, issue_body)
            else:
                status = 'manual'
                url = self.manual_url(repo, issue_title, issue_body)

            markdown = folder / f'{report_id}.md'
            atomic(markdown, f'# {issue_title}\n\n{issue_body}\n'.encode())
            record.update(status=status, url=url, submitted_at=now(),
                          submission={'method': method, 'detail': detail})
            if error:
                record['submission']['error'] = error
            put_json(record_path, record)
            return {'status': status, 'report_id': report_id, 'repository': repo,
                    'issue': {'title': issue_title, 'body': issue_body}, 'url': url,
                    'submission': record['submission'], 'replayed': False,
                    'record': record_path.relative_to(self.w.root).as_posix(),
                    'markdown': markdown.relative_to(self.w.root).as_posix()}
