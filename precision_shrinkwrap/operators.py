# SPDX-License-Identifier: GPL-3.0-or-later

import bmesh
import bpy
import numpy as np

from . import fitting
from . import mesh_utils as mu
from . import rough_cage

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
                new, err, dirs = fitting.fit_object(context, obj, surface, s, "NEAREST", sub)
                mu.write_result(obj, new, s.output)
                if s.live_offset:
                    mu.set_live_offset(obj, dirs, s.offset)
        except RuntimeError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        finally:
            progress.end()
        body_edge = mu.mean_edge_length_world(s.target.data, s.target.matrix_world)
        coarse = [o.name for o in garments if not mu.has_subdivision(o)
                  and mu.mean_edge_length_world(o.data, o.matrix_world) > 3.0 * body_edge]
        if coarse:
            self.report({"WARNING"},
                        f"{', '.join(coarse)}: much coarser than the body, flat faces will cut into "
                        "curved areas. Use 'Rough Cage to Exact Fit' for rough cages")
        else:
            self.report({"INFO"}, f"Fitted {len(garments)} object(s)")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# building a garment from a region of the body


def _link_like(context, obj, body):
    cols = body.users_collection
    (cols[0] if cols else context.scene.collection).objects.link(obj)


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


def _body_mirrors(body):
    return [m for m in body.modifiers
            if m.type == "MIRROR" and m.show_viewport and m.mirror_object is None]


def _copy_cage(context, s, body, keep, name):
    """Keep Subdivision: the body's cage faces with the whole modifier stack
    (Mirror, Subdivision, Armature ...)."""
    me = body.data.copy()
    me.name = name
    obj = body.copy()
    obj.data = me
    obj.name = name
    _link_like(context, obj, body)
    if not s.keep_shape_keys and me.shape_keys:
        obj.shape_key_clear()
    _delete_faces(me, keep)
    return obj


def _copy_applied(context, s, body, keep, name):
    """Applied: the evaluated (subdivided) body itself.  With a mirrored body
    and "Keep Mirror", only the original half is kept and a Mirror modifier is
    added, which reproduces the evaluated body exactly."""
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
    me.name = name
    a = me.attributes.get(REGION_ATTR)
    sub_keep = np.ones(len(me.polygons), dtype=bool)
    if a is not None:
        a.data.foreach_get("value", sub_keep)
        me.attributes.remove(a)

    mirrors = _body_mirrors(body) if s.keep_mirror else []
    if mirrors:
        cage = mu.mesh_coords(src)
        centers = np.empty(len(me.polygons) * 3)
        me.polygons.foreach_get("center", centers)
        centers = centers.reshape(-1, 3)
        eps = 1e-6 * max(float(np.ptp(cage, axis=0).max()) if len(cage) else 1.0, 1e-9)
        for m in mirrors:
            for ax in range(3):
                if m.use_axis[ax]:
                    side = 1.0 if cage[:, ax].mean() >= 0 else -1.0
                    sub_keep &= centers[:, ax] * side > -eps
    _delete_faces(me, sub_keep)
    if mirrors:
        P = mu.mesh_coords(me)
        for m in mirrors:
            thr = max(m.merge_threshold, 1e-6)
            for ax in range(3):
                if m.use_axis[ax]:
                    P[np.abs(P[:, ax]) <= thr, ax] = 0.0
        mu.set_mesh_coords(me, P)

    obj = bpy.data.objects.new(name, me)
    obj.parent = body.parent
    obj.parent_type = body.parent_type
    obj.parent_bone = body.parent_bone
    obj.matrix_parent_inverse = body.matrix_parent_inverse.copy()
    obj.matrix_world = body.matrix_world.copy()
    for vg in body.vertex_groups:
        if vg.name not in obj.vertex_groups:
            obj.vertex_groups.new(name=vg.name)
    for m in mirrors:
        nm = obj.modifiers.new(m.name, "MIRROR")
        mu.copy_modifier_settings(m, nm)
        nm.use_clip = True
        nm.use_mirror_merge = True
    if s.copy_armature:
        for m in body.modifiers:
            if m.type == "ARMATURE":
                mu.copy_modifier_settings(m, obj.modifiers.new(m.name, "ARMATURE"))
    _link_like(context, obj, body)
    return obj


