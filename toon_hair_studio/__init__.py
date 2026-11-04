# SPDX-License-Identifier: GPL-3.0-or-later
"""Toon Hair Studio - clean-topology anime hair meshes for VRM / VRChat.

Draw / edit Bezier guide curves; every spline becomes a quad-only lock
(triangles only at pointed tips) with:
  * Low / Mid / High poly presets
  * backface mode: closed volume / flipped back polygons / single sheet
  * branching locks (分岐毛) and lateral connection (横連結)
  * optional head-volume custom normals
  * optional inverted-hull outline mesh with tapered tips
"""

bl_info = {
    "name": "Toon Hair Studio",
    "author": "lionjazz15-cpu",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "3D View > Sidebar > ToonHair",
    "description": "Clean-topology anime hair meshes for VRM / VRChat (branching, connected locks, outline)",
    "category": "Mesh",
}

import math

import bpy
from bpy.app.handlers import persistent
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty,
                       PointerProperty)
from mathutils import Vector

from . import build, core, materials, presets
from contextlib import contextmanager

# ==========================================================================
# Properties
# ==========================================================================

_suspend_update = False


def _upd(self, context):
    if _suspend_update or not self.auto_update:
        return
    ob = self.id_data
    if build.is_group(ob):
        build.rebuild(ob)


SECTION_PRESETS = {
    # key: (label, description, profile, thickness, curvature, bevel)
    'TUBE': ("筒", "円い断面の筒 (おさげ・縦ロール・太い束)", 'ROUND', 1.0, 0.0, 0.2),
    'HALF': ("半円", "表が丸く裏が平ら (頭に沿う太い房)", 'HALF', 0.5, 0.0, 0.25),
    'LENS': ("レンズ", "両端が鋭い定番のアニメ房", 'LENS', 0.25, 0.12, 0.3),
    'CRESCENT': ("三日月", "強く反った薄い房", 'LENS', 0.2, 0.35, 0.3),
    'RIDGE': ("山形", "中央に稜線のある房", 'RIDGE', 0.3, 0.15, 0.25),
    'TRIANGLE': ("三角", "表が山形・裏が平ら", 'TRIANGLE', 0.45, 0.0, 0.2),
    'TRAPEZOID': ("台形", "平らな面と斜めの肩", 'TRAPEZOID', 0.35, 0.05, 0.3),
    'FLAT': ("板", "均一な厚みの板", 'FLAT', 0.15, 0.08, 0.5),
}


def _upd_section(self, context):
    pr = SECTION_PRESETS.get(self.section_preset)
    if pr:
        with _suspended():
            self.profile, self.thickness, self.curvature, self.bevel = pr[2:]
            if self.backface != 'CLOSED':
                self.backface = 'CLOSED'
    _upd(self, context)


LOD_TABLE = {
    'LOW': (6, 2),
    'MID': (12, 4),
    'HIGH': (20, 6),
}


@contextmanager
def _suspended():
    """Change several settings without rebuilding after each one."""
    global _suspend_update
    prev = _suspend_update
    _suspend_update = True
    try:
        yield
    finally:
        _suspend_update = prev


def _upd_lod(self, context):
    if self.lod in LOD_TABLE:
        with _suspended():
            self.length_segments, self.profile_segments = LOD_TABLE[self.lod]
    _upd(self, context)


def _is_empty_or_mesh(self, ob):
    return ob.type in {'EMPTY', 'MESH'}


def _is_mesh(self, ob):
    return ob.type == 'MESH'


class ToonHairLink(bpy.types.PropertyGroup):
    """A manual lateral join between two guide splines over [t0, t1]."""
    a: IntProperty()
    b: IntProperty()
    t0: FloatProperty(min=0.0, max=1.0)
    t1: FloatProperty(min=0.0, max=1.0)


