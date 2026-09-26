# SPDX-License-Identifier: GPL-3.0-or-later
"""Japanese UI translation (shown when Blender's interface language is Japanese)."""

_ja = {
    # panels
    "Precision Shrinkwrap": "高精度シュリンクラップ",
    "Wrap Garment": "衣装をフィット",
    "Transfer Body Topology": "素体トポロジー転写",
    # properties
    "Target": "ターゲット（素体）",
    "Body mesh to wrap onto (evaluated with its modifiers, in rest pose)":
        "フィット先の素体メッシュ（モディファイア適用後・レストポーズで評価）",
    "Offset": "オフセット",
    "Distance kept from the target surface (fabric thickness / gap)":
        "素体表面から保つ距離（布の厚み・すき間）",
    "Offset Group": "オフセット用グループ",
    "Optional vertex group scaling the offset per vertex (weight 1 = full offset)":
        "頂点ごとにオフセット量を変える頂点グループ（ウェイト1 = 指定オフセット）",
    "Influence Group": "影響グループ",
    "Optional vertex group limiting the effect; weight 0 vertices are pinned":
        "効果を制限する頂点グループ。ウェイト0の頂点は固定",
    "Iterations": "反復回数",
    "Project / relax rounds. More = cleaner valleys, slower":
        "投影と整列の反復回数。多いほど谷がきれい・遅くなる",
    "Relax": "リラックス",
    "Tangential relaxation strength that keeps vertices from piling up in creases":
        "谷に頂点が溜まらないよう面に沿って整列させる強さ",
    "Distribution": "頂点分布",
    "Preserve": "元の流れを維持",
    "Keep the original edge flow / spacing of the mesh": "元メッシュのエッジフロー・間隔を保つ",
    "Even": "均等",
    "Redistribute vertices evenly over the surface": "頂点を表面上に均等に再配置",
    "Valley Tension": "谷の張り",
    "Let the fabric bridge valleys (buttock crease, cleavage) like tight cloth. "
    "0 = follow the surface everywhere":
        "おしりの割れ目や谷間を、張った布のように橋渡しさせる。0 = 全域で表面に沿う",
    "Anneal Offset": "段階的オフセット",
    "Wrap onto a large offset first and shrink it step by step, "
    "so vertices go down into valleys on the correct side":
        "大きいオフセットから段階的に縮め、頂点が正しい側から谷に入るようにする",
    "Anneal Start": "開始オフセット",
    "Offset the annealing starts from (0 = automatic)": "段階的オフセットの開始値（0 = 自動）",
    "Subdivision Aware": "サブディビジョン対応",
    "If the mesh has a Subdivision modifier, fit the subdivided result "
    "and solve for the cage that produces it":
        "サブディビジョンモディファイアがある場合、分割後の形状をフィットさせ、それを生むケージを逆算する",
    "Cage Fit Iterations": "ケージ逆算の反復",
    "Iterations used to solve the cage of a subdivided mesh": "サブディビ用ケージを逆算する反復回数",
    "Output": "出力",
    "Shape Key": "シェイプキー",
    "Store the result as a new shape key": "結果を新しいシェイプキーとして保存",
    "Apply": "直接適用",
    "Move the mesh vertices directly": "メッシュの頂点を直接移動",
    "Mode": "モード",
    "Keep Subdivision": "サブディビ維持",
    "Copy the cage faces and the modifier stack; the cage is solved so the "
    "subdivided result sits at the offset":
        "ケージ面とモディファイアをコピー。分割後がオフセット位置に来るようケージを逆算",
    "Applied (Exact)": "適用後に完全一致",
    "Copy the subdivided mesh itself: vertices coincide exactly with the "
    "subdivided body (offset 0) or sit exactly on the offset surface":
        "分割後のメッシュそのものをコピー。オフセット0なら分割後の素体と頂点が完全一致",
    "Region": "範囲",
    "Selected Faces": "選択面",
    "Faces selected in Edit Mode": "編集モードで選択した面",
    "Vertex Group": "頂点グループ",
    "Faces whose vertices are all in the group (>= 0.5)": "全頂点がグループに含まれる面（0.5以上）",
    "Near Object": "近くのオブジェクト",
    "Faces close to another object (e.g. an existing garment)": "別オブジェクト（既存の衣装など）に近い面",
    "All": "すべて",
    "The whole mesh": "メッシュ全体",
    "Region Group": "範囲グループ",
    "Distance": "距離",
    "Maximum distance to the object for a face to be included": "面を含める最大距離",
    "Keep Shape Keys": "シェイプキーを保持",
    "Keep the body's shape keys on the new mesh (Keep Subdivision mode)":
        "素体のシェイプキーを新メッシュに残す（サブディビ維持モード）",
    "Copy Armature": "アーマチュアをコピー",
    "Add the body's Armature modifiers to the new mesh (Applied mode)":
        "素体のアーマチュアモディファイアを新メッシュに追加（適用モード）",
    # operators
    "Transfer Topology": "トポロジー転写",
    "Check Offset": "オフセット確認",
}

translations_dict = {
    "ja_JP": {**{("*", k): v for k, v in _ja.items()},
              **{("Operator", k): v for k, v in _ja.items()}},
}
