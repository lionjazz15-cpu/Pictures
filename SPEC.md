# KiraKit 仕様書（仮称） v0.1

VRMキャラを「キラキラド派手・Live2D風ふわふわ」に見せるための後付けレイヤー。
モーション（mocaptyx dance の VRMA）とシェーダ（MToon / KIRITTOON）の間に挟まる。
**VRMファイルもVRMAも改変しない。** 設定はJSONプリセット1枚で使いまわす。

- 状態: 設計のみ。実装はまだ無い。
- 参考にした作品: すずか（@k_szk）さんのSkebループ動画。確認できたのは静止スクショ1枚のみで、動画は未視聴。動きの仕様は一般的なLive2Dループ作品の作りに基づく。

---

## 1. 前提

### 1.1 既存リポジトリで確認した事実

| 対象 | 事実 |
|---|---|
| Dance-Gen（`dance_motion_service`） | 音源 → `manifest.json` ＋ `body.bvh`。任意で `body.vrma` / `body.vmd` / `camera.vmd` / `body.fbx` |
| `body.vrma` の中身 | ヒューマノイド17ボーンの回転＋hipsの移動、30fps・LINEAR。**指・表情・カメラは入っていない** |
| `manifest.json` の中身 | `fps` `duration` `frames` `tempo_bpm` `root_motion_mode` `choreography[]`（区間の start/end/energy/move_family）。**拍は個数だけで、時刻は出力されていない** |
| 内部データ | `AudioFeatures` に `beat_times` `downbeat_times` `beat_strengths` `energy_curve` `onset_times` がある。`MotionFrame` に `camera_position/rotation` `mouth_open` `smile` がある |
| Mo-Cap | Unity＋UniVRM10。VRMAは `Vrm10Instance.Runtime.VrmAnimation` で再生。Timeline用PlayableAssetと `Sample(time)` がある |
| mo-kyapu-ai-motion | three 0.184＋three-vrm 3.5 のプレビュー（VRM＋VRMA、グリーンバックMP4／透過WebM書き出し） |

### 1.2 仮定（違っていたら直す）

- 「mocaptyx dance」＝ Dance-Gen の `dance_motion_service`。リポジトリ内にこの名前は出てこない。
- KIRITTOON は Unity のシェーダ。ソースは未確認なので、本書では「満たすべき契約」だけを定める。
- 主戦場は Unity（UniVRM10）。three-vrm は後追いで同じプリセットを読む。

---

## 2. 全体構成

```
音源 → mocaptyx dance → body.vrma ＋ manifest.json（＋ timeline 追加分）
                              │
VRM → ① ポーズ適用（VRMA／mocapライブ）          既存
      ② Motion層 : ふわふわ加算＋揺れもの          KiraKit・ボーンを動かす
      ③ Deform層 : パーツ別パース＋画面空間ワープ   KiraKit・頂点を動かす
      ④ シェーダ : MToon / KIRITTOON              既存＋フック1行
      ⑤ Look層   : 背景・粒子・屈折・白フチ・グロー  KiraKit
```

| 層 | 役割 | 触るもの |
|---|---|---|
| Clock | 全レイヤー共通の時計（拍単位） | なし |
| Motion | Live2D風の動き。物理演算の置き換え | ボーンのローカル回転 |
| Deform | 顔ペタン・手ダイナミック・ぷるぷる | 頂点位置（頂点シェーダ内） |
| Look | キラキラ映え | 背景・粒子・ポストエフェクト |
| Preset | 上記すべての設定 | JSON |

原則は3つ。

1. **シミュレーションしない。** 同じ入力なら、どのフレームから評価しても同じ結果になる。
2. **動きは画面の平面内。** 奥行き方向には揺らさない（シルエットを崩さない）。
3. **効果ゼロ＝素通し。** 全パラメータ初期値でKiraKit無しと完全に同じ絵になる。

---

## 3. Clock（時計）

時間の単位は**拍**。すべてのチャンネルの周期を拍で指定する。

| モード | 時計の出どころ | 用途 |
|---|---|---|
| `loop` | 固定 `bpm` ＋ `loop_beats`（例: 120bpm×8拍＝4秒） | Skeb風ループ動画 |
| `timeline` | mocaptyx dance の拍時刻（§8.2） | ダンス連携 |
| `live` | 固定bpmまたはタップ | mocapライブ配信 |

`loop` では、各チャンネルの周期が `loop_beats` を割り切ることを検証する。割り切れない設定はエラー。これでループが必ずつながる。

