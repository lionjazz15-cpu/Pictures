using System.Collections.Generic;
using System.IO;
using UnityEngine;
using UnityEngine.Rendering;

namespace PencilLine
{
    public enum LineOutputMode
    {
        [InspectorName("合成 (画面に線を重ねる)")] Composite,
        [InspectorName("線だけ (透過・撮影用)")] LinesOnly,
        [InspectorName("オフ")] Off,
    }

    [System.Serializable]
    public class MaterialLineOverride
    {
        public Material material;
        [Tooltip("このマテリアルに線を引くか (全部まとめてオン/オフ)")]
        public bool drawLines = true;
        [Header("線の種類")]
        public bool outline = true;
        [Tooltip("奥行きの段差 (腕が体の手前にある時など)")]
        public bool innerLines = true;
        [Tooltip("別オブジェクトとの交差")]
        public bool intersection = true;
        [Tooltip("同じメッシュ内でマテリアルが変わる所")]
        public bool materialBoundary = true;
        [Tooltip("折れ目の線 (顔などはオフ推奨)")]
        public bool creaseLines = true;
        [Tooltip("シワ (へこみ) の線 (顔や肌はオフ推奨)")]
        public bool wrinkleLines = true;
        [Tooltip("テクスチャの線 (フラット版の書き影の輪郭など)")]
        public bool textureLines = true;
        [Header("見た目")]
        [Range(0f, 4f)] public float widthMultiplier = 1f;
        public bool overrideColor = false;
        [ColorUsage(false)] public Color color = Color.black;
        public bool overrideColorTrace = false;
        [Range(0f, 1f)] public float colorTrace = 0f;
        [Tooltip("半透明マテリアルでも線の対象にする (アルファ0.5で切り抜き)")]
        public bool forceInclude = false;
    }

    /// <summary>
    /// Pencil+ 風のラインをカメラに描画するコンポーネント (Built-in RP)
    /// カメラに付けるだけで動きます。
    /// </summary>
    [ExecuteAlways]
    [ImageEffectAllowedInSceneView]
    [RequireComponent(typeof(Camera))]
    [AddComponentMenu("PencilLine/Pencil Line Effect")]
    public class PencilLineEffect : MonoBehaviour
    {
        [Header("対象")]
        [Tooltip("線を引くレイヤー")]
        public LayerMask targetLayers = ~0;
        [Tooltip("半透明マテリアルも切り抜きとして線の対象にする")]
        public bool includeTransparentAsCutout = false;
        [Range(0f, 1f)] public float transparentCutoff = 0.5f;

        [Header("出力")]
        public LineOutputMode outputMode = LineOutputMode.Composite;
        [Tooltip("Play中/書き出し時の内部解像度倍率。3 がおすすめ、4は重い")]
        [Range(1, 4)] public int supersampling = 3;
        [Tooltip("エディタ上 (非Play) のプレビュー用倍率")]
        [Range(1, 4)] public int editorPreviewSupersampling = 1;
        [Tooltip("線の太さはこの高さ(px)の画面を基準に指定します。4Kで出しても見た目の太さが変わりません")]
        public float referenceHeight = 1080f;

        [Header("線の太さ (基準解像度でのpx)")]
        [Range(0f, 10f)] public float outlineWidth = 3.91f;
        [Range(0f, 10f)] public float innerWidth = 3.64f;
        [Range(0f, 10f)] public float intersectionWidth = 3.59f;
        [Range(0f, 10f)] public float materialWidth = 3.59f;
        [Range(0f, 10f)] public float creaseWidth = 3.71f;
        [Tooltip("服のシワなど、へこんだ所に出る線")]
        [Range(0f, 10f)] public float wrinkleWidth = 3.76f;
        [Tooltip("フラット版の書き影の輪郭や、テクスチャの色の境目に出る線")]
        [Range(0f, 10f)] public float textureWidth = 0f;

