# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender glue: hair group (curve object) -> generated mesh objects."""

import bpy
from mathutils import Matrix

from . import core, materials

HAIR_SUFFIX = "_Hair"
OUTLINE_SUFFIX = "_Outline"
PROXY_MOD = "TH_NormalProxy"
VG_ROOT_TIP = "TH_root_tip"
VG_PIN = "TH_pin"

PARAM_KEYS = (
    "length_segments", "profile_segments", "tip_bias", "profile", "backface",
    "width", "thickness", "curvature", "root_ratio", "bulge_pos", "tip_shape",
    "tip_mode", "tip_min", "root_cap", "twist", "root_extend", "flip_offset",
    "normal_mode", "normal_blend", "outline", "outline_width",
    "outline_tip_ratio", "outline_taper_start",
    "connect", "connect_ratio", "split", "notch_soft", "seam_thickness", "order",
    "branches", "branch_jitter", "branch_pos", "branch_spread", "branch_length_var",
    "branch_pattern", "seed", "connect_style", "smooth_iterations",
    "bevel", "bend_density", "curl_turns", "curl_radius", "curl_start", "wave_amp", "wave_freq",
    "lift", "puff", "puff_pos", "tip_flip", "roll", "layers", "layer_gap", "layer_shift",
    "layer_front_len", "layer_back_len", "layer_width", "layer_flip",
    "cut_taper", "cut_enable", "cut_height", "plane_rows", "merge_overlaps", "auto_merge_roots", "head_follow",
)

_building = False
_last_sig = {}     # group name -> signature of the inputs of its last rebuild


def signature(curve_ob):
    """What the live rebuild depends on besides the settings: the guide
    curves, the group's transform and its head centre's transform."""
    def mat(m):
        return tuple(round(x, 7) for row in m for x in row)
    sig = [mat(curve_ob.matrix_world)]
    c = curve_ob.toon_hair.center
    sig.append(mat(c.matrix_world) if c is not None else None)
    for sp in curve_ob.data.splines:
        if sp.type == 'BEZIER':
            sig.append(tuple((tuple(bp.co), tuple(bp.handle_left), tuple(bp.handle_right),
                              round(bp.radius, 6), round(bp.tilt, 6)) for bp in sp.bezier_points))
        else:
            sig.append(tuple((tuple(p.co), round(p.radius, 6), round(p.tilt, 6)) for p in sp.points))
    return tuple(sig)


def needs_rebuild(curve_ob):
    return _last_sig.get(curve_ob.name) != signature(curve_ob)


def is_group(ob):
    return ob is not None and ob.type == 'CURVE' and ob.toon_hair.enabled


def owner_group(ob):
    """The hair group a (curve or generated) object belongs to."""
    if ob is None:
        return None
    if is_group(ob):
        return ob
    owner = getattr(ob, "toon_hair_owner", None)
    if owner is not None and is_group(owner):
        return owner
    return None


def params_from(settings):
    return core.StrandParams(**{k: getattr(settings, k) for k in PARAM_KEYS})


def center_of(settings):
    c = settings.center
    return core.Center(c.matrix_world if c is not None else None)


def generate(curve_ob, world=True, with_outline=True):
    """Return (hair Geo, outline Geo or None) for a hair group."""
    st = curve_ob.toon_hair
    prm = params_from(st)
    center = center_of(st)
    mw = curve_ob.matrix_world
    splines = list(curve_ob.data.splines)
    raw = [core.sample_spline(sp, mw) for sp in splines]
    keep = [i for i, smp in enumerate(raw) if len(smp) >= 2]
    remap = {old: new for new, old in enumerate(keep)}
    samples = [raw[i] for i in keep]
    links, trunks, starts, hosts = [], {}, {}, {}
    for l in st.links:
        if l.a not in remap or l.b not in remap or l.a == l.b:
            continue
        if l.embed:
            if l.stray_idx in remap and l.host_idx in remap:
                starts[remap[l.stray_idx]] = l.t0
                hosts[remap[l.stray_idx]] = remap[l.host_idx]
            continue
        stray = {l.a, l.b} == {l.stray_idx, l.host_idx}
        links.append((remap[l.a], remap[l.b], l.t0, l.t1,
                      (remap[l.stray_idx], remap[l.host_idx]) if stray else None))
        if stray and l.trunk > 0.0:
            trunks[remap[l.stray_idx]] = l.trunk
    hair, outline = core.build_group(samples, center, prm, mirror=st.mirror_x,
                                     with_outline=with_outline, links=links, trunks=trunks,
                                     embed_starts=starts, embed_hosts=hosts)
    if st.weld_dist > 0.0:
        core.weld_geo(hair, st.weld_dist)
    if not world:
        inv = mw.inverted_safe()
        hair.transform(inv)
        if outline is not None:
            outline.transform(inv)
    return hair, outline


