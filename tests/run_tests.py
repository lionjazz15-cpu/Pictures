"""Headless tests.

    blender -b --factory-startup -P tests/run_tests.py
or, with the `bpy` module from PyPI:
    python tests/run_tests.py
"""
import os
import sys

import bpy
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import precision_shrinkwrap as psw  # noqa: E402
from precision_shrinkwrap import mesh_utils as mu  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        FAILS.append(name)


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if not hasattr(bpy.types.Scene, "precision_shrinkwrap"):
        psw.register()


def make_body(subdiv=1):
    """Closed blob with a deep, narrow crease (like a buttock crease) on +Y."""
    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32, radius=0.2)
    body = bpy.context.active_object
    body.name = "Body"
    P = mu.mesh_coords(body.data)
    x, y = P[:, 0], P[:, 1]
    k = 1.0 - 0.45 * np.exp(-(x / 0.03) ** 2) * np.clip(y / 0.2, 0, 1)
    P[:, 0] *= 1.0 + 0.1 * np.clip(y / 0.2, 0, 1)
    P[:, 1] *= k
    mu.set_mesh_coords(body.data, P)
    bpy.ops.object.shade_smooth()
    if subdiv:
        m = body.modifiers.new("Subdivision", "SUBSURF")
        m.levels = subdiv
    return body


def make_garment(res=(48, 24), scale=1.12):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=res[0], ring_count=res[1], radius=0.2 * scale)
    g = bpy.context.active_object
    g.name = "Garment"
    # remove the bottom cap to get an open garment with a boundary
    P = mu.mesh_coords(g.data)
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(g.data)
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if v.co.z < -0.12], context="VERTS")
    bm.to_mesh(g.data)
    bm.free()
    return g


def edge_stats(obj, P):
    e = np.empty(len(obj.data.edges) * 2, dtype=np.int64)
    obj.data.edges.foreach_get("vertices", e)
    e = e.reshape(-1, 2)
    L = np.linalg.norm(P[e[:, 0]] - P[e[:, 1]], axis=1)
    return L


def stretch(obj, P_before, P_after):
    r = edge_stats(obj, P_after) / np.maximum(edge_stats(obj, P_before), 1e-12)
    r /= np.median(r)
    return float(r.max()), float(r.min())


