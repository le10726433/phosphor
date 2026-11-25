"""The Missing audio files dialog: Locate…, Search folder… and Skip for clips whose file was not found.

The window does the work (Window.locate_audio_file, Window.search_missing_audio), so the clip
menu's Locate file… and this dialog share one path, including the same-folder follow-up.
"""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHeaderView, QLabel, QPushButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout)

from phosphor.engine.audio_media import missing_files


class MissingAudioDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle('Missing audio files')
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        self.intro = QLabel(); self.intro.setWordWrap(True)
        layout.addWidget(self.intro)
        self.files = QTreeWidget()
        self.files.setHeaderLabels(['File', 'Clips', 'Last location'])
        self.files.setRootIsDecorated(False); self.files.setUniformRowHeights(True)
        header = self.files.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self.files.itemDoubleClicked.connect(lambda item, column: self.locate())
        self.files.currentItemChanged.connect(lambda *args: self.sync_buttons())
        layout.addWidget(self.files)
        buttons = QDialogButtonBox()
        self.locate_button = QPushButton('Locate…')
        self.locate_button.setToolTip('Choose the selected file. Other missing files in the same folder are offered too.')
        self.search_button = QPushButton('Search folder…')
        self.search_button.setToolTip('Search a folder and its subfolders for every missing file (same name and contents).')
        self.skip_button = QPushButton('Skip')
        self.skip_button.setToolTip('Keep the rest missing: they play silence. Locate them later with right-click › '
                                    'Locate file… or File › Find missing audio files….')
        for button, role in ((self.locate_button, QDialogButtonBox.ButtonRole.ActionRole),
                             (self.search_button, QDialogButtonBox.ButtonRole.ActionRole),
                             (self.skip_button, QDialogButtonBox.ButtonRole.RejectRole)):
            buttons.addButton(button, role)
        self.locate_button.clicked.connect(lambda checked=False: self.locate())
        self.search_button.clicked.connect(lambda checked=False: self.search())
        self.skip_button.clicked.connect(lambda checked=False: self.reject())
        layout.addWidget(buttons)
        self.refresh()

    def keys(self):
        return [self.files.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) for i in range(self.files.topLevelItemCount())]

    def refresh(self):
        """List the files still missing; closes the dialog once none are left."""
        project = self.window.project
        files = missing_files(project)
        current = self.files.currentItem()
        selected = current.data(0, Qt.ItemDataRole.UserRole) if current is not None else None
        self.files.clear()
        for key, indices in files.items():
            clip = project.audio_clips[indices[0]]
            item = QTreeWidgetItem([clip.name, str(len(indices)), str(Path(clip.path).parent)])
            item.setData(0, Qt.ItemDataRole.UserRole, key)
            item.setToolTip(2, clip.path)
            self.files.addTopLevelItem(item)
            if key == selected: self.files.setCurrentItem(item)
        if self.files.currentItem() is None and self.files.topLevelItemCount():
            self.files.setCurrentItem(self.files.topLevelItem(0))
        count = len(files)
        self.intro.setText(
            f'{count} audio file{"s were" if count != 1 else " was"} not found. Locate {"them" if count != 1 else "it"} '
            'or search a folder. Missing clips stay in the project and play silence; saving keeps their last '
            'known location.')
        self.sync_buttons()
        if not count and self.isVisible(): self.accept()
        return count

    def sync_buttons(self):
        self.locate_button.setEnabled(self.files.currentItem() is not None)
        self.search_button.setEnabled(self.files.topLevelItemCount() > 0)

    def locate(self, path=None):
        item = self.files.currentItem()
        if item is None: return False
        found = self.window.locate_audio_file(item.data(0, Qt.ItemDataRole.UserRole), path)
        self.refresh()
        return found

    def search(self, folder=None):
        found = self.window.search_missing_audio(folder)
        self.refresh()
        return found
