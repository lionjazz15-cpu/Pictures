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
            if not any(vsizes[v] == {3} for v in p.vertices):
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
                ('LENS', 'RIDGE', 'ROUND'), ('POINT', 'BLUNT'), (1, 3)):
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
            if tip == 'BLUNT':
                # blunt closed tips use one cap n-gon per lock by design
                me = st.hair_object.data
                if bf == 'CLOSED':
                    continue
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
