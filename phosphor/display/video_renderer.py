"""GUI-thread, fixed-step Qt Quick renderer, independent of the encoder."""
import numpy as np
from PySide6.QtCore import QSize, QSizeF
from PySide6.QtGui import (QColor, QImage, QPainter, QRhiTexture, QRhiColorAttachment,
    QRhiTextureRenderTargetDescription, QRhiReadbackResult, QRhiReadbackDescription)
from PySide6.QtQuick import QQuickWindow, QQuickRenderControl, QQuickRenderTarget
from phosphor.display.crt_theme import PALETTES
from phosphor.display.qt_scope import GpuScope
from phosphor.display.qt_phosphor import PhosphorLayer
from phosphor.display.frame_step import step_display
from phosphor.display.trace_interpolation import interpolate_trace
from phosphor.engine.video_export import frame_ranges
from phosphor.display.export_nv12 import Nv12Pass, nv12_possible

# Qt wrappers must outlive native scene graph teardown, just as ViewHost's closed layer.
_closed_layers = []


class VideoRenderer:
    def __init__(self, preferences, width, height, fps, samples, rate=48000, nv12=False):
        self.preferences = dict(preferences); self.width, self.height = width, height
        self.fps, self.samples = fps, np.asarray(samples)
        self.ranges = list(frame_ranges(len(samples), rate, fps)); self.next_frame = 0
        self.notice = ''
        # A grab of a never-shown window tears its scene graph down after each frame, which
        # loses the phosphor's feedback textures. QQuickRenderControl keeps one scene graph
        # rendering into our own texture, read back through QRhi.
        self.control = QQuickRenderControl()
        self.view = QQuickWindow(self.control); side = self.side = min(width, height)
        self.view.resize(side, side); self.view.contentItem().setSize(QSizeF(side, side))
        self.view.setColor(QColor(PALETTES[preferences['palette']]['background']))
        self.scope = GpuScope(self.view.contentItem()); self.scope.setWidth(side); self.scope.setHeight(side)
        self.phosphor = PhosphorLayer(self.view); self.phosphor.resize(side, side)
        if not self.control.initialize():
            self.close(); raise RuntimeError('Offscreen Qt Quick rendering could not start.')
        self.rhi = self.control.rhi()
        self.texture = self.rhi.newTexture(QRhiTexture.Format.RGBA8, QSize(side, side), 1,
            QRhiTexture.Flag.RenderTarget | QRhiTexture.Flag.UsedAsTransferSource)
        if not self.texture.create(): self.close(); raise RuntimeError('Could not create the export texture.')
        self.target = self.rhi.newTextureRenderTarget(QRhiTextureRenderTargetDescription(QRhiColorAttachment(self.texture)))
        self.pass_descriptor = self.target.newCompatibleRenderPassDescriptor()
        self.target.setRenderPassDescriptor(self.pass_descriptor)
        if not self.target.create(): self.close(); raise RuntimeError('Could not create the export render target.')
        self.view.setRenderTarget(QQuickRenderTarget.fromRhiRenderTarget(self.target))
        scope = self.scope; scope.colors = PALETTES[preferences['palette']]
        scope.show_grid = preferences['show_grid']; scope.divisions = preferences['grid_divisions']
        scope.scanlines = preferences['crt_scanlines']; scope.glow = preferences['crt_glow']
        for key in ('trace_thickness', 'glow_size', 'glow_intensity', 'glow_decay', 'beam_brightness'):
            setattr(scope, key, preferences[key])
        # nv12: frames leave the GPU as packed NV12 of the square (export_nv12); otherwise
        # frame() returns the composed RGBA picture. The pass needs D3D11/Metal orientation.
        self.nv12 = None; self.unread = False
        if nv12 and nv12_possible(self.rhi, width, height):
            try: self.nv12 = Nv12Pass(self.rhi, self.texture, side)
            except Exception as error: self.notice = f'GPU colour conversion unavailable ({error}).'

    def advance(self, k):
        if k != self.next_frame: raise ValueError('Render frames in sample-clock order, starting at zero.')
        start, end = self.ranges[k]
        signal = interpolate_trace(self.samples[max(0, start-1):end], self.preferences['trace_interpolation'])
        step_display(self, self.preferences, signal, end-start, 1/self.fps)
        self.scope.update()
        self.scope.frame_time = k / self.fps

    def frame_nv12(self, k):
        """Render frame k and return frame k-1's NV12 square readback (None for k == 0).

        The readback of the previous frame is recorded before this frame's passes, so the
        GPU copy waits only for work already submitted: the CPU prepares frame k while the
        GPU finishes k-1 (double buffering through command order; D3D11 maps only the copy,
        while Metal's offscreen frame waits for its whole command buffer, so there the gain
        is the smaller readback). Call finish_nv12() after the last frame for its picture.
        """
        self.advance(k)
        control = self.control
        control.polishItems(); control.beginFrame()
        readback = self.nv12.read(control.commandBuffer()) if self.unread else None
        control.sync(); control.render()
        self.nv12.record(control.commandBuffer()); control.endFrame()
        self.next_frame += 1; self.unread = True
        return None if readback is None else self.nv12_data(readback)

    def finish_nv12(self):
        if not self.unread: return None
        control = self.control
        control.beginFrame(); readback = self.nv12.read(control.commandBuffer()); control.endFrame()
        self.unread = False
        return self.nv12_data(readback)

    def nv12_data(self, readback):
        """Returns the QRhiReadbackResult itself: its .data wrapper dies with it, so the
        encoder thread keeps the result alive while it reads memoryview(result.data)."""
        if len(readback.data) < self.side*self.side*3//2: raise RuntimeError('Offscreen Qt Quick rendering returned no picture.')
        return readback

    def frame(self, k):
        self.advance(k)
        control = self.control
        control.polishItems(); control.beginFrame(); control.sync(); control.render()
        readback = QRhiReadbackResult(); batch = self.rhi.nextResourceUpdateBatch()
        batch.readBackTexture(QRhiReadbackDescription(self.texture), readback)
        control.commandBuffer().resourceUpdate(batch); control.endFrame()
        side = self.side; data = bytes(readback.data)
        if len(data) < side*side*4: raise RuntimeError('Offscreen Qt Quick rendering returned no picture.')
        picture = QImage(data, side, side, QImage.Format.Format_RGBA8888)
        if self.rhi.isYUpInFramebuffer(): picture = picture.mirrored(False, True)
        picture = picture.copy()
        result = QImage(self.width, self.height, QImage.Format.Format_RGBA8888)
        result.fill(QColor(PALETTES[self.preferences['palette']]['background']))
        painter = QPainter(result); painter.drawImage((self.width-side)//2, (self.height-side)//2, picture); painter.end()
        self.next_frame += 1
        return result

    def close(self):
        if self.phosphor is not None:
            if self.nv12 is not None: _closed_layers.append(self.nv12.release()); self.nv12 = None
            self.phosphor.close(); _closed_layers.append(self.phosphor); self.phosphor = None
            # Keep every wrapper alive: native teardown order is the window's, not Python's.
            _closed_layers.append((self.control, self.view, self.scope, getattr(self, 'target', None),
                                   getattr(self, 'pass_descriptor', None), getattr(self, 'texture', None)))
            self.view.close(); self.view.destroy()

    def __enter__(self): return self
    def __exit__(self, *args): self.close()