def eval_world(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    P = mu.to_world(mu.mesh_coords(me), obj.matrix_world)
    ev.to_mesh_clear()
    return P


def test_fit_vs_stock():
    reset()
    body = make_body()
    g = make_garment()
    s = bpy.context.scene.precision_shrinkwrap
    s.target = body
    s.offset = 0.003
    s.output = "APPLY"
    P0 = mu.mesh_coords(g.data)

    # stock shrinkwrap for comparison
    g2 = g.copy()
    g2.data = g.data.copy()
    bpy.context.scene.collection.objects.link(g2)
    m = g2.modifiers.new("SW", "SHRINKWRAP")
    m.target = body
    m.offset = 0.003
    stock = eval_world(g2)

    bpy.ops.object.select_all(action="DESELECT")
    g.select_set(True)
    bpy.context.view_layer.objects.active = g
    assert bpy.ops.precision_shrinkwrap.fit() == {"FINISHED"}
    ours = mu.mesh_coords(g.data)

    surf = mu.target_surface(bpy.context, body)
    _, _, sd_ours = surf.signed(ours)
    _, _, sd_stock = surf.signed(stock)
    dev = np.abs(sd_ours - 0.003)
    st_ours = stretch(g, P0, ours)
    st_stock = stretch(g, P0, stock)
    print(f"   offset deviation: ours max {dev.max():.2e}  stock max {np.abs(sd_stock - 0.003).max():.2e}")
    print(f"   edge stretch (max/min vs median): ours {st_ours[0]:.2f}/{st_ours[1]:.2f}  "
          f"stock {st_stock[0]:.2f}/{st_stock[1]:.2f}")
    check("fit: offset exact", dev.max() < 1e-4, f"{dev.max():.2e}")
    check("fit: nothing inside the body", (sd_ours > 0).all())
    check("fit: less stretch than stock shrinkwrap", st_ours[0] < st_stock[0] and st_ours[1] > st_stock[1])
    check("fit: no collapsed edges", st_ours[1] > 0.2, f"{st_ours[1]:.2f}")



def test_tension():
    """Valley tension: bridges the crease and is never closer than the offset."""
    reset()
    body = make_body()
    g = make_garment()
    s = bpy.context.scene.precision_shrinkwrap
    s.target = body
    s.offset = 0.003
    s.output = "APPLY"
    bpy.ops.object.select_all(action="DESELECT")
    g.select_set(True)
    P0 = mu.mesh_coords(g.data)
    assert bpy.ops.precision_shrinkwrap.fit() == {"FINISHED"}
    follow = mu.mesh_coords(g.data)
    mu.set_mesh_coords(g.data, P0)
    s.tension = 20
    assert bpy.ops.precision_shrinkwrap.fit() == {"FINISHED"}
    surf = mu.target_surface(bpy.context, body)
    _, _, sd_f = surf.signed(follow)
    _, _, sd_t = surf.signed(mu.mesh_coords(g.data))
    check("tension: stays outside the offset", sd_t.min() > 0.003 - 1e-4, f"{sd_t.min():.5f}")
    check("tension: bridges the valley", sd_t.max() > sd_f.max() * 1.5, f"{sd_t.max():.4f}")


def test_fit_subdivided_garment():
    # (resolution, label, allowed p99 deviation): the penetration guard may
    # leave a coarse cage slightly too far out, never too close
    for res, label, allowed in (((24, 12), "coarse cage", 1.5e-3), ((48, 24), "dense cage", 6e-4)):
        reset()
        body = make_body()
        g = make_garment(res=res)
        g.modifiers.new("Subdivision", "SUBSURF").levels = 2
        s = bpy.context.scene.precision_shrinkwrap
        s.target = body
        s.offset = 0.004
        s.output = "APPLY"
        bpy.ops.object.select_all(action="DESELECT")
        g.select_set(True)
        assert bpy.ops.precision_shrinkwrap.fit() == {"FINISHED"}
        surf = mu.target_surface(bpy.context, body)
        W = eval_world(g)
        _, _, sd = surf.signed(W)
        # a cage cannot follow a crease narrower than its faces; it has to
        # bridge it, so measure the accuracy away from the crease
        away = (np.abs(W[:, 0]) > 0.03 + 2.5 * 0.4 * np.pi / res[0]) | (W[:, 1] < 0.0)
        dev = np.abs(sd - 0.004)[away]
        check(f"subdiv garment ({label}): subdivided result at offset", np.percentile(dev, 99) < allowed,
              f"p99 {np.percentile(dev, 99):.2e} max {dev.max():.2e}")
        check(f"subdiv garment ({label}): nothing closer than the offset", sd.min() > 0.004 - 5e-5,
              f"min {sd.min():.4f}")


def test_fit_mirrored_garment():
    reset()
    import bmesh
    body = make_body()
    g = make_garment(res=(32, 16))
    bm = bmesh.new()
    bm.from_mesh(g.data)
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if v.co.x < -1e-6], context="VERTS")
    bm.to_mesh(g.data)
    bm.free()
    g.modifiers.new("Mirror", "MIRROR").use_clip = True
    g.modifiers.new("Subdivision", "SUBSURF").levels = 1
    seam = np.abs(mu.mesh_coords(g.data)[:, 0]) < 1e-6
    s = bpy.context.scene.precision_shrinkwrap
    s.target = body
    s.offset = 0.003
    s.output = "APPLY"
    bpy.ops.object.select_all(action="DESELECT")
    g.select_set(True)
    assert bpy.ops.precision_shrinkwrap.fit() == {"FINISHED"}
    surf = mu.target_surface(bpy.context, body)
    W = eval_world(g)
    _, _, sd = surf.signed(W)
    away = (np.abs(W[:, 0]) > 0.03 + 2.5 * 0.4 * np.pi / 32) | (W[:, 1] < 0.0)
    dev = np.abs(sd - 0.003)[away]
    check("mirror garment: seam stays on the mirror plane", np.abs(mu.mesh_coords(g.data)[seam, 0]).max() < 1e-6)
    check("mirror garment: nothing closer than the offset", sd.min() > 0.003 - 5e-5, f"min {sd.min():.4f}")
    check("mirror garment: result at offset", np.percentile(dev, 99) < 1e-3, f"p99 {np.percentile(dev, 99):.2e}")


def select_back_faces(body):
    me = body.data
    c = np.empty(len(me.polygons) * 3)
    me.polygons.foreach_get("center", c)
    sel = c.reshape(-1, 3)[:, 1] > 0.02
    me.polygons.foreach_set("select", sel)
    return sel