        [Header("線の色")]
        [ColorUsage(false)] public Color lineColor = Color.black;
        [Tooltip("0=線色そのまま / 1=面の色を暗くした色 (色トレス)")]
        [Range(0f, 1f)] public float colorTrace = 0f;
        [Tooltip("MToon / lilToon のアウトライン色 (_OutlineColor) を線色として使う")]
        public bool useMaterialOutlineColor = false;
        [Tooltip("色トレスの明るさ (小さいほど暗い)")]
        [Range(0f, 1f)] public float colorTraceDarkness = 0.45f;
        [Tooltip("色トレスの鮮やかさ")]
        [Range(0f, 2f)] public float colorTraceSaturation = 1.3f;

        [Header("検出")]
        [Tooltip("内側輪郭の感度。小さいほど線が増える")]
        [Range(0f, 0.2f)] public float depthThreshold = 0.0322f;
        [Tooltip("斜めの面で線が出すぎる時は上げる")]
        [Range(0.5f, 8f)] public float depthSlopeBias = 1.78f;
        [Tooltip("この角度以上の法線の折れで線を引く")]
        [Range(0f, 180f)] public float creaseAngle = 85.5f;

        [Header("シワ (谷線)")]
        [Tooltip("ノーマルマップも見てシワを探す (線の強弱の向きにも使われます)")]
        public bool useNormalMaps = true;
        [Tooltip("どのくらいの幅のへこみを探すか (基準解像度でのpx)。大きいほど大きなシワだけ拾う")]
        [Range(1f, 12f)] public float wrinkleScale = 1.33f;
        [Tooltip("へこみの深さのしきい値。大きいほど深いシワだけ")]
        [Range(0.02f, 1f)] public float wrinkleThreshold = 0.168f;

        [Header("テクスチャ線")]
        [Tooltip("フラット化していないマテリアルで、テクスチャの色の差がこれ以上の所に線を引く (0でオフ)")]
        [Range(0f, 0.5f)] public float textureEdgeThreshold = 0f;

        [Header("強弱と間引き (欲しい線だけ残す)")]
        [Tooltip("0=全部同じ太さ / 1=強い線は太く、弱い線は細く")]
        [Range(0f, 1f)] public float emphasis = 0.178f;
        [Tooltip("これより弱い線は描かない。上げるほど大事な線だけ残る")]
        [Range(0f, 1f)] public float minStrength = 0f;
        [Tooltip("奥行きの段差がこの割合 (カメラからの距離比) 以上なら『強い線』とみなす")]
        [Range(0.01f, 0.5f)] public float strongDepthRatio = 0.01f;

        [Header("距離で細く (Reduction)")]
        public bool distanceReduction = true;
        public float reductionNear = 2f;
        public float reductionFar = 20f;
        [Range(0f, 1f)] public float reductionMinScale = 0.473f;

        [Header("光で強弱 (影側を太く)")]
        [Tooltip("未指定なら RenderSettings.sun → シーン内のDirectional Light")]
        public Light keyLight;
        [Range(0f, 3f)] public float litSideScale = 0.772f;
        [Range(0f, 3f)] public float shadowSideScale = 0.713f;

        [Header("材質ごとの設定")]
        public List<MaterialLineOverride> materialOverrides = new List<MaterialLineOverride>();

        [Header("線レイヤーのPNG連番書き出し (Play中のみ)")]
        public bool exportLinePNG = false;
        [Tooltip("プロジェクトフォルダからの相対パス")]
        public string exportFolder = "Recordings/Lines";
        [Tooltip("Unity Recorder を併用しない時だけオンにしてください")]
        public bool setCaptureFramerate = false;
        public int captureFramerate = 24;

        const int MaxMaterials = 1024;
        const int MaxObjects = 16383;

        Camera _cam;
        Shader _gbufferShader;
        Shader _edgeShader;
        Material _gbufferMat;
        Material _edgeMat;
        CommandBuffer _cmd;
        Texture2D _matParamTex;
        Color[] _matParamPixels;
        Texture2D _exportTex;
        int _exportFrame;
        Light _cachedLight;

