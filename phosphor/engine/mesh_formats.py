"""STL, glTF 2.0 and GLB import (0.58.0-dev). Each reader returns (vertices, faces, lines);
model_object.model_from_mesh does the shared welding, crease reduction and normalizing.
"""
import base64
from dataclasses import dataclass
import binascii
import json
from pathlib import Path, PureWindowsPath
import re
import struct
from urllib.parse import unquote

import numpy as np

from phosphor.engine import model_object
from phosphor.engine.mesh_readers import parse_stl, read_accessor

MODEL_FILTERS = '3D models (*.obj *.stl *.gltf *.glb);;Wavefront OBJ (*.obj);;STL (*.stl);;glTF 2.0 (*.gltf *.glb)'


def _check_size(size, label):
    if size > model_object.MAX_MODEL_BYTES:
        raise ValueError(f'{label} files are limited to {model_object.MAX_MODEL_BYTES//(1024*1024)} MB.')


@dataclass(frozen=True, eq=False)
class MeshSource:
    vertices: object
    faces: object
    lines: object
    name: str
    label: str
    up_axis: str = 'Y'
    animation_source: object = None


def source_triangles(mesh):
    return model_object.source_triangle_count(mesh.vertices, mesh.faces, mesh.label)


# Models with more triangles open the import dialog with Simplify ticked at this target.
SUGGESTED_TRIANGLES = 10_000


def up_axis_vertices(vertices, up_axis):
    if up_axis == 'Y': return vertices
    if up_axis != 'Z': raise ValueError('Up axis must be Y or Z.')
    points = np.asarray(vertices, dtype=float)
    return np.column_stack((points[:, 0], points[:, 2], -points[:, 1]))


def model_from_source(mesh, simplify_to=None, progress=None, up_axis=None, animation=None, in_place=True):
    if animation is not None:
        return mesh.animation_source.model(animation, simplify_to, progress,
                                           mesh.up_axis if up_axis is None else up_axis, in_place)
    vertices = up_axis_vertices(mesh.vertices, mesh.up_axis if up_axis is None else up_axis)
    return model_object.model_from_mesh(vertices, mesh.faces, mesh.lines,
                                        mesh.name, mesh.label, simplify_to, progress)


def load_model(path, simplify_to=None, up_axis=None):
    return model_from_source(read_mesh(path, up_axis), simplify_to)


def read_mesh(path, up_axis=None):
    path = Path(path)
    suffix = path.suffix.lower()
    up_axis = ('Z' if suffix == '.stl' else 'Y') if up_axis is None else up_axis
    if up_axis not in ('Y', 'Z'): raise ValueError('Up axis must be Y or Z.')
    if suffix == '.obj':
        _check_size(path.stat().st_size, 'OBJ')
        return MeshSource(*model_object.parse_obj(path.read_text(encoding='utf-8', errors='replace')), path.stem, 'OBJ', up_axis)
    if suffix == '.stl':
        _check_size(path.stat().st_size, 'STL')
        vertices, faces = parse_stl(path.read_bytes(), model_object.MAX_MODEL_FACES)
        return MeshSource(vertices, faces, [], path.stem, 'STL', up_axis)
    if suffix in ('.gltf', '.glb'):
        label = 'GLB' if suffix == '.glb' else 'glTF'
        _check_size(path.stat().st_size, label)
        data = path.read_bytes()
        static = parse_gltf(data, path.parent, label)
        gltf, binary = _container(data, label)
        source = AnimationSource(gltf, _buffers(gltf, binary, path.parent, len(data), label),
                                 static[1], static[2], path.stem, label) if gltf.get('animations') else None
        return MeshSource(*static, path.stem, label, up_axis, source)
    raise ValueError(f'Unsupported 3D model file type: {suffix or "(none)"}. Use OBJ, STL, glTF or GLB.')


def load_stl(path, up_axis=None):
    return load_model(path, up_axis=up_axis)


def load_gltf(path, up_axis=None):
    return load_model(path, up_axis=up_axis)


