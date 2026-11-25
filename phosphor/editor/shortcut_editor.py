"""Preferences › Controls › Keyboard shortcuts (UI2): search, rebind, reset, export and import.

One list of every shortcut in shortcuts.SHORTCUTS, grouped by menu (shortcuts.MENU_GROUPS); the
rest (clicks, drags, wheel gestures and editing keys handled by the views) are listed under
FIXED_GROUP and cannot be changed. Edits apply and save at once, like the side panels; they are
not part of the dialog's OK / Cancel. The Window owns the bindings (Window.shortcut_bindings,
storage/shortcut_bindings.py) and provides the hooks used here, which smokes replace:
capture_shortcut, pick_shortcuts_file, ask and warn.
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QFont
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QLineEdit, QTreeWidget, QTreeWidgetItem, QPushButton, QLabel,
                               QAbstractItemView, QHeaderView, QDialog, QDialogButtonBox, QKeySequenceEdit)

from phosphor.editor.shortcuts import SHORTCUTS, BY_ACTION, MENU_GROUPS, FIXED_GROUP, format_keys, mapped_shortcuts
from phosphor.editor.panel_widgets import FlowRow
from phosphor.storage.shortcut_bindings import (SCOPES, ShortcutBindings, ShortcutConflict, export_bindings, import_bindings)

ACTION_ROLE = Qt.ItemDataRole.UserRole
# Keys the views handle themselves (copy, paste, delete, arrows, Return): never given to a shortcut,
# or the QAction would take them first (Codex review). Undo and Redo are rebindable actions.
FIXED_KEYS = (('Copy', 'copy'), ('Cut', 'cut'), ('Paste', 'paste'), ('SelectAll', 'select_all'))
FIXED_PLAIN = (('Del', 'delete'), ('Backspace', 'delete'), ('Return', 'builder_finish'), ('Enter', 'builder_finish'),
               *((key, 'transpose') for key in ('Up', 'Down')), *((key, 'move_notes') for key in ('Left', 'Right')),
               *(('Shift+' + key, 'octave') for key in ('Up', 'Down')), *(('Shift+' + key, 'move_notes') for key in ('Left', 'Right')))


def file_state(path):
    """The shortcuts file's bytes, or None when it does not exist (to notice changes made elsewhere)."""
    try: return path.read_bytes() if path is not None else None
    except FileNotFoundError: return None
    except OSError as error: return repr(error)


def canonical_key(key):
    """key as Qt writes it (PortableText, one chord); ValueError for a key Qt does not know."""
    sequence = QKeySequence.fromString(key, QKeySequence.SequenceFormat.PortableText)
    if sequence.count() != 1 or sequence[0].key() in (Qt.Key.Key_unknown, 0):
        raise ValueError(f'"{key}" is not a key phosphor knows.')
    return sequence.toString(QKeySequence.SequenceFormat.PortableText)


def fixed_keys():
    """{PortableText key: action} for keys the views keep (the platform's copy and paste keys included)."""
    portable = QKeySequence.SequenceFormat.PortableText
    keys = {s.toString(portable): action for standard, action in FIXED_KEYS
            for s in QKeySequence.keyBindings(getattr(QKeySequence.StandardKey, standard))}
    keys.update(FIXED_PLAIN)
    return keys


def checked_bindings(bindings):
    """bindings with every key in Qt's own spelling; ValueError for an unknown, duplicated or fixed key."""
    fixed, overrides = fixed_keys(), {}
    for action, keys in bindings.overrides.items():
        canonical = tuple(dict.fromkeys(canonical_key(key) for key in keys))
        for key in canonical:
            if key in fixed:
                raise ValueError(f'Shortcut "{BY_ACTION[action].description}": {native(key)} is kept for '
                                 f'"{BY_ACTION[fixed[key]].description}" and cannot be changed.')
        overrides[action] = canonical
    result = ShortcutBindings(overrides, bindings.platform, bindings.defaults)
    for a, b, key in result.has_conflicts():
        raise ValueError(f'"{BY_ACTION[a].description}" and "{BY_ACTION[b].description}" both use {native(key)}.')
    return result


def native(key):
    """PortableText key → the platform's own notation (⌘S on macOS, Ctrl+S elsewhere)."""
    return QKeySequence.fromString(key, QKeySequence.SequenceFormat.PortableText).toString(QKeySequence.SequenceFormat.NativeText)


def native_keys(keys):
    return ' or '.join(native(key) for key in keys) if keys else 'None'


