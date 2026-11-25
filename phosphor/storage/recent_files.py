"""Qt-free, versioned recent documents, kept separately for each workspace."""
import json
import os
from pathlib import Path
import tempfile

from phosphor.storage.app_paths import USER_DIR

RECENT_PATH = USER_DIR/'recent-files.json'
KINDS = ('scene', 'project', 'audio', 'model')
_LIMITS = {'scene': 10, 'project': 10, 'audio': 20, 'model': 10}


def _key(file):
    return os.path.normcase(str(Path(file).expanduser().resolve()))


def _read(path):
    """(known entries that still exist, newest first, deduplicated and limited; entries of kinds
    a newer phosphor added, kept so a rewrite never drops them; whether the file is damaged).
    A missing file is ([], [], False)."""
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(data, dict) or data.get('version') != 1: return [], [], True
        entries = data.get('entries', [])
        if not isinstance(entries, list): return [], [], True
        result, seen, others = [], set(), []
        for entry in entries:
            if (isinstance(entry, dict) and isinstance(entry.get('kind'), str) and entry['kind'] not in KINDS
                    and isinstance(entry.get('path'), str)):
                others.append({'kind': entry['kind'], 'path': entry['path']}); continue
            if not isinstance(entry, dict) or entry.get('kind') not in KINDS: continue
            file = entry.get('path')
            if not isinstance(file, str) or not file: continue
            resolved = str(Path(file).expanduser().resolve())
            key = (entry['kind'], _key(resolved))
            if key in seen or not Path(resolved).is_file(): continue
            if sum(e['kind'] == entry['kind'] for e in result) >= _LIMITS[entry['kind']]: continue
            seen.add(key); result.append({'kind': entry['kind'], 'path': resolved})
        return result, others, False
    except FileNotFoundError:
        return [], [], False
    except (OSError, ValueError, RuntimeError):
        return [], [], True


def _write(entries, path, others=()):
    _write_json({'version': 1, 'entries': list(entries) + list(others)}, path)


def _write_json(data, path):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                                         dir=path.parent, delete=False) as stream:
            temporary = stream.name
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        if temporary is not None and os.path.exists(temporary): os.unlink(temporary)


def read_recent(kind, path=RECENT_PATH, prune=True):
    """Return existing files, newest first; prune stale entries from disk unless prune is False
    (the browser reads on a worker and never writes). A damaged file is never rewritten here, so
    it can still be inspected; the next add replaces it."""
    if kind not in KINDS: raise ValueError('Unknown recent-file kind')
    entries, others, damaged = _read(path)
    if prune and not damaged and Path(path).exists(): _write(entries, path, others)
    return [Path(e['path']) for e in entries if e['kind'] == kind]


def add_recent(kind, file, path=RECENT_PATH):
    if kind not in KINDS: raise ValueError('Unknown recent-file kind')
    file = Path(file).expanduser().resolve()
    entries, others, _ = _read(path)
    entries = [e for e in entries if e['kind'] != kind or _key(e['path']) != _key(file)]
    if file.is_file(): entries.insert(0, {'kind': kind, 'path': str(file)})
    counts = dict.fromkeys(KINDS, 0)
    kept = []
    for entry in entries:
        counts[entry['kind']] += 1
        if counts[entry['kind']] <= _LIMITS[entry['kind']]: kept.append(entry)
    _write(kept, path, others)


def remove_recent(kind, file, path=RECENT_PATH):
    if kind not in KINDS: raise ValueError('Unknown recent-file kind')
    entries, others, _ = _read(path)
    _write([e for e in entries if e['kind'] != kind or _key(e['path']) != _key(file)], path, others)


def clear_recent(kind, path=RECENT_PATH):
    if kind not in KINDS: raise ValueError('Unknown recent-file kind')
    entries, others, _ = _read(path)
    _write([e for e in entries if e['kind'] != kind], path, others)