def _container(data, label):
    binary = None
    payload = data
    if data[:4] == b'glTF':
        damaged = f'This {label} file is damaged.'
        if len(data) < 12:
            raise ValueError(damaged)
        _, version, length = struct.unpack_from('<4sII', data)
        if version != 2 or length < 20 or length > len(data):
            raise ValueError(damaged)
        offset, chunks = 12, []
        while offset < length:
            if offset+8 > length:
                raise ValueError(damaged)
            size, kind = struct.unpack_from('<II', data, offset)
            offset += 8
            if offset+size > length:
                raise ValueError(damaged)
            chunks.append((kind, data[offset:offset+size]))
            offset += size
        if not chunks or chunks[0][0] != 0x4E4F534A:
            raise ValueError(damaged)
        payload = chunks[0][1]
        if len(chunks) > 1:
            if len(chunks) != 2 or chunks[1][0] != 0x004E4942:
                raise ValueError(damaged)
            binary = chunks[1][1]
    try:
        document = json.loads(payload.decode('utf-8'))
    except (UnicodeError, ValueError) as error:
        raise ValueError(f'This {label} file is not valid glTF JSON.') from error
    if not isinstance(document, dict):
        raise ValueError(f'This {label} file is not valid glTF JSON.')
    return document, binary


def _buffers(gltf, binary, folder, size, label):
    buffers = []
    folder = Path(folder).resolve()
    for i, description in enumerate(gltf.get('buffers', [])):
        uri = description.get('uri')
        if uri is None:
            if i != 0 or binary is None:
                raise ValueError(f'glTF buffer {i} has no URI or GLB BIN chunk.')
            value = binary
        elif uri.startswith('data:'):
            if ';base64,' not in uri:
                raise ValueError(f'glTF buffer {i} is not valid base64 data.')
            try:
                value = base64.b64decode(uri.split(';base64,', 1)[1], validate=True)
            except (ValueError, binascii.Error) as error:
                raise ValueError(f'glTF buffer {i} is not valid base64 data.') from error
        else:
            u = unquote(uri)
            unsafe = f'glTF buffer {i} must be a file next to the model: {uri}'
            parts = re.split(r'[/\\]', u)
            if ('://' in u or re.match(r'^[^/\\]*:', u) or u.startswith(('/', '\\'))
                    or PureWindowsPath(u).drive or Path(u).is_absolute() or '..' in parts):
                raise ValueError(unsafe)
            target = folder.joinpath(*parts).resolve()
            if not target.is_relative_to(folder):
                raise ValueError(unsafe)
            try:
                size += target.stat().st_size
                _check_size(size, label)
                value = target.read_bytes()
            except FileNotFoundError as error:
                raise ValueError(f'glTF buffer file not found: {u}') from error
        if len(value) < description['byteLength']:
            raise ValueError(f'glTF buffer {i} is shorter than its byteLength.')
        buffers.append(value)
    return buffers


