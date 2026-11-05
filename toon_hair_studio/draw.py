# SPDX-License-Identifier: GPL-3.0-or-later
"""Draw locks directly on the head, and one-click head setup."""

import math

import bpy
from bpy.props import EnumProperty, FloatProperty
from mathutils import Vector

from . import build, presets
from .icons import ic

CENTER_NAME = "TH_HeadCenter"


# --------------------------------------------------------------------------
# head fitting
# --------------------------------------------------------------------------

def _percentile(vals, q):
    if not vals:
        return 0.0
    s = sorted(vals)
    i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[i]


def fit_head(ob, depsgraph):
    """Estimate the cranium ellipsoid of a head mesh (may include neck/body).

    Slices the mesh from the top down and stops where it narrows to a neck.
    Ears / horns are ignored by using percentiles and capping the width at
    the depth. Assumes Z up and the face looking towards -Y.
    Returns (location, (rx, ry, rz))."""
    ev = ob.evaluated_get(depsgraph)
    me = ev.to_mesh()
    try:
        mw = ob.matrix_world
        pts = [mw @ v.co for v in me.vertices]
    finally:
        ev.to_mesh_clear()
    if len(pts) < 4:
        loc = ob.matrix_world.to_translation()
        return loc, (0.1, 0.1, 0.1)
    zmax = max(p.z for p in pts)
    zmin = min(p.z for p in pts)
    H = max(zmax - zmin, 1e-6)
    n = 80
    step = H / n
    widths = []
    bottom = zmin
    wmax = 0.0
    for k in range(n):
        z1, z0 = zmax - step * (k + 1), zmax - step * k
        xs = [p.x for p in pts if z1 <= p.z <= z0]
        w = (_percentile(xs, 0.98) - _percentile(xs, 0.02)) if len(xs) > 3 else 0.0
        widths.append(w)
        wmax = max(wmax, w)
        # past the widest part of the head and narrowed to a neck
        if k > 6 and wmax > 0.0 and w < 0.62 * wmax and (zmax - z0) > 0.6 * wmax:
            bottom = z0
            break
    head = [p for p in pts if p.z >= bottom]
    xs = [p.x for p in head]
    ys = [p.y for p in head]
    x0, x1 = _percentile(xs, 0.03), _percentile(xs, 0.97)
    y0, y1 = _percentile(ys, 0.02), _percentile(ys, 0.98)
    ry = max((y1 - y0) * 0.5, 1e-4)
    rx = min((x1 - x0) * 0.5, ry * 0.95)
    rz = ry * 1.05
    # cranium: a sphere-ish volume hanging from the top of the head; the back
    # of the head defines it better than the nose, so shift slightly back
    loc = Vector(((x0 + x1) * 0.5, (y0 + y1) * 0.5 + ry * 0.05, zmax - rz))
    return loc, (rx, ry, rz)


def ensure_center(context):
    ob = bpy.data.objects.get(CENTER_NAME)
    if ob is None:
        ob = bpy.data.objects.new(CENTER_NAME, None)
        ob.empty_display_type = 'SPHERE'
        ob.empty_display_size = 1.0
        ob.scale = (0.1, 0.1, 0.1)
        ob.location = context.scene.cursor.location
        context.scene.collection.objects.link(ob)
        context.view_layer.update()
    return ob


class TOONHAIR_OT_setup_head(bpy.types.Operator):
    bl_idname = "toon_hair.setup_head"
    bl_label = "頭をセットアップ"
    bl_description = ("選択中の頭メッシュに合わせて、頭の中心(楕円体)を自動で配置します。"
                      "首や体が付いたメッシュでも頭の部分だけを測ります")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == 'MESH' and build.owner_group(ob) is None

    def execute(self, context):
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        head = context.active_object
        loc, (rx, ry, rz) = fit_head(head, context.evaluated_depsgraph_get())
        center = ensure_center(context)
        center.location = loc
        center.rotation_euler = (0.0, 0.0, 0.0)
        center.scale = (rx, ry, rz)
        context.scene.toon_hair_head = head
        context.view_layer.update()
        for ob in bpy.data.objects:
            if build.is_group(ob) and ob.toon_hair.center is None:
                ob.toon_hair.center = center
        build.rebuild_all()
        self.report({'INFO'}, f"頭の半径 {rx:.3f} / {ry:.3f} / {rz:.3f}")
        return {'FINISHED'}


# --------------------------------------------------------------------------
# groups
# --------------------------------------------------------------------------

def head_radius(center):
    return max(center.matrix_world.to_scale()) if center is not None else 0.1


def new_group(context, preset_key, center):
    """Create an empty hair group (curve object) set up from a preset."""
    from . import materials
    label, name, _icon, _fn = presets.PRESETS[preset_key]
    R = head_radius(center)
    cu = bpy.data.curves.new(name, 'CURVE')
    cu.dimensions = '3D'
    ob = bpy.data.objects.new(name, cu)
    context.collection.objects.link(ob)
    ob.show_in_front = True
    st = ob.toon_hair
    from . import _suspended
    with _suspended():
        st.enabled = True
        st.center = center
        st.material = materials.ensure_hair_material()
        st.outline_material = materials.ensure_outline_material()
        for k, v in presets.settings(preset_key, R).items():
            setattr(st, k, v)
    return ob


