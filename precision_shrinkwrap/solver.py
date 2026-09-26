# SPDX-License-Identifier: GPL-3.0-or-later
"""Core numerical solver (numpy + mathutils only, no bpy dependency).

The solver moves the vertices of a mesh onto the *offset surface* of a target
(the set of points whose distance to the target equals ``offset``) while keeping
the vertex distribution even.  Three ideas make it behave in valleys such as the
buttock crease or the cleavage, where the stock Shrinkwrap modifier stretches:

1. Offset-surface projection instead of "nearest point + normal * offset".
   Offsetting along normals makes the two sides of a narrow valley cross each
   other; projecting onto the distance level set instead fills the valley
   cleanly, exactly like real fabric of a given thickness would.
2. Tangential relaxation between projections.  Vertices that the nearest-point
   query would pile up in the crease are slid back along the surface so the
   original (or an even) distribution is kept.
3. Offset annealing.  The garment is first wrapped onto a large offset (on which
   valleys are filled and smooth) and the offset is shrunk step by step, so each
   vertex descends into the valley on the correct side.

An optional "valley tension" pass then lets the fabric bridge valleys: the mesh
is smoothed and only pushed *out* of the offset surface, never pulled in.

``closing_targets`` / the ``bridge`` option compute the closing of the offset
surface by a ball (valleys narrower than about twice the radius are spanned).
It is used to wrap rough cages for region detection, where only the coverage
matters.
"""

import numpy as np
from mathutils.bvhtree import BVHTree

_EPS = 1e-12


class Surface:
    """Signed-distance style queries against a triangulated target surface."""

    def __init__(self, verts, tris):
        verts = np.asarray(verts, dtype=np.float64)
        tris = np.asarray(tris, dtype=np.int64)
        if len(tris) == 0:
            raise ValueError("Target has no faces")
        self.bvh = BVHTree.FromPolygons(verts.tolist(), tris.tolist(), all_triangles=True)
        self.bbox_diag = float(np.linalg.norm(verts.max(0) - verts.min(0)))

    def nearest(self, P):
        """Return (closest point, face normal) for every point in P."""
        fn = self.bvh.find_nearest
        res = [fn(p) for p in P.tolist()]
        Q = np.array([r[0] if r[0] is not None else p for r, p in zip(res, P.tolist())], dtype=np.float64)
        F = np.array([r[1] if r[1] is not None else (0.0, 0.0, 1.0) for r in res], dtype=np.float64)
        return Q.reshape(-1, 3), F.reshape(-1, 3)

    def nearest_index(self, P):
        """Index of the closest triangle for every point in P (-1 if none)."""
        fn = self.bvh.find_nearest
        return np.array([(r[2] if r[2] is not None else -1) for r in (fn(p) for p in P.tolist())],
                        dtype=np.int64)

    def signed(self, P):
        """Return (closest point, outward direction, signed distance).

        The outward direction is the gradient of the distance field (unit vector
        from the closest point towards the query, flipped when inside), which is
        also the normal of the offset surface through P.
        """
        Q, F = self.nearest(P)
        V = P - Q
        dist = np.linalg.norm(V, axis=1)
        side = np.einsum("ij,ij->i", V, F)
        s = np.where(side < 0.0, -1.0, 1.0)
        tiny = dist < 1e-9 * max(self.bbox_diag, 1.0)
        dirs = np.where(tiny[:, None], F, V / np.maximum(dist, _EPS)[:, None] * s[:, None])
        return Q, dirs, dist * s

    def project(self, P, offset, idx, mode="eq", max_iter=1, tol=0.0):
        """Project points P[idx] onto the offset surface.

        mode "eq":  signed distance == offset  (follow the surface)
        mode "min": signed distance >= offset  (only push out)

        Returns the new points and the offset-surface normals (N, 3); rows that
        are not in ``idx`` get a zero normal.
        """
        P = P.copy()
        N = np.zeros_like(P)
        active = np.asarray(idx)
        for _ in range(max_iter):
            if len(active) == 0:
                break
            Q, dirs, sd = self.signed(P[active])
            N[active] = dirs
            d = offset[active]
            if mode == "min":
                need = sd < d - tol
            else:
                need = np.abs(sd - d) > tol
            if not need.any():
                break
            sel = active[need]
            P[sel] = Q[need] + dirs[need] * d[need][:, None]
            active = sel
        return P, N


