"""Headless tests for Toon Hair Studio.

Run with a Python that has the `bpy` module (pip install bpy) or with
    blender -b --python tests/test_toon_hair.py
"""

import itertools
import os
import sys

import bpy
from mathutils import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import toon_hair_studio as th  # noqa: E402
from toon_hair_studio import build  # noqa: E402

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)


def edge_use(me):
    use = {}
    for p in me.polygons:
        vs = list(p.vertices)
        for a in range(len(vs)):
            k = tuple(sorted((vs[a], vs[(a + 1) % len(vs)])))
            use[k] = use.get(k, 0) + 1
    return use


def analyse(ob, st, tag, allow_tris=False):
    me = st.hair_object.data
    t_attr = me.attributes["TH_t"].data
    inner = me.attributes["TH_inner"].data
    check(len(me.polygons) > 0, f"{tag}: no faces")
    sizes = [len(p.vertices) for p in me.polygons]
    ngons = sum(1 for s in sizes if s > 4)
    check(ngons == 0, f"{tag}: {ngons} n-gons")
    # triangles only at tips
    # (a tip vertex is one whose faces are all triangles: the apex of a tip fan)
    vsizes = {}
    for p in me.polygons:
        for v in p.vertices:
            vsizes.setdefault(v, set()).add(len(p.vertices))
    for p in me.polygons:
        if len(p.vertices) == 3:
            fan = any(vsizes[v] == {3} for v in p.vertices)
            # end caps without bevel: a triangle lying in one ring (same t)
            ring = len({round(t_attr[v].value, 6) for v in p.vertices}) == 1
            if not (fan or ring or allow_tris):
                check(False, f"{tag}: triangle away from tip")
                break
    # no degenerate faces
    small = sum(1 for p in me.polygons if p.area < 1e-12)
    check(small == 0, f"{tag}: {small} degenerate faces")
    use = edge_use(me)
    # consistent winding: an edge shared by two faces is walked both ways
    directed = {}
    for p in me.polygons:
        vs = list(p.vertices)
        for a in range(len(vs)):
            e = (vs[a], vs[(a + 1) % len(vs)])
            directed[e] = directed.get(e, 0) + 1
    flipped = sum(1 for e, n in directed.items() if n > 1)
    check(flipped == 0, f"{tag}: {flipped} edges with inconsistent winding")
    over = sum(1 for c in use.values() if c > 2)
    check(over == 0, f"{tag}: {over} non-manifold edges (>2 faces)")
    if st.backface == 'CLOSED':
        # only the root opening may be boundary
        bad = [k for k, c in use.items() if c == 1
               and max(t_attr[k[0]].value, t_attr[k[1]].value) > 1e-6]
        check(not bad, f"{tag}: {len(bad)} open edges outside the root")
    # outer faces must face away from the head
    center = th.core.Center(st.center.matrix_world)
    mw = st.hair_object.matrix_world
    good = total = 0
    for p in me.polygons:
        if any(inner[v].value > 0.5 for v in p.vertices):
            continue
        c = mw @ p.center
        n = (mw.to_3x3() @ p.normal).normalized()
        total += 1
        good += n.dot(center.radial(c)) > -0.2
    # only locks that are meant to hug the head must face away from it; plain
    # curve-like locks (head_follow 0, e.g. an ahoge standing up) may not
    if st.head_follow >= 0.5:
        check(total == 0 or good / total > 0.97, f"{tag}: only {good}/{total} outer faces face outward")
    check(me.has_custom_normals == (st.normal_mode == 'VOLUME'), f"{tag}: custom normal state wrong")
    if st.outline:
        om = st.outline_object.data
        check(len(om.polygons) > 0, f"{tag}: empty outline")
        ouse = edge_use(om)
        check(all(c <= 2 for c in ouse.values()), f"{tag}: outline non-manifold")
    return len(me.vertices), len(me.polygons), sizes.count(3)


