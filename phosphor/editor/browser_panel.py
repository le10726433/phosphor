"""The browser panel (AW4): a collapsible panel left of the scope with five tabs.

Files lists a folder's subfolders and audio files (Places, favourites, Recent audio, a search
below the folder), shows the selected file's length, rate and channels, previews it through the
output at its own level, and drags or adds files into Composer. Scenes, Equations, Transforms and
Models list what the app can open or insert. Folder listing, search, file headers and preview
decoding run on one BrowseWorker thread; the GUI only polls its results.
"""
import os
from pathlib import Path

from PySide6.QtCore import QEvent, QMimeData, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMenu, QPushButton, QSizePolicy, QSlider,
                               QStackedWidget, QStyle, QTabWidget, QToolButton, QVBoxLayout, QWidget)

from phosphor.engine import audio_browse
from phosphor.engine.audio_browse import (AUDIO_TYPES, MODEL_TYPES, SCENE_TYPES, BrowseWorker, audio_info,
                                          describe, list_folder, read_preview, search, standard_places)
from phosphor.storage import browser_places, recent_files

PATH, KIND = Qt.ItemDataRole.UserRole, Qt.ItemDataRole.UserRole + 1
FOLDER, FILE, HEADING = 'folder', 'file', 'heading'
RECENT = 'recent'  # The Places entry for Recent audio.
SEARCH_DELAY_MS = 250
AUTO_DELAY_MS = 180  # Auto preview waits until the selection settles (arrow keys held).
POLL_MS = 40
DEFAULT_LEVEL = 70  # The preview level slider, 0–100 (a gain of 0.7).
TABS = ('Files', 'Scenes', 'Equations', 'Transforms', 'Models', 'Objects')


def file_urls(paths):
    mime = QMimeData(); mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths]); return mime


class FileList(QListWidget):
    """Folders and files; dragging selected files carries their URLs (Composer's audio drop)."""
    space_pressed = Signal()
    back_pressed = Signal()

    def __init__(self):
        super().__init__()
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True); self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setUniformItemSizes(True); self.setTextElideMode(Qt.TextElideMode.ElideMiddle)

    def selected_files(self):
        return [Path(item.data(PATH)) for item in self.selectedItems() if item.data(KIND) == FILE]

    def mimeTypes(self): return ['text/uri-list']

    def mimeData(self, items):
        return file_urls([item.data(PATH) for item in items if item.data(KIND) == FILE])

    def supportedDropActions(self): return Qt.DropAction.CopyAction

    def event(self, event):
        if (event.type() == QEvent.Type.ShortcutOverride and event.modifiers() == Qt.KeyboardModifier.NoModifier
                and event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Backspace)):
            event.accept(); return True  # Space previews here instead of the window's Play.
        return super().event(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space: self.space_pressed.emit(); return
        if event.key() == Qt.Key.Key_Backspace: self.back_pressed.emit(); return
        super().keyPressEvent(event)


class LibraryPage(QWidget):
    """A filter box over a list of named entries; activating one calls activate(data)."""
    def __init__(self, hint, button, activate, menu=None):
        super().__init__()
        self.activate, self.menu_for = activate, menu
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 6, 0, 0); layout.setSpacing(6)
        self.filter = QLineEdit(); self.filter.setPlaceholderText('Filter'); self.filter.setClearButtonEnabled(True)
        self.list = FileList(); self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.hint = QLabel(hint); self.hint.setWordWrap(True); self.hint.setObjectName('browser_hint')
        self.button = QPushButton(button); self.button.setEnabled(False)
        for widget in (self.filter, self.list, self.hint, self.button): layout.addWidget(widget, 1 if widget is self.list else 0)
        self.entries = []
        self.filter.textChanged.connect(self.show_entries)
        self.list.itemActivated.connect(lambda item: self.run(item))
        self.list.currentItemChanged.connect(lambda item, old: self.button.setEnabled(item is not None and item.data(KIND) != HEADING))
        self.button.clicked.connect(lambda: self.run(self.list.currentItem()))
        if menu is not None:
            self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.list.customContextMenuRequested.connect(self.open_menu)

    def set_entries(self, entries):
        """entries: (label, tooltip, data) rows; data None makes a heading."""
        self.entries = list(entries); self.show_entries()

    def show_entries(self):
        words = self.filter.text().casefold().split()
        current = self.list.currentItem(); keep = current.data(PATH) if current is not None else None
        self.list.clear(); pending = None; active_heading = ''
        for label, tip, data in self.entries:
            if data is None: pending = label; active_heading = label; continue
            haystack = f'{label} {active_heading} {tip}'.casefold()
            if words and not all(word in haystack for word in words): continue
            if pending is not None:
                heading = QListWidgetItem(pending); heading.setData(KIND, HEADING)
                heading.setFlags(Qt.ItemFlag.NoItemFlags); self.list.addItem(heading); pending = None
            item = QListWidgetItem(label); item.setToolTip(tip); item.setData(PATH, data); item.setData(KIND, FILE)
            self.list.addItem(item)
            if data == keep: self.list.setCurrentItem(item)
        self.button.setEnabled(self.list.currentItem() is not None)

    def names(self):
        return [self.list.item(i).text() for i in range(self.list.count()) if self.list.item(i).data(KIND) != HEADING]

    def item_named(self, label):
        return next((self.list.item(i) for i in range(self.list.count())
                     if self.list.item(i).text() == label and self.list.item(i).data(KIND) != HEADING), None)

    def run(self, item):
        if item is not None and item.data(KIND) != HEADING: self.activate(item.data(PATH))

    def open_menu(self, point):
        item = self.list.itemAt(point)
        if item is None or item.data(KIND) == HEADING: return
        menu = self.menu_for(item.data(PATH))
        if menu is not None: menu.exec(self.list.viewport().mapToGlobal(point)); menu.deleteLater()


