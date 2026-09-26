# SPDX-License-Identifier: GPL-3.0-or-later
"""bpy helpers: reading meshes into numpy, evaluating modifier stacks, and the
subdivision-aware cage fitting."""

from contextlib import contextmanager

import bpy
import numpy as np

from . import solver

# Modifiers that make a fit meaningless when evaluated (they move the cage
# depending on pose / other objects).  They are switched off while fitting, so
# everything is done in rest pose.
POSE_DEFORM_TYPES = {
    "ARMATURE", "SHRINKWRAP", "SURFACE_DEFORM", "MESH_DEFORM", "LATTICE",
    "CLOTH", "SOFT_BODY", "HOOK", "CORRECTIVE_SMOOTH", "LAPLACIANDEFORM",
}

# Modifiers kept when evaluating a garment's subdivision: they must be linear in
# the cage coordinates for the cage fit to converge.
LINEAR_TOPOLOGY_TYPES = {"MIRROR", "SUBSURF"}

FACE_ATTR = "_psw_coarse_face"


# ---------------------------------------------------------------------------
# numpy <-> mesh


def mesh_coords(me):
    co = np.empty(len(me.vertices) * 3, dtype=np.float64)
    me.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)


def set_mesh_coords(me, P):
    me.vertices.foreach_set("co", np.asarray(P, dtype=np.float64).ravel())
    me.update()


def mesh_vertex_normals(me):
    no = np.empty(len(me.vertices) * 3, dtype=np.float64)
    if hasattr(me, "vertex_normals"):
        me.vertex_normals.foreach_get("vector", no)
    else:  # pragma: no cover - older Blender
        me.vertices.foreach_get("normal", no)
    return no.reshape(-1, 3)


def mesh_triangles(me):
    me.calc_loop_triangles()
    tris = np.empty(len(me.loop_triangles) * 3, dtype=np.int64)
    me.loop_triangles.foreach_get("vertices", tris)
    return tris.reshape(-1, 3)


def mesh_topology(me):
    n = len(me.vertices)
    edges = np.empty(len(me.edges) * 2, dtype=np.int64)
    me.edges.foreach_get("vertices", edges)
    loop_edges = np.empty(len(me.loops), dtype=np.int64)
    me.loops.foreach_get("edge_index", loop_edges)
    count = np.bincount(loop_edges, minlength=len(me.edges))
    return solver.Topology(n, edges.reshape(-1, 2), count, mesh_triangles(me))


def to_world(P, mat):
    M = np.array(mat, dtype=np.float64)
    return P @ M[:3, :3].T + M[:3, 3]


def to_local(P, mat):
    M = np.linalg.inv(np.array(mat, dtype=np.float64))
    return P @ M[:3, :3].T + M[:3, 3]


def normals_to_world(Nl, mat):
    M = np.array(mat, dtype=np.float64)[:3, :3]
    Nw = Nl @ np.linalg.inv(M)  # (M^-1)^T applied to row vectors
    ln = np.linalg.norm(Nw, axis=1)
    return Nw / np.maximum(ln, 1e-12)[:, None]


def vertex_group_weights(obj, me, name, default=1.0):
    """Weights of vertex group ``name`` on mesh ``me`` (which may be an evaluated
    copy of obj's mesh), or ``default`` everywhere when there is no group."""
    n = len(me.vertices)
    vg = obj.vertex_groups.get(name) if name else None
    if vg is None:
        return np.full(n, default, dtype=np.float64)
    gi = vg.index
    w = np.zeros(n, dtype=np.float64)
    for v in me.vertices:
        for g in v.groups:
            if g.group == gi:
                w[v.index] = g.weight
                break
    return w


# ---------------------------------------------------------------------------
# evaluation


@contextmanager
def modifiers_disabled(obj, types):
    """Temporarily hide viewport modifiers of the given types."""
    changed = []
    try:
        for m in obj.modifiers:
            if m.type in types and m.show_viewport:
                m.show_viewport = False
                changed.append(m)
        yield
    finally:
        for m in changed:
            m.show_viewport = True


def evaluated_depsgraph(context):
    context.view_layer.update()
    return context.evaluated_depsgraph_get()


def target_surface(context, obj):
    """BVH surface of ``obj`` in world space, fully evaluated (subdivision
    included) but in rest pose."""
    with modifiers_disabled(obj, POSE_DEFORM_TYPES):
        dg = evaluated_depsgraph(context)
        ev = obj.evaluated_get(dg)
        me = ev.to_mesh()
        try:
            P = to_world(mesh_coords(me), obj.matrix_world)
            tris = mesh_triangles(me)
        finally:
            ev.to_mesh_clear()
    return solver.Surface(P, tris)