# --------------------------------------------------------------------------
# Mesh writing
# --------------------------------------------------------------------------

def _float_attr(me, name, values, domain='POINT'):
    a = me.attributes.get(name)
    if a is not None and (a.data_type != 'FLOAT' or a.domain != domain):
        me.attributes.remove(a)
        a = None
    if a is None:
        a = me.attributes.new(name, 'FLOAT', domain)
    a.data.foreach_set("value", values)


def _vec_attr(me, name, values):
    a = me.attributes.get(name)
    if a is not None and (a.data_type != 'FLOAT_VECTOR' or a.domain != 'POINT'):
        me.attributes.remove(a)
        a = None
    if a is None:
        a = me.attributes.new(name, 'FLOAT_VECTOR', 'POINT')
    flat = [c for v in values for c in v]
    a.data.foreach_set("vector", flat)


def write_mesh(me, geo, custom_normals=True, seams=False):
    me.clear_geometry()
    me.from_pydata([v[:] for v in geo.verts], [], geo.faces)

    if seams and len(me.edges):
        cut = geo.seam_edges()
        flags = [False] * len(me.edges)
        if cut:
            vs = [0] * (2 * len(me.edges))
            me.edges.foreach_get("vertices", vs)
            for i in range(len(me.edges)):
                a, b = vs[2 * i], vs[2 * i + 1]
                if ((a, b) if a < b else (b, a)) in cut:
                    flags[i] = True
        me.edges.foreach_set("use_seam", flags)

    if len(me.loops):
        uvl = me.uv_layers.get("UVMap") or me.uv_layers.new(name="UVMap")
        flat = [c for uvs in geo.face_uv for uv in uvs for c in uv]
        uvl.data.foreach_set("uv", flat)

    if geo.verts:
        _float_attr(me, "TH_t", geo.t)
        _float_attr(me, "TH_inner", geo.inner)

    if hasattr(me, "shade_smooth"):
        me.shade_smooth()
    else:  # pragma: no cover - < 4.1
        me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
    me.update()
    if custom_normals and geo.normals:
        me.normals_split_custom_set_from_vertices([n[:] for n in geo.normals])
    me.update()


def write_vertex_groups(ob, geo):
    for name, func in ((VG_ROOT_TIP, lambda t: t), (VG_PIN, lambda t: 1.0 - t)):
        vg = ob.vertex_groups.get(name) or ob.vertex_groups.new(name=name)
        buckets = {}
        for i, t in enumerate(geo.t):
            buckets.setdefault(round(func(t), 3), []).append(i)
        for w, idx in buckets.items():
            vg.add(idx, w, 'REPLACE')


def _new_child(name, parent, collection):
    me = bpy.data.meshes.new(name)
    ob = bpy.data.objects.new(name, me)
    collection.objects.link(ob)
    ob.parent = parent
    ob.matrix_parent_inverse = Matrix.Identity(4)
    ob.lock_location = ob.lock_rotation = ob.lock_scale = (True, True, True)
    ob.toon_hair_owner = parent
    return ob


def _collection_of(ob):
    if ob.users_collection:
        return ob.users_collection[0]
    return bpy.context.scene.collection


def _sync_culling(mat):
    """Double-sided if any group using the material is single-sheet."""
    if mat is None:
        return
    single = False
    for ob in bpy.data.objects:
        if is_group(ob) and ob.toon_hair.material == mat and ob.toon_hair.backface == 'SINGLE':
            single = True
            break
    if mat.use_backface_culling == single:
        mat.use_backface_culling = not single