        readonly Dictionary<Material, int> _matIndex = new Dictionary<Material, int>();
        readonly Dictionary<Material, MaterialLineOverride> _overrides = new Dictionary<Material, MaterialLineOverride>();
        readonly List<Renderer> _renderers = new List<Renderer>();
        readonly Plane[] _planes = new Plane[6];

        static class P
        {
            public static readonly int MainTex = Shader.PropertyToID("_PL_MainTex");
            public static readonly int MainTexST = Shader.PropertyToID("_PL_MainTex_ST");
            public static readonly int Color = Shader.PropertyToID("_PL_Color");
            public static readonly int Cutoff = Shader.PropertyToID("_PL_Cutoff");
            public static readonly int CullMode = Shader.PropertyToID("_PL_CullMode");
            public static readonly int ID = Shader.PropertyToID("_PL_ID");
            public static readonly int MatrixV = Shader.PropertyToID("_PL_MatrixV");
            public static readonly int MatrixVP = Shader.PropertyToID("_PL_MatrixVP");
            public static readonly int BumpMap = Shader.PropertyToID("_PL_BumpMap");
            public static readonly int BumpST = Shader.PropertyToID("_PL_BumpST");
            public static readonly int BumpScale = Shader.PropertyToID("_PL_BumpScale");
            public static readonly int UseBump = Shader.PropertyToID("_PL_UseBump");
            public static readonly int PaletteTex = Shader.PropertyToID("_PL_PaletteTex");
            public static readonly int PaletteCount = Shader.PropertyToID("_PL_PaletteCount");
            public static readonly int PaletteWeights = Shader.PropertyToID("_PL_PaletteWeights");
            public static readonly int LabelTex = Shader.PropertyToID("_PL_LabelTex");
            public static readonly int UseLabel = Shader.PropertyToID("_PL_UseLabel");
            public static readonly int LabelSize = Shader.PropertyToID("_PL_LabelSize");
            public static readonly int Widths2 = Shader.PropertyToID("_PL_Widths2");
            public static readonly int Wrinkle = Shader.PropertyToID("_PL_Wrinkle");
            public static readonly int TexEdge = Shader.PropertyToID("_PL_TexEdge");
            public static readonly int Emphasis = Shader.PropertyToID("_PL_Emphasis");
            public static readonly int SourceTex = Shader.PropertyToID("_MainTex");

            public static readonly int G0 = Shader.PropertyToID("_PL_G0");
            public static readonly int G1 = Shader.PropertyToID("_PL_G1");
            public static readonly int Edge = Shader.PropertyToID("_PL_Edge");
            public static readonly int Lines = Shader.PropertyToID("_PL_Lines");
            public static readonly int MatParams = Shader.PropertyToID("_PL_MatParams");
            public static readonly int Size = Shader.PropertyToID("_PL_Size");
            public static readonly int OutSize = Shader.PropertyToID("_PL_OutSize");
            public static readonly int Widths = Shader.PropertyToID("_PL_Widths");
            public static readonly int CreaseCos = Shader.PropertyToID("_PL_CreaseCos");
            public static readonly int DepthThreshold = Shader.PropertyToID("_PL_DepthThreshold");
            public static readonly int DepthSlopeBias = Shader.PropertyToID("_PL_DepthSlopeBias");
            public static readonly int PixScale = Shader.PropertyToID("_PL_PixScale");
            public static readonly int Reduce = Shader.PropertyToID("_PL_Reduce");
            public static readonly int LightDirV = Shader.PropertyToID("_PL_LightDirV");
            public static readonly int LightScale = Shader.PropertyToID("_PL_LightScale");
            public static readonly int PxScale = Shader.PropertyToID("_PL_PxScale");
            public static readonly int TraceParams = Shader.PropertyToID("_PL_TraceParams");
            public static readonly int OccTol = Shader.PropertyToID("_PL_OccTol");
            public static readonly int Radius = Shader.PropertyToID("_PL_Radius");
            public static readonly int SS = Shader.PropertyToID("_PL_SS");
        }

