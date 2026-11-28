"""Searchable Quick-Add Palette (Cmd/Ctrl+K) for Objects, Equations, Transforms & Recipes."""
from dataclasses import dataclass

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QKeySequence
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout,
)

from phosphor.engine.function_plot import PARAMETRIC_GROUPS, PARAMETRIC_OBJECT
from phosphor.engine.matrix_rain import MATRIX_OBJECT
from phosphor.engine.model_object import MODEL_OBJECT
from phosphor.engine.recurrence import RECURRENCE_GROUPS, RECURRENCE_OBJECT
from phosphor.engine.solids import ANIMATED_SOLIDS, OBJECT_GROUPS, PARAMETRIC_SOLIDS
from phosphor.engine.transform_presets import BUILTIN_RECIPES
from phosphor.engine.transforms import HELP as TRANSFORM_HELP, TRANSFORM_GROUPS
from phosphor.engine.vector_text import TEXT_OBJECT
from phosphor.engine.custom_shape import CUSTOM_OBJECT

FILTER_ALL = 'All'
FILTER_OBJECTS = 'Objects'
FILTER_EQUATIONS = 'Equations'
FILTER_TRANSFORMS = 'Transforms'
FILTERS = (FILTER_ALL, FILTER_OBJECTS, FILTER_EQUATIONS, FILTER_TRANSFORMS)

DATA_ROLE = Qt.ItemDataRole.UserRole


@dataclass(frozen=True)
class QuickAddEntry:
    label: str
    badge: str
    bucket: str        # Objects / Equations / Transforms
    action: str        # object / svg / parametric / recurrence / transform / recipe / saved_transform
    payload: object
    keywords: str = ''
    tooltip: str = ''
    needs_object: bool = False