def create_from_region(context, s, body, keep, name, progress=None, smooth=0):
    """Build the garment from the body faces in ``keep``; returns (obj, message).
    ``smooth``: border smoothing iterations for automatically picked regions."""
    # Without "Fit to Body" the region is only copied (useful for loose
    # clothing that is shaped by hand or fitted to another object later).
    fit = s.transfer_fit and (s.transfer_mode == "SUBDIV" or s.offset > 0.0)
    surface = mu.target_surface(context, body) if fit else None
    if s.transfer_mode == "SUBDIV":
        obj = _copy_cage(context, s, body, keep, name)
        msg = f"Created '{obj.name}' ({len(obj.data.vertices)} cage vertices)"
    else:
        obj = _copy_applied(context, s, body, keep, name)
        msg = f"Created '{obj.name}' ({len(obj.data.vertices)} vertices)"
    if smooth > 0:
        # the applied mesh lies on the body, the cage does not
        on_body = mu.target_surface(context, body) if s.transfer_mode == "APPLIED" else None
        fitting.smooth_boundary(obj, smooth, on_body)
    if fit:
        new, err, dirs = fitting.fit_object(context, obj, surface, s, "NORMAL", progress)
        mu.write_result(obj, new, "APPLY")
        if s.transfer_mode == "SUBDIV":
            msg += f", cage residual {err:.2e}"
        if s.live_offset:
            mu.set_live_offset(obj, dirs, s.offset)
    elif s.live_offset:
        # lies on the body: the live offset starts from 0 along the normals
        mu.set_live_offset(obj, fitting.normal_directions(obj), 0.0)
    return obj, msg


def _make_active(context, obj):
    for o in context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj


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
        progress = fitting.Progress(context)
        try:
            keep = self._region(context, s, body)
            auto = s.region in {"ROUGH_CAGE", "PROXIMITY"}
            obj, msg = create_from_region(context, s, body, keep, body.name + "_Wear", progress,
                                          smooth=s.boundary_smooth if auto else 0)
        except (ValueError, RuntimeError) as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        finally:
            progress.end()
        _make_active(context, obj)
        self.report({"INFO"}, msg)
        return {"FINISHED"}

    def _region(self, context, s, body):
        me = body.data
        nf = len(me.polygons)
        if s.region == "ALL":
            return np.ones(nf, dtype=bool)
        if s.region == "SELECTED":
            sel = np.zeros(nf, dtype=bool)
            me.polygons.foreach_get("select", sel)
            if not sel.any():
                raise ValueError(f"No faces of '{body.name}' are selected: select the region in "
                                 "Edit Mode, or use Region = Rough Cage")
            return sel
        if s.region == "ROUGH_CAGE":
            cage = s.cage_object
            if cage is None or cage == body:
                raise ValueError("Choose the rough cage object")
            keep = rough_cage.region(context, body, cage, s.cage_coverage)
            if not keep.any():
                raise ValueError(f"'{cage.name}' does not cover '{body.name}': place it around the body")
            return keep
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
        keep = np.logical_and.reduceat(vin[loop_verts], starts) if len(starts) else np.zeros(0, bool)
        if not keep.any():
            if s.region == "VERTEX_GROUP":
                raise ValueError(f"No face has all its vertices in '{s.region_group}' (weight >= 0.5)")
            raise ValueError(f"No face of '{body.name}' is within {s.proximity_distance:.4g} of "
                             f"'{s.proximity_object.name}': increase Distance, or use Region = Rough Cage")
        return keep

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


class PSW_OT_conform_rough_cage(bpy.types.Operator):
    """Turn a rough low-poly cage into a garment that matches the body exactly:
    the cage only marks where the garment goes, the garment is built from the
    body's own topology"""
    bl_idname = "precision_shrinkwrap.conform_rough_cage"
    bl_label = "Rough Cage to Exact Fit"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return PSW_OT_fit.poll(context)

    def execute(self, context):
        s = _settings(context)
        body = s.target
        _ensure_object_mode(context)
        active = context.active_object
        cages = [o for o in context.selected_objects if o.type == "MESH" and o != body]
        if active in cages:
            cages.remove(active)
            cages.insert(0, active)
        progress = fitting.Progress(context)
        made = []
        try:
            for i, cage in enumerate(cages):
                keep = rough_cage.region(context, body, cage, s.cage_coverage,
                                         progress=lambda f, i=i: progress((i + 0.5 * f) / len(cages)))
                if not keep.any():
                    raise ValueError(f"'{cage.name}' does not cover '{body.name}': place it around the body")
                obj, msg = create_from_region(
                    context, s, body, keep, cage.name + "_Fit",
                    lambda f, i=i: progress((i + 0.5 + 0.5 * f) / len(cages)), smooth=s.boundary_smooth)
                if cage.data.materials:
                    obj.data.materials.clear()
                    for mat in cage.data.materials:
                        obj.data.materials.append(mat)
                    obj.data.polygons.foreach_set("material_index", np.zeros(len(obj.data.polygons), np.int32))
                if s.hide_cage:
                    cage.hide_set(True)
                made.append(obj)
        except (ValueError, RuntimeError) as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        finally:
            progress.end()
        _make_active(context, made[-1])
        self.report({"INFO"}, msg if len(made) == 1 else f"Created {len(made)} garments")
        return {"FINISHED"}


