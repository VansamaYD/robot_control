"""Versioned declarative recipes. Does not eval/exec imported Python."""
from pathlib import Path
from copy import deepcopy
import hashlib
import json
import math
import os
import tempfile
import threading
import time
import uuid


def validate(actions):
    if not isinstance(actions, list) or not 1 <= len(actions) <= 100:
        raise ValueError('INVALID_ACTION_LIST')
    result = []
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            raise ValueError(f'INVALID_ACTION:{index}')
        kind = action.get('kind')
        fields = {'move_tcp': {'kind', 'name', 'p', 'n', 'duration_s'},
                  'move_joints': {'kind', 'name', 'q_rad', 'duration_s'},
                  'scoop': {'kind', 'name', 'target_m3', 'duration_s'},
                  'wait': {'kind', 'name', 'duration_s'}}.get(kind)
        if fields is None or set(action) != fields:
            raise ValueError(f'INVALID_ACTION_FIELDS:{index}')
        if not isinstance(action['name'], str) or not 0 < len(action['name']) <= 80:
            raise ValueError(f'INVALID_NAME:{index}')
        duration = action['duration_s']
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or not .02 <= duration <= 120:
            raise ValueError(f'INVALID_DURATION:{index}')
        if kind == 'move_tcp':
            for field in ('p', 'n'):
                v = action[field]
                if not isinstance(v, list) or len(v) != 3 or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in v):
                    raise ValueError(f'INVALID_VECTOR:{index}:{field}')
            if sum(x*x for x in action['n']) < 1e-12:
                raise ValueError(f'INVALID_NORMAL:{index}')
        if kind == 'scoop':
            v = action['target_m3']
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 < v <= 32.725e-6:
                raise ValueError(f'INVALID_SCOOP_VOLUME:{index}')
        if kind == 'move_joints':
            q = action['q_rad']
            if not isinstance(q, list) or len(q) != 5 or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in q):
                raise ValueError(f'INVALID_JOINT_VECTOR:{index}')
        result.append(deepcopy(action))
    return result


def code_view(actions):
    lines = ['# Generated code view; execution uses validated actions, never exec().', 'def main(robot):']
    for action in actions:
        if action['kind'] == 'move_tcp':
            call = f"move_tcp({action['name']!r}, {action['p']!r}, {action['n']!r}, {action['duration_s']!r})"
        elif action['kind'] == 'move_joints':
            call = f"move_joints({action['name']!r}, {action['q_rad']!r}, {action['duration_s']!r})"
        elif action['kind'] == 'scoop':
            call = f"scoop({action['name']!r}, {round(action['target_m3'] * 1e6, 6)!r}, {action['duration_s']!r})"
        elif action['kind'] == 'verify_dry':
            call = f"verify_dry({action['name']!r})"
        else:
            call = f"wait({action['name']!r}, {action['duration_s']!r})"
        lines.append('    yield robot.' + call)
    return '\n'.join(lines) + '\n'


class RevisionStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def save(self, name, actions):
        if not isinstance(name, str) or not 0 < len(name.strip()) <= 80:
            raise ValueError('INVALID_PROJECT_NAME')
        record = {'schema_version': '1.0', 'name': name.strip(), 'actions': validate(actions)}
        canonical = json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        revision = hashlib.sha256(canonical.encode()).hexdigest()
        record['revision'] = revision
        record['code'] = code_view(record['actions'])
        target = self.directory / (revision + '.json')
        if not target.exists():
            fd, tmp = tempfile.mkstemp(prefix='.saving-', dir=self.directory)
            try:
                with os.fdopen(fd, 'w') as output:
                    json.dump(record, output, ensure_ascii=False, allow_nan=False, indent=2)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(tmp, target)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        return self.get(revision)

    def get(self, revision):
        if not isinstance(revision, str) or len(revision) != 64 or any(c not in '0123456789abcdef' for c in revision):
            raise ValueError('INVALID_REVISION')
        try:
            record = json.loads((self.directory / (revision + '.json')).read_text())
        except FileNotFoundError:
            raise ValueError('REVISION_NOT_FOUND') from None
        if record.get('schema_version') != '1.0':
            raise ValueError('UNSUPPORTED_VERSION')
        canonical = json.dumps({k: record[k] for k in ['schema_version', 'name', 'actions']}, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        if record['revision'] != revision or hashlib.sha256(canonical.encode()).hexdigest() != revision:
            raise ValueError('REVISION_CORRUPT')
        validate(record['actions'])
        record['code'] = code_view(record['actions'])
        return record

    def list(self):
        result = []
        for path in self.directory.glob('*.json'):
            if len(path.stem) != 64 or any(c not in '0123456789abcdef' for c in path.stem):
                continue
            record = self.get(path.stem)
            result.append({'revision': record['revision'], 'name': record['name'], 'action_count': len(record['actions'])})
        return sorted(result, key=lambda r: (r['name'], r['revision']))


class ProjectStore(RevisionStore):
    """One visible head per project; immutable revisions remain addressable."""
    def __init__(self, directory, configuration=None):
        super().__init__(directory)
        self.configuration = configuration
        self.lock = threading.RLock()
        self.index_path = self.directory/'library.index'
        if not self.index_path.exists():
            index = {'schema_version': 1, 'projects': {}, 'results': {}}
            # Legacy same-name edits become one history, ordered by file mtime.
            groups = {}
            for path in sorted(self.directory.glob('*.json'), key=lambda p: p.stat().st_mtime_ns):
                if len(path.stem) != 64 or any(c not in '0123456789abcdef' for c in path.stem): continue
                record = RevisionStore.get(self, path.stem)
                entry = groups.setdefault(record['name'], {'project_id': 'project-'+uuid.uuid4().hex,
                    'name': record['name'], 'kind': 'custom', 'archived': False, 'history': []})
                entry['history'].append(record['revision'])
                entry['revision'] = record['revision']
            index['projects'] = {e['project_id']: e for e in groups.values()}
            self._write(index)

    def _read(self):
        index = json.loads(self.index_path.read_text())
        if index.get('schema_version') != 1 or not isinstance(index.get('projects'), dict):
            raise ValueError('LIBRARY_CORRUPT')
        return index

    def _write(self, index):
        fd, temp = tempfile.mkstemp(prefix='.library-', dir=self.directory)
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(index, f, ensure_ascii=False, allow_nan=False, indent=2)
                f.flush(); os.fsync(f.fileno())
            os.replace(temp, self.index_path)
        finally:
            if os.path.exists(temp): os.unlink(temp)

    def _decorate(self, record, entry, index):
        result = index['results'].get(record['revision'])
        current_result = result and (self.configuration is None or result.get('configuration') == self.configuration)
        return {**record, 'project_id': entry['project_id'], 'kind': entry['kind'],
            'archived': entry['archived'], 'head_revision': entry['revision'],
            'version_number': entry['history'].index(record['revision'])+1,
            'version_count': len(entry['history']), 'builtin_key': entry.get('builtin_key'),
            'validation_status': result['status'] if current_result else
                'offline_checked' if entry['kind']=='builtin' else 'previous_scene' if result else 'unverified',
            'last_result': result}

    def get(self, revision, project_id=None):
        record = RevisionStore.get(self, revision)
        with self.lock:
            index = self._read()
            if project_id is not None:
                entry = index['projects'].get(project_id)
                if entry is None or revision not in entry['history']: raise ValueError('REVISION_NOT_IN_PROJECT')
                return self._decorate(record, entry, index)
            entries = [e for e in index['projects'].values() if revision in e['history']]
            if not entries: return record
            entry = min(entries, key=lambda e: (e['archived'], e['revision'] != revision))
            return self._decorate(record, entry, index)

    def save(self, name, actions, project_id=None, expected_revision=None, new_project=False, builtin_key=None):
        if not isinstance(name, str) or not 0 < len(name.strip()) <= 80:
            raise ValueError('INVALID_PROJECT_NAME')
        name = name.strip()
        if type(new_project) is not bool or (new_project and project_id): raise ValueError('INVALID_ARGUMENT')
        with self.lock:
            index = self._read()
            if builtin_key:
                if builtin_key not in ('standard', 'three-zone'): raise ValueError('INVALID_BUILTIN')
                project_id = 'builtin-'+builtin_key
            elif project_id:
                entry = index['projects'].get(project_id)
                if entry is None: raise ValueError('PROJECT_NOT_FOUND')
                if entry['kind'] == 'builtin': raise ValueError('BUILTIN_READ_ONLY')
                if entry['archived']: raise ValueError('PROJECT_ARCHIVED')
                if expected_revision != entry['revision']: raise ValueError('PROJECT_VERSION_CONFLICT')
            elif not new_project:
                entry = next((e for e in index['projects'].values() if not e['archived']
                    and e['kind']=='custom' and e['name']==name), None)
                project_id = entry['project_id'] if entry else None
            creating = project_id not in index['projects']
            if not project_id:
                project_id = 'project-'+uuid.uuid4().hex
            names = {e['name'] for e in index['projects'].values() if e['project_id'] != project_id}
            if not builtin_key and creating:
                base, suffix = name, 2
                while name in names:
                    name = base[:65]+'（副本%d）'%suffix; suffix += 1
            elif not builtin_key and name in names:
                raise ValueError('PROJECT_NAME_EXISTS')
            # Call the immutable implementation; it returns decorated data only
            # after the file exists. The index is committed once the record is durable.
            record = RevisionStore.save(self, name, actions)
            entry = index['projects'].get(project_id, {'project_id': project_id, 'history': [],
                'kind': 'builtin' if builtin_key else 'custom', 'archived': False})
            if record['revision'] not in entry['history']: entry['history'].append(record['revision'])
            entry.update(name=name, revision=record['revision'], updated_ns=time.time_ns())
            if builtin_key: entry.update(builtin_key=builtin_key, archived=False)
            index['projects'][project_id] = entry
            self._write(index)
            return self._decorate(record, entry, index)

    def list(self, include_archived=False):
        with self.lock:
            index = self._read()
            result = []
            for entry in index['projects'].values():
                if entry['archived'] and not include_archived: continue
                record = self._decorate(RevisionStore.get(self, entry['revision']), entry, index)
                result.append({k: record[k] for k in ['revision', 'name', 'project_id', 'kind', 'archived',
                    'action_count'] if k in record} | {'action_count': len(record['actions']),
                    'version_number': record['version_number'], 'version_count': record['version_count'],
                    'validation_status': record['validation_status'], 'last_result': record['last_result']})
            return sorted(result, key=lambda r: (r['archived'], r['kind'] != 'builtin', r['project_id'] != 'builtin-standard', r['name']))

    def history(self, project_id):
        with self.lock:
            index = self._read()
            if project_id not in index['projects']: raise ValueError('PROJECT_NOT_FOUND')
            e = index['projects'][project_id]
            return [{k: r[k] for k in ['revision','name','version_number','validation_status']} |
                    {'current': revision == e['revision']}
                    for revision in reversed(e['history'])
                    for r in [self._decorate(RevisionStore.get(self, revision), e, index)]]

    def restore(self, project_id, revision, expected_revision):
        with self.lock:
            index = self._read(); e = index['projects'].get(project_id)
            if e is None: raise ValueError('PROJECT_NOT_FOUND')
            if e['kind']=='builtin': raise ValueError('BUILTIN_READ_ONLY')
            if e['revision'] != expected_revision: raise ValueError('PROJECT_VERSION_CONFLICT')
            if revision not in e['history']: raise ValueError('REVISION_NOT_IN_PROJECT')
            record = RevisionStore.get(self, revision)
            if any(other['project_id'] != project_id and other['name'] == record['name']
                   for other in index['projects'].values()): raise ValueError('PROJECT_NAME_EXISTS')
            e.update(revision=revision, name=record['name'], archived=False)
            self._write(index)
            return self._decorate(record, e, index)

    def archive(self, project_id, archived=True):
        if type(archived) is not bool: raise ValueError('INVALID_ARGUMENT')
        with self.lock:
            index = self._read(); e = index['projects'].get(project_id)
            if e is None: raise ValueError('PROJECT_NOT_FOUND')
            if e['kind']=='builtin': raise ValueError('BUILTIN_READ_ONLY')
            e['archived'] = archived; self._write(index)
            return {'project_id': project_id, 'archived': archived}

    def export_library(self):
        with self.lock:
            index = self._read()
            revisions = {r for e in index['projects'].values() for r in e['history']}
            return {'schema_version': 'library-backup-1', 'index': index,
                    'records': [RevisionStore.get(self, r) for r in sorted(revisions)]}

    def archive_custom(self):
        with self.lock:
            backup = self.export_library()
            directory = self.directory/'backups'; directory.mkdir(exist_ok=True)
            target = directory/('before-archive-%d.json'%time.time_ns())
            target.write_text(json.dumps(backup, ensure_ascii=False, indent=2))
            index = self._read(); count = 0
            for e in index['projects'].values():
                if e['kind']=='custom' and not e['archived']: e['archived'] = True; count += 1
            self._write(index)
            return {'archived_projects': count, 'backup_file': str(target)}

    def note_result(self, revision, job_id, status, error=None, configuration=None):
        if status not in ('succeeded','failed'): return
        with self.lock:
            index = self._read()
            if not any(revision in e['history'] for e in index['projects'].values()): return
            if index['results'].get(revision, {}).get('job_id') == job_id: return
            index['results'][revision] = {'job_id': job_id, 'status': status, 'error': error, 'configuration': configuration}
            self._write(index)