def collect_entries(function_panel=None, recurrence_panel=None, transform_panel=None):
    """Build the complete searchable catalog of objects, equation presets, transforms, and recipes."""
    entries = []

    # 1. Built-in 2D, 3D, Curves & Math/Generative Objects
    for cat_title, subgroups in OBJECT_GROUPS:
        for sub_title, names in subgroups:
            for name in names:
                extra = 'Animated' if name in ANIMATED_SOLIDS else ('Configurable' if name in PARAMETRIC_SOLIDS else '')
                badge = f'{cat_title} · {sub_title}' + (f' · {extra}' if extra else '')
                entries.append(QuickAddEntry(
                    label=name, badge=badge, bucket=FILTER_OBJECTS, action='object', payload=name,
                    keywords=f'{cat_title} {sub_title} {extra}', tooltip=badge,
                ))
    for name, badge, action, payload, kw in (
        (TEXT_OBJECT, 'Math & Generative · Vector Text', 'object', TEXT_OBJECT, 'text letters font'),
        (MATRIX_OBJECT, 'Math & Generative · Digital Rain', 'object', MATRIX_OBJECT, 'matrix rain glyphs animated'),
        (PARAMETRIC_OBJECT, 'Math & Generative · Blank x(t,s)', 'object', PARAMETRIC_OBJECT, 'parametric equation formula'),
        (RECURRENCE_OBJECT, 'Math & Generative · Blank x[n]', 'object', RECURRENCE_OBJECT, 'recurrence recursion attractor'),
        ('Custom shape…', 'Custom & Import · Shape Builder', 'object', CUSTOM_OBJECT, 'custom draw builder'),
        ('3D model…', 'Custom & Import · OBJ / STL / GLB', 'object', MODEL_OBJECT, 'import 3d mesh model'),
        ('SVG drawing…', 'Custom & Import · Vector SVG', 'svg', None, 'import svg vector'),
    ):
        entries.append(QuickAddEntry(label=name, badge=badge, bucket=FILTER_OBJECTS, action=action, payload=payload, keywords=kw, tooltip=badge))

    # 2. Parametric & Recurrence Equation Presets (Built-in + User Saved)
    f_builtins = function_panel.store.builtins if function_panel is not None else {}
    f_saved = function_panel.saved if function_panel is not None else {}
    for group_title, names in PARAMETRIC_GROUPS:
        for name in names:
            plot = f_builtins.get(name)
            if plot is None: continue
            dim = '3D' if plot.z != '0' else '2D'
            tip = f'x = {plot.x}\ny = {plot.y}' + (f'\nz = {plot.z}' if plot.z != '0' else '')
            entries.append(QuickAddEntry(
                label=name, badge=f'Parametric · {group_title}', bucket=FILTER_EQUATIONS,
                action='parametric', payload=('builtin', name),
                keywords=f'parametric equation {group_title} {dim}', tooltip=tip,
            ))
    for name, plot in f_saved.items():
        tip = f'x = {plot.x}\ny = {plot.y}' + (f'\nz = {plot.z}' if plot.z != '0' else '')
        entries.append(QuickAddEntry(
            label=f'★ {name}', badge='Parametric · Saved', bucket=FILTER_EQUATIONS,
            action='parametric', payload=('saved', name),
            keywords='parametric saved user equation', tooltip=tip,
        ))

    r_builtins = recurrence_panel.store.builtins if recurrence_panel is not None else {}
    r_saved = recurrence_panel.saved if recurrence_panel is not None else {}
    for group_title, names in RECURRENCE_GROUPS:
        for name in names:
            plot = r_builtins.get(name)
            if plot is None: continue
            tip = f'x = {plot.x}\ny = {plot.y}' + (f'\nz = {plot.z}' if plot.z != '0' else '')
            entries.append(QuickAddEntry(
                label=name, badge=f'Recurrence · {group_title} · {plot.draw}', bucket=FILTER_EQUATIONS,
                action='recurrence', payload=('builtin', name),
                keywords=f'recurrence recursion attractor chaos {group_title} {plot.draw}', tooltip=tip,
            ))
    for name, plot in r_saved.items():
        tip = f'x = {plot.x}\ny = {plot.y}' + (f'\nz = {plot.z}' if plot.z != '0' else '')
        entries.append(QuickAddEntry(
            label=f'★ {name}', badge='Recurrence · Saved', bucket=FILTER_EQUATIONS,
            action='recurrence', payload=('saved', name),
            keywords='recurrence saved user equation', tooltip=tip,
        ))

    # 3. Transforms, Built-in Recipes & User Saved Transforms
    for group_title, kinds in TRANSFORM_GROUPS:
        for kind in kinds:
            entries.append(QuickAddEntry(
                label=kind, badge=f'Transform · {group_title}', bucket=FILTER_TRANSFORMS,
                action='transform', payload=kind,
                keywords=f'transform effect fx {group_title} {TRANSFORM_HELP.get(kind, "")}',
                tooltip=TRANSFORM_HELP.get(kind, ''), needs_object=True,
            ))
    for name, preset in BUILTIN_RECIPES.items():
        chain = ' → '.join(t.kind for t in preset.transforms)
        entries.append(QuickAddEntry(
            label=name, badge=f'Recipe · {chain}', bucket=FILTER_TRANSFORMS,
            action='recipe', payload=name,
            keywords=f'recipe stack preset transform {chain}', tooltip=chain, needs_object=True,
        ))
    if transform_panel is not None:
        for name, preset in transform_panel.saved.items():
            chain = ' → '.join(t.kind for t in preset.transforms)
            entries.append(QuickAddEntry(
                label=f'★ {name}', badge=f'Saved Transform · {chain}', bucket=FILTER_TRANSFORMS,
                action='saved_transform', payload=name,
                keywords=f'saved transform preset {chain}', tooltip=chain, needs_object=True,
            ))

    return entries


def filter_entries(entries, query, bucket=FILTER_ALL):
    """Filter and rank entries: prefix matches first, then label substring, then metadata matches."""
    words = query.strip().casefold().split()
    scored = []
    for idx, entry in enumerate(entries):
        if bucket != FILTER_ALL and entry.bucket != bucket:
            continue
        label_cf = entry.label.casefold()
        haystack = f'{label_cf} {entry.badge.casefold()} {entry.keywords.casefold()}'
        if words and not all(w in haystack for w in words):
            continue
        if not words:
            rank = 0
        elif label_cf.startswith(words[0]):
            rank = 0
        elif all(w in label_cf for w in words):
            rank = 1
        else:
            rank = 2
        scored.append((rank, idx, entry))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [entry for _, _, entry in scored]


