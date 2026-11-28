"""Photorealistic 3D hardware style (Option 2 — Warm Beige Lab Oscilloscope) for Vintage mode.

Provides cached procedural surface textures (bump-lit matte pebble ABS plastic and anisotropic
brushed aluminum), a QProxyStyle painting frustum-sculpted 3D keycaps inside recessed cutout
wells with multi-pass cast shadows, glowing 3D glass LED jewels, machined ribbed aluminum fader
sliders with calibrated silkscreened tick scales, backlit glass LCD cavities, and a deep molded
3D CRT monitor hood with ventilation louvers, corner machine screws, and a metallic nameplate.

Installed on QApplication only while Vintage is active; Classic and Modern keep Qt's native style.
"""
from functools import lru_cache
import math

import numpy as np
from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath,
                           QPen, QPixmap, QPolygonF, QRadialGradient)
from PySide6.QtWidgets import (QAbstractButton, QAbstractSpinBox, QComboBox, QLineEdit,
                               QListWidget, QProxyStyle, QSlider, QStyle, QStyleOption,
                               QStyleOptionButton, QStyleOptionComboBox, QStyleOptionSlider,
                               QStyleOptionTab, QStyleOptionToolButton, QToolBar, QToolButton)


@lru_cache(maxsize=1)
def plastic_tile():
    """Seamless 192x192 injection-molded pebble ABS plastic with directional micro-bump lighting."""
    rng = np.random.default_rng(1987)
    size = 192
    # Multi-octave heightfield for authentic injection-molded pebble grain
    fine = rng.normal(0.0, 1.0, (size, size)).astype(np.float32)
    med = np.repeat(np.repeat(rng.normal(0.0, 1.0, (size // 2, size // 2)).astype(np.float32), 2, axis=0), 2, axis=1)
    broad = np.repeat(np.repeat(rng.normal(0.0, 0.7, (size // 4, size // 4)).astype(np.float32), 4, axis=0), 4, axis=1)
    # Soften medium octave slightly so pebbles read as tactile dimples
    med = 0.4 * med + 0.15 * (np.roll(med, 1, 0) + np.roll(med, -1, 0) + np.roll(med, 1, 1) + np.roll(med, -1, 1))
    height = 0.45 * fine + 0.40 * med + 0.15 * broad
    # Top-left directional micro-bump shading (dH/dx + dH/dy)
    dx = height - np.roll(height, 1, axis=1)
    dy = height - np.roll(height, 1, axis=0)
    bump = (dx + dy) * 3.4 + height * 1.9
    base = np.array([207.0, 200.0, 182.0], dtype=np.float32)  # Warm Tektronix/HP putty #cfc8b6
    rgb = np.clip(base[None, None, :] + bump[:, :, None], 0, 255).astype(np.uint8)
    buf = np.ascontiguousarray(np.dstack([rgb, np.full((size, size), 255, dtype=np.uint8)]))
    img = QImage(buf.data, size, size, size * 4, QImage.Format.Format_RGBA8888).copy()
    return QPixmap.fromImage(img)


@lru_cache(maxsize=1)
def aluminum_tile():
    """Seamless 512x64 anisotropic horizontal brushed-aluminum faceplate texture."""
    rng = np.random.default_rng(1991)
    w, h = 512, 64
    # Fine horizontal brush lines + subtle micro-scratches
    streaks = rng.normal(0.0, 4.8, (h, 1)).astype(np.float32)
    fine = rng.normal(0.0, 2.2, (h, w)).astype(np.float32)
    for _ in range(3):
        fine = 0.5 * fine + 0.25 * np.roll(fine, 1, axis=1) + 0.25 * np.roll(fine, -1, axis=1)
    # Subtle horizontal luster wave
    x = np.linspace(0, 2 * np.pi, w, endpoint=False, dtype=np.float32)
    sheen = (np.sin(x) * 2.5 + np.cos(2 * x) * 1.2)[None, :]
    grain = streaks + fine + sheen
    base = np.array([208.0, 204.0, 193.0], dtype=np.float32)  # Warm anodized aluminum
    rgb = np.clip(base[None, None, :] + grain[:, :, None], 0, 255).astype(np.uint8)
    buf = np.ascontiguousarray(np.dstack([rgb, np.full((h, w), 255, dtype=np.uint8)]))
    img = QImage(buf.data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
    return QPixmap.fromImage(img)


def draw_screw(p, cx, cy, r=4.6):
    """Countersunk stainless Phillips machine screw with recessed socket and specular rim."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    # Countersunk socket rim (outer catchlight + dark recess)
    p.setPen(QPen(QColor(248, 245, 236, 175), 0.9))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QPointF(cx + 0.5, cy + 0.6), r * 1.28, r * 1.28)
    ao = QRadialGradient(QPointF(cx, cy), r * 1.45)
    ao.setColorAt(0.55, QColor(24, 21, 17, 200))
    ao.setColorAt(0.84, QColor(24, 21, 17, 75))
    ao.setColorAt(1.0, QColor(24, 21, 17, 0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(ao)
    p.drawEllipse(QPointF(cx, cy), r * 1.45, r * 1.45)
    # Brushed steel domed head
    head = QRadialGradient(QPointF(cx - r * 0.36, cy - r * 0.36), r * 1.45)
    head.setColorAt(0.0, QColor('#fbf9f3'))
    head.setColorAt(0.32, QColor('#d8d3c5'))
    head.setColorAt(0.72, QColor('#948e7e'))
    head.setColorAt(0.92, QColor('#635e50'))
    head.setColorAt(1.0, QColor('#403c32'))
    p.setBrush(head)
    p.setPen(QPen(QColor('#363229'), 0.8))
    p.drawEllipse(QPointF(cx, cy), r, r)
    # Angled recessed Phillips cross groove with catchlight
    d = r * 0.56
    for dx, dy in ((d, d * 0.58), (-d * 0.58, d)):
        p.setPen(QPen(QColor('#211e18'), 1.35, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx - dx, cy - dy), QPointF(cx + dx, cy + dy))
        p.setPen(QPen(QColor(250, 248, 240, 145), 0.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx - dx + 0.65, cy - dy + 0.75), QPointF(cx + dx + 0.65, cy + dy + 0.75))
    p.restore()


_LED_COLORS = {
    'green': (QColor('#ebffef'), QColor('#34eb63'), QColor('#0c5e21'), QColor(52, 235, 99, 130)),
    'amber': (QColor('#fff7d6'), QColor('#ffa826'), QColor('#854304'), QColor(255, 168, 38, 130)),
    'red':   (QColor('#ffe3e0'), QColor('#f53b2e'), QColor('#730d06'), QColor(245, 59, 46, 135)),
    'off':   (QColor('#63695e'), QColor('#343830'), QColor('#171915'), None),
}


def draw_led_dome(p, cx, cy, r=3.1, state='off'):
    """3D glass LED jewel inside a machined dark-anodized collar with subsurface bloom."""
    hi, mid, lo, glow = _LED_COLORS.get(state, _LED_COLORS['off'])
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    # Soft colored light bleed onto the surrounding beige plastic when lit
    if glow is not None:
        halo = QRadialGradient(QPointF(cx, cy), r * 3.3)
        halo.setColorAt(0.0, glow)
        halo.setColorAt(0.45, QColor(glow.red(), glow.green(), glow.blue(), 48))
        halo.setColorAt(1.0, QColor(glow.red(), glow.green(), glow.blue(), 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(QPointF(cx, cy), r * 3.3, r * 3.3)
    # Machined recessed metallic bezel ring
    collar = QLinearGradient(QPointF(cx - r, cy - r), QPointF(cx + r, cy + r))
    collar.setColorAt(0.0, QColor('#292620'))
    collar.setColorAt(0.5, QColor('#4f4a3f'))
    collar.setColorAt(1.0, QColor('#e6e1d3'))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(collar)
    p.drawEllipse(QPointF(cx, cy), r + 1.45, r + 1.45)
    p.setBrush(QColor('#1c1a15'))
    p.drawEllipse(QPointF(cx, cy), r + 0.65, r + 0.65)
    # Glass dome radial gradient
    dome = QRadialGradient(QPointF(cx - r * 0.24, cy - r * 0.26), r * 1.25)
    dome.setColorAt(0.0, hi)
    dome.setColorAt(0.42, mid)
    dome.setColorAt(0.88, lo)
    dome.setColorAt(1.0, QColor(lo.red() // 2, lo.green() // 2, lo.blue() // 2))
    p.setBrush(dome)
    p.drawEllipse(QPointF(cx, cy), r, r)
    # Primary & secondary glass specular glints
    p.setBrush(QColor(255, 255, 255, 230 if state != 'off' else 95))
    p.drawEllipse(QPointF(cx - r * 0.32, cy - r * 0.34), max(0.75, r * 0.28), max(0.6, r * 0.22))
    if state != 'off':
        p.setBrush(QColor(hi.red(), hi.green(), hi.blue(), 110))
        p.drawEllipse(QPointF(cx + r * 0.28, cy + r * 0.30), max(0.5, r * 0.16), max(0.45, r * 0.14))
    p.restore()


def draw_keycap(p, rect, *, down=False, hover=False, disabled=False, radius=4.0, led=None, led_pos='top-right'):
    """Frustum-sculpted 3D molded ABS keycap with dished top face, sloped skirt, and cast shadow."""
    r = QRectF(rect)
    if r.width() < 6 or r.height() < 6:
        return
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    # 1. Recessed dark chassis cutout well + outer bottom-right rim catchlight
    well = r.adjusted(0.5, 0.5, -0.5, -0.5)
    p.setPen(QPen(QColor(250, 247, 238, 175), 1.0))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(well.adjusted(0.5, 0.5, 0.5, 0.5), radius + 0.6, radius + 0.6)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor('#2c2922'))
    p.drawRoundedRect(well, radius + 0.5, radius + 0.5)

    # 2. Multi-pass directional bottom-right cast shadow when raised
    cap = well.adjusted(1.3, 1.3, -1.6, -1.9)
    if down:
        cap.translate(0.7, 1.0)
    else:
        p.setBrush(QColor(20, 18, 13, 62))
        p.drawRoundedRect(cap.translated(1.8, 2.4), radius + 0.4, radius + 0.4)
        p.setBrush(QColor(20, 18, 13, 98))
        p.drawRoundedRect(cap.translated(1.0, 1.4), radius, radius)

    # 3. Sloped 3D trapezoidal keycap skirt (base body)
    skirt = QLinearGradient(cap.topLeft(), cap.bottomRight())
    if disabled:
        skirt.setColorAt(0.0, QColor('#d6d0be'))
        skirt.setColorAt(1.0, QColor('#a69f8c'))
    elif down:
        skirt.setColorAt(0.0, QColor('#9e9783'))
        skirt.setColorAt(0.5, QColor('#bfb8a3'))
        skirt.setColorAt(1.0, QColor('#d6cfbc'))
    elif hover:
        skirt.setColorAt(0.0, QColor('#fcf9f1'))
        skirt.setColorAt(0.45, QColor('#dfd8c6'))
        skirt.setColorAt(1.0, QColor('#968e7a'))
    else:
        skirt.setColorAt(0.0, QColor('#f7f3e8'))
        skirt.setColorAt(0.45, QColor('#d5ceba'))
        skirt.setColorAt(1.0, QColor('#8c8572'))
    p.setBrush(skirt)
    p.drawRoundedRect(cap, radius, radius)

    # 4. Sculpted slightly dished top face inset inside the chamfered skirt
    inset = 2.0 if (cap.width() >= 26 and cap.height() >= 20) else 1.2
    face = cap.adjusted(inset, inset, -inset, -inset - (0.3 if not down else 0.0))
    if face.width() > 4 and face.height() > 4:
        fgrad = QLinearGradient(face.topLeft(), face.bottomLeft())
        if disabled:
            fgrad.setColorAt(0.0, QColor('#cec8b6'))
            fgrad.setColorAt(1.0, QColor('#beb7a4'))
        elif down:
            fgrad.setColorAt(0.0, QColor('#b2aa96'))
            fgrad.setColorAt(0.35, QColor('#c4bca8'))
            fgrad.setColorAt(1.0, QColor('#d1cab7'))
        elif hover:
            fgrad.setColorAt(0.0, QColor('#f2ede0'))
            fgrad.setColorAt(0.42, QColor('#eae4d3'))
            fgrad.setColorAt(1.0, QColor('#dbd4c0'))
        else:
            fgrad.setColorAt(0.0, QColor('#ebe5d5'))
            fgrad.setColorAt(0.45, QColor('#e0d9c6'))
            fgrad.setColorAt(1.0, QColor('#cfc8b4'))
        p.setBrush(fgrad)
        p.drawRoundedRect(face, max(1.5, radius - 1.2), max(1.5, radius - 1.2))

        # Subtle concave studio sheen across the upper half of the keycap face
        if not down and not disabled:
            sheen = QRadialGradient(QPointF(face.left() + face.width() * 0.35, face.top() + face.height() * 0.25),
                                    max(face.width(), face.height()) * 0.85)
            sheen.setColorAt(0.0, QColor(255, 253, 246, 52))
            sheen.setColorAt(1.0, QColor(255, 253, 246, 0))
            p.setBrush(sheen)
            p.drawRoundedRect(face, max(1.5, radius - 1.2), max(1.5, radius - 1.2))

    # Subtle matte ABS pebble texture overlay
    p.save()
    path = QPainterPath()
    path.addRoundedRect(cap, radius, radius)
    p.setClipPath(path)
    p.setOpacity(0.18)
    p.fillRect(cap, QBrush(plastic_tile()))
    p.restore()

    # 5. Crisp 3D chamfered edge specular highlight & dark underside lip
    hi_color = QColor('#5c5648') if down else QColor('#fffdf7')
    lo_color = QColor('#ebe5d6') if down else QColor('#575245')
    p.setPen(QPen(hi_color, 1.1))
    p.drawLine(QPointF(cap.left() + radius, cap.top() + 0.6), QPointF(cap.right() - radius, cap.top() + 0.6))
    p.drawLine(QPointF(cap.left() + 0.6, cap.top() + radius), QPointF(cap.left() + 0.6, cap.bottom() - radius))
    p.setPen(QPen(lo_color, 1.1))
    p.drawLine(QPointF(cap.left() + radius, cap.bottom() - 0.6), QPointF(cap.right() - radius, cap.bottom() - 0.6))
    p.drawLine(QPointF(cap.right() - 0.6, cap.top() + radius), QPointF(cap.right() - 0.6, cap.bottom() - radius))

    # 6. Embedded 3D glass LED dome on wide keycaps, or glowing top LED bar on compact buttons
    if led is not None:
        if cap.width() >= 54 and cap.height() >= 22:
            lr = 2.7 if cap.height() < 26 else 3.2
            if led_pos == 'top-center':
                lx, ly = cap.center().x(), cap.top() + lr + 3.4
            elif led_pos == 'top-left':
                lx, ly = cap.left() + lr + 4.8, cap.top() + lr + 4.2
            else:
                lx, ly = cap.right() - lr - 4.8, cap.top() + lr + 4.2
            draw_led_dome(p, lx, ly, lr, led)
        elif led != 'off' and cap.width() >= 14:
            hi, mid, lo, _ = _LED_COLORS.get(led, _LED_COLORS['green'])
            bar = QRectF(cap.left() + 3.0, cap.top() + 1.5, cap.width() - 6.0, 2.5)
            bgrad = QLinearGradient(bar.topLeft(), bar.bottomLeft())
            bgrad.setColorAt(0.0, hi)
            bgrad.setColorAt(0.55, mid)
            bgrad.setColorAt(1.0, lo)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bgrad)
            p.drawRoundedRect(bar, 1.1, 1.1)
    p.restore()


def draw_lcd_well(p, rect, radius=3.0, focused=False):
    """Deep backlit dark-phosphor glass LCD window with inner bezel shadow and glass sheen."""
    r = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
    if r.width() < 6 or r.height() < 6:
        return
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    # Outer recessed bevel (dark top-left, bright bottom-right catchlight)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor('#3d392f'))
    p.drawRoundedRect(r, radius, radius)
    p.setPen(QPen(QColor('#f5f1e6'), 1.1))
    p.drawLine(QPointF(r.left() + 1, r.bottom()), QPointF(r.right(), r.bottom()))
    p.drawLine(QPointF(r.right(), r.top() + 1), QPointF(r.right(), r.bottom()))

    glass = r.adjusted(1.6, 1.6, -1.3, -1.3)
    # Backlit phosphor glass radial gradient
    bg = QRadialGradient(glass.center(), max(glass.width(), glass.height()) * 0.72)
    bg.setColorAt(0.0, QColor('#1f3222'))
    bg.setColorAt(0.65, QColor('#142116'))
    bg.setColorAt(1.0, QColor('#0b120c'))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(bg)
    p.drawRoundedRect(glass, max(1.0, radius - 1.0), max(1.0, radius - 1.0))

    # Inner top & left cast shadow from the recessed bezel wall
    clip = QPainterPath()
    clip.addRoundedRect(glass, max(1.0, radius - 1.0), max(1.0, radius - 1.0))
    p.setClipPath(clip)
    top_sh = QLinearGradient(glass.topLeft(), QPointF(glass.left(), glass.top() + 5.5))
    top_sh.setColorAt(0.0, QColor(0, 0, 0, 165))
    top_sh.setColorAt(1.0, QColor(0, 0, 0, 0))
    p.fillRect(QRectF(glass.left(), glass.top(), glass.width(), 5.5), top_sh)
    left_sh = QLinearGradient(glass.topLeft(), QPointF(glass.left() + 4.5, glass.top()))
    left_sh.setColorAt(0.0, QColor(0, 0, 0, 130))
    left_sh.setColorAt(1.0, QColor(0, 0, 0, 0))
    p.fillRect(QRectF(glass.left(), glass.top(), 4.5, glass.height()), left_sh)

    # Subtle curved glass reflection across the upper third
    sheen = QLinearGradient(glass.topLeft(), QPointF(glass.left(), glass.top() + glass.height() * 0.42))
    sheen.setColorAt(0.0, QColor(255, 255, 255, 24))
    sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.fillRect(QRectF(glass.left(), glass.top(), glass.width(), glass.height() * 0.42), sheen)

    if focused:
        p.setClipping(False)
        p.setPen(QPen(QColor('#39e865'), 1.15))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(glass.adjusted(0.5, 0.5, -0.5, -0.5), max(1.0, radius - 1.0), max(1.0, radius - 1.0))
    p.restore()


def draw_machined_fader(p, full_rect, groove_rect, handle_rect, *, enabled=True, pressed=False):
    """Calibrated silkscreened tick scale, deep milled slot, and sculpted aluminum fader cap."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    g = QRectF(groove_rect)
    h = QRectF(handle_rect)
    cy = g.center().y()
    slot_h = 6.4
    slot = QRectF(g.left() + 2, cy - slot_h / 2.0, max(4.0, g.width() - 4), slot_h)

    # 1. Silkscreened lab calibration ticks above & below the milled slot
    tick_count = 11
    for i in range(tick_count):
        tx = slot.left() + (slot.width() - 1.0) * (i / (tick_count - 1))
        major = i in (0, tick_count // 2, tick_count - 1)
        tlen = 4.6 if major else 2.6
        p.setPen(QPen(QColor('#3d382e' if major else '#756e5f'), 1.15 if major else 0.95))
        p.drawLine(QPointF(tx, slot.top() - 1.5 - tlen), QPointF(tx, slot.top() - 1.5))
        p.drawLine(QPointF(tx, slot.bottom() + 1.5), QPointF(tx, slot.bottom() + 1.5 + tlen))

    # 2. Deep recessed milled metal fader slot with inner shadow & dust lip
    slot_grad = QLinearGradient(slot.topLeft(), slot.bottomLeft())
    slot_grad.setColorAt(0.0, QColor('#0e0d0a'))
    slot_grad.setColorAt(0.45, QColor('#24211b'))
    slot_grad.setColorAt(1.0, QColor('#3d3930'))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(slot_grad)
    p.drawRoundedRect(slot, 2.8, 2.8)
    # Recessed top dark rim & bottom specular catchlight
    p.setPen(QPen(QColor('#f7f4ea'), 1.0))
    p.drawLine(QPointF(slot.left() + 2, slot.bottom() + 0.6), QPointF(slot.right() - 2, slot.bottom() + 0.6))

    # 3. Multi-pass directional cast shadow beneath the machined fader cap
    knob = QRectF(h.center().x() - 8.5, cy - 9.5, 17.0, 19.0)
    if knob.top() < full_rect.top() + 0.5:
        knob = QRectF(h.center().x() - 8.5, full_rect.top() + 0.5, 17.0, max(13.0, full_rect.height() - 1.5))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(20, 18, 13, 60 if not pressed else 40))
    p.drawRoundedRect(knob.translated(2.8, 3.2), 2.8, 2.8)
    p.setBrush(QColor(20, 18, 13, 105 if not pressed else 75))
    p.drawRoundedRect(knob.translated(1.5, 1.9), 2.4, 2.4)

    # 4. Machined concave-profile anodized aluminum fader cap
    kgrad = QLinearGradient(knob.topLeft(), knob.topRight())
    if enabled:
        kgrad.setColorAt(0.0, QColor('#8c877a'))
        kgrad.setColorAt(0.16, QColor('#f4f1e8'))
        kgrad.setColorAt(0.38, QColor('#c9c4b5'))
        kgrad.setColorAt(0.50, QColor('#b8b2a2'))
        kgrad.setColorAt(0.62, QColor('#d6d1c3'))
        kgrad.setColorAt(0.84, QColor('#eeeae0'))
        kgrad.setColorAt(1.0, QColor('#757063'))
    else:
        kgrad.setColorAt(0.0, QColor('#aba597'))
        kgrad.setColorAt(1.0, QColor('#918b7d'))
    p.setBrush(kgrad)
    p.drawRoundedRect(knob, 2.4, 2.4)

    # Knurled side grip ribs + high-contrast engraved center index line
    cx = knob.center().x()
    for dx in (-4.2, -2.4, 2.4, 4.2):
        rx = cx + dx
        p.setPen(QPen(QColor('#4d483d'), 0.9))
        p.drawLine(QPointF(rx, knob.top() + 3.0), QPointF(rx, knob.bottom() - 3.0))
        p.setPen(QPen(QColor(255, 253, 248, 170), 0.8))
        p.drawLine(QPointF(rx + 0.85, knob.top() + 3.0), QPointF(rx + 0.85, knob.bottom() - 3.0))
    # Center enamel-filled pointer groove (classic lab amber/charcoal indicator)
    p.setPen(QPen(QColor('#c95a14' if enabled else '#5e584a'), 1.8))
    p.drawLine(QPointF(cx, knob.top() + 2.2), QPointF(cx, knob.bottom() - 2.2))

    # Crisp specular top/left rim & dark underside chamfer
    p.setPen(QPen(QColor('#ffffff'), 1.0))
    p.drawLine(QPointF(knob.left() + 1.5, knob.top() + 0.6), QPointF(knob.right() - 1.5, knob.top() + 0.6))
    p.drawLine(QPointF(knob.left() + 0.6, knob.top() + 1.5), QPointF(knob.left() + 0.6, knob.bottom() - 1.5))
    p.setPen(QPen(QColor('#3b372e'), 1.2))
    p.drawLine(QPointF(knob.left() + 1.5, knob.bottom() - 0.6), QPointF(knob.right() - 1.5, knob.bottom() - 0.6))
    p.drawLine(QPointF(knob.right() - 0.6, knob.top() + 1.5), QPointF(knob.right() - 0.6, knob.bottom() - 1.5))
    p.restore()


def draw_crt_hood(p, host_rect, bezel=34, trace_hex='#5ce677'):
    """Deep molded warm-beige ABS CRT monitor cowling with louvers, screws, nameplate, and phosphor spill."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    inner = QRectF(host_rect)
    outer = inner.adjusted(-bezel, -bezel, bezel, bezel)

    # 1. Soft multi-pass cast shadow beneath the monitor hood
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(22, 19, 14, 48))
    p.drawRoundedRect(outer.translated(5.0, 6.5), 14, 14)
    p.setBrush(QColor(22, 19, 14, 92))
    p.drawRoundedRect(outer.translated(2.2, 3.0), 12, 12)

    # 2. Molded warm-beige ABS hood body + tactile pebble grain
    body = QLinearGradient(outer.topLeft(), outer.bottomRight())
    body.setColorAt(0.0, QColor('#eee8d9'))
    body.setColorAt(0.42, QColor('#d2cbb9'))
    body.setColorAt(0.85, QColor('#b5ad98'))
    body.setColorAt(1.0, QColor('#9e9682'))
    p.setBrush(body)
    p.drawRoundedRect(outer, 12, 12)
    p.save()
    clip = QPainterPath()
    clip.addRoundedRect(outer, 12, 12)
    p.setClipPath(clip)
    p.setOpacity(0.24)
    p.fillRect(outer, QBrush(plastic_tile()))
    p.restore()

    # 3. Outer specular top-left chamfer & dark bottom-right edge
    p.setPen(QPen(QColor('#fdfbf4'), 1.8))
    p.drawLine(QPointF(outer.left() + 11, outer.top() + 1.2), QPointF(outer.right() - 11, outer.top() + 1.2))
    p.drawLine(QPointF(outer.left() + 1.2, outer.top() + 11), QPointF(outer.left() + 1.2, outer.bottom() - 11))
    p.setPen(QPen(QColor('#524d41'), 2.0))
    p.drawLine(QPointF(outer.left() + 11, outer.bottom() - 1.2), QPointF(outer.right() - 11, outer.bottom() - 1.2))
    p.drawLine(QPointF(outer.right() - 1.2, outer.top() + 11), QPointF(outer.right() - 1.2, outer.bottom() - 11))

    # 4. Subtle molded cooling louver slots along the top flange
    louver_w = min(140.0, inner.width() * 0.34)
    if louver_w >= 60:
        lx0 = inner.center().x() - louver_w / 2.0
        ly = outer.top() + 6.5
        step = 10.0
        count = int(louver_w // step)
        for i in range(count):
            sx = lx0 + i * step
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor('#595346'))
            p.drawRoundedRect(QRectF(sx, ly, 5.5, 3.2), 1.3, 1.3)
            p.setPen(QPen(QColor('#f5f1e6'), 0.8))
            p.drawLine(QPointF(sx + 0.8, ly + 3.7), QPointF(sx + 4.8, ly + 3.7))

    # 5. Deep sloped inner CRT cowling walls (16 px recess)
    recess = inner.adjusted(-16, -16, 16, 16)
    # Recessed lip catchlight around the outer recess edge
    p.setPen(QPen(QColor('#4d483c'), 1.2))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(recess.adjusted(-0.6, -0.6, 0.6, 0.6), 4, 4)

    top_wall = QPolygonF([recess.topLeft(), recess.topRight(), inner.topRight(), inner.topLeft()])
    left_wall = QPolygonF([recess.topLeft(), inner.topLeft(), inner.bottomLeft(), recess.bottomLeft()])
    bot_wall = QPolygonF([inner.bottomLeft(), inner.bottomRight(), recess.bottomRight(), recess.bottomLeft()])
    right_wall = QPolygonF([inner.topRight(), recess.topRight(), recess.bottomRight(), inner.bottomRight()])

    tgrad = QLinearGradient(recess.topLeft(), inner.topLeft())
    tgrad.setColorAt(0.0, QColor('#575145')); tgrad.setColorAt(1.0, QColor('#171612'))
    lgrad = QLinearGradient(recess.topLeft(), inner.topLeft())
    lgrad.setColorAt(0.0, QColor('#6e6859')); lgrad.setColorAt(1.0, QColor('#211f1a'))
    bgrad = QLinearGradient(inner.bottomLeft(), recess.bottomLeft())
    bgrad.setColorAt(0.0, QColor('#333028')); bgrad.setColorAt(0.65, QColor('#a69f8c')); bgrad.setColorAt(1.0, QColor('#d6d0be'))
    rgrad = QLinearGradient(inner.topRight(), recess.topRight())
    rgrad.setColorAt(0.0, QColor('#2b2822')); rgrad.setColorAt(0.7, QColor('#918a78')); rgrad.setColorAt(1.0, QColor('#b8b09c'))

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(tgrad); p.drawPolygon(top_wall)
    p.setBrush(lgrad); p.drawPolygon(left_wall)
    p.setBrush(bgrad); p.drawPolygon(bot_wall)
    p.setBrush(rgrad); p.drawPolygon(right_wall)

    # Miter corner seams inside the cowling
    p.setPen(QPen(QColor(18, 16, 12, 130), 1.0))
    p.drawLine(recess.topLeft(), inner.topLeft())
    p.drawLine(recess.topRight(), inner.topRight())
    p.drawLine(recess.bottomLeft(), inner.bottomLeft())
    p.drawLine(recess.bottomRight(), inner.bottomRight())

    # Subtle trace-colored phosphor bounce light reflected onto the inner bevel walls
    spill = QColor(trace_hex)
    spill.setAlpha(42)
    p.setPen(QPen(spill, 6.0))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(inner.adjusted(-3.5, -3.5, 3.5, 3.5))
    spill2 = QColor(trace_hex)
    spill2.setAlpha(18)
    p.setPen(QPen(spill2, 11.0))
    p.drawRect(inner.adjusted(-6.5, -6.5, 6.5, 6.5))

    # Crisp black molded rubber CRT gasket
    p.setPen(QPen(QColor('#0e0d0a'), 2.8))
    p.drawRect(inner.adjusted(-1.2, -1.2, 1.2, 1.2))

    # 6. Four countersunk Phillips corner machine screws
    inset = 11.5
    for sx, sy in ((outer.left() + inset, outer.top() + inset),
                   (outer.right() - inset, outer.top() + inset),
                   (outer.left() + inset, outer.bottom() - inset),
                   (outer.right() - inset, outer.bottom() - inset)):
        draw_screw(p, sx, sy, 4.6)

    # 7. Recessed anodized aluminum nameplate badge + glowing PWR LED on the bottom bezel lip
    badge_w = min(195.0, max(138.0, inner.width() * 0.68))
    if inner.width() >= 180:
        badge = QRectF(inner.center().x() - badge_w / 2.0, outer.bottom() - 16.5, badge_w, 13.0)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor('#2c2922'))
        p.drawRoundedRect(badge, 2.0, 2.0)
        p.setPen(QPen(QColor('#f4f0e4'), 0.8))
        p.drawLine(QPointF(badge.left() + 1, badge.bottom() + 0.5), QPointF(badge.right() - 1, badge.bottom() + 0.5))
        font = QFont('JetBrains Mono')
        font.setPixelSize(8)
        font.setBold(True)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.3)
        p.setFont(font)
        p.setPen(QColor('#e8e2d0'))
        p.drawText(badge, Qt.AlignmentFlag.AlignCenter, 'PHOSPHOR · X–Y CRT')
        # Power indicator LED jewel to the right of the nameplate
        draw_led_dome(p, badge.right() + 14.0, badge.center().y(), 2.6, 'green')
    p.restore()


def paint_aluminum_bar(painter, rect):
    """Paint a realistic rackmount brushed-aluminum faceplate with sheen and corner screws."""
    r = QRectF(rect)
    painter.save()
    painter.fillRect(r, QBrush(aluminum_tile()))
    sheen = QLinearGradient(r.topLeft(), r.bottomLeft())
    sheen.setColorAt(0.0, QColor(255, 255, 255, 72))
    sheen.setColorAt(0.42, QColor(255, 255, 255, 14))
    sheen.setColorAt(0.54, QColor(0, 0, 0, 14))
    sheen.setColorAt(1.0, QColor(0, 0, 0, 48))
    painter.fillRect(r, sheen)
    painter.setPen(QPen(QColor('#f8f5ec'), 1.0))
    painter.drawLine(r.topLeft(), r.topRight())
    painter.setPen(QPen(QColor('#4a453a'), 2.0))
    painter.drawLine(QPointF(r.left(), r.bottom() - 0.5), QPointF(r.right(), r.bottom() - 0.5))
    # Subtle engraved horizontal chassis pin-stripe
    painter.setPen(QPen(QColor(82, 76, 64, 75), 1.0))
    painter.drawLine(QPointF(r.left() + 18, r.bottom() - 3.0), QPointF(r.right() - 18, r.bottom() - 3.0))
    # Rackmount corner screws on left & right margins
    if r.height() >= 32 and r.width() >= 240:
        for sx in (r.left() + 8.5, r.right() - 8.5):
            draw_screw(painter, sx, r.top() + 11.0, 3.6)
            draw_screw(painter, sx, r.bottom() - 11.0, 3.6)
    painter.restore()


class VintageProxyStyle(QProxyStyle):
    """QProxyStyle rendering photorealistic 3D lab-instrument materials and controls."""

    def _led_state(self, option, widget):
        """Return LED dome color state for checkable controls, or None if not checkable."""
        if not isinstance(widget, QAbstractButton) or not widget.isCheckable():
            return None
        on = bool(option.state & QStyle.StateFlag.State_On)
        if not on:
            return 'off'
        name = widget.objectName()
        text = widget.text().strip()
        if name == 'composer_record' or text == 'Record':
            return 'red'
        if text in ('S', 'M') or 'solo' in name.lower():
            return 'amber'
        return 'green'

    def drawPrimitive(self, element, option, painter, widget=None):
        if element == QStyle.PrimitiveElement.PE_Widget:
            if isinstance(widget, QToolBar) and widget.objectName() == 'app_bar':
                paint_aluminum_bar(painter, option.rect)
                return
            if widget is not None and not isinstance(widget, (QLineEdit, QAbstractSpinBox, QListWidget)):
                if widget.autoFillBackground() or widget.objectName() in ('sidebar', 'top_row', 'composer_panel', ''):
                    if widget.parentWidget() is None or widget.objectName() in ('sidebar', 'composer_panel', 'monitor_page'):
                        painter.fillRect(option.rect, QBrush(plastic_tile()))
                        return
        elif element == QStyle.PrimitiveElement.PE_PanelToolBar:
            paint_aluminum_bar(painter, option.rect)
            return
        elif element == QStyle.PrimitiveElement.PE_FrameTabWidget:
            r = QRectF(option.rect).adjusted(0.5, 0.5, -0.5, -0.5)
            painter.save()
            painter.fillRect(r, QBrush(plastic_tile()))
            # Subtle studio top-left highlight wash inside the recessed sub-panel
            wash = QLinearGradient(r.topLeft(), r.bottomRight())
            wash.setColorAt(0.0, QColor(250, 247, 238, 38))
            wash.setColorAt(1.0, QColor(45, 41, 33, 22))
            painter.fillRect(r, wash)
            painter.setPen(QPen(QColor('#f7f4ea'), 1.5))
            painter.drawLine(r.topLeft(), r.topRight())
            painter.drawLine(r.topLeft(), r.bottomLeft())
            painter.setPen(QPen(QColor('#595447'), 1.8))
            painter.drawLine(r.bottomLeft(), r.bottomRight())
            painter.drawLine(r.topRight(), r.bottomRight())
            painter.restore()
            return
        elif element == QStyle.PrimitiveElement.PE_PanelButtonCommand:
            down = bool(option.state & (QStyle.StateFlag.State_Sunken | QStyle.StateFlag.State_On))
            hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
            disabled = not bool(option.state & QStyle.StateFlag.State_Enabled)
            led = self._led_state(option, widget)
            has_menu = hasattr(widget, 'menu') and widget.menu() is not None
            draw_keycap(painter, option.rect, down=down, hover=hover, disabled=disabled, radius=3.5,
                        led=led, led_pos='top-left' if has_menu else 'top-right')
            if has_menu:
                self._draw_chevron(painter, QPointF(option.rect.right() - 10, option.rect.center().y()),
                                   QColor('#1e1c17' if not disabled else '#5c574b'))
            return
        elif element == QStyle.PrimitiveElement.PE_PanelLineEdit:
            focused = bool(option.state & QStyle.StateFlag.State_HasFocus)
            draw_lcd_well(painter, option.rect, radius=3.0, focused=focused)
            return
        elif element == QStyle.PrimitiveElement.PE_IndicatorCheckBox:
            r = QRectF(option.rect).adjusted(0.5, 0.5, -0.5, -0.5)
            checked = bool(option.state & QStyle.StateFlag.State_On)
            draw_lcd_well(painter, r, radius=2.5, focused=checked)
            if checked:
                painter.save()
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                inner = r.adjusted(2.2, 2.2, -2.2, -2.2)
                g = QRadialGradient(inner.center(), inner.width() * 0.8)
                g.setColorAt(0.0, QColor('#42f26e'))
                g.setColorAt(1.0, QColor('#14662c'))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(g)
                painter.drawRoundedRect(inner, 1.5, 1.5)
                pen = QPen(QColor('#ffffff'), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                x0, y0, w, h = inner.x(), inner.y(), inner.width(), inner.height()
                painter.drawPolyline(QPolygonF([
                    QPointF(x0 + w * 0.20, y0 + h * 0.52),
                    QPointF(x0 + w * 0.43, y0 + h * 0.76),
                    QPointF(x0 + w * 0.82, y0 + h * 0.26),
                ]))
                painter.restore()
            return
        super().drawPrimitive(element, option, painter, widget)

    def drawControl(self, element, option, painter, widget=None):
        if element == QStyle.ControlElement.CE_TabBarTabShape:
            r = QRectF(option.rect).adjusted(0.5, 1.0, -1.0, 0.0)
            selected = bool(option.state & QStyle.StateFlag.State_Selected)
            hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            if not selected:
                r = r.adjusted(0.0, 2.5, 0.0, 0.0)
            grad = QLinearGradient(r.topLeft(), r.bottomLeft())
            if selected:
                grad.setColorAt(0.0, QColor('#f7f3e8'))
                grad.setColorAt(0.28, QColor('#e6e0ce'))
                grad.setColorAt(1.0, QColor('#d2cbb8'))
            elif hover:
                grad.setColorAt(0.0, QColor('#e0dac9'))
                grad.setColorAt(1.0, QColor('#c2baa4'))
            else:
                grad.setColorAt(0.0, QColor('#cbc4b1'))
                grad.setColorAt(1.0, QColor('#aea692'))
            path = QPainterPath()
            path.moveTo(r.bottomLeft())
            path.lineTo(r.left(), r.top() + 4)
            path.quadTo(r.topLeft(), QPointF(r.left() + 4, r.top()))
            path.lineTo(r.right() - 4, r.top())
            path.quadTo(r.topRight(), QPointF(r.right(), r.top() + 4))
            path.lineTo(r.bottomRight())
            path.closeSubpath()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(grad)
            painter.drawPath(path)
            # Subtle pebble grain on tab face
            painter.save()
            painter.setClipPath(path)
            painter.setOpacity(0.16)
            painter.fillRect(r, QBrush(plastic_tile()))
            painter.restore()
            # Crisp 3D bevel edges
            painter.setPen(QPen(QColor('#fffdf7' if selected else '#e8e2d2'), 1.2))
            painter.drawLine(QPointF(r.left() + 3, r.top() + 0.6), QPointF(r.right() - 3, r.top() + 0.6))
            painter.drawLine(QPointF(r.left() + 0.6, r.top() + 3), QPointF(r.left() + 0.6, r.bottom()))
            painter.setPen(QPen(QColor('#595345'), 1.2))
            painter.drawLine(QPointF(r.right() - 0.6, r.top() + 3), QPointF(r.right() - 0.6, r.bottom()))
            # Embedded LED indicator dome on wide selected tabs, glowing top bar on compact tabs
            if selected:
                if r.width() >= 82:
                    draw_led_dome(painter, r.right() - 7.5, r.top() + 6.5, 2.4, 'green')
                elif r.width() >= 24:
                    hi, mid, lo, _ = _LED_COLORS['green']
                    bar = QRectF(r.left() + 5.0, r.top() + 2.0, r.width() - 10.0, 2.5)
                    bgrad = QLinearGradient(bar.topLeft(), bar.bottomLeft())
                    bgrad.setColorAt(0.0, hi); bgrad.setColorAt(0.55, mid); bgrad.setColorAt(1.0, lo)
                    painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(bgrad)
                    painter.drawRoundedRect(bar, 1.1, 1.1)
            painter.restore()
            return
        elif element == QStyle.ControlElement.CE_PushButtonBevel:
            self.drawPrimitive(QStyle.PrimitiveElement.PE_PanelButtonCommand, option, painter, widget)
            return
        super().drawControl(element, option, painter, widget)

    def pixelMetric(self, metric, option=None, widget=None):
        if metric == QStyle.PixelMetric.PM_TabBarTabHSpace:
            return 10 if (widget is not None and hasattr(widget, 'count') and widget.count() >= 4) else 16
        return super().pixelMetric(metric, option, widget)

    def drawComplexControl(self, control, option, painter, widget=None):
        if control == QStyle.ComplexControl.CC_ToolButton and isinstance(option, QStyleOptionToolButton):
            down = bool(option.state & (QStyle.StateFlag.State_Sunken | QStyle.StateFlag.State_On))
            hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
            disabled = not bool(option.state & QStyle.StateFlag.State_Enabled)
            led = self._led_state(option, widget)
            compact = option.rect.height() <= 22
            has_menu = bool(option.features & QStyleOptionToolButton.ToolButtonFeature.HasMenu)
            draw_keycap(painter, option.rect, down=down, hover=hover, disabled=disabled,
                        radius=2.5 if compact else 4.0, led=None if compact else led,
                        led_pos='top-left' if has_menu else 'top-right')
            label_opt = QStyleOptionToolButton(option)
            label_opt.state &= ~(QStyle.StateFlag.State_Sunken | QStyle.StateFlag.State_Raised)
            if has_menu:
                label_opt.rect = option.rect.adjusted(0, 0, -14, 0)
            if down:
                label_opt.rect = label_opt.rect.translated(1, 1)
            self.drawControl(QStyle.ControlElement.CE_ToolButtonLabel, label_opt, painter, widget)
            if has_menu:
                ar = QRect(option.rect.right() - 15, option.rect.top(), 13, option.rect.height())
                self._draw_chevron(painter, ar.center(), QColor('#1e1c17' if not disabled else '#5c574b'))
            return

        if control == QStyle.ComplexControl.CC_Slider and isinstance(option, QStyleOptionSlider):
            if option.orientation == Qt.Orientation.Horizontal:
                groove = self.subControlRect(control, option, QStyle.SubControl.SC_SliderGroove, widget)
                handle = self.subControlRect(control, option, QStyle.SubControl.SC_SliderHandle, widget)
                enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
                pressed = bool(option.activeSubControls & QStyle.SubControl.SC_SliderHandle and option.state & QStyle.StateFlag.State_Sunken)
                draw_machined_fader(painter, option.rect, groove, handle, enabled=enabled, pressed=pressed)
                return

        if control == QStyle.ComplexControl.CC_ComboBox and isinstance(option, QStyleOptionComboBox):
            down = bool(option.state & QStyle.StateFlag.State_Sunken)
            hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
            disabled = not bool(option.state & QStyle.StateFlag.State_Enabled)
            draw_keycap(painter, option.rect, down=down, hover=hover, disabled=disabled, radius=3.5)
            ax = option.rect.right() - 20
            painter.save()
            painter.setPen(QPen(QColor('#7a7363'), 1.0))
            painter.drawLine(ax, option.rect.top() + 5, ax, option.rect.bottom() - 5)
            painter.setPen(QPen(QColor('#f4f0e4'), 1.0))
            painter.drawLine(ax + 1, option.rect.top() + 5, ax + 1, option.rect.bottom() - 5)
            self._draw_chevron(painter, QPointF(option.rect.right() - 10, option.rect.center().y()),
                               QColor('#1e1c17' if not disabled else '#5c574b'))
            painter.restore()
            return

        super().drawComplexControl(control, option, painter, widget)

    @staticmethod
    def _draw_chevron(painter, center, color):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        cx, cy = center.x(), center.y()
        painter.drawPolyline(QPolygonF([QPointF(cx - 3.5, cy - 1.5), QPointF(cx, cy + 2.2), QPointF(cx + 3.5, cy - 1.5)]))
        painter.restore()