def test_transfer_applied_exact():
    reset()
    body = make_body(subdiv=2)
    select_back_faces(body)
    s = bpy.context.scene.precision_shrinkwrap
    s.target = body
    s.offset = 0.0
    s.transfer_mode = "APPLIED"
    assert bpy.ops.precision_shrinkwrap.transfer_topology() == {"FINISHED"}
    wear = bpy.context.active_object
    B = eval_world(body)
    W = mu.to_world(mu.mesh_coords(wear.data), wear.matrix_world)
    # every wear vertex must coincide exactly with a subdivided body vertex
    from mathutils.kdtree import KDTree
    kd = KDTree(len(B))
    for i, p in enumerate(B):
        kd.insert(p, i)
    kd.balance()
    d = np.array([kd.find(p)[2] for p in W])
    check("applied offset 0: vertices coincide exactly", d.max() == 0.0, f"max {d.max():.1e}")
    check("applied: no subdivision modifier", not any(m.type == "SUBSURF" for m in wear.modifiers))

    s.offset = 0.002
    bpy.ops.object.select_all(action="DESELECT")
    bpy.context.view_layer.objects.active = body
    assert bpy.ops.precision_shrinkwrap.transfer_topology() == {"FINISHED"}
    wear2 = bpy.context.active_object
    surf = mu.target_surface(bpy.context, body)
    W2 = mu.to_world(mu.mesh_coords(wear2.data), wear2.matrix_world)
    _, _, sd = surf.signed(W2)
    check("applied offset: exact offset", np.abs(sd - 0.002).max() < 1e-4, f"{np.abs(sd - 0.002).max():.2e}")
    check("applied offset: same topology", len(wear2.data.vertices) == len(wear.data.vertices))
    # vertices stay above their own body vertex (edge flow preserved)
    shift = np.linalg.norm(W2 - W, axis=1)
    check("applied offset: vertices stay above their body vertex",
          np.percentile(shift, 95) < 0.002 * 2.0, f"p95 {np.percentile(shift, 95):.4f}")


def test_transfer_subdiv():
    reset()
    body = make_body(subdiv=2)
    select_back_faces(body)
    s = bpy.context.scene.precision_shrinkwrap
    s.target = body
    s.offset = 0.002
    s.transfer_mode = "SUBDIV"
    assert bpy.ops.precision_shrinkwrap.transfer_topology() == {"FINISHED"}
    wear = bpy.context.active_object
    check("subdiv transfer: keeps subdivision modifier", any(m.type == "SUBSURF" for m in wear.modifiers))
    surf = mu.target_surface(bpy.context, body)
    _, _, sd = surf.signed(eval_world(wear))
    dev = np.abs(sd - 0.002)
    check("subdiv transfer: subdivided result at offset", np.percentile(dev, 99) < 3e-4,
          f"p99 {np.percentile(dev, 99):.2e} max {dev.max():.2e}")
    check("subdiv transfer: nothing inside", (sd > 0).all(), f"min {sd.min():.4f}")


def _nearest_distances(A, B):
    from mathutils.kdtree import KDTree
    kd = KDTree(len(B))
    for i, p in enumerate(B):
        kd.insert(p, i)
    kd.balance()
    return np.array([kd.find(p)[2] for p in A])


def test_transfer_copy_only():
    """Fit to Body off: the topology is copied as is, even with an offset set."""
    reset()
    body = make_body(subdiv=2)
    sel = select_back_faces(body)
    s = bpy.context.scene.precision_shrinkwrap
    s.target = body
    s.offset = 0.01
    s.transfer_fit = False

    s.transfer_mode = "SUBDIV"
    assert bpy.ops.precision_shrinkwrap.transfer_topology() == {"FINISHED"}
    wear = bpy.context.active_object
    cage = mu.mesh_coords(wear.data)
    d = _nearest_distances(cage, mu.mesh_coords(body.data))
    check("copy only (subdiv): cage identical to the body cage", d.max() == 0.0, f"max {d.max():.1e}")
    n_region = len({v for p, k in zip(body.data.polygons, sel) if k for v in p.vertices})
    check("copy only (subdiv): region vertex count", len(cage) == n_region, f"{len(cage)} / {n_region}")
    check("copy only (subdiv): keeps subdivision modifier", any(m.type == "SUBSURF" for m in wear.modifiers))

    s.transfer_mode = "APPLIED"
    bpy.ops.object.select_all(action="DESELECT")
    bpy.context.view_layer.objects.active = body
    assert bpy.ops.precision_shrinkwrap.transfer_topology() == {"FINISHED"}
    wear2 = bpy.context.active_object
    d = _nearest_distances(mu.to_world(mu.mesh_coords(wear2.data), wear2.matrix_world), eval_world(body))
    check("copy only (applied): vertices coincide with the subdivided body", d.max() == 0.0, f"max {d.max():.1e}")


