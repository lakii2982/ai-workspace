"""Claude-style project memory: one durable fact per file, plus a readable index.

Layout inside a project:

    记忆/MEMORY.md      index: `- [Title](条目/name.md) — description`
    记忆/条目/<name>.md one memory with frontmatter (name/description/metadata.type)
    记忆/TIMELINE.md    append-only task events (owned by timeline.py)

Files are authoritative. MEMORY.md is derived and rewritten on every change, so
manual edits to the index cannot silently change what agents recall.
"""
from __future__ import annotations

import hashlib
import re
from .core import Error, atomic, now

TYPES = ('user', 'feedback', 'project', 'reference')
TYPE_NOTES = {
    'user': '用户是谁：角色、专业、长期偏好',
    'feedback': '用户对工作方式的指导：纠正与已确认的做法',
    'project': '进行中的工作、目标或约束，不能从资料或 Git 推导',
    'reference': '外部资源指针：URL、看板、工单',
}
REQUIRED_SECTIONS = {'feedback': ('**Why:**', '**How to apply:**'),
                     'project': ('**Why:**', '**How to apply:**')}
NAME = re.compile('[a-z0-9\\u4e00-\\u9fff]+(?:-[a-z0-9\\u4e00-\\u9fff]+)*\\Z')
LINK = re.compile(r'\[\[([^\[\]\n]{1,64})\]\]')
INDEX_LINE = re.compile(r'^- \[([^\]]*)\]\(条目/([^)]+)\.md\) — (.*)$')
MAX_NAME = 64
MAX_DESCRIPTION = 200
MAX_BODY = 20000
MAX_SOURCE = 500
MEMORY_PATH = '记忆/MEMORY.md'
ENTRIES_PATH = '记忆/条目'


def _valid_name(name):
    return (isinstance(name, str) and 1 <= len(name) <= MAX_NAME
            and bool(NAME.fullmatch(name)))


def _single_line(value, field, limit):
    if not isinstance(value, str) or not value.strip():
        raise Error('INVALID_ARGUMENT', f'{field} must be non-empty text')
    value = value.strip()
    if '\n' in value or '\r' in value:
        raise Error('INVALID_ARGUMENT', f'{field} must be one line')
    if len(value) > limit:
        raise Error('INVALID_ARGUMENT', f'{field} exceeds {limit} characters')
    return value


def _pair(line):
    key, sep, value = line.partition(':')
    if not sep or not key.strip():
        raise Error('INVALID_DATA', 'Frontmatter lines must be key: value')
    return key.strip(), value.strip()


def parse_memory(raw, name):
    """Parse one memory file. Raises Error for anything an agent must review."""
    text = raw.replace('\r\n', '\n').replace('\r', '\n')
    lines = text.split('\n')
    if not lines or lines[0].strip() != '---':
        raise Error('INVALID_DATA', 'Missing frontmatter block')
    try:
        end = lines.index('---', 1)
    except ValueError:
        raise Error('INVALID_DATA', 'Unterminated frontmatter block') from None
    fields, section = {}, None
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if line.startswith((' ', '\t')):
            if section is None:
                raise Error('INVALID_DATA', 'Indented value without a parent key')
            key, value = _pair(line.strip())
            fields[section].setdefault(key, value)
            continue
        key, value = _pair(line)
        if value:
            section, fields[key] = None, value
        else:
            section, fields[key] = key, {}
    stored = fields.get('name')
    if stored != name:
        raise Error('INVALID_DATA', f'name must match the filename: {name}')
    description = fields.get('description')
    if not isinstance(description, str) or not description.strip():
        raise Error('INVALID_DATA', 'description is required')
    if len(description) > MAX_DESCRIPTION:
        raise Error('INVALID_DATA', f'description exceeds {MAX_DESCRIPTION} characters')
    metadata = fields.get('metadata')
    if not isinstance(metadata, dict) or metadata.get('type') not in TYPES:
        raise Error('INVALID_DATA', 'metadata.type must be one of ' + ', '.join(TYPES))
    for key in ('created', 'updated', 'source'):
        if key in metadata and not isinstance(metadata[key], str):
            raise Error('INVALID_DATA', f'metadata.{key} must be text')
    body = '\n'.join(lines[end + 1:]).strip('\n')
    if not body.strip():
        raise Error('INVALID_DATA', 'Memory body is empty')
    heading = next((m.group(1).strip() for line in body.splitlines()
                    if (m := re.fullmatch(r'#\s+(.+)', line.strip()))), None)
    return dict(name=name, title=heading or name, description=description,
                type=metadata['type'], created=metadata.get('created', ''),
                updated=metadata.get('updated', ''), source=metadata.get('source', ''),
                content=body, links=list(dict.fromkeys(LINK.findall(body))))


