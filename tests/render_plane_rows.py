"""Render two joined guides of different lengths with/without plane-shared rows.

    python tests/render_plane_rows.py [out_dir]
"""
import os
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_preview as rp  # noqa: E402
from toon_hair_studio import build, draw  # noqa: E402


def main():
    out = sys.argv[-1] if len(sys.argv) > 1 and not sys.argv[-1].endswith(".py") else rp.OUT
    os.makedirs(out, exist_ok=True)
    rp.OUT = out
    sc, cam, c = rp.setup_scene()
    for o in [o for o in bpy.data.objects if build.is_group(o)]:
        for m in (o.toon_hair.hair_object, o.toon_hair.outline_object, o):
            if m is not None:
                bpy.data.objects.remove(m, do_unlink=True)
    center = bpy.data.objects[draw.CENTER_NAME]
    g = draw.new_group(bpy.context, 'CUSTOM', center)
    st = g.toon_hair
    st.width, st.root_ratio, st.bulge_pos, st.tip_shape = 0.045, 0.8, 0.3, 1.6
    st.connect, st.split, st.connect_ratio = True, 0.7, 3.0
    inv = g.matrix_world.inverted()
    for dx, length in ((-0.024, 0.11), (0.024, 0.2)):
        pts = [c + Vector((dx, -0.107 - 0.012 * k, 0.06 - length * k / 4)) for k in range(5)]
        draw.add_bezier(g.data, [inv @ p for p in pts])
    rp.topology_mode(True)
    files = []
    for plane in (False, True):
        st.plane_rows = plane
        build.rebuild(g)
        cam.location = c + Vector((0.05, -0.8, -0.04))
        rp.look_at(cam, c + Vector((0.0, -0.13, -0.06)))
        sc.render.filepath = os.path.join(out, f"plane_rows_{int(plane)}.png")
        bpy.ops.render.render(write_still=True)
        files.append(sc.render.filepath)
    return files


if __name__ == "__main__":
    main()