def islands(me):
    parent = list(range(len(me.vertices)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for e in me.edges:
        a, b = e.vertices
        parent[find(a)] = find(b)
    return len({find(i) for i in range(len(me.vertices))})


def face_islands(me, cut_seams=True):
    """Number of face islands when walking across edges that are not seams."""
    seam = {tuple(sorted(e.vertices)) for e in me.edges if e.use_seam} if cut_seams else set()
    edge_faces = {}
    for p in me.polygons:
        vs = list(p.vertices)
        for a in range(len(vs)):
            k = tuple(sorted((vs[a], vs[(a + 1) % len(vs)])))
            if k not in seam:
                edge_faces.setdefault(k, []).append(p.index)
    parent = list(range(len(me.polygons)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for fs in edge_faces.values():
        for f in fs[1:]:
            parent[find(f)] = find(fs[0])
    return len({find(i) for i in range(len(me.polygons))})


class _OpProps:
    def __init__(self, idname):
        mod, name = idname.split(".")
        self._rna = getattr(getattr(bpy.ops, mod), name).get_rna_type()

    def __setattr__(self, key, value):
        if key.startswith("_"):
            return object.__setattr__(self, key, value)
        check(key in self._rna.properties, f"ui: operator {self._rna.identifier} has no property {key}")


class MockLayout:
    """Records UI calls and validates icons, properties and operators."""
    ICONS = None

    def __init__(self):
        if MockLayout.ICONS is None:
            items = bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items
            MockLayout.ICONS = {i.identifier for i in items}
        self.calls = 0

    def __setattr__(self, key, value):
        object.__setattr__(self, key, value)

    def _icon(self, kw):
        icon = kw.get("icon", "NONE")
        check(icon in MockLayout.ICONS, f"ui: unknown icon {icon}")

    def _sub(self, *a, **kw):
        sub = MockLayout()
        return sub
    column = row = box = grid_flow = split = column_flow = _sub

    def label(self, text="", **kw):
        self._icon(kw)

    def separator(self, **kw):
        pass

    def prop(self, data, prop, **kw):
        self._icon(kw)
        check(prop in data.bl_rna.properties, f"ui: {type(data).__name__} has no property {prop}")

    def operator(self, idname, **kw):
        self._icon(kw)
        mod, name = idname.split(".")
        check(hasattr(getattr(bpy.ops, mod), name), f"ui: unknown operator {idname}")
        return _OpProps(idname)

    def operator_menu_enum(self, idname, prop, **kw):
        self._icon(kw)
        ops = _OpProps(idname)
        check(prop in ops._rna.properties, f"ui: {idname} has no enum {prop}")


class _PanelSelf:
    def __init__(self):
        self.layout = MockLayout()


def _stray_scene():
    from toon_hair_studio import draw
    center = bpy.data.objects[draw.CENTER_NAME]
    c = center.matrix_world.to_translation()
    g = draw.new_group(bpy.context, 'CUSTOM', center)
    st = g.toon_hair
    st.width, st.root_ratio, st.bulge_pos = 0.045, 0.5, 0.35
    inv = g.matrix_world.inverted()
    root = c + Vector((0.0, -0.04, 0.095))
    for dx in (-0.032, 0.0, 0.032):
        pts = [root + Vector((dx * 0.3, 0, 0)), c + Vector((dx * 0.6, -0.088, 0.06)),
               c + Vector((dx, -0.106, 0.0)), c + Vector((dx * 1.15, -0.11, -0.06)),
               c + Vector((dx * 1.3, -0.105, -0.12))]
        draw.add_bezier(g.data, [inv @ p for p in pts])
    for a_, b_ in ((0, 1), (1, 2)):
        l = st.links.add()
        l.a, l.b, l.t0, l.t1 = a_, b_, 0.0, 0.6
    build.rebuild(g)
    g["host_verts"] = len(st.hair_object.data.vertices)       # the 3 locks alone
    stray = [c + Vector((0.004, -0.095, 0.045)), c + Vector((0.01, -0.108, 0.0)),
             c + Vector((0.03, -0.13, -0.05)), c + Vector((0.06, -0.15, -0.09))]
    sp = draw.add_bezier(g.data, [inv @ p for p in stray])
    for bp in sp.bezier_points:
        bp.radius = 0.4
    build.rebuild(g)
    for o in bpy.context.selected_objects:
        o.select_set(False)
    g.select_set(True)
    bpy.context.view_layer.objects.active = g
    return g


def test_attach_embed():
    """'Keep as is': shape, UVs and the host stay; the buried part goes."""
    g = _stray_scene()
    st = g.toon_hair
    me = st.hair_object.data
    host_before = sorted(tuple(round(c_, 5) for c_ in v.co) for v in me.vertices)
    tip_before = g.matrix_world @ g.data.splines[3].bezier_points[-1].co
    nf_before = len(me.polygons)
    res = bpy.ops.toon_hair.attach(mode='EMBED')
    check(res == {'FINISHED'}, "embed attach failed")
    me = st.hair_object.data
    check(len(me.polygons) < nf_before, "embed: buried part was not removed")
    check(islands(me) == 1, f"embed: curve not connected to its host ({islands(me)} pieces)")
    analyse(g, st, "attach/embed")         # the bridge is all quads
    after = {tuple(round(c_, 5) for c_ in v.co) for v in me.vertices}
    # the host is untouched: every vertex that changed belongs to the stray
    changed = set(host_before) - after
    stray_before = len(host_before) - g["host_verts"]
    # allowed: the stray's own vertices, plus the few host vertices inside the
    # hole the bridge replaces (they are removed, nothing else moves)
    check(len(changed) <= stray_before + 8, f"embed: host geometry changed ({len(changed)} verts moved)")
    # the stray's tip is where it was and still maps to v = 1
    tip_local = st.hair_object.matrix_world.inverted() @ tip_before
    near = min(me.vertices, key=lambda v: (v.co - tip_local).length)
    check((near.co - tip_local).length < 1e-3, "embed: stray tip moved")
    uv = me.uv_layers.active.data
    vs_at_tip = [uv[li].uv.y for p in me.polygons for li, vi in zip(p.loop_indices, p.vertices) if vi == near.index]
    check(vs_at_tip and max(vs_at_tip) > 0.99, "embed: stray UV lost its 0..1 range")


def test_long_lock_twist():
    """A long lock hanging to the chest and curling must not twist."""
    import math
    from mathutils import Matrix
    from toon_hair_studio import core
    c = core.Center(Matrix.Translation((0, 0, 1.5)) @ Matrix.Diagonal((0.1, 0.1, 0.1, 1)))
    pts = [Vector((0.07, -0.06, 1.56)), Vector((0.1, -0.07, 1.45)), Vector((0.1, -0.06, 1.3)),
           Vector((0.09, -0.05, 1.15)), Vector((0.07, -0.06, 1.08)), Vector((0.03, -0.08, 1.1)),
           Vector((0.0, -0.09, 1.14))]
    smp = []
    for a_, b_ in zip(pts, pts[1:]):
        smp += [(a_.lerp(b_, k / 16), 1.0, 0.0) for k in range(16)]
    smp.append((pts[-1], 1.0, 0.0))
    for hf in (0.0, 0.6):
        fr = core.compute_frames(core.resample(smp, 40), c, head_follow=hf)
        worst = 0.0
        for (P0, T0, N0, B0), (P1, T1, N1, B1) in zip(fr, fr[1:]):
            tr = (N0 - T1 * N0.dot(T1)).normalized()
            worst = max(worst, math.degrees(tr.angle(N1)))
        check(worst < 10.0, f"long lock twists {worst:.1f} deg per row (head_follow {hf})")


def test_attach_stray():
    """A thin curve stuck into a lock attaches as a stray hair / branch."""
    from toon_hair_studio import draw
    center = bpy.data.objects[draw.CENTER_NAME]
    c = center.matrix_world.to_translation()
    g = draw.new_group(bpy.context, 'CUSTOM', center)
    st = g.toon_hair
    st.width, st.root_ratio, st.bulge_pos = 0.045, 0.5, 0.35
    inv = g.matrix_world.inverted()
    root = c + Vector((0.0, -0.04, 0.095))
    for dx in (-0.032, 0.0, 0.032):
        pts = [root + Vector((dx * 0.3, 0, 0)), c + Vector((dx * 0.6, -0.088, 0.06)),
               c + Vector((dx, -0.106, 0.0)), c + Vector((dx * 1.15, -0.11, -0.06)),
               c + Vector((dx * 1.3, -0.105, -0.12))]
        draw.add_bezier(g.data, [inv @ p for p in pts])
    for a_, b_ in ((0, 1), (1, 2)):
        l = st.links.add()
        l.a, l.b, l.t0, l.t1 = a_, b_, 0.0, 0.6
    stray = [c + Vector((0.004, -0.095, 0.045)), c + Vector((0.01, -0.108, 0.0)),
             c + Vector((0.03, -0.13, -0.05)), c + Vector((0.06, -0.15, -0.09))]
    sp = draw.add_bezier(g.data, [inv @ p for p in stray])
    for bp in sp.bezier_points:
        bp.radius = 0.4
    build.rebuild(g)
    check(islands(st.hair_object.data) == 2, "attach: stray should start separate")
    n_before = len(g.data.splines[3].bezier_points)
    for o in bpy.context.selected_objects:
        o.select_set(False)
    g.select_set(True)
    bpy.context.view_layer.objects.active = g
    res = bpy.ops.toon_hair.attach(mode='BRANCH')
    check(res == {'FINISHED'}, "attach operator failed")
    sp = g.data.splines[3]
    check(len(sp.bezier_points) > n_before, "attach: stray was not extended to the host root")
    r = g.matrix_world @ sp.bezier_points[0].co
    check((r - root).length < 0.02, f"attach: extended root {(r - root).length:.3f} from host root")
    check(any(3 in (l.a, l.b) for l in st.links), "attach: no link for the stray")
    check(islands(st.hair_object.data) == 1, "attach: stray not merged into the sheet")
    analyse(g, st, "attach")
    # division sliders switch the LOD to custom
    st.lod = 'MID'
    st.profile_segments = 3
    check(st.lod == 'CUSTOM' and st.profile_segments == 3, "division slider did not switch LOD to custom")
    st.lod = 'LOW'
    check((st.length_segments, st.profile_segments) == th_lod_low(), "LOD preset did not set divisions")


def th_lod_low():
    import toon_hair_studio as th
    return th.LOD_TABLE['LOW']


def test_buried_lock():
    """3 manually linked guides from one root with a thin middle lock: the
    overlapping root part is laid side by side (no buried lock) and the
    locks branch apart below."""
    from toon_hair_studio import core, draw
    center = bpy.data.objects[draw.CENTER_NAME]
    c = center.matrix_world.to_translation()
    g = draw.new_group(bpy.context, 'CUSTOM', center)
    st = g.toon_hair
    st.width, st.root_ratio, st.bulge_pos = 0.05, 0.5, 0.35
    inv = g.matrix_world.inverted()
    root = c + Vector((0.0, -0.035, 0.095))
    for dx, rad in ((-0.03, 1.0), (0.0, 0.45), (0.03, 1.0)):
        pts = [root, c + Vector((dx * 0.5, -0.085, 0.06)), c + Vector((dx, -0.105, 0.0)),
               c + Vector((dx * 1.2, -0.11, -0.06)), c + Vector((dx * 1.4, -0.105, -0.12))]
        sp = draw.add_bezier(g.data, [inv @ p for p in pts])
        for bp in sp.bezier_points:
            bp.radius = rad
    for a_, b_ in ((0, 1), (1, 2)):
        l = st.links.add()
        l.a, l.b, l.t0, l.t1 = a_, b_, 0.0, 0.55
    for merge in (False, True):
        st.merge_overlaps = merge
        build.rebuild(g)
        analyse(g, st, f"buried/merge{merge}")
        check(islands(st.hair_object.data) == 1, f"buried/merge{merge}: not one piece")
    # unit level: after tiling, neighbours no longer overlap near the root
    hair, _o = build.generate(g, world=True, with_outline=False)
    prm = build.params_from(st)
    cen = build.center_of(st)
    import random
    smps = [core.sample_spline(sp, g.matrix_world) for sp in g.data.splines]
    prm.density_profile = core.density_profile(smps, prm)
    locks = []
    for n, smp in enumerate(smps):
        locks += core.make_unit(smp, cen, prm, False, float(n), random.Random(n)).locks
    locks.sort(key=lambda lk: cen.head_coord(lk.frames[3][0])[0])
    before = min(core.overlap_ratio(locks[b], locks[b + 1], 1) for b in range(2))
    core.tile_root_overlaps(locks)
    after = min(core.overlap_ratio(locks[b], locks[b + 1], 1) for b in range(2))
    check(before < 0.9 and after > 0.99, f"tiling did not resolve the overlap: {before:.2f} -> {after:.2f}")


def test_ui_draw():
    """Run every panel's draw() against a validating mock layout."""
    import toon_hair_studio as th
    panels = [c for c in th.classes if issubclass(c, bpy.types.Panel)]
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    g = bpy.data.objects["Hair_Bangs"]
    for active in (None, g):
        bpy.context.view_layer.objects.active = active
        for st_mode in ('POINT', 'CUT', 'BLUNT'):
            if active is not None:
                g.toon_hair.tip_mode = st_mode
                g.toon_hair.layers = 'BOTH'
            for cls in panels:
                if hasattr(cls, "poll") and not cls.poll(bpy.context):
                    continue
                me = _PanelSelf()
                try:
                    if hasattr(cls, "draw_header"):
                        cls.draw_header(me, bpy.context)
                    cls.draw(me, bpy.context)
                except Exception as ex:
                    check(False, f"ui: {cls.__name__}.draw raised {type(ex).__name__}: {ex}")
    g.toon_hair.tip_mode = 'POINT'
    g.toon_hair.layers = 'NONE'


def test_plane_rows_and_weld():
    """Plane-shared rows for guides of different lengths, weld pass, checker."""
    import random
    from mathutils import Matrix
    from toon_hair_studio import core, draw
    c = core.Center(Matrix.Diagonal((0.1, 0.1, 0.1, 1)))

    def guide(dx, length):
        return [(Vector((dx, -0.11 - 0.01 * i / 7, 0.06 - length * i / 7)), 1.0, 0.0) for i in range(8)]
    smps = [guide(-0.012, 0.18), guide(0.012, 0.24)]       # lengths differ by a third
    offsets = {}
    for plane in (False, True):
        prm = core.StrandParams(width=0.03, connect=True, split=0.5, connect_ratio=3.0,
                                length_segments=12, profile_segments=4, plane_rows=plane)
        prm.density_profile = core.density_profile(smps, prm)
        units = []
        for n, smp in enumerate(smps):
            u = core.make_unit(smp, c, prm, False, float(n), random.Random(n))
            u.remake = (lambda al, smp=smp, n=n: core.make_unit(smp, c, prm, False, float(n),
                                                                random.Random(n), align=al))
            units.append(u)
        run = core.cluster_units(units, prm)[0]
        if plane:
            run, _side = core.align_run(run, [prm.split])
        ref, nb = sorted(run, key=lambda u: -u.length)
        band = [i for i, t in enumerate(ref.locks[0].ts) if 0 < t <= prm.split]
        offsets[plane] = max(abs((nb.locks[0].frames[i][0] - ref.locks[0].frames[i][0]).dot(ref.locks[0].frames[i][1]))
                             for i in band)
        h, _o = core.build_group(smps, c, prm, with_outline=False)
        check(all(core.newell([h.verts[q] for q in f]).length > 1e-12 for f in h.faces),
              f"plane_rows={plane}: degenerate faces")
    check(offsets[True] < 1e-4 < offsets[False], f"plane rows not aligned: {offsets}")

    # weld two single-sheet groups whose borders touch
    center = bpy.data.objects[draw.CENTER_NAME]
    gs = []
    for dx in (-0.0151, 0.0151):
        g = draw.new_group(bpy.context, 'CUSTOM', center)
        st = g.toon_hair
        st.backface, st.width, st.root_ratio, st.tip_mode, st.tip_min = 'SINGLE', 0.03, 1.0, 'BLUNT', 1.0
        st.bulge_pos, st.curvature = 0.0, 0.0
        inv = g.matrix_world.inverted()
        cc = center.matrix_world.to_translation()
        draw.add_bezier(g.data, [inv @ (cc + Vector((dx, -0.2, 0.05 - 0.05 * k))) for k in range(4)])
        build.rebuild(g)
        gs.append(g)
    for o in bpy.context.selected_objects:
        o.select_set(False)
    for g in gs:
        g.select_set(True)
    res = bpy.ops.toon_hair.bake('EXEC_DEFAULT', selected_only=True, weld=0.002, name="Hair_Welded")
    ob = bpy.data.objects.get("Hair_Welded")
    check(res == {'FINISHED'} and ob is not None, "weld bake failed")
    me = ob.data
    check(islands(me) == 1, f"weld: groups not stitched ({islands(me)} islands)")
    use = edge_use(me)
    check(all(c_ <= 2 for c_ in use.values()), "weld: non-manifold edges")
    directed = {}
    for p in me.polygons:
        vs = list(p.vertices)
        for a_ in range(len(vs)):
            e = (vs[a_], vs[(a_ + 1) % len(vs)])
            directed[e] = directed.get(e, 0) + 1
    check(all(n_ == 1 for n_ in directed.values()), "weld: inconsistent winding")
    # the same stitch inside one group via weld_dist
    g = gs[0]
    sp = gs[1].data.splines[0]
    draw.add_bezier(g.data, [gs[1].matrix_world @ bp.co for bp in sp.bezier_points])
    g.toon_hair.connect = False
    g.toon_hair.weld_dist = 0.002
    me = g.toon_hair.hair_object.data
    check(islands(me) == 1, "weld_dist: locks not stitched")
    # closed locks stay valid with welding on
    g.toon_hair.backface = 'CLOSED'
    analyse(g, g.toon_hair, "weld_dist/closed")
    # connection checker finds nothing on clean groups
    res = bpy.ops.toon_hair.check()
    check(res == {'FINISHED'}, "check operator failed")
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for o in bpy.data.objects:
        if build.is_group(o) and o.toon_hair.hair_object and o.toon_hair.backface == 'CLOSED':
            op_, many = build.check_connections(o.toon_hair.hair_object)
            check(not op_ and not many, f"checker: {o.name} open={len(op_)} many={len(many)}")


def test_uv_seams():
    """Front / back seams split every closed lock into two UV islands."""
    for name in ("Hair_Bangs", "Hair_Back", "Hair_Ahoge"):
        g = bpy.data.objects[name]
        st = g.toon_hair
        for bev in (0.3, 0.0):
            st.backface = 'CLOSED'
            st.bevel = bev
            build.rebuild(g)
            me = st.hair_object.data
            n_seam = sum(1 for e in me.edges if e.use_seam)
            parts = islands(me)
            uv_isl = face_islands(me)
            check(n_seam > 0, f"{name}: no seams")
            check(uv_isl == 2 * parts, f"{name}/bevel{bev}: {uv_isl} uv islands for {parts} pieces (want 2 each)")
        st.uv_seams = False
        check(not any(e.use_seam for e in st.hair_object.data.edges), f"{name}: seams not cleared")
        st.uv_seams = True
    res = bpy.ops.toon_hair.bake('EXEC_DEFAULT', uv_mode='UNWRAP', name="Hair_Unwrapped")
    ob = bpy.data.objects.get("Hair_Unwrapped")
    check(res == {'FINISHED'} and ob is not None, "unwrap bake failed")
    uv = ob.data.uv_layers.active.data
    us = [d.uv.x for d in uv]
    vs = [d.uv.y for d in uv]
    check(min(us) >= -1e-4 and max(us) <= 1.0001 and min(vs) >= -1e-4 and max(vs) <= 1.0001,
          "unwrapped UVs out of 0..1")
    check(len({(round(d.uv.x, 4), round(d.uv.y, 4)) for d in uv}) > len(ob.data.vertices) // 4,
          "unwrap produced degenerate UVs")


def test_live_scope():
    """Unrelated depsgraph updates must not rebuild; manual hiding sticks."""
    g = bpy.data.objects["Hair_Bangs"]
    st = g.toon_hair
    st.outline = True
    build.rebuild(g)
    calls = []
    orig = build.rebuild

    def counting(ob):
        calls.append(ob.name)
        return orig(ob)
    build.rebuild = counting
    try:
        st.outline_object.hide_render = True
        st.outline_object.hide_viewport = True
        other = bpy.data.objects.new("TH_test_empty", None)
        bpy.context.scene.collection.objects.link(other)
        other.hide_viewport = True
        bpy.context.view_layer.update()
        other.location.x += 1.0
        bpy.context.view_layer.update()
        check(not calls, f"unrelated updates rebuilt groups: {calls}")
        check(st.outline_object.hide_render, "manually hidden outline was shown again")
        # a real edit still rebuilds
        g.data.splines[0].bezier_points[-1].co.z -= 0.01
        g.data.update_tag()
        bpy.context.view_layer.update()
        check(calls.count("Hair_Bangs") == 1, f"curve edit should rebuild once: {calls}")
        check(st.outline_object.hide_render, "outline visibility changed by a rebuild")
    finally:
        build.rebuild = orig


def test_pattsun():
    """Blunt-cut bangs: one continuous hem, cut straight at the plane."""
    from toon_hair_studio import draw, core
    bpy.ops.toon_hair.add_group(preset='PATTSUN', edit=False)
    g = bpy.context.active_object
    st = g.toon_hair
    sp = g.data.splines[0]
    base = [bp.co.copy() for bp in sp.bezier_points]
    g.data.splines.remove(sp)
    for dx in (-0.06, -0.03, 0.0, 0.03, 0.06):
        draw.add_bezier(g.data, [p + Vector((dx, 0.0, 0.0)) for p in base])
    for bev in (0.3, 0.0):
        st.bevel = bev
        build.rebuild(g)
        me = st.hair_object.data
        check(islands(me) == 1, f"pattsun: hem not continuous ({islands(me)} islands)")
        analyse(g, st, f"pattsun/bevel{bev}")
        if bev > 0:
            check(all(len(p.vertices) == 4 for p in me.polygons), "pattsun with bevel should be all quads")
    # the hem lies on the cut plane
    center = core.Center(st.center.matrix_world)
    mw = st.hair_object.matrix_world
    zs = [(center.inv @ (mw @ v.co)).z for v in me.vertices]
    check(abs(min(zs) - st.cut_height) < 0.08, f"pattsun: hem at {min(zs):.3f}, cut at {st.cut_height}")


def test_manual_links():
    """Join two neighbouring guides only where points are selected."""
    from toon_hair_studio import draw
    bpy.ops.toon_hair.add_group(preset='CUSTOM', edit=False)
    g = bpy.context.active_object
    st = g.toon_hair
    st.connect = False
    st.width = 0.035
    sp = g.data.splines[0]
    pts = [bp.co.copy() for bp in sp.bezier_points]
    draw.add_bezier(g.data, [p + Vector((0.03, 0.0, 0.0)) for p in pts])
    build.rebuild(g)
    me = st.hair_object.data
    check(islands(me) == 2, f"links: expected 2 separate locks, got {islands(me)}")

    def select(spline_points):
        for i, s_ in enumerate(g.data.splines):
            for j, bp in enumerate(s_.bezier_points):
                bp.select_control_point = (i, j) in spline_points
    n = len(pts)
    # one point on each curve: join from the root to there
    select({(0, n // 2), (1, n // 2)})
    res = bpy.ops.toon_hair.link_selected()
    check(res == {'FINISHED'} and len(st.links) == 1, "link_selected failed")
    check(islands(st.hair_object.data) == 1, "links: root-to-point join did not merge the locks")
    analyse(g, st, "links/root-to-point")
    # two points on each curve: join only in between
    select({(0, 1), (0, n - 2), (1, 1), (1, n - 2)})
    bpy.ops.toon_hair.link_selected()
    check(len(st.links) == 1, "re-linking the same pair should replace the link")
    check(islands(st.hair_object.data) == 1, "links: band join did not merge the locks")
    check(st.links[0].t0 > 0.05, "band join should not start at the root")
    analyse(g, st, "links/band")
    # unlink
    bpy.ops.toon_hair.unlink(all=True)
    check(islands(st.hair_object.data) == 2, "unlink did not separate the locks")
    # edit-mode path: select everything and link
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.curve.select_all(action='SELECT')
    res = bpy.ops.toon_hair.link_selected()
    bpy.ops.object.mode_set(mode='OBJECT')
    check(res == {'FINISHED'} and len(st.links) == 1, "edit-mode link failed")
    check(islands(st.hair_object.data) == 1, "edit-mode link did not merge")
    analyse(g, st, "links/editmode")


def test_unify():
    """Messy overlapping bangs become ONE clean body that splits into locks."""
    from toon_hair_studio import draw, unify
    center = bpy.data.objects[draw.CENTER_NAME]
    c = center.matrix_world.to_translation()
    g = draw.new_group(bpy.context, 'BANGS', center)
    st = g.toon_hair
    check(st.unify, "unify should be on for new groups")
    inv = g.matrix_world.inverted()
    # uneven gaps, overlaps and a short narrow lock buried between two others
    for rx, ex, ls, rad in ((-0.07, -0.085, 0.95, 1.0), (-0.045, -0.05, 1.05, 1.1),
                            (-0.035, -0.02, 0.6, 0.6), (-0.012, -0.012, 1.0, 1.0),
                            (0.004, 0.022, 1.1, 1.2), (0.03, 0.045, 0.85, 0.9), (0.05, 0.08, 1.0, 1.0)):
        pts = [c + Vector((rx * 0.5, -0.03, 0.1)), c + Vector((rx * 0.8, -0.085, 0.07)),
               c + Vector(((rx + ex) * 0.5, -0.105, 0.02 - 0.03 * (ls - 1))),
               c + Vector((ex, -0.11, -0.03 - 0.06 * (ls - 0.8)))]
        sp = draw.add_bezier(g.data, [inv @ p for p in pts])
        for bp in sp.bezier_points:
            bp.radius = rad
    captured = {}
    orig = unify.build_unified

    def spy(locks, *a, **kw):
        J = orig(locks, *a, **kw)
        captured["J"], captured["K"] = J, len(locks)
        return J
    unify.build_unified = spy
    try:
        for bf in ('CLOSED', 'SINGLE', 'FLIPPED'):
            for bevel in (0.0, 0.3):
                st.backface, st.bevel = bf, bevel
                build.rebuild(g)
                me = st.hair_object.data
                analyse(g, st, f"unify/{bf}/bevel{bevel}")
                check(islands(me) == (2 if bf == 'FLIPPED' else 1),
                      f"unify/{bf}: group is not one body ({islands(me)} pieces)")
        st.backface, st.bevel = 'CLOSED', 0.3
        st.branches, st.branch_jitter = 1, 0
        build.rebuild(g)
        J, K = captured["J"], captured["K"]
        check(K == 7, f"unify: expected 7 locks, got {K}")
        check(all(row[0] for row in J), "unify: roots should all be one body")
        check(all(not row[-1] for row in J), "unify: every pointed tip should be its own spike")
        # each lock splits off at most once and never re-joins (clean V crotches)
        for b, row in enumerate(J):
            flips = sum(1 for i in range(len(row) - 1) if row[i] != row[i + 1])
            check(flips == 1, f"unify: boundary {b} splits {flips} times")
        # even columns: no front edge across a row is much shorter than the typical one
        me = st.hair_object.data
        mw = st.hair_object.matrix_world
        lens = sorted((mw @ me.vertices[e.vertices[0]].co - mw @ me.vertices[e.vertices[1]].co).length
                      for e in me.edges)
        check(lens[len(lens) // 2] > 0.0, "unify: degenerate edges")
        # off -> the old glued locks still build
        st.unify = False
        build.rebuild(g)
        analyse(g, st, "unify/off", allow_tris=True)
        st.unify = True
    finally:
        unify.build_unified = orig


def test_new_features():
    """Head setup, drawing helpers, presets (incl. curl) and bevel topology."""
    from toon_hair_studio import draw, core, presets
    # head + neck + shoulders mesh, like a real character
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.1, location=(0, 0, 1.5), segments=32, ring_count=16)
    head = bpy.context.active_object
    bpy.ops.mesh.primitive_cylinder_add(radius=0.04, depth=0.2, location=(0, 0, 1.35))
    neck = bpy.context.active_object
    bpy.ops.mesh.primitive_cube_add(size=0.4, location=(0, 0, 1.1))
    body = bpy.context.active_object
    for o in (head, neck, body):
        o.select_set(True)
    bpy.context.view_layer.objects.active = head
    bpy.ops.object.join()
    res = bpy.ops.toon_hair.setup_head()
    check(res == {'FINISHED'}, "setup_head failed")
    c = bpy.data.objects[draw.CENTER_NAME]
    sx, sy, sz = c.scale
    check(0.07 < sy < 0.13 and 0.07 < sz < 0.14, f"head fit radius off: {tuple(c.scale)}")
    check(abs(c.location.z - 1.5) < 0.04, f"head fit centre off: {tuple(c.location)}")
    check(bpy.context.scene.toon_hair_head == head, "head not stored")

    # stroke -> anchors
    stroke = [Vector((0, -0.1, 1.6 - i * 0.01)) for i in range(30)]
    ctrl = draw.stroke_to_controls(stroke, max_points=8, min_points=3, spacing=0.035)
    check(3 <= len(ctrl) <= 8 and (ctrl[0] - stroke[0]).length < 1e-6
          and (ctrl[-1] - stroke[-1]).length < 1e-6, "stroke_to_controls wrong ends/count")

    # every preset builds clean topology through the curve workflow
    for key in presets.ORDER:
        bpy.ops.object.mode_set(mode='OBJECT') if bpy.context.mode != 'OBJECT' else None
        res = bpy.ops.toon_hair.add_group(preset=key, edit=False)
        check(res == {'FINISHED'}, f"add_group {key} failed")
        g = bpy.context.active_object
        st = g.toon_hair
        # add a second strand next to the first so connect / mirror kick in
        sp = g.data.splines[0]
        pts = [bp.co.copy() + Vector((0.03, 0, 0)) for bp in sp.bezier_points]
        draw.add_bezier(g.data, pts)
        build.rebuild(g)
        analyse(g, st, f"preset/{key}")
        if key == 'DRILL':
            check(len(st.hair_object.data.vertices) > 300, "curl preset too coarse")

    # every section preset, with front/back layers, puff and tip flip
    g = bpy.data.objects["Hair_BackVolume"]
    st = g.toon_hair
    for sec in th.SECTION_PRESETS:
        for layers in ('BOTH', 'NONE'):
            st.section_preset = sec
            st.layers = layers
            build.rebuild(g)
            analyse(g, st, f"section/{sec}/{layers}")
    st.layers = 'BOTH'
    nb = len(st.hair_object.data.polygons)
    st.layers = 'NONE'
    check(len(st.hair_object.data.polygons) < nb, "layers did not add geometry")
    st.roll = 1.2
    st.tip_flip = 0.2
    build.rebuild(g)
    analyse(g, st, "roll+flip")

    test_manual_links()
    test_pattsun()
    test_live_scope()
    test_uv_seams()
    test_plane_rows_and_weld()
    test_ui_draw()
    test_buried_lock()
    test_attach_stray()
    test_attach_embed()
    test_long_lock_twist()
    test_unify()

    # density layout puts more loops at bends and tips
    prm = core.StrandParams(length_segments=12, tip_bias=0.5, bend_density=1.0)
    f = [1.0] * 65
    lay = core._layout(f, 0.0, 1.0, 10)
    check(abs(lay[5] - 0.5) < 1e-6, "uniform layout not uniform")
    f = [1.0 + (3.0 if i > 48 else 0.0) for i in range(65)]
    lay = core._layout(f, 0.0, 1.0, 10)
    check(lay[-2] > 0.9 and (lay[1] - lay[0]) > (lay[-1] - lay[-2]), "density layout not denser at tip")


def test_reinstall_reload():
    """Updating the add-on without restarting must not keep stale submodules."""
    import types
    import toon_hair_studio as pkg
    pkg.unregister()
    stale = types.ModuleType(pkg.__name__ + ".build")
    sys.modules[pkg.__name__ + ".build"] = stale
    del sys.modules[pkg.__name__]
    import toon_hair_studio as fresh
    check(fresh.build is not stale and hasattr(fresh.build, "selected_ts"),
          "reinstall kept a stale build module")
    fresh.register()


def main():
    th.register()
    bpy.ops.toon_hair.sample_hairstyle()
    groups = [o for o in bpy.data.objects if build.is_group(o)]
    check(len(groups) == 4, "sample should create 4 groups")
    rows = []
    for ob in groups:
        st = ob.toon_hair
        st.outline = True
        for connect, bf, lod, prof, tip, br in itertools.product(
                (False, True), ('CLOSED', 'FLIPPED', 'SINGLE'), ('LOW', 'MID', 'HIGH'),
                ('LENS', 'RIDGE', 'HALF'), ('POINT', 'BLUNT'), (1, 3)):
            th._suspend_update = True
            st.connect, st.backface, st.lod, st.profile, st.tip_mode = connect, bf, lod, prof, tip
            st.branches = br
            st.bevel = 0.0 if lod == 'LOW' else 0.3
            st.connect_style = 'GROOVE' if prof == 'RIDGE' else 'SMOOTH'
            st.normal_mode = 'NONE' if lod == 'HIGH' else 'VOLUME'
            th._suspend_update = False
            st.length_segments, st.profile_segments = th.LOD_TABLE[lod]
            build.rebuild(ob)
            tag = f"{ob.name}/{'conn' if connect else 'indep'}/{bf}/{lod}/{prof}/{tip}/br{br}"
            if tip == 'BLUNT' and bf != 'CLOSED':
                me = st.hair_object.data
                check(all(len(p.vertices) == 4 for p in me.polygons), f"{tag}: blunt should be all quads")
                continue
            v, f, tris = analyse(ob, st, tag)
            if prof == 'LENS':
                rows.append((tag, v, f, tris))
        # restore defaults
        th._suspend_update = True
        st.backface, st.lod, st.profile, st.tip_mode = 'CLOSED', 'MID', 'LENS', 'POINT'
        st.normal_mode = 'VOLUME'
        th._suspend_update = False
    for r in rows:
        if "/MID/" in r[0]:
            print("%-40s verts %5d faces %5d tris %3d" % r)

    test_new_features()

    # live update via depsgraph: move a control point
    ob = bpy.data.objects["Hair_Bangs"]
    hm = ob.toon_hair.hair_object.data
    before = [v.co.copy() for v in hm.vertices]
    ob.data.splines[0].bezier_points[-1].co.z -= 0.02
    ob.data.update_tag()
    bpy.context.view_layer.update()
    moved = max((a.co - b).length for a, b in zip(hm.vertices, before))
    check(moved > 1e-4, "live update via depsgraph handler did not rebuild")

    # bake
    res = bpy.ops.toon_hair.bake('EXEC_DEFAULT', with_outline=True)
    check(res == {'FINISHED'}, "bake failed")
    baked = bpy.data.objects.get("Hair_Baked")
    check(baked is not None and baked.data.has_custom_normals, "baked mesh lacks custom normals")
    check(baked is not None and "TH_pin" in baked.vertex_groups, "baked mesh lacks TH_pin")

    test_reinstall_reload()

    if FAILS:
        print("\n".join(FAILS[:40]))
        print(f"FAILED: {len(FAILS)} checks")
        sys.exit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
