# 01. 研究・OSS・データセット調査（2026年10月時点）

> 調査日：2026-10-24
> 方針：論文の数値は**その論文の条件下の値**として記録し、ChairMotion の机下・近距離・椅子回転ありの環境で同じ精度が出るとは見なさない。
> 研究間の数値も比較・順位付けしない（入力・距離・被験者・正解データ・指標が異なるため）。

## 0. 検証ステータスの凡例

| 記号 | 意味 |
|---|---|
| ✅ | 一次情報（論文の要旨・出版社ページ・リポジトリの LICENSE 等）で確認できた |
| ⚠️ | 存在は確認できたが、詳細（数値・ハード・ライセンス）は未確認 |
| ❌ | 今回の調査では存在自体を確認できなかった（名称違い・未公開の可能性） |

今回の調査環境では arXiv / ACM DL / ti.com の本文を直接取得できず、検索エンジンの要旨・抜粋と GitHub 上のファイルで裏付けを取った。⚠️ の項目は着手前に原文を読むこと。

---

## 1. 元の計画書に記載された研究の再確認

| 研究 | ステータス | 確認できた内容 | 計画書との差分・注意 |
|---|---|---|---|
| **ChairPose**（arXiv 2508.01850、ACM DOI 10.1145/3746059.3747750） | ✅ | 圧力のみで着席全身姿勢を推定。2段階の生成モデルと、物理ベースのデータ拡張を使う。椅子形状に依存しないセンシングマット。8名×4脚で、**未知の人物かつ未知の椅子の条件で MPJPE 89.4mm**。データセット TDSD（12動作・4脚・8名、圧力・3D姿勢・椅子3Dスキャンを同期）。入力は **80×28 = 2,240点**の時系列。コードは **GPL-3.0**（LICENSE で確認）。データは Kaggle（`lalaray/chairpose`） | ACM DOI は存在するが、UIST'25 採録はプロシーディングスで確認すること。**Kaggle データのライセンスは未確認**。学習済み重みはリポジトリに見当たらない。8名は汎化の評価として少ない |
| **3D-Sitpose**（DOI 10.1145/3793858） | ✅（要旨） | **ACM Transactions on Sensor Networks** 22(2), 2026年2月オンライン公開。ミリ波点群を2チャネルのNNで処理し、**25関節**を出力。主要6角度で姿勢フィードバック。**20名・屋内2環境**。全関節の平均ユークリッド誤差 **6.65cm** | 計画書の「IWR1443」「Kinect 教師」「2.4m前方」は**今回の調査では裏付けが取れなかった**。本文で要確認。いずれにせよ机下（0.3〜0.8m・斜め上から）とは幾何条件が違う |
| **mmLite**（ICDCS 2026） | ⚠️ | 「mmLite: Leveraging mmWave Radar for Efficient Seated Pose Estimation」Ruili Shi, Shuai Wang, Wenchao Jiang。IEEE ICDCS 2026 pp.894–904。DOI 10.1109/2575-8411.2026.00090 | 計画書の 1.04cm / 2.23° / 86.4ms / RPi 4B は**今回確認できず**。評価条件（被験者内か被験者間か、距離、関節数）を原文で必ず確認する |
| **SUPER**（arXiv 2407.02455, 2024） | ✅ | 近接配置した**2台**のミリ波レーダーで着席上半身を推定。強度点群とドップラー点群をマスキングで融合し、SMPL パラメータを出力。被験者を1人ずつ除外する評価で、ベースラインを 30〜184% 上回る | 出力が SMPL のため商用時は人体モデルを差し替える必要がある（→03） |
| **mmWave-DensePose**（2026） | ❌ | 名称では見つからない | 近い研究：**mmPoint**（BMVC 2023。単一フレームから高密度点群を生成、コード公開あり）、**mmDEAR**（2025。点群を高密度化する2段構成。2Dマスクは学習時のみ使い、推論は画像なし） |
| **m2C2**（IROS 2026） | ❌ | 名称では見つからない | 代替：**RC-AutoCalib**（CVPR 2025。レーダーとカメラのオンライン自動キャリブレーション）、2403.10981（近距離MIMOレーダーと深度センサーの自動キャリブレーション）、京大の「動く人物からのカメラ外部パラメータ推定」（IROS 2022） |
| **RF-PoseR**（2026） | ⚠️ | 「RF-PoseR: A Human Pose Rectifier for mmWave Radar-Based Pose Estimation」IEEE IoT Journal、2026-01-06。DOI 10.1109/JIOT.2026.3651437、第一著者 Qi Chen（中国科学技術大学） | オープンアクセスではない。関連：**RadarFlowPose**（NeurIPS 2026 ポスター。粗い姿勢事前分布をフローマッチングで精緻化し、ドップラー情報も使う） |
| **MotionPRO**（CVPR 2025） | ✅（計画書のリンク） | 圧力＋RGBの融合 | 床圧力が対象。椅子に適用するには再設計が必要 |
| **BodyMAP**（CVPR 2024, arXiv 2404.03183） | ✅ | ベッド上の人体メッシュと3D圧力マップを同時推定 | SMPL 依存（商用には注意） |

