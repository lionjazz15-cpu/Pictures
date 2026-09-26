# SPDX-License-Identifier: GPL-3.0-or-later
"""Find the region of the body that a rough, low-poly cage covers.

A rough cage (a few quads blocked out around the chest, the hips, ...) cannot
be shrinkwrapped itself: with so few vertices its flat faces cut through every
curved area.  Instead the cage is only used to say *where* the garment goes:

1. the cage (with its own modifiers, e.g. Mirror) is subdivided linearly until
   its faces are about half the size of the body's faces,
2. that dense copy is wrapped onto the body with the precision solver (offset
   annealing makes it go down into the cleavage / crease on the right side),
3. every body face that is at least ``coverage`` covered by the wrapped copy
   belongs to the region; single-face holes are filled and specks removed.

The garment itself is then built from the body's own topology, so it matches
the body exactly (or sits at an exact, even offset).
"""

import math

import bpy
import numpy as np

from . import mesh_utils as mu
from . import solver

MAX_DENSE_FACES = 30_000


def densify(context, cage, target_edge):
    """World coords, triangles, topology and vertex normals of a linearly
    subdivided copy of the evaluated cage."""
    # a Shrinkwrap on the cage is part of its shape (e.g. the Slide on Body edit)
    with mu.modifiers_disabled(cage, mu.POSE_DEFORM_TYPES - {"SHRINKWRAP"}):
        dg = mu.evaluated_depsgraph(context)
        me0 = bpy.data.meshes.new_from_object(cage.evaluated_get(dg), depsgraph=dg)
    tmp = None
    me = None
    try:
        if len(me0.polygons) == 0:
            raise RuntimeError(f"'{cage.name}' has no faces")
        edge = mu.mean_edge_length_world(me0, cage.matrix_world)
        levels = int(np.clip(math.ceil(math.log2(max(edge / max(target_edge, 1e-9), 1.0))), 0, 7))
        while levels > 0 and len(me0.polygons) * 4 ** levels > MAX_DENSE_FACES:
            levels -= 1
        tmp = bpy.data.objects.new("_psw_dense", me0)
        context.scene.collection.objects.link(tmp)
        tmp.matrix_world = cage.matrix_world
        if levels:
            m = tmp.modifiers.new("Dense", "SUBSURF")
            m.subdivision_type = "SIMPLE"
            m.levels = m.render_levels = levels
        dg = mu.evaluated_depsgraph(context)
        me = bpy.data.meshes.new_from_object(tmp.evaluated_get(dg), depsgraph=dg)
        P = mu.to_world(mu.mesh_coords(me), cage.matrix_world)
        N = mu.normals_to_world(mu.mesh_vertex_normals(me), cage.matrix_world)
        return P, mu.mesh_triangles(me), mu.mesh_topology(me), N
    finally:
        if tmp is not None:
            bpy.data.objects.remove(tmp, do_unlink=True)
        bpy.data.meshes.remove(me0)
        if me is not None:
            bpy.data.meshes.remove(me)


def project_along_normals(surf, P, N):
    """Move every point onto the body along its normal (whichever direction
    hits first), like the Shrinkwrap modifier's Project mode; points whose
    rays miss go to the closest point.  A cage around the body keeps its
    heights this way: in front of the cleavage the closest point would be on
    the breasts, which pulls the band's top and bottom rows together."""
    ray = surf.bvh.ray_cast
    out = []
    for p, n in zip(P.tolist(), N.tolist()):
        best = None
        for d in (n, (-n[0], -n[1], -n[2])):
            loc, _, _, dist = ray(p, d)
            if loc is not None and (best is None or dist < best[1]):
                best = (loc, dist)
        out.append(best[0] if best is not None else (np.nan, np.nan, np.nan))
    Q = np.array(out, dtype=np.float64).reshape(-1, 3)
    miss = np.isnan(Q[:, 0])
    if miss.any():
        Q[miss] = surf.nearest(P[miss])[0]
    return Q


def _face_pairs(me):
    """Pairs of faces sharing an edge."""
    loop_edge = np.empty(len(me.loops), dtype=np.int64)
    me.loops.foreach_get("edge_index", loop_edge)
    loop_face = mu._loop_faces(me)
    order = np.argsort(loop_edge, kind="stable")
    e_sorted, f_sorted = loop_edge[order], loop_face[order]
    pair = np.nonzero(e_sorted[1:] == e_sorted[:-1])[0]
    return f_sorted[pair], f_sorted[pair + 1]


