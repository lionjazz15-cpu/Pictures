# SPDX-License-Identifier: GPL-3.0-or-later
"""High level fitting pipeline shared by the operators."""

import bpy
import numpy as np

from . import mesh_utils as mu
from . import solver


class Progress:
    def __init__(self, context):
        self.wm = context.window_manager if context.window else None
        if self.wm:
            self.wm.progress_begin(0, 1000)

    def __call__(self, f):
        if self.wm:
            self.wm.progress_update(int(max(0.0, min(1.0, f)) * 1000))

    def end(self):
        if self.wm:
            self.wm.progress_end()


def _solve_kwargs(settings, surface, P0, offsets, start_mode):
    kw = dict(
        iterations=settings.iterations,
        relax=settings.relax,
        preserve=settings.distribution == "PRESERVE",
        tension=settings.tension,
    )
    if start_mode == "NEAREST" and settings.use_anneal:
        a = settings.anneal_start
        kw["anneal_start"] = a if a > 0 else solver.auto_anneal_start(surface, P0, offsets)
    return kw


def _mirror_constraint(obj, cage0, mw):
    """Keeps vertices on the object's mirror planes (world <-> local)."""
    locks = mu.mirror_locks(obj, cage0)
    if not locks:
        return None

    def constrain(P):
        L = mu.to_local(P, mw)
        for idx, ax in locks:
            L[idx, ax] = cage0[idx, ax]
        return mu.to_world(L, mw)
    return constrain


def fit_object(context, obj, surface, settings, start_mode="NEAREST", progress=None):
    """Fit ``obj`` onto ``surface``.  Returns (new cage coords in local space,
    cage fit residual or 0.0).

    start_mode:
        NEAREST - garment of arbitrary shape: start from its current position
                  and anneal the offset down.
        NORMAL  - mesh that lies on the target (topology transfer): start from
                  vertex normal offset, no annealing.
    """
    mw = obj.matrix_world.copy()
    cage0 = mu.mesh_coords(obj.data)
    infl_cage = mu.vertex_group_weights(obj, obj.data, settings.vertex_group)

    if settings.subdiv_aware and mu.has_subdivision(obj):
        ev = mu.SubdivEvaluator(context, obj)
        try:
            S0, me_eval = ev.evaluate(keep_mesh=True)
            try:
                topo = mu.mesh_topology(me_eval)
                offsets = settings.offset * mu.vertex_group_weights(obj, me_eval, settings.offset_group)
                infl = mu.vertex_group_weights(obj, me_eval, settings.vertex_group)
                normals = mu.normals_to_world(mu.mesh_vertex_normals(me_eval), mw)
            finally:
                bpy.data.meshes.remove(me_eval)
            P0 = mu.to_world(S0, mw)
            start = P0 + normals * offsets[:, None] if start_mode == "NORMAL" else None
            P = solver.solve(
                P0, topo, surface, offsets, pinned=infl <= 0.0, start=start,
                progress=(lambda f: progress(0.8 * f)) if progress else None,
                **_solve_kwargs(settings, surface, P0, offsets, start_mode))
            C, err = fit_subdiv_cage(ev, mu.to_local(P, mw), mw, surface, offsets,
                                     settings.cage_iterations)
            if progress:
                progress(1.0)
        finally:
            ev.free()
    else:
        me = obj.data
        topo = mu.mesh_topology(me)
        offsets = settings.offset * mu.vertex_group_weights(obj, me, settings.offset_group)
        P0 = mu.to_world(cage0, mw)
        start = None
        if start_mode == "NORMAL":
            start = P0 + mu.normals_to_world(mu.mesh_vertex_normals(me), mw) * offsets[:, None]
        P = solver.solve(
            P0, topo, surface, offsets, pinned=infl_cage <= 0.0, start=start, progress=progress,
            constrain=_mirror_constraint(obj, cage0, mw),
            **_solve_kwargs(settings, surface, P0, offsets, start_mode))
        C, err = mu.to_local(P, mw), 0.0

    new = cage0 + np.clip(infl_cage, 0.0, 1.0)[:, None] * (C - cage0)
    return new, err


def fit_subdiv_cage(ev, T_local, mw, surface, offsets, iterations):
    """Solve the cage of a subdivided mesh so that its subdivided vertices land
    on the targets ``T_local``.

    1. Corner interpolation (progressive iterative approximation): exact at the
       vertices that correspond to cage vertices.
    2. If the subdivision matrix can be recovered, a least-squares fit over
       *all* subdivided vertices, then a few rounds that raise the targets of
       vertices which ended up closer than the offset (a coarse cage cannot
       follow a narrow crease; this keeps it from cutting into the body).
    Returns (cage coords, max deviation of the subdivided result).
    """
    C, _ = mu.fit_cage(ev, T_local, iterations=iterations)
    op = ev.linear_operator()
    if op is None:
        S = ev.evaluate(C)
        return C, float(np.abs(S - T_local).max()) if len(S) else 0.0

    W = op.row_ok.astype(np.float64)
    T = T_local.copy()
    Minv3 = np.linalg.inv(np.array(mw, dtype=np.float64)[:3, :3])
    scale = float(np.linalg.norm(T_local.max(0) - T_local.min(0))) or 1.0
    tol = 1e-5 * scale
    for _ in range(max(10, iterations)):
        C = ev.constrain(op.solve_least_squares(T, W, C))
        S = op.matvec(C)
        _, dirs, sd = surface.signed(mu.to_world(S, mw))
        deficit = offsets - sd
        bad = (deficit > tol) & op.row_ok
        if not bad.any():
            break
        # raise the target a bit beyond the offset and make it count more
        T[bad] += (dirs[bad] @ Minv3.T) * (1.2 * deficit[bad])[:, None]
        W[bad] *= 2.0
    S = ev.evaluate(C)
    return C, float(np.abs(S - T_local).max()) if len(S) else 0.0