class ToonHairSettings(bpy.types.PropertyGroup):
    links: bpy.props.CollectionProperty(type=ToonHairLink)
    uv_seams: BoolProperty(name="UVシーム", default=True, update=_upd,
                           description="表面と裏面(+ベベル・ふた)の境目に自動でシームを入れる")
    enabled: BoolProperty(default=False)
    auto_update: BoolProperty(name="自動更新 Live", default=True,
                              description="カーブを編集したら即メッシュを再生成")
    hair_object: PointerProperty(type=bpy.types.Object)
    outline_object: PointerProperty(type=bpy.types.Object)
    center: PointerProperty(name="頭の中心 Head", type=bpy.types.Object, poll=_is_empty_or_mesh,
                            update=_upd,
                            description="頭の中心(スケールで楕円体)。毛束の向きと球状法線の基準")
    material: PointerProperty(name="髪マテリアル", type=bpy.types.Material, update=_upd)
    outline_material: PointerProperty(name="線マテリアル", type=bpy.types.Material, update=_upd)

    # --- topology ---
    lod: EnumProperty(
        name="ポリ数 LOD",
        items=[('LOW', "Low", "長さ6 × 断面2 (超ローポリ)"),
               ('MID', "Mid", "長さ12 × 断面4 (ミドルポリ)"),
               ('HIGH', "High", "長さ20 × 断面6"),
               ('CUSTOM', "Custom", "手動")],
        default='MID', update=_upd_lod)
    length_segments: IntProperty(name="長さ分割", default=12, min=2, max=64, update=_upd)
    profile_segments: IntProperty(name="断面分割", default=4, min=1, max=16, update=_upd)
    tip_bias: FloatProperty(name="毛先に分割を寄せる", default=0.3, min=0.0, max=1.0, update=_upd)
    bend_density: FloatProperty(name="曲がりに分割を寄せる", default=0.5, min=0.0, max=3.0, update=_upd,
                                description="カーブが曲がっている所ほどループを細かくする (グループ内で共通なので連結しても行がそろう)")
    bevel: FloatProperty(name="ベベル", default=0.3, min=0.0, max=0.9, update=_upd,
                         description="厚みありの時、表と裏の間の縁に細い側面の帯を1本入れる (厚みに対する比率)。0でナイフエッジ")

    # --- cross section ---
    profile: EnumProperty(
        name="断面",
        items=[('LENS', "レンズ", "両端が鋭い。アニメ髪の定番"),
               ('RIDGE', "山形", "中央に稜線。シャープな影とハイライト"),
               ('ROUND', "丸/筒", "ふっくら。厚み1.0で筒"),
               ('HALF', "半円", "表が丸く裏が平ら"),
               ('TRIANGLE', "三角", "表が山形で裏が平ら"),
               ('TRAPEZOID', "台形", "平らな面と斜めの肩"),
               ('FLAT', "板", "ほぼ均一の厚み")],
        default='LENS', update=_upd)
    section_preset: EnumProperty(
        name="断面プリセット",
        items=[(k, v[0], v[1]) for k, v in SECTION_PRESETS.items()],
        default='LENS', update=_upd_section,
        description="断面の形・厚み・反り・ベベルをまとめて設定")
    backface: EnumProperty(
        name="裏面",
        items=[('CLOSED', "厚みあり", "閉じた立体。カリングON・アウトラインが最も安定"),
               ('FLIPPED', "裏面ポリ", "片面+反転コピー。Unity/MToonのCull Backでも裏が見える"),
               ('SINGLE', "なし(片面)", "最軽量。両面表示マテリアルで表示")],
        default='CLOSED', update=_upd)
    width: FloatProperty(name="幅", default=0.03, min=0.0001, soft_max=0.2, subtype='DISTANCE',
                         unit='LENGTH', update=_upd)
    thickness: FloatProperty(name="厚み", default=0.25, min=0.0, soft_max=1.0, update=_upd,
                             description="幅に対する厚みの比率 (厚みありのみ)")
    curvature: FloatProperty(name="反り", default=0.12, soft_min=-0.5, soft_max=0.5, update=_upd,
                             description="断面のアーチ量 (幅比)")
    flip_offset: FloatProperty(name="裏面オフセット", default=0.0003, min=0.0, soft_max=0.005,
                               subtype='DISTANCE', unit='LENGTH', update=_upd)

    # --- silhouette ---
    root_ratio: FloatProperty(name="根元の幅", default=0.7, min=0.0, soft_max=2.0, update=_upd)
    bulge_pos: FloatProperty(name="膨らみ位置", default=0.25, min=0.0, max=0.95, update=_upd)
    tip_shape: FloatProperty(name="毛先の形", default=1.4, min=0.2, max=5.0, update=_upd,
                             description="<1 鋭く尖る / 1 直線 / >1 ふっくらしてから尖る")
    tip_mode: EnumProperty(
        name="毛先",
        items=[('POINT', "尖り", "房ごとに1頂点に収束 (三角形は毛先のみ)"),
               ('BLUNT', "平ら", "房ごとに細くなって切りっぱなし"),
               ('CUT', "ぱっつん", "幅を保ったまま切りそろえ。横連結すると裾まで一続き")],
        default='POINT', update=_upd)
    tip_min: FloatProperty(name="毛先の最小幅", default=0.12, min=0.0, max=1.0, update=_upd)
    cut_taper: FloatProperty(name="裾の細り", default=0.05, min=0.0, max=0.9, update=_upd,
                             description="ぱっつんの時、裾に向かって房を細くする量")
    cut_enable: BoolProperty(name="水平カット", default=False, update=_upd,
                             description="全部の房を頭基準の高さでまっすぐ切りそろえる (ぱっつん)")
    cut_height: FloatProperty(name="カットの高さ", default=0.2, min=-1.5, max=1.0, update=_upd,
                              description="頭の中心を0、頭頂を1、あごの辺りを-1とした高さ")
    root_cap: BoolProperty(name="根元を閉じる", default=False, update=_upd)
    twist: FloatProperty(name="ねじれ", default=0.0, subtype='ANGLE', soft_min=-math.pi * 2,
                         soft_max=math.pi * 2, update=_upd)
    # --- volume / layering ---
    layers: EnumProperty(
        name="重ね",
        items=[('NONE', "なし", "1層"),
               ('FRONT', "前に重ねる", "手前に1層 (短め・外ハネ)"),
               ('BACK', "後ろに重ねる", "奥に1層 (内巻き)"),
               ('BOTH', "前後に重ねる", "手前と奥に1層ずつ。立体的な束感")],
        default='NONE', update=_upd,
        description="同じカーブから手前/奥の層を自動で作り、少しずらして重ねる")
    layer_gap: FloatProperty(name="層の間隔", default=0.08, min=0.0, soft_max=0.4, update=_upd,
                             description="層どうしの前後の距離 (頭の半径比)")
    layer_shift: FloatProperty(name="層の左右ずらし", default=0.5, soft_min=-1.0, soft_max=1.0, update=_upd,
                               description="層を房の幅の何割ずらすか。つなぎ目を隠す")
    layer_front_len: FloatProperty(name="前の層の長さ", default=0.8, min=0.1, max=1.0, update=_upd)
    layer_back_len: FloatProperty(name="後ろの層の長さ", default=0.92, min=0.1, max=1.0, update=_upd)
    layer_width: FloatProperty(name="重ねる層の幅", default=0.85, min=0.1, soft_max=2.0, update=_upd)
    layer_flip: FloatProperty(name="層の毛先の開き", default=0.12, soft_min=-0.5, soft_max=0.5, update=_upd,
                              description="前の層は外へ、後ろの層は内へ毛先を向ける量 (頭の半径比)")
    puff: FloatProperty(name="ふくらみ", default=0.0, soft_min=-0.3, soft_max=0.6, update=_upd,
                        description="房の途中を外へ膨らませる量 (頭の半径比)")
    puff_pos: FloatProperty(name="ふくらみ位置", default=0.6, min=0.05, max=0.95, update=_upd)
    tip_flip: FloatProperty(name="毛先 外ハネ/内巻き", default=0.0, soft_min=-0.5, soft_max=0.5, update=_upd,
                            description="+で外ハネ、-で内巻き (頭の半径比)")
    lift: FloatProperty(name="浮かせる", default=0.0, soft_min=-0.2, soft_max=0.5, update=_upd,
                        description="グループ全体を頭から浮かせる量 (頭の半径比)。層を前後に重ねる時に")
    roll: FloatProperty(name="向き(立てる)", default=0.0, subtype='ANGLE', soft_min=-math.pi, soft_max=math.pi,
                        update=_upd, description="房を中心線のまわりに回して立てる (ひさし前髪・跳ね毛)")
    curl_turns: FloatProperty(name="カール回数", default=0.0, min=0.0, soft_max=8.0, update=_upd,
                              description="縦ロールのように房をらせん状に巻く回数")
    curl_radius: FloatProperty(name="カール半径", default=0.5, min=0.0, soft_max=2.0, update=_upd,
                               description="らせんの半径 (幅比)")
    curl_start: FloatProperty(name="カール開始", default=0.3, min=0.0, max=0.95, update=_upd)
    wave_amp: FloatProperty(name="うねり", default=0.0, min=0.0, soft_max=1.5, update=_upd,
                            description="左右にうねらせる量 (幅比)")
    wave_freq: FloatProperty(name="うねり回数", default=2.0, min=0.1, soft_max=8.0, update=_upd)
    root_extend: FloatProperty(name="根元を埋める", default=0.0, min=0.0, soft_max=0.05,
                               subtype='DISTANCE', unit='LENGTH', update=_upd)
    mirror_x: BoolProperty(name="X ミラー", default=False, update=_upd,
                           description="頭の中心のX軸で左右対称に生成")

    # --- branching (分岐毛) ---
    branches: IntProperty(name="分岐数", default=1, min=1, max=6, update=_upd,
                          description="1本のガイドから途中で分かれる毛先の数")
    branch_jitter: IntProperty(name="分岐数のばらつき", default=0, min=0, max=2, update=_upd,
                               description="房ごとにランダムで ±この数だけ分岐数を変える")
    branch_pos: FloatProperty(name="分岐位置", default=0.5, min=0.05, max=0.9, update=_upd)
    branch_spread: FloatProperty(name="広がり", default=0.25, min=0.0, soft_max=1.5, update=_upd,
                                 description="分岐後に毛先が外へ開く量(幅比)")
    branch_length_var: FloatProperty(name="長さの差", default=0.25, min=0.0, max=0.65, update=_upd,
                                     description="分岐後の部分を最大どれだけ短くするか(比率)")
    branch_pattern: EnumProperty(
        name="長さパターン",
        items=[('CENTER', "中央が長い", ""),
               ('OUTER', "外側が長い", ""),
               ('RANDOM', "ランダム", ""),
               ('EVEN', "均等", "")],
        default='CENTER', update=_upd)
    seed: IntProperty(name="シード", default=0, min=0, update=_upd)

    # --- connected sheet (1房ずつ独立しない髪 / 横連結) ---
    connect: BoolProperty(name="毛束を連結", default=False, update=_upd,
                          description="隣り合う毛束を根元〜途中まで1枚の面でつなぎ、毛先だけ分かれるギザギザの髪にする")
    order: EnumProperty(
        name="並び順",
        items=[('AZIMUTH', "頭の周り順", "根元の方位角で自動ソート"),
               ('CURVE', "カーブ順", "スプラインを作った順")],
        default='AZIMUTH', update=_upd)
    connect_style: EnumProperty(
        name="連結スタイル",
        items=[('SMOOTH', "なめらか", "隣の房と段差なく1枚の面としてつなぐ (反り・厚みをつなぎ目で平らにし、曲線補間+スムージング)"),
               ('GROOVE', "毛束感(溝)", "房ごとに反りを残し、つなぎ目に溝を入れる")],
        default='SMOOTH', update=_upd)
    smooth_iterations: IntProperty(name="なめらかさ", default=4, min=0, max=20, update=_upd,
                                   description="連結部分にかける縮まないスムージング(Taubin)の回数")
    connect_ratio: FloatProperty(name="連結する距離", default=1.8, min=0.1, soft_max=6.0, update=_upd,
                                 description="房の中間地点同士がこの距離(幅比)以内ならつなぐ。離れていれば別の塊になる")
    split: FloatProperty(name="切れ込み位置", default=0.55, min=0.0, max=1.0, update=_upd,
                         description="ここ(根元0〜毛先1)より先で毛束が分かれる")
    notch_soft: FloatProperty(name="切れ込みの開き", default=0.5, min=0.02, max=1.0, update=_upd,
                              description="小さいほど切れ込みが急に開く")
    seam_thickness: FloatProperty(name="つなぎ目の厚み", default=0.6, min=0.0, max=1.0, update=_upd,
                                  description="厚みありの時、毛束同士の境目に残す厚み。0で深い溝")

    # --- shading ---
    normal_mode: EnumProperty(
        name="法線",
        items=[('VOLUME', "あり: 頭の球体", "カスタム法線あり。頭の楕円体方向へブレンドし、影が塊でまとまる(アニメ髪の定番)"),
               ('NONE', "なし", "カスタム法線なし。普通のスムーズシェード"),
               ('PROXY', "あり: プロキシ転写", "指定メッシュからData Transferで法線転写")],
        default='VOLUME', update=_upd)
    normal_blend: FloatProperty(name="ブレンド", default=0.6, min=0.0, max=1.0, update=_upd)
    normal_proxy: PointerProperty(name="プロキシ", type=bpy.types.Object, poll=_is_mesh, update=_upd)

    # --- outline ---
    outline: BoolProperty(name="アウトライン", default=False, update=_upd,
                          description="反転ハルのアウトラインメッシュを生成(MToon/lilToon/Poiyomiのシェーダー線を使うなら不要)")
    outline_width: FloatProperty(name="線の太さ", default=0.0012, min=0.0, soft_max=0.01,
                                 subtype='DISTANCE', unit='LENGTH', update=_upd)
    outline_tip_ratio: FloatProperty(name="毛先の線の細さ", default=0.15, min=0.0, max=1.0,
                                     update=_upd)
    outline_taper_start: FloatProperty(name="細くなり始め", default=0.5, min=0.0, max=1.0,
                                       update=_upd)


