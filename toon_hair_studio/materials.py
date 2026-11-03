# SPDX-License-Identifier: GPL-3.0-or-later
"""EEVEE toon materials for hair and the inverted-hull outline."""

import bpy

HAIR_MAT = "TH_Hair_Toon"
OUTLINE_MAT = "TH_Hair_Outline"

# node names exposed in the UI
N_LIT = "TH_LitColor"
N_SHADE = "TH_ShadeColor"
N_INNER = "TH_InnerColor"
N_RING = "TH_RingColor"
N_THRESH = "TH_Threshold"
N_SOFT = "TH_Softness"
N_INNER_STR = "TH_InnerStrength"
N_RING_STR = "TH_RingStrength"
N_RING_H = "TH_RingHeight"
N_RING_W = "TH_RingWidth"
N_RING_ZIG = "TH_RingZigzag"
N_RIM_STR = "TH_RimStrength"
N_OUTLINE = "TH_OutlineColor"


def _sock(sockets, *names):
    for n in names:
        for s in sockets:
            if s.identifier == n:
                return s
    for n in names:
        for s in sockets:
            if s.name == n and s.enabled:
                return s
    for n in names:
        for s in sockets:
            if s.name == n:
                return s
    raise KeyError(names)


class _G:
    def __init__(self, mat):
        self.nt = mat.node_tree
        self.nt.nodes.clear()
        self.x = 0

    def node(self, kind, loc, name=None, label=None):
        n = self.nt.nodes.new(kind)
        n.location = loc
        if name:
            n.name = name
            n.label = label or name.replace("TH_", "")
        return n

    def link(self, out_sock, in_sock):
        self.nt.links.new(out_sock, in_sock)

    def value(self, name, v, loc):
        n = self.node('ShaderNodeValue', loc, name)
        n.outputs[0].default_value = v
        return n.outputs[0]

    def rgb(self, name, col, loc):
        n = self.node('ShaderNodeRGB', loc, name)
        n.outputs[0].default_value = (*col, 1.0)
        return n.outputs[0]

    def math(self, op, a, b=None, loc=(0, 0), clamp=False):
        n = self.node('ShaderNodeMath', loc)
        n.operation = op
        n.use_clamp = clamp
        for idx, v in enumerate((a, b)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                n.inputs[idx].default_value = v
            else:
                self.link(v, n.inputs[idx])
        return n.outputs[0]

    def mix(self, fac, a, b, loc, blend='MIX'):
        n = self.node('ShaderNodeMix', loc)
        n.data_type = 'RGBA'
        n.blend_type = blend
        f = _sock(n.inputs, 'Factor_Float', 'Factor', 'Fac')
        if isinstance(fac, (int, float)):
            f.default_value = fac
        else:
            self.link(fac, f)
        self.link(a, _sock(n.inputs, 'A_Color'))
        self.link(b, _sock(n.inputs, 'B_Color'))
        return _sock(n.outputs, 'Result_Color')

    def map_range(self, v, fmin, fmax, tmin, tmax, loc, smooth=True):
        n = self.node('ShaderNodeMapRange', loc)
        n.data_type = 'FLOAT'
        n.interpolation_type = 'SMOOTHSTEP' if smooth else 'LINEAR'
        n.clamp = True
        ins = [s for s in n.inputs if s.type == 'VALUE']
        # Value, From Min, From Max, To Min, To Max (float variants come first)
        for sock, val in zip(ins[:5], (v, fmin, fmax, tmin, tmax)):
            if isinstance(val, (int, float)):
                sock.default_value = val
            else:
                self.link(val, sock)
        return _sock(n.outputs, 'Result')


def _set_common(mat):
    if hasattr(mat, "surface_render_method"):
        try:
            mat.surface_render_method = 'DITHERED'
        except TypeError:
            pass
    mat.use_backface_culling = True
    if hasattr(mat, "use_backface_culling_shadow"):
        mat.use_backface_culling_shadow = False


def build_hair_material(mat, lit=(0.62, 0.42, 0.33), shade=(0.36, 0.20, 0.18),
                        inner=(0.25, 0.13, 0.12), ring=(1.0, 0.92, 0.85)):
    if not mat.use_nodes and hasattr(mat, "use_nodes"):
        mat.use_nodes = True
    g = _G(mat)
    X = -1400

    # --- light mask: Diffuse -> Shader to RGB -> luminance -> step ---------
    diff = g.node('ShaderNodeBsdfDiffuse', (X, 300))
    _sock(diff.inputs, 'Color').default_value = (1, 1, 1, 1)
    s2r = g.node('ShaderNodeShaderToRGB', (X + 200, 300))
    g.link(diff.outputs[0], s2r.inputs[0])
    bw = g.node('ShaderNodeRGBToBW', (X + 400, 300))
    g.link(_sock(s2r.outputs, 'Color'), bw.inputs[0])
    thr = g.value(N_THRESH, 0.35, (X + 200, 120))
    soft = g.value(N_SOFT, 0.03, (X + 200, 30))
    lo = g.math('SUBTRACT', thr, soft, (X + 400, 120))
    hi = g.math('ADD', thr, soft, (X + 400, 30))
    lit_mask = g.map_range(bw.outputs[0], lo, hi, 0.0, 1.0, (X + 600, 250))

    c_lit = g.rgb(N_LIT, lit, (X + 600, 520))
    c_shade = g.rgb(N_SHADE, shade, (X + 600, 700))
    base = g.mix(lit_mask, c_shade, c_lit, (X + 850, 450))

    # --- inner / back side darkening ---------------------------------------
    geo = g.node('ShaderNodeNewGeometry', (X + 400, -150))
    attr_in = g.node('ShaderNodeAttribute', (X + 400, -350))
    attr_in.attribute_name = "TH_inner"
    inner_raw = g.math('MAXIMUM', _sock(geo.outputs, 'Backfacing'),
                       _sock(attr_in.outputs, 'Fac', 'Factor'), (X + 650, -200))
    inner_str = g.value(N_INNER_STR, 0.85, (X + 650, -350))
    inner_fac = g.math('MULTIPLY', inner_raw, inner_str, (X + 850, -250), clamp=True)
    c_inner = g.rgb(N_INNER, inner, (X + 850, -450))
    col = g.mix(inner_fac, base, c_inner, (X + 1100, 300))

    # --- angel ring (天使の輪) on head-space height with zigzag --------------
    attr_h = g.node('ShaderNodeAttribute', (X, -650))
    attr_h.attribute_name = "TH_head"
    sep = g.node('ShaderNodeSeparateXYZ', (X + 200, -650))
    g.link(_sock(attr_h.outputs, 'Vector'), sep.inputs[0])
    zig_amt = g.value(N_RING_ZIG, 0.04, (X + 200, -850))
    az = g.math('MULTIPLY', _sock(sep.outputs, 'X'), 6.2831853 * 28.0, (X + 400, -650))
    sinv = g.math('SINE', az, None, (X + 600, -650))
    tri = g.math('ABSOLUTE', sinv, None, (X + 800, -650))
    zig = g.math('MULTIPLY', tri, zig_amt, (X + 1000, -650))
    ring_h = g.value(N_RING_H, 0.55, (X + 400, -900))
    hz = g.math('ADD', ring_h, zig, (X + 1200, -800))
    d = g.math('SUBTRACT', _sock(sep.outputs, 'Y'), hz, (X + 1400, -700))
    d = g.math('ABSOLUTE', d, None, (X + 1600, -700))
    ring_w = g.value(N_RING_W, 0.05, (X + 1400, -900))
    band = g.map_range(d, 0.0, ring_w, 1.0, 0.0, (X + 1800, -700))
    ring_str = g.value(N_RING_STR, 0.7, (X + 1800, -950))
    band = g.math('MULTIPLY', band, ring_str, (X + 2000, -700))
    band = g.math('MULTIPLY', band, lit_mask, (X + 2200, -700))
    not_inner = g.math('SUBTRACT', 1.0, inner_fac, (X + 2200, -900), clamp=True)
    band = g.math('MULTIPLY', band, not_inner, (X + 2400, -700), clamp=True)
    c_ring = g.rgb(N_RING, ring, (X + 2200, -450))
    col = g.mix(band, col, c_ring, (X + 2600, 200))

    # --- soft rim light ------------------------------------------------------
    lw = g.node('ShaderNodeLayerWeight', (X + 2200, 600))
    _sock(lw.inputs, 'Blend').default_value = 0.5
    rim = g.map_range(_sock(lw.outputs, 'Facing'), 0.75, 0.95, 0.0, 1.0, (X + 2400, 600))
    rim_str = g.value(N_RIM_STR, 0.2, (X + 2400, 800))
    rim = g.math('MULTIPLY', rim, rim_str, (X + 2600, 650))
    rim = g.math('MULTIPLY', rim, lit_mask, (X + 2800, 650))
    col = g.mix(rim, col, c_lit, (X + 3000, 300), blend='SCREEN')

    emi = g.node('ShaderNodeEmission', (X + 3200, 300))
    g.link(col, _sock(emi.inputs, 'Color'))
    out = g.node('ShaderNodeOutputMaterial', (X + 3400, 300))
    g.link(emi.outputs[0], out.inputs[0])

    _set_common(mat)
    mat.diffuse_color = (*lit, 1.0)
    return mat


def build_outline_material(mat, color=(0.13, 0.06, 0.05)):
    if not mat.use_nodes and hasattr(mat, "use_nodes"):
        mat.use_nodes = True
    g = _G(mat)
    c = g.rgb(N_OUTLINE, color, (-400, 0))
    emi = g.node('ShaderNodeEmission', (-200, 0))
    g.link(c, _sock(emi.inputs, 'Color'))
    out = g.node('ShaderNodeOutputMaterial', (0, 0))
    g.link(emi.outputs[0], out.inputs[0])
    _set_common(mat)
    mat.diffuse_color = (*color, 1.0)
    return mat


def ensure_hair_material(name=HAIR_MAT):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        build_hair_material(mat)
    return mat


def ensure_outline_material(name=OUTLINE_MAT):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        build_outline_material(mat)
    return mat


def is_toon_material(mat):
    return mat is not None and mat.node_tree is not None and N_LIT in mat.node_tree.nodes


def draw_material_controls(layout, mat):
    if not is_toon_material(mat):
        return
    nodes = mat.node_tree.nodes
    col = layout.column(align=True)

    def row(name, text):
        n = nodes.get(name)
        if n is not None:
            col.prop(n.outputs[0], "default_value", text=text)

    row(N_LIT, "明部 Lit")
    row(N_SHADE, "影 Shade")
    row(N_INNER, "内側 Inner")
    row(N_INNER_STR, "内側の強さ")
    col.separator()
    row(N_THRESH, "影の境界 Threshold")
    row(N_SOFT, "境界ぼかし Softness")
    col.separator()
    row(N_RING, "天使の輪 Color")
    row(N_RING_STR, "天使の輪 強さ")
    row(N_RING_H, "天使の輪 高さ")
    row(N_RING_W, "天使の輪 幅")
    row(N_RING_ZIG, "ギザギザ量")
    row(N_RIM_STR, "リムライト")


def draw_outline_controls(layout, mat):
    if mat is None or mat.node_tree is None:
        return
    n = mat.node_tree.nodes.get(N_OUTLINE)
    if n is not None:
        layout.prop(n.outputs[0], "default_value", text="線の色 Color")
