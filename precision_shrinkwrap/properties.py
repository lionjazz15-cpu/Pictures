# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty,
                       PointerProperty, StringProperty)


def _is_mesh(self, obj):
    return obj.type == "MESH"


class PSW_Settings(bpy.types.PropertyGroup):
    target: PointerProperty(
        name="Target", type=bpy.types.Object, poll=_is_mesh,
        description="Body mesh to wrap onto (evaluated with its modifiers, in rest pose)")
    offset: FloatProperty(
        name="Offset", default=0.002, min=0.0, soft_max=0.05, unit="LENGTH", precision=4,
        description="Distance kept from the target surface (fabric thickness / gap)")
    offset_group: StringProperty(
        name="Offset Group",
        description="Optional vertex group scaling the offset per vertex (weight 1 = full offset)")
    vertex_group: StringProperty(
        name="Influence Group",
        description="Optional vertex group limiting the effect; weight 0 vertices are pinned")

    iterations: IntProperty(
        name="Iterations", default=30, min=1, soft_max=200,
        description="Project / relax rounds. More = cleaner valleys, slower")
    relax: FloatProperty(
        name="Relax", default=0.5, min=0.0, max=1.0,
        description="Tangential relaxation strength that keeps vertices from piling up in creases")
    distribution: EnumProperty(
        name="Distribution",
        items=[("PRESERVE", "Preserve", "Keep the original edge flow / spacing of the mesh"),
               ("EVEN", "Even", "Redistribute vertices evenly over the surface")],
        default="PRESERVE")
    tension: IntProperty(
        name="Valley Tension", default=0, min=0, soft_max=100,
        description="Let the fabric bridge valleys (buttock crease, cleavage) like tight cloth. "
                    "0 = follow the surface everywhere")
    use_anneal: BoolProperty(
        name="Anneal Offset", default=True,
        description="Wrap onto a large offset first and shrink it step by step, "
                    "so vertices go down into valleys on the correct side")
    anneal_start: FloatProperty(
        name="Anneal Start", default=0.0, min=0.0, unit="LENGTH", precision=4,
        description="Offset the annealing starts from (0 = automatic)")
    subdiv_aware: BoolProperty(
        name="Subdivision Aware", default=True,
        description="If the mesh has a Subdivision modifier, fit the subdivided result "
                    "and solve for the cage that produces it")
    cage_iterations: IntProperty(
        name="Cage Fit Iterations", default=20, min=1, soft_max=100,
        description="Iterations used to solve the cage of a subdivided mesh")
    output: EnumProperty(
        name="Output",
        items=[("SHAPE_KEY", "Shape Key", "Store the result as a new shape key"),
               ("APPLY", "Apply", "Move the mesh vertices directly")],
        default="SHAPE_KEY")

    # topology transfer
    transfer_mode: EnumProperty(
        name="Mode",
        items=[("SUBDIV", "Keep Subdivision",
                "Copy the cage faces and the modifier stack; the cage is solved so the "
                "subdivided result sits at the offset"),
               ("APPLIED", "Applied (Exact)",
                "Copy the subdivided mesh itself: vertices coincide exactly with the "
                "subdivided body (offset 0) or sit exactly on the offset surface")],
        default="SUBDIV")
    transfer_fit: BoolProperty(
        name="Fit to Body", default=True,
        description="Place the new mesh at the offset from the body. Turn off to only copy "
                    "the topology as it is (e.g. for loose clothing shaped afterwards)")
    region: EnumProperty(
        name="Region",
        items=[("SELECTED", "Selected Faces", "Faces selected in Edit Mode"),
               ("VERTEX_GROUP", "Vertex Group", "Faces whose vertices are all in the group (>= 0.5)"),
               ("PROXIMITY", "Near Object", "Faces close to another object (e.g. an existing garment)"),
               ("ALL", "All", "The whole mesh")],
        default="SELECTED")
    region_group: StringProperty(name="Region Group")
    proximity_object: PointerProperty(name="Near Object", type=bpy.types.Object, poll=_is_mesh)
    proximity_distance: FloatProperty(
        name="Distance", default=0.01, min=0.0, unit="LENGTH", precision=4,
        description="Maximum distance to the object for a face to be included")
    keep_shape_keys: BoolProperty(
        name="Keep Shape Keys", default=True,
        description="Keep the body's shape keys on the new mesh (Keep Subdivision mode)")
    copy_armature: BoolProperty(
        name="Copy Armature", default=True,
        description="Add the body's Armature modifiers to the new mesh (Applied mode)")
