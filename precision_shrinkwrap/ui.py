# SPDX-License-Identifier: GPL-3.0-or-later

import bpy


class PSW_PT_main(bpy.types.Panel):
    bl_label = "Precision Shrinkwrap"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "PrecisionSW"

    def draw(self, context):
        s = context.scene.precision_shrinkwrap
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.prop(s, "target")
        layout.prop(s, "offset")
        if context.active_object is not None:
            layout.prop_search(s, "offset_group", context.active_object, "vertex_groups")


class PSW_PT_rough_cage(bpy.types.Panel):
    bl_label = "Rough Cage to Exact Fit"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "PrecisionSW"
    bl_parent_id = "PSW_PT_main"

    def draw(self, context):
        s = context.scene.precision_shrinkwrap
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.label(text="Select the rough cage(s), then:", icon="INFO")
        col = layout.column()
        _draw_output_settings(col, s)
        col.prop(s, "tension")
        col.prop(s, "cage_coverage")
        col.prop(s, "boundary_smooth")
        col.prop(s, "hide_cage")
        layout.operator("precision_shrinkwrap.conform_rough_cage", icon="MOD_SHRINKWRAP")


def _draw_output_settings(col, s):
    col.prop(s, "transfer_mode")
    if s.transfer_mode == "APPLIED":
        col.prop(s, "keep_mirror")
        col.prop(s, "copy_armature")
    else:
        col.prop(s, "keep_shape_keys")
    col.prop(s, "transfer_fit")


class PSW_PT_fit(bpy.types.Panel):
    bl_label = "Wrap Garment"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "PrecisionSW"
    bl_parent_id = "PSW_PT_main"

    def draw(self, context):
        s = context.scene.precision_shrinkwrap
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        col = layout.column()
        col.prop(s, "iterations")
        col.prop(s, "relax")
        col.prop(s, "distribution")
        col.prop(s, "tension")
        row = col.row(heading="Anneal Offset")
        row.prop(s, "use_anneal", text="")
        sub = row.row()
        sub.active = s.use_anneal
        sub.prop(s, "anneal_start", text="")
        col.prop(s, "subdiv_aware")
        sub = col.column()
        sub.active = s.subdiv_aware
        sub.prop(s, "cage_iterations")
        if context.active_object is not None:
            col.prop_search(s, "vertex_group", context.active_object, "vertex_groups")
        col.prop(s, "output")
        layout.operator("precision_shrinkwrap.fit", icon="MOD_SHRINKWRAP")
        layout.operator("precision_shrinkwrap.check_offset", icon="DRIVER_DISTANCE")


class PSW_PT_transfer(bpy.types.Panel):
    bl_label = "Transfer Body Topology"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "PrecisionSW"
    bl_parent_id = "PSW_PT_main"

    def draw(self, context):
        s = context.scene.precision_shrinkwrap
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        col = layout.column()
        _draw_output_settings(col, s)
        col.prop(s, "region")
        if s.region == "VERTEX_GROUP" and s.target is not None:
            col.prop_search(s, "region_group", s.target, "vertex_groups")
        elif s.region == "PROXIMITY":
            col.prop(s, "proximity_object")
            col.prop(s, "proximity_distance")
            col.prop(s, "boundary_smooth")
        elif s.region == "ROUGH_CAGE":
            col.prop(s, "cage_object")
            col.prop(s, "cage_coverage")
            col.prop(s, "boundary_smooth")
        layout.operator("precision_shrinkwrap.transfer_topology", icon="MOD_SUBSURF")


classes = (PSW_PT_main, PSW_PT_rough_cage, PSW_PT_fit, PSW_PT_transfer)