---

## 2. 追加で見つかった重要研究（2024〜2026）

### 2.1 座面・接触圧からの姿勢推定

| 研究 | ステータス | 要点 | ChairMotion への示唆 |
|---|---|---|---|
| **Intelligent Seat**（UbiComp Companion '24, MIT CSAIL / GIST） | ✅ | 高密度ピエゾ抵抗シートで着席3D関節を推定。6名×7姿勢×20回。**平均関節誤差 20.2cm、姿勢分類 96.3%**。MIT DSpace の論文は CC BY | **座面だけでは3D関節の精度が出にくい**ことを示す。分類は得意でも回帰は苦手。レーダー等を併用すべき根拠になる |
| **PressureMesh / MDP-Net**（arXiv 2608.09550, USTC） | ✅（要旨） | 複数の圧力デバイスからの時系列で人体メッシュを推定。MoE型のマルチモーダル融合。MDPデータセット。**関節誤差 12.6cm** | 「複数の圧力面（座面＋背もたれ＋フットマット）」を MoE で統合する設計は ChairMotion にそのまま参考になる。椅子の圧力で3D姿勢を推定した先行例として **Zhao et al.** に言及がある（椅子圧力の時系列＋3Dキーポイントのデータセット）→原典を要追跡 |
| **Spatio-temporal Masked Transformer による曖昧な圧力からの姿勢推定**（arXiv 2303.05691） | ✅ | 圧力の時系列をマスク付き Transformer で扱う | 圧力エンコーダーの時系列化と、欠損セルへの頑健化に使える |
| **P2P-Insole**（arXiv 2505.00755） | ✅ | 足裏圧力＋IMUで姿勢推定 | 将来のフットマット（床）で足の接地を推定する参考 |

**結論（圧力）**：座面圧力は「骨盤・重心・接触状態・脚組みの分類」には強いが、**足首・足先の3D位置は原理的に観測できない**。公開結果は 89〜200mm 級であり、座面単体で「高精度」を謳うのは不適切。

### 2.2 ミリ波レーダーによる3D姿勢

