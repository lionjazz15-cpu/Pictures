# SPDX-License-Identifier: GPL-3.0-or-later
"""One-click hair part presets. Sizes are relative to the head radius R."""

PRESETS = {
    # key: (label, object name, icon, settings(R))
    'BANGS': ("前髪", "Hair_Bangs", 'GP_SELECT_STROKES', lambda R: dict(
        width=R * 0.5, root_ratio=0.35, bulge_pos=0.55, tip_shape=2.0,
        connect=True, split=0.65, connect_ratio=2.2,
        branches=2, branch_jitter=1, branch_pos=0.75, branch_spread=0.2,
        branch_length_var=0.4, branch_pattern='RANDOM')),
    'PATTSUN': ("ぱっつん前髪", "Hair_BluntBangs", 'ALIGN_JUSTIFY', lambda R: dict(
        width=R * 0.45, root_ratio=0.35, bulge_pos=0.45, tip_mode='CUT', cut_taper=0.04,
        cut_enable=True, cut_height=0.25, connect=True, split=1.0, connect_ratio=2.4,
        connect_style='SMOOTH', curvature=0.08)),
    'SIDE': ("横髪", "Hair_Side", 'MOD_CURVE', lambda R: dict(
        width=R * 0.55, root_ratio=0.35, bulge_pos=0.5, tip_shape=1.8, mirror_x=True,
        connect=True, split=0.55, connect_ratio=2.2,
        branches=3, branch_pos=0.65, branch_spread=0.3, branch_length_var=0.5,
        branch_pattern='CENTER')),
    'BACK': ("後ろ髪", "Hair_Back", 'FORCE_CURVE', lambda R: dict(
        width=R * 0.7, root_ratio=0.25, bulge_pos=0.5, tip_shape=1.8, thickness=0.3,
        connect=True, split=0.7, connect_ratio=2.2,
        branches=2, branch_jitter=1, branch_pos=0.8, branch_spread=0.12,
        branch_length_var=0.45, branch_pattern='RANDOM')),
    'BACK_VOLUME': ("後ろ髪(立体)", "Hair_BackVolume", 'MOD_SOLIDIFY', lambda R: dict(
        width=R * 0.7, root_ratio=0.25, bulge_pos=0.5, tip_shape=1.8, thickness=0.35,
        connect=True, split=0.72, connect_ratio=2.2,
        branches=2, branch_jitter=1, branch_pos=0.8, branch_spread=0.15,
        branch_length_var=0.45, branch_pattern='RANDOM',
        layers='BOTH', layer_gap=0.09, layer_shift=0.5, layer_front_len=0.78, layer_back_len=0.92,
        layer_width=0.8, layer_flip=0.14, puff=0.12, puff_pos=0.62, tip_flip=-0.06)),
    'SIDEBURN': ("もみあげ", "Hair_Sideburn", 'IPO_EASE_IN', lambda R: dict(
        width=R * 0.3, root_ratio=0.6, bulge_pos=0.4, tip_shape=1.5, mirror_x=True,
        branches=2, branch_pos=0.6, branch_spread=0.25, branch_length_var=0.3)),
    'AHOGE': ("アホ毛", "Hair_Ahoge", 'IPO_ELASTIC', lambda R: dict(
        width=R * 0.15, root_ratio=0.5, bulge_pos=0.2, tip_shape=1.0, profile='RIDGE')),
    'TAIL': ("おさげ/ポニテ", "Hair_Tail", 'CON_SPLINEIK', lambda R: dict(
        width=R * 0.6, root_ratio=0.6, bulge_pos=0.35, tip_shape=1.6, profile='ROUND',
        thickness=0.6, connect=True, split=0.8, connect_ratio=2.5,
        branches=3, branch_pos=0.7, branch_spread=0.15, branch_length_var=0.3)),
    'DRILL': ("縦ロール", "Hair_Drill", 'MOD_SCREW', lambda R: dict(
        width=R * 0.35, root_ratio=0.6, bulge_pos=0.4, tip_shape=1.4, profile='ROUND',
        thickness=0.7, mirror_x=True, lod='CUSTOM', length_segments=28, profile_segments=4,
        curl_turns=4.0, curl_radius=0.6, curl_start=0.25)),
    'CUSTOM': ("汎用", "Hair_Group", 'CURVE_BEZCURVE', lambda R: dict(width=R * 0.35)),
}

ORDER = ('BANGS', 'PATTSUN', 'SIDE', 'BACK', 'BACK_VOLUME', 'SIDEBURN', 'AHOGE', 'TAIL', 'DRILL', 'CUSTOM')

ITEMS = [(k, PRESETS[k][0], "", PRESETS[k][2], i) for i, k in enumerate(ORDER)]


def common(R):
    return dict(outline_width=R * 0.012, flip_offset=R * 0.003)


def settings(key, R):
    s = common(R)
    s.update(PRESETS[key][3](R))
    return s
