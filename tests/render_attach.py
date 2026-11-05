"""3 linked locks + a thin stray stuck into the middle one, before/after attach.

    python tests/render_attach.py [out_dir]
"""
import os
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_preview as rp  # noqa: E402
from toon_hair_studio import build, draw  # noqa: E402


def make_scene():
    sc, cam, c = rp.setup_scene()
    for o in [o for o in bpy.data.objects if build.is_group(o)]:
        for m in (o.toon_hair.hair_object, o.toon_hair.outline_object, o):
            if m is not None:
                bpy.data.objects.remove(m, do_unlink=True)
    center = bpy.data.objects[draw.CENTER_NAME]
    g = draw.new_group(bpy.context, 'CUSTOM', center)
    st = g.toon_hair
    st.width, st.root_ratio, st.bulge_pos, st.tip_shape = 0.045, 0.5, 0.35, 1.6
    st.outline = True
    inv = g.matrix_world.inverted()
    root = c + Vector((0.0, -0.04, 0.095))
    for dx in (-0.032, 0.0, 0.032):
        pts = [root + Vector((dx * 0.3, 0, 0)), c + Vector((dx * 0.6, -0.088, 0.06)),
               c + Vector((dx, -0.106, 0.0)), c + Vector((dx * 1.15, -0.11, -0.06)),
               c + Vector((dx * 1.3, -0.105, -0.12))]
        draw.add_bezier(g.data, [inv @ p for p in pts])
    for a, b in ((0, 1), (1, 2)):
        l = st.links.add()
        l.a, l.b, l.t0, l.t1 = a, b, 0.0, 0.6
    # thin stray stuck into the middle lock, coming out to the right
    stray = [c + Vector((0.004, -0.095, 0.045)), c + Vector((0.01, -0.108, 0.0)),
             c + Vector((0.03, -0.13, -0.05)), c + Vector((0.06, -0.15, -0.09))]
    sp = draw.add_bezier(g.data, [inv @ p for p in stray])
    for bp in sp.bezier_points:
        bp.radius = 0.4
    build.rebuild(g)
    return sc, cam, c, g


def main():
    out = sys.argv[-1] if len(sys.argv) > 1 and not sys.argv[-1].endswith(".py") else rp.OUT
    os.makedirs(out, exist_ok=True)
    rp.OUT = out
    sc, cam, c, g = make_scene()
    files = []
    mode = os.environ.get("TH_ATTACH_MODE", "EMBED")
    for stage in ("before", "after"):
        if stage == "after":
            for o in bpy.context.selected_objects:
                o.select_set(False)
            g.select_set(True)
            bpy.context.view_layer.objects.active = g
            print("attach:", bpy.ops.toon_hair.attach(mode=mode), "links", len(g.toon_hair.links))
        for topo in (False, True):
            rp.topology_mode(topo)
            cam.location = c + Vector((0.12, -0.6, 0.02))
            rp.look_at(cam, c + Vector((0.0, -0.1, -0.01)))
            sc.render.filepath = os.path.join(out, f"attach_{stage}_{int(topo)}.png")
            bpy.ops.render.render(write_still=True)
            files.append(sc.render.filepath)
    return files


if __name__ == "__main__":
    main()