        void OnEnable()
        {
            _cam = GetComponent<Camera>();
            _exportFrame = 0;
            if (Application.isPlaying && exportLinePNG && setCaptureFramerate)
            {
                Time.captureFramerate = captureFramerate;
            }
        }

        void OnDisable()
        {
            if (_cmd != null)
            {
                _cmd.Release();
                _cmd = null;
            }
            DestroySafe(_gbufferMat);
            DestroySafe(_edgeMat);
            DestroySafe(_matParamTex);
            DestroySafe(_exportTex);
            _gbufferMat = null;
            _edgeMat = null;
            _matParamTex = null;
            _exportTex = null;
        }

        static void DestroySafe(Object o)
        {
            if (o == null) return;
            if (Application.isPlaying) Destroy(o);
            else DestroyImmediate(o);
        }

        bool EnsureResources()
        {
            if (_cam == null) _cam = GetComponent<Camera>();
            if (_gbufferShader == null) _gbufferShader = Shader.Find("Hidden/PencilLine/GBuffer");
            if (_edgeShader == null) _edgeShader = Shader.Find("Hidden/PencilLine/Edge");
            if (_gbufferShader == null || _edgeShader == null) return false;

            if (_gbufferMat == null) _gbufferMat = new Material(_gbufferShader) { hideFlags = HideFlags.HideAndDontSave };
            if (_edgeMat == null) _edgeMat = new Material(_edgeShader) { hideFlags = HideFlags.HideAndDontSave };
            if (_cmd == null) _cmd = new CommandBuffer { name = "PencilLine GBuffer" };
            if (_matParamTex == null)
            {
                _matParamTex = new Texture2D(MaxMaterials, 2, TextureFormat.RGBAFloat, false, true)
                {
                    hideFlags = HideFlags.HideAndDontSave,
                    filterMode = FilterMode.Point,
                    wrapMode = TextureWrapMode.Clamp,
                };
                _matParamPixels = new Color[MaxMaterials * 2];
            }
            return true;
        }

        void OnRenderImage(RenderTexture src, RenderTexture dst)
        {
            if (outputMode == LineOutputMode.Off || !EnsureResources())
            {
                Graphics.Blit(src, dst);
                return;
            }

            bool isSceneView = _cam.cameraType == CameraType.SceneView;
            int ss = Mathf.Clamp(Application.isPlaying && !isSceneView ? supersampling : editorPreviewSupersampling, 1, 4);
            int w = src.width;
            int h = src.height;
            int maxTex = SystemInfo.maxTextureSize;
            while (ss > 1 && (w * ss > maxTex || h * ss > maxTex)) ss--;
            int hw = w * ss;
            int hh = h * ss;

            var g0 = RenderTexture.GetTemporary(hw, hh, 0, RenderTextureFormat.ARGBFloat, RenderTextureReadWrite.Linear);
            var g1 = RenderTexture.GetTemporary(hw, hh, 0, RenderTextureFormat.ARGBHalf, RenderTextureReadWrite.Linear);
            var depth = RenderTexture.GetTemporary(hw, hh, 24, RenderTextureFormat.Depth);
            var edge = RenderTexture.GetTemporary(hw, hh, 0, RenderTextureFormat.ARGBFloat, RenderTextureReadWrite.Linear);
            var lines = RenderTexture.GetTemporary(hw, hh, 0, RenderTextureFormat.ARGBHalf, RenderTextureReadWrite.Linear);
            g0.filterMode = FilterMode.Point;
            g1.filterMode = FilterMode.Point;
            edge.filterMode = FilterMode.Point;
            lines.filterMode = FilterMode.Point;

            // 1. 法線・深度・ID を高解像度で書き出す
            BuildGBufferCommands(g0, g1, depth);
            Graphics.ExecuteCommandBuffer(_cmd);

            // 2. エッジ検出 → 太らせ
            SetupEdgeMaterial(g0, g1, edge, lines, ss, w, h, hw, hh);
            _edgeMat.SetTexture(P.SourceTex, src);
            Graphics.Blit(src, edge, _edgeMat, 0);
            Graphics.Blit(src, lines, _edgeMat, 1);

            // 3. 出力。合成は「元画像をそのままコピー → 線をブレンドで重ねる」
            _edgeMat.SetTexture(P.SourceTex, src);
            if (outputMode == LineOutputMode.LinesOnly && !isSceneView)
            {
                Graphics.Blit(src, dst, _edgeMat, 3);
            }
            else
            {
                Graphics.Blit(src, dst);
                _edgeMat.SetTexture(P.SourceTex, src);
                Graphics.Blit(src, dst, _edgeMat, 2);
            }

            // 4. 線レイヤーを透過PNGで保存
            if (exportLinePNG && Application.isPlaying && !isSceneView)
            {
                ExportPNG(src, w, h);
            }

            RenderTexture.ReleaseTemporary(g0);
            RenderTexture.ReleaseTemporary(g1);
            RenderTexture.ReleaseTemporary(depth);
            RenderTexture.ReleaseTemporary(edge);
            RenderTexture.ReleaseTemporary(lines);
        }