Clockが配る信号:

| 信号 | 内容 |
|---|---|
| `beat_phase` / `bar_phase` / `loop_phase` | 0→1 の位相（1小節＝4拍） |
| `beat_pulse` / `downbeat_pulse` | 拍の瞬間に立って減衰する包絡。拍の強さで高さが変わる |
| `energy` | 0〜1 の盛り上がり |
| `section` | 現在の区間（`choreography[]` 由来） |

---

## 4. Motion層

### 4.1 ふわふわ加算

VRMA適用後のヒューマノイドボーンに、小さな回転を加算する。

| チャンネル | 対象 | 既定周期 | 備考 |
|---|---|---|---|
| `breath` | chest / upperChest / shoulder | 8拍 | 胸の拡縮はDeform層の伸縮で表現 |
| `sway` | spine / chest の傾き | 8拍＋4拍 | 2成分を位相ずらしで重ねて8の字 |
| `head_follow` | neck / head | `sway` に遅れて追従 | |
| `float` | 全身の上下 | 4拍 | `loop` モード専用 |
| `blink` | 表情 `blink` | 指定時刻 | ダンス側が表情を持つ場合は瞬きだけ担当 |
| `eye_wobble` | 瞳ハイライト | 拍ごと | Deform層のぷるぷるで表現 |
| `beat_bounce` | 全身の伸縮 | 1拍 | `beat_pulse` 駆動 |

波形は `sine` / `eased`（タメとオーバーシュート付き・既定）/ `bounce` / `pulse`。

ダンス連携時の制限:

- **hips・脚・足には加算しない。**（接地が滑るため）
- `sway` は体の動きが大きいほど自動で弱める（ダンスと喧嘩させない）。
- `float` は無効。

### 4.2 揺れもの（追従フィルタ）

髪・耳・しっぽ・リボンを、VRM SpringBone の代わりに動かす。Live2Dの物理と同じ「親の動きに遅れてついてくる振り子」を、線形の2次フィルタで表す。

- **入力**: チェーン根元の、画面平面内での横移動と回転。
- **出力**: 各関節の振れ角。回転軸はカメラの視線方向（＝画面内で揺れる）。
- **連鎖**: 各関節の入力は親関節の出力。先端ほど遅れ、振幅が増える。
- **制限**: 最大角をなめらかにクリップ。重力・衝突は無い。

チェーン1本のパラメータは3つ。

| パラメータ | 意味 |
|---|---|
| `soft` | やわらかさ（固有周期。大きいほどゆっくり揺れる） |
| `lag` | 減衰と先端への遅れの増え方 |
| `amount` | 振れ幅 |

評価方法は3通りで、フィルタは同一。

| モード | 入力 | 方法 | 結果 |
|---|---|---|---|
| 周期 | ループ素材 | 周波数成分ごとに倍率と遅れを掛ける（厳密解） | 完全ループ・助走不要 |
| ベイク | 長尺VRMA | 固定刻みで先頭から積分（開始前2秒の助走） | 決定的。`loopable` 素材は周期扱い |
| ライブ | mocap | 固定刻みで逐次更新 | リアルタイム |

出力先: 実行時はボーンに直接書く。オフラインはUnityの AnimationClip にベイクできる。

### 4.3 VRM SpringBone との関係

- チェーン定義は VRM の SpringBone 設定から自動で読む（VRM1 `VRMC_springBone` / VRM0 `secondaryAnimation`）。
- 変換: `stiffness` → `soft`、`dragForce` → `lag`。`gravity` とコライダーは無視。
- KiraKit が担当するチェーンは SpringBone を止める。
- **スカートなど脚との衝突が要るチェーンは、既定で SpringBone のまま残す。** ボーン名（skirt 等）で判定し、プリセットで上書きできる。

---

## 5. Deform層

### 5.1 パーツ

| 既定パーツ | ピボット | メンバー |
|---|---|---|
| `head` | head | head とその子孫（髪・耳を含む） |
| `hand_L` / `hand_R` | leftHand / rightHand | hand と指 |
| `foot_L` / `foot_R` | leftFoot / rightFoot | foot と toes |
| 任意（最大計8） | 指定ボーン | 指定ボーン以下（しっぽ・小物など） |

頂点ごとのパーツウェイト＝メンバーボーンのスキンウェイト合計。前腕・首など隣接ボーンには `falloff` で勾配を付ける。継ぎ目は出ない。
ロード時に自動計算し、メッシュの空きUVチャンネル2本（既定: 2番と3番、float4×2）に書き込む。元のVRMは書き換えず、実行時のメッシュ複製に持たせる。