| 研究 | ステータス | 要点 | 示唆 |
|---|---|---|---|
| **M4Human**（CVPR 2026, arXiv 2512.12378） | ✅ | 66.1万フレーム・999シーケンス・20名・50動作。RGB・深度・**レーダー点群と生のレーダーテンソル（RT）**を同期。正解は Vicon 由来のメッシュ。顔はマスク済み | 現時点で最大級のミリ波人体データ。**生テンソルを含む**ので、点群とヒートマップ入力の比較研究に使える。ライセンスと商用可否は未確認 |
| **Wave2Body**（arXiv 2607.18875, 2026-07） | ✅ | 姿勢推定を「レーダートークン → 身体トークンの翻訳」として定式化。自己教師ありのレーダートークナイザ（**ラベルなしレーダーデータ**）、姿勢のみのデータで学習した身体トークナイザ、軽量な翻訳器の3つで構成。ドメインをまたぐ汎化性能が高く、計算コストも低い。**コードは MIT**（LICENSE で確認） | **最小限のペアデータで学習できる**設計として最有力。ただし論文の学習は M4Human / mmBody / AMASS / SMPL-X に依存しており、いずれも商用に制約がある → 身体トークナイザを自前の商用可能な姿勢データで再学習すれば構造を流用できる |
| **RAPTR**（NeurIPS 2025, arXiv 2511.08387, MERL） | ✅ | Transformer による2段デコーダ。HIBER で関節誤差 34.3%減、MMVR で 76.9%減 | MERL がコードを配布（ライセンス要確認） |
| **物理ガイド前処理**（arXiv 2603.08236, Warwick, 2026-03） | ✅ | 物理的な前処理で、既存のミリ波手法よりパラメータを 55.7〜88.9% 削減。**Raspberry Pi 5 で 18.2FPS、ピークRAM 7.3MB**（Balanced 構成） | 組込み・低遅延化の参考。同グループの 2512.23054（物理に基づく人物表現）も参照 |
| **MS-STPoseNet**（IEEE IoT-J 2026） | ⚠️ | 短期の時系列文脈で点群から3D姿勢を推定。自前データで MPJPE 3.08cm | 自前データ上の値なので、そのまま比較しない |
| **R-DMRF-HPE**（Measurement 2026） | ✅（要旨） | カメラ＋ミリ波を融合。レーダー点群にGNNを使い、**品質に応じて重みを適応的に変える**。mRI で MPJPE 91.82mm | Confidence-Aware Fusion の直接の先行例 |
| **mmWave Sit-to-Stand 比較**（arXiv 2411.14656） | ✅ | 着席から起立する動作で、ミリ波をウェアラブル・Kinect と比較。点群を17関節に変換 | 下半身の観測性を確かめた先行例 |
| **Physics-Bounded mmWave Sensing**（arXiv 2608.14176） | ⚠️ | プライバシー保護と、スケジューリング可能な姿勢推定 | 要精読 |
| **ProbRadarM3F**（arXiv 2405.05164） | ✅ | 確率マップに導かれた多形式特徴融合 | 点群とヒートマップの併用の参考 |
| **SitPose**（arXiv 2412.12216） | ✅ | 深度センサーで着席姿勢を分類（36名、33,409サンプル） | 深度カメラという代替案の参考 |

**結論（レーダー）**：公開研究の大半は**1.5〜3mの距離から全身を見る**配置であり、**机下0.3〜0.8mから脚だけを見る**研究は今回見つからなかった。ここが ChairMotion 最大の未知領域であり、Phase 2 で最初に検証する（→02 §3.3）。

### 2.3 マルチモーダル融合・キャリブレーション

- **圧力＋レーダー＋RGB** を椅子で組み合わせた研究は**見つからなかった**（空白領域。独自性があり、論文化も見込める）。
- 参考になる設計：R-DMRF-HPE（品質適応の重み付け）、PressureMesh（複数の圧力面を MoE で統合）、AdaptiveFusion（arXiv 2409.04851。可変のモダリティ・視点の融合）、Towards Balanced Multi-Modal Learning（arXiv 2501.05264。モダリティの不均衡対策）。
- キャリブレーション：RC-AutoCalib（CVPR 2025）、人物の動きを使ったキャリブレーション（京大 IROS 2022）、M4Human のコーナーリフレクタ＋PnP 方式。

### 2.4 人体モデル（SMPL の代替）