        void BuildGBufferCommands(RenderTexture g0, RenderTexture g1, RenderTexture depth)
        {
            _cmd.Clear();
            _cmd.SetRenderTarget(new RenderTargetIdentifier[] { g0, g1 }, depth);
            _cmd.ClearRenderTarget(true, true, Color.clear, 1f);

            Matrix4x4 view = _cam.worldToCameraMatrix;
            Matrix4x4 proj = GL.GetGPUProjectionMatrix(_cam.projectionMatrix, true);
            _cmd.SetGlobalMatrix(P.MatrixV, view);
            _cmd.SetGlobalMatrix(P.MatrixVP, proj * view);

            CollectRenderers();
            RebuildOverrideLookup();
            _matIndex.Clear();
            System.Array.Clear(_matParamPixels, 0, _matParamPixels.Length);

            bool linear = QualitySettings.activeColorSpace == ColorSpace.Linear;
            int obj = 0;
            foreach (var r in _renderers)
            {
                if (obj >= MaxObjects) break;
                int subCount = GetSubMeshCount(r);
                if (subCount == 0) continue;
                obj++;

                var mats = r.sharedMaterials;
                int n = Mathf.Min(mats.Length, subCount);
                for (int s = 0; s < n; s++)
                {
                    var m = mats[s];
                    if (m == null) continue;
                    _overrides.TryGetValue(m, out var ov);
                    MaterialInfo info = MaterialAdapter.Read(m);
                    if (!Classify(info, ov, out float cutoff)) continue;
                    int mi = GetMaterialIndex(m, info, ov, linear);

                    Texture tex = info.mainTex;
                    Vector4 st = info.mainST;
                    Vector4 colV = linear ? (Vector4)info.color.linear : (Vector4)info.color;
                    float cull = info.cull;

                    _cmd.SetGlobalTexture(P.MainTex, tex != null ? tex : Texture2D.whiteTexture);
                    _cmd.SetGlobalVector(P.MainTexST, st);
                    _cmd.SetGlobalVector(P.Color, colV);
                    _cmd.SetGlobalFloat(P.Cutoff, cutoff);
                    _cmd.SetGlobalFloat(P.CullMode, cull);
                    _cmd.SetGlobalFloat(P.ID, obj * 1024 + mi);

                    bool bump = useNormalMaps && info.bumpMap != null;
                    _cmd.SetGlobalFloat(P.UseBump, bump ? 1f : 0f);
                    _cmd.SetGlobalTexture(P.BumpMap, bump ? info.bumpMap : Texture2D.normalTexture);
                    _cmd.SetGlobalVector(P.BumpST, bump ? info.bumpST : new Vector4(1f, 1f, 0f, 0f));
                    _cmd.SetGlobalFloat(P.BumpScale, bump ? info.bumpScale : 1f);

                    bool pal = info.paletteTex != null && info.paletteCount > 0.5f;
                    _cmd.SetGlobalTexture(P.PaletteTex, pal ? info.paletteTex : Texture2D.blackTexture);
                    _cmd.SetGlobalFloat(P.PaletteCount, pal ? info.paletteCount : 0f);
                    _cmd.SetGlobalVector(P.PaletteWeights, new Vector4(info.paletteWeightL, info.paletteWeightC, 0f, 0f));
                    bool label = pal && info.labelTex != null;
                    _cmd.SetGlobalTexture(P.LabelTex, label ? info.labelTex : Texture2D.blackTexture);
                    _cmd.SetGlobalFloat(P.UseLabel, label ? 1f : 0f);
                    _cmd.SetGlobalVector(P.LabelSize, label ? info.labelSize : Vector4.one);

                    _cmd.DrawRenderer(r, _gbufferMat, s, 0);
                }
            }

            _matParamTex.SetPixels(_matParamPixels);
            _matParamTex.Apply(false);
        }

