"""Log-frequency spectrum curve editor, publishing only completed edits."""
import math
from PySide6.QtCore import QPointF, QRectF, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget, QMenu, QInputDialog, QToolTip
from phosphor.display import theme as themes
from phosphor.engine import noise_loops
from phosphor.editor.paint_guard import guarded_paint


class SpectrumPlot(QWidget):
    """Editable two-to-thirty-two-node spectrum with a measured background."""
    edited = Signal(object)

    def __init__(self, theme=None):
        super().__init__()
        self.theme = theme
        self.curve = noise_loops.BUILTINS[0]
        self.measured = None
        self.drag = None
        self.setMinimumSize(200, 140); self.setMouseTracking(True)
        self.timer = QTimer(self); self.timer.setInterval(150); self.timer.timeout.connect(self.poll)
        self.set_curve(self.curve)

    def set_curve(self, curve):
        self.curve = tuple(curve); self.spectrum_curve = self.curve; self.measured = None
        self.poll()
        if self.measured is None: self.timer.start()
        self.update()

    def poll(self):
        self.measured = noise_loops.spectrum(self.spectrum_curve, wait=False)
        if self.measured is not None: self.timer.stop(); self.update()

    def plot_rect(self): return QRectF(33, 10, max(1, self.width()-48), max(1, self.height()-34))

    def to_point(self, hz, db):
        r = self.plot_rect()
        return QPointF(r.left()+math.log10(hz/20)/3*r.width(), r.top()-db/72*r.height())

    def from_point(self, point):
        r = self.plot_rect()
        return (20*1000**max(0., min(1., (point.x()-r.left())/r.width())),
                -72*max(0., min(1., (point.y()-r.top())/r.height())))

    def handle_at(self, point):
        distances = [(math.hypot(point.x()-self.to_point(*node).x(), point.y()-self.to_point(*node).y()), i)
                     for i, node in enumerate(self.curve)]
        distance, index = min(distances)
        return index if distance <= 9 else None

    @guarded_paint
    def paintEvent(self, event):
        style = self.theme or themes.current()
        theme = style.colors
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(theme['field']))
        r = self.plot_rect()
        font = painter.font()
        if style.style == themes.MODERN: font.setPixelSize(style.fonts.timeline_small_px)
        else: font.setPointSizeF(style.fonts.plot_points)
        painter.setFont(font)
        for hz in (20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000):
            x = self.to_point(hz, 0).x()
            painter.setPen(QPen(QColor(theme['border']), 1)); painter.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            painter.setPen(QColor(theme['muted']))
            label = f'{hz/1000:g}k' if hz >= 1000 else str(hz)
            painter.drawText(QRectF(x-14, r.bottom()+3, 28, 16), Qt.AlignmentFlag.AlignHCenter, label)
        for db in range(-72, 1, 12):
            y = self.to_point(20, db).y()
            painter.setPen(QPen(QColor(theme['border']), 1)); painter.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            painter.setPen(QColor(theme['muted']))
            painter.drawText(QRectF(0, y-8, 29, 16), Qt.AlignmentFlag.AlignRight, str(db))
        painter.save(); painter.setClipRect(r.adjusted(-4, -4, 4, 4))
        def line(nodes, opacity, width):
            path = QPainterPath()
            for i, node in enumerate(nodes):
                point = self.to_point(*node)
                if i == 0: path.moveTo(point)
                else: path.lineTo(point)
            painter.setOpacity(opacity); painter.setPen(QPen(QColor(theme['fg']), width)); painter.drawPath(path)
        if self.measured is not None: line(zip(*self.measured), .25, 1.)
        line(self.curve, 1., 1.6)
        painter.setBrush(QColor(theme['field']))
        for node in self.curve: painter.drawEllipse(self.to_point(*node), 4, 4)
        painter.restore()

    def mousePressEvent(self, event):
        point = event.position(); index = self.handle_at(point)
        if event.button() == Qt.MouseButton.RightButton:
            if index is None: return
            menu = QMenu(self); delete = menu.addAction('Delete node'); delete.setEnabled(len(self.curve) > 2)
            if menu.exec(event.globalPosition().toPoint()) == delete:
                self.curve = self.curve[:index]+self.curve[index+1:]; self.publish()
            return
        if event.button() != Qt.MouseButton.LeftButton: return
        self.drag = (index, point, self.curve)

    def mouseMoveEvent(self, event):
        point = event.position()
        if self.drag is None:
            index = self.handle_at(point)
            if index is not None:
                hz, db = self.curve[index]; QToolTip.showText(event.globalPosition().toPoint(), f'{hz:g} Hz, {db:g} dB', self)
            return
        index, start, original = self.drag
        if index is None or math.hypot(point.x()-start.x(), point.y()-start.y()) < 3: return
        hz, db = self.from_point(point)
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier: hz = original[index][0]
        if event.modifiers() & Qt.KeyboardModifier.AltModifier: db = original[index][1]
        low = original[index-1][0]+1e-6 if index else 20.
        high = original[index+1][0]-1e-6 if index+1 < len(original) else 20000.
        nodes = list(original); nodes[index] = (max(low, min(high, hz)), db)
        self.curve = tuple(nodes); self.update()

    def mouseReleaseEvent(self, event):
        if self.drag is None or event.button() != Qt.MouseButton.LeftButton: return
        index, start, original = self.drag; self.drag = None
        if index is not None and math.hypot(event.position().x()-start.x(), event.position().y()-start.y()) < 3:
            self.curve = original; self.update(); return
        if index is None and self.plot_rect().contains(event.position()) and len(self.curve) < 32:
            node = self.from_point(event.position())
            if all(abs(hz-node[0]) > 1e-6 for hz, _ in self.curve): self.curve = tuple(sorted((*self.curve, node)))
        if self.curve != original: self.publish()

    def mouseDoubleClickEvent(self, event):
        self.drag = None
        index = self.handle_at(event.position())
        if index is None: return
        hz, db = self.curve[index]
        value, ok = QInputDialog.getDouble(self, 'Node gain', 'Gain (dB)', db, -72., 0., 2)
        if ok:
            nodes = list(self.curve); nodes[index] = (hz, value); self.curve = tuple(nodes); self.publish()

    def publish(self):
        curve = self.curve
        self.set_curve(curve); self.edited.emit(curve)