def _local(node):
    if 'matrix' in node:
        return np.array(node['matrix'], dtype=float).reshape(4, 4).T
    translation = np.eye(4)
    translation[:3, 3] = node.get('translation', [0, 0, 0])
    x, y, z, w = node.get('rotation', [0, 0, 0, 1])
    rotation = np.eye(4)
    rotation[:3, :3] = [[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                       [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                       [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]]
    scale = np.eye(4)
    scale[:3, :3] = np.diag(node.get('scale', [1, 1, 1]))
    return translation @ rotation @ scale


def _placements(gltf):
    nodes = gltf.get('nodes', [])
    if not nodes:
        for mesh in range(len(gltf.get('meshes', []))):
            yield mesh, np.eye(4)
        return
    # glTF requires a strict tree (one parent per node). A node shared by several parents
    # could multiply the placements exponentially, so it is rejected.
    children = [child for node in nodes for child in node.get('children', [])]
    if len(children) != len(set(children)):
        raise ValueError('glTF node hierarchy is not a tree: a node has more than one parent.')
    if gltf.get('scenes'):
        scene = gltf.get('scene', 0)
        if type(scene) is not int or scene < 0:
            raise IndexError(scene)
        roots = gltf['scenes'][scene].get('nodes', [])
    else:
        # A cyclic component has no root, so check the whole hierarchy before
        # selecting roots rather than silently losing that component.
        complete, active = set(), set()
        for start in range(len(nodes)):
            pending = [(start, False)]
            while pending:
                index, leaving = pending.pop()
                if not isinstance(index, int) or not 0 <= index < len(nodes):
                    raise IndexError(index)
                if leaving:
                    active.remove(index)
                    complete.add(index)
                elif index in active:
                    raise ValueError('glTF node hierarchy has a cycle.')
                elif index not in complete:
                    active.add(index)
                    pending.append((index, True))
                    pending.extend((child, False) for child in reversed(nodes[index].get('children', [])))
        children = {child for node in nodes for child in node.get('children', [])}
        roots = [i for i in range(len(nodes)) if i not in children]
    stack = [(i, np.eye(4), frozenset()) for i in reversed(roots)]
    while stack:
        index, parent, path = stack.pop()
        if index in path:
            raise ValueError('glTF node hierarchy has a cycle.')
        if not isinstance(index, int) or not 0 <= index < len(nodes):
            raise IndexError(index)
        node = nodes[index]
        world = parent @ _local(node)
        if 'mesh' in node:
            yield node['mesh'], world
        path = path | {index}
        stack.extend((child, world, path) for child in reversed(node.get('children', [])))


def parse_gltf(data: bytes, folder: Path, label: str):
    gltf, binary = _container(data, label)
    try:
        return _parse_gltf(gltf, binary, folder, len(data), label)
    except (KeyError, IndexError, TypeError, AttributeError) as error:
        raise ValueError(f'This {label} file is not valid glTF.') from error


def _parse_gltf(gltf, binary, folder, size, label):
    version = gltf.get('asset', {}).get('version', '')
    if not isinstance(version, str) or not version.startswith('2.'):
        raise ValueError('Only glTF 2.0 files are supported.')
    extensions = [gltf.get('extensionsRequired', []), gltf.get('extensionsUsed', [])]
    extensions.extend(view.get('extensions', {}) for view in gltf.get('bufferViews', []))
    extensions.extend(primitive.get('extensions', {}) for mesh in gltf.get('meshes', [])
                      for primitive in mesh.get('primitives', []))
    for name in ('KHR_draco_mesh_compression', 'EXT_meshopt_compression'):
        if any(name in used for used in extensions):
            raise ValueError(f'This {label} uses {name} compression, which phosphor cannot read. Re-export it without compression.')
    _check_size(size, label)
    buffers = _buffers(gltf, binary, folder, size, label)
    vertices, faces, lines = [], [], []
    offset = elements = 0
    meshes = gltf.get('meshes', [])
    for mesh, world in _placements(gltf):
        if not isinstance(mesh, int) or not 0 <= mesh < len(meshes):
            raise IndexError(mesh)
        for primitive in meshes[mesh].get('primitives', []):
            attributes = primitive.get('attributes', {})
            mode = primitive.get('mode', 4)
            if 'POSITION' not in attributes or mode == 0:
                continue
            points = np.asarray(read_accessor(gltf, attributes['POSITION'], buffers), dtype=float)
            if points.ndim != 2 or points.shape[1] != 3:
                raise ValueError('glTF POSITION must be a VEC3 accessor.')
            points = points @ world[:3, :3].T + world[:3, 3]
            if not np.isfinite(points).all():
                raise ValueError(f'This {label} has an invalid (non-finite) vertex.')
            indices = (np.asarray(read_accessor(gltf, primitive['indices'], buffers), dtype=np.int64)
                       if 'indices' in primitive else np.arange(len(points), dtype=np.int64))
            if indices.ndim != 1:
                raise ValueError('glTF primitive indices must be a SCALAR accessor.')
            if np.any(indices < 0) or np.any(indices >= len(points)):
                raise ValueError('glTF primitive indices refer to a missing vertex.')
            indices = indices+offset
            triangles = np.empty((0, 3), dtype=np.int64)
            new_lines = []
            if mode == 1:
                new_lines = indices[:len(indices)//2*2].reshape(-1, 2).tolist()
            elif mode in (2, 3):
                if len(indices) >= 2:
                    line = np.r_[indices, indices[:1]] if mode == 2 else indices
                    new_lines = [line.tolist()]
            elif mode == 4:
                triangles = indices[:len(indices)//3*3].reshape(-1, 3)
            elif mode == 5 and len(indices) >= 3:
                triangles = np.column_stack((indices[:-2], indices[1:-1], indices[2:]))
                triangles[1::2, :2] = triangles[1::2, 1::-1].copy()
            elif mode == 6 and len(indices) >= 3:
                triangles = np.column_stack((np.full(len(indices)-2, indices[0]), indices[1:-1], indices[2:]))
            elif mode not in (5, 6):
                raise ValueError(f'glTF primitive mode {mode} is not supported.')
            triangles = triangles[(triangles[:, 0] != triangles[:, 1]) &
                                  (triangles[:, 1] != triangles[:, 2]) &
                                  (triangles[:, 0] != triangles[:, 2])]
            elements += len(triangles)+sum(len(line)-1 for line in new_lines)
            if elements > model_object.MAX_MODEL_FACES:
                raise ValueError(f'This {label} has more than {model_object.MAX_MODEL_FACES} faces. Simplify it first.')
            vertices.append(points)
            offset += len(points)
            faces.extend(triangles.tolist())
            lines.extend(new_lines)
    if not faces and not lines:
        raise ValueError(f'This {label} has no faces or lines to draw.')
    return np.concatenate(vertices), faces, lines


@dataclass(frozen=True)
class AnimationInfo:
    name: str
    duration: float
    key_count: int  # Maximum keys in a sampler (single-key actions stay hidden).


def sample_channel(times, values, t, interpolation='LINEAR', rotation=False):
    """glTF interpolation, including derivative tangents measured per second."""
    if interpolation not in ('LINEAR', 'STEP', 'CUBICSPLINE'):
        raise ValueError(f'Unsupported glTF interpolation: {interpolation}')
    cubic = interpolation == 'CUBICSPLINE'
    if t <= times[0]: result = values[1 if cubic else 0].copy()
    elif t >= times[-1]: result = values[-2 if cubic else -1].copy()
    else:
        i = int(np.searchsorted(times, t, side='right') - 1)
        dt = times[i+1]-times[i]
        a = (t-times[i])/dt
        if cubic:
            p, q = values[3*i+1], values[3*(i+1)+1]
            result = ((2*a**3-3*a*a+1)*p + (a**3-2*a*a+a)*dt*values[3*i+2]
                      + (-2*a**3+3*a*a)*q + (a**3-a*a)*dt*values[3*(i+1)])
        elif interpolation == 'STEP': result = values[i].copy()
        elif rotation:
            p, q = values[i]/np.linalg.norm(values[i]), values[i+1]/np.linalg.norm(values[i+1])
            dot = float(p @ q)
            if dot < 0: q, dot = -q, -dot
            angle = np.arccos(min(1., dot))
            result = ((1-a)*p+a*q if dot > .9995 else
                      (np.sin((1-a)*angle)*p + np.sin(a*angle)*q)/np.sin(angle))
        else: result = (1-a)*values[i]+a*values[i+1]
    if rotation:
        length = np.linalg.norm(result)
        if length == 0: raise ValueError('glTF animation has a zero quaternion.')
        result = result/length
    return result


def skin_positions(points, joints, weights, joint_world, inverse_bind):
    """World-space linear blend skinning; mesh-node transform cancels by glTF definition."""
    matrices = np.asarray(joint_world) @ np.asarray(inverse_bind)
    homogeneous = np.column_stack((points, np.ones(len(points))))
    return np.einsum('vk,vkij,vj->vi', weights, matrices[joints], homogeneous)[:, :3]


class AnimationSource:
    """Import-only hierarchy, accessors and channels; never embedded in a scene."""
    def __init__(self, gltf, buffers, faces, lines, name, label):
        self.gltf, self.buffers = gltf, buffers
        self.faces, self.lines, self.name, self.label = faces, lines, name, label
        self.nodes = gltf.get('nodes', [])
        self.parents = {}
        for i, node in enumerate(self.nodes):
            for child in node.get('children', []):
                if child in self.parents: raise ValueError('glTF hierarchy has multiple parents.')
                self.parents[child] = i
        self.channels, infos = [], []
        for index, animation in enumerate(gltf.get('animations', [])):
            samplers = []
            used = {c['sampler'] for c in animation.get('channels', [])
                    if c['target']['path'] in ('translation', 'rotation', 'scale')}
            for sampler_index, sampler in enumerate(animation.get('samplers', [])):
                if sampler_index not in used:
                    samplers.append(None)
                    continue
                times = self.accessor(sampler['input'])
                values = self.accessor(sampler['output'])
                interpolation = sampler.get('interpolation', 'LINEAR')
                if (times.ndim != 1 or len(times) == 0 or not np.isfinite(times).all()
                        or np.any(np.diff(times) <= 0) or times[0] < 0
                        or not np.isfinite(values).all()
                        or len(values) != len(times)*(3 if interpolation == 'CUBICSPLINE' else 1)):
                    raise ValueError('Invalid glTF animation sampler.')
                if interpolation not in ('LINEAR', 'STEP', 'CUBICSPLINE'):
                    raise ValueError('Unsupported glTF animation interpolation.')
                samplers.append((times, values, interpolation))
            channels = []
            for channel in animation.get('channels', []):
                target = channel['target']
                if target['path'] == 'weights': continue
                if target['path'] not in ('translation', 'rotation', 'scale'): continue
                node = target['node']
                if not 0 <= node < len(self.nodes): raise ValueError('Missing animation node.')
                sampler = samplers[channel['sampler']]
                width = 4 if target['path'] == 'rotation' else 3
                if sampler[1].ndim != 2 or sampler[1].shape[1] != width:
                    raise ValueError('Invalid glTF animation output accessor.')
                channels.append((node, target['path'], *sampler))
            self.channels.append(channels)
            infos.append(AnimationInfo(animation.get('name', f'Animation {index+1}'),
                                       max((c[2][-1] for c in channels), default=0.),
                                       max((len(c[2]) for c in channels), default=0)))
        self.animations = tuple(infos)
        self.primitives = []
        # Match the static reader's active-scene placement and primitive order exactly.
        roots = (gltf['scenes'][gltf.get('scene', 0)].get('nodes', []) if gltf.get('scenes')
                 else [i for i in range(len(self.nodes)) if i not in self.parents])
        pending = list(reversed(roots))
        while pending:
            i = pending.pop()
            node = self.nodes[i]
            pending.extend(reversed(node.get('children', [])))
            if 'mesh' not in node: continue
            for primitive in gltf['meshes'][node['mesh']].get('primitives', []):
                attrs = primitive.get('attributes', {})
                if 'POSITION' not in attrs or primitive.get('mode', 4) == 0: continue
                points = self.accessor(attrs['POSITION'])
                skin = None
                if 'skin' in node:
                    description = gltf['skins'][node['skin']]
                    joint_values = self.accessor(attrs['JOINTS_0'])
                    joints = np.asarray(joint_values, dtype=int)
                    weights = self.accessor(attrs['WEIGHTS_0'])
                    joint_nodes = description['joints']
                    if (joints.shape != (len(points), 4) or weights.shape != joints.shape
                            or np.any(joints != joint_values)
                            or np.any(joints < 0) or np.any(joints >= len(joint_nodes))
                            or not np.isfinite(weights).all() or np.any(weights < 0)
                            or np.any(weights.sum(axis=1) <= 0)):
                        raise ValueError('Invalid glTF skin weights or joints.')
                    weights = weights / weights.sum(axis=1, keepdims=True)
                    inverse = (self.accessor(description['inverseBindMatrices']).reshape(-1, 4, 4).transpose(0, 2, 1)
                               if 'inverseBindMatrices' in description else np.tile(np.eye(4), (len(joint_nodes), 1, 1)))
                    if (inverse.shape != (len(joint_nodes), 4, 4) or not np.isfinite(inverse).all()
                            or any(type(j) is not int or not 0 <= j < len(self.nodes) for j in joint_nodes)):
                        raise ValueError('Invalid glTF inverse bind matrices or joint nodes.')
                    skin = (joints, weights, joint_nodes, inverse)
                self.primitives.append((i, points, skin))

    def accessor(self, index):
        return np.asarray(read_accessor(self.gltf, index, self.buffers), dtype=float)

    @property
    def picker(self):
        return tuple(i for i, a in enumerate(self.animations) if a.key_count >= 2 and a.duration > 0)

    def worlds(self, animation=None, t=0.):
        nodes = [dict(n) for n in self.nodes]
        if animation is not None:
            for node, path, times, values, interpolation in self.channels[animation]:
                nodes[node][path] = sample_channel(times, values, t, interpolation, path == 'rotation')
        result, active = {}, set()
        def world(i):
            if i in result: return result[i]
            if i in active: raise ValueError('glTF hierarchy has a cycle.')
            active.add(i)
            result[i] = (world(self.parents[i]) if i in self.parents else np.eye(4)) @ _local(nodes[i])
            active.remove(i)
            return result[i]
        return np.asarray([world(i) for i in range(len(nodes))])

    def positions(self, animation=None, t=0., in_place=False, up_axis='Y'):
        worlds = self.worlds(animation, t)
        initial = self.worlds(animation, 0.) if in_place else None
        output = []
        for node, points, skin in self.primitives:
            if skin is None:
                placed = points @ worlds[node, :3, :3].T + worlds[node, :3, 3]
                root = node
            else:
                joints, weights, joint_nodes, inverse = skin
                placed = skin_positions(points, joints, weights, worlds[joint_nodes], inverse)
                root = joint_nodes[0]
            if in_place:
                # Follow the root of this mesh's animated hierarchy, including parents.
                translations = {c[0] for c in self.channels[animation] if c[1] == 'translation'} if animation is not None else set()
                if skin is not None:
                    root = next((j for j in joint_nodes if j in translations), root)
                ancestor = root
                while ancestor in self.parents:
                    ancestor = self.parents[ancestor]
                    if ancestor in translations: root = ancestor
                travel = up_axis_vertices([worlds[root, :3, 3] - initial[root, :3, 3]], up_axis)[0]
            placed = up_axis_vertices(placed, up_axis)
            if in_place: placed = placed - [travel[0], 0., travel[2]]
            output.append(placed)
        return np.concatenate(output)

    def bake(self, animation, in_place=True, up_axis='Y', duration=None):
        from phosphor.engine.model_animation import MAX_FRAMES
        duration = self.animations[animation].duration if duration is None else duration
        count = int(np.ceil(duration*30)) + 1
        # The full mesh is import-only; still bound its allocation before baking.
        if count > MAX_FRAMES or count * sum(len(p) for _, p, _ in self.primitives) * 24 > 256*1024*1024:
            raise ValueError('Animation is too large to bake; shorten it before importing.')
        return np.asarray([self.positions(animation, min(i/30., duration), in_place, up_axis)
                           for i in range(count)])

    def model(self, animation, simplify_to=None, progress=None, up_axis='Y', in_place=True, duration=None):
        from dataclasses import replace
        from phosphor.engine.model_animation import ModelAnimation
        frames = self.bake(animation, in_place, up_axis, duration)
        used = []
        model, report = model_object.model_from_mesh(frames[0], self.faces, self.lines,
                                                    self.name, self.label, simplify_to, progress, used)
        frames = frames[:, used]
        low, high = frames.min(axis=(0, 1)), frames.max(axis=(0, 1))
        frames = frames - (low+high)/2
        radius = np.linalg.norm(frames, axis=2).max()
        payload = ModelAnimation.from_frames(frames/radius, 30.,
                         self.animations[animation].duration if duration is None else duration)
        model = replace(model, vertices=tuple(tuple(float(x) for x in p) for p in payload.frames[0]), animation=payload)
        model.validate()
        return model, report
