# SPDX-License-Identifier: GPL-3.0-or-later

import bmesh
import bpy
import numpy as np

from . import fitting
from . import mesh_utils as mu

REGION_ATTR = "_psw_region"


def _settings(context):
    return context.scene.precision_shrinkwrap


def _ensure_object_mode(context):
    if context.mode != "OBJECT" and context.active_object is not None:
        bpy.ops.object.mode_set(mode="OBJECT")


class PSW_OT_fit(bpy.types.Operator):
    """Wrap the selected meshes onto the target with an even offset, without
    stretching in valleys"""
    bl_idname = "precision_shrinkwrap.fit"
    bl_label = "Precision Shrinkwrap"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        s = _settings(context)
        return s.target is not None and any(
            o.type == "MESH" and o != s.target for o in context.selected_objects)

    def execute(self, context):
        s = _settings(context)
        _ensure_object_mode(context)
        garments = [o for o in context.selected_objects if o.type == "MESH" and o != s.target]
        surface = mu.target_surface(context, s.target)
        progress = fitting.Progress(context)
        try:
            for i, obj in enumerate(garments):
                sub = (lambda f, i=i: progress((i + f) / len(garments)))
                new, err = fitting.fit_object(context, obj, surface, s, "NEAREST", sub)
                mu.write_result(obj, new, s.output)
        except RuntimeError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        finally:
            progress.end()
        self.report({"INFO"}, f"Fitted {len(garments)} object(s)")
        return {"FINISHED"}