FACE_ID_ATTR = "_psw_body_face"


def body_face_surface(context, body):
    """The evaluated (rest pose) body as a Surface, plus per triangle: a key
    ``face * 8 + mirror copy`` (which body cage face it comes from and which
    mirror copy it is on), its world centre, unit normal and area; and the
    evaluated area of every key."""
    me = body.data
    n_faces = len(me.polygons)
    a = me.attributes.new(FACE_ID_ATTR, "INT", "FACE")
    a.data.foreach_set("value", np.arange(n_faces, dtype=np.int32))
    try:
        with modifiers_disabled(body, POSE_DEFORM_TYPES):
            dg = evaluated_depsgraph(context)
            ev = body.evaluated_get(dg)
            em = ev.to_mesh()
            try:
                local = mesh_coords(em)
                tris = mesh_triangles(em)
                tri_poly = np.empty(len(em.loop_triangles), dtype=np.int64)
                em.loop_triangles.foreach_get("polygon_index", tri_poly)
                ea = em.attributes.get(FACE_ID_ATTR)
                if ea is not None:
                    fid = np.empty(len(em.polygons), dtype=np.int32)
                    ea.data.foreach_get("value", fid)
                elif len(em.polygons) == n_faces:
                    fid = np.arange(n_faces, dtype=np.int32)
                else:
                    raise RuntimeError(f"'{body.name}': a modifier drops face data; "
                                       "cannot map the evaluated body to its faces")
            finally:
                ev.to_mesh_clear()
    finally:
        me.attributes.remove(me.attributes[FACE_ID_ATTR])
    tri_face = fid[tri_poly].astype(np.int64)
    # mirror copy id: which side of each mirror plane the triangle is on
    center = local[tris].mean(axis=1)
    tri_side = np.zeros(len(tris), dtype=np.int64)
    axes = sorted({ax for m in body.modifiers if m.type == "MIRROR" and m.show_viewport
                   and m.mirror_object is None for ax in range(3) if m.use_axis[ax]})
    if axes:
        cage = mesh_coords(me)
        for k, ax in enumerate(axes):
            side = 1.0 if cage[:, ax].mean() >= 0 else -1.0
            tri_side |= (center[:, ax] * side < 0).astype(np.int64) << k
    P = to_world(local, body.matrix_world)
    cross = np.cross(P[tris[:, 1]] - P[tris[:, 0]], P[tris[:, 2]] - P[tris[:, 0]])
    length = np.linalg.norm(cross, axis=1)
    area = 0.5 * length
    normal = cross / np.maximum(length, 1e-30)[:, None]
    key = tri_face * 8 + tri_side
    key_area = np.bincount(key, weights=area, minlength=n_faces * 8)
    return solver.Surface(P, tris), key, key_area, P[tris].mean(axis=1), normal, area


def mirror_locks(obj, cage):
    """(vertex indices, axis) of cage vertices lying on the plane of one of
    obj's Mirror modifiers; they have to stay on it."""
    locks = []
    for m in obj.modifiers:
        if m.type != "MIRROR" or not m.show_viewport or m.mirror_object is not None:
            continue
        thr = max(m.merge_threshold, 1e-6) if (m.use_clip or m.use_mirror_merge) else 1e-6
        for ax in range(3):
            if m.use_axis[ax]:
                idx = np.nonzero(np.abs(cage[:, ax]) <= thr)[0]
                if len(idx):
                    locks.append((idx, ax))
    return locks


def mean_edge_length_world(me, mat):
    e = np.empty(len(me.edges) * 2, dtype=np.int64)
    me.edges.foreach_get("vertices", e)
    if len(e) == 0:
        return 0.0
    P = to_world(mesh_coords(me), mat)
    e = e.reshape(-1, 2)
    return float(np.linalg.norm(P[e[:, 0]] - P[e[:, 1]], axis=1).mean())


def copy_modifier_settings(src, dst):
    for prop in src.bl_rna.properties:
        pid = prop.identifier
        if prop.is_readonly or pid in {"name", "type", "rna_type"}:
            continue
        try:
            setattr(dst, pid, getattr(src, pid))
        except (AttributeError, TypeError, ValueError):
            pass