        void CollectRenderers()
        {
            _renderers.Clear();
            GeometryUtility.CalculateFrustumPlanes(_cam, _planes);
            var all = FindObjectsByType<Renderer>(FindObjectsSortMode.None);
            foreach (var r in all)
            {
                if (r == null || !r.enabled) continue;
                if (!(r is MeshRenderer) && !(r is SkinnedMeshRenderer)) continue;
                if ((targetLayers.value & (1 << r.gameObject.layer)) == 0) continue;
                if (r.shadowCastingMode == ShadowCastingMode.ShadowsOnly) continue;
                if (!GeometryUtility.TestPlanesAABB(_planes, r.bounds)) continue;
                _renderers.Add(r);
            }
        }

        void RebuildOverrideLookup()
        {
            _overrides.Clear();
            foreach (var ov in materialOverrides)
            {
                if (ov != null && ov.material != null) _overrides[ov.material] = ov;
            }
        }

        static int GetSubMeshCount(Renderer r)
        {
            if (r is SkinnedMeshRenderer smr)
            {
                return smr.sharedMesh != null ? smr.sharedMesh.subMeshCount : 0;
            }
            var mf = r.GetComponent<MeshFilter>();
            return (mf != null && mf.sharedMesh != null) ? mf.sharedMesh.subMeshCount : 0;
        }

        bool Classify(MaterialInfo info, MaterialLineOverride ov, out float cutoff)
        {
            cutoff = -1f;
            bool forceInclude = ov != null && ov.forceInclude;
            switch (info.alpha)
            {
                case AlphaKind.Cutout:
                    cutoff = info.cutoff;
                    return true;
                case AlphaKind.Transparent:
                    // 半透明 (目のハイライトや頬染めなど) は通常は線の対象外
                    // ただし MToon の TransparentWithZWrite (髪など) は対象にする
                    if (!info.transparentWithZWrite && !includeTransparentAsCutout && !forceInclude) return false;
                    cutoff = transparentCutoff;
                    return true;
                default:
                    return true;
            }
        }

