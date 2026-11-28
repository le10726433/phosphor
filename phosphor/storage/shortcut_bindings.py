"""Per-machine keyboard shortcut overrides.

A damaged or newer file is reported and never overwritten except by an explicit
Reset all in the app.
"""
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from types import MappingProxyType

from phosphor.editor.shortcuts import BY_ACTION
from phosphor.storage.app_paths import USER_DIR


SHORTCUT_BINDINGS_PATH = USER_DIR/'shortcuts.json'
FORMAT = 'phosphor-shortcuts'
VERSION = 1
MAX_KEYS = 4
SCOPES = dict(
    new='window', open='window', save='window', save_as='window', undo='window', redo='window',
    fullscreen='window', trace_only='window', escape='window', play='window', home='window',
    end='window', zoom_song='window', zoom_selection='window', toggle_library='window',
    panel_input='window', panel_scene='window', panel_mix='window', panel_appearance='window',
    panel_signal='window', switch_workspace='window', quick_add='window', desk3d='window',
    object_mute='objects', object_solo='objects', object_unmute_all='objects',
    split_clip='timeline', add_marker='timeline', note_duplicate='timeline', note_split='timeline',
    note_join='timeline', note_legato='timeline', note_quantize='timeline')
REBINDABLE = tuple(SCOPES)
PLATFORMS = ('darwin', 'win32')
_MODIFIERS = {name.lower(): name for name in ('Meta', 'Ctrl', 'Alt', 'Shift')}
_UNSUPPORTED = 'Unsupported keyboard shortcuts file.'


def normalize_key(text):
    """Normalize a single PortableText chord, including literal plus and comma keys."""
    error = ValueError(f'"{text}" is not a single key with modifiers.')
    if not isinstance(text, str):
        raise error
    key = text.strip()
    if len(key) > 40:
        raise error
    modifiers = set()
    while '+' in key and key != '+':
        prefix, key = key.split('+', 1)
        modifier = _MODIFIERS.get(prefix.lower())
        if modifier is None or modifier in modifiers:
            raise error
        modifiers.add(modifier)
    if key.lower() in _MODIFIERS:
        raise error
    if len(key) == 1 and key.isprintable() and not key.isspace():
        key = key.upper()
    elif not re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,11}', key):
        raise error
    key = {'Escape': 'Esc', 'Delete': 'Del'}.get(key, key)
    return '+'.join([name for name in _MODIFIERS.values() if name in modifiers] + [key])


def physical_to_portable(text, platform):
    """Convert a platform registry entry and its alternatives to portable keys."""
    if platform == 'darwin':
        text = text.replace('Command+', 'Ctrl+').replace('Control+', 'Meta+').replace('Option+', 'Alt+')
    return tuple(normalize_key(part) for part in text.split(' or '))


def platform_name(platform=None):
    """Return the registry platform for this machine or the supplied platform."""
    return 'darwin' if (platform or sys.platform) == 'darwin' else 'win32'


def default_bindings(platform=None):
    """Return the registry defaults for all rebindable actions."""
    platform = platform_name(platform)
    return {action: physical_to_portable(
        BY_ACTION[action].macos if platform == 'darwin' else BY_ACTION[action].windows, platform)
        for action in REBINDABLE}


def validated_keys(keys):
    """Validate and normalize one action's ordered list of alternate keys."""
    if not isinstance(keys, (list, tuple)):
        raise ValueError('Provide a list of keys for this shortcut.')
    if len(keys) > MAX_KEYS:
        raise ValueError(f'Use at most {MAX_KEYS} keys for one shortcut.')
    result = []
    for key in keys:
        key = normalize_key(key)
        if key in result:
            raise ValueError(f'{key} is listed twice.')
        result.append(key)
    return tuple(result)


def _check_action(action):
    if action not in SCOPES:
        raise ValueError(f'Unknown shortcut action "{action}".')


def _clash(a, b):
    return a != b and (SCOPES[a] == SCOPES[b] or 'window' in (SCOPES[a], SCOPES[b]))


class ShortcutConflict(ValueError):
    """A proposed edit would take keys from other actions."""

    def __init__(self, conflicts):
        self.conflicts = conflicts
        other, key = conflicts[0]
        super().__init__(f'{key} is already used by "{BY_ACTION[other].description}".')


