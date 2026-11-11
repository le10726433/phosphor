"""High-range phosphor energy buffer built from stock Qt Quick shader items."""
import ctypes
from phosphor.storage.app_paths import ASSETS_DIR
import numpy as np
from PySide6.QtCore import QMetaObject, QSize, QUrl
from PySide6.QtGui import QVector2D, QVector3D
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickItem, QSGGeometry, QSGGeometryNode, QSGNode, QSGVertexColorMaterial
from phosphor.display.qt_geometry import BEAM_VERTEX
from phosphor.display.phosphor import MAX_PENDING_VERTICES

SHADERS = ASSETS_DIR/'shaders'
QML = b'''
import QtQuick
Item {
    id: root
    anchors.fill: parent
    property real fastKeep: 0
    property real slowKeep: 0
    property real exposure: 5
    property real slowGain: 0
    property vector3d fastColor: Qt.vector3d(1, 1, 1)
    property vector3d slowColor: Qt.vector3d(1, 1, 1)
    property bool gaussian: false
    property real sigma: 1
    property real depositGain: 1
    property vector2d texelX: Qt.vector2d(0, 0)
    property vector2d texelY: Qt.vector2d(0, 0)
    property bool bloomOn: false
    property real bloomGain: 0
    property real bloomSigma: 1
    property real bloomFactor: 1
    property size bloomSize: Qt.size(1, 1)
    property vector2d energyTexel: Qt.vector2d(0, 0)
    property vector2d bloomTexelX: Qt.vector2d(0, 0)
    property vector2d bloomTexelY: Qt.vector2d(0, 0)
    // Advance once per step: a live feedback layer renders at the display rate instead.
    function advance() {
        deposited.scheduleUpdate()
        if (gaussian) { blurredX.scheduleUpdate(); blurredY.scheduleUpdate() }
        energyCopy.scheduleUpdate(); energy.scheduleUpdate()
        if (bloomOn) { bloomDowned.scheduleUpdate(); bloomedX.scheduleUpdate(); bloomedY.scheduleUpdate() }
    }
    function releaseSources() {
        deposited.sourceItem = null; blurredX.sourceItem = null
        blurredY.sourceItem = null; energy.sourceItem = null; energyCopy.sourceItem = null
        bloomDowned.sourceItem = null; bloomedX.sourceItem = null; bloomedY.sourceItem = null
    }
    // Sources stay visible but off-screen: hidden ones do not update their textures.
    // Both layers stay 8-bit: floating-point layers render empty in a multisampled
    // window. The accumulate and display shaders pack energy as 16-bit fixed point.
    ShaderEffectSource {
        id: deposited; x: -40000; width: root.width; height: root.height; sourceItem: depositItem
        hideSource: true; live: false; smooth: false
    }
    ShaderEffect {
        id: blurX; width: root.width; height: root.height; blending: false
        property var source: deposited
        property vector2d texel: root.texelX
        property real sigma: root.sigma
        property real packedInput: 0
        fragmentShader: blurShader
    }
    ShaderEffectSource {
        id: blurredX; x: -40000; width: root.width; height: root.height; sourceItem: blurX
        hideSource: true; live: false; smooth: false
    }
    ShaderEffect {
        id: blurY; width: root.width; height: root.height; blending: false
        property var source: blurredX
        property vector2d texel: root.texelY
        property real sigma: root.sigma
        property real packedInput: 1
        fragmentShader: blurShader
    }
    ShaderEffectSource {
        id: blurredY; x: -40000; width: root.width; height: root.height; sourceItem: blurY
        hideSource: true; live: false; smooth: false
    }
    ShaderEffect {
        id: combine; width: root.width; height: root.height; blending: false
        property var previous: energyCopy
        property var deposit: root.gaussian ? blurredY : deposited
        property real depositGain: root.depositGain
        property real packedDeposit: root.gaussian ? 1 : 0
        property real fastKeep: root.fastKeep
        property real slowKeep: root.slowKeep
        fragmentShader: accumulateShader
    }
    // Feedback runs through a copy, not a recursive layer. Qt 6.10.3 frees a recursive
    // layer's old texture on resize while Direct3D 11's recorded frame still binds it.
    ShaderEffectSource {
        id: energy; x: -40000; width: root.width; height: root.height; sourceItem: combine
        hideSource: true; live: false; smooth: false
    }
    ShaderEffect {
        id: energyPass; width: root.width; height: root.height; blending: false
        property var source: energy
    }
    ShaderEffectSource {
        id: energyCopy; x: -40000; width: root.width; height: root.height; sourceItem: energyPass
        hideSource: true; live: false; smooth: false
    }
    // Glow as bloom: the energy is box-downsampled, blurred at low resolution in both
    // directions, and added back before tone mapping, so it follows corners smoothly.
    ShaderEffect {
        id: bloomDown; width: root.width; height: root.height; blending: false
        property var energy: energy
        property vector2d energyTexel: root.energyTexel
        property real factor: root.bloomFactor
        fragmentShader: bloomDownShader
    }
    ShaderEffectSource {
        id: bloomDowned; x: -40000; width: root.width; height: root.height; sourceItem: bloomDown
        textureSize: root.bloomSize; hideSource: true; live: false; smooth: false
    }
    ShaderEffect {
        id: bloomX; width: root.width; height: root.height; blending: false
        property var source: bloomDowned
        property vector2d texel: root.bloomTexelX
        property real sigma: root.bloomSigma
        fragmentShader: bloomBlurShader
    }
    ShaderEffectSource {
        id: bloomedX; x: -40000; width: root.width; height: root.height; sourceItem: bloomX
        textureSize: root.bloomSize; hideSource: true; live: false; smooth: false
    }
    ShaderEffect {
        id: bloomY; width: root.width; height: root.height; blending: false
        property var source: bloomedX
        property vector2d texel: root.bloomTexelY
        property real sigma: root.bloomSigma
        fragmentShader: bloomBlurShader
    }
    ShaderEffectSource {
        id: bloomedY; x: -40000; width: root.width; height: root.height; sourceItem: bloomY
        textureSize: root.bloomSize; hideSource: true; live: false; smooth: true
    }
    ShaderEffect {
        anchors.fill: parent
        property var energy: energy
        property var bloom: bloomedY
        property real bloomGain: root.bloomOn ? root.bloomGain : 0
        property real exposure: root.exposure
        property real slowGain: root.slowGain
        property vector3d fastColor: root.fastColor
        property vector3d slowColor: root.slowColor
        fragmentShader: displayShader
    }
}
'''