def ensure_objects(curve_ob):
    st = curve_ob.toon_hair
    coll = _collection_of(curve_ob)
    if st.hair_object is None or st.hair_object.name not in bpy.data.objects:
        st.hair_object = _new_child(curve_ob.name + HAIR_SUFFIX, curve_ob, coll)
    if st.material is None:
        st.material = materials.ensure_hair_material()
    if st.outline_material is None:
        st.outline_material = materials.ensure_outline_material()
    if st.outline:
        if st.outline_object is None or st.outline_object.name not in bpy.data.objects:
            ob = _new_child(curve_ob.name + OUTLINE_SUFFIX, curve_ob, coll)
            ob.hide_select = True
            st.outline_object = ob
        st.outline_object.visible_shadow = False
        if hasattr(st.outline_object, "visible_diffuse"):
            st.outline_object.visible_diffuse = False
            st.outline_object.visible_glossy = False


def _assign_material(me, mat):
    if len(me.materials) == 0:
        me.materials.append(mat)
    elif me.materials[0] != mat:
        me.materials[0] = mat


def _sync_proxy_modifier(ob, st):
    mod = ob.modifiers.get(PROXY_MOD)
    if st.normal_mode == 'PROXY' and st.normal_proxy is not None:
        if mod is None:
            mod = ob.modifiers.new(PROXY_MOD, 'DATA_TRANSFER')
        mod.object = st.normal_proxy
        mod.use_object_transform = True
        mod.use_loop_data = True
        mod.data_types_loops = {'CUSTOM_NORMAL'}
        mod.loop_mapping = 'POLYINTERP_NEAREST'
        mod.mix_factor = st.normal_blend
    elif mod is not None:
        ob.modifiers.remove(mod)


def rebuild(curve_ob):
    """Regenerate the meshes of one hair group."""
    global _building
    if not is_group(curve_ob) or _building:
        return
    _building = True
    try:
        st = curve_ob.toon_hair
        ensure_objects(curve_ob)
        hair, outline = generate(curve_ob, world=False)

        hob = st.hair_object
        write_mesh(hob.data, hair, custom_normals=st.normal_mode == 'VOLUME', seams=st.uv_seams)
        write_vertex_groups(hob, hair)
        _assign_material(hob.data, st.material)
        _sync_proxy_modifier(hob, st)
        _sync_culling(st.material)

        oob = st.outline_object
        if oob is not None:
            # only touch visibility when the outline is switched on/off, so a
            # user hiding the outline object by hand keeps it hidden
            if outline is not None:
                write_mesh(oob.data, outline, custom_normals=False)
                _assign_material(oob.data, st.outline_material)
                if oob.get("th_auto_hidden"):
                    oob.hide_viewport = False
                    oob.hide_render = False
                    del oob["th_auto_hidden"]
            else:
                oob.data.clear_geometry()
                if not oob.get("th_auto_hidden"):
                    oob.hide_viewport = True
                    oob.hide_render = True
                    oob["th_auto_hidden"] = True
        _last_sig[curve_ob.name] = signature(curve_ob)
    finally:
        _building = False


def control_point_ts(spline):
    """Arc-length parameter (0..1) of each control point of a spline."""
    smp = core.sample_spline(spline, Matrix.Identity(4))
    if len(smp) < 2:
        return []
    L = [0.0]
    for i in range(1, len(smp)):
        L.append(L[-1] + (smp[i][0] - smp[i - 1][0]).length)
    total = L[-1] or 1.0
    if spline.type == 'BEZIER':
        n, step = len(spline.bezier_points), 32
    elif spline.type == 'NURBS' and len(spline.points) >= 3:
        n, step = len(spline.points), 32
    else:
        n, step = len(spline.points), 4
    return [L[min(k * step, len(L) - 1)] / total for k in range(n)]


def selected_ts(spline):
    if spline.type == 'BEZIER':
        sel = [bp.select_control_point for bp in spline.bezier_points]
    else:
        sel = [p.select for p in spline.points]
    ts = control_point_ts(spline)
    return [t for t, s_ in zip(ts, sel) if s_]


def on_spline_removed(curve_ob, idx):
    """Keep manual links pointing at the right splines after a removal."""
    links = curve_ob.toon_hair.links
    for i in range(len(links) - 1, -1, -1):
        l = links[i]
        if idx in (l.a, l.b):
            links.remove(i)
            continue
        if l.a > idx:
            l.a -= 1
        if l.b > idx:
            l.b -= 1
        if l.stray_idx > idx:
            l.stray_idx -= 1
        if l.host_idx > idx:
            l.host_idx -= 1


# --------------------------------------------------------------------------
# attach a curve stuck into a lock (branch / stray hair)
# --------------------------------------------------------------------------

