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


def analyse(ob, st, tag):
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
            if not (fan or ring):
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

    # density layout puts more loops at bends and tips
    prm = core.StrandParams(length_segments=12, tip_bias=0.5, bend_density=1.0)
    f = [1.0] * 65
    lay = core._layout(f, 0.0, 1.0, 10)
    check(abs(lay[5] - 0.5) < 1e-6, "uniform layout not uniform")
    f = [1.0 + (3.0 if i > 48 else 0.0) for i in range(65)]
    lay = core._layout(f, 0.0, 1.0, 10)
    check(lay[-2] > 0.9 and (lay[1] - lay[0]) > (lay[-1] - lay[-2]), "density layout not denser at tip")


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

    if FAILS:
        print("\n".join(FAILS[:40]))
        print(f"FAILED: {len(FAILS)} checks")
        sys.exit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