class PSW_OT_transfer_topology(bpy.types.Operator):
    """Create a new mesh from the target's own topology (selected region), lying
    exactly on the target or at an even offset from it"""
    bl_idname = "precision_shrinkwrap.transfer_topology"
    bl_label = "Transfer Topology"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _settings(context).target is not None

    def execute(self, context):
        s = _settings(context)
        body = s.target
        _ensure_object_mode(context)
        try:
            keep = self._region(context, s, body)
        except ValueError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        if not keep.any():
            self.report({"ERROR"}, "No faces in the region")
            return {"CANCELLED"}

        surface = mu.target_surface(context, body)
        progress = fitting.Progress(context)
        try:
            if s.transfer_mode == "SUBDIV":
                obj = self._copy_cage(context, s, body, keep)
                new, err = fitting.fit_object(context, obj, surface, s, "NORMAL", progress)
                mu.write_result(obj, new, "APPLY")
                msg = f"Created '{obj.name}' (cage residual {err:.2e})"
            else:
                obj = self._copy_applied(context, s, body, keep)
                if s.offset > 0.0:
                    new, _ = fitting.fit_object(context, obj, surface, s, "NORMAL", progress)
                    mu.write_result(obj, new, "APPLY")
                msg = f"Created '{obj.name}' ({len(obj.data.vertices)} vertices)"
        except RuntimeError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        finally:
            progress.end()

        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        self.report({"INFO"}, msg)
        return {"FINISHED"}

    # -- region ------------------------------------------------------------

    def _region(self, context, s, body):
        me = body.data
        nf = len(me.polygons)
        if s.region == "ALL":
            return np.ones(nf, dtype=bool)
        if s.region == "SELECTED":
            sel = np.zeros(nf, dtype=bool)
            me.polygons.foreach_get("select", sel)
            return sel
        loop_verts = np.empty(len(me.loops), dtype=np.int64)
        me.loops.foreach_get("vertex_index", loop_verts)
        starts = np.empty(nf, dtype=np.int64)
        me.polygons.foreach_get("loop_start", starts)
        if s.region == "VERTEX_GROUP":
            if s.region_group not in body.vertex_groups:
                raise ValueError("Region vertex group not found")
            vin = mu.vertex_group_weights(body, me, s.region_group) >= 0.5
        else:
            other = s.proximity_object
            if other is None or other == body:
                raise ValueError("Choose another object for the region")
            near = mu.target_surface(context, other)
            P = self._cage_on_surface(context, body)
            _, _, sd = near.signed(P)
            vin = np.abs(sd) <= s.proximity_distance
        # a face is in the region if all its corners are
        return np.logical_and.reduceat(vin[loop_verts], starts) if len(starts) else np.zeros(0, bool)

    def _cage_on_surface(self, context, body):
        """World positions of the cage vertices as they appear after the
        modifiers (on the limit surface when subdivided)."""
        n = len(body.data.vertices)
        with mu.modifiers_disabled(body, mu.POSE_DEFORM_TYPES):
            dg = mu.evaluated_depsgraph(context)
            ev = body.evaluated_get(dg)
            me = ev.to_mesh()
            try:
                P = mu.mesh_coords(me)
            finally:
                ev.to_mesh_clear()
        if len(P) < n:
            P = mu.mesh_coords(body.data)
        return mu.to_world(P[:n], body.matrix_world)

    # -- mesh creation -----------------------------------------------------

    @staticmethod
    def _delete_faces(me, keep):
        bm = bmesh.new()
        bm.from_mesh(me)
        bm.faces.ensure_lookup_table()
        kill = [f for f in bm.faces if not keep[f.index]]
        if kill:
            bmesh.ops.delete(bm, geom=kill, context="FACES")
        loose_edges = [e for e in bm.edges if not e.link_faces]
        if loose_edges:
            bmesh.ops.delete(bm, geom=loose_edges, context="EDGES")
        loose = [v for v in bm.verts if not v.link_faces]
        if loose:
            bmesh.ops.delete(bm, geom=loose, context="VERTS")
        bm.to_mesh(me)
        bm.free()
        me.update()

    @staticmethod
    def _link_like(context, obj, body):
        cols = body.users_collection
        (cols[0] if cols else context.scene.collection).objects.link(obj)

    def _copy_cage(self, context, s, body, keep):
        me = body.data.copy()
        me.name = body.data.name + "_Wear"
        obj = body.copy()
        obj.data = me
        obj.name = body.name + "_Wear"
        self._link_like(context, obj, body)
        if not s.keep_shape_keys and me.shape_keys:
            obj.shape_key_clear()
        self._delete_faces(me, keep)
        return obj

    def _copy_applied(self, context, s, body, keep):
        src = body.data
        attr = src.attributes.new(REGION_ATTR, "BOOLEAN", "FACE")
        attr.data.foreach_set("value", keep)
        try:
            with mu.modifiers_disabled(body, mu.POSE_DEFORM_TYPES):
                dg = mu.evaluated_depsgraph(context)
                ev = body.evaluated_get(dg)
                me = bpy.data.meshes.new_from_object(ev, preserve_all_data_layers=True, depsgraph=dg)
        finally:
            src.attributes.remove(src.attributes[REGION_ATTR])
        me.name = body.data.name + "_Wear"
        a = me.attributes.get(REGION_ATTR)
        sub_keep = np.zeros(len(me.polygons), dtype=bool)
        if a is not None:
            a.data.foreach_get("value", sub_keep)
            me.attributes.remove(a)
        else:
            sub_keep[:] = True
        self._delete_faces(me, sub_keep)

        obj = bpy.data.objects.new(body.name + "_Wear", me)
        obj.parent = body.parent
        obj.parent_type = body.parent_type
        obj.parent_bone = body.parent_bone
        obj.matrix_parent_inverse = body.matrix_parent_inverse.copy()
        obj.matrix_world = body.matrix_world.copy()
        for vg in body.vertex_groups:
            if vg.name not in obj.vertex_groups:
                obj.vertex_groups.new(name=vg.name)
        if s.copy_armature:
            for m in body.modifiers:
                if m.type == "ARMATURE":
                    nm = obj.modifiers.new(m.name, "ARMATURE")
                    for prop in ("object", "use_vertex_groups", "use_bone_envelopes",
                                 "use_deform_preserve_volume", "use_multi_modifier"):
                        setattr(nm, prop, getattr(m, prop))
        self._link_like(context, obj, body)
        return obj


class PSW_OT_check_offset(bpy.types.Operator):
    """Report the distance of the selected meshes (with modifiers, rest pose)
    to the target"""
    bl_idname = "precision_shrinkwrap.check_offset"
    bl_label = "Check Offset"

    @classmethod
    def poll(cls, context):
        return PSW_OT_fit.poll(context)

    def execute(self, context):
        s = _settings(context)
        surface = mu.target_surface(context, s.target)
        for obj in context.selected_objects:
            if obj.type != "MESH" or obj == s.target:
                continue
            r = fitting.offset_report(context, obj, surface, s.offset)
            self.report({"INFO"},
                        f"{obj.name}: distance min {r['min'] * 1000:.3f} / mean {r['mean'] * 1000:.3f} / "
                        f"max {r['max'] * 1000:.3f} (x0.001), max deviation {r['max_dev'] * 1000:.3f}, "
                        f"inside {r['inside']}/{r['count']}")
        return {"FINISHED"}


classes = (PSW_OT_fit, PSW_OT_transfer_topology, PSW_OT_check_offset)