class Topology:
    """Uniform graph Laplacian; boundary vertices only see boundary neighbours so
    open edges (leg holes, waist band, neckline) slide along themselves instead
    of shrinking inwards."""

    def __init__(self, n_verts, edges, edge_face_count, tris=None):
        edges = np.asarray(edges, dtype=np.int64).reshape(-1, 2)
        count = np.asarray(edge_face_count, dtype=np.int64)
        boundary_edge = count != 2
        vb = np.zeros(n_verts, dtype=bool)
        vb[edges[boundary_edge].ravel()] = True
        src = np.concatenate([edges[:, 0], edges[:, 1]])
        dst = np.concatenate([edges[:, 1], edges[:, 0]])
        keep = (~vb[src]) | np.concatenate([boundary_edge, boundary_edge])
        self.n = n_verts
        self.src = src[keep]
        self.dst = dst[keep]
        self.deg = np.bincount(self.src, minlength=n_verts).astype(np.float64)
        self.boundary = vb
        self.tris = None if tris is None else np.asarray(tris, dtype=np.int64).reshape(-1, 3)

    def laplacian(self, P):
        n = self.n
        S = np.empty((n, 3))
        for k in range(3):
            S[:, k] = np.bincount(self.src, weights=P[self.dst, k], minlength=n)
        has = self.deg > 0
        L = np.zeros_like(P)
        L[has] = S[has] / self.deg[has][:, None] - P[has]
        return L

    def mean_edge_length(self, P):
        if len(self.src) == 0:
            return 0.0
        return float(np.linalg.norm(P[self.src] - P[self.dst], axis=1).mean())


def _tangential(U, N):
    return U - np.einsum("ij,ij->i", U, N)[:, None] * N


def closing_targets(P, surface, offset, radius, tris):
    """Project P onto the closing of the offset surface by a ball of ``radius``.

    The mesh (P, tris) is first put onto the level set offset + radius (L),
    where valleys narrower than 2 * (offset + radius) are filled.  A point then
    goes to distance ``radius`` below its closest point on L: straight under L
    (exactly at ``offset`` from the target) or, under a crease of L, onto the
    arc of that radius.  Returns (targets, outward normals).
    """
    n = len(P)
    Y, _ = surface.project(P, offset + radius, np.arange(n), max_iter=8)
    bvh = BVHTree.FromPolygons(Y.tolist(), tris.tolist(), all_triangles=True)
    fn = bvh.find_nearest
    res = [fn(p) for p in P.tolist()]
    foot = np.array([r[0] if r[0] is not None else y for r, y in zip(res, Y.tolist())],
                    dtype=np.float64).reshape(-1, 3)
    _, nrm, _ = surface.signed(foot)
    V = P - foot
    dist = np.linalg.norm(V, axis=1)
    inward = (np.einsum("ij,ij->i", V, nrm) < 0.0) & (dist > 1e-9 * max(surface.bbox_diag, 1.0))
    u = np.where(inward[:, None], V / np.maximum(dist, _EPS)[:, None], -nrm)
    r = np.broadcast_to(np.asarray(radius, dtype=np.float64), (n,))
    return foot + u * r[:, None], -u


def auto_anneal_start(surface, P, offset):
    """Starting offset for annealing: far enough out that valleys are filled,
    but never absurdly large compared to the target."""
    _, _, sd = surface.signed(P)
    far = float(np.percentile(np.abs(sd), 90)) if len(sd) else 0.0
    base = max(float(offset.max()) * 3.0, far)
    return min(base, 0.05 * surface.bbox_diag) if surface.bbox_diag > 0 else base


