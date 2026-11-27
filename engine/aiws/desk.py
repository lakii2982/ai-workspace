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

    def record_host_binding(self, client, version):
        if not isinstance(client, str) or not client or not isinstance(version, str):
            raise Error('INVALID_ARGUMENT', 'Missing MCP client identity')
        record = dict(schema_version=1, client=client[:160], version=version[:100], seen_at=now())
        path = self.w.path(Path('.workspace/host-bindings', fingerprint(client) + '.json'))
        put_json(path, record)
        return {**record, 'workspace': str(self.w.root)}

    def list_host_bindings(self):
        folder = self.w.path('.workspace/host-bindings')
        return {'workspace': str(self.w.root), 'items': [
            load(self.w.path(p.relative_to(self.w.root))) for p in sorted(folder.glob('*.json'))]}

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

    def guard(self, jid, lease_token, allow_closed=False):
        job = self.jobs.get(jid)
        if job['status'] in ('completed', 'cancelled'):
            if allow_closed:
                return
            raise Error('INVALID_STATE', 'Closed task')
        check_lease(job, lease_token, require=enabled(self.w))

    def checkout_draft(self, jid, project_id, name, lease_token=None, create=False):
        self.guard(jid, lease_token)
        source = self.draft(jid, project_id, name)
        exists = source.is_file()
        if create and exists:
            raise Error('CONFLICT', 'Draft already exists')
        if not exists and not create:
            raise Error('NOT_FOUND', 'Draft does not exist')
        data = source.read_bytes() if exists else b''
        copy_id = uuid.uuid4().hex
        folder = Path('.workspace/edits', jid, copy_id)
        copy = folder / 'content' / name
        record = dict(schema_version=1, copy_id=copy_id, source=str(source.relative_to(self.w.root)),
                      path=str(copy), base_hash=hashlib.sha256(data).hexdigest() if exists else None)
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
        matches_copy = target.is_file() and digest(target) == incoming
        if existing and matches_copy:
            result = existing
        else:
            # A previous save may have replaced the file before persisting
            # its job manifest. Compare the identical bytes again under lock.
            expected = incoming if matches_copy else record['base_hash']
            result = Artifacts(self.w).save(jid, record['source'], data, expected, lease_token=lease_token)
        put_json(meta, {**record, 'committed': result})
        return result

    def request_publication(self, jid, project_id, name, target_name, reason, lease_token=None):
        self.guard(jid, lease_token, allow_closed=True)
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
        found = [record for record in found if not state or record['state'] == state]
        found.sort(key=lambda record: record.get('created_at', ''), reverse=True)
        found.sort(key=lambda record: record['state'] != 'pending')
        return {**page(found,limit,offset),'total':len(found),
                'pending_total':sum(record['state']=='pending' for record in found)}

    def record_sent(self, jid, project_id, name, source_hash, channel='', evidence_path=None,
                    evidence_hash=None, lease_token=None):
        """Record a user's already-sent deliverable without contacting the channel."""
        self.guard(jid, lease_token, allow_closed=True)
        if not all(isinstance(value, str) and value.strip() for value in (name, source_hash)):
            raise Error('INVALID_ARGUMENT', 'Deliverable version is required')
        if not isinstance(channel, str) or bool(evidence_path) != bool(evidence_hash):
            raise Error('INVALID_ARGUMENT', 'Channel evidence path and hash must be supplied together')
        folder = self.task_folder(jid, project_id)
        if Path(name).name != name or name in ('.', '..'):
            raise Error('INVALID_ARGUMENT', 'Invalid deliverable name')
        path = str(Path(folder['deliverables']) / name)
        artifact = next((item for item in self.jobs.get(jid)['artifacts']
                         if item['path'] == path and item['hash'] == source_hash
                         and item['purpose'] == 'deliverable' and item['status'] == 'approved'), None)
        if not re.fullmatch(r'[0-9a-f]{64}', source_hash):
            raise Error('INVALID_ARGUMENT', 'Expected the published SHA-256')
        if artifact is None:
            # A customer may report a send after another version was published.
            decisions=self.list_decisions(project_id, jid, state='published', limit=100)
            while not any(r['name']==name and r['source_hash']==source_hash for r in decisions['items']):
                if not decisions['has_more']:
                    raise Error('NOT_FOUND', 'Published deliverable version not found')
                decisions=self.list_decisions(project_id,jid,state='published',limit=100,offset=decisions['next_offset'])
        if self.published_version(jid,project_id,name,source_hash)['state']=='unavailable':
            raise Error('NOT_FOUND', 'Published deliverable version is unavailable')
        evidence = dict(kind='user_statement')
        if evidence_path:
            self.w.allowed(evidence_path, self.jobs.get(jid)['scope'])
            evidence_file = self.w.path(evidence_path)
            if not evidence_file.is_file() or digest(evidence_file) != evidence_hash:
                raise Error('CONFLICT', 'Channel receipt changed or is missing')
            evidence = dict(kind='channel_receipt', path=evidence_path, hash=evidence_hash)
        key = fingerprint(jid, project_id, name, source_hash, channel.strip(), evidence)
        record = dict(schema_version=1, sent_id=key, jid=jid, project_id=project_id, name=name,
                      source_hash=source_hash, channel=channel.strip() or None, evidence=evidence,
                      recorded_at=now(), status='reported_sent')
        destination = self.w.path(Path('.workspace/sent', project_id, jid, key + '.json'))
        if destination.exists():
            return load(destination)
        put_json(destination, record)
        return record

    def sent_version(self, record):
        version=self.published_version(record['jid'],record['project_id'],record['name'],record['source_hash'])
        if version['state']=='archived':
            version['read']=dict(operation='read_sent_version',project_id=record['project_id'],sent_id=record['sent_id'])
        return version

    def published_version(self,jid,project_id,name,source_hash):
        folder=self.task_folder(jid,project_id)
        current=self.w.path(Path(folder['deliverables'])/name)
        named=current.parent/'历史版本'/(current.stem+'_'+source_hash[:12]+current.suffix)
        for state,path in [('current',current),('archived',named),('archived',self.w.path(Path('.workspace/history/files')/source_hash))]:
            if path.is_file() and digest(path)==source_hash:
                return dict(state=state,path=str(path.relative_to(self.w.root)),hash=source_hash)
        return dict(state='unavailable',hash=source_hash)

    def list_sent(self, project_id, jid=None, name=None, source_hash=None, limit=20, offset=0):
        self.w.project_for(project_id)
        folder = Path('.workspace/sent', project_id)
        if jid:
            self.task_folder(jid, project_id)
            folder /= jid
        records = [load(self.w.path(path.relative_to(self.w.root)))
                   for path in sorted(self.w.path(folder).rglob('*.json'))]
        records = [record for record in records
                   if (not name or record['name'] == name)
                   and (not source_hash or record['source_hash'] == source_hash)]
        records.sort(key=lambda record: record['recorded_at'], reverse=True)
        result=page(records,limit,offset)
        result['items']=[{**record,'version':self.sent_version(record)} for record in result['items']]
        return result

    def read_sent_version(self, project_id, sent_id):
        record = next((item for item in self.list_sent(project_id, limit=100)['items']
                       if item['sent_id'] == identifier(sent_id)), None)
        if record is None:
            raise Error('NOT_FOUND', 'Sent record not found')
        version = record['version']
        if version['state'] == 'unavailable':
            raise Error('NOT_FOUND', 'Recorded version is unavailable')
        content=self.w.path(version['path']).read_bytes()
        if hashlib.sha256(content).hexdigest()!=version['hash']:
            raise Error('CONFLICT','File version changed during read')
        return {**version, 'content_hex':content.hex(), 'encoding':'hex'}

    def resolve_publication(self, jid, project_id, decision_id, approve, source_hash, note='', lease_token=None):
        self.guard(jid, lease_token, allow_closed=True)
        folder = self.task_folder(jid, project_id)
        path = self.w.path(Path('.workspace/decisions', project_id, jid, identifier(decision_id) + '.json'))
        record = load(path)
        if type(approve) is not bool or source_hash != record['source_hash']:
            raise Error('CONFLICT', 'Decision must identify the reviewed version')
        outcome = 'published' if approve else 'returned'
        if record['state'] == outcome:
            if approve:return {**record,'version':self.published_version(jid,project_id,record['name'],source_hash)}
            return record
        if record['state'] != 'pending':
            raise Error('INVALID_STATE', 'Decision already resolved')
        if approve:
            source = self.w.path(record['source'])
            if not source.is_file() or digest(source) != source_hash:
                put_json(path, {**record, 'state': 'superseded', 'resolved_at': now()})
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
        if approve:return {**record,'version':self.published_version(jid,project_id,record['name'],source_hash)}
        return record