SLIDE_MOD = "PSW Slide"


def _slide_modifier(obj):
    m = obj.modifiers.get(SLIDE_MOD) if obj is not None else None
    return m if m is not None and m.type == "SHRINKWRAP" else None


class PSW_OT_slide_start(bpy.types.Operator):
    """Edit the fitted garment with its vertices sliding on the body surface:
    move them in Edit Mode (proportional editing works well), then press
    Confirm to rebuild the garment from the body's topology"""
    bl_idname = "precision_shrinkwrap.slide_start"
    bl_label = "Slide on Body"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        s = _settings(context)
        obj = context.active_object
        return (s.target is not None and obj is not None and obj.type == "MESH"
                and obj != s.target and _slide_modifier(obj) is None)

    def execute(self, context):
        s = _settings(context)
        obj = context.active_object
        _ensure_object_mode(context)
        m = obj.modifiers.new(SLIDE_MOD, "SHRINKWRAP")
        m.target = s.target
        m.wrap_method = "NEAREST_SURFACEPOINT"
        m.wrap_mode = "OUTSIDE_SURFACE"
        m.offset = s.offset
        m.show_in_editmode = True
        m.show_on_cage = True
        obj.modifiers.move(len(obj.modifiers) - 1, 0)
        bpy.ops.object.mode_set(mode="EDIT")
        self.report({"INFO"}, "Move vertices; they slide on the body. Then press Confirm")
        return {"FINISHED"}


class PSW_OT_slide_confirm(bpy.types.Operator):
    """Take the slid shape as the new region and rebuild the garment from the
    body's topology (exact match / exact offset again)"""
    bl_idname = "precision_shrinkwrap.slide_confirm"
    bl_label = "Confirm and Re-transfer"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _settings(context).target is not None and _slide_modifier(context.active_object) is not None

    def execute(self, context):
        s = _settings(context)
        body = s.target
        old = context.active_object
        _ensure_object_mode(context)
        progress = fitting.Progress(context)
        try:
            keep = rough_cage.region(context, body, old, s.cage_coverage,
                                     progress=lambda f: progress(0.5 * f))
            if not keep.any():
                raise ValueError(f"'{old.name}' no longer covers '{body.name}'")
            obj, msg = create_from_region(context, s, body, keep, old.name + "_tmp",
                                          lambda f: progress(0.5 + 0.5 * f), smooth=s.boundary_smooth)
        except (ValueError, RuntimeError) as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        finally:
            progress.end()
        obj.data.materials.clear()
        for mat in old.data.materials:
            obj.data.materials.append(mat)
        if len(old.data.materials):
            obj.data.polygons.foreach_set("material_index", np.zeros(len(obj.data.polygons), np.int32))
        name, old_mesh = old.name, old.data
        bpy.data.objects.remove(old, do_unlink=True)
        if old_mesh.users == 0:
            bpy.data.meshes.remove(old_mesh)
        obj.name = name
        obj.data.name = name
        _make_active(context, obj)
        self.report({"INFO"}, msg.replace(name + "_tmp", name))
        return {"FINISHED"}


class PSW_OT_slide_cancel(bpy.types.Operator):
    """Stop sliding on the body and keep the garment as it was"""
    bl_idname = "precision_shrinkwrap.slide_cancel"
    bl_label = "Cancel Slide"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _slide_modifier(context.active_object) is not None

    def execute(self, context):
        obj = context.active_object
        _ensure_object_mode(context)
        obj.modifiers.remove(_slide_modifier(obj))
        return {"FINISHED"}


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


classes = (PSW_OT_fit, PSW_OT_transfer_topology, PSW_OT_conform_rough_cage, PSW_OT_slide_start,
           PSW_OT_slide_confirm, PSW_OT_slide_cancel, PSW_OT_check_offset)
