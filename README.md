# Toon Hair Studio — VTuber向け アニメ髪メーカー (Blender 4.2+)

カーブを描くだけで、**トポロジーがきれいなアニメ風の髪**を作れる Blender アドオンです。

- ローポリ〜ミドルポリ(Low / Mid / High プリセット)
- 裏面は **厚みあり / 裏面ポリ / なし(片面)** から選択
- 1房ずつ独立した毛束も、**つながった髪の塊(毛先だけ分かれる)** も作れる
- 頭の球体方向の法線で、**アニメ影がきれいにまとまる**
- 毛先に向かって細くなる**アウトライン**(片面でも縁に線が出る)
- EEVEE 用トゥーンマテリアル(2値影・内側の暗色・ギザギザ天使の輪・リムライト)

| 正面 | トポロジー | 背面 |
|---|---|---|
| ![front](docs/preview_front.png) | ![topology](docs/preview_topology.png) | ![back](docs/preview_back.png) |

*付属の「サンプル髪型を生成」で作ったものを EEVEE でレンダーしたままの画像です(Mid、厚みあり、前髪・横髪・後ろ髪は連結モード)。*

---

## インストール

1. `dist/toon_hair_studio-1.0.0.zip` をダウンロード
2. Blender 4.2 以降:`編集 > プリファレンス > アドオン > ▼ > ディスクからインストール` で zip を選択
3. 3Dビューのサイドバー(`N`キー)に **ToonHair** タブが出ます

Blender 5.2 で動作確認済みです。インストールと全機能の自動テストを実施しています。

## 使い方

1. 3Dカーソルを頭の中心に置き、**「毛束グループを追加」** を押す
   - `TH_HeadCenter`(球の Empty)ができます。頭に合わせて移動・スケールしてください。楕円体にもできます。
   - これが「毛束の向き」と「球状の法線」の基準になります。
2. できたカーブを編集モードで編集すると、メッシュがリアルタイムで更新されます
   - **1スプライン = 1房**です。`E` で押し出す、複製するなどで房を増やせます。
   - **Alt+S** で、その位置だけ幅を変えられます。**Ctrl+T** でねじれます。
   - カーブの「ドロー」ツールの奥行きを「サーフェス」にすると、頭の表面に直接描けます。
   - **「3Dカーソルに毛束を追加」** を使うと、カーソル位置を根元にして1房追加できます。
3. 前髪・横髪・後ろ髪・アホ毛などはグループを分けて、それぞれ設定します
4. 完成したら **「書き出し用メッシュに統合」** を押す
   - 1つの独立したメッシュにまとめられ、VRM / FBX 用に使えます。

すぐ試したい場合は **「サンプル髪型を生成」** を押してください。

## 主な設定

### 形状 / トポロジー
| 項目 | 内容 |
|---|---|
| LOD | Low(長さ6×断面2)/ Mid(12×4)/ High(20×6)/ Custom |
| 毛先に分割を寄せる | 幅が急に変わる毛先に分割を寄せて、ローポリでもシルエットを保つ |
| 裏面 | **厚みあり**:閉じた立体。カリングONでよく、アウトラインが最も安定<br>**裏面ポリ**:片面に反転コピーを付ける。Unity/MToon の Cull Back でも裏から消えない<br>**なし**:最軽量。両面表示マテリアルで表示(自動で切替) |
| 断面 | レンズ(定番)/ 山形(中央に稜線)/ 丸 / 板 |
| 幅・厚み・反り | 反りを強くすると、毛束ごとに溝ができて立体感が出る |
| 根元の幅・膨らみ位置・毛先の形 | 根元 → 膨らみ → 尖った毛先、というアニメ毛束のシルエット |
| 毛先 | 尖り(三角形は毛先だけ)/ 平ら(全部四角形) |
| X ミラー | 頭の中心の X 軸で左右対称に生成 |

### 連結(つながった髪)
隣り合う房を、根元から「切れ込み位置」まで頂点を共有して1枚の面にし、その先で房ごとに分けて尖らせます。毛先がギザギザの前髪や、一体感のある後ろ髪向けです。

- 全部四角形のままで、「厚みあり」なら根元以外は閉じた多様体になる
- 並び順は「頭の周り順(自動)」か「カーブ順」から選べる
- 根元同士が「連結する距離」より離れていれば、自動で別の塊になる
- 「つなぎ目の厚み」を下げると、房の境目に溝が入って毛束感が出る

### シェーディング
- **頭の球体**(推奨):法線を頭の楕円体方向へブレンドして、髪全体を1つの塊として陰影付けする。アニメ影の定番テクニック(法線転写・球状法線)をアドオン内で計算します。
- **スムーズ**:形状そのままのスムーズ法線
- **プロキシ転写**:任意のメッシュから Data Transfer で法線を転写(自分で作った髪のプロキシを使う場合)

マテリアルのパネルで、明部・影・内側・影の境界・天使の輪(高さ・幅・ギザギザ)・リムライトを調整できます。

> **背景(World)が明るいと全体が明部になって影が消えます。** トゥーン表示では World の強さを下げるか、ライトパスの「カメラレイ」だけで背景色を見せてください(`tests/render_preview.py` に例があります)。カラーマネジメントは **Standard** 推奨です(「トゥーン表示設定」ボタンで切り替え)。

