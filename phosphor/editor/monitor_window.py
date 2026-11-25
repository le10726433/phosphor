"""Independent monitor host and Quick controls; geometry imports need no Qt.

UI classes are loaded on first attribute access, so headless callers can import
the geometry helpers without installing PySide6. Construct a dedicated host for
the monitor: its native container must never be moved between top-level windows.
"""
from phosphor.display import theme as themes
import math
import sys


def _numbers(value, length):
    try:
        values = tuple(value)
        if len(values) != length or any(
            isinstance(v, (bool, str, bytes)) or not math.isfinite(v)
            for v in values
        ):
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Expected finite numeric coordinates") from None
    return values


def _screens(screens):
    try:
        result = [_numbers(s, 4) for s in screens]
    except TypeError:
        raise ValueError("Expected screen rectangles") from None
    if not result or any(w <= 0 or h <= 0 for _, _, w, h in result):
        raise ValueError("Expected non-empty screens with positive sizes")
    return result


def fit_geometry(rect, screens):
    """Keep a usable rect, otherwise resize and centre on the first screen.

    The brief's 64x64 title visibility cannot fit a 32px title strip: interpret
    it as 64px horizontally and the entire 32px vertically. Size bounds apply
    even to visible rectangles. On screens smaller than 280px the 240px minimum
    takes precedence over the 40px clearance.
    """
    x, y, w, h = _numbers(rect, 4)
    screens = _screens(screens)
    if w <= 0 or h <= 0:
        raise ValueError("Expected positive window dimensions")
    for sx, sy, sw, sh in screens:
        visible_width = min(x + w, sx + sw) - max(x, sx)
        visible_height = min(y + 32, sy + sh) - max(y, sy)
        if (visible_width >= 64 and visible_height >= 32
                and 240 <= w <= max(240, sw - 40)
                and 240 <= h <= max(240, sh - 40)):
            return (x, y, w, h)
    sx, sy, sw, sh = screens[0]
    w = min(max(240, w), max(240, sw - 40))
    h = min(max(240, h), max(240, sh - 40))
    return (sx + (sw - w) / 2, sy + (sh - h) / 2, w, h)


def pick_fullscreen_screen(window_center, main_window_center, screens):
    """Return the containing screen, otherwise the nearest rectangle (first tie).

    main_window_center is validated but deliberately does not affect selection.
    """
    x, y = _numbers(window_center, 2)
    _numbers(main_window_center, 2)
    screens = _screens(screens)
    for index, (sx, sy, sw, sh) in enumerate(screens):
        if sx <= x < sx + sw and sy <= y < sy + sh:
            return index
    def distance(screen):
        sx, sy, sw, sh = screen
        return math.hypot(max(sx - x, 0, x - sx - sw),
                          max(sy - y, 0, y - sy - sh))
    return min(range(len(screens)), key=lambda i: distance(screens[i]))


def __getattr__(name):
    if name not in ("CornerButtons", "MonitorWindow"):
        raise AttributeError(name)
    _load_qt_classes()
    return globals()[name]


def exit_key(event):
    """The key press is the window's Escape action's current shortcut (rebindable, UI2)."""
    from PySide6.QtGui import QKeySequence
    from phosphor.editor.shortcuts import key_sequences
    return QKeySequence(event.keyCombination()) in key_sequences('escape')


