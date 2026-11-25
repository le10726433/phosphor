"""NV12 export frames: a GPU pass that packs the rendered picture before readback.

The shader (assets/shaders/export_nv12.frag) writes BT.601 limited-range NV12, the
same conversion ffmpeg applied to the old RGBA pipe, at 1.5 bytes per pixel instead of
4. rgba_to_nv12 is its NumPy reference; Nv12Canvas centres the side x side square in the
output frame on the encoder thread (the padding never changes, so it is written once).
"""
import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import (QColor, QShader, QRhiTexture, QRhiColorAttachment, QRhiTextureRenderTargetDescription,
    QRhiSampler, QRhiShaderResourceBinding, QRhiShaderStage, QRhiVertexInputLayout, QRhiViewport,
    QRhiDepthStencilClearValue, QRhiReadbackResult, QRhiReadbackDescription)
from phosphor.storage.app_paths import ASSETS_DIR

SHADERS = ASSETS_DIR / 'shaders'


def rgba_to_nv12(rgba):
    """(h, w, 4) uint8 -> 1-D NV12 bytes; must match export_nv12.frag."""
    r, g, b = (rgba[..., i].astype(np.float64) for i in range(3))
    h, w = rgba.shape[:2]
    y = np.floor(16 + 0.256788 * r + 0.504129 * g + 0.097906 * b + .5)
    blocks = [c.reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3)) for c in (r, g, b)]
    cb = np.floor(128 - 0.148223 * blocks[0] - 0.290993 * blocks[1] + 0.439216 * blocks[2] + .5)
    cr = np.floor(128 + 0.439216 * blocks[0] - 0.367788 * blocks[1] - 0.071427 * blocks[2] + .5)
    chroma = np.stack((cb, cr), axis=-1).reshape(h // 2, w)
    return np.clip(np.concatenate([y.ravel(), chroma.ravel()]), 0, 255).astype(np.uint8)


def nv12_possible(rhi, width, height):
    """The pass needs a top-down framebuffer (D3D11/Metal) and 2x2-aligned placement."""
    side = min(width, height)
    return (not rhi.isYUpInFramebuffer() and side % 4 == 0 and width % 2 == 0 and height % 2 == 0
            and (width - side) // 2 % 2 == 0 and (height - side) // 2 % 2 == 0)


class Nv12Canvas:
    """Full output NV12 frame with the background padding; place() copies one square in."""
    def __init__(self, width, height, side, background):
        self.width, self.height, self.side = width, height, side
        self.x, self.y = (width - side) // 2, (height - side) // 2
        colour = QColor(background)
        fill = rgba_to_nv12(np.array([[[colour.red(), colour.green(), colour.blue(), 255]] * 2] * 2, np.uint8))
        self.buffer = np.empty(width * height * 3 // 2, np.uint8)
        self.luma = self.buffer[:width * height].reshape(height, width)
        self.chroma = self.buffer[width * height:].reshape(height // 2, width)
        self.luma[:] = fill[0]
        self.chroma.reshape(height // 2, width // 2, 2)[:] = fill[4:6]

    def place(self, square):
        """square: NV12 bytes of the side x side picture (any buffer). Returns the frame."""
        side = self.side
        if self.x == 0 and self.y == 0: return square
        data = np.frombuffer(square, np.uint8, side * side * 3 // 2)
        self.luma[self.y:self.y+side, self.x:self.x+side] = data[:side*side].reshape(side, side)
        self.chroma[self.y//2:(self.y+side)//2, self.x:self.x+side] = data[side*side:].reshape(side // 2, side)
        return self.buffer


def _shader(name):
    return QShader.fromSerialized((SHADERS / name).read_bytes())


class Nv12Pass:
    """Records the RGBA -> NV12 pass and its readback on a QQuickRenderControl frame."""
    def __init__(self, rhi, source, side):
        if side % 4: raise ValueError('The NV12 pass needs a side divisible by 4.')
        self.rhi, self.side = rhi, side
        self.size = QSize(side // 4, side * 3 // 2)
        self.resources = []
        self.texture = self.keep(rhi.newTexture(QRhiTexture.Format.RGBA8, self.size, 1,
            QRhiTexture.Flag.RenderTarget | QRhiTexture.Flag.UsedAsTransferSource))
        if not self.texture.create(): raise RuntimeError('Could not create the NV12 export texture.')
        self.target =self.keep(rhi.newTextureRenderTarget(QRhiTextureRenderTargetDescription(QRhiColorAttachment(self.texture))))
        self.pass_descriptor = self.keep(self.target.newCompatibleRenderPassDescriptor())
        self.target.setRenderPassDescriptor(self.pass_descriptor)
        if not self.target.create(): raise RuntimeError('Could not create the NV12 export target.')
        Filter, Address = QRhiSampler.Filter, QRhiSampler.AddressMode
        self.sampler = self.keep(rhi.newSampler(Filter.Nearest, Filter.Nearest, Filter.None_,
                                                Address.ClampToEdge, Address.ClampToEdge))
        if not self.sampler.create(): raise RuntimeError('Could not create the NV12 export sampler.')
        self.bindings = self.keep(rhi.newShaderResourceBindings())
        self.bindings.setBindings([QRhiShaderResourceBinding.sampledTexture(
            1, QRhiShaderResourceBinding.StageFlag.FragmentStage, source, self.sampler)])
        self.bindings.create()
        self.pipeline = self.keep(rhi.newGraphicsPipeline())
        self.pipeline.setShaderStages([QRhiShaderStage(QRhiShaderStage.Type.Vertex, _shader('export_nv12.vert.qsb')),
                                       QRhiShaderStage(QRhiShaderStage.Type.Fragment, _shader('export_nv12.frag.qsb'))])
        self.pipeline.setVertexInputLayout(QRhiVertexInputLayout())
        self.pipeline.setShaderResourceBindings(self.bindings)
        self.pipeline.setRenderPassDescriptor(self.pass_descriptor)
        if not self.pipeline.create(): raise RuntimeError('Could not create the NV12 export pipeline.')

    def keep(self, resource):
        self.resources.append(resource)
        return resource

    def record(self, cb):
        cb.beginPass(self.target, QColor(0, 0, 0, 0), QRhiDepthStencilClearValue(1., 0))
        cb.setGraphicsPipeline(self.pipeline)
        cb.setViewport(QRhiViewport(0, 0, self.size.width(), self.size.height()))
        cb.setShaderResources()
        cb.draw(3)
        cb.endPass()

    def read(self, cb):
        """Queue a readback of the packed texture; data is ready after endFrame."""
        result = QRhiReadbackResult(); batch = self.rhi.nextResourceUpdateBatch()
        batch.readBackTexture(QRhiReadbackDescription(self.texture), result)
        cb.resourceUpdate(batch)
        return result

    def release(self):
        """Free GPU resources; the caller keeps the Python wrappers alive (see VideoRenderer)."""
        for resource in reversed(self.resources): resource.destroy()
        return self.resources