def add_bezier(cu, points):
    sp = cu.splines.new('BEZIER')
    sp.bezier_points.add(len(points) - 1)
    for bp, co in zip(sp.bezier_points, points):
        bp.co = co
        bp.handle_left_type = 'AUTO'
        bp.handle_right_type = 'AUTO'
    return sp


def stroke_to_controls(stroke, max_points=8, min_points=3, spacing=None):
    """Resample a drawn polyline to a few evenly spaced Bezier anchors."""
    if len(stroke) < 2:
        return list(stroke)
    lengths = [0.0]
    for i in range(1, len(stroke)):
        lengths.append(lengths[-1] + (stroke[i] - stroke[i - 1]).length)
    total = lengths[-1]
    if total <= 0.0:
        return [stroke[0]]
    n = max_points if spacing is None else int(round(total / max(spacing, 1e-6))) + 1
    n = min(max(n, min_points), max_points)
    out = []
    j = 0
    for k in range(n):
        target = total * k / (n - 1)
        while j < len(lengths) - 2 and lengths[j + 1] < target:
            j += 1
        seg = lengths[j + 1] - lengths[j]
        f = (target - lengths[j]) / seg if seg > 0.0 else 0.0
        out.append(stroke[j].lerp(stroke[j + 1], min(max(f, 0.0), 1.0)))
    return out


# --------------------------------------------------------------------------
# draw operator
# --------------------------------------------------------------------------

_draw_handle = None
_draw_points = []


def _draw_callback():
    if len(_draw_points) < 2:
        return
    try:
        import gpu
        from gpu_extras.batch import batch_for_shader
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": [p[:] for p in _draw_points]})
        gpu.state.line_width_set(3.0)
        gpu.state.depth_test_set('NONE')
        shader.bind()
        shader.uniform_float("color", (1.0, 0.55, 0.2, 1.0))
        batch.draw(shader)
        gpu.state.line_width_set(1.0)
    except Exception:  # drawing must never break the modal tool
        pass