def render(name, description, kind, content, created, updated, source=''):
    lines = ['---', f'name: {name}', f'description: {description}', 'metadata:',
             f'  type: {kind}', f'  created: {created}', f'  updated: {updated}']
    if source:
        lines.append(f'  source: {source}')
    lines += ['---', '', content.strip('\n'), '']
    return '\n'.join(lines)


def _index_title(title):
    return title.replace('[', '（').replace(']', '）')


class Memories:
    def __init__(self, w):
        self.w = w

    # -- locations -------------------------------------------------------
    def _paths(self, project_id):
        project = self.w.project_for(project_id)
        return project, project['path'] + '/' + MEMORY_PATH, project['path'] + '/' + ENTRIES_PATH

    def _scan(self, project_id):
        """Read every memory file, isolating unreadable ones instead of failing recall."""
        project, index_rel, entries_rel = self._paths(project_id)
        folder = self.w.path(entries_rel)
        index_path = self.w.path(index_rel)
        entries, warnings = [], []
        if folder.is_dir():
            for path in sorted(folder.glob('*.md')):
                relative = entries_rel + '/' + path.name
                if path.is_symlink() or not path.is_file():
                    warnings.append(dict(path=relative, state='needs_review', reason='Not a regular file'))
                    continue
                raw = path.read_bytes()
                try:
                    item = parse_memory(raw.decode('utf-8'), path.name[:-3])
                except UnicodeDecodeError:
                    warnings.append(dict(path=relative, state='needs_review', reason='Not UTF-8'))
                    continue
                except Error as error:
                    warnings.append(dict(path=relative, state='needs_review', reason=error.message))
                    continue
                entries.append({**item, 'path': relative,
                                'hash': hashlib.sha256(raw).hexdigest()})
        index_text = None
        previous = []
        if index_path.exists():
            index_text = index_path.read_text(encoding='utf-8')
            previous = [m.group(2) for m in map(INDEX_LINE.match, index_text.splitlines()) if m]
        known = {entry['name'] for entry in entries}
        order = [name for name in previous if name in known]
        order += sorted(known - set(order))
        by_name = {entry['name']: entry for entry in entries}
        ordered = [by_name[name] for name in order]
        indexed = set(previous)
        drift = dict(in_sync=index_path.exists(),
                     missing=[name for name in previous if name not in known],
                     untracked=[name for name in sorted(known) if name not in indexed])
        drift['in_sync'] = (index_path.exists() and not drift['missing']
                            and not drift['untracked']
                            and index_text == self.index_text(project, ordered))
        return project, index_rel, entries_rel, ordered, warnings, index_text, drift

    @staticmethod
    def index_text(project, entries):
        name = project.get('name') or project['project_id']
        lines = [f'# {name} 项目记忆', '',
                 '每条记忆一个文件，位于 `记忆/条目/`；本索引由核心根据文件生成。', '']
        for entry in entries:
            lines.append(f'- [{_index_title(entry["title"])}](条目/{entry["name"]}.md) — {entry["description"]}')
        return '\n'.join(lines).rstrip('\n') + '\n'

    def _write_index(self, project_id, project, entries):
        relative = self._paths(project_id)[1]
        text = self.index_text(project, entries)
        target = self.w.path(relative)
        atomic(target, text.encode('utf-8'))
        return text, hashlib.sha256(text.encode('utf-8')).hexdigest()

    @staticmethod
    def _view(entry, detail):
        view = {key: entry[key] for key in
                ('name', 'title', 'description', 'type', 'created', 'updated',
                 'source', 'path', 'hash', 'links')}
        if detail == 'full':
            view['content'] = entry['content']
        return view

    @staticmethod
    def _terms(query):
        if not isinstance(query, str):
            raise Error('INVALID_ARGUMENT', 'query must be text')
        if len(query) >= 2 and query[0] == query[-1] and query[0] in ('"', "'"):
            return [query[1:-1].casefold()]
        return query.casefold().split() or ['']

    @staticmethod
    def _matches(entry, terms):
        text = ' '.join([entry['name'], entry['title'], entry['description'],
                         entry['source'], entry['content']]).casefold()
        return any(term in text for term in terms)

    # -- operations ------------------------------------------------------
    def list(self, project_id, query='', types=None, detail='index', limit=20, offset=0):
        if detail not in ('index', 'full'):
            raise Error('INVALID_ARGUMENT', 'detail must be index or full')
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise Error('INVALID_ARGUMENT', 'Invalid pagination')
        if types is not None and (not isinstance(types, list) or not set(types) <= set(TYPES)):
            raise Error('INVALID_ARGUMENT', 'types must be a subset of ' + ', '.join(TYPES))
        terms = self._terms(query)
        project, index_rel, _, entries, warnings, index_text, drift = self._scan(project_id)
        rows = [entry for entry in entries
                if (not types or entry['type'] in types) and self._matches(entry, terms)]
        counts = {kind: sum(1 for entry in entries if entry['type'] == kind) for kind in TYPES}
        known = {entry['name'] for entry in entries}
        return {
            'project_id': project_id,
            'index_path': index_rel,
            'index_text': index_text if index_text is not None else self.index_text(project, entries),
            'index_initialized': index_text is not None,
            'index_drift': drift,
            'items': [self._view(entry, detail) for entry in rows[offset:offset + limit]],
            'total': len(rows), 'has_more': len(rows) > offset + limit,
            'next_offset': offset + limit if len(rows) > offset + limit else None,
            'counts': {kind: counts[kind] for kind in TYPES},
            'warnings': warnings,
            'broken_links': [dict(name=entry['name'], link=link)
                             for entry in entries for link in entry['links'] if link not in known],
            'types': {kind: TYPE_NOTES[kind] for kind in TYPES},
            'expand': dict(operation='list_memory_notes', project_id=project_id, detail='full'),
        }

    def read(self, project_id, name):
        if not _valid_name(name):
            raise Error('INVALID_ARGUMENT', 'Invalid memory name')
        _, index_rel, _, entries, warnings, _, _ = self._scan(project_id)
        by_name = {entry['name']: entry for entry in entries}
        if name not in by_name:
            raise Error('NOT_FOUND', 'Memory not found; list_memory_notes shows the current index')
        entry = by_name[name]
        return {
            **self._view(entry, 'full'),
            'project_id': project_id,
            'index_path': index_rel,
            'links': [dict(name=link, exists=link in by_name,
                           description=by_name[link]['description'] if link in by_name else None)
                      for link in entry['links']],
            'backlinks': [dict(name=other['name'], description=other['description'])
                          for other in entries if name in other['links'] and other['name'] != name],
            'warnings': warnings,
        }

    def save(self, project_id, name, description, type, content,
             source=None, expected_hash=None):
        if not _valid_name(name):
            raise Error('INVALID_ARGUMENT',
                        'name must be a slug of lowercase letters, digits, hyphens or CJK (max 64)')
        description = _single_line(description, 'description', MAX_DESCRIPTION)
        kind = type
        if not isinstance(kind, str) or kind not in TYPES:
            raise Error('INVALID_ARGUMENT', 'type must be one of ' + ', '.join(TYPES))
        if not isinstance(content, str) or not content.strip():
            raise Error('INVALID_ARGUMENT', 'content must be non-empty text')
        content = content.replace('\r\n', '\n').replace('\r', '\n')
        if len(content) > MAX_BODY:
            raise Error('INVALID_ARGUMENT', f'content exceeds {MAX_BODY} characters')
        if '\x00' in content:
            raise Error('INVALID_ARGUMENT', 'content contains a null byte')
        for marker in REQUIRED_SECTIONS.get(kind, ()):
            if marker not in content:
                raise Error('INVALID_ARGUMENT',
                            f'{kind} memory must state {marker.strip("*")} in its content')
        if source is not None:
            source = _single_line(source, 'source', MAX_SOURCE)
        if expected_hash is not None and not isinstance(expected_hash, str):
            raise Error('INVALID_ARGUMENT', 'expected_hash must be text')

        project, _, entries_rel = self._paths(project_id)
        relative = entries_rel + '/' + name + '.md'
        target = self.w.path(relative)
        with self.w.lock('memory:' + project_id):
            exists = target.is_file()
            actual = hashlib.sha256(target.read_bytes()).hexdigest() if exists else None
            if expected_hash != actual:
                raise Error('CONFLICT', 'Memory changed; read it again and retry with the current hash')
            created = now()
            previous = None
            if exists:
                try:
                    previous = parse_memory(target.read_text(encoding='utf-8'), name)
                except (Error, UnicodeDecodeError):
                    previous = None
                if previous:
                    created = previous['created'] or created
            updated = created if not previous else now()
            if previous and (previous['description'] == description
                             and previous['type'] == kind and previous['content'] == content.strip('\n')
                             and previous['source'] == (source or '')):
                # Replaying the same write still repairs a hand-edited index.
                _, _, _, entries, _, _, _ = self._scan(project_id)
                _, index_hash = self._write_index(project_id, project, entries)
                result = self.read(project_id, name)
                return {**result, 'replayed': True, 'created_memory': False,
                        'index_hash': index_hash}
            atomic(target, render(name, description, kind, content, created, updated,
                                  source or '').encode('utf-8'))
            _, _, _, entries, _, _, _ = self._scan(project_id)
            _, index_hash = self._write_index(project_id, project, entries)
        result = self.read(project_id, name)
        return {**result, 'replayed': False, 'created_memory': not exists,
                'index_hash': index_hash}

    def delete(self, project_id, name, expected_hash=None):
        if not _valid_name(name):
            raise Error('INVALID_ARGUMENT', 'Invalid memory name')
        if expected_hash is not None and not isinstance(expected_hash, str):
            raise Error('INVALID_ARGUMENT', 'expected_hash must be text')
        project, _, entries_rel = self._paths(project_id)
        relative = entries_rel + '/' + name + '.md'
        target = self.w.path(relative)
        if not target.is_file():
            raise Error('NOT_FOUND', 'Memory not found; list_memory_notes shows the current index')
        with self.w.lock('memory:' + project_id):
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            if expected_hash != actual:
                raise Error('CONFLICT', 'Memory changed; read it again and retry with the current hash')
            _, _, _, entries, _, _, _ = self._scan(project_id)
            backlinks = [entry['name'] for entry in entries
                         if name in entry['links'] and entry['name'] != name]
            target.unlink()
            _, _, _, remaining, _, _, _ = self._scan(project_id)
            _, index_hash = self._write_index(project_id, project, remaining)
        return {'project_id': project_id, 'name': name, 'path': relative,
                'deleted_hash': actual, 'backlinks': backlinks, 'index_hash': index_hash}

    def overview(self, project_id, detail='brief', limit=50):
        """Compact index for prepare_work_context and get_project_overview.

        Only what can be recalled is returned: an empty project costs a few bytes,
        and warnings or index drift appear only when an agent must act on them.
        """
        _, index_rel, _, entries, warnings, index_text, drift = self._scan(project_id)
        project = self.w.project_for(project_id)
        items = [self._view(entry, detail) for entry in entries[:limit]]
        result = {'index_path': index_rel, 'items': items, 'total': len(entries)}
        if entries:
            counts = {kind: sum(1 for entry in entries if entry['type'] == kind) for kind in TYPES}
            result.update(
                index_text=index_text if index_text is not None else self.index_text(project, entries),
                counts=counts,
                expand=dict(operation='list_memory_notes', project_id=project_id, detail='full'))
            if len(entries) > limit:
                result['omitted'] = len(entries) - limit
        if warnings:
            result['warnings'] = warnings
        if drift['missing'] or drift['untracked'] or (index_text is not None and not drift['in_sync']):
            result['index_drift'] = drift
        return result