def fill_holes(me, keep, max_fraction=0.05):
    """Fill every patch of uncovered faces that is small compared to the region
    (holes, notches along a mirror seam) and drop covered faces that touch no
    other covered face."""
    n = len(me.polygons)
    a, b = _face_pairs(me)
    keep = keep.copy()
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    both = ~keep[a] & ~keep[b]
    for x, y in zip(a[both].tolist(), b[both].tolist()):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry
    labels = np.array([find(i) for i in range(n)], dtype=np.int64)
    uncovered = ~keep
    sizes = np.bincount(labels[uncovered], minlength=n)
    limit = max(4, int(max_fraction * keep.sum()))
    keep |= uncovered & (sizes[labels] <= limit)
    marked = np.bincount(a, weights=keep[b], minlength=n) + np.bincount(b, weights=keep[a], minlength=n)
    keep &= marked > 0
    return keep


def region(context, body, cage, coverage=0.5, iterations=20, progress=None):
    """Boolean mask over the body's (cage) faces covered by ``cage``.

    The cage is wrapped with a valley bridge, so it spans the cleavage /
    crease at its full height instead of being pulled in (which would leave a
    pinched strip in the middle).  A body face then counts as covered by the
    part of its area that either
    - the wrapped cage lies on (its faces project onto it), or
    - looks at the wrapped cage from below: a ray along the face normal hits
      it from the body side (the valley under a bridge).
    """
    surf, tri_key, key_area, tri_center, tri_normal, tri_area = mu.body_face_surface(context, body)
    body_edge = mu.mean_edge_length_world(body.data, body.matrix_world)
    P0, tris, topo, N0 = densify(context, cage, body_edge / 2.0)
    offsets = np.zeros(len(P0))
    bridge = max(4.0 * body_edge, 0.02 * surf.bbox_diag)
    P = solver.solve(P0, topo, surf, offsets, iterations=iterations, relax=0.5, preserve=True,
                     start=project_along_normals(surf, P0, N0), bridge=bridge, progress=progress)
    A, B, C = P[tris[:, 0]], P[tris[:, 1]], P[tris[:, 2]]
    area = 0.5 * np.linalg.norm(np.cross(B - A, C - A), axis=1)
    centers = (A + B + C) / 3.0
    hit = surf.nearest_index(centers)
    ok = hit >= 0
    covered = np.bincount(tri_key[hit[ok]], weights=area[ok], minlength=len(key_area))
    projected = covered / np.maximum(key_area, 1e-30) >= coverage

    ray_hit = _seen_from_inside(P, tris, surf, tri_center, tri_normal, body_edge)
    below = np.bincount(tri_key, weights=tri_area * ray_hit, minlength=len(key_area))
    seen = below / np.maximum(key_area, 1e-30) >= coverage

    n_faces = len(body.data.polygons)
    projected = projected.reshape(n_faces, 8).any(axis=1)
    seen = seen.reshape(n_faces, 8).any(axis=1)
    keep = projected | _connected(body.data, seen & ~projected, projected)
    return fill_holes(body.data, keep)


def _seen_from_inside(P, tris, surf, tri_center, tri_normal, body_edge):
    """For each body triangle: does a ray along its normal hit the wrapped cage
    (P, tris) from the inside, i.e. from the body side?"""
    _, _, sd = surf.signed(P)
    reach = 1.5 * float(np.percentile(np.abs(sd), 99)) + 2.0 * body_edge
    start = 0.5 * body_edge
    lo, hi = P.min(axis=0) - reach, P.max(axis=0) + reach
    cand = np.nonzero(np.all((tri_center >= lo) & (tri_center <= hi), axis=1))[0]
    result = np.zeros(len(tri_center), dtype=bool)
    if len(cand) == 0:
        return result
    bvh = solver.BVHTree.FromPolygons(P.tolist(), tris.tolist(), all_triangles=True)
    origins = tri_center[cand] - tri_normal[cand] * start
    hits = []
    for o, d in zip(origins.tolist(), tri_normal[cand].tolist()):
        loc = bvh.ray_cast(o, d, reach + start)[0]
        hits.append(loc if loc is not None else (np.nan, np.nan, np.nan))
    H = np.array(hits, dtype=np.float64).reshape(-1, 3)
    got = ~np.isnan(H[:, 0])
    if got.any():
        # accept only hits on the cage's inner side: seen from the hit point
        # the body lies behind the ray (rejects e.g. an arm facing the torso)
        _, dirs, _ = surf.signed(H[got])
        inner = np.einsum("ij,ij->i", dirs, tri_normal[cand][got]) > 0.0
        idx = cand[got]
        result[idx[inner]] = True
    return result


def _connected(me, candidates, seeds):
    """Candidate faces connected to the seed faces through candidate faces."""
    if not candidates.any():
        return candidates
    n = len(me.polygons)
    a, b = _face_pairs(me)
    reached = seeds.copy()
    out = np.zeros(n, dtype=bool)
    while True:
        grow = np.zeros(n, dtype=bool)
        grow[b[reached[a]]] = True
        grow[a[reached[b]]] = True
        new = grow & candidates & ~out
        if not new.any():
            return out
        out |= new
        reached |= new
