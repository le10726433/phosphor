"""Shortcut and gesture reference. Qt imports are deferred for pure reference tests.

macOS strings name physical keys; Qt swaps Command/Control internally.
"""
from dataclasses import dataclass, replace
import sys


@dataclass(frozen=True)
class Shortcut:
    action: str
    macos: str
    windows: str
    where: str
    description: str
    standard: str = ''


SHORTCUTS = (
    Shortcut('object_mute', 'M', 'M', 'Objects list', 'Toggle mute on selected objects'),
    Shortcut('object_solo', 'S', 'S', 'Objects list', 'Toggle solo on selected objects'),
    Shortcut('object_unmute_all', 'Option+M', 'Alt+M', 'Objects list', 'Unmute all objects'),
    Shortcut('new', 'Command+N', 'Ctrl+N', 'general', 'New instance (a second phosphor window)', 'New'),
    Shortcut('open', 'Command+O', 'Ctrl+O', 'general', 'Open scene / project', 'Open'),
    Shortcut('save', 'Command+S', 'Ctrl+S', 'general', 'Save scene / project', 'Save'),
    Shortcut('save_as', 'Shift+Command+S', 'Ctrl+Shift+S', 'general', 'Save … as', 'SaveAs'),
    Shortcut('undo', 'Command+Z', 'Ctrl+Z', 'general', 'Undo (Composer and Sandbox; shape builder keeps its own history)', 'Undo'),
    Shortcut('redo', 'Shift+Command+Z', 'Ctrl+Y or Ctrl+Shift+Z', 'general', 'Redo (Composer and Sandbox; shape builder keeps its own history)', 'Redo'),
    Shortcut('fullscreen', 'Control+Command+F', 'F11', 'general', 'Full screen (the app window)'),
    Shortcut('trace_only', 'Shift+Command+F', 'Ctrl+Shift+F', 'general', 'Trace only'),
    Shortcut('escape', 'Escape', 'Escape', 'general', 'Leave full screen, Trace only or the full-screen trace'),
    Shortcut('play', 'Space', 'Space', 'general', 'Play / Stop (Composer and Sandbox)'),
    Shortcut('home', 'Home', 'Home', 'general', 'Go to start / loop start'),
    Shortcut('end', 'End', 'End', 'general', 'Stop at song end (Composer)'),
    Shortcut('zoom_song', 'Command+0', 'Ctrl+0', 'general', 'Zoom to song (Composer)'),
    Shortcut('copy', 'Command+C', 'Ctrl+C', 'Composer timeline; shape builder', 'Copy selection', 'Copy'),
    Shortcut('cut', 'Command+X', 'Ctrl+X', 'Composer timeline; shape builder', 'Cut selection', 'Cut'),
    Shortcut('paste', 'Command+V', 'Ctrl+V', 'Composer timeline; shape builder', 'Paste selection (timeline: at playhead)', 'Paste'),
    Shortcut('delete', 'Delete or Backspace', 'Delete or Backspace', 'Composer timeline; shape builder', 'Delete selection'),
    Shortcut('select_all', 'Command+A', 'Ctrl+A', 'Composer note roll', 'Select all notes', 'SelectAll'),
    Shortcut('transpose', '↑ / ↓', '↑ / ↓', 'Composer note roll', 'Transpose selected notes one semitone (scale degree with Snap to scale)'),
    Shortcut('note_duplicate', 'Command+D', 'Ctrl+D', 'Composer note roll', 'Duplicate selected notes'),
    Shortcut('note_split', 'Command+E', 'Ctrl+E', 'Composer note roll', 'Split notes at playhead'),
    Shortcut('note_join', 'Command+J', 'Ctrl+J', 'Composer note roll', 'Join touching selected notes of the same pitch'),
    Shortcut('note_legato', 'Command+L', 'Ctrl+L', 'Composer note roll', 'Extend selected notes to the next chord group'),
    Shortcut('note_quantize', 'Command+U', 'Ctrl+U', 'Composer note roll', 'Quantize selected notes…'),
    Shortcut('note_copy_drag', 'Option+drag note', 'Alt+drag note', 'Composer note roll', 'Copy and move selected notes'),
    Shortcut('note_chord', 'Click in Chord draw mode', 'Click in Chord draw mode', 'Composer note roll', 'Stamp a chord'),
    Shortcut('pitch_zoom', 'Control+wheel', 'Ctrl+wheel', 'Composer note roll', 'Zoom pitch rows over the pitch names'),
    Shortcut('octave', 'Shift+↑ / Shift+↓', 'Shift+↑ / Shift+↓', 'Composer note roll', 'Transpose selected notes one octave'),
    Shortcut('move_notes', '← / → (also with Shift)', '← / → (also with Shift)', 'Composer note roll', 'Move selected notes one Snap step (Off: quarter beat)'),
    Shortcut('scrub', 'Click / drag ruler', 'Click / drag ruler', 'Composer ruler', 'Move playhead / scrub'),
    Shortcut('loop', 'Shift+drag', 'Shift+drag', 'Composer ruler', 'Set loop range and enable Loop'),
    Shortcut('clip_select', 'Click', 'Click', 'Composer Scenes track', 'Select clip and edit its scene'),
    Shortcut('clip_toggle', 'Shift+click', 'Shift+click', 'Composer Scenes track', 'Add or remove clip from selection (drag moves them as a block)'),
    Shortcut('split_clip', 'S', 'S', 'Composer timeline', 'Split clip at playhead'),
    Shortcut('add_marker', 'M', 'M', 'Composer timeline', 'Add section marker at playhead'),
    Shortcut('clip_duplicate', 'Option+drag', 'Alt+drag', 'Composer Scenes track', 'Duplicate clip'),
    Shortcut('clip_move', 'Drag body / left bar / right bar', 'Drag body / left bar / right bar', 'Composer Scenes track', 'Move clip (past the middle of a neighbour: swap; into a free gap) / change start / change end'),
    Shortcut('clip_add', 'Double-click empty space', 'Double-click empty space', 'Composer Scenes track', 'Add clip at bar'),
    Shortcut('clip_menu', 'Right-click', 'Right-click', 'Composer Scenes track', 'Clip menu or Paste'),
    Shortcut('draw_note', 'Click / drag empty space', 'Click / drag empty space', 'Composer note roll', 'Draw note / set length'),
    Shortcut('note_select', 'Click note', 'Click note', 'Composer note roll', 'Select only that note'),
    Shortcut('note_drag', 'Drag note / right edge', 'Drag note / right edge', 'Composer note roll', 'Move selected notes / resize note'),
    Shortcut('note_box', 'Shift+drag empty space', 'Shift+drag empty space', 'Composer note roll', 'Add notes touched by selection box'),
    Shortcut('note_toggle', 'Shift+click', 'Shift+click', 'Composer note roll', 'Add or remove note from selection'),
    Shortcut('note_menu', 'Right-click note / empty space', 'Right-click note / empty space', 'Composer note roll', 'Delete note / Paste at playhead'),
    Shortcut('lane_pick', 'Click legend name', 'Click legend name', 'Composer transform roll', 'Edit line or start automating it'),
    Shortcut('node_add', 'Click line / elsewhere', 'Click line / elsewhere', 'Composer transform roll', 'Add node on line / at height'),
    Shortcut('node_drag', 'Drag node / segment', 'Drag node / segment', 'Composer transform roll', 'Move node / move segment gain'),
    Shortcut('node_gain', 'Shift+drag node', 'Shift+drag node', 'Composer transform roll', 'Change gain only'),
    Shortcut('node_gain_entry', 'Double-click node', 'Double-click node', 'Composer transform roll', 'Type node gain (selected nodes if selected)'),
    Shortcut('node_select', 'Shift+click node', 'Shift+click node', 'Composer transform roll', 'Add or remove node from selection'),
    Shortcut('node_time', 'Option+drag node', 'Alt+drag node', 'Composer transform roll', 'Change time only'),
    Shortcut('node_menu', 'Right-click node / reference line', 'Right-click node / reference line', 'Composer transform roll', 'Node menu / Reference gain dialog'),
    Shortcut('reference_snap', 'Command+drag', 'Ctrl+drag', 'Composer transform roll', 'Bypass reference snapping'),
    Shortcut('timeline_resize', 'Drag top edge', 'Drag top edge', 'Composer', 'Resize timeline'),
    Shortcut('roll_scroll', 'Wheel', 'Wheel', 'Composer rolls', 'Scroll pitches / automation blocks'),
    Shortcut('time_scroll', 'Shift+wheel / horizontal wheel', 'Shift+wheel / horizontal wheel', 'Composer timeline', 'Move in time'),
    Shortcut('time_zoom', 'Control+wheel', 'Ctrl+wheel', 'Composer timeline', 'Zoom time around pointer'),
    Shortcut('row_height', 'Command+wheel', 'Alt+wheel', 'Composer rolls', 'Set row / block height (outside rolls: zoom time)'),
    Shortcut('trackpad_pan', 'Two-finger drag', 'Two-finger drag', 'Composer timeline (trackpad)', 'Move time and pitch rows / blocks with the fingers'),
    Shortcut('trackpad_zoom', 'Pinch', 'Pinch', 'Composer timeline (trackpad)', 'Zoom time around the pointer'),
    Shortcut('trackpad_height', 'Option+pinch', 'Alt+pinch', 'Composer timeline (trackpad)', 'Set row / block height'),
    Shortcut('inspector_close', 'Escape', 'Escape', 'Inspect cycle panel', 'Close the inspector and return to the previous panel'),
    Shortcut('builder_finish', 'Return / Enter', 'Return / Enter', 'Shape builder', 'Finish stroke'),
    Shortcut('builder_cancel', 'Escape', 'Escape', 'Shape builder', 'Cancel gesture'),
    Shortcut('builder_undo', 'Command+Z', 'Ctrl+Z', 'Shape builder', 'Undo drawing edit', 'Undo'),
    Shortcut('builder_redo', 'Shift+Command+Z', 'Ctrl+Y or Ctrl+Shift+Z', 'Shape builder', 'Redo drawing edit', 'Redo'),
    Shortcut('builder_zoom', 'Wheel', 'Wheel', 'Shape builder', 'Zoom canvas'),
    Shortcut('builder_pinch', 'Pinch', 'Pinch', 'Shape builder', 'Zoom canvas around the pointer (trackpad)'),
    Shortcut('builder_pan', 'Middle-drag or two-finger drag', 'Middle-drag or two-finger drag', 'Shape builder', 'Pan canvas (two fingers on a trackpad)'),
    Shortcut('builder_move', 'Drag point / stroke', 'Drag point / stroke', 'Shape builder Select', 'Move point / connected shape'),
    Shortcut('builder_box', 'Drag empty space', 'Drag empty space', 'Shape builder Select', 'Box-select connected shapes'),
    Shortcut('builder_add', 'Shift+click / Shift+box drag', 'Shift+click / Shift+box drag', 'Shape builder Select', 'Add shapes to selection'),
    Shortcut('builder_axis', 'Shift+drag', 'Shift+drag', 'Shape builder', 'Constrain movement to an axis'),
    Shortcut('builder_snap', 'Option', 'Alt', 'Shape builder', 'Bypass snapping while held'),
    Shortcut('builder_menu', 'Right-click', 'Right-click', 'Shape builder', 'Selection and clipboard menu'),
)
BY_ACTION = {entry.action: entry for entry in SHORTCUTS}


