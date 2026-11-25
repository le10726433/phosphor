"""Generated STL/glTF/GLB imports and bit-identical OBJ refactor oracles."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

from phosphor.engine import model_object
from phosphor.engine.mesh_formats import load_model, parse_gltf
from phosphor.engine.model_object import model_from_obj
from tests.fixtures.dense_mesh import dense_mesh_obj
from tests.unit.mesh_files import gltf_mesh, stl_bytes, write_gltf
from tests.unit.test_model_object import CUBE, TRIANGLES, CORNERS, QUADS, two_cubes


class MeshFormatTests(unittest.TestCase):
    def test_obj_oracles(self):
        cases = [(CUBE, 'efb8ecc99e9d0570'), (TRIANGLES, '33d7f919f7121531'),
                 (two_cubes(), '3b6b82b6c710a349'),
                 ('v 0 0 0\nv 1 0 0\nv 1 1 0\nf 1 2 3\n', '9dd098c4b11be1ff'),
                 ('v 0 0 0\nv 1 0 0\nl 1 2\n', 'f1a892f4dee23833'),
                 (dense_mesh_obj(), '2ca7926b9ffcc3c8'),
                 (dense_mesh_obj(rings=24, segments=30), '32c238360c19c772')]
        for text, expected in cases:
            with self.subTest(expected=expected):
                model, r = model_from_obj(text, 'Oracle')
                value = json.dumps([model.document(), [r.source_edges, r.kept_edges, r.limited, r.smooth]])
                self.assertEqual(hashlib.sha256(value.encode()).hexdigest()[:16], expected)

    def assert_cube(self, path, text=TRIANGLES):
        model, _ = load_model(path)
        self.assertEqual(replace(model, name='Model'), model_from_obj(text)[0])

    def test_stl_cube_and_limits(self):
        with tempfile.TemporaryDirectory() as folder:
            for binary in (False, True):
                path = Path(folder)/'cube.stl'
                path.write_bytes(stl_bytes(binary))
                self.assert_cube(path)
                with patch.object(model_object, 'MAX_MODEL_FACES', 3):
                    with self.assertRaisesRegex(ValueError, 'STL'):
                        load_model(path)
                with patch.object(model_object, 'MAX_MODEL_BYTES', 10):
                    with self.assertRaisesRegex(ValueError, '^STL files are limited'):
                        load_model(path)

    def test_cube_containers(self):
        # CORNERS/QUADS define the same cube used by the generated triangle builders.
        self.assertEqual(CUBE, CORNERS+''.join('f '+' '.join(map(str, q))+'\n' for q in QUADS))
        with tempfile.TemporaryDirectory() as folder:
            for kind in ('external', 'data', 'glb'):
                for component in (5123, 5125):
                    with self.subTest(kind=kind, component=component):
                        doc, data = gltf_mesh(component_type=component)
                        self.assert_cube(write_gltf(folder, kind, doc, data))

    def test_node_transforms(self):
        with tempfile.TemporaryDirectory() as folder:
            for moved in (dict(translation=[5, 0, 0]),
                          dict(matrix=[1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 5, 0, 0, 1])):
                doc, data = gltf_mesh()
                doc['nodes'] = [dict(mesh=0), dict(mesh=0, **moved)]
                doc['scenes'][0]['nodes'] = [0, 1]
                self.assert_cube(write_gltf(folder, 'data', doc, data), two_cubes())
            doc, data = gltf_mesh()
            # Cancel the parent's size change on each cube so the OBJ oracle has
            # identical cube dimensions; the child translation still becomes 5.
            doc['nodes'] = [dict(scale=[2, 2, 2], children=[1, 2]), dict(mesh=0, scale=[.5]*3),
                            dict(mesh=0, translation=[2.5, 0, 0], scale=[.5]*3)]
            self.assert_cube(write_gltf(folder, 'data', doc, data), two_cubes())

    def test_primitive_modes(self):
        positions = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0)]
        with tempfile.TemporaryDirectory() as folder:
            corners = ''.join(f'v {x} {y} {z}\n' for x, y, z in positions)
            # A lone flat quad has too few creases, so every mesh edge (diagonal too) is kept:
            # the strip and the fan must give exactly the OBJ model of the same two triangles.
            for mode, indices, edges, obj in [(5, [0, 1, 2, 3], 5, 'f 1 2 3\nf 3 2 4\n'), (6, [0, 1, 3, 2], 5, 'f 1 2 4\nf 1 4 3\n'),
                                              (1, [0, 1, 2, 3], 2, 'l 1 2\nl 3 4\n'), (3, [0, 1, 3], 2, 'l 1 2 4\n'),
                                              (2, [0, 1, 3, 2], 4, 'l 1 2 4 3 1\n')]:
                with self.subTest(mode=mode):
                    doc, data = gltf_mesh(positions, indices, mode)
                    model, _ = load_model(write_gltf(folder, 'data', doc, data))
                    self.assertEqual(len(model.edges), edges)
                    self.assertEqual(replace(model, name='Model'), model_from_obj(corners+obj)[0])
            doc, data = gltf_mesh(positions, [0, 1], 0)
            with self.assertRaisesRegex(ValueError, 'no faces or lines'):
                load_model(write_gltf(folder, 'data', doc, data))

    def test_rejected_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            for key, extension in [('extensionsRequired', 'KHR_draco_mesh_compression'),
                                   ('extensionsUsed', 'EXT_meshopt_compression')]:
                doc, data = gltf_mesh()
                doc[key] = [extension]
                with self.assertRaisesRegex(ValueError, 'without compression'):
                    load_model(write_gltf(folder, 'data', doc, data))
            for uri in ('../escape.bin', 'sub/../../x.bin', str(Path(folder).resolve()/'x.bin'),
                        'http://example.com/a.bin', 'C:\\x.bin', '%2e%2e/escape.bin'):
                doc, _ = gltf_mesh()
                doc['buffers'][0]['uri'] = uri
                with self.subTest(uri=uri), self.assertRaises(ValueError):
                    parse_gltf(json.dumps(doc).encode(), Path(folder), 'glTF')
            doc, data = gltf_mesh()
            doc['asset']['version'] = '1.0'
            with self.assertRaisesRegex(ValueError, 'Only glTF 2.0'):
                load_model(write_gltf(folder, 'data', doc, data))
            with self.assertRaisesRegex(ValueError, 'Unsupported 3D model file type'):
                load_model(Path(folder)/'cube.fbx')

    def test_total_size_and_face_limits(self):
        with tempfile.TemporaryDirectory() as folder:
            path = write_gltf(folder)
            with patch.object(model_object, 'MAX_MODEL_BYTES', path.stat().st_size):
                with self.assertRaisesRegex(ValueError, '^glTF files are limited'):
                    load_model(path)
            with patch.object(model_object, 'MAX_MODEL_FACES', 3):
                with self.assertRaisesRegex(ValueError, 'more than 3 faces'):
                    load_model(path)

    def test_hierarchy_cycles_and_scene_fallbacks(self):
        with tempfile.TemporaryDirectory() as folder:
            for no_nodes in (False, True):
                doc, data = gltf_mesh()
                del doc['scenes']
                if no_nodes:
                    del doc['nodes']
                self.assert_cube(write_gltf(folder, 'data', doc, data))
            for with_scene in (False, True):
                doc, data = gltf_mesh()
                doc['nodes'][0]['children'] = [0]
                if not with_scene:
                    del doc['scenes']
                with self.assertRaisesRegex(ValueError, 'hierarchy has a cycle'):
                    load_model(write_gltf(folder, 'data', doc, data))
            # Shared children could multiply placements exponentially; glTF forbids them.
            doc, data = gltf_mesh()
            doc['nodes'] = [dict(children=[1, 1]), dict(mesh=0)]
            with self.assertRaisesRegex(ValueError, 'not a tree'):
                load_model(write_gltf(folder, 'data', doc, data))

    def test_quaternion_rotation(self):
        # 90° about z: rotated corner (1, 2, 3) of a box lands at (-2, 1, 3).
        box = [(x, 2*y, 3*z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
        turned = [(-y, x, z) for x, y, z in box]
        faces = ''.join(f'f {q[0]} {q[1]} {q[2]}\nf {q[0]} {q[2]} {q[3]}\n' for q in QUADS)
        expected = model_from_obj(''.join(f'v {x} {y} {z}\n' for x, y, z in turned)+faces)[0]
        doc, data = gltf_mesh(box, [q[i]-1 for q in QUADS for i in (0, 1, 2, 0, 2, 3)])
        doc['nodes'][0]['rotation'] = [0, 0, 2**-.5, 2**-.5]
        with tempfile.TemporaryDirectory() as folder:
            model = load_model(write_gltf(folder, 'data', doc, data))[0]
        self.assertEqual(model.edges, expected.edges)
        np.testing.assert_allclose(model.vertices, expected.vertices, atol=1e-12)

    def test_damaged_container_and_malformed_json(self):
        import struct
        with tempfile.TemporaryDirectory() as folder:
            for data in (b'glTF', struct.pack('<4sII', b'glTF', 2, 100),
                         struct.pack('<4sIIII', b'glTF', 2, 20, 4, 0x4E4F534A)):
                with self.subTest(data=data), self.assertRaisesRegex(ValueError, 'damaged'):
                    parse_gltf(data, Path(folder), 'GLB')
            for data in (b'[]', b'{', b'\xff'):
                with self.assertRaisesRegex(ValueError, 'not valid glTF JSON'):
                    parse_gltf(data, Path(folder), 'glTF')
            doc, data = gltf_mesh()
            doc['nodes'][0]['mesh'] = 99
            with self.assertRaisesRegex(ValueError, 'not valid glTF'):
                load_model(write_gltf(folder, 'data', doc, data))

    def test_invalid_vertices_indices_and_nested_compression(self):
        with tempfile.TemporaryDirectory() as folder:
            doc, data = gltf_mesh([(0, 0, 0), (1, 0, 0)], [0, 2], mode=1)
            with self.assertRaisesRegex(ValueError, 'missing vertex'):
                load_model(write_gltf(folder, 'data', doc, data))
            doc, data = gltf_mesh()
            doc['nodes'][0]['translation'] = [float('inf'), 0, 0]
            with self.assertRaisesRegex(ValueError, 'non-finite'):
                load_model(write_gltf(folder, 'data', doc, data))
            for on_view in (False, True):
                doc, data = gltf_mesh()
                target = doc['bufferViews'][0] if on_view else doc['meshes'][0]['primitives'][0]
                target['extensions'] = {'EXT_meshopt_compression': {}}
                with self.assertRaisesRegex(ValueError, 'without compression'):
                    load_model(write_gltf(folder, 'data', doc, data))


class MeshSimplifyFormatTests(unittest.TestCase):
    def test_none_and_source_roundtrip(self):
        from phosphor.engine.mesh_formats import read_mesh, model_from_source
        from tests.unit.mesh_files import write_cube_files
        with tempfile.TemporaryDirectory() as folder:
            for path in write_cube_files(folder):
                with self.subTest(path=path.name):
                    expected = load_model(path)
                    self.assertEqual(load_model(path, simplify_to=None), expected)
                    self.assertEqual(model_from_source(read_mesh(path)), expected)

    def test_dense_simplification_report(self):
        text = dense_mesh_obj()
        model, report = model_from_obj(text, 'D', simplify_to=500)
        vertices, faces, _ = model_object.parse_obj(text)
        self.assertTrue(report.simplified)
        self.assertIn(report.result_triangles, (499, 500))
        self.assertEqual(report.source_triangles, model_object.source_triangle_count(vertices, faces))
        model.validate()

    def test_quad_triangulation_and_bad_target(self):
        vertices, faces, _ = model_object.parse_obj(CUBE)
        self.assertEqual(model_object.source_triangle_count(vertices, faces), 2*len(QUADS))
        points, welded = model_object._weld(vertices)
        triangles = model_object._triangles(faces, welded)
        np.testing.assert_array_equal(model_object._triangles(triangles, np.arange(len(points))), triangles)
        for target in (3, True, 4.5, 12):
            with self.subTest(target=target), self.assertRaises(ValueError):
                model_from_obj(CUBE, simplify_to=target)

    def test_explicit_line_survives(self):
        text = dense_mesh_obj()
        vertices, _, _ = model_object.parse_obj(text)
        n = len(vertices)
        text += f'v -5 -5 -5\nv 5 5 5\nl {n+1} {n+2}\n'
        model, report = model_from_obj(text, simplify_to=500)
        self.assertTrue(report.simplified)
        points = np.asarray(model.vertices)
        a = int(np.flatnonzero(np.all(np.isclose(points, -1/np.sqrt(3)), axis=1))[0])
        b = int(np.flatnonzero(np.all(np.isclose(points, 1/np.sqrt(3)), axis=1))[0])
        self.assertIn((min(a, b), max(a, b)), model.edges)


if __name__ == '__main__':
    unittest.main()
