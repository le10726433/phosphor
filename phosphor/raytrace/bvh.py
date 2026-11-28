"""Fast vectorized Surface-Area-Heuristic (SAH) BVH builder for GPU compute path tracing.

Packs triangles and BVH nodes into 16-byte aligned NumPy arrays ready for direct upload
to WGSL storage buffers on macOS Metal and Windows Direct3D 12 / Vulkan via wgpu.
"""
import numpy as np


# GPU struct layouts (all 16-byte aligned for WGSL storage buffers):
#
# struct BvhNode {           // 32 bytes (8 x f32/u32)
#     bmin: vec3<f32>,
#     left_or_first: u32,    // leaf: first triangle index; internal: left child index
#     bmax: vec3<f32>,
#     count_or_right: u32,   // leaf: tri_count (>0); internal: 0x80000000 | right_child_idx
# };
#
# struct GpuTri {            // 112 bytes (28 x f32/u32 = 7 x vec4)
#     v0: vec3<f32>,  mat_id: u32,
#     e1: vec3<f32>,  uv0x: f32,
#     e2: vec3<f32>,  uv0y: f32,
#     n0: vec3<f32>,  uv1x: f32,
#     n1: vec3<f32>,  uv1y: f32,
#     n2: vec3<f32>,  uv2x: f32,
#     uv2y: f32, pad0: f32, pad1: f32, pad2: f32,
# };

INTERNAL_FLAG = np.uint32(0x80000000)


