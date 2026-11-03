# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender glue: hair group (curve object) -> generated mesh objects."""

import bpy
from mathutils import Matrix

from . import core, materials

HAIR_SUFFIX = "_Hair"
OUTLINE_SUFFIX = "_Outline"
PROXY_MOD = "TH_NormalProxy"
VG_ROOT_TIP = "TH_root_tip"
VG_PIN = "TH_pin"

PARAM_KEYS = (
    "length_segments", "profile_segments", "tip_bias", "profile", "backface",
    "width", "thickness", "curvature", "root_ratio", "bulge_pos", "tip_shape",
    "tip_mode", "tip_min", "root_cap", "twist", "root_extend", "flip_offset",
    "normal_mode", "normal_blend", "outline", "outline_width",
    "outline_tip_ratio", "outline_taper_start",
    "connect", "connect_ratio", "split", "notch_soft", "seam_thickness", "order",
)

_building = False


def is_group(ob):
    return ob is not None and ob.type == 'CURVE' and ob.toon_hair.enabled


def owner_group(ob):
    """The hair group a (curve or generated) object belongs to."""
    if ob is None:
        return None
    if is_group(ob):
        return ob
    owner = getattr(ob, "toon_hair_owner", None)
    if owner is not None and is_group(owner):
        return owner
    return None


def params_from(settings):
    return core.StrandParams(**{k: getattr(settings, k) for k in PARAM_KEYS})


def center_of(settings):
    c = settings.center
    return core.Center(c.matrix_world if c is not None else None)


def generate(curve_ob, world=True, with_outline=True):
    """Return (hair Geo, outline Geo or None) for a hair group."""
    st = curve_ob.toon_hair
    prm = params_from(st)
    center = center_of(st)
    mw = curve_ob.matrix_world
    samples = [s for s in (core.sample_spline(sp, mw) for sp in curve_ob.data.splines) if len(s) >= 2]
    hair, outline = core.build_group(samples, center, prm, mirror=st.mirror_x,
                                     with_outline=with_outline)
    if not world:
        inv = mw.inverted_safe()
        hair.transform(inv)
        if outline is not None:
            outline.transform(inv)
    return hair, outline


# --------------------------------------------------------------------------
# Mesh writing
# --------------------------------------------------------------------------

def _float_attr(me, name, values, domain='POINT'):
    a = me.attributes.get(name)
    if a is not None and (a.data_type != 'FLOAT' or a.domain != domain):
        me.attributes.remove(a)
        a = None
    if a is None:
        a = me.attributes.new(name, 'FLOAT', domain)
    a.data.foreach_set("value", values)


def _vec_attr(me, name, values):
    a = me.attributes.get(name)
    if a is not None and (a.data_type != 'FLOAT_VECTOR' or a.domain != 'POINT'):
        me.attributes.remove(a)
        a = None
    if a is None:
        a = me.attributes.new(name, 'FLOAT_VECTOR', 'POINT')
    flat = [c for v in values for c in v]
    a.data.foreach_set("vector", flat)


def write_mesh(me, geo, custom_normals=True):
    me.clear_geometry()
    me.from_pydata([v[:] for v in geo.verts], [], geo.faces)

    if len(me.loops):
        uvl = me.uv_layers.get("UVMap") or me.uv_layers.new(name="UVMap")
        flat = [c for uvs in geo.face_uv for uv in uvs for c in uv]
        uvl.data.foreach_set("uv", flat)

    if geo.verts:
        _float_attr(me, "TH_t", geo.t)
        _float_attr(me, "TH_inner", geo.inner)
        _vec_attr(me, "TH_head", geo.head)

    if hasattr(me, "shade_smooth"):
        me.shade_smooth()
    else:  # pragma: no cover - < 4.1
        me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
    me.update()
    if custom_normals and geo.normals:
        me.normals_split_custom_set_from_vertices([n[:] for n in geo.normals])
    me.update()


def write_vertex_groups(ob, geo):
    for name, func in ((VG_ROOT_TIP, lambda t: t), (VG_PIN, lambda t: 1.0 - t)):
        vg = ob.vertex_groups.get(name) or ob.vertex_groups.new(name=name)
        buckets = {}
        for i, t in enumerate(geo.t):
            buckets.setdefault(round(func(t), 3), []).append(i)
        for w, idx in buckets.items():
            vg.add(idx, w, 'REPLACE')


def _new_child(name, parent, collection):
    me = bpy.data.meshes.new(name)
    ob = bpy.data.objects.new(name, me)
    collection.objects.link(ob)
    ob.parent = parent
    ob.matrix_parent_inverse = Matrix.Identity(4)
    ob.lock_location = ob.lock_rotation = ob.lock_scale = (True, True, True)
    ob.toon_hair_owner = parent
    return ob


