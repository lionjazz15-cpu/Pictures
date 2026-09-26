# PencilLine — 引き継ぎメモ (Claude Code 用)

Unity で Pencil+ 風の「カリッカリのアニメルック」をリアルタイムに作るツール。映像制作用 (Unity Recorder → After Effects)。
ユーザーは日本語話者。UI 文字列・コメント・返答は日本語で。

## 動作環境 (厳守)
- Unity **2022.3.22f1** (VRChat Creator Companion と同じ) / **Built-in Render Pipeline** / Windows DX11 / Linear カラースペース
- 対象アバター: lilToon / MToon (VRM 0.x, 1.0) / Poiyomi / Standard
- **クラウド環境では Unity を実行できない**。コンパイル確認はユーザーがローカルで行う。
  - 2022.3 に存在する API だけを使う (例: `FindObjectsByType` は可、`Texture2D.Reinitialize` は可)
  - C# は tree-sitter 等で最低限の構文チェックをしてから push する
  - シェーダーは HLSL (CGPROGRAM)、`#pragma target 4.5`
- `.meta` ファイルは消さない・作り直さない (GUID が変わるとユーザーのシーンで Missing Script になる)

## 構成
```
Runtime/PencilLineEffect.cs     カメラに付けるライン本体 (OnRenderImage, [ImageEffectAllowedInSceneView])
Runtime/MaterialAdapter.cs      lilToon / MToon0 / MToon10 / 汎用 の差を吸収して読む
Editor/AvatarConverterWindow.cs アバターを D&D → 全マテリアルを一括でフラット化 or アウトラインだけオフ (複製/バリアントを作る)
Editor/OutlineUtil.cs           シェーダー側アウトラインのオフ
Editor/PaletteBuilder.cs        パレット抽出の本体 (k-means++ in OKLab, 書き影の自動統合, MToon 影色)
Editor/FlatPaletteWindow.cs     1マテリアルのパレット調整 UI
Editor/LilToonBaker.cs          lilToon の色調補正 + メイン2nd/3rd (デカール) を PNG に焼き込み
Shaders/PencilLineGBuffer.shader  MRT: g0=(八面体法線xy, 線形深度, ID=obj*1024+mat) g1=(アルベド, パレット番号+1 (+64で線フラグ))
Shaders/PencilLineEdge.shader     Pass0 検出 / Pass1 円形ダイレート(前後関係チェック) / Pass2 乗算済みでブレンド / Pass3 線だけ
Shaders/FlatPalette.shader        パレットに丸めるフラットシェーダー (4テクセルを個別にスナップして補間)
Shaders/LilToonBake.shader        焼き込み用
```

### 設計上の注意
- G バッファは自前の VP 行列 (`GL.GetGPUProjectionMatrix(proj, true)`) で描く。カリングは Cull Off + ジオメトリ法線との比較で自前判定 (VFACE は使わない)。
- 全パスで座標は `int2(uv * size)` から `Load` する (SV_Position は使わない。D3D のフリップ対策)。
- 合成は「src をそのまま Blit → 線を Blend One OneMinusSrcAlpha で重ねる」。src を直接サンプルしない (以前これで画面が灰色になった)。
- 線の種類と優先度: 外周7 > 内側輪郭6 > 交差5 > 材質境界4 > 折れ目3 > シワ(谷線)2 > テクスチャ線1。マテリアルごとのビットマスクで個別オフ。
- 線の太さは `referenceHeight` (1080) 基準の px。内部はスーパーサンプリング倍率を掛けた解像度。
- パレットテクスチャは 32×4 (RGBAFloat, linear): row0 元色OKLab / row1 ベース色 / row2 1影色 / row3 線フラグ。

## ユーザーが気に入った線の設定 (次のバージョンの既定値にする)
Supersampling 3, Editor Preview Supersampling 1, Reference Height 1080
Outline 3.91 / Inner 3.64 / Intersection 3.59 / Material 3.59 / Crease 3.71 / Wrinkle 3.76 / Texture 0
Line Color 黒, Color Trace 0, Color Trace Darkness 0.45, Saturation 1.3
Depth Threshold 0.0322, Depth Slope Bias 1.78, Crease Angle 85.5
Use Normal Maps on, Wrinkle Scale 1.33, Wrinkle Threshold 0.168, Texture Edge Threshold 0
Emphasis 0.178, Min Strength 0, Strong Depth Ratio 0.01
Distance Reduction on (near 2, far 20, min 0.473), Lit Side 0.772, Shadow Side 0.713

一括変換で使っていた設定: 色の数 32, 色相の許容差 1, 明度差の上限 0.5 / 0.5 (自動統合をほぼ切っている)

## 次にやること
1. **厚塗り・なめらかなグラデーションのモデルをフラット化するとジャギジャギになる問題** (最優先)
   - 原因: 滑らかなグラデーションを k-means で丸めると、色の境目がノイズ混じりの等高線になり、テクセル単位でガタガタする。
   - 方針案 (組み合わせる):
     a. 丸める前にエッジ保存の平滑化 (バイラテラル / 桑原フィルタ) で塗りのムラを消す
     b. パレット番号マップを作って最頻値フィルタ + 小さい島の除去で整理し、2倍解像度で焼き込んだ「フラット済みテクスチャ」を出力 (シェーダーは普通にサンプルするだけ → 境界もなめらか)
     c. 「ソフトフラット」モード: 明度だけ段階化して色相・彩度はなめらかに残す、または近い2色を fwidth でなめらかに補間
   - ユーザーに「どれくらいフラットにしたいか」を1つのスライダーで選べる形が理想。
2. 上記の線設定を `PencilLineEffect` の既定値に反映。
3. ユーザー未確認: シーンビュー表示、灰色の修正、Avatar Converter の結果、シワ線・テクスチャ線の出方。報告を受けたら直す。
4. ユーザーが参考にしたいルックの X 投稿があるが、こちらからは閲覧できない。画像をもらって方向性を合わせる。