# ==========================================================================
# Helpers
# ==========================================================================

CENTER_NAME = "TH_HeadCenter"


def _find_center(context):
    ob = bpy.data.objects.get(CENTER_NAME)
    if ob is not None:
        return ob
    for o in bpy.data.objects:
        if build.is_group(o) and o.toon_hair.center is not None:
            return o.toon_hair.center
    return None


def _create_center(context, location=None, radius=0.1):
    ob = bpy.data.objects.new(CENTER_NAME, None)
    ob.empty_display_type = 'SPHERE'
    ob.empty_display_size = 1.0
    ob.scale = (radius, radius, radius)
    ob.location = location if location is not None else context.scene.cursor.location
    context.scene.collection.objects.link(ob)
    context.view_layer.update()          # make matrix_world valid right away
    return ob


def _head_radius(center):
    if center is None:
        return 0.1
    return max(center.matrix_world.to_scale())


def _new_group(context, name, center, spline_points=None, **settings):
    cu = bpy.data.curves.new(name, 'CURVE')
    cu.dimensions = '3D'
    ob = bpy.data.objects.new(name, cu)
    context.collection.objects.link(ob)
    ob.show_in_front = True
    st = ob.toon_hair
    with _suspended():
        st.enabled = True
        st.center = center
        st.material = materials.ensure_hair_material()
        st.outline_material = materials.ensure_outline_material()
        for k, v in settings.items():
            setattr(st, k, v)
    for pts in (spline_points or []):
        _add_bezier(cu, [ob.matrix_world.inverted() @ p for p in pts])
    return ob