### 5.2 パーツのパラメータ

| パラメータ | 範囲 | 意味 |
|---|---|---|
| `persp` | −1 〜 +0.8 | −1＝完全フラット、0＝そのまま、+0.5＝3倍強いパース |
| `scale` | 既定 1 | 画面上の大きさ（ピボット中心） |
| `shift` | 既定 (0,0) | 画面上の位置ずらし |
| `depth_bias` | 既定 0 | 前後関係だけを動かす（見た目の位置は不変） |
| `falloff` | 0〜1 | なじませ幅 |
| `squash` / `bend` / `wobble` | 既定 0 | 画面空間ワープ（§5.4） |

### 5.3 パーツ別パースの式

ビュー空間で計算する（`z` はカメラからの奥行き）。頂点 `P`、ピボット `V`、`d = V.z`、`dz = P.z − d`。

```
j      = (1 + persp) / (1 − persp)      // パース倍率。0=平行投影, 1=そのまま
ray    = V.xy * (P.z / d)               // ピボットを通る視線上の点
o      = P.xy − ray                     // 視線からの横ずれ
r      = P.z / (d + j * dz)
P'.xy  = ray + warp(o) * r * scale + shift * P.z
P'.z   = P.z                            // 奥行きは変えない
P_out  = P + Σ w_i * (P'_i − P)         // パーツウェイトで合成
```

- ピボットの画面上の位置と大きさは変わらない。パーツに専用カメラを当ててドリーズームしたのと同じ絵になる。
- `d + j*dz` が正になるよう `j` を上限クランプする（仮想カメラがパーツに潜らないため）。
- **法線は変形しない。** 陰影はKiraKit無しと同じ。

### 5.4 画面空間ワープ

ピボット基準・パーツ半径で正規化した画面座標で、横ずれ `o` に掛ける。

| 種類 | 動き | 主な駆動元 |
|---|---|---|
| `squash` | 縦に伸びたら横が細る | `beat_bounce`、`breath`、追従フィルタの速度 |
| `bend` | ピボットから離れるほど横にしなる | 追従フィルタの出力 |
| `wobble` | パーツ内を流れるゆるい波（ゼリー感） | Clock位相 |

`head` を `persp ≈ −0.7` にして小さく回すと、顔パーツが奥行きに応じて横に滑る。これがLive2Dの「顔の角度XY」の見え方になる。

### 5.5 自動ドライブ

| パーツ | 自動ルール |
|---|---|
| `head` | 常時フラット。カメラに対して真横に近づくほど 0 に戻す |
| `hand_*` / `foot_*` | 胸よりカメラ側に出た距離（腕の長さで正規化）に応じて `persp` と `scale` を上げる。体の横では 0 |

- 値は0.1秒程度で平滑化する（ポッピング防止）。
- 最終値＝自動値×`mult`＋`offset`。`mult` と `offset` はキーフレーム可能（決めカットだけ手で盛る）。

### 5.6 シェーダ契約（Kiraフック）

共通インクルード1本と、頂点関数内の呼び出し1行。

```hlsl
#include "KiraDeform.hlsl"
// ビュー変換の後、射影の前に呼ぶ
positionVS = KiraDeformVS(positionVS, kiraWeightsA, kiraWeightsB);
```

| 項目 | 契約 |
|---|---|
| 呼ぶパス | 本描画・アウトライン・深度・モーションベクター。シャドウキャスターは既定で呼ばない |
| 頂点入力 | パーツウェイト用のUVチャンネル2本（番号は設定可能） |
| ユニフォーム | レンダラー単位（複数キャラ対応）。パーツ数、ピボット（ビュー空間）、パラメータ3ベクトル×8 |
| 法線・視線ベクトル | 変形前の値を使う（リム・マットキャップが歪まない） |
| 無効時 | 完全に素通し |
| カメラ依存 | ピボットはカメラごとに描画直前に設定する |
| バウンズ | レンダラーのバウンズを拡張する（カリング抜け防止） |

シェーダ別の対応:

| シェーダ | 対応方法 |
|---|---|
| MToon | UniVRM同梱の MToon10 を複製してフックを入れた `KiraMToon` を用意し、実行時にマテリアルを差し替える（プロパティ名は同一）。VRMファイル上は標準MToonのまま |
| KIRITTOON | 上のインクルードと呼び出しを各パスに追加する。必要条件は「空きUV2本」「射影前にビュー空間位置を触れること」 |
| three-vrm | `MToonMaterial` に同じ式をGLSLで注入する。ユニフォーム名とプリセットは共通 |
| 改造できないシェーダ | 互換モード（CPUでメッシュを焼いて変形）。v2以降 |

