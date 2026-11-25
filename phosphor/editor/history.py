"""Immutable Composer and Sandbox snapshots and edit names; deliberately independent of Qt.

One History class serves both workspaces (U1, 0.78.0-dev): the Composer keeps Project
snapshots (State), the Sandbox keeps scene snapshots (SceneState). Snapshots share every
unchanged frozen object with their neighbours, so an entry costs a Settings and a shapes tuple;
the Sandbox history also weighs the models and drawings it keeps alive (heavy_payloads) and
drops its oldest entries past a byte budget.
"""
from dataclasses import dataclass, fields, replace
from time import monotonic

from phosphor.engine.composition import Project
from phosphor.engine.signal_engine import Settings
from phosphor.storage.app_files import GLOBAL_FIELDS, scene_state


@dataclass(frozen=True)
class State:
    project: Project
    clip_index: int = 0
    selected_object: int = -1


@dataclass(frozen=True)
class SceneState:
    """A Sandbox snapshot: what the scene file saves (settings and listening mode) plus the
    selected object. The MIDI input device is a machine choice and is not recorded."""
    settings: Settings
    mode: str = 'drawing'
    selected: int = -1


# Settings fields a scene file never saves (machine signal preferences and runtime clocks);
# Sandbox undo keeps their current values (live_fields, with_live).
LIVE_FIELDS = tuple(f.name for f in fields(Settings) if f.name not in GLOBAL_FIELDS and f.name != 'shapes')
SCENE_HISTORY_BUDGET = 128 << 20  # Estimated bytes of models and drawings kept alive by undo.


def saved_value(state):
    """Equal for two SceneStates exactly when their scene files would be (ignoring the device)."""
    return scene_state(state.settings, state.mode, '')


def with_live(settings, current):
    """settings with current's runtime-only fields, so undo never rewinds them."""
    values = {name: getattr(current, name) for name in LIVE_FIELDS if getattr(settings, name) != getattr(current, name)}
    return replace(settings, **values) if values else settings


def heavy_payloads(state):
    """{id: estimated bytes} of the large frozen objects a SceneState holds (models, drawings).
    Tuples of float tuples cost about 150 bytes a vertex or node and 130 an edge in CPython."""
    found = {}
    for shape in state.settings.shapes:
        model, drawing = shape.model, shape.custom
        if model is not None:
            animation = getattr(model, 'animation', None)
            found[id(model)] = 150*len(model.vertices) + 130*len(model.edges) + len(getattr(animation, 'data', b''))
        if drawing is not None:
            found[id(drawing)] = 150*len(drawing.nodes) + 130*len(drawing.edges) + 150*len(drawing.curves)
    return found


class History:
    def __init__(self, limit=200, weigh=None, budget=None):
        """limit: entries kept; weigh(state) -> {id: bytes} and budget: drop the oldest entries
        while the distinct payloads all entries keep alive exceed budget bytes."""
        self.limit, self.weigh, self.budget = limit, weigh, budget
        self.reset(None)

    def reset(self, state, dirty=False):
        self._entries = [('', state, object())]
        self._index = 0
        self._saved = None if dirty else self._entries[0][2]
        self._key = self._time = None

    @property
    def state(self): return self._entries[self._index][1]
    @property
    def can_undo(self): return self._index > 0
    @property
    def can_redo(self): return self._index + 1 < len(self._entries)
    @property
    def undo_label(self): return self._entries[self._index][0] if self.can_undo else ''
    @property
    def redo_label(self): return self._entries[self._index + 1][0] if self.can_redo else ''
    @property
    def dirty(self): return self._entries[self._index][2] is not self._saved
    def __len__(self): return len(self._entries)

    @property
    def weight(self):
        """Estimated bytes of the distinct payloads every entry keeps alive (0 without weigh)."""
        if self.weigh is None: return 0
        total = {}
        for _, state, _ in self._entries: total.update(self.weigh(state))
        return sum(total.values())

    def push(self, label, state, key=None, now=None):
        now = monotonic() if now is None else now
        merge = (not self.can_redo and self.can_undo and key is not None
                 and key == self._key and self._time is not None
                 and 0 <= now - self._time <= .4
                 and self._entries[self._index][2] is not self._saved)
        del self._entries[self._index + 1:]
        if merge:
            self._entries[self._index] = (self.undo_label, state, object())
        else:
            self._entries.append((label, state, object()))
            if len(self._entries) > self.limit: self._entries.pop(0)
            if self.budget is not None:
                while len(self._entries) > 2 and self.weight > self.budget: self._entries.pop(0)
            self._index = len(self._entries) - 1
        self._key, self._time = key, now

    def amend(self, state):
        """Replace the current entry's state without a new step (the Sandbox keeps its current
        entry's selection up to date, so Undo reselects what was selected before the edit)."""
        label, _, token = self._entries[self._index]
        self._entries[self._index] = (label, state, token)

    def undo(self):
        if not self.can_undo: return None
        self._index -= 1
        self._key = self._time = None
        return self.state

    def redo(self):
        if not self.can_redo: return None
        self._index += 1
        self._key = self._time = None
        return self.state

    def mark_saved(self):
        self._saved = self._entries[self._index][2]
        self._key = self._time = None