def _add_bezier(cu, points):
    sp = cu.splines.new('BEZIER')
    sp.bezier_points.add(len(points) - 1)
    for bp, co in zip(sp.bezier_points, points):
        bp.co = co
        bp.handle_left_type = 'AUTO'
        bp.handle_right_type = 'AUTO'
    return sp


def _head_dir(az_deg, el_deg):
    az, el = math.radians(az_deg), math.radians(el_deg)
    return Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))


def _strand_points(center, az0, az1, el0, el1, r0, r1, n=4, hang=0.0, flare=0.0):
    """Points from (az0, el0) to (az1, el1) around the head.

    Above the equator the strand hugs the head; below it, it hangs down
    (gravity) instead of wrapping under the chin. ``hang`` lengthens the
    hanging part, ``flare`` spreads it outward.
    """
    c = center.matrix_world.to_translation()
    R = _head_radius(center)
    pts = []
    for k in range(n):
        f = k / (n - 1)
        fa = 1.0 - (1.0 - f) ** 2.5          # spread out quickly from the crown
        az = math.radians(az0 + (az1 - az0) * fa)
        el = math.radians(el0 + (el1 - el0) * f)
        r = (r0 + (r1 - r0) * f) * R
        horiz = Vector((math.sin(az), -math.cos(az), 0.0))
        if el >= 0.0:
            p = c + horiz * (r * math.cos(el)) + Vector((0, 0, r * math.sin(el)))
        else:
            p = c + horiz * (r * (1.0 + flare * f * f)) + Vector((0, 0, r * math.sin(el) * (1.0 + hang)))
        pts.append(p)
    return pts


def _whorl_strand(center, whorl, az, el_end, r_end=1.12, n=5, hang=0.0, flare=0.0, drop_n=2):
    """Guide from the crown whorl over the head (great circle) to (az, el_end).

    Below the equator the guide stops hugging the head and hangs down."""
    c = center.matrix_world.to_translation()
    R = _head_radius(center)
    hanging = el_end < 0.0
    end = _head_dir(az, 0.0 if hanging else el_end)
    w = whorl.normalized()
    ang = w.angle(end)
    axis = w.cross(end)
    pts = []
    for k in range(n):
        f = k / (n - 1)
        if axis.length > 1e-6:
            from mathutils import Quaternion
            d = Quaternion(axis.normalized(), ang * f) @ w
        else:
            d = w
        pts.append(c + d * (R * (1.0 + (r_end - 1.0) * f)))
    if hanging:
        horiz = Vector((end.x, end.y, 0.0)).normalized()
        depth = R * math.sin(math.radians(-el_end)) * (1.0 + hang)
        for k in range(1, drop_n + 1):
            f = k / drop_n
            pts.append(c + horiz * (R * r_end * (1.0 + flare * f)) - Vector((0, 0, depth * f)))
    return pts


def active_group(context):
    return build.owner_group(context.active_object)


# ==========================================================================
# Operators
# ==========================================================================

class TOONHAIR_OT_create_center(bpy.types.Operator):
    bl_idname = "toon_hair.create_center"
    bl_label = "頭の中心を作成"
    bl_description = "3Dカーソル位置に頭の中心(楕円体)を作成。頭に合わせて移動/スケールしてください"
    bl_options = {'REGISTER', 'UNDO'}

    radius: FloatProperty(name="頭の半径", default=0.1, min=0.001, subtype='DISTANCE', unit='LENGTH')

    def execute(self, context):
        ob = _create_center(context, radius=self.radius)
        g = active_group(context)
        if g is not None:
            g.toon_hair.center = ob
        return {'FINISHED'}


class TOONHAIR_OT_add_group(bpy.types.Operator):
    bl_idname = "toon_hair.add_group"
    bl_label = "毛束グループを追加"
    bl_description = "新しいガイドカーブ(毛束グループ)を作成。1スプライン=1毛束"
    bl_options = {'REGISTER', 'UNDO'}

    preset: EnumProperty(name="パーツ", items=presets.ITEMS, default='CUSTOM')
    edit: BoolProperty(name="カーブ編集モードに入る", default=True)

    def execute(self, context):
        from . import draw
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        center = _find_center(context) or _create_center(context)
        ob = draw.new_group(context, self.preset, center)
        # one starter strand from the crown over the front, to edit freely
        whorl = _head_dir(180, 72)
        az = {'SIDE': 70, 'SIDEBURN': 85, 'BACK': 180, 'BACK_VOLUME': 180, 'TAIL': 150,
              'DRILL': 85}.get(self.preset, 0)
        el = -60 if self.preset in {'SIDE', 'BACK', 'BACK_VOLUME', 'SIDEBURN', 'TAIL', 'DRILL'} else 10
        pts = _whorl_strand(center, whorl, az, el, 1.12, n=5, hang=0.6)
        inv = ob.matrix_world.inverted()
        _add_bezier(ob.data, [inv @ p for p in pts])
        for o in context.selected_objects:
            o.select_set(False)
        ob.select_set(True)
        context.view_layer.objects.active = ob
        build.rebuild(ob)
        if self.edit:
            bpy.ops.object.mode_set(mode='EDIT')
        return {'FINISHED'}


class TOONHAIR_OT_convert_curve(bpy.types.Operator):
    bl_idname = "toon_hair.convert_curve"
    bl_label = "選択カーブを毛束に"
    bl_description = "選択中のカーブを毛束グループにする (ベベルは無効化)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return any(o.type == 'CURVE' for o in context.selected_objects)

    def execute(self, context):
        center = _find_center(context) or _create_center(context)
        R = _head_radius(center)
        for ob in context.selected_objects:
            if ob.type != 'CURVE' or ob.toon_hair.enabled:
                continue
            ob.data.bevel_depth = 0.0
            ob.data.bevel_mode = 'ROUND'
            ob.data.extrude = 0.0
            ob.data.dimensions = '3D'
            st = ob.toon_hair
            with _suspended():
                st.enabled = True
                st.center = center
                st.width = R * 0.35
                st.outline_width = R * 0.012
                st.flip_offset = R * 0.003
                st.material = materials.ensure_hair_material()
                st.outline_material = materials.ensure_outline_material()
            ob.show_in_front = True
            build.rebuild(ob)
        return {'FINISHED'}


