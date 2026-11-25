"""Searchable, non-modal keyboard and gesture reference (Help › Keyboard shortcuts): current bindings."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView

from phosphor.editor.shortcuts import filter_shortcuts, display_keys


class ShortcutsView(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Keyboard shortcuts — phosphor')
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.resize(880, 560)
        layout = QVBoxLayout(self)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText('Search actions, keys or places…')
        self.search.setAccessibleName('Search keyboard shortcuts')
        layout.addWidget(self.search)
        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(['Action', 'Keys', 'Where'])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        self.search.textChanged.connect(self.refresh)
        self.refresh()

    def refresh(self, query=''):
        entries = filter_shortcuts(query, preferences=getattr(self.parent(), 'preferences', None))
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            for column, value in enumerate((entry.description, display_keys(entry), entry.where)):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.table.resizeRowsToContents()