def _units_of(curve_ob):
    """One unit per spline (no mirror / layers), for geometric queries."""
    import random
    st = curve_ob.toon_hair
    prm = params_from(st)
    center = center_of(st)
    mw = curve_ob.matrix_world
    samples = [core.sample_spline(sp, mw) for sp in curve_ob.data.splines]
    valid = [smp for smp in samples if len(smp) >= 2]
    prm.density_profile = core.density_profile(valid, prm) if valid else None
    units = []
    for i, smp in enumerate(samples):
        u = core.make_unit(smp, center, prm, False, float(i), random.Random(i)) if len(smp) >= 2 else None
        units.append(u)
    return units, center


def _nearest_on_path(path, q):
    best = (1e30, 0.0, None)
    for (pa, ta), (pb, tb) in zip(path, path[1:]):
        d = pb - pa
        L2 = d.length_squared
        f = 0.0 if L2 < 1e-18 else min(max((q - pa).dot(d) / L2, 0.0), 1.0)
        p = pa + d * f
        dist = (q - p).length
        if dist < best[0]:
            best = (dist, ta + (tb - ta) * f, p)
    return best


def _unit_width(u, t):
    """Total width of a unit (all sub-locks) at param t."""
    lk0 = u.locks[0]
    ts = lk0.ts
    i = min(range(len(ts)), key=lambda k: abs(ts[k] - t))
    return sum(lk.widths[min(i, len(lk.widths) - 1)] for lk in u.locks)


def _surface_exit(curve_ob, su, hu):
    """Param along the stuck curve where its centre leaves the host lock
    through the host's surface (front/back or side), measured in the host's
    own cross-section frames - so the root ring of the embedded curve sits
    right at the host's surface."""
    st = curve_ob.toon_hair
    half_t = 0.5 * st.thickness
    rows = [(lk.frames[i], lk.widths[i]) for lk in hu.locks for i in range(len(lk.frames))]
    inside_seen = False
    for q, t in su.path:
        (P, T, N, B), w = min(rows, key=lambda r: (r[0][0] - q).length)
        if w <= 1e-6:
            continue
        x = (q - P).dot(B) / (0.5 * w)
        y = (q - P).dot(N)
        xc = min(abs(x), 1.0)
        env = 1.0 - xc * xc
        mid = st.curvature * env * w
        front = mid + half_t * env * w
        back = mid - half_t * env * w
        inside = abs(x) <= 1.0 and back <= y <= front
        if inside:
            inside_seen = True
        elif inside_seen:
            return t
    return None


def _embed_curve(curve_ob, s_idx, units):
    """'Keep as is' attach: find the lock the curve is stuck in and where it
    comes out; the curve is then generated from just before that point, so
    the buried part disappears and the root sinks into the host. Shape and
    UVs of both stay exactly as they were."""
    su = units[s_idx]
    if su is None:
        return None
    root = su.path[0][0]
    ws = _unit_width(su, 0.05)
    best = None
    for h_idx, hu in enumerate(units):
        if hu is None or h_idx == s_idx:
            continue
        dist, th, _ph = _nearest_on_path(hu.path, root)
        wh = _unit_width(hu, th)
        if dist <= 0.5 * (wh + ws) * 1.1:
            score = dist / max(wh, 1e-6)
            if best is None or score < best[0]:
                best = (score, h_idx)
    if best is None:
        return None
    h_idx = best[1]
    hu = units[h_idx]
    t_out = _surface_exit(curve_ob, su, hu)
    if t_out is None or t_out <= 0.02:
        return None
    start = max(0.0, t_out - 0.01)
    links = curve_ob.toon_hair.links
    for i in range(len(links) - 1, -1, -1):
        if links[i].embed and links[i].stray_idx == s_idx:
            links.remove(i)
    l = links.add()
    l.a, l.b = min(s_idx, h_idx), max(s_idx, h_idx)
    l.t0, l.t1, l.embed = start, start, True
    l.stray_idx, l.host_idx = s_idx, h_idx
    return h_idx


def attach_curves(curve_ob, targets, mode='BRANCH'):
    if mode == 'EMBED':
        units, _c = _units_of(curve_ob)
        done = []
        for s_idx in targets:
            h = _embed_curve(curve_ob, s_idx, units)
            if h is not None:
                done.append((s_idx, h))
        return done
    return _attach_branch(curve_ob, targets)


