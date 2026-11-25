"""STL bytes and glTF accessors to NumPy (0.58.0-dev)."""
import struct
import unittest
import numpy as np
from phosphor.engine.mesh_readers import parse_stl, read_accessor

TRIANGLES = (((0, 0, 0), (1, 0, 0), (1, 1, 0)), ((0, 0, 0), (1, 1, 0), (0, 1, 2.5)))


def ascii_stl(triangles=TRIANGLES, solids=1):
    rows = []
    for solid in range(solids):
        rows.append(f'solid part{solid}')
        for triangle in triangles:
            rows += ['  FACET NORMAL 0 0 1', '    outer loop']
            rows += [f'      vertex {x} {y} {z}' for x, y, z in triangle]
            rows += ['    endloop', '  endfacet']
        rows.append(f'endsolid part{solid}')
    return '\n'.join(rows).encode('ascii')


def binary_stl(triangles=TRIANGLES, header=b'solid binary file that looks like text', extra=b''):
    body = b''.join(struct.pack('<12fH', 0, 0, 1, *(c for v in t for c in v), 0) for t in triangles)
    return header.ljust(80, b' ')[:80]+struct.pack('<I', len(triangles))+body+extra


class StlTests(unittest.TestCase):
    def expected(self, triangles=TRIANGLES):
        return np.array([v for t in triangles for v in t], float)

    def check(self, data, triangles=TRIANGLES):
        vertices, faces = parse_stl(data, 100)
        self.assertEqual((vertices.dtype, faces.dtype), (np.float64, np.int64))
        np.testing.assert_array_equal(vertices, self.expected(triangles))
        np.testing.assert_array_equal(faces, np.arange(3*len(triangles)).reshape(-1, 3))

    def test_ascii(self):
        self.check(ascii_stl())
        self.check(b'\n  '+ascii_stl(solids=2), TRIANGLES*2)

    def test_binary_even_when_it_begins_with_solid(self):
        self.check(binary_stl())
        self.check(binary_stl(header=b'\x00'*80))
        self.check(binary_stl(extra=b'\x00'*7))  # Trailing bytes are ignored.

    def test_long_ascii_is_not_mistaken_for_binary(self):
        many = TRIANGLES*40
        data = ascii_stl(many)
        self.assertLess(84+50*struct.unpack('<I', data[80:84])[0], 10**12)
        self.check(data, many)

    def test_errors(self):
        nan = ((float('nan'), 0, 0), (1, 0, 0), (1, 1, 0)),
        bad = ascii_stl().replace(b'vertex 1 0 0', b'vertex 1 x 0', 1)
        four = ascii_stl().replace(b'endloop', b'vertex 0 0 0\n endloop', 1)
        for data, text in ((b'', 'truncated'), (b'hello', 'truncated'), (binary_stl()[:-10], 'truncated'),
                           (binary_stl(()), 'no faces'), (b'solid x\nfacet\nendsolid x\n', 'no faces'),
                           (binary_stl(nan), 'non-finite'), (bad, 'Invalid STL vertex on line 5'),
                           (four, 'does not have 3 vertices')):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, text):
                parse_stl(data, 100)

    def test_face_limit(self):
        for data in (ascii_stl(), binary_stl()):
            with self.assertRaisesRegex(ValueError, 'more than 1 faces'):
                parse_stl(data, 1)


def gltf_with(views, accessors, buffer):
    return dict(accessors=accessors, bufferViews=views, buffers=[dict(byteLength=len(buffer))]), [buffer]


