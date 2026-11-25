"""Deterministic faceted torus meshes for 3D-model performance tests.

The defaults resemble a dense game-character OBJ after feature-edge reduction: about
5000 triangles keep about 2000 sharp edges in about 110 separate pieces with about 800
odd-degree vertices, which is what makes route planning (model_object.model_route) slow.
Drafted by the local Gemma 4 12B; jitter and relief tuned by Claude.
"""
import numpy as np


def dense_mesh_obj(rings: int = 50, segments: int = 50, facets: int = 7,
                   relief: float = 0.1, jitter: float = 0.06) -> str:
    """OBJ text for a closed, faceted 'gem torus' of 2*rings*segments triangles.

    relief: depth of the twisted facets (fraction of the tube radius).
    jitter: seeded per-vertex radial roughness; it breaks the crease lines into pieces.
    """
    if not (isinstance(rings, int) and rings >= 3):
        raise ValueError("rings must be an integer >= 3")
    if not (isinstance(segments, int) and segments >= 3):
        raise ValueError("segments must be an integer >= 3")
    if not (isinstance(facets, int) and facets >= 1):
        raise ValueError("facets must be an integer >= 1")
    if not (0 <= relief <= 1 and 0 <= jitter <= .5):
        raise ValueError("relief must be 0-1 and jitter 0-0.5")

    u = 2 * np.pi * np.arange(rings) / rings
    v = 2 * np.pi * np.arange(segments) / segments
    U, V = np.meshgrid(u, v, indexing='ij')

    # Triangle wave with `facets` peaks per turn, twisted along the ring.
    f_v = 1 - 4 * np.abs(((facets * (V + 0.5 * U) / (2 * np.pi)) % 1) - 0.5)
    r = 0.35 * (1 + relief * f_v)
    # A fixed hash of the grid position (no random generator, so every platform agrees).
    noise = (np.sin(U * 12.9898 * rings + V * 78.233 * segments) * 43758.5453) % 1.0
    r = r * (1 + jitter * (2 * noise - 1))

    X = (1 + r * np.cos(V)) * np.cos(U)
    Y = (1 + r * np.cos(V)) * np.sin(U)
    Z = r * np.sin(V)
    vertices = np.stack([X, Y, Z], axis=-1).reshape(-1, 3)

    lines = [f"# dense_mesh_obj rings={rings} segments={segments} facets={facets} relief={relief} jitter={jitter}"]
    lines.extend(f"v {x:.6f} {y:.6f} {z:.6f}" for x, y, z in vertices)
    for i in range(rings):
        for j in range(segments):
            a = i * segments + j
            b = ((i + 1) % rings) * segments + j
            c = ((i + 1) % rings) * segments + ((j + 1) % segments)
            d = i * segments + ((j + 1) % segments)
            lines.append(f"f {a+1} {b+1} {c+1}")
            lines.append(f"f {a+1} {c+1} {d+1}")
    return "\n".join(lines) + "\n"


def write_dense_mesh(path, **kwargs) -> int:
    """Write dense_mesh_obj(**kwargs) to path (UTF-8). Return the number of triangles."""
    text = dense_mesh_obj(**kwargs)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return sum(1 for line in text.splitlines() if line.startswith('f '))