        int GetMaterialIndex(Material m, MaterialInfo info, MaterialLineOverride ov, bool linear)
        {
            if (_matIndex.TryGetValue(m, out int idx)) return idx;
            idx = _matIndex.Count + 1;
            if (idx >= MaxMaterials) return MaxMaterials - 1;
            _matIndex.Add(m, idx);

            Color lc = lineColor;
            if (ov != null && ov.overrideColor) lc = ov.color;
            else if (useMaterialOutlineColor && info.hasOutlineColor) lc = info.outlineColor;
            if (linear) lc = lc.linear;
            float wm = ov != null ? ov.widthMultiplier : 1f;
            float trace = (ov != null && ov.overrideColorTrace) ? ov.colorTrace : colorTrace;
            int mask = 0;
            if (ov == null)
            {
                mask = 127;
            }
            else if (ov.drawLines)
            {
                if (ov.outline) mask |= 1;
                if (ov.innerLines) mask |= 2;
                if (ov.intersection) mask |= 4;
                if (ov.materialBoundary) mask |= 8;
                if (ov.creaseLines) mask |= 16;
                if (ov.wrinkleLines) mask |= 32;
                if (ov.textureLines) mask |= 64;
            }
            mask &= GlobalMask();

            _matParamPixels[idx] = new Color(lc.r, lc.g, lc.b, wm);
            _matParamPixels[MaxMaterials + idx] = new Color(mask, trace, 0f, 0f);
            return idx;
        }

        // 太さ 0 の線の種類は全体でオフ
        int GlobalMask()
        {
            int m = 0;
            if (outlineWidth > 0f) m |= 1;
            if (innerWidth > 0f) m |= 2;
            if (intersectionWidth > 0f) m |= 4;
            if (materialWidth > 0f) m |= 8;
            if (creaseWidth > 0f) m |= 16;
            if (wrinkleWidth > 0f) m |= 32;
            if (textureWidth > 0f) m |= 64;
            return m;
        }

        Light FindKeyLight()
        {
            if (keyLight != null) return keyLight;
            if (RenderSettings.sun != null) return RenderSettings.sun;
            if (_cachedLight != null && _cachedLight.isActiveAndEnabled && _cachedLight.type == LightType.Directional)
            {
                return _cachedLight;
            }
            _cachedLight = null;
            foreach (var l in FindObjectsByType<Light>(FindObjectsSortMode.None))
            {
                if (l.type == LightType.Directional && l.isActiveAndEnabled)
                {
                    _cachedLight = l;
                    break;
                }
            }
            return _cachedLight;
        }

        void SetupEdgeMaterial(RenderTexture g0, RenderTexture g1, RenderTexture edge, RenderTexture lines,
                               int ss, int w, int h, int hw, int hh)
        {
            var m = _edgeMat;
            m.SetTexture(P.G0, g0);
            m.SetTexture(P.G1, g1);
            m.SetTexture(P.Edge, edge);
            m.SetTexture(P.Lines, lines);
            m.SetTexture(P.MatParams, _matParamTex);
            m.SetVector(P.Size, new Vector4(hw, hh, 1f / hw, 1f / hh));
            m.SetVector(P.OutSize, new Vector4(w, h, 1f / w, 1f / h));
            m.SetVector(P.Widths, new Vector4(outlineWidth, innerWidth, intersectionWidth, materialWidth));
            m.SetVector(P.Widths2, new Vector4(creaseWidth, wrinkleWidth, textureWidth, 0f));
            m.SetFloat(P.CreaseCos, Mathf.Cos(creaseAngle * Mathf.Deg2Rad));
            m.SetFloat(P.DepthThreshold, depthThreshold);
            m.SetFloat(P.DepthSlopeBias, depthSlopeBias);

            Vector4 pix = _cam.orthographic
                ? new Vector4(0f, 2f * _cam.orthographicSize / hh, 0f, 0f)
                : new Vector4(2f * Mathf.Tan(_cam.fieldOfView * 0.5f * Mathf.Deg2Rad) / hh, 0f, 0f, 0f);
            m.SetVector(P.PixScale, pix);
            m.SetVector(P.Reduce, new Vector4(reductionNear, reductionFar, reductionMinScale, distanceReduction ? 1f : 0f));

            Light l = FindKeyLight();
            Vector3 toLight = l != null
                ? _cam.worldToCameraMatrix.MultiplyVector(-l.transform.forward).normalized
                : Vector3.forward;
            bool useLight = l != null && (!Mathf.Approximately(litSideScale, 1f) || !Mathf.Approximately(shadowSideScale, 1f));
            m.SetVector(P.LightDirV, new Vector4(toLight.x, toLight.y, toLight.z, useLight ? 1f : 0f));
            m.SetVector(P.LightScale, new Vector4(litSideScale, shadowSideScale, 0f, 0f));

            float pxScale = (h / Mathf.Max(1f, referenceHeight)) * ss;
            m.SetFloat(P.PxScale, pxScale);
            m.SetVector(P.TraceParams, new Vector4(colorTraceDarkness, colorTraceSaturation, 0f, 0f));
            m.SetFloat(P.OccTol, 0.02f);
            m.SetVector(P.Wrinkle, new Vector4(Mathf.Max(1, Mathf.RoundToInt(wrinkleScale * pxScale)), wrinkleThreshold, 0f, 0f));
            m.SetVector(P.TexEdge, new Vector4(textureEdgeThreshold, 0f, 0f, 0f));
            m.SetVector(P.Emphasis, new Vector4(emphasis, minStrength, strongDepthRatio, 0f));

            float maxW = Mathf.Max(outlineWidth, innerWidth, intersectionWidth, materialWidth, creaseWidth, wrinkleWidth, textureWidth);
            maxW *= Mathf.Lerp(1f, 1.25f, emphasis);
            float maxMul = 1f;
            foreach (var ov in materialOverrides)
            {
                if (ov != null) maxMul = Mathf.Max(maxMul, ov.widthMultiplier);
            }
            if (useLight) maxMul *= Mathf.Max(1f, litSideScale, shadowSideScale);
            int radius = Mathf.Clamp(Mathf.CeilToInt(maxW * maxMul * pxScale * 0.5f) + 1, 1, 32);
            m.SetFloat(P.Radius, radius);
            m.SetFloat(P.SS, ss);
        }