def make_torso(half=False):
    """Torso with two breasts and a deep cleavage; optionally a half with Mirror."""
    import bmesh
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24, radius=1)
    t = bpy.context.active_object
    t.scale = (0.14, 0.095, 0.32)
    bpy.ops.object.transform_apply(scale=True)
    for x in (-0.066, 0.066):
        bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=0.066, location=(x, -0.072, 0.1))
    for o in bpy.context.scene.objects:
        o.select_set(True)
    bpy.context.view_layer.objects.active = t
    bpy.ops.object.join()
    body = bpy.context.active_object
    body.name = "Body"
    r = body.modifiers.new("R", "REMESH")
    r.voxel_size = 0.01
    bpy.ops.object.modifier_apply(modifier="R")
    sm = body.modifiers.new("S", "SMOOTH")
    sm.factor, sm.iterations = 0.5, 4
    bpy.ops.object.modifier_apply(modifier="S")
    if half:
        bm = bmesh.new()
        bm.from_mesh(body.data)
        bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], plane_co=(0, 0, 0),
                               plane_no=(1, 0, 0), clear_inner=True)
        for v in bm.verts:
            if abs(v.co.x) < 1e-5:
                v.co.x = 0.0
        bm.to_mesh(body.data)
        bm.free()
        body.modifiers.new("Mirror", "MIRROR").use_clip = True
    body.modifiers.new("Subdivision", "SUBSURF").levels = 1
    return body


def make_rough_band():
    """A quickly blocked-out 10 x 3 band around the chest, well off the body."""
    import bmesh
    bpy.ops.mesh.primitive_cylinder_add(vertices=10, radius=1, depth=1, end_fill_type="NOTHING")
    g = bpy.context.active_object
    g.name = "RoughCage"
    bm = bmesh.new()
    bm.from_mesh(g.data)
    bmesh.ops.subdivide_edges(bm, edges=[e for e in bm.edges if abs(e.verts[0].co.z - e.verts[1].co.z) > 0.5],
                              cuts=2)
    bm.to_mesh(g.data)
    bm.free()
    g.scale = (0.2, 0.2, 0.12)
    g.location = (0, 0, 0.09)
    bpy.ops.object.transform_apply(location=True, scale=True)
    g.data.materials.append(bpy.data.materials.new("Fabric"))
    return g


def border_loops(obj):
    """Number of open border loops of the evaluated object."""
    import bmesh
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(obj.evaluated_get(dg), depsgraph=dg)
    bm = bmesh.new()
    bm.from_mesh(me)
    edges = {e for e in bm.edges if e.is_boundary}
    loops = 0
    while edges:
        loops += 1
        stack = [edges.pop()]
        while stack:
            for v in stack.pop().verts:
                for e in v.link_edges:
                    if e in edges:
                        edges.remove(e)
                        stack.append(e)
    bm.free()
    bpy.data.meshes.remove(me)
    return loops


