"""Render the layered 'back hair (volume)' preset and the section presets.

    python tests/render_volume.py [out_dir]
"""
import math
import os
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_preview as rp  # noqa: E402
from toon_hair_studio import build, draw  # noqa: E402
import toon_hair_studio as th  # noqa: E402


def main():
    out = sys.argv[-1] if len(sys.argv) > 1 and not sys.argv[-1].endswith(".py") else rp.OUT
    os.makedirs(out, exist_ok=True)
    rp.OUT = out
    sc, cam, c = rp.setup_scene()
    center = bpy.data.objects[draw.CENTER_NAME]
    # replace the sample back hair with the layered preset on the same guides
    old = bpy.data.objects["Hair_Back"]
    g = draw.new_group(bpy.context, 'BACK_VOLUME', center)
    for sp in old.data.splines:
        draw.add_bezier(g.data, [old.matrix_world @ bp.co for bp in sp.bezier_points])
    old.toon_hair.hair_object.hide_render = True
    for o in [o for o in bpy.data.objects if build.is_group(o)]:
        o.toon_hair.outline = True
    build.rebuild(g)
    for layers in ('NONE', 'BOTH'):
        g.toon_hair.layers = layers
        rp.shoot(sc, cam, c, (-0.6, 0.8, 0.05), f"volume_back_{layers.lower()}.png")
        rp.shoot(sc, cam, c, (-0.95, 0.15, -0.05), f"volume_side_{layers.lower()}.png")

    # section presets: one blunt lock each, seen from the tip
    for o in bpy.data.objects:
        if o.type == 'MESH':
            o.hide_render = True
    names = list(th.SECTION_PRESETS)
    locks = []
    for i, key in enumerate(names):
        s = draw.new_group(bpy.context, 'CUSTOM', center)
        st = s.toon_hair
        st.section_preset = key
        st.width, st.tip_mode, st.tip_min = 0.05, 'BLUNT', 1.0
        st.root_ratio, st.bulge_pos, st.normal_mode, st.outline = 1.0, 0.0, 'NONE', False
        st.lod, st.length_segments, st.profile_segments = 'CUSTOM', 4, 6
        x = (i - (len(names) - 1) / 2) * 0.075
        inv = s.matrix_world.inverted()
        draw.add_bezier(s.data, [inv @ (c + Vector((x, -0.2, 0.06 - 0.04 * k))) for k in range(3)])
        build.rebuild(s)
        locks.append(s)
    rp.topology_mode(True)
    for o in bpy.data.objects:
        if o.type == 'MESH':
            o.hide_render = True
    for s in locks:
        s.toon_hair.hair_object.hide_render = False
    target = c + Vector((0.0, -0.2, -0.04))
    cam.location = target + Vector((0.0, -1.25, -1.0))
    rp.look_at(cam, target)
    sc.render.resolution_y = int(sc.render.resolution_x * 0.45)
    sc.render.filepath = os.path.join(out, "sections.png")
    bpy.ops.render.render(write_still=True)


if __name__ == "__main__":
    main()