def has_subdivision(obj):
    return any(m.type == "SUBSURF" and m.show_viewport for m in obj.modifiers)


class SubdivEvaluator:
    """Evaluates ``subdivide(cage)`` for an object with a Subdivision modifier.

    Works on a temporary copy that only keeps the linear topology modifiers up
    to the last Subdivision modifier (no armature, no solidify after it, no
    shape keys), so that the evaluation is a fixed linear map of the cage.
    In Blender's subdivision output the first ``n_cage`` vertices correspond to
    the cage vertices, which is what the cage fitting relies on.
    """

    def __init__(self, context, obj):
        self.context = context
        self.obj = obj
        self.mesh = obj.data.copy()
        self.tmp = obj.copy()
        self.tmp.data = self.mesh
        self.tmp.animation_data_clear()
        context.scene.collection.objects.link(self.tmp)
        if self.mesh.shape_keys:
            self.tmp.shape_key_clear()
        last = max((i for i, m in enumerate(self.tmp.modifiers) if m.type == "SUBSURF"), default=-1)
        for i, m in enumerate(self.tmp.modifiers):
            m.show_viewport = i <= last and m.type in LINEAR_TOPOLOGY_TYPES
        self.n_cage = len(self.mesh.vertices)
        self.cage = mesh_coords(self.mesh)
        # Vertices on a mirror plane must stay on it, or the merge result - and
        # so the topology - changes between evaluations.
        self.locks = mirror_locks(self.tmp, self.cage)
        self.n_eval = None
        fa = self.mesh.attributes.new(FACE_ATTR, "INT", "FACE")
        fa.data.foreach_set("value", np.arange(len(self.mesh.polygons), dtype=np.int32))

    def constrain(self, C):
        for idx, ax in self.locks:
            C[idx, ax] = self.cage[idx, ax]
        return C

    def evaluate(self, cage=None, keep_mesh=False):
        """Returns subdivided local coords; with keep_mesh also a standalone
        copy of the evaluated mesh (caller must remove it)."""
        if cage is not None:
            set_mesh_coords(self.mesh, cage)
        dg = evaluated_depsgraph(self.context)
        ev = self.tmp.evaluated_get(dg)
        if keep_mesh:
            me = bpy.data.meshes.new_from_object(ev, preserve_all_data_layers=True, depsgraph=dg)
            P = mesh_coords(me)
        else:
            me = ev.to_mesh()
            try:
                P = mesh_coords(me)
            finally:
                ev.to_mesh_clear()
        if self.n_eval is None:
            self.n_eval = len(P)
        elif len(P) != self.n_eval:
            if keep_mesh:
                bpy.data.meshes.remove(me)
            raise RuntimeError(
                f"'{self.obj.name}': the modifier result changed topology while fitting "
                "(check Mirror merge / Weld modifiers)")
        return (P, me) if keep_mesh else P

    def free(self):
        bpy.data.objects.remove(self.tmp, do_unlink=True)
        bpy.data.meshes.remove(self.mesh)

    def linear_operator(self, rng_seed=0):
        """Recover the sparse matrices S_c with subdivide(cage)[:, c] == S_c @ cage[:, c].

        The support of every subdivided vertex is known from the topology (the
        cage vertices of the faces around its coarse face), so a few
        evaluations with random cage coordinates determine the weights by a
        small least-squares problem per row.  Without a Mirror modifier all
        three axes share one matrix; with one, every axis is recovered
        separately (a reflection flips signs per axis).  The result is verified
        against a fresh evaluation; None is returned when it cannot be trusted.
        """
        mirrors = [m for m in self.tmp.modifiers if m.type == "MIRROR" and m.show_viewport]
        if self.n_cage == 0 or any(m.mirror_object is not None or any(m.use_bisect_axis) for m in mirrors):
            return None
        self.mirror_axes = sorted({ax for m in mirrors for ax in range(3) if m.use_axis[ax]})

        _, me = self.evaluate(self.cage, keep_mesh=True)
        try:
            a = me.attributes.get(FACE_ATTR)
            if a is None:
                return None
            sub_face = np.empty(len(me.polygons), dtype=np.int64)
            a.data.foreach_get("value", sub_face)
            M = len(me.vertices)
            s_loop_vert = np.empty(len(me.loops), dtype=np.int64)
            me.loops.foreach_get("vertex_index", s_loop_vert)
            s_loop_face = _loop_faces(me)
        finally:
            bpy.data.meshes.remove(me)

        cm = self.mesh
        N, F = self.n_cage, len(cm.polygons)
        c_loop_vert = np.empty(len(cm.loops), dtype=np.int64)
        cm.loops.foreach_get("vertex_index", c_loop_vert)
        c_loop_face = _loop_faces(cm)
        f_off, f_verts = _csr(c_loop_face, c_loop_vert, F)
        v_off, v_faces = _csr(c_loop_vert, c_loop_face, N)
        # faces sharing a vertex with each face, then their vertices
        ff = _unique_pairs(*_expand(c_loop_face, c_loop_vert, v_off, v_faces), F)
        fn = _unique_pairs(*_expand(ff[0], ff[1], f_off, f_verts), N)
        n_off, n_verts = _csr(fn[0], fn[1], F)
        jf = _unique_pairs(s_loop_vert, sub_face[s_loop_face], F)
        rows, cols = _unique_pairs(*_expand(jf[0], jf[1], n_off, n_verts), N)

        m = np.bincount(rows, minlength=M)
        starts = np.concatenate([[0], np.cumsum(m)[:-1]])
        m99 = float(np.percentile(m, 99))
        shared = not mirrors
        if shared:
            n_eval = int(np.clip(np.ceil(1.5 * m99 / 3.0), 4, 40))
            groups = [(list(range(3)), np.arange(3 * n_eval))]
        else:
            n_eval = int(np.clip(np.ceil(1.5 * m99), 8, 60))
            groups = [([c], np.arange(c, 3 * n_eval, 3)) for c in range(3)]
        rng = np.random.default_rng(rng_seed)
        R = np.empty((N, 3 * n_eval))
        Y = np.empty((M, 3 * n_eval))
        for k in range(n_eval):
            R[:, 3 * k:3 * k + 3] = self._random_cage(rng)
            Y[:, 3 * k:3 * k + 3] = self.evaluate(R[:, 3 * k:3 * k + 3])
        set_mesh_coords(self.mesh, self.cage)

        vals = np.zeros((len(rows), 3))
        n_samples = len(groups[0][1])
        row_ok = (m > 0) & (m * 1.5 <= n_samples)
        for channels, sample_cols in groups:
            Rg, Yg = R[:, sample_cols], Y[:, sample_cols]
            for mm in np.unique(m[row_ok]):
                js_all = np.nonzero(row_ok & (m == mm))[0]
                for c0 in range(0, len(js_all), 2048):
                    js = js_all[c0:c0 + 2048]
                    idx = starts[js][:, None] + np.arange(mm)
                    A = Rg[cols[idx]]                      # (G, m, samples)
                    AtA = np.einsum("gik,gjk->gij", A, A)
                    AtA += np.eye(mm) * 1e-10 * max(float(np.abs(AtA).max()), 1.0)
                    Aty = np.einsum("gik,gk->gi", A, Yg[js])
                    w = np.linalg.solve(AtA, Aty[..., None])[..., 0]
                    for c in channels:
                        vals[idx, c] = w
        keep = row_ok[rows]
        op = SparseOp(rows[keep], cols[keep], vals[keep], M, N, row_ok)

        probe = self._random_cage(rng)
        truth = self.evaluate(probe)
        set_mesh_coords(self.mesh, self.cage)
        err = np.abs(op.matvec(probe) - truth)[row_ok]
        if err.size == 0 or err.max() > 1e-4:
            return None
        return op

    def _random_cage(self, rng):
        """Random cage coordinates that keep the modifier topology: vertices on
        a mirror plane stay there, all others stay clear of it (no new merges)."""
        X = rng.uniform(-1.0, 1.0, size=(self.n_cage, 3))
        for ax in getattr(self, "mirror_axes", ()):
            X[:, ax] = np.where(X[:, ax] < 0, -1.0, 1.0) * rng.uniform(0.1, 1.0, self.n_cage)
        return self.constrain(X)