class TOONHAIR_OT_draw(bpy.types.Operator):
    bl_idname = "toon_hair.draw"
    bl_label = "頭に描く"
    bl_description = ("頭の上をドラッグして毛束を描きます。"
                      "Ctrl+クリックで房を消す / Ctrl+Z で直前の房を取り消し / Esc・右クリックで終了")
    bl_options = {'REGISTER', 'UNDO'}

    preset: EnumProperty(name="パーツ", items=[('ACTIVE', "選択中のグループ", "")] + presets.ITEMS,
                         default='ACTIVE')
    lift: FloatProperty(name="浮かせる量", default=0.1, min=0.0, soft_max=0.5,
                        description="頭の表面から毛束を浮かせる量 (頭の半径比)")

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D'

    # ---- helpers -----------------------------------------------------------
    def _group(self):
        return bpy.data.objects.get(self.group_name)

    def _ray(self, context, coord):
        from bpy_extras import view3d_utils
        region, rv3d = context.region, context.region_data
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
        head = context.scene.toon_hair_head
        dg = context.evaluated_depsgraph_get()
        if head is not None:
            mi = head.matrix_world.inverted()
            o = mi @ origin
            d = (mi.to_3x3() @ direction).normalized()
            ok, loc, nor, _idx = head.ray_cast(o, d, depsgraph=dg)
            if ok:
                nw = (head.matrix_world.to_3x3().inverted().transposed() @ nor).normalized()
                return True, head.matrix_world @ loc, nw
            return False, None, None
        ok, loc, nor, _i, ob, _m = context.scene.ray_cast(dg, origin, direction)
        if ok and build.owner_group(ob.original if hasattr(ob, "original") else ob) is None:
            return True, loc, nor
        return False, None, None

    def _plane_point(self, context, coord, depth_point):
        from bpy_extras import view3d_utils
        return view3d_utils.region_2d_to_location_3d(context.region, context.region_data, coord, depth_point)

    def _add_point(self, context, event):
        coord = (event.mouse_region_x, event.mouse_region_y)
        hit, loc, nor = self._ray(context, coord)
        n = len(self.stroke)
        if hit:
            ramp = min(1.0, n / 3.0)       # root sits on the scalp, then lifts
            p = loc + nor * (self.lift * self.R * ramp)
        else:
            if not self.stroke:
                return
            p = self._plane_point(context, coord, self.stroke[-1])
        if self.stroke and (p - self.stroke[-1]).length < self.R * 0.04:
            return
        self.stroke.append(p)
        _draw_points[:] = self.stroke

    def _finish_stroke(self, context):
        stroke = self.stroke
        self.stroke = []
        _draw_points.clear()
        g = self._group()
        if g is None or len(stroke) < 2:
            return
        length = sum((stroke[i] - stroke[i - 1]).length for i in range(1, len(stroke)))
        if length < self.R * 0.15:
            return
        ctrl = stroke_to_controls(stroke, max_points=8, min_points=3, spacing=self.R * 0.35)
        inv = g.matrix_world.inverted()
        add_bezier(g.data, [inv @ p for p in ctrl])
        self.added.append(len(g.data.splines) - 1)
        build.rebuild(g)

    def _delete_near(self, context, event):
        from bpy_extras import view3d_utils
        g = self._group()
        if g is None:
            return
        region, rv3d = context.region, context.region_data
        mouse = Vector((event.mouse_region_x, event.mouse_region_y))
        mw = g.matrix_world
        center = build.center_of(g.toon_hair)
        best, best_d = None, 25.0
        for idx, sp in enumerate(g.data.splines):
            pts = [mw @ bp.co for bp in sp.bezier_points] if sp.type == 'BEZIER' \
                else [mw @ p.co.to_3d() for p in sp.points]
            cand = list(pts)
            if g.toon_hair.mirror_x:
                cand += [center.mirror(p) for p in pts]
            for a, b in zip(cand, cand[1:]):
                for f in (0.0, 0.25, 0.5, 0.75, 1.0):
                    s = view3d_utils.location_3d_to_region_2d(region, rv3d, a.lerp(b, f))
                    if s is not None and (s - mouse).length < best_d:
                        best, best_d = idx, (s - mouse).length
        if best is not None:
            g.data.splines.remove(g.data.splines[best])
            build.on_spline_removed(g, best)
            self.added = [i if i < best else i - 1 for i in self.added if i != best]
            build.rebuild(g)

    def _undo_last(self):
        g = self._group()
        if g is None or not self.added:
            return
        idx = self.added.pop()
        if idx < len(g.data.splines):
            g.data.splines.remove(g.data.splines[idx])
            build.on_spline_removed(g, idx)
            build.rebuild(g)

    # ---- modal -----------------------------------------------------------------
    def invoke(self, context, event):
        global _draw_handle
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        center = bpy.data.objects.get(CENTER_NAME)
        if center is None:
            from . import active_group
            g = active_group(context)
            center = g.toon_hair.center if g is not None else None
        if center is None:
            center = ensure_center(context)
        if self.preset == 'ACTIVE':
            from . import active_group
            g = active_group(context)
            if g is None:
                g = new_group(context, 'CUSTOM', center)
        else:
            g = new_group(context, self.preset, center)
        for o in context.selected_objects:
            o.select_set(False)
        g.select_set(True)
        context.view_layer.objects.active = g
        build.rebuild(g)
        self.group_name = g.name
        self.R = head_radius(g.toon_hair.center or center)
        self.stroke = []
        self.added = []
        self.drawing = False
        _draw_points.clear()
        if _draw_handle is None:
            _draw_handle = bpy.types.SpaceView3D.draw_handler_add(_draw_callback, (), 'WINDOW', 'POST_VIEW')
        context.window_manager.modal_handler_add(self)
        context.area.header_text_set(
            f"[{g.name}] ドラッグ: 描く | Ctrl+クリック: 消す | Ctrl+Z: 取り消し | "
            "中ボタン/ホイール: 視点 | Esc・右クリック・Enter: 終了")
        return {'RUNNING_MODAL'}

    def _end(self, context):
        global _draw_handle
        if _draw_handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
            _draw_handle = None
        _draw_points.clear()
        if context.area is not None:
            context.area.header_text_set(None)
            context.area.tag_redraw()

    def modal(self, context, event):
        if context.area is None:
            self._end(context)
            return {'CANCELLED'}
        context.area.tag_redraw()
        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM',
                          'NDOF_MOTION'} or event.type.startswith('NUMPAD'):
            return {'PASS_THROUGH'}
        if event.type in {'ESC', 'RIGHTMOUSE', 'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
            if self.drawing:
                self.drawing = False
                self._finish_stroke(context)
            self._end(context)
            return {'FINISHED'}
        if event.type == 'Z' and event.ctrl and event.value == 'PRESS':
            self._undo_last()
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE':
            if event.value == 'PRESS':
                if event.ctrl:
                    self._delete_near(context, event)
                    return {'RUNNING_MODAL'}
                self.drawing = True
                self.stroke = []
                self._add_point(context, event)
                if not self.stroke:          # started off the head: ignore
                    self.drawing = False
            elif event.value == 'RELEASE' and self.drawing:
                self.drawing = False
                self._finish_stroke(context)
            return {'RUNNING_MODAL'}
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'} and self.drawing:
            self._add_point(context, event)
            return {'RUNNING_MODAL'}
        return {'RUNNING_MODAL'}


classes = (TOONHAIR_OT_setup_head, TOONHAIR_OT_draw)
