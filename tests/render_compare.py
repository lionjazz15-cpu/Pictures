"""Render the sample's back hair with SMOOTH vs GROOVE lateral joins.

    python tests/render_compare.py [out_dir]
"""
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_preview as rp  # noqa: E402
from toon_hair_studio import build  # noqa: E402


def main():
    out = sys.argv[-1] if len(sys.argv) > 1 and not sys.argv[-1].endswith(".py") else rp.OUT
    os.makedirs(out, exist_ok=True)
    rp.OUT = out
    sc, cam, c = rp.setup_scene()
    groups = [o for o in bpy.data.objects if build.is_group(o)]
    for style in ('SMOOTH', 'GROOVE'):
        for g in groups:
            g.toon_hair.outline = False
            g.toon_hair.connect_style = style
        rp.shoot(sc, cam, c, (-0.55, 0.85, 0.25), f"compare_back_{style.lower()}.png")
        rp.shoot(sc, cam, c, (0.3, -0.95, 0.35), f"compare_front_{style.lower()}.png")


if __name__ == "__main__":
    main()