@dataclass(frozen=True, slots=True, init=False)
class ShortcutBindings:
    """An immutable snapshot of one platform's defaults and user overrides."""

    platform: str
    _defaults: MappingProxyType
    _overrides: MappingProxyType

    def __init__(self, overrides=None, platform=None, defaults=None):
        platform = platform_name(platform)
        if defaults is None:
            defaults = default_bindings(platform)
        if set(defaults) != set(REBINDABLE):
            raise ValueError('Shortcut defaults must cover exactly the rebindable actions.')
        defaults = {action: validated_keys(defaults[action]) for action in REBINDABLE}
        custom = {}
        for action, keys in (overrides or {}).items():
            _check_action(action)
            if not isinstance(keys, (str, list, tuple)):
                try:
                    keys = tuple(keys)
                except TypeError as error:
                    raise ValueError('Provide a list of keys for this shortcut.') from error
            keys = validated_keys(keys)
            if keys != defaults[action]:
                custom[action] = keys
        object.__setattr__(self, 'platform', platform)
        object.__setattr__(self, '_defaults', MappingProxyType(defaults))
        object.__setattr__(self, '_overrides', MappingProxyType(custom))

    @property
    def defaults(self):
        """Return a copy of the active platform defaults."""
        return dict(self._defaults)

    @property
    def overrides(self):
        """Return a copy of the actions changed by the user."""
        return dict(self._overrides)

    def keys(self, action):
        """Return the effective keys for one action."""
        _check_action(action)
        return self._overrides.get(action, self._defaults[action])

    def is_custom(self, action):
        """Return whether the action differs from its default."""
        _check_action(action)
        return action in self._overrides

    def effective(self):
        """Return all effective bindings in registry scope order."""
        return {action: self.keys(action) for action in REBINDABLE}

    def conflicts(self, action, keys):
        """List competing actions in proposed key order and then action order."""
        _check_action(action)
        return tuple((other, key) for key in validated_keys(keys) for other in REBINDABLE
                     if _clash(action, other) and key in self.keys(other))

    def with_keys(self, action, keys, take_over=False):
        """Assign keys, optionally removing them from competing actions."""
        _check_action(action)
        keys = validated_keys(keys)
        conflicts = self.conflicts(action, keys)
        if conflicts and not take_over:
            raise ShortcutConflict(conflicts)
        custom = self.overrides
        for other, key in conflicts:
            custom[other] = tuple(k for k in custom.get(other, self._defaults[other]) if k != key)
        custom[action] = keys
        return ShortcutBindings(custom, self.platform, self._defaults)

    def reset(self, action):
        """Restore one action to its platform default."""
        _check_action(action)
        custom = self.overrides
        custom.pop(action, None)
        return ShortcutBindings(custom, self.platform, self._defaults)

    def reset_all(self):
        """Restore all actions to their platform defaults."""
        return ShortcutBindings(platform=self.platform, defaults=self._defaults)

    def has_conflicts(self):
        """List shared keys between overlapping scopes, once per pair of actions."""
        return tuple((a, b, key) for i, a in enumerate(REBINDABLE) for b in REBINDABLE[i+1:]
                     if _clash(a, b) for key in self.keys(a) if key in self.keys(b))

    def document(self):
        """Serialize only the actions whose keys differ from the current defaults."""
        return dict(format=FORMAT, version=VERSION, platform=self.platform,
                    bindings={a: list(self._overrides[a]) for a in REBINDABLE if a in self._overrides})


def _require_clash_free(bindings):
    conflicts = bindings.has_conflicts()
    if conflicts:
        a, b, key = conflicts[0]
        raise ValueError(f'"{BY_ACTION[a].description}" and "{BY_ACTION[b].description}" both use {key}.')


def bindings_from_document(data, platform=None, defaults=None, notices=None):
    """Load portable overrides against the receiving machine's defaults."""
    if not isinstance(data, dict) or data.get('format') != FORMAT:
        raise ValueError(_UNSUPPORTED)
    version = data.get('version')
    if type(version) is int and version > VERSION:
        raise ValueError(f'The keyboard shortcuts file is from a newer phosphor (version {version}); '
                         'update phosphor to use it.')
    if (type(version) is not int or version != VERSION or set(data) != {'format', 'version', 'platform', 'bindings'}
            or data['platform'] not in PLATFORMS or not isinstance(data['bindings'], dict)):
        raise ValueError(_UNSUPPORTED)
    if data['platform'] != platform_name(platform) and isinstance(notices, list):
        source = 'macOS' if data['platform'] == 'darwin' else 'Windows'
        notices.append(f'These shortcuts were saved on {source}; check keys that use Command or Control.')
    overrides = {}
    for action, keys in data['bindings'].items():
        if action not in SCOPES:
            if isinstance(notices, list):
                notices.append(f'Skipped an unknown shortcut "{action}" (from a newer phosphor?).')
            continue
        if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
            raise ValueError(_UNSUPPORTED)
        try:
            overrides[action] = validated_keys(keys)
        except ValueError as error:
            raise ValueError(f'Shortcut "{action}": {error}') from error
    result = ShortcutBindings(overrides, platform, defaults)
    _require_clash_free(result)
    return result


def _read(path, platform, defaults, notices, missing_ok):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        if missing_ok and isinstance(error, FileNotFoundError):
            return ShortcutBindings(platform=platform, defaults=defaults)
        raise ValueError(f'Cannot read keyboard shortcuts: {error}') from error
    return bindings_from_document(data, platform, defaults, notices)


def read_bindings(path=SHORTCUT_BINDINGS_PATH, platform=None, defaults=None, notices=None):
    """Read machine shortcuts, using defaults until a file exists."""
    return _read(path, platform, defaults, notices, missing_ok=True)


def write_bindings(bindings, path=SHORTCUT_BINDINGS_PATH):
    """Atomically save the overrides after checking for conflicting shortcuts."""
    from phosphor.storage.app_files import atomic_json
    _require_clash_free(bindings)
    atomic_json(path, bindings.document())


def export_bindings(bindings, path):
    """Save the same portable shortcut document to a chosen file."""
    write_bindings(bindings, path)


def import_bindings(path, platform=None, defaults=None, notices=None):
    """Import an existing shortcut file against this machine's defaults."""
    return _read(path, platform, defaults, notices, missing_ok=False)