class TOONHAIR_OT_add_strand(bpy.types.Operator):
    bl_idname = "toon_hair.add_strand"
    bl_label = "3Dカーソルに毛束を追加"
    bl_description = "アクティブグループに、3Dカーソル位置を根元にした毛束を1本追加"
    bl_options = {'REGISTER', 'UNDO'}

    length: FloatProperty(name="長さ(頭半径比)", default=1.6, min=0.1, soft_max=8.0)
    hang: FloatProperty(name="垂れ", default=0.6, min=0.0, max=1.0)

    @classmethod
    def poll(cls, context):
        return active_group(context) is not None

    def execute(self, context):
        g = active_group(context)
        center = g.toon_hair.center
        root = context.scene.cursor.location.copy()
        R = _head_radius(center)
        c = center.matrix_world.to_translation() if center else Vector()
        out = root - c
        out = out.normalized() if out.length > 1e-6 else Vector((0, -1, 0))
        down = Vector((0, 0, -1))
        L = self.length * R
        pts = []
        for k in range(4):
            f = k / 3
            d = out.lerp(down, min(1.0, self.hang * (0.4 + f))).normalized()
            p = root + out * (0.15 * R * math.sin(f * math.pi)) + d * (L * f)
            pts.append(p)
        inv = g.matrix_world.inverted()
        was_edit = context.mode == 'EDIT_CURVE' and context.active_object == g
        if was_edit:
            bpy.ops.object.mode_set(mode='OBJECT')
        _add_bezier(g.data, [inv @ p for p in pts])
        if was_edit:
            bpy.ops.object.mode_set(mode='EDIT')
        build.rebuild(g)
        return {'FINISHED'}


class TOONHAIR_OT_sample(bpy.types.Operator):
    bl_idname = "toon_hair.sample_hairstyle"
    bl_label = "サンプル髪型を生成"
    bl_description = "頭の中心の周りに 前髪/横髪/後ろ髪/アホ毛 のサンプルを生成"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        center = _find_center(context) or _create_center(context)
        R = _head_radius(center)
        common = dict(outline_width=R * 0.012, flip_offset=R * 0.003)

        # all guides grow from one crown whorl with thin roots, so the locks
        # fan out like an umbrella and tile the head without overlapping
        whorl = _head_dir(180, 72)
        thin = dict(root_ratio=0.12, bulge_pos=0.55, connect=True, connect_ratio=2.2)

        # 前髪: 5本のガイド、それぞれ2〜3本に分岐 + 横連結
        bangs = [_whorl_strand(center, whorl, az, -2 + abs(az) * 0.3, 1.13, n=6)
                 for az in (-50, -25, 0, 25, 50)]
        _new_group(context, "Hair_Bangs", center, bangs, width=R * 0.6, tip_shape=2.4,
                   split=0.7, branches=2, branch_jitter=1, branch_pos=0.8,
                   branch_spread=0.2, branch_length_var=0.45, branch_pattern='RANDOM',
                   seed=3, **dict(thin, bulge_pos=0.6), **common)

        # 横髪: 3本に分岐する房 (ミラー)
        sides = [_whorl_strand(center, whorl, az, -60, 1.12, n=6, hang=0.5 + i * 0.3,
                               flare=0.1 + i * 0.05)
                 for i, az in enumerate((62, 82, 102))]
        _new_group(context, "Hair_Side", center, sides, width=R * 0.6, mirror_x=True,
                   tip_shape=1.8, split=0.55, branches=3, branch_pos=0.68, branch_spread=0.3,
                   branch_length_var=0.5, branch_pattern='CENTER', **thin, **common)

        # 後ろ髪: 2本に分岐 + 横連結
        back = [_whorl_strand(center, whorl, az, -80, 1.14, n=6, hang=1.3, flare=0.22)
                for az in (115, 137, 159, 180, 201, 223, 245)]
        _new_group(context, "Hair_Back", center, back, width=R * 0.72, tip_shape=1.8,
                   thickness=0.3, split=0.7, branches=2, branch_jitter=1, branch_pos=0.8,
                   branch_spread=0.12, branch_length_var=0.45, branch_pattern='RANDOM',
                   seed=7, **thin, **common)

        c = center.matrix_world.to_translation()
        ahoge = [[c + _head_dir(10, 88) * R * 0.98,
                  c + _head_dir(10, 80) * R * 1.35 + Vector((0, 0, R * 0.25)),
                  c + _head_dir(-20, 70) * R * 1.45 + Vector((0, 0, R * 0.25))]]
        _new_group(context, "Hair_Ahoge", center, ahoge, width=R * 0.16, root_ratio=0.6,
                   bulge_pos=0.2, tip_shape=1.0, profile='RIDGE', **common)

        build.rebuild_all()
        return {'FINISHED'}


def _selected_splines(g):
    """[(spline index, selected arc params)] of splines with selected points."""
    out = []
    for idx, sp in enumerate(g.data.splines):
        ts = build.selected_ts(sp)
        if ts:
            out.append((idx, ts))
    return out


