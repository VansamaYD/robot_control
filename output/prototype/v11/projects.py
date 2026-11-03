"""Versioned declarative recipes. Does not eval/exec imported Python."""
from pathlib import Path
from copy import deepcopy
import hashlib
import json
import math
import os
import tempfile


def validate(actions):
    if not isinstance(actions, list) or not 1 <= len(actions) <= 100:
        raise ValueError('INVALID_ACTION_LIST')
    result = []
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            raise ValueError(f'INVALID_ACTION:{index}')
        kind = action.get('kind')
        fields = {'move_tcp': {'kind', 'name', 'p', 'n', 'duration_s'},
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
        result.append(deepcopy(action))
    return result


def code_view(actions):
    lines = ['# Generated code view; execution uses validated actions, never exec().', 'def main(robot):']
    for action in actions:
        if action['kind'] == 'move_tcp':
            call = f"move_tcp({action['name']!r}, {action['p']!r}, {action['n']!r}, {action['duration_s']!r})"
        elif action['kind'] == 'scoop':
            call = f"scoop({action['name']!r}, {round(action['target_m3'] * 1e6, 6)!r}, {action['duration_s']!r})"
        else:
            call = f"wait({action['name']!r}, {action['duration_s']!r})"
        lines.append('    yield robot.' + call)
    return '\n'.join(lines) + '\n'


class ProjectStore:
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
            record = self.get(path.stem)
            result.append({'revision': record['revision'], 'name': record['name'], 'action_count': len(record['actions'])})
        return sorted(result, key=lambda r: (r['name'], r['revision']))
