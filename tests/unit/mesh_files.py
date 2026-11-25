"""Small generated mesh files shared by unit and editor smoke tests."""
import base64
import json
from pathlib import Path
import struct

from phosphor.engine.model_object import parse_obj
from tests.unit.test_model_object import TRIANGLES


def stl_bytes(binary=False):
    vertices, faces, _ = parse_obj(TRIANGLES)
    if binary:
        header = b'solid binary cube'.ljust(80, b' ')
        return header+struct.pack('<I', len(faces))+b''.join(
            struct.pack('<12fH', 0, 0, 0, *(v for i in face for v in vertices[i]), 0)
            for face in faces)
    rows = ['solid cube']
    for face in faces:
        rows.extend(['facet normal 0 0 0', 'outer loop'])
        rows.extend('vertex '+' '.join(map(str, vertices[i])) for i in face)
        rows.extend(['endloop', 'endfacet'])
    return ('\n'.join(rows+['endsolid cube'])+'\n').encode('ascii')


def gltf_mesh(positions=None, indices=None, mode=4, component_type=5123):
    """Return a document and packed buffer, with selectable uint16/uint32 indices."""
    if positions is None:
        positions, faces, _ = parse_obj(TRIANGLES)
        if indices is None:
            indices = [i for face in faces for i in face]
    data = struct.pack('<'+'f'*(len(positions)*3), *(v for p in positions for v in p))
    views = [dict(buffer=0, byteOffset=0, byteLength=len(data))]
    accessors = [dict(bufferView=0, componentType=5126, count=len(positions), type='VEC3')]
    primitive = dict(attributes=dict(POSITION=0), mode=mode)
    if indices is not None:
        packed = struct.pack('<'+('H' if component_type == 5123 else 'I')*len(indices), *indices)
        views.append(dict(buffer=0, byteOffset=len(data), byteLength=len(packed)))
        accessors.append(dict(bufferView=1, componentType=component_type, count=len(indices), type='SCALAR'))
        primitive['indices'] = 1
        data += packed
    document = dict(asset=dict(version='2.0'), buffers=[dict(byteLength=len(data))],
                    bufferViews=views, accessors=accessors,
                    meshes=[dict(primitives=[primitive])], nodes=[dict(mesh=0)],
                    scenes=[dict(nodes=[0])], scene=0)
    return document, data


def write_gltf(folder, kind='external', document=None, data=None, name='cube'):
    if document is None:
        document, data = gltf_mesh()
    # Keep callers' dictionaries intact when choosing a buffer container.
    document = json.loads(json.dumps(document))
    folder = Path(folder)
    path = folder/(name+('.glb' if kind == 'glb' else '.gltf'))
    if kind == 'glb':
        document['buffers'][0].pop('uri', None)
        text = json.dumps(document).encode('utf-8')
        text += b' '*((-len(text)) % 4)
        packed = data+b'\0'*((-len(data)) % 4)
        chunks = struct.pack('<II', len(text), 0x4E4F534A)+text
        chunks += struct.pack('<II', len(packed), 0x004E4942)+packed
        path.write_bytes(struct.pack('<4sII', b'glTF', 2, 12+len(chunks))+chunks)
    else:
        if kind == 'data':
            document['buffers'][0]['uri'] = 'data:application/octet-stream;base64,'+base64.b64encode(data).decode('ascii')
        else:
            document['buffers'][0]['uri'] = name+'.bin'
            (folder/(name+'.bin')).write_bytes(data)
        path.write_text(json.dumps(document), encoding='utf-8')
    return path


def write_cube_files(folder):
    folder = Path(folder)
    obj = folder/'cube.obj'
    obj.write_text(TRIANGLES, encoding='utf-8')
    ascii_stl, binary_stl = folder/'ascii.stl', folder/'binary.stl'
    ascii_stl.write_bytes(stl_bytes())
    binary_stl.write_bytes(stl_bytes(True))
    return [obj, ascii_stl, binary_stl, write_gltf(folder), write_gltf(folder, 'glb')]