class TOONHAIR_OT_link_selected(bpy.types.Operator):
    bl_idname = "toon_hair.link_selected"
    bl_label = "選択点をつなぐ"
    bl_description = ("編集モードで選んだ点の所だけ、隣のカーブの房とつなぎます。"
                      "各カーブ1点=根元からその点まで / 2点以上=その間だけ。3本以上なら隣同士を順に")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return active_group(context) is not None

    def execute(self, context):
        g = active_group(context)
        if context.mode == 'EDIT_CURVE':
            # make sure the edit-mode selection is visible to the data
            g.update_from_editmode()
        sel = _selected_splines(g)
        if len(sel) < 2:
            self.report({'WARNING'}, "2本以上のカーブで点を選択してください (編集モード)")
            return {'CANCELLED'}
        center = build.center_of(g.toon_hair)
        mw = g.matrix_world

        def az(idx):
            smp = core.sample_spline(g.data.splines[idx], mw)
            return center.head_coord(smp[len(smp) // 2][0])[0]
        sel.sort(key=lambda it: az(it[0]))
        links = g.toon_hair.links
        made = 0
        for (ia, ta), (ib, tb) in zip(sel, sel[1:]):
            ra = (0.0, ta[0]) if len(ta) == 1 else (min(ta), max(ta))
            rb = (0.0, tb[0]) if len(tb) == 1 else (min(tb), max(tb))
            t0, t1 = 0.5 * (ra[0] + rb[0]), 0.5 * (ra[1] + rb[1])
            for i in range(len(links) - 1, -1, -1):
                if {links[i].a, links[i].b} == {ia, ib}:
                    links.remove(i)
            l = links.add()
            l.a, l.b, l.t0, l.t1 = ia, ib, t0, t1
            made += 1
        build.rebuild(g)
        self.report({'INFO'}, f"{made} か所つなぎました (自動の横連結より優先)")
        return {'FINISHED'}


class TOONHAIR_OT_unlink(bpy.types.Operator):
    bl_idname = "toon_hair.unlink"
    bl_label = "つなぎを解除"
    bl_description = "点を選んだカーブの手動のつなぎを解除 (全部: すべて解除)"
    bl_options = {'REGISTER', 'UNDO'}

    all: BoolProperty(name="全部", default=False)

    @classmethod
    def poll(cls, context):
        g = active_group(context)
        return g is not None and len(g.toon_hair.links) > 0

    def execute(self, context):
        g = active_group(context)
        links = g.toon_hair.links
        if self.all:
            links.clear()
        else:
            if context.mode == 'EDIT_CURVE':
                g.update_from_editmode()
            idx = {i for i, _ts in _selected_splines(g)}
            for i in range(len(links) - 1, -1, -1):
                if links[i].a in idx or links[i].b in idx:
                    links.remove(i)
        build.rebuild(g)
        return {'FINISHED'}


class TOONHAIR_OT_rebuild(bpy.types.Operator):
    bl_idname = "toon_hair.rebuild"
    bl_label = "再生成"
    bl_options = {'REGISTER', 'UNDO'}

    all: BoolProperty(name="全グループ", default=False)

    def execute(self, context):
        if self.all:
            build.rebuild_all()
        else:
            g = active_group(context)
            if g is None:
                self.report({'WARNING'}, "毛束グループが選択されていません")
                return {'CANCELLED'}
            build.rebuild(g)
        return {'FINISHED'}


class TOONHAIR_OT_bake(bpy.types.Operator):
    bl_idname = "toon_hair.bake"
    bl_label = "書き出し用メッシュに統合"
    bl_description = ("全(または選択)グループを1つの独立メッシュに統合。"
                      "カスタム法線・UV・頂点グループ(TH_root_tip / TH_pin)付き。VRM/FBX書き出し用")
    bl_options = {'REGISTER', 'UNDO'}

    selected_only: BoolProperty(name="選択グループのみ", default=False)
    with_outline: BoolProperty(name="アウトラインも出力", default=False,
                               description="MToon等のシェーダー側アウトラインを使うならOFF")
    name: bpy.props.StringProperty(name="名前", default="Hair_Baked")
    uv_mode: EnumProperty(
        name="UV",
        items=[('STRIP', "房ごと 0〜1", "U=幅方向, V=根元→毛先。グラデーションテクスチャ向け (重なりあり)"),
               ('UNWRAP', "シームで展開", "表/裏の自動シームで展開。テクスチャを描く人向け")],
        default='STRIP')

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        if self.selected_only:
            groups = list({build.owner_group(o) for o in context.selected_objects} - {None})
        else:
            groups = [o for o in bpy.data.objects if build.is_group(o)]
        if not groups:
            self.report({'WARNING'}, "毛束グループがありません")
            return {'CANCELLED'}
        ob, oob = build.bake(groups, self.name, self.with_outline, context.collection)
        if self.uv_mode == 'UNWRAP':
            build.unwrap(ob)
        proxy_groups = [g for g in groups if g.toon_hair.normal_mode == 'PROXY'
                        and g.toon_hair.normal_proxy is not None]
        if proxy_groups:
            build._sync_proxy_modifier(ob, proxy_groups[0].toon_hair)
        for o in context.selected_objects:
            o.select_set(False)
        ob.select_set(True)
        context.view_layer.objects.active = ob
        self.report({'INFO'}, f"{ob.name}: {len(ob.data.vertices)} verts, {len(ob.data.polygons)} faces")
        return {'FINISHED'}


# ==========================================================================
# UI
# ==========================================================================

class _Panel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "ToonHair"


class TOONHAIR_PT_main(_Panel, bpy.types.Panel):
    bl_label = "Toon Hair Studio"

    def draw(self, context):
        lay = self.layout
        sc = context.scene

        # ① 準備 ---------------------------------------------------------------
        box = lay.box()
        box.label(text="① 準備", icon='USER')
        head = sc.toon_hair_head
        if head is None:
            box.label(text="頭のメッシュを選択して ↓", icon='INFO')
        else:
            box.label(text=f"頭: {head.name}", icon='CHECKMARK')
        box.operator("toon_hair.setup_head", icon='SPHERE')

        # ② 作る ---------------------------------------------------------------
        box = lay.box()
        box.label(text="② 作る: パーツを押して頭の上をドラッグ", icon='GREASEPENCIL')
        grid = box.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
        for key in presets.ORDER:
            label, _name, icon, _fn = presets.PRESETS[key]
            grid.operator("toon_hair.draw", text=label, icon=icon).preset = key
        g = active_group(context)
        row = box.row(align=True)
        row.enabled = g is not None
        row.operator("toon_hair.draw", text="選択中のグループに描き足す", icon='ADD').preset = 'ACTIVE'
        col = box.column(align=True)
        col.label(text="カーブで作る (Tabで編集モード):", icon='CURVE_BEZCURVE')
        row = col.row(align=True)
        row.operator_menu_enum("toon_hair.add_group", "preset", text="グループ追加", icon='OUTLINER_OB_CURVE')
        row.operator("toon_hair.add_strand", text="カーソルに1房", icon='PLUS')
        col.operator("toon_hair.convert_curve", icon='CURVE_DATA')
        col.operator("toon_hair.sample_hairstyle", icon='OUTLINER_OB_HAIR')

        # ③ 調整 ---------------------------------------------------------------
        box = lay.box()
        if g is None:
            box.label(text="③ 調整: 毛束グループを選択してください", icon='INFO')
        else:
            st = g.toon_hair
            row = box.row()
            row.label(text=f"③ 調整: {g.name}", icon='CURVE_DATA')
            row.prop(st, "auto_update", text="", icon='FILE_REFRESH')
            col = box.column(align=True)
            col.row(align=True).prop(st, "lod", expand=True)
            col.prop(st, "section_preset", text="断面")
            col.prop(st, "width")
            col.prop(st, "thickness")
            col.prop(st, "branches")
            col.prop(st, "layers", text="重ね")
            col.row(align=True).prop(st, "tip_mode", expand=True)
            if st.tip_mode == 'CUT' or st.cut_enable:
                row = col.row(align=True)
                row.prop(st, "cut_enable", text="水平カット", toggle=True)
                sub = row.row(align=True)
                sub.active = st.cut_enable
                sub.prop(st, "cut_height", text="高さ")
            row = col.row(align=True)
            row.prop(st, "connect", text="自動でつなぐ", toggle=True)
            row.prop(st, "mirror_x", toggle=True)
            row = col.row(align=True)
            row.operator("toon_hair.link_selected", text="選択点をつなぐ", icon='LINKED')
            row.operator("toon_hair.unlink", text="", icon='UNLINKED').all = False
            if st.links:
                col.label(text=f"手動つなぎ {len(st.links)} か所 (自動より優先)", icon='LINKED')
            col.row(align=True).prop(st, "backface", expand=True)
            tri = build.triangle_count(st.hair_object)
            box.label(text=f"三角形: {tri:,}", icon='MESH_DATA')
            box.label(text="細かい設定は下のパネル / カーブ編集: Alt+S 幅, Ctrl+T ねじれ", icon='BLANK1')

        # ④ 書き出し -----------------------------------------------------------
        box = lay.box()
        total = sum(build.triangle_count(o.toon_hair.hair_object)
                    for o in bpy.data.objects if build.is_group(o))
        box.label(text=f"④ 書き出し (全体 三角形 {total:,})", icon='EXPORT')
        row = box.row(align=True)
        row.operator("toon_hair.bake", icon='EXPORT')
        row.operator("toon_hair.rebuild", text="", icon='FILE_REFRESH').all = True


class TOONHAIR_PT_shape(_Panel, bpy.types.Panel):
    bl_label = "形状 / トポロジー"
    bl_options = {'DEFAULT_CLOSED'}
    bl_parent_id = "TOONHAIR_PT_main"

    @classmethod
    def poll(cls, context):
        return active_group(context) is not None

    def draw(self, context):
        st = active_group(context).toon_hair
        lay = self.layout
        lay.use_property_split = True
        lay.use_property_decorate = False
        lay.row().prop(st, "lod", expand=True)
        col = lay.column(align=True)
        col.enabled = st.lod == 'CUSTOM'
        col.prop(st, "length_segments")
        col.prop(st, "profile_segments")
        lay.prop(st, "tip_bias")
        lay.prop(st, "bend_density")
        lay.separator()
        lay.prop(st, "backface")
        lay.prop(st, "profile")
        col = lay.column(align=True)
        col.prop(st, "width")
        sub = col.column(align=True)
        sub.enabled = st.backface == 'CLOSED'
        sub.prop(st, "thickness")
        sub.prop(st, "bevel")
        col.prop(st, "curvature")
        if st.backface == 'FLIPPED':
            lay.prop(st, "flip_offset")
        lay.separator()
        col = lay.column(align=True)
        col.prop(st, "root_ratio")
        col.prop(st, "bulge_pos")
        col.prop(st, "tip_shape")
        lay.prop(st, "tip_mode")
        if st.tip_mode == 'BLUNT':
            lay.prop(st, "tip_min")
        if st.tip_mode == 'CUT':
            lay.prop(st, "cut_taper")
        row = lay.row(heading="水平カット")
        row.prop(st, "cut_enable", text="")
        sub = row.row()
        sub.active = st.cut_enable
        sub.prop(st, "cut_height")
        col = lay.column(align=True)
        col.prop(st, "twist")
        col.prop(st, "root_extend")
        if st.backface == 'CLOSED':
            lay.prop(st, "root_cap")
        lay.prop(st, "mirror_x")
        col = lay.column(align=True)
        col.label(text="カール / うねり")
        col.prop(st, "curl_turns")
        sub = col.column(align=True)
        sub.active = st.curl_turns > 0.0
        sub.prop(st, "curl_radius")
        sub.prop(st, "curl_start")
        col.prop(st, "wave_amp")
        sub = col.column(align=True)
        sub.active = st.wave_amp > 0.0
        sub.prop(st, "wave_freq")
        lay.label(text="ヒント: 編集モードで Alt+S=部分的な幅, Ctrl+T=ねじり", icon='INFO')


class TOONHAIR_PT_volume(_Panel, bpy.types.Panel):
    bl_label = "立体 (重ね / ふくらみ)"
    bl_parent_id = "TOONHAIR_PT_main"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return active_group(context) is not None

    def draw(self, context):
        st = active_group(context).toon_hair
        lay = self.layout
        lay.use_property_split = True
        lay.use_property_decorate = False
        lay.prop(st, "layers")
        col = lay.column(align=True)
        col.active = st.layers != 'NONE'
        col.prop(st, "layer_gap")
        col.prop(st, "layer_shift")
        if st.layers in {'FRONT', 'BOTH'}:
            col.prop(st, "layer_front_len")
        if st.layers in {'BACK', 'BOTH'}:
            col.prop(st, "layer_back_len")
        col.prop(st, "layer_width")
        col.prop(st, "layer_flip")
        lay.separator()
        col = lay.column(align=True)
        col.prop(st, "puff")
        sub = col.column(align=True)
        sub.active = st.puff != 0.0
        sub.prop(st, "puff_pos")
        col.prop(st, "tip_flip")
        col.prop(st, "lift")
        col.prop(st, "roll")


class TOONHAIR_PT_branch(_Panel, bpy.types.Panel):
    bl_label = "分岐毛"
    bl_options = {'DEFAULT_CLOSED'}
    bl_parent_id = "TOONHAIR_PT_main"

    @classmethod
    def poll(cls, context):
        return active_group(context) is not None

    def draw(self, context):
        st = active_group(context).toon_hair
        lay = self.layout
        lay.use_property_split = True
        lay.use_property_decorate = False
        lay.prop(st, "branches")
        col = lay.column()
        col.active = st.branches > 1 or st.branch_jitter > 0
        col.prop(st, "branch_jitter")
        col.prop(st, "branch_pos")
        col.prop(st, "branch_spread")
        col.prop(st, "branch_length_var")
        col.prop(st, "branch_pattern")
        col.prop(st, "seed")
        col.prop(st, "notch_soft")


class TOONHAIR_PT_connect(_Panel, bpy.types.Panel):
    bl_label = "横連結 (隣の房とつなぐ)"
    bl_options = {'DEFAULT_CLOSED'}
    bl_parent_id = "TOONHAIR_PT_main"

    @classmethod
    def poll(cls, context):
        return active_group(context) is not None

    def draw_header(self, context):
        self.layout.prop(active_group(context).toon_hair, "connect", text="")

    def draw(self, context):
        st = active_group(context).toon_hair
        lay = self.layout
        lay.use_property_split = True
        lay.use_property_decorate = False
        box = lay.box()
        box.label(text=f"手動でつなぐ ({len(st.links)} か所, 自動より優先)", icon='LINKED')
        box.label(text="編集モードで各カーブの点を選ぶ: 1点=根元から / 2点=その間", icon='BLANK1')
        row = box.row(align=True)
        row.operator("toon_hair.link_selected", icon='LINKED')
        row.operator("toon_hair.unlink", text="解除", icon='UNLINKED').all = False
        row.operator("toon_hair.unlink", text="全部", icon='X').all = True
        col = lay.column()
        col.active = st.connect and len(st.links) == 0
        lay = col
        lay.prop(st, "connect_style", expand=True)
        if st.connect_style == 'SMOOTH':
            lay.prop(st, "smooth_iterations")
        lay.prop(st, "order")
        lay.prop(st, "connect_ratio")
        lay.prop(st, "split")
        lay.prop(st, "notch_soft")
        if st.connect_style == 'GROOVE':
            sub = lay.column()
            sub.enabled = st.backface == 'CLOSED'
            sub.prop(st, "seam_thickness")


class TOONHAIR_PT_shading(_Panel, bpy.types.Panel):
    bl_label = "法線 / マテリアル / UV"
    bl_options = {'DEFAULT_CLOSED'}
    bl_parent_id = "TOONHAIR_PT_main"

    @classmethod
    def poll(cls, context):
        return active_group(context) is not None

    def draw(self, context):
        st = active_group(context).toon_hair
        lay = self.layout
        lay.use_property_split = True
        lay.use_property_decorate = False
        lay.prop(st, "normal_mode")
        if st.normal_mode != 'NONE':
            lay.prop(st, "normal_blend")
        if st.normal_mode == 'PROXY':
            lay.prop(st, "normal_proxy")
        lay.separator()
        lay.prop(st, "material")
        lay.prop(st, "uv_seams")

class TOONHAIR_PT_outline(_Panel, bpy.types.Panel):
    bl_label = "アウトライン"
    bl_options = {'DEFAULT_CLOSED'}
    bl_parent_id = "TOONHAIR_PT_main"

    @classmethod
    def poll(cls, context):
        return active_group(context) is not None

    def draw_header(self, context):
        self.layout.prop(active_group(context).toon_hair, "outline", text="")

    def draw(self, context):
        st = active_group(context).toon_hair
        lay = self.layout
        lay.use_property_split = True
        lay.use_property_decorate = False
        lay.active = st.outline
        lay.prop(st, "outline_width")
        lay.prop(st, "outline_tip_ratio")
        lay.prop(st, "outline_taper_start")
        lay.prop(st, "outline_material")



# ==========================================================================
# Live update
# ==========================================================================

@persistent
def _on_depsgraph(scene, depsgraph):
    if build._building or _suspend_update:
        return
    candidates = set()
    centers = set()
    for u in depsgraph.updates:
        idb = u.id
        if isinstance(idb, bpy.types.Object):
            ob = idb.original
            if build.is_group(ob):
                if (u.is_updated_geometry or u.is_updated_transform) and ob.toon_hair.auto_update:
                    candidates.add(ob.name)
            elif u.is_updated_transform:
                centers.add(ob.name)
    if centers:
        for ob in bpy.data.objects:
            if (build.is_group(ob) and ob.toon_hair.auto_update and ob.toon_hair.center is not None
                    and ob.toon_hair.center.name in centers):
                candidates.add(ob.name)
    for name in candidates:
        ob = bpy.data.objects.get(name)
        # Blender also reports objects as updated on unrelated changes (e.g.
        # visibility): only rebuild when the curves or the head really moved
        if ob is not None and build.needs_rebuild(ob):
            try:
                build.rebuild(ob)
            except Exception as ex:  # never break the depsgraph loop
                print("[ToonHair] rebuild failed:", ex)


# ==========================================================================
# Registration
# ==========================================================================

classes = (
    ToonHairLink,
    ToonHairSettings,
    TOONHAIR_OT_create_center,
    TOONHAIR_OT_add_group,
    TOONHAIR_OT_convert_curve,
    TOONHAIR_OT_add_strand,
    TOONHAIR_OT_sample,
    TOONHAIR_OT_link_selected,
    TOONHAIR_OT_unlink,
    TOONHAIR_OT_rebuild,
    TOONHAIR_OT_bake,
    TOONHAIR_PT_main,
    TOONHAIR_PT_shape,
    TOONHAIR_PT_volume,
    TOONHAIR_PT_branch,
    TOONHAIR_PT_connect,
    TOONHAIR_PT_shading,
    TOONHAIR_PT_outline,
)


def register():
    from . import draw
    for c in classes + draw.classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.toon_hair_head = PointerProperty(
        name="頭", type=bpy.types.Object, poll=lambda self, ob: ob.type == 'MESH')
    bpy.types.Object.toon_hair = PointerProperty(type=ToonHairSettings)
    bpy.types.Object.toon_hair_owner = PointerProperty(type=bpy.types.Object)
    if _on_depsgraph not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)


def unregister():
    if _on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph)
    from . import draw
    del bpy.types.Scene.toon_hair_head
    del bpy.types.Object.toon_hair_owner
    del bpy.types.Object.toon_hair
    for c in reversed(classes + draw.classes):
        bpy.utils.unregister_class(c)
