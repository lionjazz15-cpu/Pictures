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
        col.prop(s, "transfer_mode")
        col.prop(s, "region")
        if s.region == "VERTEX_GROUP" and s.target is not None:
            col.prop_search(s, "region_group", s.target, "vertex_groups")
        elif s.region == "PROXIMITY":
            col.prop(s, "proximity_object")
            col.prop(s, "proximity_distance")
        if s.transfer_mode == "SUBDIV":
            col.prop(s, "keep_shape_keys")
        else:
            col.prop(s, "copy_armature")
        layout.operator("precision_shrinkwrap.transfer_topology", icon="MOD_SUBSURF")


classes = (PSW_PT_main, PSW_PT_fit, PSW_PT_transfer)
