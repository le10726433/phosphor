"""Offline reader for the bundled Markdown manual."""
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QImage, QKeySequence, QShortcut, QTextCursor, QTextDocument
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QSizePolicy,
                               QPushButton, QTextBrowser, QTextEdit, QVBoxLayout, QWidget)

from phosphor.display import theme as themes
from phosphor.editor.manual_search import build_index, heading_anchor, search
from phosphor.storage.app_paths import APP_DIR


class ManualBrowser(QTextBrowser):
    shown = Signal()  # After every page change (sourceChanged does not fire with doSetSource overridden).

    def loadResource(self, resource_type, url):
        if resource_type == QTextDocument.ResourceType.ImageResource and url.isLocalFile():
            image = QImage(url.toLocalFile())
            if image.width() > 620:
                return image.scaledToWidth(620, Qt.TransformationMode.SmoothTransformation)
            if not image.isNull():
                return image
        return super().loadResource(resource_type, url)

    def doSetSource(self, url, resource_type=QTextDocument.ResourceType.UnknownResource):
        super().doSetSource(url, resource_type)
        # Qt's Markdown reader leaves headings unnamed. Add the anchors used by
        # the manual's section links, using the same punctuation rules as GitHub.
        block = self.document().begin()
        used = set()
        while block.isValid():
            if block.blockFormat().headingLevel() and block.text():
                slug = heading_anchor(block.text(), used)
                cursor = QTextCursor(block)
                cursor.movePosition(QTextCursor.MoveOperation.NextCharacter, QTextCursor.MoveMode.KeepAnchor)
                fmt = cursor.charFormat()
                if not fmt.anchorNames():
                    fmt.setAnchor(True)
                    fmt.setAnchorNames([slug])
                    cursor.setCharFormat(fmt)
            block = block.next()
        if url.fragment():
            self.scrollToAnchor(url.fragment())
        self.shown.emit()


def chapters(manual_dir=APP_DIR / 'docs' / 'manual'):
    """((path, title), ...) of the numbered chapters in reading order; the title is the first heading."""
    result = []
    for path in sorted(Path(manual_dir).glob('[0-9][0-9]-*.md')):
        first = next((line for line in path.read_text(encoding='utf-8').splitlines() if line.startswith('# ')), path.stem)
        result.append((path, first.lstrip('#').strip()))
    return tuple(result)