class SparseOp:
    """Minimal sparse matrix, one set of values per axis (no scipy in Blender)."""

    def __init__(self, rows, cols, vals, n_rows, n_cols, row_ok):
        self.rows, self.cols, self.vals = rows, cols, vals
        self.shape = (n_rows, n_cols)
        self.row_ok = row_ok

    def matvec(self, X):
        out = np.empty((self.shape[0], 3))
        for k in range(3):
            out[:, k] = np.bincount(self.rows, weights=self.vals[:, k] * X[self.cols, k],
                                    minlength=self.shape[0])
        return out

    def rmatvec(self, Y):
        out = np.empty((self.shape[1], 3))
        for k in range(3):
            out[:, k] = np.bincount(self.cols, weights=self.vals[:, k] * Y[self.rows, k],
                                    minlength=self.shape[1])
        return out

    def solve_least_squares(self, T, W, X0, reg=1e-3, iterations=200, tol=1e-12):
        """argmin_X  sum_j W_j |S_j X - T_j|^2 + reg |X - X0|^2  (conjugate gradient)."""
        def A(X):
            return self.rmatvec(W[:, None] * self.matvec(X)) + reg * X
        X = X0.copy()
        b = self.rmatvec(W[:, None] * T) + reg * X0
        r = b - A(X)
        p = r.copy()
        rs = np.einsum("ij,ij->j", r, r)
        rs0 = np.maximum(rs, 1e-300)
        for _ in range(iterations):
            Ap = A(p)
            alpha = rs / np.maximum(np.einsum("ij,ij->j", p, Ap), 1e-300)
            X += alpha * p
            r -= alpha * Ap
            rs_new = np.einsum("ij,ij->j", r, r)
            if (rs_new <= tol * rs0).all():
                break
            p = r + (rs_new / np.maximum(rs, 1e-300)) * p
            rs = rs_new
        return X