class LeftPanel(QWidget):
    def __init__(self, browser, effects):
        super().__init__()
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(6)
        self.title = QLabel('LIBRARY'); self.title.setObjectName('panel_title'); layout.addWidget(self.title)
        toggle = QHBoxLayout(); toggle.setSpacing(0); layout.addLayout(toggle)
        self.group = QButtonGroup(self)
        self.stack = QStackedWidget(); layout.addWidget(self.stack, 1)
        self.buttons = {}
        for name, page in (('Browser', browser), ('Effects', effects)):
            button = QPushButton(name); button.setCheckable(True)
            button.setObjectName('library_' + name.lower())
            index = self.stack.addWidget(page)
            self.group.addButton(button, index); toggle.addWidget(button, 1)
            self.buttons[name.lower()] = button
        self.buttons['browser'].setChecked(True)
        self.group.idClicked.connect(self.stack.setCurrentIndex)
        self.stack.currentChanged.connect(lambda index: self.group.button(index).setChecked(True))


class BrowserPanel(QWidget):
    """owner is the Window: it adds clips, opens scenes and models, inserts presets and owns audio."""
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.setObjectName('browser_panel')
        self.worker = BrowseWorker()
        self.folder = None          # The Files tab's folder, or None while showing Recent audio.
        self.places_error = ''
        self.preview_path = None    # The file being decoded or played.
        self.preview_wanted = None  # (path, rate) waiting for the worker.
        self.pending_tokens = {}
        self.favourite_list = []
        self.library_recent = {}   # kind -> recent files, read on the worker.
        self.library_here = {}     # kind -> the Files folder's files of that kind, listed on the worker.
        self.preview_rate = None   # The rate the device really opened at, after a fallback (review 8).
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(6)
        self.tabs = QTabWidget(); self.tabs.setObjectName('browser_tabs'); self.tabs.setDocumentMode(True)
        self.tabs.setUsesScrollButtons(True); layout.addWidget(self.tabs, 1)
        self.build_files()
        self.scenes = LibraryPage('Recent scenes and the scenes in the Files folder. Double-click opens one in Sandbox.',
                                  'Open in Sandbox', self.open_scene)
        self.equations = LibraryPage('Built-in and saved Parametric & Recurrence presets. Double-click adds an object with it.',
                                     'Add Equation object', self.add_equation)
        self.transforms = LibraryPage('Built-in Recipes and saved steps/stacks (right-click a step › Save step…). '
                                      'Double-click inserts into the selected object.',
                                      'Insert into object', self.insert_transform, self.transform_menu)
        self.models = LibraryPage('Recent models and the model files in the Files folder. Double-click imports one.',
                                  'Import…', self.import_model)
        self.objects = LibraryPage('Built-in 2D shapes, 3D solids, curves and generative objects. Double-click adds one.',
                                   'Add object', self.add_object)
        for name, page in zip(TABS[1:], (self.scenes, self.equations, self.transforms, self.models, self.objects)):
            page.setObjectName('browser_' + name.lower()); self.tabs.addTab(page, name)
        self.tabs.currentChanged.connect(lambda index: self.refresh_tab())
        self.poll = QTimer(self); self.poll.setInterval(POLL_MS); self.poll.timeout.connect(self.take_results)
        self.search_timer = QTimer(self); self.search_timer.setSingleShot(True); self.search_timer.setInterval(SEARCH_DELAY_MS)
        self.search_timer.timeout.connect(self.start_search)
        self.auto_timer = QTimer(self); self.auto_timer.setSingleShot(True); self.auto_timer.setInterval(AUTO_DELAY_MS)
        self.auto_timer.timeout.connect(self.auto_preview)

    # Files tab ---------------------------------------------------------------------
    def build_files(self):
        page = QWidget(); page.setObjectName('browser_files'); self.tabs.addTab(page, TABS[0])
        layout = QVBoxLayout(page); layout.setContentsMargins(0, 6, 0, 0); layout.setSpacing(6)
        row = QHBoxLayout(); row.setSpacing(4); layout.addLayout(row)
        self.places = QComboBox(); self.places.setObjectName('browser_places')
        self.places.setMinimumContentsLength(1); self.places.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.places.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.places.setToolTip('Places, favourite folders and Recent audio')
        self.up = QToolButton(); self.up.setText('↑'); self.up.setToolTip('Up one folder (Backspace)')
        self.star = QToolButton(); self.star.setCheckable(True); self.star.setText('☆')
        self.star.setToolTip('Keep this folder in Places as a favourite')
        row.addWidget(self.places, 1); row.addWidget(self.up); row.addWidget(self.star)
        self.path_label = QLabel(); self.path_label.setObjectName('browser_path')
        self.path_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred); layout.addWidget(self.path_label)
        self.filter = QLineEdit(); self.filter.setPlaceholderText('Search below this folder'); self.filter.setClearButtonEnabled(True)
        layout.addWidget(self.filter)
        self.list = FileList(); self.list.setObjectName('browser_list'); layout.addWidget(self.list, 1)
        self.list.setToolTip('Drag files onto an audio track, or onto Add track for a new one')
        self.message = QLabel(); self.message.setObjectName('browser_message'); self.message.setWordWrap(True)
        self.message.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred); layout.addWidget(self.message)
        self.info = QLabel(); self.info.setObjectName('browser_info')
        self.info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred); layout.addWidget(self.info)
        controls = QHBoxLayout(); controls.setSpacing(6); layout.addLayout(controls)
        self.play = QToolButton(); self.play.setObjectName('browser_preview'); self.play.setText('▶')
        self.play.setToolTip('Preview the selected file through the output (Space)')
        self.auto = QCheckBox('Auto'); self.auto.setToolTip('Preview each file you select')
        self.level = QSlider(Qt.Orientation.Horizontal); self.level.setRange(0, 100); self.level.setValue(DEFAULT_LEVEL)
        self.level.setObjectName('browser_level'); self.level.setToolTip('Preview level (the song’s level is unchanged)')
        self.level.setMinimumWidth(40)
        self.add = QPushButton('Add'); self.add.setToolTip('Add the selected files at the playhead on the selected track (Enter)')
        controls.addWidget(self.play); controls.addWidget(self.auto); controls.addWidget(self.level, 1); controls.addWidget(self.add)
        self.places.activated.connect(self.place_chosen)
        self.up.clicked.connect(self.go_up)
        self.star.clicked.connect(self.toggle_favourite)
        self.filter.textChanged.connect(self.filter_changed)
        self.list.itemActivated.connect(self.activated)
        self.list.currentItemChanged.connect(lambda item, old: self.selection_changed())
        self.list.space_pressed.connect(self.toggle_preview)
        self.list.back_pressed.connect(self.go_up)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self.open_file_menu)
        self.play.clicked.connect(self.toggle_preview)
        self.level.valueChanged.connect(self.level_changed)
        self.add.clicked.connect(lambda: self.add_files(self.list.selected_files()))

    def favourites(self):
        """The favourite folders read by the last rebuild_places (browser.json is a local user file)."""
        return self.favourite_list

    def read_favourites(self):
        self.places_error = ''; self.favourite_list = []
        path = self.owner.browser_places_path
        if path is None: return
        try: self.favourite_list = browser_places.read_favourites(path)
        except (OSError, ValueError) as error: self.places_error = f'Favourites could not be read: {error}'

    def request_recent(self, kind):
        """Recent files resolve and check every entry (maybe on a share): read them on the worker."""
        if self.owner.remember_recent: self.submit('recent:' + kind, _read_recent, kind, self.owner.recent_path)
        else: self.got_recent([], None, 'recent:' + kind)

    def cancel(self, channel):
        """Drop channel's pending result (a running stoppable job is told to stop)."""
        self.worker.submit(channel, _noop); self.pending_tokens.pop(channel, None)

    def rebuild_places(self):
        self.read_favourites()
        self.places.clear()
        for label, path in standard_places():
            self.places.addItem(label, str(path)); self.places.setItemData(self.places.count() - 1, str(path), Qt.ItemDataRole.ToolTipRole)
        favourites = self.favourite_list
        if favourites:
            self.places.insertSeparator(self.places.count())
            for folder in favourites:
                self.places.addItem('★ ' + (folder.name or str(folder)), str(folder))
                self.places.setItemData(self.places.count() - 1, str(folder), Qt.ItemDataRole.ToolTipRole)
        self.places.insertSeparator(self.places.count())
        self.places.addItem('Recent audio', RECENT)
        self.sync_place()

    def sync_place(self):
        target = RECENT if self.folder is None else str(self.folder)
        index = self.places.findData(target)
        self.places.blockSignals(True)
        if index >= 0: self.places.setCurrentIndex(index)
        else:
            self.places.setCurrentIndex(-1)
            self.places.setPlaceholderText(self.folder.name or str(self.folder) if self.folder else '')
        self.places.blockSignals(False)
        favourite = self.folder is not None and any(_same(self.folder, f) for f in self.favourites())
        self.star.setChecked(favourite); self.star.setText('★' if favourite else '☆')
        self.star.setEnabled(self.folder is not None and self.owner.browser_places_path is not None and not self.places_error)
        self.up.setEnabled(self.folder is not None and self.folder.parent != self.folder)
        self.path_label.setText(str(self.folder) if self.folder is not None else 'Audio files you added to projects')
        self.path_label.setToolTip(self.path_label.text())
        if self.places_error: self.message.setText(self.places_error)

    def place_chosen(self, index):
        data = self.places.itemData(index)
        if data == RECENT: self.show_recent()
        elif data: self.open_folder(Path(data))

    def start(self, folder=None):
        """First show: Places, then folder (default: Music, else Home)."""
        if folder is None:
            places = dict(standard_places())
            folder = places.get('Music') or places.get('Home') or Path.home()
        self.rebuild_places(); self.open_folder(Path(folder))

    def open_folder(self, folder):
        self.folder = Path(folder); self.filter.blockSignals(True); self.filter.clear(); self.filter.blockSignals(False)
        self.search_timer.stop(); self.cancel('search')  # Stops a running search.
        self.sync_place(); self.list.clear(); self.message.setText('Reading…')
        self.submit('list', list_folder, self.folder, AUDIO_TYPES)
        if self.tabs.currentIndex() in (1, 4): self.refresh_tab()
        self.owner.browser_folder_changed(self.folder)

    def show_recent(self):
        self.folder = None; self.filter.blockSignals(True); self.filter.clear(); self.filter.blockSignals(False)
        self.search_timer.stop(); self.cancel('list'); self.cancel('search')  # A late listing must not replace it (review 4).
        self.sync_place(); self.list.clear(); self.message.setText('Reading…')
        self.request_recent('audio')

    def go_up(self):
        if self.folder is not None and self.folder.parent != self.folder: self.open_folder(self.folder.parent)

    def toggle_favourite(self):
        path = self.owner.browser_places_path
        if self.folder is None or path is None: return
        try:
            if any(_same(self.folder, f) for f in self.favourites()): browser_places.remove_favourite(self.folder, path)
            else: browser_places.add_favourite(self.folder, path)
        except (OSError, ValueError) as error: self.message.setText(str(error))
        self.rebuild_places()

    def fill(self, folders, files, relative_to=None):
        self.list.clear()
        style = self.style()
        folder_icon, file_icon = style.standardIcon(QStyle.StandardPixmap.SP_DirIcon), style.standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        for path in folders:
            item = QListWidgetItem(folder_icon, path.name or str(path)); item.setData(PATH, str(path)); item.setData(KIND, FOLDER)
            item.setToolTip(str(path)); self.list.addItem(item)
        for path in files:
            label = path.name
            if relative_to is not None:
                try: label = str(path.relative_to(relative_to))
                except ValueError: pass
            item = QListWidgetItem(file_icon, label); item.setData(PATH, str(path)); item.setData(KIND, FILE)
            item.setToolTip(str(path)); self.list.addItem(item)
        self.info.setText('')

    def filter_changed(self, text):
        if self.folder is None:
            words = text.casefold().split()
            for i in range(self.list.count()):
                item = self.list.item(i); item.setHidden(bool(words) and not all(w in item.text().casefold() for w in words))
            return
        self.search_timer.start()

    def start_search(self):
        if self.folder is None: return
        text = self.filter.text().strip()
        if not text: self.open_folder(self.folder); return
        self.list.clear(); self.message.setText('Searching…')
        self.submit('search', search, self.folder, text, AUDIO_TYPES, stoppable=True)

    def activated(self, item):
        if item.data(KIND) == FOLDER: self.open_folder(Path(item.data(PATH)))
        elif item.data(KIND) == FILE: self.add_files(self.list.selected_files() or [Path(item.data(PATH))])

    def selection_changed(self):
        item = self.list.currentItem()
        if item is None or item.data(KIND) != FILE: self.info.setText(''); return
        self.info.setText('…'); self.submit('info', audio_info, Path(item.data(PATH)))
        if self.auto.isChecked(): self.auto_timer.start()

    def auto_preview(self):
        item = self.list.currentItem()
        if self.auto.isChecked() and item is not None and item.data(KIND) == FILE: self.start_preview(Path(item.data(PATH)))

    def add_files(self, paths):
        if not paths: return False
        if not self.owner.browser_can_add():
            self.message.setText('Switch to Composer to add audio clips.'); return False
        return self.owner.add_audio_clip_files([str(p) for p in paths])

    def build_file_menu(self, item):
        menu = QMenu(self)
        if item.data(KIND) == FOLDER:
            menu.addAction('Open', lambda: self.open_folder(Path(item.data(PATH))))
            path = self.owner.browser_places_path
            favourite = menu.addAction('Add to favourites', lambda: self.add_favourite(Path(item.data(PATH))))
            favourite.setEnabled(path is not None)
            return menu
        files = self.list.selected_files() or [Path(item.data(PATH))]
        can = self.owner.browser_can_add()
        menu.addAction('Preview', lambda: self.start_preview(Path(item.data(PATH))))
        menu.addAction('Add at playhead', lambda: self.add_files(files)).setEnabled(can)
        menu.addAction('Add to a new track', lambda: self.owner.add_audio_clip_files([str(p) for p in files], track=-1)).setEnabled(can)
        if self.folder is None:
            menu.addAction('Open containing folder', lambda: self.open_folder(Path(item.data(PATH)).parent))
        return menu

    def add_favourite(self, folder):
        try: browser_places.add_favourite(folder, self.owner.browser_places_path)
        except (OSError, ValueError) as error: self.message.setText(str(error))
        self.rebuild_places()

    def open_file_menu(self, point):
        item = self.list.itemAt(point)
        if item is None: return
        menu = self.build_file_menu(item); menu.exec(self.list.viewport().mapToGlobal(point)); menu.deleteLater()

    # Preview -------------------------------------------------------------------------
    def level_value(self): return self.level.value() / 100

    def level_changed(self, value):
        self.owner.audio.preview_level = value / 100

    def previewing(self):
        return self.preview_wanted is not None or self.owner.audio.preview_running

    def toggle_preview(self):
        if self.previewing(): self.stop_preview(); return
        files = self.list.selected_files()
        item = self.list.currentItem()
        path = Path(item.data(PATH)) if item is not None and item.data(KIND) == FILE else (files[0] if files else None)
        if path is not None: self.start_preview(path)

    def start_preview(self, path):
        reason = self.owner.preview_blocked()
        if reason: self.message.setText(reason); return False
        self.stop_preview()
        rate = self.preview_rate or self.owner.audio.expected_rate()
        self.preview_path, self.preview_wanted = Path(path), (Path(path), rate)
        self.submit('preview', read_preview, Path(path), rate)
        self.sync_preview(); return True

    def stop_preview(self):
        self.preview_wanted = None; self.auto_timer.stop()
        self.cancel('preview')  # Drops a decode that has not started.
        if self.owner.audio.preview_running: self.owner.stop_preview()
        self.preview_path = None; self.sync_preview()

    def sync_preview(self):
        on = self.previewing()
        self.play.setText('■' if on else '▶')
        self.play.setToolTip(('Stop the preview' if on else 'Preview the selected file through the output') + ' (Space)')
        if on and not self.poll.isActive(): self.poll.start()

    # Worker results ------------------------------------------------------------------
    def submit(self, channel, fn, *args, stoppable=False):
        self.pending_tokens[channel] = self.worker.submit(channel, fn, *args, stoppable=stoppable)
        if not self.poll.isActive(): self.poll.start()

    def take_results(self):
        for result in self.worker.take():
            if self.pending_tokens.get(result.channel) != result.token: continue
            del self.pending_tokens[result.channel]
            handler = getattr(self, 'got_' + result.channel.split(':')[0])
            handler(result.value, result.error, result.channel)
        audio = self.owner.audio
        retry = getattr(audio, 'preview_retry_rate', None)
        if retry and self.preview_path is not None and self.preview_wanted is None:
            # The device opened at another rate: decode again at that rate, and use it from now on.
            audio.preview_retry_rate = None; self.preview_rate = retry
            self.preview_wanted = (self.preview_path, retry)
            self.submit('preview', read_preview, self.preview_path, retry)
        if self.preview_path is not None and self.preview_wanted is None and not audio.preview_running:
            self.preview_path = None  # Finished by itself.
        self.sync_preview()
        if not self.pending_tokens and not self.previewing(): self.poll.stop()

    def got_list(self, listing, error, channel):
        if error is not None: self.message.setText(str(error)); return
        self.fill(listing.folders, listing.files)
        self.message.setText(listing.error or ('' if self.list.count() else 'No folders or audio files here.'))

    def got_search(self, result, error, channel):
        if error is not None: self.message.setText(str(error)); return
        if result is None: return
        self.fill([], result.files, self.folder)
        count = len(result.files)
        self.message.setText(('No matches' if not count else f'{count} match' + ('es' if count != 1 else ''))
                             + (' (search stopped at the limit)' if result.truncated else '') + ('.' if not count else ''))

    def got_info(self, info, error, channel):
        self.info.setText('Not a readable audio file.' if error is not None or info is None else describe(info))

    def got_preview(self, samples, error, channel):
        wanted, self.preview_wanted = self.preview_wanted, None
        if wanted is None: return
        if error is not None or samples is None:  # The error first: a failed job has no value (review 5).
            self.message.setText(f'Could not preview {wanted[0].name}: {error or "not readable"}'); self.preview_path = None; return
        if not self.owner.start_preview(samples, wanted[1], self.level_value()): self.preview_path = None

    def got_scenes(self, listing, error, channel):
        self.library_here['scene'] = () if error is not None or listing is None else listing.files; self.fill_library(self.scenes, 'scene')

    def got_models(self, listing, error, channel):
        self.library_here['model'] = () if error is not None or listing is None else listing.files; self.fill_library(self.models, 'model')

    def got_recent(self, paths, error, channel):
        kind = channel.split(':')[1]; paths = [] if error is not None or paths is None else list(paths)
        if kind == 'audio':
            if self.folder is not None: return  # Left Recent audio while it was read.
            self.fill([], paths)
            self.message.setText('' if paths else 'No recent audio yet: files you add to a project appear here.')
            return
        self.library_recent[kind] = paths
        self.fill_library(self.scenes if kind == 'scene' else self.models, kind)

    # Library tabs --------------------------------------------------------------------
    def refresh_tab(self):
        index = self.tabs.currentIndex()
        if index == 1: self.request_recent('scene'); self.list_for('scenes', SCENE_TYPES)
        elif index == 2: self.equations.set_entries(self.owner.browser_equations())
        elif index == 3: self.transforms.set_entries(self.owner.browser_transforms())
        elif index == 4: self.request_recent('model'); self.list_for('models', MODEL_TYPES)
        elif index == 5: self.objects.set_entries(self.owner.browser_objects())

    def list_for(self, channel, types):
        kind = 'scene' if channel == 'scenes' else 'model'
        self.library_here[kind] = ()
        if self.folder is not None: self.submit(channel, list_folder, self.folder, types)

    def fill_library(self, page, kind):
        rows = []
        recent = self.library_recent.get(kind, [])
        if recent: rows.append(('Recent', '', None)); rows += [(p.name, str(p), str(p)) for p in recent]
        here = self.library_here.get(kind, ())
        if here and self.folder is not None:
            rows.append((f'In {self.folder.name or self.folder}', '', None)); rows += [(p.name, str(p), str(p)) for p in here]
        page.set_entries(rows)

    def open_scene(self, path): self.owner.browser_open_scene(Path(path))
    def import_model(self, path): self.owner.browser_import_model(Path(path))
    def add_equation(self, data): self.owner.browser_add_equation(*data)
    def insert_transform(self, name): self.owner.browser_insert_transform(name)
    def add_object(self, kind): self.owner.add_object(kind)

    def transform_menu(self, name):
        modes = self.owner.browser_transform_modes(name)
        if not modes: return None
        menu = QMenu(self)
        for label, mode in modes: menu.addAction(label, lambda mode=mode: self.owner.browser_insert_transform(name, mode))
        return menu

    def close_worker(self):
        self.poll.stop(); self.search_timer.stop(); self.worker.close()

    def sizeHint(self): return QSize(260, 400)


def _noop(*args, **kwargs): return None


def _read_recent(kind, path):
    return recent_files.read_recent(kind, path, prune=False)


def _same(a, b):
    """The same folder, by absolute path: never resolve on the GUI thread (an offline share stalls it)."""
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))
