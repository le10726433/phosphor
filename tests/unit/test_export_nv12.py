"""CPU contracts for NV12 conversion and padding; no GPU or QApplication needed."""
import unittest

import numpy as np

from phosphor.display.export_nv12 import Nv12Canvas, rgba_to_nv12


class ExportNv12Tests(unittest.TestCase):
    @staticmethod
    def chroma(rgb):
        r, g, b = rgb
        return np.clip(np.floor([
            128 - .148223*r - .290993*g + .439216*b + .5,
            128 + .439216*r - .367788*g - .071427*b + .5,
        ]), 0, 255).astype(np.uint8)

    def test_solid_colours_and_alpha(self):
        for rgb, y, u, v in (((0, 0, 0), 16, 128, 128),
                             ((255, 255, 255), 235, 128, 128),
                             ((255, 0, 0), 81, 90, 240)):
            with self.subTest(rgb=rgb):
                image = np.empty((2, 2, 4), np.uint8)
                image[:] = (*rgb, 255)
                result = rgba_to_nv12(image)
                self.assertEqual(result.shape, (2*2*3//2,))
                self.assertEqual(result.dtype, np.dtype(np.uint8))
                np.testing.assert_array_equal(result, [y, y, y, y, u, v])
                image[..., 3] = [[0, 64], [128, 192]]
                np.testing.assert_array_equal(rgba_to_nv12(image), result)

    def test_chroma_uses_two_by_two_mean(self):
        image = np.array([[[0, 0, 0, 255], [255, 255, 255, 255]],
                          [[255, 255, 255, 255], [0, 0, 0, 255]]], np.uint8)
        np.testing.assert_array_equal(rgba_to_nv12(image)[4:], self.chroma((127.5,)*3))

    def test_row_major_luma_and_interleaved_chroma_layout(self):
        image = np.array([
            [[0, 12, 240, 255], [255, 10, 20, 0], [60, 90, 10, 128], [220, 80, 15, 255]],
            [[30, 200, 70, 255], [90, 40, 180, 255], [10, 230, 255, 255], [170, 20, 140, 255]],
            [[250, 100, 50, 255], [20, 50, 90, 255], [130, 240, 60, 255], [70, 10, 200, 255]],
            [[40, 160, 210, 255], [180, 220, 30, 255], [200, 30, 100, 255], [110, 190, 0, 255]],
        ], np.uint8)
        expected_y = []
        for row in image:
            for r, g, b, _ in row.astype(np.float64):
                expected_y.append(np.clip(np.floor(16 + .256788*r + .504129*g + .097906*b + .5), 0, 255))
        expected_uv = []
        for row in (0, 2):
            chroma_row = []
            for column in (0, 2):
                mean = image[row:row+2, column:column+2, :3].mean(axis=(0, 1))
                chroma_row.extend(self.chroma(mean))
            expected_uv.append(chroma_row)
        result = rgba_to_nv12(image)
        self.assertEqual(result.shape, (4*4*3//2,))
        self.assertEqual(result.dtype, np.dtype(np.uint8))
        np.testing.assert_array_equal(result[:16], expected_y)
        np.testing.assert_array_equal(result[16:].reshape(2, 4), expected_uv)

    def test_canvas_padding_and_repeated_placement(self):
        canvas = Nv12Canvas(8, 4, 4, '#102030')
        background = np.full((2, 2, 4), [16, 32, 48, 255], np.uint8)
        fill = rgba_to_nv12(background)
        expected_y = np.full((4, 8), fill[0], np.uint8)
        expected_uv = np.tile(fill[4:], (2, 4))
        np.testing.assert_array_equal(canvas.buffer[:32].reshape(4, 8), expected_y)
        np.testing.assert_array_equal(canvas.buffer[32:].reshape(2, 8), expected_uv)
        rng = np.random.default_rng(570)
        for _ in range(2):
            square = rgba_to_nv12(rng.integers(0, 256, (4, 4, 4), dtype=np.uint8))
            expected_y[:, 2:6] = square[:16].reshape(4, 4)
            expected_uv[:, 2:6] = square[16:].reshape(2, 4)
            frame = canvas.place(square)
            np.testing.assert_array_equal(frame[:32].reshape(4, 8), expected_y)
            np.testing.assert_array_equal(frame[32:].reshape(2, 8), expected_uv)

    def test_square_canvas_returns_input_itself(self):
        canvas = Nv12Canvas(4, 4, 4, '#102030')
        square = rgba_to_nv12(np.zeros((4, 4, 4), np.uint8))
        self.assertIs(canvas.place(square), square)