def build_bvh(v0, v1, v2, n0, n1, n2, uv0, uv1, uv2, mat_ids, max_leaf_tris=4, num_bins=8):
    """Build a binned-SAH BVH over N triangles and return GPU-ready (nodes_bytes, tris_bytes, stats)."""
    v0 = np.asarray(v0, dtype=np.float32)
    v1 = np.asarray(v1, dtype=np.float32)
    v2 = np.asarray(v2, dtype=np.float32)
    n0 = np.asarray(n0, dtype=np.float32)
    n1 = np.asarray(n1, dtype=np.float32)
    n2 = np.asarray(n2, dtype=np.float32)
    uv0 = np.asarray(uv0, dtype=np.float32)
    uv1 = np.asarray(uv1, dtype=np.float32)
    uv2 = np.asarray(uv2, dtype=np.float32)
    mat_ids = np.asarray(mat_ids, dtype=np.uint32)

    n_tris = len(v0)
    if n_tris == 0:
        raise ValueError('Cannot build BVH for empty triangle list')

    tri_min = np.minimum(np.minimum(v0, v1), v2)
    tri_max = np.maximum(np.maximum(v0, v1), v2)
    centroids = (v0 + v1 + v2) * np.float32(1.0 / 3.0)

    order = np.arange(n_tris, dtype=np.uint32)
    # Preallocate worst-case node array (2 * N - 1)
    max_nodes = max(2, 2 * n_tris)
    node_min = np.empty((max_nodes, 3), dtype=np.float32)
    node_max = np.empty((max_nodes, 3), dtype=np.float32)
    node_a = np.zeros(max_nodes, dtype=np.uint32)  # left_or_first
    node_b = np.zeros(max_nodes, dtype=np.uint32)  # count or (INTERNAL_FLAG | right)
    node_count = 1

    # Iterative stack: (node_idx, start, count)
    stack = [(0, 0, n_tris)]
    max_depth = 0
    depths = [0]

    while stack:
        nid, start, count = stack.pop()
        depth = depths.pop()
        if depth > max_depth:
            max_depth = depth

        idxs = order[start:start + count]
        bmin = tri_min[idxs].min(axis=0)
        bmax = tri_max[idxs].max(axis=0)
        node_min[nid] = bmin
        node_max[nid] = bmax

        if count <= max_leaf_tris:
            node_a[nid] = np.uint32(start)
            node_b[nid] = np.uint32(count)
            continue

        c_sub = centroids[idxs]
        cmin = c_sub.min(axis=0)
        cmax = c_sub.max(axis=0)
        cext = cmax - cmin

        best_cost = float('inf')
        best_axis = -1
        best_split = 0.0

        # Evaluate binned SAH across all 3 axes
        parent_ext = np.maximum(bmax - bmin, 1e-6)
        parent_area = float(parent_ext[0] * parent_ext[1] + parent_ext[1] * parent_ext[2] + parent_ext[2] * parent_ext[0])

        t_min_sub = tri_min[idxs]
        t_max_sub = tri_max[idxs]

        for axis in range(3):
            ext = float(cext[axis])
            if ext < 1e-5:
                continue
            scale = num_bins / ext
            bins = np.clip(((c_sub[:, axis] - cmin[axis]) * scale).astype(np.int32), 0, num_bins - 1)

            bin_counts = np.zeros(num_bins, dtype=np.int32)
            bin_min = np.full((num_bins, 3), np.inf, dtype=np.float32)
            bin_max = np.full((num_bins, 3), -np.inf, dtype=np.float32)

            for b in range(num_bins):
                mask = (bins == b)
                cnt = int(mask.sum())
                if cnt:
                    bin_counts[b] = cnt
                    bin_min[b] = t_min_sub[mask].min(axis=0)
                    bin_max[b] = t_max_sub[mask].max(axis=0)

            # Prefix (left) and suffix (right) sweeps
            left_count = np.cumsum(bin_counts[:-1])
            right_count = np.cumsum(bin_counts[:0:-1])[::-1]

            lmin = np.minimum.accumulate(bin_min[:-1], axis=0)
            lmax = np.maximum.accumulate(bin_max[:-1], axis=0)
            rmin = np.minimum.accumulate(bin_min[:0:-1], axis=0)[::-1]
            rmax = np.maximum.accumulate(bin_max[:0:-1], axis=0)[::-1]

            for s in range(num_bins - 1):
                lc, rc = int(left_count[s]), int(right_count[s])
                if lc == 0 or rc == 0:
                    continue
                le = np.maximum(lmax[s] - lmin[s], 0.0)
                re = np.maximum(rmax[s] - rmin[s], 0.0)
                la = float(le[0] * le[1] + le[1] * le[2] + le[2] * le[0])
                ra = float(re[0] * re[1] + re[1] * re[2] + re[2] * re[0])
                cost = 0.5 + (lc * la + rc * ra) / max(parent_area, 1e-6)
                if cost < best_cost:
                    best_cost = cost
                    best_axis = axis
                    best_split = float(cmin[axis]) + (s + 1) * (ext / num_bins)

        if best_axis < 0 or best_cost >= count:
            # Fallback: median split on longest centroid axis if leaf is still large
            if count <= max_leaf_tris * 2:
                node_a[nid] = np.uint32(start)
                node_b[nid] = np.uint32(count)
                continue
            best_axis = int(np.argmax(cext))
            mid = count // 2
            part = np.argpartition(c_sub[:, best_axis], mid)
            order[start:start + count] = idxs[part]
            left_cnt = mid
        else:
            left_mask = c_sub[:, best_axis] < best_split
            left_cnt = int(left_mask.sum())
            if left_cnt == 0 or left_cnt == count:
                left_cnt = count // 2
                part = np.argpartition(c_sub[:, best_axis], left_cnt)
                order[start:start + count] = idxs[part]
            else:
                order[start:start + count] = np.concatenate([idxs[left_mask], idxs[~left_mask]])

        left_id = node_count
        right_id = node_count + 1
        node_count += 2

        node_a[nid] = np.uint32(left_id)
        node_b[nid] = INTERNAL_FLAG | np.uint32(right_id)

        stack.append((right_id, start + left_cnt, count - left_cnt))
        depths.append(depth + 1)
        stack.append((left_id, start, left_cnt))
        depths.append(depth + 1)

    # Pack BVH nodes: 8 x 4 bytes = 32 bytes/node
    nodes_u32 = np.empty((node_count, 8), dtype=np.uint32)
    nodes_f32 = nodes_u32.view(np.float32)
    nodes_f32[:, 0:3] = node_min[:node_count]
    nodes_u32[:, 3] = node_a[:node_count]
    nodes_f32[:, 4:7] = node_max[:node_count]
    nodes_u32[:, 7] = node_b[:node_count]

    # Reorder triangles into BVH leaf order and pack into 28 x 4 bytes = 112 bytes/tri
    ov0 = v0[order]
    oe1 = v1[order] - ov0
    oe2 = v2[order] - ov0
    on0 = n0[order]
    on1 = n1[order]
    on2 = n2[order]
    ouv0 = uv0[order]
    ouv1 = uv1[order]
    ouv2 = uv2[order]
    omat = mat_ids[order]

    tris_u32 = np.zeros((n_tris, 28), dtype=np.uint32)
    tris_f32 = tris_u32.view(np.float32)
    tris_f32[:, 0:3] = ov0
    tris_u32[:, 3] = omat
    tris_f32[:, 4:7] = oe1
    tris_f32[:, 7] = ouv0[:, 0]
    tris_f32[:, 8:11] = oe2
    tris_f32[:, 11] = ouv0[:, 1]
    tris_f32[:, 12:15] = on0
    tris_f32[:, 15] = ouv1[:, 0]
    tris_f32[:, 16:19] = on1
    tris_f32[:, 19] = ouv1[:, 1]
    tris_f32[:, 20:23] = on2
    tris_f32[:, 23] = ouv2[:, 0]
    tris_f32[:, 24] = ouv2[:, 1]

    stats = dict(num_tris=n_tris, num_nodes=node_count, max_depth=max_depth)
    return np.ascontiguousarray(nodes_u32), np.ascontiguousarray(tris_u32), stats
