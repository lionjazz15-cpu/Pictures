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
    """World coords, triangles and topology of a linearly subdivided copy of the
    evaluated cage."""
    with mu.modifiers_disabled(cage, mu.POSE_DEFORM_TYPES):
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
        return P, mu.mesh_triangles(me), mu.mesh_topology(me)
    finally:
        if tmp is not None:
            bpy.data.objects.remove(tmp, do_unlink=True)
        bpy.data.meshes.remove(me0)
        if me is not None:
            bpy.data.meshes.remove(me)


def fill_holes(me, keep, iterations=4):
    """Fill faces that are (almost) surrounded by region faces and drop
    region faces that touch no other region face."""
    n = len(me.polygons)
    loop_edge = np.empty(len(me.loops), dtype=np.int64)
    me.loops.foreach_get("edge_index", loop_edge)
    tot = np.empty(n, dtype=np.int64)
    me.polygons.foreach_get("loop_total", tot)
    loop_face = mu._loop_faces(me)
    order = np.argsort(loop_edge, kind="stable")
    e_sorted, f_sorted = loop_edge[order], loop_face[order]
    pair = np.nonzero(e_sorted[1:] == e_sorted[:-1])[0]
    a, b = f_sorted[pair], f_sorted[pair + 1]
    keep = keep.copy()
    for _ in range(iterations):
        marked = np.bincount(a, weights=keep[b], minlength=n) + np.bincount(b, weights=keep[a], minlength=n)
        grow = (~keep) & (marked >= tot - 1) & (marked >= 2)
        if not grow.any():
            break
        keep |= grow
    marked = np.bincount(a, weights=keep[b], minlength=n) + np.bincount(b, weights=keep[a], minlength=n)
    keep &= marked > 0
    return keep


def region(context, body, cage, coverage=0.5, iterations=20, progress=None):
    """Boolean mask over the body's (cage) faces covered by ``cage``."""
    surf, tri_key, key_area = mu.body_face_surface(context, body)
    body_edge = mu.mean_edge_length_world(body.data, body.matrix_world)
    P0, tris, topo = densify(context, cage, body_edge / 2.0)
    offsets = np.zeros(len(P0))
    P = solver.solve(P0, topo, surf, offsets, iterations=iterations, relax=0.5, preserve=True,
                     anneal_start=solver.auto_anneal_start(surf, P0, offsets), progress=progress)
    A, B, C = P[tris[:, 0]], P[tris[:, 1]], P[tris[:, 2]]
    area = 0.5 * np.linalg.norm(np.cross(B - A, C - A), axis=1)
    hit = surf.nearest_index((A + B + C) / 3.0)
    ok = hit >= 0
    key = tri_key[hit[ok]]
    covered = np.bincount(key, weights=area[ok], minlength=len(key_area))
    frac = covered / np.maximum(key_area, 1e-30)
    n_faces = len(body.data.polygons)
    keep = frac.reshape(n_faces, 8).max(axis=1) >= coverage
    return fill_holes(body.data, keep)
