"""Shared painter and hit tests for continuous bars, on/off blocks and gain lines."""
from dataclasses import dataclass
from phosphor.display import theme as themes
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QColor, QPen

BARS, BLOCKS, LINE = 'bars', 'blocks', 'line'
LANE_HEIGHT = 60
COLLAPSED_HEIGHT = 16  # A collapsed lane (UI1): a thin labelled strip; right-click › Expand.


@dataclass(frozen=True)
class LaneItem:
    beat: float
    value: float
    end: float | None = None
    selected: bool = False
    hold: bool = False  # LINE: hold this value to the next item (a step) instead of a ramp.


class LaneStrip:
    def __init__(self, style=BARS, pad=4):
        if style not in (BARS, BLOCKS, LINE): raise ValueError('Invalid lane style.')
        self.style, self.pad, self.rect = style, pad, QRectF()
        self.collapsed = False  # Shown as a thin labelled strip (paint_collapsed); its owner saves the state.

    def height(self, full=LANE_HEIGHT):
        """The lane's height in its row: `full` open, COLLAPSED_HEIGHT collapsed."""
        return COLLAPSED_HEIGHT if self.collapsed else full

    def paint_collapsed(self, p, colors, label):
        """The collapsed strip: a rule on top and the lane's name, dimmed."""
        m = themes.for_widget(self).metrics
        p.save(); p.setClipRect(self.rect)
        p.fillRect(self.rect, colors['background'])
        p.setPen(QPen(colors['grid'], m.lane_stroke)); p.drawLine(self.rect.topLeft(), self.rect.topRight())
        p.setOpacity(m.lane_label_opacity); p.setPen(colors['text'])
        p.drawText(self.rect.adjusted(6, 0, -6, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
        p.restore()

    def set_rect(self, rect): self.rect = QRectF(rect)

    def y_of(self, value):
        return self.rect.bottom() - self.pad - min(1., max(0., value)) * max(1., self.rect.height() - 2 * self.pad)

    def value_at(self, y):
        return min(1., max(0., (self.rect.bottom() - self.pad - y) / max(1., self.rect.height() - 2 * self.pad)))

    def paint(self, p, colors, x_of, items, enabled=True, span=None, background=True, default=1.):
        """Draw items. LINE joins them, extended flat across span (first, last beat), or draws a
        dashed line at `default` across span without items; background=False overlays another set."""
        m = themes.for_widget(self).metrics
        p.save(); p.setClipRect(self.rect)
        if background: p.fillRect(self.rect, colors['background'])
        if not enabled: p.setOpacity(m.lane_disabled_opacity)
        if background:
            p.setPen(QPen(colors['grid'], m.lane_stroke))
            p.drawLine(self.rect.topLeft(), self.rect.topRight())
            guide = QColor(colors['grid']); guide.setAlpha(m.lane_guide_alpha); p.setPen(guide)
            for v in ((.25, .5, .75) if self.style != BLOCKS else ()):  # Blocks have two values only.
                p.drawLine(QPointF(self.rect.left(), self.y_of(v)), QPointF(self.rect.right(), self.y_of(v)))
        if self.style == LINE:
            self._paint_line(p, colors, x_of, items, span, default); p.restore(); return
        for item in items:
            color = colors['text' if item.selected else 'trace']
            p.setPen(QPen(color, m.lane_selected_stroke if item.selected else m.lane_stroke))
            x, y = x_of(item.beat), self.y_of(item.value)
            if self.style == BARS:
                p.drawLine(QPointF(x, self.y_of(0)), QPointF(x, y))
                p.fillRect(QRectF(x - 2.5, y - 2.5, 5, 5), color)
            elif item.end is not None:
                rect = QRectF(x, self.rect.top() + self.pad, x_of(item.end) - x, self.rect.height() - 2 * self.pad)
                if item.value >= .5: p.fillRect(rect, color)
                else: p.drawRect(rect)
        p.restore()

    def _paint_line(self, p, colors, x_of, items, span, default):
        m = themes.for_widget(self).metrics
        color = colors['trace']
        if not items:
            if span is not None:
                p.setPen(QPen(color, m.lane_stroke, Qt.PenStyle.DashLine)); y = self.y_of(default)
                p.drawLine(QPointF(x_of(span[0]), y), QPointF(x_of(span[1]), y))
            return
        nodes = [QPointF(x_of(item.beat), self.y_of(item.value)) for item in items]
        p.setPen(QPen(color, m.lane_line_stroke))
        if span is not None:
            p.drawLine(QPointF(x_of(span[0]), nodes[0].y()), nodes[0])
            p.drawLine(nodes[-1], QPointF(x_of(span[1]), nodes[-1].y()))
        for item, a, b in zip(items, nodes, nodes[1:]):
            if item.hold:
                corner = QPointF(b.x(), a.y()); p.drawLine(a, corner); p.drawLine(corner, b)
            else: p.drawLine(a, b)
        for item, node in zip(items, nodes):
            square = QRectF(node.x() - 3, node.y() - 3, 6, 6)
            p.fillRect(square, color)
            if item.selected: p.setPen(QPen(colors['text'], m.lane_line_stroke)); p.drawRect(square.adjusted(-2, -2, 2, 2))

    def node_at(self, x, y, x_of, items, tolerance=6):
        """Index of the LINE node within tolerance pixels of (x, y) in both directions, or None."""
        candidates = [(abs(x - x_of(n.beat)) + abs(y - self.y_of(n.value)), i) for i, n in enumerate(items)
                      if abs(x - x_of(n.beat)) <= tolerance and abs(y - self.y_of(n.value)) <= tolerance]
        return min(candidates)[1] if candidates else None

    def bar_at(self, x, y, x_of, items, tolerance=5):
        candidates = [(abs(y - self.y_of(n.value)), abs(x - x_of(n.beat)), i)
                      for i, n in enumerate(items) if abs(x - x_of(n.beat)) <= tolerance]
        return min(candidates)[2] if candidates else None

    def crossed(self, x0, x1, x_of, items):
        low, high = sorted((x0, x1))
        return tuple(i for i, n in enumerate(items) if low <= x_of(n.beat) <= high)

    def block_at(self, beat, items):
        return next((i for i, n in reversed(tuple(enumerate(items))) if n.end is not None and n.beat <= beat < n.end), None)
