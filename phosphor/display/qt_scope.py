"""Shared Direct3D/Qt Quick triangle viewport; all audio remains outside this item."""
import ctypes
from collections import deque
import time
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtQuick import QQuickItem,QSGNode,QSGGeometryNode,QSGGeometry,QSGFlatColorMaterial,QSGVertexColorMaterial
from phosphor.display.crt_theme import PALETTES
from phosphor.display.qt_geometry import BEAM_VERTEX, beam_triangles, stroke_triangles, grid_lines
from phosphor.display.glow_trail import GlowTrail, VISUAL_DEFAULTS
from phosphor.display.phosphor import view_scale

# Editing overlays (scope.overlays, bottom to top): guides, muted outlines, the three
# orientation axes, the position and resize handles (UI1: their own layer, so they fade on
# their own), and the cycle inspector's layers. The first five sit in layers 5–9; the rest
# after the glow trail, so they draw above everything else.
OVERLAY_LAYERS = 11
BASE_OVERLAYS = 5


class GpuScope(QQuickItem):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFlag(QQuickItem.Flag.ItemHasContents, True)
        self.setClip(True)
        self.signal = np.zeros((1920, 2))
        # Separate screen-space polylines (the X(t)/Y(t) sweep) drawn instead of signal.
        self.polylines = None
        self.colors = PALETTES['Green monochrome']
        self.glow = True
        for key,value in VISUAL_DEFAULTS.items(): setattr(self,key,value)
        self.trail = GlowTrail()
        self.trail_key = None
        self.trail_visible = set()
        self.extra_visible = set()  # Inspector overlay layers that hold vertices.
        self.history_key = None
        self.scanlines = True
        self.divisions = 8
        self.show_grid = True
        self.background_key = None
        # QSGGeometry keeps a pointer to this attribute set; retain its wrapper.
        self.attributes = QSGGeometry.defaultAttributes_Point2D()
        self.beam_attributes = QSGGeometry.defaultAttributes_ColoredPoint2D()
        self.beam_layer = None
        self.draw_trace = True  # False while the phosphor energy buffer shows the trace.
        self.root_node = None
        self.layers = []
        self.upload_ms = deque(maxlen=4096)
        self.background_builds = 0
        self.overlays = []
        self.pointer = None
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setAcceptHoverEvents(True)  # The handles show while the pointer moves over the scope (UI1).

    def hoverEnterEvent(self, event):
        if self.pointer: self.pointer('hover', event.position())

    def hoverMoveEvent(self, event):
        if self.pointer: self.pointer('hover', event.position())

    def mousePressEvent(self, event):
        if self.pointer:
            event.setAccepted(bool(self.pointer('press', event.position())))

    def mouseMoveEvent(self, event):
        if self.pointer: self.pointer('move', event.position())

    def mouseReleaseEvent(self, event):
        if self.pointer: self.pointer('release', event.position())

    def layer(self, root, index):
        node = QSGGeometryNode()
        geometry = QSGGeometry(self.attributes, 0)
        if geometry.sizeOfVertex() != 8:
            raise RuntimeError('Unexpected Qt Point2D buffer layout.')
        geometry.setDrawingMode(QSGGeometry.DrawingMode.DrawTriangles)
        material = QSGFlatColorMaterial()
        node.setGeometry(geometry)
        node.setMaterial(material)
        node.setFlag(QSGNode.Flag.OwnsGeometry)
        node.setFlag(QSGNode.Flag.OwnsMaterial)
        root.appendChildNode(node)
        self.layers[index] = (node, geometry, material)

    def create_beam_layer(self, root):
        node = QSGGeometryNode()
        geometry = QSGGeometry(self.beam_attributes, 0)
        if geometry.sizeOfVertex() != BEAM_VERTEX.itemsize:
            raise RuntimeError('Unexpected Qt ColoredPoint2D buffer layout.')
        geometry.setDrawingMode(QSGGeometry.DrawingMode.DrawTriangles)
        material = QSGVertexColorMaterial()
        node.setGeometry(geometry)
        node.setMaterial(material)
        node.setFlag(QSGNode.Flag.OwnsGeometry)
        node.setFlag(QSGNode.Flag.OwnsMaterial)
        root.appendChildNode(node)
        self.beam_layer = (node, geometry, material)

    def upload_beam(self, vertices):
        node, geometry, _ = self.beam_layer
        if geometry.vertexCount() != len(vertices):
            geometry.allocate(len(vertices))
        if len(vertices):
            ctypes.memmove(int(geometry.vertexData()), vertices.ctypes.data, vertices.nbytes)
        # Marking only geometry leaves a node that started empty undrawn.
        node.markDirty(QSGNode.DirtyStateBit.DirtyGeometry | QSGNode.DirtyStateBit.DirtyMaterial)

    def screen_points(self):
        w, h = self.width(), self.height()
        return self.signal * np.array([.45*min(w, h), -.45*min(w, h)]) + [w/2, h/2]

    def trace_polylines(self):
        return self.polylines if self.polylines is not None else [self.screen_points()]

    def strokes(self, polylines, width):
        parts = [stroke_triangles(points, width) for points in polylines]
        return np.concatenate(parts) if parts else np.empty((0, 2), dtype=np.float32)

    def upload(self, index, vertices, color):
        node, geometry, material = self.layers[index]
        if geometry.vertexCount() != len(vertices):
            geometry.allocate(len(vertices))
        if len(vertices):
            vertices = np.ascontiguousarray(vertices, dtype=np.float32)
            ctypes.memmove(int(geometry.vertexData()), vertices.ctypes.data, vertices.nbytes)
        material.setColor(color)
        node.markDirty(QSGNode.DirtyStateBit.DirtyGeometry | QSGNode.DirtyStateBit.DirtyMaterial)

    def updatePaintNode(self, old, data):
        started = time.perf_counter()
        if old is None:
            old = QSGNode()
            self.root_node = old
            self.layers = [None]*(10+GlowTrail.CAPACITY*2+OVERLAY_LAYERS-BASE_OVERLAYS)
            # Cached history sits behind the current trace and editing overlays.
            for index in (0,1,*range(10,10+GlowTrail.CAPACITY*2),*range(2,10),*range(10+GlowTrail.CAPACITY*2,len(self.layers))):
                self.layer(old,index)
                if index == 4: self.create_beam_layer(old)  # Above glow, below overlays.
            self.trail.reset(); self.trail_visible.clear(); self.trail_key = None
            self.background_key = None; self.extra_visible = set()
        w, h = self.width(), self.height()
        key = (w, h, self.colors['grid'], self.divisions, self.scanlines, self.show_grid)
        if key != self.background_key:
            strips = [stroke_triangles(line,1) for line in grid_lines(w,h,self.divisions)] if self.show_grid else []
            self.upload(0, np.concatenate(strips) if strips else np.empty((0, 2)), QColor(self.colors['grid']))
            scan = [stroke_triangles([(0, y), (w, y)], .5) for y in range(0, int(h), 4)] if self.scanlines else []
            color = QColor(self.colors['grid']); color.setAlphaF(.3)
            self.upload(1, np.concatenate(scan) if scan else np.empty((0, 2)), color)
            self.background_key = key
            self.background_builds += 1
        polylines = self.trace_polylines()
        s = view_scale(w, h)
        thickness, glow_size = self.trace_thickness*s, self.glow_size*s
        outer = thickness+2*glow_size
        inner = thickness+2*glow_size/3
        enabled = self.draw_trace and self.glow and self.glow_intensity > 0
        trail_key = (w,h,s,self.trace_thickness,self.glow_size,self.glow_decay,self.colors['trace'],self.history_key)
        if trail_key != self.trail_key:
            self.trail.reset(); self.trail_key = trail_key
        slot,weights = self.trail.update(getattr(self, 'frame_time', started),self.glow_decay,enabled)
        for history,weight in enumerate(weights):
            for band,width,opacity in ((0,outer,.045),(1,inner,.12)):
                index = 10+history*2+band
                if history == slot:
                    self.upload(index,self.strokes(polylines,width),QColor('transparent'))
                    self.trail_visible.add(index)
                if weight > 0:
                    color = QColor(self.colors['trace'])
                    color.setAlphaF(opacity*self.glow_intensity*weight*.5)
                    node,geometry,material = self.layers[index]
                    material.setColor(color); node.markDirty(QSGNode.DirtyStateBit.DirtyMaterial)
                elif index in self.trail_visible:
                    self.upload(index,np.empty((0,2)),QColor('transparent'))
                    self.trail_visible.discard(index)
        for index, width, opacity in ((2, outer, .045*self.glow_intensity), (3, inner, .12*self.glow_intensity), (4, thickness, .95)):
            color = QColor(self.colors['trace']); color.setAlphaF(opacity)
            # Overlapping glow strokes already build up where the beam is slow.
            visible = (index == 4 and self.draw_trace and self.beam_brightness <= 0) or (index != 4 and enabled)
            vertices = self.strokes(polylines, width) if visible else np.empty((0, 2))
            self.upload(index, vertices, color)
        trace = QColor(self.colors['trace'])
        beams = [beam_triangles(points, thickness, (trace.red(), trace.green(), trace.blue()), .95, self.beam_brightness)
                 for points in polylines] if self.draw_trace and self.beam_brightness > 0 else []
        self.upload_beam(np.concatenate(beams) if beams else np.empty(0, BEAM_VERTEX))
        for index in range(OVERLAY_LAYERS):
            vertices, color = self.overlays[index] if index < len(self.overlays) else (np.empty((0,2)), QColor('transparent'))
            if index < BASE_OVERLAYS: self.upload(5+index, vertices, color); continue
            slot = 10+GlowTrail.CAPACITY*2+index-BASE_OVERLAYS
            if len(vertices): self.extra_visible.add(slot)
            elif slot in self.extra_visible: self.extra_visible.discard(slot)
            else: continue  # Inspector layers stay empty without re-uploading every frame.
            self.upload(slot, vertices, color)
        self.upload_ms.append((time.perf_counter()-started)*1000)
        return old

