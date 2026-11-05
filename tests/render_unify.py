"""Messy hand-drawn bangs (uneven gaps, overlaps, a buried short lock),
glued locks (unify off) vs one unified body (unify on).

    python tests/render_unify.py [out_dir]
"""
import os
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_preview as rp  # noqa: E402
from toon_hair_studio import build, draw  # noqa: E402

# (root dx, end dx, length scale, width radius)
LOCKS = [(-0.07, -0.085, 0.95, 1.0), (-0.045, -0.05, 1.05, 1.1), (-0.035, -0.02, 0.6, 0.6),
         (-0.012, -0.012, 1.0, 1.0), (0.004, 0.022, 1.1, 1.2), (0.03, 0.045, 0.85, 0.9),
         (0.05, 0.08, 1.0, 1.0)]


def make_scene():
    sc, cam, c = rp.setup_scene()
    for o in [o for o in bpy.data.objects if build.is_group(o)]:
        for m in (o.toon_hair.hair_object, o.toon_hair.outline_object, o):
            if m is not None:
                bpy.data.objects.remove(m, do_unlink=True)
    center = bpy.data.objects[draw.CENTER_NAME]
    g = draw.new_group(bpy.context, 'BANGS', center)
    st = g.toon_hair
    inv = g.matrix_world.inverted()
    for rx, ex, ls, rad in LOCKS:
        pts = [c + Vector((rx * 0.5, -0.03, 0.1)), c + Vector((rx * 0.8, -0.085, 0.07)),
               c + Vector(((rx + ex) * 0.5, -0.105, 0.02 - 0.03 * (ls - 1))),
               c + Vector((ex, -0.11, -0.03 - 0.06 * (ls - 0.8)))]
        sp = draw.add_bezier(g.data, [inv @ p for p in pts])
        for bp in sp.bezier_points:
            bp.radius = rad
    st.outline = True
    build.rebuild(g)
    return sc, cam, c, g


def main():
    out = sys.argv[-1] if len(sys.argv) > 1 and not sys.argv[-1].endswith(".py") else rp.OUT
    os.makedirs(out, exist_ok=True)
    rp.OUT = out
    sc, cam, c, g = make_scene()
    for unify in (False, True):
        g.toon_hair.unify = unify
        build.rebuild(g)
        for topo in (False, True):
            rp.topology_mode(topo)
            cam.location = c + Vector((0.1, -0.55, 0.08))
            rp.look_at(cam, c + Vector((0.0, -0.08, 0.02)))
            sc.render.filepath = os.path.join(out, f"unify_{int(unify)}_{int(topo)}.png")
            bpy.ops.render.render(write_still=True)


if __name__ == "__main__":
    main()