---

## 6. Look層

詳細は動画を確認してから詰める。v0.1 では構成だけ決める。

- **3レイヤー描画**: 背景／キャラ／手前FX を別々に描いて合成する。レイヤー単体（透過）の書き出しもできる。
- **白フチ**: キャラレイヤーのアルファを膨張させて作る（シェーダのアウトラインとは別物）。
- **背景部品**: ハーフトーン、市松、グラデ、ストライプ。パレット入力付き。
- **粒子**: 十字キラキラ、破片、星、紙吹雪。Clock駆動でループ可能。
- **屈折**: 宝石・ガラスの法線で背景をRGB別にずらす（分散付き）。強さはスライダー。
- **ポスト**: グロー、色収差、ホログラム風グラデ、グリッター。
- **パレット**: メイン・アクセント・白の3色。`section` ごとに切り替え可能。
- **音連動**: `beat_pulse` → キラキラの発生とグロー、`energy` → 粒子密度、`section` → パレットと模様。

---

## 7. プリセット

```jsonc
{
  "clock":  { "mode": "timeline", "bpm": 120, "loop_beats": 8 },
  "motion": {
    "recipe": "fuwafuwa",                       // fuwafuwa / pyoko / yura
    "intensity": 1.0,
    "channels": { "breath": { "beats": 8, "amp": 1.0 } },
    "chains": {
      "auto": true,                             // SpringBone設定から生成
      "keep_springbone": ["skirt"],
      "overrides": { "hair_front": { "soft": 0.6, "lag": 0.5, "amount": 1.2 } }
    }
  },
  "parts": {
    "auto": true,
    "head":   { "persp": -0.7 },
    "hand_L": { "auto_punch": 0.5, "auto_grow": 0.3 },
    "hand_R": { "auto_punch": 0.5, "auto_grow": 0.3 }
  },
  "look": {
    "palette": ["#ffb400", "#3aa0ff", "#ffffff"],
    "background": "halftone",
    "particles": ["sparkle", "shard"],
    "post": { "glow": 0.6, "chroma": 0.3, "sticker_outline": 6 }
  }
}
```

ショット定義＝ VRM ＋ モーション（VRMA＋manifest）＋ プリセット ＋ 上書き。色違い・曲違いはショット定義の差し替えで作る。

---

## 8. mocaptyx dance 連携

### 8.1 そのまま使えるもの

| 出力 | KiraKit での使い道 |
|---|---|
| `body.vrma` | 体の動き。追従フィルタと自動ドライブの入力 |
| `manifest.fps` / `duration` / `frames` | ベイク範囲 |
| `manifest.tempo_bpm` | Clockのフォールバック（拍時刻が無いとき） |
| `manifest.choreography[]` | `section`（パレット・模様の切り替え） |
| `manifest.root_motion_mode == "loopable"` | 周期扱いにして完全ループでベイク |

### 8.2 mocaptyx dance 側に追加してほしいもの

| 優先 | 追加 | 理由 | 現状 |
|---|---|---|---|
| **必須** | 拍の時刻: `beat_times[]` `downbeat_times[]` `beat_strengths[]` | 拍同期の時計 | 内部にあるが、manifestには個数しか出ていない |
| 高 | `energy`（時刻列＋値） | 粒子密度・強さの連動 | 内部にある |
| 中 | カメラトラック（位置・回転・画角、毎フレーム） | パーツ別パースはカメラ依存 | `camera.vmd` のみ |
| 低 | 表情（`mouth_open` `smile`）を VRMA の expressions に | 口と笑顔 | 内部にあるが VRMA に未出力 |
| 低 | `onset_times[]` | 細かいキラキラのきっかけ | 内部にある |

出力形式の案（`manifest.json` に `timeline` キーを足す。別ファイル `timeline.json` でも可）:

```jsonc
"timeline": {
  "version": 1,
  "beat_times": [0.52, 1.02, 1.52],
  "downbeat_times": [0.52, 2.52],
  "beat_strengths": [0.9, 0.4, 0.6],
  "energy": { "fps": 10, "values": [0.21, 0.25, 0.4] }
}
```

`timeline` が無い場合は `tempo_bpm` から等間隔の拍を作って動かす（劣化動作）。