class ManualView(QWidget):
    def __init__(self):
        super().__init__()
        index = QUrl.fromLocalFile(str(APP_DIR / 'docs' / 'manual' / 'README.md'))
        self.contents_url = index
        self.chapters = chapters()
        browser = ManualBrowser()
        self.browser = browser
        browser.setObjectName('manual_browser')
        back = QPushButton('Back')
        contents = QPushButton('Contents')
        forward = QPushButton('Forward')
        back.setEnabled(False)
        forward.setEnabled(False)
        browser.backwardAvailable.connect(back.setEnabled)
        browser.forwardAvailable.connect(forward.setEnabled)
        back.clicked.connect(browser.backward)
        contents.clicked.connect(lambda: browser.setSource(index))
        forward.clicked.connect(browser.forward)

        navigation = QHBoxLayout()
        for button in (back, contents, forward):
            navigation.addWidget(button)
        navigation.addStretch()
        layout = QVBoxLayout(self)
        layout.addLayout(navigation)
        self.search_field = QLineEdit()
        self.search_field.setObjectName('manual_search')
        self.search_field.setPlaceholderText('Search manual (Enter: next, Shift+Enter: previous)')
        self.search_field.setAccessibleName('Search manual')
        self.results = QListWidget()
        self.results.setObjectName('manual_search_results')
        self.results.setMaximumHeight(150)
        self.results.hide()
        self.index = build_index(APP_DIR / 'docs' / 'manual')
        self.matches = []
        self.search_field.installEventFilter(self)
        # macOS item views start editing on Return instead of emitting itemActivated.
        self.results.installEventFilter(self)
        self.search_field.textChanged.connect(self.update_search)
        self.results.itemActivated.connect(self.open_result)
        self.results.itemClicked.connect(self.open_result)
        browser.sourceChanged.connect(self.highlight_matches)
        self.find_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        self.find_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.find_shortcut.activated.connect(self.focus_search)
        # ◀ Previous chapter, the chapter's title, Next chapter ▶ beside the search field (UI1).
        search_row = QHBoxLayout()
        search_row.addWidget(self.search_field, 3)
        self.previous_chapter = QPushButton('◀ Previous chapter'); self.previous_chapter.setObjectName('manual_previous_chapter')
        self.next_chapter = QPushButton('Next chapter ▶'); self.next_chapter.setObjectName('manual_next_chapter')
        self.chapter_title = QLabel(); self.chapter_title.setObjectName('manual_chapter_title')
        self.chapter_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.chapter_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.previous_chapter.clicked.connect(lambda checked=False: self.step_chapter(-1))
        self.next_chapter.clicked.connect(lambda checked=False: self.step_chapter(1))
        search_row.addWidget(self.previous_chapter); search_row.addWidget(self.chapter_title, 2); search_row.addWidget(self.next_chapter)
        browser.shown.connect(self.refresh_chapter)
        layout.addLayout(search_row)
        layout.addWidget(self.results)
        layout.addWidget(browser)
        browser.setSource(index)
        self.refresh_chapter()

    # Chapters ----------------------------------------------------------------
    def chapter_index(self):
        """The shown chapter's position in self.chapters; -1 on the contents page (or another file)."""
        shown = Path(self.browser.source().toLocalFile()).name
        return next((i for i, (path, _) in enumerate(self.chapters) if path.name == shown), -1)

    def step_chapter(self, step):
        """Previous (-1) or next (+1) chapter; before chapter 1 is the contents page."""
        target = self.chapter_index() + step
        if target < 0: self.browser.setSource(self.contents_url)
        elif target < len(self.chapters): self.browser.setSource(QUrl.fromLocalFile(str(self.chapters[target][0])))

    def refresh_chapter(self, *_):
        index = self.chapter_index()
        title = self.chapters[index][1] if index >= 0 else 'Contents'
        self.chapter_title.setText(title); self.chapter_title.setToolTip(title)
        on_contents = Path(self.browser.source().toLocalFile()).name == self.contents_url.fileName()
        self.previous_chapter.setEnabled(index >= 0)
        self.next_chapter.setEnabled(index + 1 < len(self.chapters) if index >= 0 or on_contents else bool(self.chapters))
        previous = self.chapters[index - 1][1] if index > 0 else 'Contents'
        following = self.chapters[index + 1][1] if index + 1 < len(self.chapters) else ''
        self.previous_chapter.setToolTip(f'Go to {previous}' if index >= 0 else '')
        self.next_chapter.setToolTip(f'Go to {following}' if following else '')

    def focus_search(self):
        self.search_field.setFocus()
        self.search_field.selectAll()

    def update_search(self):
        self.results.clear()
        query = self.search_field.text().strip()
        for result in search(self.index, query):
            section = result.section
            item = QListWidgetItem(f'{section.chapter_title} › {section.heading} — {result.snippet}')
            item.setData(Qt.ItemDataRole.UserRole, result)
            self.results.addItem(item)
        if query and not self.results.count():
            item = QListWidgetItem('No matches')
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.results.addItem(item)
        self.results.setVisible(bool(query))
        self.highlight_matches()

    def open_result(self, item):
        result = item.data(Qt.ItemDataRole.UserRole)
        if result is None:
            return
        url = QUrl.fromLocalFile(str(result.section.chapter_file))
        url.setFragment(result.section.anchor)
        self.browser.setSource(url)
        self.highlight_matches()
        # Start match stepping at this section, preserving the anchor's scroll position.
        block = self.browser.document().begin()
        while block.isValid():
            cursor = QTextCursor(block)
            cursor.movePosition(QTextCursor.MoveOperation.NextCharacter, QTextCursor.MoveMode.KeepAnchor)
            if result.section.anchor in cursor.charFormat().anchorNames():
                cursor.clearSelection()
                self.browser.setTextCursor(cursor)
                break
            block = block.next()
        self.browser.scrollToAnchor(result.section.anchor)

    def highlight_matches(self, *_):
        self.matches = []
        query = self.search_field.text().strip()
        if query:
            cursor = QTextCursor(self.browser.document())
            while True:
                cursor = self.browser.document().find(query, cursor)
                if cursor.isNull():
                    break
                self.matches.append(QTextCursor(cursor))
        colors = themes.for_widget(self).colors
        selections = []
        for cursor in self.matches:
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format.setBackground(QColor(colors['search_highlight']))
            selection.format.setForeground(QColor(colors['search_text']))
            selections.append(selection)
        self.browser.setExtraSelections(selections)

    def eventFilter(self, watched, event):
        if watched is self.results and event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.results.currentItem() is not None:
                self.open_result(self.results.currentItem())
            return True
        if watched is self.search_field and event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.matches:
                current = self.browser.textCursor()
                backwards = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                if backwards:
                    candidates = [c for c in self.matches if c.selectionStart() < current.selectionStart()]
                    target = candidates[-1] if candidates else self.matches[-1]
                else:
                    candidates = [c for c in self.matches if c.selectionStart() >= current.selectionEnd()
                                  and c != current]
                    target = candidates[0] if candidates else self.matches[0]
                self.browser.setTextCursor(target)
                self.browser.ensureCursorVisible()
            return True
        return super().eventFilter(watched, event)


class ManualWindow(QWidget):
    """Help › Manual (UI1): a non-modal tool window owned by the main window, so it stays on top of
    it while the main window keeps working. One instance; its place is saved by the owner."""
    def __init__(self, owner, geometry=(), remember=None):
        super().__init__(owner, Qt.WindowType.Tool)
        self.setWindowTitle('Manual — phosphor')
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)  # Stays shown while phosphor is in front.
        self.setMinimumSize(420, 320)
        self.view = ManualView()
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.addWidget(self.view)
        self.remember = remember
        self.save_timer = QTimer(self); self.save_timer.setSingleShot(True); self.save_timer.setInterval(400)
        self.save_timer.timeout.connect(self.save_geometry)
        if geometry: self.setGeometry(*geometry)
        else: self.resize(780, 640)

    def placed(self):
        """(x, y, width, height) of the window's client area, as setGeometry takes it."""
        rect = self.geometry(); return (rect.x(), rect.y(), rect.width(), rect.height())

    def save_geometry(self):
        if self.remember is not None and self.isVisible(): self.remember(self.placed())

    def moveEvent(self, event):
        super().moveEvent(event); self.save_timer.start()

    def resizeEvent(self, event):
        super().resizeEvent(event); self.save_timer.start()

    def closeEvent(self, event):
        self.save_geometry(); super().closeEvent(event)