def _loop_faces(me):
    tot = np.empty(len(me.polygons), dtype=np.int64)
    me.polygons.foreach_get("loop_total", tot)
    start = np.empty(len(me.polygons), dtype=np.int64)
    me.polygons.foreach_get("loop_start", start)
    lf = np.empty(len(me.loops), dtype=np.int64)
    for_face = np.repeat(np.arange(len(tot)), tot)
    lf[np.repeat(start, tot) + (np.arange(tot.sum()) - np.repeat(np.cumsum(tot) - tot, tot))] = for_face
    return lf


def _csr(keys, values, n_keys):
    order = np.argsort(keys, kind="stable")
    off = np.concatenate([[0], np.cumsum(np.bincount(keys, minlength=n_keys))])
    return off, values[order]


def _expand(x, y, off, vals):
    """For pairs (x, y) and a CSR map y -> vals, return all pairs (x, z)."""
    cnt = off[y + 1] - off[y]
    total = int(cnt.sum())
    xr = np.repeat(x, cnt)
    within = np.arange(total) - np.repeat(np.cumsum(cnt) - cnt, cnt)
    return xr, vals[np.repeat(off[y], cnt) + within]


def _unique_pairs(a, b, nb):
    key = np.unique(a.astype(np.int64) * nb + b)
    return key // nb, key % nb


def fit_cage(evaluator, targets_local, iterations=20, tol=1e-7):
    """Progressive iterative approximation: find cage coords whose subdivision
    passes through ``targets_local`` at the cage-corresponding vertices.
    Converges for Catmull-Clark; the residual is returned for reporting."""
    n = evaluator.n_cage
    C = evaluator.cage.copy()
    T = targets_local[:n]
    err = np.inf
    for _ in range(max(int(iterations), 1)):
        S = evaluator.evaluate(C)
        R = T - S[:n]
        err = float(np.abs(R).max()) if n else 0.0
        if err < tol:
            break
        C = evaluator.constrain(C + R)
    return C, err


# ---------------------------------------------------------------------------
# live offset (Geometry Nodes modifier)

LIVE_MOD = "PSW Offset"
LIVE_GROUP = "PSW Live Offset"
DIR_ATTR = "psw_dir"
FITTED_ATTR = "psw_fitted"


def vectors_to_local(V, mat):
    """World-space displacement vectors to the object's local space."""
    M = np.array(mat, dtype=np.float64)[:3, :3]
    return V @ np.linalg.inv(M).T


