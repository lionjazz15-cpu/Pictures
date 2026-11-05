"""'Only where chosen' unify: messy bangs with two picked pairs, and a curve
stuck into a lock attached as a branch, everything else stays free.

    python tests/render_unify_selected.py [out_dir]
    TH_PART=stray python tests/render_unify_selected.py [out_dir]
"""
import os
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_attach as ra  # noqa: E402
import render_preview as rp  # noqa: E402
import render_unify as ru  # noqa: E402
from toon_hair_studio import build  # noqa: E402


def activate(g):
    for o in bpy.context.selected_objects:
        o.select_set(False)
    g.select_set(True)
    bpy.context.view_layer.objects.active = g


def shoot(sc, cam, c, pos, tgt, path):
    for topo in (False, True):
        rp.topology_mode(topo)
        cam.location = c + Vector(pos)
        rp.look_at(cam, c + Vector(tgt))
        sc.render.filepath = path % int(topo)
        bpy.ops.render.render(write_still=True)


def main():
    out = sys.argv[-1] if len(sys.argv) > 1 and not sys.argv[-1].endswith(".py") else rp.OUT
    os.makedirs(out, exist_ok=True)
    rp.OUT = out
    if os.environ.get("TH_PART", "bangs") == "stray":
        sc, cam, c, g = ra.make_scene()
        g.toon_hair.unify_scope = 'SELECTED'
        activate(g)
        bpy.ops.toon_hair.attach(mode='BRANCH')
        shoot(sc, cam, c, (0.12, -0.6, 0.02), (0.0, -0.1, -0.01), os.path.join(out, "usel_stray_%d.png"))
        return
    sc, cam, c, g = ru.make_scene()
    st = g.toon_hair
    st.branches, st.branch_jitter = 1, 0
    st.unify_scope = 'SELECTED'
    activate(g)
    for pair in ((3, 4), (5, 6)):
        for i, s_ in enumerate(g.data.splines):
            for j, bp in enumerate(s_.bezier_points):
                bp.select_control_point = i in pair and j == 1
        bpy.ops.toon_hair.unify_selected()
    shoot(sc, cam, c, (0.1, -0.55, 0.08), (0.0, -0.08, 0.02), os.path.join(out, "usel_bangs_%d.png"))


if __name__ == "__main__":
    main()
