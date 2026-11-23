"""Shared desktop workflows. All paths are relative to one workspace."""
import hashlib
import json
import re
import uuid
from pathlib import Path

from .core import Error, atomic, digest, load, now, put_json
from .files import Files
from .jobs import Artifacts, Jobs
from .remote_store import enabled
from .workflow import check_lease


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{32}', value):
        raise Error('INVALID_ARGUMENT', 'Invalid resource ID')
    return value


def fingerprint(*values):
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()[:32]


def page(items, limit, offset):
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
        raise Error('INVALID_ARGUMENT', 'Invalid pagination')
    end = offset + limit
    return {'items': items[offset:end], 'next_offset': end if end < len(items) else None,
            'has_more': end < len(items)}


class Desktop:
    def __init__(self, workspace):
        self.w = workspace
        self.jobs = Jobs(workspace)
        self.files = Files(workspace)

    def task_folder(self, jid, project_id):
        for entry in self.files.directories(jid):
            if entry['project_id'] == project_id:
                return entry
        raise Error('INVALID_SCOPE', 'Project outside task')

    def draft(self, jid, project_id, name):
        if not name or Path(name).name != name or name in ('.', '..'):
            raise Error('INVALID_ARGUMENT', 'A single filename is required')
        folder = self.task_folder(jid, project_id)
        return self.w.path(Path(folder['work']) / '工作稿' / name)

    def guard(self, jid, lease_token):
        check_lease(self.jobs.get(jid), lease_token, require=enabled(self.w))

    def checkout_draft(self, jid, project_id, name, lease_token=None):
        self.guard(jid, lease_token)
        source = self.draft(jid, project_id, name)
        if not source.is_file():
            raise Error('NOT_FOUND', 'Draft does not exist')
        data = source.read_bytes()
        copy_id = uuid.uuid4().hex
        folder = Path('.workspace/edits', jid, copy_id)
        copy = folder / 'content' / name
        record = dict(schema_version=1, copy_id=copy_id, source=str(source.relative_to(self.w.root)),
                      path=str(copy), base_hash=hashlib.sha256(data).hexdigest())
        atomic(self.w.path(copy), data)
        put_json(self.w.path(folder / 'record.json'), record)
        return record

    def commit_draft(self, jid, copy_id, lease_token=None):
        self.guard(jid, lease_token)
        meta = self.w.path(Path('.workspace/edits', jid, identifier(copy_id), 'record.json'))
        record = load(meta)
        if 'committed' in record:
            return record['committed']
        data = self.w.path(record['path']).read_bytes()
        target = self.w.path(record['source'])
        incoming = hashlib.sha256(data).hexdigest()
        existing = next((a for a in self.jobs.get(jid)['artifacts']
                         if a['path'] == record['source'] and a['hash'] == incoming), None)
        if existing and target.is_file() and digest(target) == incoming:
            result = existing
        else:
            result = Artifacts(self.w).save(jid, record['source'], data, record['base_hash'],
                                            lease_token=lease_token)
        put_json(meta, {**record, 'committed': result})
        return result

    def request_publication(self, jid, project_id, name, target_name, reason, lease_token=None):
        self.guard(jid, lease_token)
        source = self.draft(jid, project_id, name)
        if not source.is_file() or not reason.strip():
            raise Error('INVALID_ARGUMENT', 'A saved draft and a reason are required')
        if not target_name or Path(target_name).name != target_name or target_name in ('.', '..'):
            raise Error('INVALID_ARGUMENT', 'Invalid deliverable name')
        folder = self.task_folder(jid, project_id)
        target = self.w.path(Path(folder['deliverables']) / target_name)
        source_hash = digest(source)
        target_hash = digest(target) if target.is_file() else None
        key = fingerprint(jid, project_id, name, source_hash, target_name, target_hash, reason)
        path = self.w.path(Path('.workspace/decisions', project_id, jid, key + '.json'))
        if path.exists():
            return load(path)
        record = dict(schema_version=1, decision_id=key, jid=jid, project_id=project_id,
                      reason=reason, source=str(source.relative_to(self.w.root)), source_hash=source_hash,
                      name=target_name, expected_hash=target_hash, state='pending', created_at=now())
        put_json(path, record)
        return record

    def list_decisions(self, project_id, jid=None, state=None, limit=20, offset=0):
        self.w.project_for(project_id)
        directory = Path('.workspace/decisions', project_id)
        if jid:
            self.task_folder(jid, project_id)
            directory /= jid
        found = [load(self.w.path(p.relative_to(self.w.root))) for p in
                 sorted(self.w.path(directory).rglob('*.json'))]
        return page([r for r in found if not state or r['state'] == state], limit, offset)

    def resolve_publication(self, jid, project_id, decision_id, approve, source_hash, note='', lease_token=None):
        self.guard(jid, lease_token)
        folder = self.task_folder(jid, project_id)
        path = self.w.path(Path('.workspace/decisions', project_id, jid, identifier(decision_id) + '.json'))
        record = load(path)
        if type(approve) is not bool or source_hash != record['source_hash']:
            raise Error('CONFLICT', 'Decision must identify the reviewed version')
        outcome = 'published' if approve else 'returned'
        if record['state'] == outcome:
            return record
        if record['state'] != 'pending':
            raise Error('INVALID_STATE', 'Decision already resolved')
        if approve:
            source = self.w.path(record['source'])
            if not source.is_file() or digest(source) != source_hash:
                raise Error('CONFLICT', 'Draft changed; request review of the new version')
            target = str(Path(folder['deliverables']) / record['name'])
            # Recover if publishing completed immediately before interruption.
            already_saved = record.get('publishing') and any(
                a['path'] == target and a['hash'] == source_hash for a in self.jobs.get(jid)['artifacts'])
            if not already_saved or not self.w.path(target).is_file() or digest(self.w.path(target)) != source_hash:
                put_json(path, {**record, 'publishing': True})
                self.files.publish(jid, project_id, record['source'], source_hash, record['name'],
                                   record['expected_hash'], True, lease_token)
        record.update(state=outcome, note=note, resolved_at=now())
        record.pop('publishing', None)
        put_json(path, record)
        return record