| モデル | ステータス | ライセンス | 備考 |
|---|---|---|---|
| **Anny**（NAVER LABS Europe, arXiv 2511.03589） | ✅ | コードは **Apache-2.0**、MakeHuman 由来アセット（MPFB2）は **CC0** | スキャンを使わず人体計測統計から構築（乳児〜高齢者）。v0.5（2026-06）で NVlabs の SOMA リグ／トポロジーに対応。**`smplx` トポロジーは非商用**なので使わない |
| **MHR / Momentum Human Rig**（Meta, arXiv 2511.15586） | ✅（コード） | リポジトリの LICENSE は **Apache-2.0** | モデルアセット（`assets.zip`）を別途ダウンロードする。**アセットのライセンス文面は要確認** |
| SMPL / SMPL-X / STAR / SUPR | ✅ | 研究は無償、**商用は別契約** | 製品には組み込まない |

---

## 3. OSS・ツール（ライセンスは 03 で詳述）

| OSS | 用途 | ライセンス確認 |
|---|---|---|
| OpenRadar（presenseradar/openradar） | Range/Doppler FFT・角度推定・点群の基礎 | ✅ Apache-2.0 |
| abdullah-al-masud/iwr6843aop | IWR6843AOP とカメラの同時記録 | ✅ MIT（2025） |
| huangshk/mmWave-Pose-2D | IWR6843 で 2D 骨格推定 | ⚠️ **LICENSE ファイルが見つからない**（＝権利者が全権利を保持）。シリアル読み出し部は kirkster96 のリポジトリから流用されている |
| urad-by-Anteral/urad-mmwave-core | レーダーデータの取得・処理 | ✅ MIT（2026, Anteral S.L.） |
| gmntu/psa | Velostat の 16×16 / 32×32 圧力アレイ（ESP32 Thing Plus + CD74HC4067） | ⚠️ **LICENSE ファイルが見つからない**。構造自体は汎用的なので、参考にして独自に再設計する |
| Wave2Body（Galaxywalk/Wave2Body） | トークン翻訳型のレーダー姿勢推定 | ✅ MIT（データ・SMPL-X は別） |
| mmPoint（NUAAXQ/mmPoint） | レーダー点群の高密度化 | ⚠️ LICENSE が見つからない |
| ChairPose（lalasray/ChairPose） | 圧力からの着席姿勢推定 | ✅ GPL-3.0（製品には取り込まない） |
| MediaPipe（Pose / Hand Landmarker） | Webcam で上半身・手 | ✅ コードは Apache-2.0、⚠️ 各モデルカードの条件は要確認 |
| MMPose / RTMPose / RTMW | Webcam の全身133点 | コードは Apache-2.0、⚠️ 重みの学習データの条件は要確認 |
| Lab Streaming Layer（liblsl） | 研究時の多センサー時刻同期記録 | MIT（要再確認） |
| Warudo プラグイン SDK | アバター連携（C#, .NET 8、Playground でホットリロード） | Warudo 規約に従う。公式の WarudoPluginExamples に、VMC を PoseTrackingTemplate として登録する例がある |

---

## 4. 学習データセット

| データセット | 中身 | 商用可否 | 用途 |
|---|---|---|---|
| ChairPose **TDSD** | 椅子圧力（80×28）＋3D姿勢＋椅子スキャン、8名×4脚×12動作 | ⚠️ Kaggle の条件が未確認 | 研究段階の事前検証。商用学習はライセンス確認後のみ |
| **M4Human** | レーダー点群・生テンソル・RGB-D・Vicon メッシュ、20名 | ⚠️ 未確認（SMPL-X 依存） | 研究評価用 |
| **mRI** | ミリ波＋RGB-D＋IMU、リハビリ動作、20名・16万フレーム超 | リポジトリは CC0。新版は Dryad で配布（Dryad は原則 CC0）。⚠️ 正解に SMPL 由来の成分があるか要確認 | **商用学習に使える可能性が最も高い**公開レーダーデータ |
| MM-Fi | IWR6843 を含む多モーダル | ⚠️ ライセンスが見つからない（非商用の可能性） | 研究評価用 |
| mmBody | ミリ波＋メッシュ | ⚠️ メールで申請して条件に同意する方式 | 研究評価用 |
| mmFree-Pose（MDPI Bioengineering 2025） | プライバシー保護向けのミリ波データ | ⚠️ | 研究評価用 |
| AMASS | 大規模モーションの集積 | ❌ 非商用 | 使わない |
| CMU Graphics Lab Motion Capture DB | 光学モーキャプ | 研究は自由、**商用製品への組み込みは可・データの再販は不可**と案内（⚠️ 現行の文言を保存しておく） | 姿勢事前分布（身体トークナイザ）の商用学習候補 |