def solve(P0, topo, surface, offset, *, iterations=30, relax=0.5, preserve=True,
          tension=0, bridge=0.0, anneal_start=0.0, pinned=None, start=None, progress=None,
          substeps=4, constrain=None, bridge_rounds=10):
    """Fit points P0 (world space, (N, 3)) onto the offset surface.

    offset:        (N,) per-vertex offset distance
    iterations:    number of project/relax rounds
    relax:         tangential relaxation strength per round (0..1)
    preserve:      keep the original edge flow (True) or make it even (False)
    tension:       number of valley-bridging smoothing rounds after the fit
    bridge:        radius of a closing that spans valleys (needs topo.tris).
                   Coarse: the spanned rows can fold, so it is only meant for
                   region detection, not for final garments
    anneal_start:  offset to start the annealing from (<= offset: no annealing)
    pinned:        (N,) bool, vertices that must not move
    start:         optional initial positions (defaults to P0)
    substeps:      relaxation sub-steps per projection
    constrain:     optional callable(P) -> P applied after every update (e.g. to
                   keep vertices on a mirror plane)
    progress:      optional callable(fraction)
    """
    P0 = np.asarray(P0, dtype=np.float64)
    n = len(P0)
    offset = np.broadcast_to(np.asarray(offset, dtype=np.float64), (n,)).copy()
    pinned = np.zeros(n, dtype=bool) if pinned is None else np.asarray(pinned, dtype=bool)
    free = np.nonzero(~pinned)[0]
    P = (P0 if start is None else np.asarray(start, dtype=np.float64)).copy()
    if len(free) == 0:
        return P
    fix = constrain if constrain is not None else (lambda X: X)

    L0 = topo.laplacian(P0) if preserve else np.zeros_like(P0)
    scale = max(topo.mean_edge_length(P0), surface.bbox_diag * 1e-4, 1e-9)
    tol = 1e-4 * scale

    iterations = max(int(iterations), 1)
    bridging = bridge > 0.0 and topo.tris is not None and len(topo.tris)
    rounds = int(bridge_rounds) if bridging else 0
    tension = max(int(tension), 0)
    total = iterations + rounds + tension + 1
    extra = np.maximum(float(anneal_start) - offset, 0.0)

    N = np.zeros_like(P)
    for k in range(iterations):
        t = k / (iterations - 1) if iterations > 1 else 1.0
        d_k = offset + extra * (1.0 - t) ** 2
        P, N = surface.project(P, d_k, free, max_iter=2)
        # several cheap relaxation sub-steps per (expensive) projection so the
        # redistribution travels far enough across dense meshes
        for _ in range(substeps):
            U = relax * _tangential(topo.laplacian(P) - L0, N)
            U[pinned] = 0.0
            P += U
        P = fix(P)
        if progress:
            progress((k + 1) / total)

    P, N = surface.project(P, offset, free, max_iter=12, tol=tol)
    P = fix(P)
    if progress:
        progress((iterations + 1) / total)

    if rounds:
        # Valley bridge: alternate projection onto the closing surface with
        # tangential relaxation on it, so the vertices lifted out of a valley
        # spread over the span instead of bunching up.
        # The rows lifted out of the valley have to share a shorter span, so
        # they are spread evenly here (keeping the original spacing would
        # fold them over each other).
        for k in range(rounds):
            T, NS = closing_targets(P, surface, offset, bridge, topo.tris)
            P[free] = T[free]
            for _ in range(substeps * 2):
                U = relax * _tangential(topo.laplacian(P), NS)
                U[pinned] = 0.0
                P += U
            P = fix(P)
            if progress:
                progress((iterations + 2 + k) / total)
        T, _ = closing_targets(P, surface, offset, bridge, topo.tris)
        P[free] = T[free]
        P, N = surface.project(P, offset, free, mode="min", max_iter=6, tol=tol)
        P = fix(P)

    # Valley tension: smooth freely (this pulls fabric out of concave areas and
    # pushes it into convex ones) then only push out; convex areas return to
    # the offset surface, concave areas stay bridged.
    for k in range(tension):
        L0t = _tangential(L0, N) if preserve else 0.0
        # smoothing is cheap and has to travel across the whole valley, so
        # use many more sub-steps per projection than the fit itself
        for _ in range(substeps * 4):
            U = relax * (topo.laplacian(P) - L0t)
            U[pinned] = 0.0
            P += U
        P, N_new = surface.project(P, offset, free, mode="min", max_iter=6, tol=tol)
        P = fix(P)
        N[free] = N_new[free]
        if progress:
            progress((iterations + rounds + 2 + k) / total)
    return P
