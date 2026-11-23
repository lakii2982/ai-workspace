"""Personal reusable instructions with immutable revisions and task bindings."""
from pathlib import Path

from .core import Error, load, now, put_json
from .desk import Desktop, fingerprint, identifier, page


class Scenarios(Desktop):
    def location(self, scenario_id):
        return self.w.path(Path('.workspace/scenarios', identifier(scenario_id) + '.json'))

    def save_scenario(self, jid, title, instructions, reason, confirmed=False, expected_version=0,
                      scenario_id=None, lease_token=None):
        self.guard(jid, lease_token)
        if not all(isinstance(s, str) and s.strip() for s in (title, instructions, reason)):
            raise Error('INVALID_ARGUMENT', 'Supply title, reusable instructions and change reason')
        if type(expected_version) is not int or expected_version < 0:
            raise Error('INVALID_ARGUMENT', 'Invalid version')
        key = scenario_id or fingerprint(jid, title)
        path = self.location(key)
        with self.w.lock('scenario:' + key):
            record = load(path) if path.exists() else dict(schema_version=1, scenario_id=key, versions=[])
            versions = record['versions']
            if expected_version != len(versions):
                raise Error('CONFLICT', 'Read the current version before editing')
            if versions and confirmed is not True:
                raise Error('CONFIRMATION_REQUIRED', 'Confirm a permanent rule change')
            revision = dict(version=len(versions) + 1, title=title, instructions=instructions,
                            reason=reason, source_job=jid, created_at=now())
            versions.append(revision)
            put_json(path, record)
            return {'scenario_id': key, **revision}

    def read_scenario(self, scenario_id, version=None):
        versions = load(self.location(scenario_id))['versions']
        if version is None:
            version = len(versions)
        if type(version) is not int or not 1 <= version <= len(versions):
            raise Error('NOT_FOUND', 'Unknown scenario version')
        return {'scenario_id': scenario_id, **versions[version - 1]}

    def list_scenarios(self, limit=20, offset=0):
        items = []
        for p in sorted(self.w.path('.workspace/scenarios').glob('*.json')):
            rev = self.read_scenario(p.stem)
            items.append({k: rev[k] for k in ('scenario_id', 'version', 'title')})
        return page(items, limit, offset)

    def apply_scenario(self, jid, scenario_id, version=None, lease_token=None):
        self.guard(jid, lease_token)
        path = self.w.path(Path('.workspace/scenario-bindings', jid, identifier(scenario_id) + '.json'))
        if path.exists():
            previous = load(path)['version']
            if version is not None and version != previous:
                raise Error('CONFLICT', 'Task already uses a different revision')
            version = previous
        result = self.read_scenario(scenario_id, version)
        put_json(path, dict(schema_version=1, scenario_id=scenario_id, version=result['version']))
        return result

    def bindings(self, jid):
        folder = self.w.path(Path('.workspace/scenario-bindings', jid))
        return [load(self.w.path(p.relative_to(self.w.root))) for p in sorted(folder.glob('*.json'))]
