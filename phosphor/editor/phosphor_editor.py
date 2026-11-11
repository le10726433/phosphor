"""Phosphor decay preview and editor for the Appearance preferences.

The plot shows the light one beam pass leaves, as the display tone-maps it, on a
logarithmic time axis. The fill under the curve takes the emitted colour at each
moment, so a two-colour phosphor such as P7 visibly fades from flash to afterglow.
Dragging the flash or afterglow point, typing a value or picking a colour emits
edited(profile); the preferences dialog then switches the display to Custom.
"""
import math
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap, QIcon
from PySide6.QtWidgets import (QColorDialog, QDoubleSpinBox, QGridLayout, QLabel, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)
from phosphor.display.phosphor import PROFILE_RANGES, afterglow_level_at, decay_preview

T_MIN, T_MAX = 1e-3, 5.
HANDLE_RADIUS = 6
GRAB_RADIUS = 12


def time_at(fraction):
    return T_MIN*(T_MAX/T_MIN)**fraction


def fraction_at(seconds):
    return math.log(seconds/T_MIN)/math.log(T_MAX/T_MIN)


def time_label(seconds):
    return f'{seconds*1000:g} ms' if seconds < 1 else f'{seconds:g} s'


class DecayPlot(QWidget):
    edited = Signal(object)

    def __init__(self, theme):
        super().__init__()
        self.theme = theme
        self.profile = None
        self.intensity = 1.
        self.drag = None
        self.setMinimumSize(380, 200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)

    def set_profile(self, profile, intensity):
        self.profile, self.intensity = dict(profile), intensity
        self.update()

    def plot_rect(self):
        return QRectF(44, 10, max(1., self.width()-56), max(1., self.height()-36))

    def to_point(self, seconds, brightness):
        r = self.plot_rect()
        return QPointF(r.left()+fraction_at(seconds)*r.width(), r.bottom()-brightness*r.height())

    def handles(self):
        """Flash and afterglow points, each on its own curve at its decay time."""
        p = self.profile
        flash, _, _ = decay_preview(p, self.intensity, [p['flash_time']])
        _, afterglow, _ = decay_preview(p, self.intensity, [p['afterglow_time']])
        return dict(flash=self.to_point(p['flash_time'], flash[0]),
                    afterglow=self.to_point(p['afterglow_time'], afterglow[0]))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        theme = self.theme
        painter.fillRect(self.rect(), QColor(theme['field']))
        r = self.plot_rect()
        if not self.isEnabled():
            painter.setOpacity(.35)
        grid = QColor(theme['border'])
        painter.setPen(QPen(grid, 1))
        font = painter.font(); font.setPointSizeF(max(7., font.pointSizeF()-2)); painter.setFont(font)
        for decade in (1e-3, 1e-2, 1e-1, 1.):
            x = self.to_point(decade, 0).x()
            painter.setPen(QPen(grid, 1)); painter.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            painter.setPen(QColor(theme['muted']))
            painter.drawText(QRectF(x-30, r.bottom()+3, 60, 16), Qt.AlignmentFlag.AlignHCenter, time_label(decade))
        for level in (0., .5, 1.):
            y = r.bottom()-level*r.height()
            painter.setPen(QPen(grid, 1)); painter.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            painter.setPen(QColor(theme['muted']))
            painter.drawText(QRectF(0, y-8, r.left()-6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f'{level:g}')
        if self.profile is None:
            return
        columns = max(2, int(r.width()))
        times = time_at(np.linspace(0, 1, columns))
        flash, afterglow, light = decay_preview(self.profile, self.intensity, times)
        brightness = light.max(axis=1)
        # Fill each column with the emitted colour at that moment, at full saturation.
        step = r.width()/(columns-1)
        for index in range(columns):
            if brightness[index] <= 1e-4:
                continue
            hue = light[index]/brightness[index]
            color = QColor.fromRgbF(*hue, .62)
            top = r.bottom()-brightness[index]*r.height()
            painter.fillRect(QRectF(r.left()+index*step-step/2, top, step+.6, r.bottom()-top), color)
        xs = r.left()+np.arange(columns)*step
        for values, key, style in ((flash, 'flash_color', Qt.PenStyle.DashLine), (afterglow, 'afterglow_color', Qt.PenStyle.DashLine),
                                   (brightness, None, Qt.PenStyle.SolidLine)):
            path = QPainterPath(QPointF(xs[0], r.bottom()-values[0]*r.height()))
            for x, value in zip(xs[1:], values[1:]):
                path.lineTo(QPointF(x, r.bottom()-value*r.height()))
            color = QColor(self.profile[key]) if key else QColor(theme['fg'])
            painter.setPen(QPen(color, 1.4 if key else 1.8, style))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        for name, point in self.handles().items():
            painter.setPen(QPen(QColor(theme['fg']), 2 if self.drag == name else 1.2))
            painter.setBrush(QColor(self.profile[f'{name}_color']))
            painter.drawEllipse(point, HANDLE_RADIUS, HANDLE_RADIUS)

    def handle_at(self, position):
        if self.profile is None or not self.isEnabled():
            return None
        found = [(math.dist((point.x(), point.y()), (position.x(), position.y())), name) for name, point in self.handles().items()]
        distance, name = min(found)
        return name if distance <= GRAB_RADIUS else None

    def mousePressEvent(self, event):
        self.drag = self.handle_at(event.position())
        event.setAccepted(self.drag is not None)
        self.update()

    def mouseMoveEvent(self, event):
        if self.drag is None:
            hover = self.handle_at(event.position())
            self.setCursor(Qt.CursorShape.SizeAllCursor if hover == 'afterglow' else Qt.CursorShape.SizeHorCursor if hover else Qt.CursorShape.ArrowCursor)
            return
        r = self.plot_rect()
        fraction = min(1., max(0., (event.position().x()-r.left())/r.width()))
        key = f'{self.drag}_time'
        low, high = PROFILE_RANGES[key]
        profile = dict(self.profile)
        profile[key] = min(high, max(low, time_at(fraction)))
        if self.drag == 'afterglow':
            brightness = (r.bottom()-event.position().y())/r.height()
            profile['afterglow_level'] = afterglow_level_at(profile, self.intensity, brightness)
        self.profile = profile
        self.update()
        self.edited.emit(dict(profile))

    def mouseReleaseEvent(self, event):
        self.drag = None
        self.update()


def swatch(color):
    pixmap = QPixmap(14, 14)
    pixmap.fill(QColor(color))
    return QIcon(pixmap)


class PhosphorEditor(QWidget):
    """Decay plot with typed fields and colour pickers, all editing one profile."""
    edited = Signal(object)
    FIELDS = (('flash_time', 'Flash decay (ms)', 1000., 1), ('afterglow_time', 'Afterglow decay (ms)', 1000., 1),
              ('afterglow_level', 'Afterglow level', 1., 3))

    def __init__(self, theme):
        super().__init__()
        self.profile = None
        self.syncing = False
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0)
        self.plot = DecayPlot(theme); self.plot.setObjectName('decay_plot')
        self.plot.edited.connect(self.plot_edited)
        layout.addWidget(self.plot, 1)
        grid = QGridLayout(); layout.addLayout(grid)
        self.fields = {}
        for row, (key, label, scale, decimals) in enumerate(self.FIELDS):
            low, high = PROFILE_RANGES[key]
            field = QDoubleSpinBox(); field.setObjectName(key)
            field.setDecimals(decimals); field.setRange(low*scale, high*scale)
            field.setSingleStep(.01 if scale == 1 else 1.); field.setKeyboardTracking(False)
            field.valueChanged.connect(lambda value, key=key, scale=scale: self.typed(key, value/scale))
            grid.addWidget(QLabel(label), row, 0); grid.addWidget(field, row, 1)
            self.fields[key] = (field, scale)
        self.colors = {}
        for row, (key, label) in enumerate((('flash_color', 'Flash colour'), ('afterglow_color', 'Afterglow colour'))):
            button = QPushButton(label); button.setObjectName(key)
            button.clicked.connect(lambda checked=False, key=key, label=label: self.pick(key, label))
            grid.addWidget(button, row, 2)
            self.colors[key] = button
        grid.setColumnStretch(1, 1)

    def set_theme(self, theme):
        self.plot.theme = theme
        self.plot.update()

    def set_profile(self, profile, intensity, enabled=True):
        self.profile = dict(profile)
        self.syncing = True
        try:
            for key, (field, scale) in self.fields.items():
                field.setValue(profile[key]*scale)
            for key, button in self.colors.items():
                button.setIcon(swatch(profile[key]))
        finally:
            self.syncing = False
        self.plot.set_profile(profile, intensity)
        self.setEnabled(enabled)

    def change(self, **values):
        if self.profile is None or self.syncing:
            return
        self.edited.emit(dict(self.profile, **values))

    def plot_edited(self, profile):
        self.edited.emit(dict(profile))

    def typed(self, key, value):
        low, high = PROFILE_RANGES[key]
        self.change(**{key: min(high, max(low, value))})

    def pick(self, key, label):
        color = QColorDialog.getColor(QColor(self.profile[key]), self, label)
        if color.isValid():
            self.set_color(key, color.name())

    def set_color(self, key, color):
        self.change(**{key: QColor(color).name()})
