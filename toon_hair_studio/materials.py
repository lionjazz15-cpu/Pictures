# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal material slots (shading is left to MToon / lilToon / Poiyomi)."""

import bpy

HAIR_MAT = "TH_Hair"
OUTLINE_MAT = "TH_Hair_Outline"


def _principled(mat, color):
    if hasattr(mat, "use_nodes") and not mat.use_nodes:
        mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (*color, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.6
    mat.diffuse_color = (*color, 1.0)


def ensure_hair_material(name=HAIR_MAT):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        _principled(mat, (0.45, 0.30, 0.25))
        mat.use_backface_culling = True
    return mat


def ensure_outline_material(name=OUTLINE_MAT):
    """Black, front-culled look for the mesh outline (inverted hull)."""
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        if hasattr(mat, "use_nodes") and not mat.use_nodes:
            mat.use_nodes = True
        nt = mat.node_tree
        nt.nodes.clear()
        emi = nt.nodes.new("ShaderNodeEmission")
        emi.inputs["Color"].default_value = (0.05, 0.03, 0.03, 1.0)
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        out.location = (250, 0)
        nt.links.new(emi.outputs[0], out.inputs[0])
        mat.diffuse_color = (0.05, 0.03, 0.03, 1.0)
        mat.use_backface_culling = True
    return mat