def capture_dialog(parent, description, current):
    """Ask for one key with modifiers; the PortableText key, or None when cancelled."""
    dialog = QDialog(parent); dialog.setWindowTitle('Change shortcut — phosphor')
    layout = QVBoxLayout(dialog)
    label = QLabel(f'Press the new keys for “{description}” (now {current}).'); label.setWordWrap(True); layout.addWidget(label)
    edit = QKeySequenceEdit(dialog); edit.setMaximumSequenceLength(1); edit.setObjectName('shortcut_capture'); layout.addWidget(edit)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel); layout.addWidget(buttons)
    buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
    edit.editingFinished.connect(lambda: buttons.button(QDialogButtonBox.StandardButton.Ok).setFocus())
    edit.setFocus()
    if dialog.exec() != QDialog.DialogCode.Accepted or edit.keySequence().isEmpty(): return None
    return QKeySequence(edit.keySequence()[0]).toString(QKeySequence.SequenceFormat.PortableText)


class ShortcutEditor(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        layout = QVBoxLayout(self)
        caption = QLabel('Click a shortcut, then Change… (or double-click it) and press the new keys. Changes apply and '
                         'save at once, on this computer only. Mouse, wheel and editing keys are listed under Fixed.')
        caption.setWordWrap(True); layout.addWidget(caption)
        self.search = QLineEdit(); self.search.setObjectName('shortcut_search'); self.search.setPlaceholderText('Search actions or keys…')
        self.search.setClearButtonEnabled(True); layout.addWidget(self.search)
        self.tree = QTreeWidget(); self.tree.setObjectName('shortcut_tree'); self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(['Action', 'Keys']); self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setUniformRowHeights(True); layout.addWidget(self.tree, 1)
        row = FlowRow(); layout.addLayout(row); self.buttons = {}
        for key, text, tip in (('change', 'Change…', 'Press new keys for the selected shortcut'),
                               ('clear', 'Clear', 'Remove the selected shortcut’s keys'),
                               ('reset', 'Reset', 'Restore the selected shortcut’s default keys'),
                               ('reset_all', 'Reset all', 'Restore every shortcut’s default keys'),
                               ('export', 'Export…', 'Save your shortcuts to a file'),
                               ('import', 'Import…', 'Load shortcuts from a file exported by phosphor')):
            button = QPushButton(text); button.setObjectName('shortcut_' + key); button.setToolTip(tip)
            button.clicked.connect(lambda checked=False, key=key: getattr(self, key if key != 'import' else 'import_file')())
            row.addWidget(button); self.buttons[key] = button
        self.message = QLabel(); self.message.setObjectName('shortcut_message'); self.message.setWordWrap(True); layout.addWidget(self.message)
        self.search.textChanged.connect(lambda *args: self.refresh())
        self.tree.itemSelectionChanged.connect(self.sync_buttons)
        self.tree.itemDoubleClicked.connect(lambda item, column: self.change() if item.data(0, ACTION_ROLE) else None)
        self.refresh()

    # The list -------------------------------------------------------------------
    def refresh(self):
        selected = self.selected_action()
        bindings = self.window.shortcut_bindings
        words = self.search.text().casefold().split()
        def matches(*texts): return all(word in ' '.join(texts).casefold() for word in words)
        self.tree.clear()
        # Only the weight: the view fills in the rest from its own (stylesheet) font at paint time,
        # so a changed row follows a new style or font instead of keeping a copied one.
        bold = QFont(); bold.setBold(True)
        current = {e.action: e for e in mapped_shortcuts(getattr(self.window, 'preferences', None))}  # Wheel keys as set.
        for group, actions in (*MENU_GROUPS, (FIXED_GROUP, tuple(e.action for e in SHORTCUTS if e.action not in SCOPES))):
            parent = None
            for action in actions:
                entry = current[action]
                keys = native_keys(bindings.keys(action)) if action in SCOPES else format_keys(entry)
                if not matches(entry.description, keys, entry.where, group, action): continue
                if parent is None:
                    parent = QTreeWidgetItem(self.tree, [group]); parent.setFlags(Qt.ItemFlag.ItemIsEnabled); parent.setExpanded(True)
                item = QTreeWidgetItem(parent, [entry.description, keys]); item.setToolTip(0, entry.where); item.setToolTip(1, keys)
                if action in SCOPES:
                    item.setData(0, ACTION_ROLE, action)
                    if bindings.is_custom(action): item.setFont(1, bold); item.setToolTip(1, keys + ' (changed; default ' + native_keys(bindings.defaults[action]) + ')')
                    if action == selected: item.setSelected(True); self.tree.setCurrentItem(item)
                else: item.setFlags(Qt.ItemFlag.ItemIsEnabled); item.setDisabled(True)
            if parent is not None: parent.setExpanded(True)
        error = self.window.shortcut_error
        if error: self.message.setText(error + ' Your shortcuts use the defaults until Reset all replaces the file.')
        self.sync_buttons()

    def selected_action(self):
        items = self.tree.selectedItems() if hasattr(self, 'tree') else []
        return items[0].data(0, ACTION_ROLE) if items else None

    def select(self, action):
        """Select action's row (smokes and the conflict prompt use this)."""
        for item in self.items():
            if item.data(0, ACTION_ROLE) == action: self.tree.setCurrentItem(item); item.setSelected(True); return item

    def items(self):
        for i in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(i)
            for j in range(group.childCount()): yield group.child(j)

    def sync_buttons(self):
        action, damaged = self.selected_action(), bool(self.window.shortcut_error)
        custom = action is not None and self.window.shortcut_bindings.is_custom(action)
        for key, enabled in (('change', action is not None), ('clear', action is not None and bool(self.window.shortcut_bindings.keys(action))),
                             ('reset', custom), ('reset_all', damaged or bool(self.window.shortcut_bindings.overrides)),
                             ('export', not damaged), ('import', True)):
            self.buttons[key].setEnabled(enabled and (key in ('reset_all', 'import') or not damaged))

    # Edits ----------------------------------------------------------------------
    def commit(self, bindings, message='', replace=False):
        if self.window.set_shortcut_bindings(bindings, replace=replace): self.message.setText(message)
        self.refresh()

    def assign(self, action, keys, message):
        """Give action keys, asking before taking any from another shortcut (Change and Reset)."""
        bindings, description = self.window.shortcut_bindings, BY_ACTION[action].description
        fixed = fixed_keys()
        for key in keys:
            if key in fixed:
                self.window.warn('Shortcut', f'{native(key)} is kept for “{BY_ACTION[fixed[key]].description}” and cannot be changed.'); return
        try: updated = bindings.with_keys(action, keys)
        except ShortcutConflict as conflict:
            others = sorted({BY_ACTION[other].description for other, _ in conflict.conflicts})
            taken = native_keys(tuple(dict.fromkeys(key for _, key in conflict.conflicts)))
            text = (f'{taken} is already used by “{"”, “".join(others)}”. Use it for “{description}” instead? '
                    f'The other shortcut{"s lose" if len(others) > 1 else " loses"} this key.')
            if not self.window.ask('Shortcut in use', text): return
            updated = bindings.with_keys(action, keys, take_over=True)
        except ValueError as error: self.window.warn('Shortcut', str(error)); return
        self.commit(updated, message)

    def change(self):
        action = self.selected_action()
        if action is None or self.window.shortcut_error: return
        bindings, description = self.window.shortcut_bindings, BY_ACTION[action].description
        key = self.window.capture_shortcut(description, native_keys(bindings.keys(action)))
        if not key: return
        try: key = canonical_key(key)
        except ValueError as error: self.window.warn('Shortcut', str(error)); return
        self.assign(action, (key,), f'“{description}”: {native(key)}.')

    def clear(self):
        action = self.selected_action()
        if action is not None: self.commit(self.window.shortcut_bindings.with_keys(action, ()), f'“{BY_ACTION[action].description}” has no shortcut now.')

    def reset(self):
        action = self.selected_action()
        if action is not None:  # Through the conflict prompt: a takeover may have given its keys away (Codex review).
            self.assign(action, self.window.shortcut_bindings.defaults[action], f'“{BY_ACTION[action].description}” uses its default keys.')

    def reset_all(self):
        if not self.window.ask('Reset all shortcuts', 'Restore the default keys for every shortcut?'): return
        # One of the two edits that replace a damaged file; the damage clears only once the write succeeds.
        self.commit(self.window.shortcut_bindings.reset_all(), 'Every shortcut uses its default keys.', replace=True)

    def export(self):
        path = self.window.pick_shortcuts_file(True)
        if not path: return
        try: export_bindings(self.window.shortcut_bindings, path)
        except (OSError, ValueError) as error: self.window.warn('Export shortcuts', str(error)); return
        self.message.setText(f'Exported to {path}.')

    def import_file(self):
        path = self.window.pick_shortcuts_file(False)
        if not path: return
        notices = []
        try: bindings = checked_bindings(import_bindings(path, defaults=self.window.shortcut_bindings.defaults, notices=notices))
        except ValueError as error: self.window.warn('Import shortcuts', str(error)); return
        self.commit(bindings, ' '.join(['Imported.', *notices]), replace=True)  # An explicit import replaces a damaged file too.