### アウトライン
- Solidify ではなく自前で反転ハルを生成します。縁を横方向にも広げるので、**片面の髪でも縁に線が出ます**。
- 線は毛先に向かって細くなり(毛先の線の細さ・細くなり始め)、毛先は尖った線になります。
- 影は落としません(アウトラインのオブジェクトはシャドウ不可視)。

## VRM / VTuber 向けメモ

- **アウトラインは MToon 側で描くのが基本**です。統合するときは「アウトラインも出力」を OFF にしてください。
  - MToon のアウトラインは頂点法線方向に膨らむので、このアドオンの滑らかなカスタム法線と相性が良いです。
- MToon の Cull Mode と裏面の対応:
  - **Back**:「厚みあり」か「裏面ポリ」
  - **Off**:「なし」
- 頂点グループ:
  - `TH_root_tip`:根元0 → 毛先1
  - `TH_pin`:根元1 → 毛先0(クロスのピンや、揺れウェイトの下地に)
- 頂点属性:`TH_t`(根元→毛先)、`TH_inner`(内側=1)、`TH_head`(頭の方位角・高さ)
- UV:U = 房の幅方向 0〜1、V = 根元0 → 毛先1。グラデーションテクスチャやハイライトテクスチャがそのまま使えます。
- 統合メッシュはカスタム法線を保持しています。FBX 書き出しでもそのまま出力されます(スムージングはデフォルトの「法線のみ」で問題ありません)。

## リサーチまとめ(なぜ自作したか)

| 既存ツール | 方式 | 物足りない点 |
|---|---|---|
| [Hair Tool](https://80.lv/articles/hair-tool-for-blender/) | カーブ → UV 付きリボン | リアル寄りのヘアカード向け。トゥーン用の法線・線はなし |
| [Anime Hair Maker](https://superhivemarket.com/products/anime-hair-maker) / [Stylized Hair Pro](https://superhivemarket.com/products/stylized-hair-pro) / [Curve Hair](https://kushiro.gumroad.com/l/curve_hair) | カーブのベベル断面 | ベベル方式だと毛先が1点に潰れて重複頂点ができ、ねじれも起きやすい。連結した髪は手作業 |
| [Simple Hair Cards](https://superhivemarket.com/products/simple-hair-cards) / [Curve-Bstrands](https://superhivemarket.com/products/curve-bstrands) / [Bystedt 氏の無料 GN](https://www.cgchannel.com/?p=156171) | テクスチャアトラスのヘアカード | アルファカード前提でトゥーン向きではない |

トゥーン髪をきれいに見せる定番テクニックを、このアドオンに組み込みました。

- **法線転写 / 球状法線**:顔や髪を単純な形状の法線で陰影付けして、アニメらしい影にする([BNPR: Normal spherizing](https://bnpr.gitbook.io/bnpr/shaders/toon-shading-nbpr-abnormal-normal-sphering)、[80.lv](https://80.lv/articles/get-clean-normals-for-stylized-shading-with-this-free-blender-tool/))
- **反転ハル**:Solidify + 法線反転 + カリング。頂点グループで線の太さを変える([BNPR: Inverse hull](https://bnpr.gitbook.io/bnpr/outline/inverse-hull-method)、[可変太さ](https://bnpr.gitbook.io/bnpr/outline/adjusting-line-thickness-on-inverse-hulls))。ただし片面ポリだと縁に線が出ないので、本アドオンでは横方向にも広げたハルを生成しています。
- **裏面の扱い**:ヘアカードはエンジン側で両面表示が必要になる([Blender Artists](https://blenderartists.org/t/part-of-the-mesh-invisible-in-game/1527406))。カリングを前提にするなら裏面をモデリングする([Ready Player Me](https://docs.readyplayer.me/asset-creation-guide/validation/validation-checks/materials-validation/check-backface-culling))。MToon には両面表示とアウトライン幅の設定がある([MToon](https://wiki.virtualcast.jp/wiki/en/unity/shader/mtoonreference))。
- **平面から巻き付ける・カーブで作る**とトポロジーと UV がきれいになる([BlenderNation: Flat modeling anime hair](https://www.blendernation.com/2020/02/01/flat-modeling-anime-hair/))。本アドオンは弧長で等間隔にリサンプルして、格子状の四角形を直接生成します。

## 開発

```bash
pip install bpy            # Blender 5.x の Python モジュール
python tests/test_toon_hair.py     # 全モードのトポロジー / 法線 / 統合をテスト
python tests/render_preview.py     # docs/ にプレビューをレンダー(EEVEE)
```

テストでは、連結/独立 × 裏面3種 × LOD 3段階 × 断面4種 × 毛先2種の全組み合わせで、次の点を検証しています。

- n-gon がない
- 三角形は毛先だけ
- 縮退した面がない
- 多様体である(「厚みあり」は根元以外に穴がない)
- 外側の面が外を向いている
- カスタム法線とアウトラインがある

## ファイル構成

```
toon_hair_studio/
  __init__.py      プロパティ・オペレーター・UI・ライブ更新
  core.py          ジオメトリ生成(bpy 非依存):リサンプル、フレーム、断面、連結シート、法線、反転ハル
  build.py         Blender へのメッシュ書き込み・統合
  materials.py     EEVEE トゥーン / アウトラインマテリアル
tests/             自動テストとプレビューのレンダー
dist/              インストール用 zip
```

ライセンス:GPL-3.0-or-later
