"""Render blunt-cut ('ぱっつん') bangs on the sample hairstyle.

    python tests/render_pattsun.py [out_dir]
"""
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_preview as rp  # noqa: E402
from toon_hair_studio import build, draw  # noqa: E402


def main():
    out = sys.argv[-1] if len(sys.argv) > 1 and not sys.argv[-1].endswith(".py") else rp.OUT
    os.makedirs(out, exist_ok=True)
    rp.OUT = out
    sc, cam, c = rp.setup_scene()
    center = bpy.data.objects[draw.CENTER_NAME]
    old = bpy.data.objects["Hair_Bangs"]
    g = draw.new_group(bpy.context, 'PATTSUN', center)
    for sp in old.data.splines:
        draw.add_bezier(g.data, [old.matrix_world @ bp.co for bp in sp.bezier_points])
    for o in (old.toon_hair.hair_object, old.toon_hair.outline_object, old):
        if o is not None:
            bpy.data.objects.remove(o, do_unlink=True)
    g.toon_hair.outline = True
    build.rebuild(g)
    rp.shoot(sc, cam, c, (0.25, -0.98, 0.05), "pattsun_front.png")
    rp.topology_mode(True)
    rp.shoot(sc, cam, c, (0.25, -0.98, 0.05), "pattsun_topology.png")


if __name__ == "__main__":
    main()
