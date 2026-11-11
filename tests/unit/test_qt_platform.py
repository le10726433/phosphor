import unittest
from PySide6.QtQuick import QSGRendererInterface
from phosphor.editor.qt_platform import graphics_api


class GraphicsPlatformTests(unittest.TestCase):
    def test_macos_uses_metal(self):
        self.assertEqual(graphics_api('darwin'), QSGRendererInterface.GraphicsApi.Metal)

    def test_windows_retains_direct3d(self):
        self.assertEqual(graphics_api('win32'), QSGRendererInterface.GraphicsApi.Direct3D11)

    def test_unsupported_platform_is_explicit(self):
        with self.assertRaises(RuntimeError):
            graphics_api('unsupported')