def format_keys(entry, platform=None):
    if (platform or sys.platform) != 'darwin':
        return entry.windows
    value = entry.macos
    for word, symbol in (('Shift+', '⇧'), ('Control+', '⌃'), ('Option+', '⌥'), ('Command+', '⌘')):
        value = value.replace(word, symbol)
        value = value.replace(word[:-1], symbol)
    return value


def mapped_shortcuts(preferences=None, platform=None):
    from phosphor.editor.wheel_mapping import validated_mapping, gesture
    if preferences is None: return SHORTCUTS
    mapping = validated_mapping(preferences, platform)
    actions = dict(time_scroll='wheel_scroll', time_zoom='wheel_zoom', pitch_zoom='wheel_zoom', row_height='wheel_height', builder_zoom='wheel_builder_zoom')
    result = []
    for entry in SHORTCUTS:
        if entry.action in actions:
            key = mapping[actions[entry.action]]
            suffix = ' / horizontal wheel' if entry.action == 'time_scroll' else ''
            entry = replace(entry, macos=gesture(key, 'darwin') + suffix, windows=gesture(key, 'win32') + suffix)
        result.append(entry)
    return tuple(result)


def filter_shortcuts(query, platform=None, preferences=None):
    """Case-insensitive AND search across action, keys, context and description."""
    words = query.casefold().split()
    def keys(entry):  # The keys in use only: a rebound action no longer matches its old keys (Codex review).
        return (display_keys(entry, platform),) if entry.action in _overrides else (format_keys(entry, platform), entry.macos, entry.windows)
    return tuple(entry for entry in mapped_shortcuts(preferences, platform) if all(word in ' '.join((
        entry.action, entry.description, entry.where, *keys(entry))).casefold() for word in words))


