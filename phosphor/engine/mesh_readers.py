"""STL bytes and glTF 2.0 accessors to NumPy arrays (0.58.0-dev)."""
import math
import struct
import numpy as np

_STL_RECORD = np.dtype([('normal', '<f4', (3,)), ('v', '<f4', (3, 3)), ('attribute', '<u2')])


def _stl_result(vertices):
    vertices = np.asarray(vertices, dtype=np.float64).reshape(-1, 3)
    if not np.isfinite(vertices).all():
        raise ValueError('This STL has an invalid (non-finite) vertex.')
    if not len(vertices):
        raise ValueError('This STL has no faces to draw.')
    return vertices, np.arange(len(vertices), dtype=np.int64).reshape(-1, 3)


def _stl_ascii(data, max_faces):
    vertices, loop, faces = [], None, 0  # loop is None between facets.
    for number, line in enumerate(data.decode('ascii', errors='replace').splitlines(), 1):
        parts = line.split()
        keyword = parts[0].lower() if parts else ''
        if keyword in ('facet', 'outer'):
            loop = []
        elif keyword == 'vertex':
            try:
                vertex = tuple(float(x) for x in parts[1:4])
            except ValueError:
                vertex = ()
            if len(vertex) != 3:
                raise ValueError(f'Invalid STL vertex on line {number}.')
            loop = (loop or [])+[vertex]
        elif keyword == 'endloop' or (keyword == 'endfacet' and loop is not None):
            if len(loop or ()) != 3:
                raise ValueError(f'STL facet on line {number} does not have 3 vertices.')
            faces += 1
            if faces > max_faces:
                raise ValueError(f'This STL has more than {max_faces} faces. Simplify it first.')
            vertices += loop
            loop = None
    return _stl_result(vertices)


def parse_stl(data: bytes, max_faces: int) -> tuple:
    """(vertices float64 (3F, 3), faces int64 (F, 3) = arange(3F)) from ASCII or binary STL bytes.

    Binary files may also begin with b'solid', so the size decides first: a binary file
    is exactly 84 + 50 × count bytes. Text that starts with 'solid' and has facets is
    ASCII; otherwise a binary file with trailing bytes is accepted."""
    count = struct.unpack_from('<I', data, 80)[0] if len(data) >= 84 else None
    text = data.lstrip()
    if count is None or 84+50*count != len(data):
        if text.startswith(b'solid') and b'facet' in text:
            return _stl_ascii(data, max_faces)
        if count is None or 84+50*count > len(data):
            raise ValueError('This STL is truncated or is not an STL file.')
    if count > max_faces:
        raise ValueError(f'This STL has more than {max_faces} faces. Simplify it first.')
    return _stl_result(np.frombuffer(data, _STL_RECORD, count, 84)['v'])


_ACC_COMPONENTS = {5120: np.dtype('i1'), 5121: np.dtype('u1'), 5122: np.dtype('<i2'), 5123: np.dtype('<u2'),
                   5125: np.dtype('<u4'), 5126: np.dtype('<f4')}
_ACC_TYPES = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT4': 16}
_ACC_NORMALIZED = {5120: 127., 5121: 255., 5122: 32767., 5123: 65535.}


def _acc_int(value, name):
    if type(value) is not int or value < 0:
        raise ValueError(f'glTF {name} is invalid.')
    return value


def _acc_view(gltf, buffers, index):
    """(buffer bytes, view start, view byteLength, byteStride or None) of bufferView `index`."""
    view = gltf['bufferViews'][_acc_int(index, 'buffer view')]
    data = buffers[_acc_int(view['buffer'], 'buffer')]
    start, length = _acc_int(view.get('byteOffset', 0), 'buffer view'), _acc_int(view['byteLength'], 'buffer view')
    if start+length > len(data):
        raise ValueError(f'glTF buffer view {index} is outside its buffer.')
    stride = view.get('byteStride')
    return data, start, length, None if stride is None else _acc_int(stride, 'buffer view')


def _acc_read(gltf, buffers, view_index, offset, count, dtype, components, packed=False):
    """Elements from a buffer view; sparse data is tightly packed (packed=True ignores byteStride)."""
    data, start, length, view_stride = _acc_view(gltf, buffers, view_index)
    offset, size = _acc_int(offset, 'accessor'), dtype.itemsize*components
    stride = size if packed or not view_stride else view_stride
    if count and offset+stride*(count-1)+size > length:
        raise ValueError('glTF accessor data is outside its buffer view.')
    if not count:
        return np.zeros((0, components), dtype)
    return np.ndarray((count, components), dtype, data, start+offset, (stride, dtype.itemsize)).copy()


def read_accessor(gltf: dict, index: int, buffers: list) -> np.ndarray:
    """glTF 2.0 accessor `index`: SCALAR (count,), VECn/MAT4 (count, n). Float and
    normalized accessors are float64, other integers int64. Handles byteStride,
    normalized integers, sparse substitution and accessors without a bufferView."""
    accessors = gltf.get('accessors', []) if isinstance(gltf, dict) else []
    if type(index) is not int or not 0 <= index < len(accessors):
        raise ValueError(f'glTF accessor {index} does not exist.')
    try:
        accessor = accessors[index]
        component, kind = accessor['componentType'], accessor['type']
        normalized = accessor.get('normalized', False) is True
        if component not in _ACC_COMPONENTS or kind not in _ACC_TYPES or (normalized and component not in _ACC_NORMALIZED):
            raise ValueError(f'glTF accessor {index} has an unsupported type.')
        dtype, components = _ACC_COMPONENTS[component], _ACC_TYPES[kind]
        count = _acc_int(accessor['count'], 'accessor')
        if 'bufferView' in accessor:
            values = _acc_read(gltf, buffers, accessor['bufferView'], accessor.get('byteOffset', 0), count, dtype, components)
        else:
            values = np.zeros((count, components), dtype)
        sparse = accessor.get('sparse')
        if sparse is not None:
            changed = _acc_int(sparse['count'], 'sparse accessor')
            indices, replacement = sparse['indices'], sparse['values']
            if indices['componentType'] not in (5121, 5123, 5125):
                raise ValueError(f'glTF accessor {index} has invalid sparse indices.')
            where = _acc_read(gltf, buffers, indices['bufferView'], indices.get('byteOffset', 0), changed,
                              _ACC_COMPONENTS[indices['componentType']], 1, True).ravel().astype(np.int64)
            if (where >= count).any():
                raise ValueError(f'glTF accessor {index} has invalid sparse indices.')
            values[where] = _acc_read(gltf, buffers, replacement['bufferView'], replacement.get('byteOffset', 0),
                                      changed, dtype, components, True)
    except (KeyError, IndexError, TypeError, AttributeError) as error:
        raise ValueError(f'glTF accessor {index} is invalid.') from error
    if normalized:
        values = np.maximum(values/_ACC_NORMALIZED[component], -1.)
    else:
        values = values.astype(np.float64 if component == 5126 else np.int64)
    return values.ravel() if components == 1 else values