def test_rough_cage():
    for half in (False, True):
        for mode in ("APPLIED", "SUBDIV"):
            label = f"rough cage ({mode.lower()}, {'mirrored half body' if half else 'full body'})"
            reset()
            body = make_torso(half)
            cage = make_rough_band()
            s = bpy.context.scene.precision_shrinkwrap
            s.target = body
            s.offset = 0.001
            s.transfer_mode = mode
            bpy.ops.object.select_all(action="DESELECT")
            cage.select_set(True)
            bpy.context.view_layer.objects.active = cage
            assert bpy.ops.precision_shrinkwrap.conform_rough_cage() == {"FINISHED"}
            w = bpy.context.active_object
            surf = mu.target_surface(bpy.context, body)
            W = eval_world(w)
            _, _, sd = surf.signed(W)
            dev = np.abs(sd - 0.001)
            check(f"{label}: exact offset", np.percentile(dev, 99) < 1.5e-4,
                  f"p99 {np.percentile(dev, 99):.1e} max {dev.max():.1e}")
            check(f"{label}: nothing inside", sd.min() > 0.0009, f"min {sd.min():.5f}")
            # covers the band's height all the way round (both sides when mirrored)
            check(f"{label}: covers the band", W[:, 2].min() < 0.05 and W[:, 2].max() > 0.12
                  and W[:, 0].min() < -0.12 and W[:, 0].max() > 0.12,
                  f"z {W[:, 2].min():.3f}..{W[:, 2].max():.3f}")
            check(f"{label}: one piece without holes (2 border loops)", border_loops(w) == 2,
                  f"{border_loops(w)} loops")
            check(f"{label}: cage material, cage hidden",
                  w.data.materials[0].name == "Fabric" and cage.hide_get())
            if half:
                check(f"{label}: output is a half with Mirror",
                      any(m.type == "MIRROR" for m in w.modifiers)
                      and mu.mesh_coords(w.data)[:, 0].min() > -1e-6)


def make_hips():
    """Hips with buttocks and two legs (a crotch and a buttock crease)."""
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24, radius=1)
    t = bpy.context.active_object
    t.scale, t.location = (0.16, 0.11, 0.3), (0, 0, 0.45)
    bpy.ops.object.transform_apply(location=True, scale=True)
    for x in (-0.08, 0.08):
        bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=0.07, depth=0.75, location=(x, 0, -0.02))
    for x in (-0.07, 0.07):
        bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=0.085, location=(x, 0.045, 0.3))
    for o in bpy.context.scene.objects:
        o.select_set(True)
    bpy.context.view_layer.objects.active = t
    bpy.ops.object.join()
    body = bpy.context.active_object
    body.name = "Body"
    r = body.modifiers.new("R", "REMESH")
    r.voxel_size = 0.01
    bpy.ops.object.modifier_apply(modifier="R")
    sm = body.modifiers.new("S", "SMOOTH")
    sm.factor, sm.iterations = 0.5, 6
    bpy.ops.object.modifier_apply(modifier="S")
    body.modifiers.new("Subdivision", "SUBSURF").levels = 1
    return body


def test_rough_cage_hips():
    """A shorts-like cage around the hips: no strips running down a leg, no
    slit along the crotch."""
    import bmesh
    reset()
    body = make_hips()
    bpy.ops.mesh.primitive_cylinder_add(vertices=10, radius=1, depth=1, end_fill_type="NOTHING")
    cage = bpy.context.active_object
    bm = bmesh.new()
    bm.from_mesh(cage.data)
    bmesh.ops.subdivide_edges(bm, edges=[e for e in bm.edges if abs(e.verts[0].co.z - e.verts[1].co.z) > 0.5],
                              cuts=2)
    bm.to_mesh(cage.data)
    bm.free()
    cage.scale, cage.location = (0.25, 0.2, 0.2), (0, 0, 0.32)
    bpy.ops.object.transform_apply(location=True, scale=True)
    s = bpy.context.scene.precision_shrinkwrap
    s.target = body
    s.offset = 0.001
    s.transfer_mode = "APPLIED"
    bpy.ops.object.select_all(action="DESELECT")
    cage.select_set(True)
    bpy.context.view_layer.objects.active = cage
    assert bpy.ops.precision_shrinkwrap.conform_rough_cage() == {"FINISHED"}
    w = bpy.context.active_object
    W = eval_world(w)
    check("rough cage hips: stays within the cage height", W[:, 2].min() > 0.2 and W[:, 2].max() < 0.44,
          f"z {W[:, 2].min():.3f}..{W[:, 2].max():.3f} (cage 0.22..0.42)")
    check("rough cage hips: no holes", border_loops(w) <= 3, f"{border_loops(w)} loops")