def live_offset_group():
    """Node group: position += psw_dir * (Offset - psw_fitted).

    psw_dir is the offset direction of every vertex found by the fit (already
    scaled by per-vertex offset weights), psw_fitted the offset it was fitted
    at, so the result is unchanged at the fitted value and follows the slider
    in real time."""
    ng = bpy.data.node_groups.get(LIVE_GROUP)
    if ng is not None and ng.bl_idname == "GeometryNodeTree":
        return ng
    ng = bpy.data.node_groups.new(LIVE_GROUP, "GeometryNodeTree")
    iface = ng.interface
    iface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    sock = iface.new_socket("Offset", in_out="INPUT", socket_type="NodeSocketFloat")
    sock.subtype = "DISTANCE"
    sock.default_value = 0.002
    iface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nodes, links = ng.nodes, ng.links
    gi = nodes.new("NodeGroupInput")
    go = nodes.new("NodeGroupOutput")
    direction = nodes.new("GeometryNodeInputNamedAttribute")
    direction.data_type = "FLOAT_VECTOR"
    direction.inputs["Name"].default_value = DIR_ATTR
    fitted = nodes.new("GeometryNodeInputNamedAttribute")
    fitted.data_type = "FLOAT"
    fitted.inputs["Name"].default_value = FITTED_ATTR
    delta = nodes.new("ShaderNodeMath")
    delta.operation = "SUBTRACT"
    scale = nodes.new("ShaderNodeVectorMath")
    scale.operation = "SCALE"
    move = nodes.new("GeometryNodeSetPosition")
    links.new(gi.outputs["Offset"], delta.inputs[0])
    links.new(fitted.outputs["Attribute"], delta.inputs[1])
    links.new(direction.outputs["Attribute"], scale.inputs[0])
    links.new(delta.outputs[0], scale.inputs["Scale"])
    links.new(gi.outputs["Geometry"], move.inputs["Geometry"])
    links.new(scale.outputs["Vector"], move.inputs["Offset"])
    links.new(move.outputs["Geometry"], go.inputs["Geometry"])
    for node, x, y in ((gi, -700, 0), (direction, -700, -200), (fitted, -700, -380), (delta, -450, -250),
                       (scale, -250, -200), (move, 0, 0), (go, 200, 0)):
        node.location = (x, y)
    return ng


def live_offset_modifier(obj):
    m = obj.modifiers.get(LIVE_MOD) if obj is not None else None
    return m if m is not None and m.type == "NODES" else None


def live_offset_identifier(ng):
    return ng.interface.items_tree["Offset"].identifier


def set_live_offset(obj, dir_local, fitted):
    """Store the offset directions on the mesh and add / update the
    "PSW Offset" modifier at the top of the stack (after a Slide on Body
    modifier if there is one)."""
    me = obj.data
    n = len(me.vertices)
    for name, dtype, key, data in ((DIR_ATTR, "FLOAT_VECTOR", "vector", np.asarray(dir_local, np.float64)),
                                   (FITTED_ATTR, "FLOAT", "value", np.full(n, float(fitted)))):
        a = me.attributes.get(name)
        if a is not None and (a.data_type != dtype or a.domain != "POINT"):
            me.attributes.remove(a)
            a = None
        if a is None:
            a = me.attributes.new(name, dtype, "POINT")
        a.data.foreach_set(key, data.ravel())
    ng = live_offset_group()
    m = live_offset_modifier(obj)
    if m is None:
        m = obj.modifiers.new(LIVE_MOD, "NODES")
    m.node_group = ng
    m[live_offset_identifier(ng)] = float(fitted)
    m.show_in_editmode = True
    index = 1 if len(obj.modifiers) > 1 and obj.modifiers[0].name == "PSW Slide" else 0
    current = list(obj.modifiers).index(m)
    if current != index:
        obj.modifiers.move(current, index)
    obj.update_tag()
    me.update()


# ---------------------------------------------------------------------------
# writing results


def write_result(obj, new_local, output="APPLY", key_name="PrecisionShrinkwrap"):
    """Write fitted cage coordinates to ``obj``.

    APPLY: move the mesh; existing shape keys get the same displacement so
           their morphs stay valid.
    SHAPE_KEY: store the result in a new shape key (value 1.0).
    """
    me = obj.data
    old = mesh_coords(me)
    if output == "SHAPE_KEY":
        if me.shape_keys is None:
            obj.shape_key_add(name="Basis", from_mix=False)
        key = obj.shape_key_add(name=key_name, from_mix=False)
        key.data.foreach_set("co", np.asarray(new_local, dtype=np.float64).ravel())
        key.value = 1.0
        obj.active_shape_key_index = len(me.shape_keys.key_blocks) - 1
        me.update()
        return
    delta = np.asarray(new_local) - old
    if me.shape_keys:
        for kb in me.shape_keys.key_blocks:
            co = np.empty(len(kb.data) * 3)
            kb.data.foreach_get("co", co)
            kb.data.foreach_set("co", (co.reshape(-1, 3) + delta).ravel())
    set_mesh_coords(me, new_local)
