"""Render preview images of the sample hairstyle (EEVEE).

    python tests/render_preview.py [out_dir]
"""
import math
import os
import sys

import bpy
from mathutils import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import toon_hair_studio as th  # noqa: E402
from toon_hair_studio import build, materials  # noqa: E402

OUT = sys.argv[-1] if len(sys.argv) > 1 and not sys.argv[-1].endswith(".py") else os.path.join(ROOT, "docs")
RES = int(os.environ.get("TH_RES", "720"))


def look_at(ob, target):
    d = target - ob.location
    ob.rotation_euler = d.to_track_quat('-Z', 'Y').to_euler()


def setup_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    th.register()
    sc = bpy.context.scene
    sc.render.engine = 'BLENDER_EEVEE'
    sc.view_settings.view_transform = 'Standard'
    sc.render.resolution_x = RES
    sc.render.resolution_y = RES
    if hasattr(sc.eevee, "taa_render_samples"):
        sc.eevee.taa_render_samples = 16
    sc.render.film_transparent = False
    world = bpy.data.worlds.new("W")
    world.use_nodes = True
    nt = world.node_tree
    bg = nt.nodes.get("Background")
    bg.inputs[0].default_value = (0.93, 0.94, 0.97, 1)
    # background visible to the camera only; lighting comes from the sun
    amb = nt.nodes.new("ShaderNodeBackground")
    amb.inputs[0].default_value = (1, 1, 1, 1)
    amb.inputs[1].default_value = 0.08
    lp = nt.nodes.new("ShaderNodeLightPath")
    mix = nt.nodes.new("ShaderNodeMixShader")
    out = nt.nodes.get("World Output")
    nt.links.new(lp.outputs["Is Camera Ray"], mix.inputs[0])
    nt.links.new(amb.outputs[0], mix.inputs[1])
    nt.links.new(bg.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs[0])
    sc.world = world

    c = Vector((0, 0, 1.5))
    sc.cursor.location = c
    bpy.ops.toon_hair.create_center(radius=0.1)
    center = bpy.data.objects[th.CENTER_NAME]
    center.scale = (0.095, 0.1, 0.105)

    # head + neck with a toon skin material
    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32, radius=1, location=c)
    head = bpy.context.active_object
    head.scale = (0.093, 0.098, 0.103)
    bpy.ops.object.shade_smooth()
    bpy.ops.mesh.primitive_cylinder_add(radius=0.04, depth=0.16, location=c + Vector((0, 0.01, -0.13)))
    neck = bpy.context.active_object
    bpy.ops.object.shade_smooth()
    skin = bpy.data.materials.new("Skin")
    materials.build_hair_material(skin, lit=(1.0, 0.86, 0.78), shade=(0.93, 0.68, 0.62),
                                  inner=(0.9, 0.7, 0.65))
    nodes = skin.node_tree.nodes
    nodes[materials.N_RING_STR].outputs[0].default_value = 0.0
    nodes[materials.N_INNER_STR].outputs[0].default_value = 0.0
    nodes[materials.N_RIM_STR].outputs[0].default_value = 0.0
    for o in (head, neck):
        o.data.materials.append(skin)

    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", 'SUN'))
    sun.data.energy = 1.6
    sun.rotation_euler = (math.radians(55), 0.0, math.radians(-50))
    sc.collection.objects.link(sun)

    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    cam.data.lens = 85
    sc.collection.objects.link(cam)
    sc.camera = cam
    bpy.ops.toon_hair.sample_hairstyle()
    return sc, cam, c


def shoot(sc, cam, c, pos, name):
    cam.location = c + Vector(pos)
    look_at(cam, c + Vector((0, 0, -0.07)))
    sc.render.filepath = os.path.join(OUT, name)
    bpy.ops.render.render(write_still=True)


def topology_mode(on):
    """Overlay quad wireframes (Wireframe modifier keeps quads, unlike the shader node)."""
    wire = bpy.data.materials.get("TH_Wire") or bpy.data.materials.new("TH_Wire")
    wire.diffuse_color = (0.05, 0.05, 0.08, 1)
    for ob in list(bpy.data.objects):
        if build.is_group(ob):
            h = ob.toon_hair.hair_object
            ob.toon_hair.outline_object.hide_render = on
            mod = h.modifiers.get("TH_Wire")
            if on and mod is None:
                mod = h.modifiers.new("TH_Wire", 'WIREFRAME')
                mod.thickness = 0.0006
                mod.use_replace = False
                mod.use_even_offset = True
                mod.material_offset = 1
                if len(h.data.materials) < 2:
                    h.data.materials.append(wire)
            elif not on and mod is not None:
                h.modifiers.remove(mod)


def main():
    os.makedirs(OUT, exist_ok=True)
    sc, cam, c = setup_scene()
    shoot(sc, cam, c, (0.42, -0.95, 0.12), "preview_front.png")
    shoot(sc, cam, c, (-0.7, 0.75, 0.2), "preview_back.png")
    topology_mode(True)
    shoot(sc, cam, c, (0.42, -0.95, 0.12), "preview_topology.png")
    topology_mode(False)
    print("rendered to", OUT)


if __name__ == "__main__":
    main()