### 8.3 担当分け

| 対象 | 担当 |
|---|---|
| hips・脚・足 | mocaptyx dance のみ。KiraKit は触らない |
| 背骨・胸・首・頭 | mocaptyx dance が主。KiraKit は小さく加算 |
| 腕・手 | mocaptyx dance のみ（KiraKit は見た目のパースだけ変える） |
| 指 | どちらも未対応。必要ならKiraKitに手のポーズプリセットを足す（v2） |
| 髪・耳・しっぽ・リボン | KiraKit（追従フィルタ） |
| スカート | VRM SpringBone（既定） |
| 口・笑顔 | mocaptyx dance（出力されるようになったら） |
| 瞬き・瞳ハイライト | KiraKit |
| カメラ | mocaptyx dance のトラックがあればそれ。無ければ KiraKit 側で固定または手付け |

### 8.4 実行順（Unity）

```
Vrm10Instance（VRMA適用 → コンストレイント → 視線 → 表情 → SpringBone）
 → KiraMotion（加算と追従フィルタ。LateUpdateの後段）
 → KiraDeform（カメラごとに描画直前、ピボットとパラメータを設定）
 → 描画（KiraMToon / KIRITTOON）
 → KiraLook（レイヤー合成とポスト）
```

- Timeline／Recorder からは `Evaluate(time)` で決定的に評価する（Mo-Cap の `Sample(time)` と同じ流儀）。
- VRMAは30fps。KiraKit は描画fpsで補間して評価する。

---

## 9. 自動と手動

| 作業 | 自動 | 手動で触る場面 |
|---|---|---|
| パーツ分けとウェイト | ヒューマノイドボーンとスキンウェイトから | しっぽ・小物を足すとき |
| 揺れものチェーン | SpringBone設定から | 揺れ方の好み調整 |
| 左右・房ごとの位相ばらし | シード値から | 不要 |
| 顔のフラット化 | 常時＋向きで減衰 | 不要 |
| 手足のパース誇張 | カメラへの突き出し量から | 決めカットで盛る |
| 拍同期 | timeline から | 不要 |
| 色・模様・粒子 | プリセット | 作品ごとに選ぶ |

---

## 10. 受け入れ基準

| # | 基準 |
|---|---|
| 1 | `loop` モードで、先頭フレームと1周後のフレームの全ボーン姿勢と全頂点が一致する |
| 2 | 同じ入力なら、評価開始フレームを変えても同じフレームの結果が一致する（ベイク） |
| 3 | 全パラメータ初期値で、KiraKit無しと画像が一致する（MToon・KIRITTOON 両方） |
| 4 | `persp` を変えても、ピボットの画面位置と大きさが動かない（誤差0.5px以内） |
| 5 | `persp = −1` のパーツは、頂点の画面位置が奥行きに依存しない |
| 6 | 手首・首・足首に継ぎ目や段差が出ない |
| 7 | ダンス連携時、足の接地位置がKiraKit無しと変わらない |
| 8 | `timeline` 無しでも `tempo_bpm` だけで動く |

---

## 11. 実装フェーズ

| フェーズ | 内容 | 置き場所 |
|---|---|---|
| P0 | `timeline` の出力追加 | Dance-Gen |
| P1 | Clock＋Motion層（追従フィルタ、加算、ループ／ベイク） | 本リポジトリ（Unity） |
| P2 | Deform層（フック、`KiraMToon`、パーツ自動生成、自動ドライブ） | 本リポジトリ（Unity） |
| P3 | KIRITTOON へのフック組み込み | KIRITTOON 側 |
| P4 | Look層 | 本リポジトリ（Unity） |
| P5 | three-vrm 版（同じプリセットを読む） | mo-kyapu-ai-motion |

P1の最初の成果物は「VRM SpringBone」と「追従フィルタ」を並べた比較動画。ここで動きの質が出なければ先に進まない。

---

## 12. 未確定事項

1. 「mocaptyx dance」は Dance-Gen の `dance_motion_service` で合っているか。
2. KIRITTOON の実行環境（Unity の Built-in／URP、または別環境）とソースの場所。空きUVチャンネルはあるか。
3. Unity のバージョンとレンダーパイプライン。
4. 主な出力は動画書き出し（オフライン）か、ライブ配信（リアルタイム）か。両方なら優先はどちらか。
5. スカートを SpringBone のまま残す既定でよいか。
6. 参考動画の確認手段（画面録画の共有、または `x.com` の許可）。Look層と動きの細部はこれ待ち。
