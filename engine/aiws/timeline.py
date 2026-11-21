"""Project-local events and current project memory, with one readable authority."""
import hashlib
import json
import re
from .core import Error, atomic, digest

CATEGORIES = {'decision', 'constraint', 'conclusion', 'experience', 'hypothesis', 'question'}
STATES = {'established', 'tentative', 'resolved', 'withdrawn'}
BLOCK = re.compile(r'<!-- event:([\w-]+) hash:([a-f0-9]+) -->\n<!-- aiws-memory:(.*?) -->\n(.*?)<!-- /aiws-event -->', re.S)


class Timeline:
    def __init__(self, w):
        self.w = w

    def _path(self, project_id):
        project = self.w.project_for(project_id)
        relative = project['path'] + '/记忆/TIMELINE.md'
        return relative, self.w.path(relative)

    def append(self, project_id, event_id, event_date, record_date, kind, summary,
               status, impact, owner, next_action, sources, expected_hash=None, memory=None):
        if kind not in ('fact', 'plan', 'recommendation', 'unconfirmed', 'decision'):
            raise Error('INVALID_ARGUMENT', 'Invalid event kind')
        values = [event_id, event_date, record_date, summary, status, impact, owner, next_action]
        if any(not isinstance(v, str) or not v.strip() for v in values):
            raise Error('INVALID_ARGUMENT', 'All event fields required')
        if not event_id.isascii() or not all(c.isalnum() or c in '-_' for c in event_id):
            raise Error('INVALID_ARGUMENT', 'Invalid event ID')
        if not isinstance(sources, list) or not sources or any(not isinstance(s, str) for s in sources):
            raise Error('INVALID_ARGUMENT', 'Evidence required')
        if memory is not None:
            if not isinstance(memory, dict) or set(memory) - {'category', 'state', 'reason', 'supersedes'}:
                raise Error('INVALID_ARGUMENT', 'Invalid memory fields')
            if memory.get('category') not in CATEGORIES or memory.get('state') not in STATES:
                raise Error('INVALID_ARGUMENT', 'Invalid memory category/state')
            if not isinstance(memory.get('reason', ''), str):
                raise Error('INVALID_ARGUMENT', 'Memory reason must be text')
            refs = memory.get('supersedes', [])
            if not isinstance(refs, list) or any(not isinstance(x, str) or x == event_id for x in refs):
                raise Error('INVALID_ARGUMENT', 'Invalid supersedes')
            if memory['state'] in ('resolved', 'withdrawn') and not refs:
                raise Error('INVALID_ARGUMENT', 'Resolved/withdrawn memory must reference an event')
            memory = {**memory, 'reason': memory.get('reason', ''), 'supersedes': refs}
        relative, path = self._path(project_id)
        body = (f'## {event_date} | {summary}\n\n- 类型：{kind}；状态：{status}\n'
                f'- 记录日期：{record_date}\n- 影响：{impact}\n- 责任人：{owner}\n'
                f'- 后续：{next_action}\n- 来源：' + '; '.join(sources) + '\n')
        if memory:
            body += f'- 记忆：{memory["category"]}；性质：{memory["state"]}\n- 理由：{memory["reason"]}\n'
            if memory['supersedes']:
                body += '- 替代/解决：' + ', '.join(memory['supersedes']) + '\n'
        fingerprint = hashlib.sha256(body.encode()).hexdigest()
        marker = f'<!-- event:{event_id} hash:{fingerprint} -->'
        chunk = marker + '\n'
        if memory is not None:
            data = dict(format_version=1, event_id=event_id, event_date=event_date,
                        record_date=record_date, kind=kind, summary=summary, status=status,
                        impact=impact, owner=owner, next_action=next_action, sources=sources,
                        memory=memory, body=body)
            # Escape comment terminators in supplied prose without changing decoded JSON.
            encoded = json.dumps(data, ensure_ascii=False).replace('-->', '\\u002d\\u002d>')
            chunk += '<!-- aiws-memory:' + encoded + ' -->\n'
        chunk += body
        if memory is not None:
            chunk += '<!-- /aiws-event -->\n'
        with self.w.lock('file:' + relative):
            old = path.read_text() if path.exists() else '# Project timeline\n'
            if f'<!-- event:{event_id} ' in old:
                if chunk.strip() not in old:
                    raise Error('CONFLICT', 'Event ID reused or its recorded content changed')
                return {'path': relative, 'hash': digest(path), 'replayed': True}
            if (digest(path) if path.exists() else None) != expected_hash:
                raise Error('CONFLICT', 'Timeline changed')
            if memory and memory['supersedes']:
                known = set(re.findall(r'<!-- event:([\w-]+) ', old))
                if not set(memory['supersedes']) <= known:
                    raise Error('INVALID_ARGUMENT', 'Referenced event not in this project timeline')
            atomic(path, (old + '\n' + chunk).encode())
        return {'path': relative, 'hash': digest(path), 'replayed': False}

    def _read(self, project_id):
        relative, path = self._path(project_id)
        # An append can atomically replace the file while another job reads it.
        # Parse and fingerprint the same snapshot, not two reads of the path.
        try:
            content = path.read_bytes()
        except FileNotFoundError:
            text, sha = '', None
        else:
            text = content.decode().replace('\r\n', '\n').replace('\r', '\n')
            sha = hashlib.sha256(content).hexdigest()
        records, pending = [], []
        for match in BLOCK.finditer(text):
            line = text.count('\n', 0, match.start()) + 1
            location = dict(path=relative, hash=sha, locator=f'line:{line}')
            try:
                data = json.loads(match[3])
                valid = (data.get('format_version') == 1 and data.get('event_id') == match[1]
                         and data.get('body') == match[4]
                         and hashlib.sha256(match[4].encode()).hexdigest() == match[2]
                         and data['memory']['category'] in CATEGORIES and data['memory']['state'] in STATES)
            except (ValueError, KeyError, TypeError):
                data, valid = {}, False
            item = {**data, **location, 'event_id': match[1], 'current_body': match[4]}
            if valid:
                item['derived_state'] = data['memory']['state']
                item['replaced_by'] = []
                records.append(item)
            else:
                item['derived_state'] = 'needs_review'
                pending.append(item)
        # Current meaning is explicit, never inferred from latest timestamp.
        by_id = {r['event_id']: r for r in records}
        for record in records:
            state = record['memory']['state']
            if state not in ('established', 'resolved', 'withdrawn'):
                continue
            for target in record['memory'].get('supersedes', []):
                if target in by_id:
                    old = by_id[target]
                    old['derived_state'] = {'established': 'superseded', 'resolved': 'resolved', 'withdrawn': 'withdrawn'}[state]
                    old['replaced_by'].append(record['event_id'])
        # Editing a replacing event is not an explicit decision to revive its predecessor.
        # Keep those predecessor conclusions suspended until the edited record is reviewed.
        suspended = []
        for record in pending:
            memory = record.get('memory')
            if not isinstance(memory, dict) or memory.get('state') not in ('established', 'resolved', 'withdrawn'):
                continue
            refs = memory.get('supersedes', [])
            if not isinstance(refs, list):
                continue
            for target in refs:
                if isinstance(target, str) and target in by_id:
                    old = by_id[target]
                    old['derived_state'] = 'needs_review'
                    old['replaced_by'].append(record['event_id'])
                    suspended.append({**old, 'review_reason': 'Replacing event was edited and needs review'})
        pending.extend(suspended)
        legacy_text = BLOCK.sub('', text).strip()
        legacy = []
        meaningful = []
        for number, line in enumerate(legacy_text.splitlines()):
            stripped = line.strip()
            if not stripped:
                continue
            if number == 0 and stripped.startswith('# '):
                continue
            if re.fullmatch(r'项目编号[：:]\s*PRJ-\d+', stripped):
                continue
            meaningful.append(stripped)
        if meaningful:
            legacy = [dict(path=relative, hash=sha, locator='line:1', state='unstructured', text=legacy_text)]
        return records, pending, legacy

    def list_memory(self, project_id, query='', categories=None, view='current', limit=20, offset=0):
        if view not in ('current', 'history') or type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise Error('INVALID_ARGUMENT', 'Invalid view/pagination')
        if not isinstance(query, str) or (categories is not None and (not isinstance(categories, list) or not set(categories) <= CATEGORIES)):
            raise Error('INVALID_ARGUMENT', 'Invalid memory query/categories')
        records, pending, legacy = self._read(project_id)
        tentative = [r for r in records if r['derived_state'] == 'tentative']
        # Space-separated search terms are alternatives. Quoted text explicitly requests
        # an exact phrase; a single unspaced Chinese term retains literal matching.
        if len(query) >= 2 and query[0] == query[-1] and query[0] in ('\"', "'"):
            terms = [query[1:-1].casefold()]
        else:
            terms = query.casefold().split() or ['']
        def matches(r):
            text = (r['summary'] + ' ' + r['memory'].get('reason', '') + ' ' + r['current_body']).casefold()
            return (not categories or r['memory']['category'] in categories) and any(term in text for term in terms)
        rows = [r for r in records if matches(r) and (view == 'history' or r['derived_state'] == 'established' or (r['memory']['category'] == 'question' and r['derived_state'] == 'tentative'))]
        rows.reverse()
        return dict(items=rows[offset:offset + limit], has_more=len(rows) > offset + limit,
                    next_offset=offset + limit if len(rows) > offset + limit else None,
                    total=len(rows), tentative=[r for r in tentative if matches(r)], legacy=legacy,
                    pending_review=pending)

    def read_memory(self, project_id, event_id, include_history=True):
        records, pending, legacy = self._read(project_id)
        by_id = {r['event_id']: r for r in records + pending}
        if event_id not in by_id:
            raise Error('NOT_FOUND', 'Structured memory event not found; inspect legacy entries')
        item = dict(by_id[event_id])
        if include_history:
            visited, queue = {event_id}, [event_id]
            history = []
            while queue:
                current = by_id[queue.pop()]
                for related in current.get('memory', {}).get('supersedes', []) + current.get('replaced_by', []):
                    if related in by_id and related not in visited:
                        visited.add(related); queue.append(related); history.append(by_id[related])
            item['history'] = history
        return item