def smooth_boundary(obj, iterations, surface=None, strength=0.5):
    """Smooth the open border of ``obj`` along the surface.

    A region picked automatically (rough cage, proximity) follows the body's
    faces, so its border is a staircase.  The border vertices are relaxed along
    the border (1D, tangential to the mesh) and, when ``surface`` is given,
    projected back onto it; the ring next to the border follows gently so no
    fold appears.  Mirror seams are not borders and stay on their plane.
    """
    me = obj.data
    if iterations <= 0 or len(me.vertices) == 0:
        return
    mw = obj.matrix_world
    cage0 = mu.mesh_coords(me)
    P = mu.to_world(cage0, mw)
    N = mu.normals_to_world(mu.mesh_vertex_normals(me), mw)
    n = len(P)
    edges = np.empty(len(me.edges) * 2, dtype=np.int64)
    me.edges.foreach_get("vertices", edges)
    edges = edges.reshape(-1, 2)
    loop_edges = np.empty(len(me.loops), dtype=np.int64)
    me.loops.foreach_get("edge_index", loop_edges)
    border = np.bincount(loop_edges, minlength=len(edges)) == 1
    locks = mu.mirror_locks(obj, cage0)
    on_seam = np.zeros(n, dtype=bool)
    for idx, _ in locks:
        on_seam[idx] = True
    border &= ~(on_seam[edges[:, 0]] & on_seam[edges[:, 1]])
    be = edges[border]
    if len(be) == 0:
        return
    bv = np.zeros(n, dtype=bool)
    bv[be.ravel()] = True
    b_src = np.concatenate([be[:, 0], be[:, 1]])
    b_dst = np.concatenate([be[:, 1], be[:, 0]])
    b_deg = np.bincount(b_src, minlength=n).astype(np.float64)
    # the ring next to the border
    touch = bv[edges[:, 0]] ^ bv[edges[:, 1]]
    ring = np.zeros(n, dtype=bool)
    ring[np.where(bv[edges[touch, 0]], edges[touch, 1], edges[touch, 0])] = True
    a_src = np.concatenate([edges[:, 0], edges[:, 1]])
    a_dst = np.concatenate([edges[:, 1], edges[:, 0]])
    a_deg = np.bincount(a_src, minlength=n).astype(np.float64)
    moved = np.nonzero(bv | ring)[0]

    def mean(src, dst, deg):
        S = np.stack([np.bincount(src, weights=P[dst, k], minlength=n) for k in range(3)], axis=1)
        return S / np.maximum(deg, 1.0)[:, None]

    for _ in range(int(iterations)):
        U = np.zeros_like(P)
        U[bv] = strength * (mean(b_src, b_dst, b_deg)[bv] - P[bv])
        U[ring] = 0.5 * strength * (mean(a_src, a_dst, a_deg)[ring] - P[ring])
        U -= np.einsum("ij,ij->i", U, N)[:, None] * N
        P += U
        if surface is not None:
            P, _ = surface.project(P, np.zeros(n), moved, max_iter=1)
        if locks:
            L = mu.to_local(P, mw)
            for idx, ax in locks:
                L[idx, ax] = cage0[idx, ax]
            P = mu.to_world(L, mw)
    mu.write_result(obj, mu.to_local(P, mw), "APPLY")


def offset_report(context, obj, surface, offset):
    """Distance statistics of the (evaluated, rest pose) object to the surface."""
    with mu.modifiers_disabled(obj, mu.POSE_DEFORM_TYPES):
        dg = mu.evaluated_depsgraph(context)
        ev = obj.evaluated_get(dg)
        me = ev.to_mesh()
        try:
            P = mu.to_world(mu.mesh_coords(me), obj.matrix_world)
        finally:
            ev.to_mesh_clear()
    _, _, sd = surface.signed(P)
    dev = sd - offset
    return {
        "count": len(sd),
        "min": float(sd.min()) if len(sd) else 0.0,
        "max": float(sd.max()) if len(sd) else 0.0,
        "mean": float(sd.mean()) if len(sd) else 0.0,
        "max_dev": float(np.abs(dev).max()) if len(sd) else 0.0,
        "inside": int((sd < 0).sum()),
    }
