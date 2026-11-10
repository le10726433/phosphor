"""Native Qt graphics selection shared by the editor and viewport benchmark."""
import os
import sys
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface


def graphics_api(platform=None):
    platform = sys.platform if platform is None else platform
    if platform == 'darwin':
        return QSGRendererInterface.GraphicsApi.Metal
    if platform == 'win32':
        return QSGRendererInterface.GraphicsApi.Direct3D11
    raise RuntimeError('The Qt editor currently supports macOS and Windows.')


def configure_graphics():
    # Python scene-graph callbacks must stay on the GUI thread on both platforms.
    os.environ.setdefault('QSG_RENDER_LOOP', 'basic')
    QQuickWindow.setGraphicsApi(graphics_api())


SYSTEM_AUDIO_AVAILABLE = sys.platform in ('win32','darwin')
SYSTEM_AUDIO_NOTICE = 'System-audio visualization requires macOS 14.2 or later, or Windows WASAPI.'