def _attach_branch(curve_ob, targets):
    """Attach guides stuck into a lock: extend each one back along its host
    to the host's root (so they share the trunk) and join it to the host
    from the root to where it comes out. Returns list of (target, host)."""
    units, center = _units_of(curve_ob)
    splines = curve_ob.data.splines
    mw = curve_ob.matrix_world
    inv = mw.inverted()
    done = []
    for s_idx in targets:
        su = units[s_idx] if s_idx < len(units) else None
        if su is None:
            continue
        root = su.path[0][0]
        ws = _unit_width(su, 0.05)
        best = None
        for h_idx, hu in enumerate(units):
            if hu is None or h_idx == s_idx or h_idx in targets:
                continue
            dist, th, ph = _nearest_on_path(hu.path, root)
            wh = _unit_width(hu, th)
            if dist <= 0.5 * (wh + ws) * 1.1:
                score = dist / max(wh, 1e-6)
                if best is None or score < best[0]:
                    best = (score, h_idx, th, ph, wh)
        if best is None:
            continue
        _score, h_idx, th, ph, wh = best
        hu = units[h_idx]
        sp = splines[s_idx]
        orig_root = root.copy()
        # 1) extend back along the host to its root (shared trunk)
        if th > 0.06 and sp.type == 'BEZIER':
            off = root - ph
            new_pts = []
            for f in (0.0, 0.5):
                t = th * f
                p, _tan = core._path_at(hu.path, t)
                scale = _unit_width(hu, t) / max(wh, 1e-6)
                new_pts.append(inv @ (p + off * min(scale, 1.0)))
            bps = sp.bezier_points
            old = [(bp.co.copy(), bp.handle_left.copy(), bp.handle_right.copy(), bp.handle_left_type,
                    bp.handle_right_type, bp.radius, bp.tilt) for bp in bps]
            bps.add(len(new_pts))
            r0 = old[0][5]
            for k, co in enumerate(new_pts):
                bp = bps[k]
                bp.handle_left_type = bp.handle_right_type = 'AUTO'
                bp.co = co
                bp.radius, bp.tilt = r0, old[0][6]
            for k, (co, hl, hr, tl, tr, rad, tilt) in enumerate(old):
                bp = bps[k + len(new_pts)]
                bp.handle_left_type, bp.handle_right_type = 'FREE', 'FREE'
                bp.co, bp.handle_left, bp.handle_right = co, hl, hr
                bp.handle_left_type, bp.handle_right_type = tl, tr
                bp.radius, bp.tilt = rad, tilt
            bps[len(new_pts)].handle_left_type = bps[len(new_pts)].handle_right_type = 'AUTO'
        # 2) where does it come out of the host?
        units2, _c = _units_of(curve_ob)
        su, hu = units2[s_idx], units2[h_idx]
        trunk = _nearest_on_path(su.path, orig_root)[1] if th > 0.06 else 0.0
        t_out = 0.8
        inside_seen = False
        for q, t in su.path:
            dist, th2, _p = _nearest_on_path(hu.path, q)
            lim = 0.5 * (_unit_width(hu, th2) + _unit_width(su, t)) * 0.95
            if dist <= lim:
                inside_seen = True
            elif inside_seen and t > 0.05:
                t_out = t
                break
        t_end = min(t_out + 0.04, 0.95)
        # 3) join to the host (and to the neighbour on the other side when the
        #    curve sits between two joined locks)
        links = curve_ob.toon_hair.links
        partners = {h_idx}
        linked = {}
        band = {}
        for l in links:
            linked.setdefault(l.a, set()).add(l.b)
            linked.setdefault(l.b, set()).add(l.a)
            band[frozenset((l.a, l.b))] = l.t1
        if h_idx in linked:
            az = {i: center.head_coord(u.mid)[0] for i, u in enumerate(units2) if u is not None}
            comp, stack = set(), [h_idx]
            while stack:
                x = stack.pop()
                if x in comp:
                    continue
                comp.add(x)
                stack += list(linked.get(x, ()))
            order = sorted(comp | {s_idx}, key=lambda i: az[i])
            k = order.index(s_idx)
            for nb in (order[k - 1] if k > 0 else None, order[k + 1] if k + 1 < len(order) else None):
                if nb is not None:
                    partners.add(nb)
        for nb in partners:
            for i in range(len(links) - 1, -1, -1):
                if {links[i].a, links[i].b} == {s_idx, nb}:
                    links.remove(i)
            l = links.add()
            l.a, l.b, l.t0 = min(s_idx, nb), max(s_idx, nb), 0.0
            if nb == h_idx:
                l.t1 = t_end
                l.stray_idx, l.host_idx, l.trunk = s_idx, h_idx, trunk
            else:
                # neighbour on the other side: keep it joined where it was
                # joined to the host, but not past the point the curve leaves
                l.t1 = min(t_end, band.get(frozenset((h_idx, nb)), t_end))
        done.append((s_idx, h_idx))
    return done