def describe(before, after):
    # Tracks first (AW1), except when adding clips to a project without tracks made 'Audio 1'.
    from phosphor.engine.audio_clips import AudioTrack
    first_clip = not before.audio_tracks and after.audio_tracks == (AudioTrack(),) and after.audio_clips != before.audio_clips
    if before.audio_tracks != after.audio_tracks and not first_clip:  # Adding the first clip makes 'Audio 1'.
        from phosphor.engine.audio_tracks import describe as describe_tracks
        label = describe_tracks(before, after)
        if label: return label
    if before.audio_clips != after.audio_clips:
        from phosphor.engine.audio_edit import describe as describe_audio
        return describe_audio(before.audio_clips, after.audio_clips)
    if before.markers != after.markers:
        if len(before.markers) != len(after.markers):
            return 'Add marker' if len(after.markers) > len(before.markers) else 'Delete marker'
        return 'Edit markers'
    if before.bpm != after.bpm: return 'Change tempo'
    if (before.loop, before.loop_start, before.loop_end) != (after.loop, after.loop_start, after.loop_end): return 'Change loop'
    if len(before.clips) != len(after.clips): return 'Add clip' if len(after.clips) > len(before.clips) else 'Delete clip'
    pairs = tuple(zip(before.clips, after.clips))
    for field, label in (('start', 'Move clip'), ('length', 'Resize clip'), ('lanes', 'Edit automation'), ('color', 'Colour clip')):
        if any(getattr(a, field) != getattr(b, field) for a, b in pairs): return label
    if any(a.muted != b.muted for a, b in pairs):
        return 'Mute clip' if any(b.muted and not a.muted for a, b in pairs) else 'Unmute clip'
    if any(a.settings != b.settings or a.mode != b.mode for a, b in pairs):
        from phosphor.engine.lane_access import without_switches
        if all(a.mode == b.mode and without_switches(a).settings == without_switches(b).settings for a, b in pairs):
            return 'Edit On/Off'
        return 'Edit scene'
    if before.notes != after.notes:
        if len(before.notes) == len(after.notes) and all(replace(a, velocity=b.velocity) == b for a, b in zip(before.notes, after.notes)):
            return 'Edit velocity'
        return 'Edit notes'
    if any(a.name != b.name for a, b in pairs): return 'Rename clip'
    if before.chords != after.chords and replace(before, chords=after.chords) == after:
        return 'Chords on' if after.chords else 'Chords off'
    if before.velocity != after.velocity and replace(before, velocity=after.velocity) == after:
        return 'Velocity on' if after.velocity else 'Velocity off'
    return 'Edit'


# Sandbox labels that never merge with the next edit, whatever has focus.
STRUCTURAL = frozenset(('Add object', 'Delete object', 'Reorder objects', 'Add transform',
                        'Remove transform', 'Reorder transforms', 'Change shape type',
                        'Add variable', 'Remove variable', 'Rename variable'))
_SCENE_LABELS = (('source', 'Change source'), ('bpm', 'Change tempo'), ('drawing_gain', 'Change level'),
                 ('music_gain', 'Change original blend'), ('midi_file', 'Change MIDI file'))
_OBJECT_LABELS = (('solid', 'Change shape type'), ('custom', 'Edit drawing'), ('model', 'Edit model'),
                  ('text', 'Edit text'), ('plot', 'Edit equations'), ('recurrence', 'Edit recurrence'),
                  ('path_start', 'Edit path'), ('path_trace', 'Edit path'), ('motion', 'Edit motion path'),
                  ('animation', 'Edit animation'), ('lanes', 'Edit automation'),
                  ('shape_size', 'Resize object'))


def _transform_label(before, after):
    if len(before) != len(after): return 'Add transform' if len(after) > len(before) else 'Remove transform'
    if before != after and sorted(map(id, before)) == sorted(map(id, after)): return 'Reorder transforms'
    if [t.kind for t in before] != [t.kind for t in after]:
        return 'Reorder transforms' if sorted(t.kind for t in before) == sorted(t.kind for t in after) else 'Edit transform'
    for a, b in zip(before, after):
        if a == b: continue
        if a.enabled != b.enabled and replace(a, enabled=b.enabled) == b:
            return 'Bypass transform' if not b.enabled else 'Enable transform'
        if a.noise != b.noise: return 'Edit Noise'
        if b.kind == 'Map': return 'Edit Map'
        return 'Edit ' + b.kind.lower()
    return 'Edit transform'


def _variable_label(before, after):
    """Object variables (T5): a rename also rewrites the expressions that read the variable."""
    if len(before) != len(after): return 'Add variable' if len(after) > len(before) else 'Remove variable'
    if [v.name for v in before] != [v.name for v in after]: return 'Rename variable'
    return 'Edit variable'


def describe_scene(before, after):
    """The Edit menu name of a Sandbox step between two SceneStates."""
    old, new = before.settings, after.settings
    if before.mode != after.mode: return 'Change listening mode'
    a, b = old.shapes, new.shapes
    if len(a) != len(b): return 'Add object' if len(b) > len(a) else 'Delete object'
    if a != b and sorted(map(id, a)) == sorted(map(id, b)): return 'Reorder objects'
    for x, y in zip(a, b):
        if x == y: continue
        if x.muted != y.muted: return 'Mute object' if y.muted else 'Unmute object'
        if x.stack_bypassed != y.stack_bypassed: return 'Bypass stack' if y.stack_bypassed else 'Enable stack'
        if x.transform_variables != y.transform_variables: return _variable_label(x.transform_variables, y.transform_variables)
        if x.transforms != y.transforms: return _transform_label(x.transforms, y.transforms)
        for name, label in _OBJECT_LABELS:
            if getattr(x, name) != getattr(y, name): return label
        if (x.position_x, x.position_y) != (y.position_x, y.position_y): return 'Move object'
        return 'Edit object'
    for name, label in _SCENE_LABELS:
        if getattr(old, name) != getattr(new, name): return label
    if (old.scale_type, old.scale_root, old.scale_end) != (new.scale_type, new.scale_root, new.scale_end): return 'Change scale'
    return 'Edit scene settings'