class DepositItem(QQuickItem):
    """Draws each batch of new energy for exactly one rendered frame."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFlag(QQuickItem.Flag.ItemHasContents, True)
        # QSGGeometry keeps a pointer to this attribute set; retain its wrapper.
        self.attributes = QSGGeometry.defaultAttributes_ColoredPoint2D()
        self.pending = []
        self.pending_vertices = 0
        self.nodes = None

    def add(self, vertices):
        if len(vertices) and self.pending_vertices+len(vertices) <= MAX_PENDING_VERTICES:
            self.pending.append(vertices)
            self.pending_vertices += len(vertices)

    def updatePaintNode(self, old, data):
        if self.nodes is None:
            root = QSGNode()
            node = QSGGeometryNode()
            geometry = QSGGeometry(self.attributes, 0)
            if geometry.sizeOfVertex() != BEAM_VERTEX.itemsize:
                raise RuntimeError('Unexpected Qt ColoredPoint2D buffer layout.')
            geometry.setDrawingMode(QSGGeometry.DrawingMode.DrawTriangles)
            material = QSGVertexColorMaterial()
            node.setGeometry(geometry)
            node.setMaterial(material)
            node.setFlag(QSGNode.Flag.OwnsGeometry)
            node.setFlag(QSGNode.Flag.OwnsMaterial)
            root.appendChildNode(node)
            self.nodes = (root, node, geometry, material)
        root, node, geometry, _ = self.nodes
        vertices = np.concatenate(self.pending) if self.pending else np.empty(0, BEAM_VERTEX)
        self.pending, self.pending_vertices = [], 0
        if geometry.vertexCount() != len(vertices):
            geometry.allocate(len(vertices))
        if len(vertices):
            ctypes.memmove(int(geometry.vertexData()), vertices.ctypes.data, vertices.nbytes)
        # Marking only geometry leaves a node that started empty undrawn.
        node.markDirty(QSGNode.DirtyStateBit.DirtyGeometry | QSGNode.DirtyStateBit.DirtyMaterial)
        return root


class PhosphorLayer:
    """Raises RuntimeError if the shaders cannot load, so callers keep the line trace."""

    def __init__(self, view):
        for name in ('phosphor_accumulate.frag.qsb', 'phosphor_blur.frag.qsb', 'phosphor_display.frag.qsb',
                     'phosphor_bloom_down.frag.qsb', 'phosphor_bloom_blur.frag.qsb'):
            if not (SHADERS/name).is_file():
                raise RuntimeError(f'Missing shader: {name}')
        self.engine = QQmlEngine(view)
        self.deposit = DepositItem(view.contentItem())
        context = self.engine.rootContext()
        context.setContextProperty('depositItem', self.deposit)
        context.setContextProperty('accumulateShader', QUrl.fromLocalFile(str(SHADERS/'phosphor_accumulate.frag.qsb')))
        context.setContextProperty('blurShader', QUrl.fromLocalFile(str(SHADERS/'phosphor_blur.frag.qsb')))
        context.setContextProperty('displayShader', QUrl.fromLocalFile(str(SHADERS/'phosphor_display.frag.qsb')))
        context.setContextProperty('bloomDownShader', QUrl.fromLocalFile(str(SHADERS/'phosphor_bloom_down.frag.qsb')))
        context.setContextProperty('bloomBlurShader', QUrl.fromLocalFile(str(SHADERS/'phosphor_bloom_blur.frag.qsb')))
        self.component = QQmlComponent(self.engine)
        self.component.setData(QML, QUrl.fromLocalFile(str(SHADERS/'phosphor.qml')))
        self.root = self.component.create()
        if self.root is None:
            raise RuntimeError('; '.join(error.toString() for error in self.component.errors()))
        self.root.setParentItem(view.contentItem())
        self.set_active(False)

    def resize(self, width, height):
        self.deposit.setWidth(width)
        self.deposit.setHeight(height)

    def set_active(self, active):
        if self.root.isVisible() != active:
            self.root.setVisible(active)

    def step(self, vertices, uniforms):
        self.deposit.add(vertices)
        for name, value in uniforms.items():
            if name == 'bloomSize':
                self.root.setProperty(name, QSize(*value))
                continue
            vector = {2: QVector2D, 3: QVector3D}[len(value)] if isinstance(value, tuple) else None
            self.root.setProperty(name, vector(*value) if vector else value)
        self.deposit.update()
        QMetaObject.invokeMethod(self.root, 'advance')

    def close(self):
        """Drop the effect sources' window references; ~QQuickWindow cannot order them safely."""
        QMetaObject.invokeMethod(self.root, 'releaseSources')
        self.root.setVisible(False)
        self.root.setParentItem(None)