**結論（データ）**：着席時の下半身について、**商用学習に使える「圧力＋机下レーダー」のデータは存在しない**。自社データセット（→04）が必須であり、これが長期的な参入障壁になる。

---

## 5. 調査から導いた設計判断（要約）

1. **座面圧力は「骨盤・接触・状態」担当**とし、脚先の座標を出させない（Intelligent Seat の 20cm、ChairPose の 89mm が示す限界）。
2. **机下レーダーの近距離・脚専用という条件には研究の前例がない** → 最初にリスクを潰す。並行して**机下の深度（ToF）センサー**と必ず比較する（→02 §3.4）。
3. **少ないペアデータで学習するため、Wave2Body 型の「センサー表現・身体事前分布・対応付け」の分離**を採用する。身体事前分布は商用可能なモーションデータと自社データで学習する。
4. **品質適応の重み付け（R-DMRF-HPE）＋複数の圧力面の統合（PressureMesh）＋時系列での補正（RF-PoseR / RadarFlowPose）**を組み合わせたものを、ChairMotion の融合の骨格にする。
5. 人体モデルは **Anny（Apache-2.0）または MHR（コードは Apache-2.0、アセットは要確認）**。出力は VRM Humanoid のボーンに直接合わせ、SMPL には依存しない。

## 参考リンク

- ChairPose: https://arxiv.org/abs/2508.01850 / https://dl.acm.org/doi/10.1145/3746059.3747750 / https://github.com/lalasray/ChairPose
- 3D-Sitpose: https://dx.doi.org/10.1145/3793858
- SUPER: https://arxiv.org/abs/2407.02455
- Intelligent Seat: https://dspace.mit.edu/handle/1721.1/157621
- PressureMesh: https://arxiv.org/pdf/2608.09550
- M4Human: https://arxiv.org/abs/2512.12378 / https://cvpr.thecvf.com/virtual/2026/poster/38566
- Wave2Body: https://arxiv.org/pdf/2607.18875 / https://github.com/Galaxywalk/Wave2Body
- RAPTR: https://arxiv.org/abs/2511.08387 / https://merl.com/research/downloads/RAPTR
- RadarFlowPose: https://neurips.cc/virtual/2026/poster/148337
- 物理ガイド前処理: https://arxiv.org/abs/2603.08236
- R-DMRF-HPE: https://www.sciencedirect.com/science/article/abs/pii/S0263224126003969
- mmWave Sit-to-Stand: https://arxiv.org/html/2411.14656v2
- mmPoint: https://www.cs.ox.ac.uk/files/14371/BMVC-2023-mmPoint.pdf
- mmDEAR: https://arxiv.org/html/2503.02375v1
- RC-AutoCalib: https://arxiv.org/html/2505.22427v1
- Anny: https://europe.naverlabs.com/blog/anny-a-free-to-use-3d-human-parametric-model-for-all-ages/ / https://pypi.org/project/anny/
- MHR: https://arxiv.org/abs/2511.15586v3
- mRI: https://github.com/sizhean/mRI
- Warudo スクリプティング: https://docs.warudo.app/docs/scripting/overview / https://docs.warudo.app/docs/mocap/vmc