class QuickAddDialog(QDialog):
    """Fast searchable palette for adding any object, equation preset, transform, or recipe."""

    def __init__(self, entries, has_object=True, initial_filter=FILTER_ALL, parent=None):
        super().__init__(parent)
        self.setObjectName('quick_add_dialog')
        self.setWindowTitle('Quick Add')
        self.setModal(True)
        self.resize(520, 440)
        self._entries = list(entries)
        self._has_object = bool(has_object)
        self._bucket = initial_filter if initial_filter in FILTERS else FILTER_ALL
        self.chosen = None

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        self.search = QLineEdit(self)
        self.search.setObjectName('quick_add_search')
        self.search.setPlaceholderText('Search 2D, 3D, equations, transforms, recipes…  (↑/↓ Enter)')
        self.search.setClearButtonEnabled(True)
        self.search.installEventFilter(self)
        root.addWidget(self.search)

        pill_row = QHBoxLayout()
        pill_row.setSpacing(4)
        self._filter_group = QButtonGroup(self)
        self._filter_buttons = {}
        for name in FILTERS:
            btn = QPushButton(name, self)
            btn.setCheckable(True)
            btn.setObjectName(f'quick_add_filter_{name.lower()}')
            btn.setChecked(name == self._bucket)
            btn.clicked.connect(lambda _=False, n=name: self._set_bucket(n))
            self._filter_group.addButton(btn)
            self._filter_buttons[name] = btn
            pill_row.addWidget(btn)
        pill_row.addStretch(1)
        self.count_label = QLabel('', self)
        pill_row.addWidget(self.count_label)
        root.addLayout(pill_row)

        self.list = QListWidget(self)
        self.list.setObjectName('quick_add_list')
        self.list.setUniformItemSizes(True)
        root.addWidget(self.list, 1)

        footer = QHBoxLayout()
        self.hint = QLabel('Enter to add · Esc to close' + ('' if self._has_object else ' · Select an object to add transforms'), self)
        footer.addWidget(self.hint, 1)
        self.add_btn = QPushButton('Add', self)
        self.add_btn.setDefault(True)
        self.add_btn.clicked.connect(self._accept_current)
        cancel_btn = QPushButton('Cancel', self)
        cancel_btn.clicked.connect(self.reject)
        footer.addWidget(self.add_btn)
        footer.addWidget(cancel_btn)
        root.addLayout(footer)

        self.search.textChanged.connect(self._refresh)
        self.list.itemActivated.connect(lambda _: self._accept_current())
        self.list.currentItemChanged.connect(lambda *_: self._sync_add_enabled())

        self._refresh()
        self.search.setFocus()

    def _set_bucket(self, name):
        self._bucket = name
        self._refresh()
        self.search.setFocus()

    def _refresh(self):
        matches = filter_entries(self._entries, self.search.text(), self._bucket)
        self.list.clear()
        first_enabled = -1
        for entry in matches:
            enabled = self._has_object or not entry.needs_object
            suffix = '' if enabled else '  (select an object first)'
            text = f'{entry.label}    —  {entry.badge}{suffix}'
            item = QListWidgetItem(text)
            item.setData(DATA_ROLE, entry)
            item.setToolTip(entry.tooltip or entry.badge)
            if not enabled:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                item.setForeground(QColor('#777777'))
            elif first_enabled < 0:
                first_enabled = self.list.count()
            self.list.addItem(item)
        if first_enabled >= 0:
            self.list.setCurrentRow(first_enabled)
        self.count_label.setText(f'{len(matches)} items')
        self._sync_add_enabled()

    def _sync_add_enabled(self):
        item = self.list.currentItem()
        self.add_btn.setEnabled(item is not None and bool(item.flags() & Qt.ItemFlag.ItemIsEnabled))

    def _accept_current(self):
        item = self.list.currentItem()
        if item is None or not (item.flags() & Qt.ItemFlag.ItemIsEnabled):
            return
        self.chosen = item.data(DATA_ROLE)
        self.accept()

    def eventFilter(self, watched, event):
        if watched is self.search and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                count = self.list.count()
                if count > 0:
                    step = 1 if key == Qt.Key.Key_Down else -1
                    row = self.list.currentRow()
                    for _ in range(count):
                        row = (row + step) % count
                        if self.list.item(row).flags() & Qt.ItemFlag.ItemIsEnabled:
                            self.list.setCurrentRow(row)
                            break
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._accept_current()
                return True
        return super().eventFilter(watched, event)