# Menu groups for Preferences › Controls › Keyboard shortcuts (UI2): the rebindable actions
# (storage/shortcut_bindings.SCOPES) by the menu or place that holds them, in menu order.
MENU_GROUPS = (('File', ('new', 'open', 'save', 'save_as')), ('Edit', ('undo', 'redo')),
               ('View', ('zoom_song', 'fullscreen', 'trace_only', 'escape')), ('Transport', ('play', 'home', 'end')),
               ('Objects list', ('object_mute', 'object_solo', 'object_unmute_all')),
               ('Composer timeline', ('split_clip', 'add_marker', 'note_duplicate', 'note_split', 'note_join',
                                      'note_legato', 'note_quantize')))
FIXED_GROUP = 'Fixed (mouse, wheel and editing keys)'
# The user's rebound keys, {action: tuple of Qt PortableText keys}; Window.apply_shortcut_bindings sets it.
_overrides = {}


def set_overrides(overrides):
    """Use these keys (PortableText) instead of the registry's for key_sequences()."""
    global _overrides
    _overrides = {action: tuple(keys) for action, keys in overrides.items()}


def default_key_sequences(action):
    """The registry's QAction bindings, ignoring the user's rebound keys."""
    from PySide6.QtGui import QKeySequence
    entry = BY_ACTION[action]
    if entry.standard:
        result = QKeySequence.keyBindings(getattr(QKeySequence.StandardKey, entry.standard))
        if entry.standard == 'Redo' and sys.platform != 'darwin':
            alternate = QKeySequence('Ctrl+Shift+Z')
            if alternate not in result:
                result.append(alternate)
        return result
    physical = entry.macos if sys.platform == 'darwin' else entry.windows
    if sys.platform == 'darwin':
        physical = physical.replace('Control+', 'Meta+').replace('Command+', 'Ctrl+').replace('Option+', 'Alt+')
    return [QKeySequence('Esc' if physical == 'Escape' else physical)]


def key_sequences(action):
    """QAction bindings: the user's rebound keys (UI2), else Qt's standard platform mapping
    where available, else the registry's keys."""
    if action in _overrides:
        from PySide6.QtGui import QKeySequence
        return [QKeySequence.fromString(key, QKeySequence.SequenceFormat.PortableText) for key in _overrides[action]]
    return default_key_sequences(action)


def display_keys(entry, platform=None):
    """The keys shown for entry: its rebound keys in the platform's own notation, else format_keys."""
    if entry.action not in _overrides: return format_keys(entry, platform)
    from PySide6.QtGui import QKeySequence
    keys = [QKeySequence.fromString(key, QKeySequence.SequenceFormat.PortableText).toString(QKeySequence.SequenceFormat.NativeText)
            for key in _overrides[entry.action]]
    return ' or '.join(keys) if keys else 'None'
