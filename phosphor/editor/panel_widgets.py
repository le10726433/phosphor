"""Side panel widgets that stretch with the panel while text keeps one size (UI2)."""
from PySide6.QtCore import Qt, QSize, QRect
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QLabel, QLayout, QSizePolicy, QStyleOption

from phosphor.editor.layout_metrics import CAPTION_WIDTH, CAPTION_MIN
from phosphor.editor.paint_guard import guarded_paint


class InlineCaption(QLabel):
    """One-line caption beside a control.

    CAPTION_WIDTH wide (or its text width, if longer) when the panel has room, so rows line
    up; in a narrow panel it shrinks to CAPTION_MIN and elides with …, keeping the full text
    in its tooltip. It never wraps and never makes the panel scroll sideways.
    """
    def __init__(self, text='', width=CAPTION_WIDTH, minimum=CAPTION_MIN):
        super().__init__(text)
        self.setProperty('inlineCaption', True); self._width, self._minimum = width, min(minimum, width)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setWordWrap(False)

    def sizeHint(self):
        hint = super().sizeHint(); return QSize(max(self._width, hint.width()), hint.height())

    def minimumSizeHint(self):
        return QSize(self._minimum, super().minimumSizeHint().height())

    def elided(self):
        """True when the caption is too narrow for its text (and draws it elided)."""
        return self.fontMetrics().horizontalAdvance(self.text()) > self.contentsRect().width()

    def setToolTip(self, text):
        self._tip = text; super().setToolTip(text)

    @guarded_paint
    def paintEvent(self, event):
        if not self.elided(): return super().paintEvent(event)
        painter = QPainter(self); rect = self.contentsRect()
        option = QStyleOption(); option.initFrom(self)
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, rect.width())
        self.style().drawItemText(painter, rect, int(self.alignment()), self.palette(), self.isEnabled(), text, self.foregroundRole())

    def event(self, event):
        # An elided caption shows its full text (and any tip) on hover.
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.Type.ToolTip and self.elided():
            from PySide6.QtWidgets import QToolTip
            tip = getattr(self, '_tip', '')  # The full caption first, then any tip of its own (Codex review).
            text = self.text() + ('\n' + tip if tip and tip != self.text() else '')
            QToolTip.showText(event.globalPos(), text, self); return True
        return super().event(event)


class FlowRow(QLayout):
    """A row of buttons that wraps onto further lines in a narrow panel (UI2).

    Each line is stretched to the full width: extra room goes to the items whose horizontal
    size policy can grow (a Fixed one, such as the ↑ ↓ buttons, keeps its width). Its minimum
    width is its widest item, so a row of buttons never makes a panel scroll sideways.
    """
    def __init__(self, parent=None, spacing=5):
        super().__init__(parent); self._items = []; self.setSpacing(spacing); self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item): self._items.append(item)
    def addWidget(self, widget, stretch=0):  # The stretch of QHBoxLayout.addWidget is accepted and unused.
        super().addWidget(widget)
    def count(self): return len(self._items)
    def itemAt(self, index): return self._items[index] if 0 <= index < len(self._items) else None
    def takeAt(self, index): return self._items.pop(index) if 0 <= index < len(self._items) else None
    def expandingDirections(self): return Qt.Orientation.Horizontal
    def hasHeightForWidth(self): return True
    def heightForWidth(self, width): return self._place(QRect(0, 0, width, 0), False)

    def setGeometry(self, rect):
        super().setGeometry(rect); self._place(rect, True)

    def sizeHint(self):
        visible = [item for item in self._items if not item.isEmpty()]
        width = sum(item.sizeHint().width() for item in visible) + self.spacing() * max(0, len(visible) - 1)
        height = max((item.sizeHint().height() for item in visible), default=0)
        margins = self.contentsMargins()
        return QSize(width + margins.left() + margins.right(), height + margins.top() + margins.bottom())

    def minimumSize(self):
        visible = [item for item in self._items if not item.isEmpty()]
        width = max((item.minimumSize().width() for item in visible), default=0)
        margins = self.contentsMargins()
        # The height at the row's current width (tab pages size from minimums, not heightForWidth); before
        # it is placed, the tallest item. Never the height at the narrowest width (Codex review).
        placed = self.geometry().width()
        if visible and placed > 0: return QSize(width + margins.left() + margins.right(), self.heightForWidth(placed))
        height = max((item.minimumSize().height() for item in visible), default=0)
        return QSize(width + margins.left() + margins.right(), height + margins.top() + margins.bottom())

    def _lines(self, width):
        lines, line, used = [], [], 0
        for item in self._items:
            if item.isEmpty(): continue
            need = item.sizeHint().width() + (self.spacing() if line else 0)
            if line and used + need > width: lines.append(line); line, used = [], 0; need = item.sizeHint().width()
            line.append(item); used += need
        if line: lines.append(line)
        return lines

    def _place(self, rect, apply):
        margins = self.contentsMargins(); inner = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        y = inner.y()
        for number, line in enumerate(self._lines(inner.width())):
            if number: y += self.spacing()
            height = max(item.sizeHint().height() for item in line)
            if apply:
                widths = [min(item.sizeHint().width(), inner.width()) for item in line]
                spare = inner.width() - sum(widths) - self.spacing() * (len(line) - 1)
                growing = [i for i, item in enumerate(line) if item.widget() is None or
                           item.widget().sizePolicy().horizontalPolicy() != QSizePolicy.Policy.Fixed]
                for share, i in enumerate(growing):
                    widths[i] += spare // len(growing) + (1 if share < spare % len(growing) else 0)
                x = inner.x()
                for item, width in zip(line, widths):
                    item.setGeometry(QRect(x, y, max(width, item.minimumSize().width()), height)); x += width + self.spacing()
            y += height
        return y - rect.y() + margins.bottom()