class AccessorTests(unittest.TestCase):
    def test_float_vec3_and_scalar_indices(self):
        positions = np.arange(12, dtype='<f4').reshape(4, 3)
        indices = np.array([0, 1, 2, 2, 3, 0], '<u2')
        buffer = positions.tobytes()+indices.tobytes()
        gltf, buffers = gltf_with([dict(buffer=0, byteLength=48), dict(buffer=0, byteOffset=48, byteLength=12)],
                                  [dict(bufferView=0, componentType=5126, count=4, type='VEC3'),
                                   dict(bufferView=1, componentType=5123, count=6, type='SCALAR')], buffer)
        out = read_accessor(gltf, 0, buffers)
        self.assertEqual((out.shape, out.dtype), ((4, 3), np.float64))
        np.testing.assert_array_equal(out, positions)
        out = read_accessor(gltf, 1, buffers)
        self.assertEqual((out.shape, out.dtype), ((6,), np.int64))
        np.testing.assert_array_equal(out, indices)

    def test_byte_stride_and_offsets(self):
        # Interleaved: position (3 floats) + 4 padding bytes per vertex, view and accessor offsets.
        rows = [struct.pack('<3f', i, 2*i, -i)+b'\xff'*4 for i in range(3)]
        buffer = b'\x00'*8+b''.join(rows)
        gltf, buffers = gltf_with([dict(buffer=0, byteOffset=4, byteLength=4+16*3, byteStride=16)],
                                  [dict(bufferView=0, byteOffset=4, componentType=5126, count=3, type='VEC3')], buffer)
        np.testing.assert_array_equal(read_accessor(gltf, 0, buffers), [[i, 2*i, -i] for i in range(3)])

    def test_normalized_integers(self):
        cases = ((5120, 'i1', [-128, -127, 0, 127], [-1, -1, 0, 1]), (5121, 'u1', [0, 51, 255, 0], [0, .2, 1, 0]),
                 (5122, '<i2', [-32768, 0, 32767, 0], [-1, 0, 1, 0]), (5123, '<u2', [0, 65535, 0, 0], [0, 1, 0, 0]))
        for component, dtype, raw, expected in cases:
            buffer = np.array(raw, dtype).tobytes()
            gltf, buffers = gltf_with([dict(buffer=0, byteLength=len(buffer))],
                                      [dict(bufferView=0, componentType=component, count=1, type='VEC4', normalized=True)], buffer)
            with self.subTest(component=component):
                out = read_accessor(gltf, 0, buffers)
                self.assertEqual(out.dtype, np.float64)
                np.testing.assert_allclose(out, [expected])

    def test_sparse_with_and_without_buffer_view(self):
        base = np.zeros((4, 3), '<f4').tobytes()
        indices = np.array([1, 3], '<u2').tobytes()
        values = np.array([[1, 2, 3], [4, 5, 6]], '<f4').tobytes()
        buffer = base+indices+values
        views = [dict(buffer=0, byteLength=48), dict(buffer=0, byteOffset=48, byteLength=4), dict(buffer=0, byteOffset=52, byteLength=24)]
        sparse = dict(count=2, indices=dict(bufferView=1, componentType=5123), values=dict(bufferView=2))
        expected = [[0, 0, 0], [1, 2, 3], [0, 0, 0], [4, 5, 6]]
        for view in (dict(bufferView=0), {}):
            gltf, buffers = gltf_with(views, [dict(componentType=5126, count=4, type='VEC3', sparse=sparse, **view)], buffer)
            out = read_accessor(gltf, 0, buffers)
            self.assertEqual(out.dtype, np.float64)
            np.testing.assert_array_equal(out, expected)

    def test_invalid_accessors(self):
        buffer = bytes(48)
        good = dict(bufferView=0, componentType=5126, count=4, type='VEC3')
        bad = (dict(good, count=5), dict(good, byteOffset=4), dict(good, type='MAT3'), dict(good, componentType=5124),
               dict(good, normalized=True), dict(good, bufferView=3), {'type': 'VEC3'},
               dict(good, sparse=dict(count=1, indices=dict(bufferView=5, componentType=5125), values=dict(bufferView=0))))
        for accessor in bad:
            gltf, buffers = gltf_with([dict(buffer=0, byteLength=48)], [accessor], buffer)
            with self.subTest(accessor=accessor), self.assertRaisesRegex(ValueError, '^glTF '):
                read_accessor(gltf, 0, buffers)
        gltf, buffers = gltf_with([dict(buffer=0, byteLength=64)], [good], buffer)  # View past the buffer.
        for index in (0, 1, -1):
            with self.assertRaisesRegex(ValueError, '^glTF '):
                read_accessor(gltf, index, buffers)


if __name__ == '__main__':
    unittest.main()