def _load_qt_classes():
    from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt, Signal
    from PySide6.QtGui import QColor, QPainter, QPen
    from PySide6.QtQuick import QQuickPaintedItem
    from PySide6.QtWidgets import QWidget, QVBoxLayout

    class CornerButtons(QQuickPaintedItem):
        """Small Quick overlay. Parent must be the view's contentItem().

        The item itself is the hot zone (the buttons plus a margin): Qt Quick sends hover to
        the topmost item that accepts it, so a separate zone item behind the buttons never
        saw the mouse leave. It stays transparent (opacity 0) until hovered; presses outside
        the buttons are not accepted, so they reach the scope underneath.
        """
        MARGIN, TOP = 40, 8  # Hot-zone margin left of and below the buttons; top inset.
        clicked = Signal(str)

        def __init__(self, parent=None, theme=None):
            super().__init__(parent)
            self._names = []
            self._hovered = self._pressed = -1
            self.set_theme(theme or themes.current())
            self.setZ(1000)
            self.setAcceptHoverEvents(True)
            self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
            self.setOpacity(0)
            if parent is not None:
                parent.widthChanged.connect(self._anchor)
            self.set_buttons([])

        def set_buttons(self, names):
            names = list(names)
            if any(n not in {"popout", "return", "fullscreen", "exit_fullscreen"}
                   for n in names):
                raise ValueError("Unknown monitor button")
            self._names = names
            self._hovered = self._pressed = -1
            self._anchor()
            self.update()

        def set_theme(self, theme):
            self.theme = theme
            colors = theme.colors
            modern = theme.style == themes.MODERN
            self._foreground = QColor(colors['overlay_fore' if modern else 'fg'])
            self._hover_foreground = QColor(colors['selection_text']) if modern else self._foreground
            self._background = QColor(colors['overlay_back' if modern else 'bg'])
            self._background.setAlpha(theme.metrics.corner_alpha)
            self._accent = QColor(colors['overlay_accent' if modern else 'accent'])
            self._accent.setAlpha(theme.metrics.corner_alpha)
            self.update()

        def _anchor(self):
            parent = self.parentItem()
            strip = max(0, len(self._names) * 32 - 4)
            self.setWidth(strip + self.MARGIN + 8); self.setHeight(self.TOP + 28 + self.MARGIN)
            if parent is not None:
                self.setX(parent.width() - self.width()); self.setY(0)
            self.setVisible(bool(self._names))
            if not self._names: self.setOpacity(0)

        def _hit(self, point):
            x, y = point.x() - self.MARGIN, point.y() - self.TOP
            if 0 <= y < 28 and 0 <= x < len(self._names) * 32 - 4:
                index = int(x // 32)
                if x - index * 32 < 28:
                    return index
            return -1

        def hoverEnterEvent(self, event):
            self.hoverMoveEvent(event)

        def hoverMoveEvent(self, event):
            self.setOpacity(1)
            self._hovered = self._hit(event.position())
            self.update()
            event.accept()

        def hoverLeaveEvent(self, event):
            self._hovered = -1
            self.setOpacity(0)
            self.update()
            event.accept()

        def mousePressEvent(self, event):
            self._pressed = self._hit(event.position())
            event.setAccepted(self._pressed >= 0)

        def mouseMoveEvent(self, event):
            self._hovered = self._hit(event.position())
            self.update()
            event.accept()

        def mouseReleaseEvent(self, event):
            index, self._pressed = self._pressed, -1
            event.accept()
            if (event.button() == Qt.MouseButton.LeftButton and index >= 0
                    and index == self._hit(event.position())):
                self.clicked.emit(self._names[index])

        def mouseUngrabEvent(self):
            self._pressed = -1

        def paint(self, painter):
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.translate(self.MARGIN, self.TOP)
            for index, name in enumerate(self._names):
                painter.save()
                painter.translate(index * 32, 0)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(self._accent if index == self._hovered
                                 else self._background)
                painter.drawRoundedRect(QRectF(0, 0, 28, 28), self.theme.metrics.corner_radius, self.theme.metrics.corner_radius)
                foreground = self._hover_foreground if index == self._hovered else self._foreground
                painter.setPen(QPen(foreground, self.theme.metrics.corner_stroke))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                def line(x1, y1, x2, y2):
                    painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
                if name in ("popout", "return"):
                    for coords in ((7, 10, 7, 21), (7, 21, 18, 21),
                                   (18, 21, 18, 15), (7, 10, 13, 10)):
                        line(*coords)
                    line(12, 16, 22, 6)
                    if name == "popout":
                        line(16, 6, 22, 6); line(22, 6, 22, 12)
                    else:
                        line(12, 10, 12, 16); line(12, 16, 18, 16)
                else:
                    for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
                        c = 8 if name == "fullscreen" else 3
                        x, y = 14 + sx * c, 14 + sy * c
                        d = -5 if name == "fullscreen" else 5
                        line(x, y, x + sx * d, y)
                        line(x, y, x, y + sy * d)
                painter.restore()

    class MonitorWindow(QWidget):
        """Top-level owner of a dedicated host, attached only at construction."""
        returned = Signal()
        fullscreen_changed = Signal(bool)

        def __init__(self, host, parent=None):
            super().__init__(parent, Qt.WindowType.Window)
            self.host = host
            self._fullscreen = False
            self._normal_geometry = None
            self._normal_screen_name = None
            self.setWindowTitle("phosphor — Signal monitor")
            # Draw edge to edge: Qt 6 otherwise keeps contents out of the notch's safe area,
            # which left a 38 px strip at the top of the full-screen trace on a MacBook.
            self.setAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False)
            self.setMinimumSize(240, 240)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(host)
            self._view = getattr(host, "view", None)
            if self._view is not None:
                self._view.installEventFilter(self)

        @property
        def is_fullscreen(self):
            return self._fullscreen

        def closeEvent(self, event):
            event.ignore()
            self.returned.emit()

        def set_fullscreen(self, on, screen=None):
            on = bool(on)
            if on == self._fullscreen:
                return
            if on:
                screen = screen or self.screen()
                if screen is None:
                    raise ValueError("No screen available")
                self._normal_geometry = QRect(self.geometry())
                current_screen = self.screen()
                self._normal_screen_name = current_screen.name() if current_screen else ""
                self._fullscreen = True
                if sys.platform == "darwin":
                    self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
                    self.setGeometry(screen.geometry())
                    self.show()
                    self.raise_()
                    # Above the menu bar and Dock, or macOS keeps it 38 px short (notch).
                    from phosphor.editor.mac_window import cover_screen
                    cover_screen(self, True)
                else:
                    # Move before fullscreen so Qt selects the requested display.
                    self.setGeometry(screen.geometry())
                    if self.windowHandle() is not None:
                        self.windowHandle().setScreen(screen)
                    self.showFullScreen()
            else:
                self._fullscreen = False
                if sys.platform == "darwin":
                    from phosphor.editor.mac_window import cover_screen
                    cover_screen(self, False)
                    self.setWindowFlag(Qt.WindowType.FramelessWindowHint, False)
                    self.setGeometry(self._normal_geometry)
                    self.show()
                else:
                    self.showNormal()
                    self.setGeometry(self._normal_geometry)
            self.fullscreen_changed.emit(on)

        def geometry_document(self):
            rect = self._normal_geometry if self._fullscreen else self.geometry()
            screen = self.screen()
            name = self._normal_screen_name if self._fullscreen else (
                screen.name() if screen else "")
            return dict(x=rect.x(), y=rect.y(), width=rect.width(),
                        height=rect.height(), screen=name)

        def restore_geometry(self, document, screens):
            screens = list(screens)
            # Prefer the saved display for rescue; preserve valid coordinates.
            ordered = sorted(screens, key=lambda s: s.name() != document.get("screen"))
            geometries = [(s.availableGeometry().x(), s.availableGeometry().y(),
                           s.availableGeometry().width(), s.availableGeometry().height())
                          for s in ordered]
            try:
                rect = tuple(document[k] for k in ("x", "y", "width", "height"))
            except (KeyError, TypeError):
                raise ValueError("Invalid geometry document") from None
            fitted = fit_geometry(rect, geometries)
            if self._fullscreen:
                self.set_fullscreen(False)
            self.setGeometry(*(round(v) for v in fitted))

        def eventFilter(self, watched, event):
            if (watched is self._view and event.type() == QEvent.Type.KeyPress
                    and exit_key(event) and self._fullscreen):
                self.set_fullscreen(False)
                event.accept()
                return True
            return super().eventFilter(watched, event)

        def keyPressEvent(self, event):
            if exit_key(event) and self._fullscreen:
                self.set_fullscreen(False)
                event.accept()
            else:
                super().keyPressEvent(event)

    globals().update(CornerButtons=CornerButtons, MonitorWindow=MonitorWindow)