        void ExportPNG(RenderTexture src, int w, int h)
        {
            var rt = RenderTexture.GetTemporary(w, h, 0, RenderTextureFormat.ARGB32, RenderTextureReadWrite.sRGB);
            _edgeMat.SetTexture(P.SourceTex, src);
            Graphics.Blit(src, rt, _edgeMat, 3);

            var prev = RenderTexture.active;
            RenderTexture.active = rt;
            if (_exportTex == null || _exportTex.width != w || _exportTex.height != h)
            {
                DestroySafe(_exportTex);
                _exportTex = new Texture2D(w, h, TextureFormat.RGBA32, false, false) { hideFlags = HideFlags.HideAndDontSave };
            }
            _exportTex.ReadPixels(new Rect(0, 0, w, h), 0, 0, false);
            _exportTex.Apply(false);
            RenderTexture.active = prev;
            RenderTexture.ReleaseTemporary(rt);

            string dir = Path.GetFullPath(Path.Combine(Application.dataPath, "..", exportFolder));
            Directory.CreateDirectory(dir);
            File.WriteAllBytes(Path.Combine(dir, $"lines_{_exportFrame:D5}.png"), _exportTex.EncodeToPNG());
            _exportFrame++;
        }

        [ContextMenu("画面に映っているマテリアルを材質リストに追加")]
        void PopulateOverrides()
        {
            if (_cam == null) _cam = GetComponent<Camera>();
            CollectRenderers();
#if UNITY_EDITOR
            UnityEditor.Undo.RecordObject(this, "Populate Material Overrides");
#endif
            var existing = new HashSet<Material>();
            foreach (var ov in materialOverrides)
            {
                if (ov != null && ov.material != null) existing.Add(ov.material);
            }
            foreach (var r in _renderers)
            {
                foreach (var m in r.sharedMaterials)
                {
                    if (m == null || existing.Contains(m)) continue;
                    existing.Add(m);
                    materialOverrides.Add(new MaterialLineOverride { material = m, color = lineColor });
                }
            }
        }
    }
}