def check_connections(ob, root_t=0.02):
    """Problems on a generated hair mesh: open edges that are neither at a
    root nor on the outer border of a single-sheet group, and edges with
    more than two faces. Returns (open_edges, nonmanifold_edges)."""
    me = ob.data
    t = me.attributes.get("TH_t")
    tv = [0.0] * len(me.vertices)
    if t is not None:
        t.data.foreach_get("value", tv)
    use = {}
    for p in me.polygons:
        vs = list(p.vertices)
        for a in range(len(vs)):
            k = tuple(sorted((vs[a], vs[(a + 1) % len(vs)])))
            use[k] = use.get(k, 0) + 1
    owner = owner_group(ob)
    sheet = owner is not None and owner.toon_hair.backface != 'CLOSED'
    open_e = [k for k, c in use.items() if c == 1 and min(tv[k[0]], tv[k[1]]) > root_t]
    if sheet:
        open_e = []          # single sheets are open by design
    many = [k for k, c in use.items() if c > 2]
    return open_e, many


def triangle_count(ob):
    if ob is None or ob.type != 'MESH':
        return 0
    me = ob.data
    n = len(me.polygons)
    if not n:
        return 0
    tot = [0] * n
    me.polygons.foreach_get("loop_total", tot)
    return sum(tot) - 2 * n


def rebuild_all(only_center=None):
    for ob in bpy.data.objects:
        if is_group(ob) and (only_center is None or ob.toon_hair.center == only_center):
            rebuild(ob)


def unwrap(ob, margin=0.01):
    """UV-unwrap a mesh object along its seams (angle based) into UVMap."""
    ctx = bpy.context
    prev_active = ctx.view_layer.objects.active
    prev_sel = [o for o in ctx.selected_objects]
    for o in prev_sel:
        o.select_set(False)
    ob.select_set(True)
    ctx.view_layer.objects.active = ob
    try:
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.uv.unwrap(method='ANGLE_BASED', margin=margin)
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')
        ob.select_set(False)
        for o in prev_sel:
            o.select_set(True)
        ctx.view_layer.objects.active = prev_active


def bake(groups, name="Hair_Baked", with_outline=False, collection=None, weld=0.0):
    """Merge groups into one standalone mesh (world space) for export."""
    hair = core.Geo()
    outline = core.Geo()
    mats = []
    face_mats = []
    for g in groups:
        h, o = generate(g, world=True, with_outline=with_outline)
        mat = g.toon_hair.material
        if mat not in mats:
            mats.append(mat)
        h.face_mat = [mats.index(mat)] * len(h.faces)
        hair.extend(h)
        if o is not None:
            outline.extend(o)

    if weld > 0.0:
        core.weld_geo(hair, weld)          # stitch neighbouring groups' borders
    face_mats = hair.face_mat
    collection = collection or bpy.context.scene.collection
    me = bpy.data.meshes.new(name)
    write_mesh(me, hair, custom_normals=any(g.toon_hair.normal_mode == 'VOLUME' for g in groups),
               seams=True)
    for m in mats:
        me.materials.append(m)
    me.polygons.foreach_set("material_index", face_mats)
    me.update()
    ob = bpy.data.objects.new(name, me)
    collection.objects.link(ob)
    write_vertex_groups(ob, hair)

    oob = None
    if with_outline and outline.verts:
        ome = bpy.data.meshes.new(name + OUTLINE_SUFFIX)
        write_mesh(ome, outline, custom_normals=False)
        ome.materials.append(groups[0].toon_hair.outline_material)
        oob = bpy.data.objects.new(name + OUTLINE_SUFFIX, ome)
        collection.objects.link(oob)
        oob.visible_shadow = False
    return ob, oob