def _collection_of(ob):
    if ob.users_collection:
        return ob.users_collection[0]
    return bpy.context.scene.collection


def _sync_culling(mat):
    """Double-sided if any group using the material is single-sheet."""
    if mat is None:
        return
    single = False
    for ob in bpy.data.objects:
        if is_group(ob) and ob.toon_hair.material == mat and ob.toon_hair.backface == 'SINGLE':
            single = True
            break
    if mat.use_backface_culling == single:
        mat.use_backface_culling = not single


def ensure_objects(curve_ob):
    st = curve_ob.toon_hair
    coll = _collection_of(curve_ob)
    if st.hair_object is None or st.hair_object.name not in bpy.data.objects:
        st.hair_object = _new_child(curve_ob.name + HAIR_SUFFIX, curve_ob, coll)
    if st.material is None:
        st.material = materials.ensure_hair_material()
    if st.outline_material is None:
        st.outline_material = materials.ensure_outline_material()
    if st.outline:
        if st.outline_object is None or st.outline_object.name not in bpy.data.objects:
            ob = _new_child(curve_ob.name + OUTLINE_SUFFIX, curve_ob, coll)
            ob.hide_select = True
            st.outline_object = ob
        st.outline_object.visible_shadow = False
        if hasattr(st.outline_object, "visible_diffuse"):
            st.outline_object.visible_diffuse = False
            st.outline_object.visible_glossy = False


def _assign_material(me, mat):
    if len(me.materials) == 0:
        me.materials.append(mat)
    elif me.materials[0] != mat:
        me.materials[0] = mat


def _sync_proxy_modifier(ob, st):
    mod = ob.modifiers.get(PROXY_MOD)
    if st.normal_mode == 'PROXY' and st.normal_proxy is not None:
        if mod is None:
            mod = ob.modifiers.new(PROXY_MOD, 'DATA_TRANSFER')
        mod.object = st.normal_proxy
        mod.use_object_transform = True
        mod.use_loop_data = True
        mod.data_types_loops = {'CUSTOM_NORMAL'}
        mod.loop_mapping = 'POLYINTERP_NEAREST'
        mod.mix_factor = st.normal_blend
    elif mod is not None:
        ob.modifiers.remove(mod)


def rebuild(curve_ob):
    """Regenerate the meshes of one hair group."""
    global _building
    if not is_group(curve_ob) or _building:
        return
    _building = True
    try:
        st = curve_ob.toon_hair
        ensure_objects(curve_ob)
        hair, outline = generate(curve_ob, world=False)

        hob = st.hair_object
        write_mesh(hob.data, hair)
        write_vertex_groups(hob, hair)
        _assign_material(hob.data, st.material)
        _sync_proxy_modifier(hob, st)
        _sync_culling(st.material)

        oob = st.outline_object
        if oob is not None:
            if outline is not None:
                write_mesh(oob.data, outline, custom_normals=False)
                _assign_material(oob.data, st.outline_material)
                oob.hide_viewport = False
                oob.hide_render = False
            else:
                oob.data.clear_geometry()
                oob.hide_viewport = True
                oob.hide_render = True
    finally:
        _building = False


def rebuild_all(only_center=None):
    for ob in bpy.data.objects:
        if is_group(ob) and (only_center is None or ob.toon_hair.center == only_center):
            rebuild(ob)


def bake(groups, name="Hair_Baked", with_outline=False, collection=None):
    """Merge groups into one standalone mesh (world space) for export."""
    hair = core.Geo()
    outline = core.Geo()
    mats = []
    face_mats = []
    for g in groups:
        h, o = generate(g, world=True, with_outline=with_outline)
        mat = g.toon_hair.material
        if mat not in mats:
            mats.append(mat)
        face_mats += [mats.index(mat)] * len(h.faces)
        hair.extend(h)
        if o is not None:
            outline.extend(o)

    collection = collection or bpy.context.scene.collection
    me = bpy.data.meshes.new(name)
    write_mesh(me, hair)
    for m in mats:
        me.materials.append(m)
    me.polygons.foreach_set("material_index", face_mats)
    me.update()
    ob = bpy.data.objects.new(name, me)
    collection.objects.link(ob)
    write_vertex_groups(ob, hair)

    oob = None
    if with_outline and outline.verts:
        ome = bpy.data.meshes.new(name + OUTLINE_SUFFIX)
        write_mesh(ome, outline, custom_normals=False)
        ome.materials.append(groups[0].toon_hair.outline_material)
        oob = bpy.data.objects.new(name + OUTLINE_SUFFIX, ome)
        collection.objects.link(oob)
        oob.visible_shadow = False
    return ob, oob