def test_slide_and_retransfer():
    reset()
    body = make_torso(True)
    cage = make_rough_band()
    s = bpy.context.scene.precision_shrinkwrap
    s.target = body
    s.offset = 0.001
    s.transfer_mode = "APPLIED"
    bpy.ops.object.select_all(action="DESELECT")
    cage.select_set(True)
    bpy.context.view_layer.objects.active = cage
    assert bpy.ops.precision_shrinkwrap.conform_rough_cage() == {"FINISHED"}
    w = bpy.context.active_object
    name = w.name
    top_before = eval_world(w)[:, 2].max()

    assert bpy.ops.precision_shrinkwrap.slide_start() == {"FINISHED"}
    check("slide: edit mode with slide modifier", bpy.context.mode == "EDIT_MESH"
          and w.modifiers[0].name == "PSW Slide")
    bpy.ops.object.mode_set(mode="OBJECT")
    # pull the top border up by 2 cm (the modifier keeps it on the body)
    P = mu.mesh_coords(w.data)
    P[P[:, 2] > top_before - 0.01, 2] += 0.02
    mu.set_mesh_coords(w.data, P)
    bpy.ops.object.mode_set(mode="EDIT")
    assert bpy.ops.precision_shrinkwrap.slide_confirm() == {"FINISHED"}
    w2 = bpy.context.active_object
    W = eval_world(w2)
    _, _, sd = mu.target_surface(bpy.context, body).signed(W)
    check("slide: garment rebuilt under the same name, material kept",
          w2.name == name and w2.data.materials[0].name == "Fabric")
    check("slide: no slide modifier left", all(m.name != "PSW Slide" for m in w2.modifiers))
    check("slide: new border follows the edit", W[:, 2].max() > top_before + 0.012,
          f"top {top_before:.3f} -> {W[:, 2].max():.3f}")
    check("slide: exact offset again", np.abs(sd - 0.001).max() < 1e-4, f"{np.abs(sd - 0.001).max():.1e}")


def _set_live(obj, value):
    m = obj.modifiers["PSW Offset"]
    m[mu.live_offset_identifier(m.node_group)] = value
    obj.update_tag()


def test_live_offset():
    for mode, fit in (("APPLIED", True), ("SUBDIV", True), ("APPLIED", False)):
        label = f"live offset ({mode.lower()}{'' if fit else ', copy only'})"
        reset()
        body = make_torso(True)
        cage = make_rough_band()
        s = bpy.context.scene.precision_shrinkwrap
        s.target = body
        s.offset = 0.001
        s.transfer_mode = mode
        s.transfer_fit = fit
        bpy.ops.object.select_all(action="DESELECT")
        cage.select_set(True)
        bpy.context.view_layer.objects.active = cage
        assert bpy.ops.precision_shrinkwrap.conform_rough_cage() == {"FINISHED"}
        w = bpy.context.active_object
        surf = mu.target_surface(bpy.context, body)
        check(f"{label}: modifier added first", w.modifiers[0].name == "PSW Offset")
        before = eval_world(w)
        _set_live(w, 0.001 if fit else 0.0)
        same = np.abs(eval_world(w) - before).max()
        check(f"{label}: unchanged at the fitted value", same < 1e-6, f"{same:.1e}")
        _set_live(w, 0.003)
        _, _, sd = surf.signed(eval_world(w))
        dev = np.abs(sd - 0.003)
        allowed = 3e-4 if mode == "SUBDIV" else 1.5e-4
        check(f"{label}: follows the slider (3 mm)", np.percentile(dev, 99) < allowed and sd.min() > 0.0025,
              f"p99 {np.percentile(dev, 99):.1e} min {sd.min():.4f}")


def test_errors_are_explained():
    reset()
    body = make_body(subdiv=1)
    body.data.polygons.foreach_set("select", np.zeros(len(body.data.polygons), dtype=bool))
    s = bpy.context.scene.precision_shrinkwrap
    s.target = body
    s.region = "SELECTED"
    bpy.context.view_layer.objects.active = body
    try:
        bpy.ops.precision_shrinkwrap.transfer_topology()
        msg = ""
    except RuntimeError as e:
        msg = str(e)
    check("no selection: error tells what to do", "Edit Mode" in msg and "Rough Cage" in msg, msg[-120:])


if __name__ == "__main__":
    for t in (test_fit_vs_stock, test_tension, test_fit_subdivided_garment, test_fit_mirrored_garment,
              test_transfer_applied_exact, test_transfer_subdiv, test_transfer_copy_only,
              test_rough_cage, test_rough_cage_hips, test_slide_and_retransfer, test_live_offset, test_errors_are_explained):
        print(f"== {t.__name__}")
        t()
    print("FAILED: " + ", ".join(FAILS) if FAILS else "ALL PASSED")
    sys.stdout.flush()
    # The PyPI bpy module can crash while shutting down once an add-on has
    # registered operators (even a trivial one); exit directly instead.
    os._exit(1 if FAILS else 0)
