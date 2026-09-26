using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace PencilLine.EditorTools
{
    [System.Serializable]
    public class PaletteSettings
    {
        public int clusterCount = 12;
        public int sampleSize = 512;
        public float weightL = 1f;
        public float weightC = 1f;
        public float hueTolerance = 30f;
        public float maxLDiff = 0.35f;
        public float maxLDiffAchromatic = 0.22f;
        public float achromaticChroma = 0.035f;
        public bool bakeLilToonLayers = true;
        public int bakeScale = 1;
        public bool useMaterialShade = true;

        public PaletteSettings Clone()
        {
            return (PaletteSettings)MemberwiseClone();
        }
    }

    public class PaletteEntry
    {
        public Vector3 lab;          // シェーダー作業空間でのOKLab (元テクスチャの色)
        public Color display;        // 表示用 (sRGB)
        public float share;          // 面積の割合
        public int mergeInto = -1;   // 統合先 (-1 = 自分がベース色)
        public Color baseColor;      // 表示用 (sRGB)
        public Color shadowColor;    // 表示用 (sRGB)
        public bool shadowFromPaint; // 書き影から影色を取ったか
        public Vector3 matShadeSum;  // MToon の影色の合計 (シェーダー空間)
        public int count;
        public bool lineFlag;        // この色の境界に線を引く (書き影の輪郭 = シワ線)
    }

    /// <summary>
    /// 1つのマテリアルからパレットを作り、フラット版マテリアルを書き出す処理 (UIなし)
    /// </summary>
    public class PaletteBuilder
    {
        public const int MaxPalette = 32;
        public const int PaletteRows = 4; // 0: 元色(OKLab) 1: ベース色 2: 1影色 3: 線フラグ
        public const string FlatShaderName = "PencilLine/Flat Palette";

        public PaletteSettings settings;
        public string outputDir;       // null なら元マテリアルと同じフォルダ
        public bool uniqueFileNames;   // 一括変換では true (同名マテリアル対策)

        public readonly List<PaletteEntry> entries = new List<PaletteEntry>();
        public readonly List<string> warnings = new List<string>();

        public Material Source { get; private set; }
        public MaterialInfo Info { get; private set; }
        public Texture2D BakedTexture { get; private set; }
        public bool HasMaterialShade { get; private set; }

        Color _tint = Color.white;

        public PaletteBuilder(PaletteSettings settings)
        {
            this.settings = settings ?? new PaletteSettings();
        }

        public static bool IsFlat(Material m)
        {
            return m != null && m.shader != null && m.shader.name == FlatShaderName;
        }

        static bool IsLinearProject => PlayerSettings.colorSpace == ColorSpace.Linear;
        public static Color ToDisplay(Color c) => IsLinearProject ? c.gamma : c;
        public static Color ToShader(Color c) => IsLinearProject ? c.linear : c;

        // ------------------------------------------------------------
        // 抽出
        // ------------------------------------------------------------
        public bool Extract(Material src, bool showProgress, out string error)
        {
            error = null;
            Source = src;
            warnings.Clear();
            entries.Clear();
            BakedTexture = null;
            Info = MaterialAdapter.Read(src);

            try
            {
                // 1. 抽出に使うテクスチャ (lilToon のレイヤーは先に焼き込む)
                Texture tex = Info.mainTex;
                Color tint = ToShader(Info.color);
                if (!IsFlat(src) && settings.bakeLilToonLayers && LilToonBaker.NeedsBake(src, null))
                {
                    Progress(showProgress, "lilToon のレイヤーを焼き込み中…", 0.05f);
                    BakedTexture = LilToonBaker.Bake(src, settings.bakeScale, warnings, DirFor(src), uniqueFileNames);
                    if (BakedTexture != null)
                    {
                        tex = BakedTexture;
                        tint = Color.white; // _Color は焼き込み済み
                    }
                }
                if (tex == null) tex = Texture2D.whiteTexture; // テクスチャなし = 単色マテリアル
                _tint = tint;

                // 2. 読み込み
                Progress(showProgress, "テクスチャを読み込み中…", 0.15f);
                ComputeReadSize(tex, settings.sampleSize, out int w, out int h);
                Color[] px = ReadTexture(tex, w, h);

                HasMaterialShade = Info.hasShade && settings.useMaterialShade;
                Color[] shadePx = null;
                Color shadeTint = ToShader(Info.shadeColor);
                if (HasMaterialShade && Info.shadeTex != null) shadePx = ReadTexture(Info.shadeTex, w, h);

                var pts = new List<Vector3>(px.Length);
                var idx = new List<int>(px.Length);
                for (int i = 0; i < px.Length; i++)
                {
                    if (px[i].a < 0.1f) continue;
                    pts.Add(ToOklab(px[i]));
                    idx.Add(i);
                }
                if (pts.Count == 0)
                {
                    error = "不透明なピクセルがありません";
                    return false;
                }

                // 3. k-means++
                Progress(showProgress, "色を分類中…", 0.4f);
                var rng = new System.Random(12345);
                var centers = InitCenters(pts, Mathf.Min(settings.clusterCount, pts.Count), rng);
                int k = centers.Count;
                for (int iter = 0; iter < 20; iter++)
                {
                    var sum = new Vector3[k];
                    var cnt = new int[k];
                    for (int i = 0; i < pts.Count; i++)
                    {
                        int b = Nearest(pts[i], centers);
                        sum[b] += pts[i];
                        cnt[b]++;
                    }
                    float moved = 0f;
                    for (int j = 0; j < k; j++)
                    {
                        if (cnt[j] == 0) continue;
                        Vector3 nc = sum[j] / cnt[j];
                        moved = Mathf.Max(moved, (nc - centers[j]).sqrMagnitude);
                        centers[j] = nc;
                    }
                    if (moved < 1e-8f) break;
                }

                // 4. 最終割り当てと、色ごとの MToon 影色の合計
                var counts = new int[k];
                var shadeSums = new Vector3[k];
                for (int i = 0; i < pts.Count; i++)
                {
                    int b = Nearest(pts[i], centers);
                    counts[b]++;
                    if (HasMaterialShade)
                    {
                        Color sc = shadePx != null ? shadePx[idx[i]] : Color.white;
                        shadeSums[b] += new Vector3(sc.r * shadeTint.r, sc.g * shadeTint.g, sc.b * shadeTint.b);
                    }
                }

                for (int j = 0; j < k; j++)
                {
                    if (counts[j] == 0) continue;
                    Color srcCol = FromOklab(centers[j]);
                    Color baseShader = new Color(srcCol.r * tint.r, srcCol.g * tint.g, srcCol.b * tint.b, 1f);
                    entries.Add(new PaletteEntry
                    {
                        lab = centers[j],
                        display = ToDisplay(Clamp01(srcCol)),
                        share = counts[j] / (float)pts.Count,
                        baseColor = ToDisplay(Clamp01(baseShader)),
                        matShadeSum = shadeSums[j],
                        count = counts[j],
                    });
                }
                entries.Sort((a, b) => b.share.CompareTo(a.share));
                if (entries.Count > MaxPalette) entries.RemoveRange(MaxPalette, entries.Count - MaxPalette);

                AutoMerge();
                return true;
            }
            finally
            {
                if (showProgress) EditorUtility.ClearProgressBar();
            }
        }

        static void Progress(bool show, string msg, float t)
        {
            if (show) EditorUtility.DisplayProgressBar("Flat Palette", msg, t);
        }

        static void ComputeReadSize(Texture tex, int maxSize, out int w, out int h)
        {
            float scale = Mathf.Min(1f, (float)maxSize / Mathf.Max(tex.width, tex.height));
            w = Mathf.Max(1, Mathf.RoundToInt(tex.width * scale));
            h = Mathf.Max(1, Mathf.RoundToInt(tex.height * scale));
        }

        static Color[] ReadTexture(Texture tex, int w, int h)
        {
            // シェーダーが見るのと同じ値 (リニアプロジェクトならリニア値) を読む
            var rt = RenderTexture.GetTemporary(w, h, 0, RenderTextureFormat.ARGBFloat, RenderTextureReadWrite.Linear);
            var prev = RenderTexture.active;
            Graphics.Blit(tex, rt);
            RenderTexture.active = rt;
            var t2 = new Texture2D(w, h, TextureFormat.RGBAFloat, false, true);
            t2.ReadPixels(new Rect(0, 0, w, h), 0, 0, false);
            t2.Apply(false);
            RenderTexture.active = prev;
            RenderTexture.ReleaseTemporary(rt);
            Color[] px = t2.GetPixels();
            Object.DestroyImmediate(t2);
            return px;
        }

        float Dist(Vector3 a, Vector3 b)
        {
            Vector3 d = a - b;
            return d.x * d.x * settings.weightL + (d.y * d.y + d.z * d.z) * settings.weightC;
        }

        int Nearest(Vector3 p, List<Vector3> centers)
        {
            int best = 0;
            float bd = float.MaxValue;
            for (int j = 0; j < centers.Count; j++)
            {
                float d = Dist(p, centers[j]);
                if (d < bd)
                {
                    bd = d;
                    best = j;
                }
            }
            return best;
        }

        List<Vector3> InitCenters(List<Vector3> pts, int k, System.Random rng)
        {
            var centers = new List<Vector3> { pts[rng.Next(pts.Count)] };
            var d2 = new float[pts.Count];
            for (int i = 0; i < d2.Length; i++) d2[i] = float.MaxValue;

            while (centers.Count < k)
            {
                Vector3 last = centers[centers.Count - 1];
                double total = 0;
                for (int i = 0; i < pts.Count; i++)
                {
                    d2[i] = Mathf.Min(d2[i], Dist(pts[i], last));
                    total += d2[i];
                }
                if (total <= 1e-12) break; // これ以上違う色がない
                double r = rng.NextDouble() * total;
                int pick = pts.Count - 1;
                for (int i = 0; i < pts.Count; i++)
                {
                    r -= d2[i];
                    if (r <= 0)
                    {
                        pick = i;
                        break;
                    }
                }
                centers.Add(pts[pick]);
            }
            return centers;
        }

        // ------------------------------------------------------------
        // 書き影の自動統合
        // 面積の小さい色から順に、同じ色相で面積の大きい色へ統合する
        // ------------------------------------------------------------
        public void AutoMerge()
        {
            int n = entries.Count;
            foreach (var e in entries)
            {
                e.mergeInto = -1;
                e.shadowFromPaint = false;
                e.lineFlag = false;
                e.shadowColor = AutoShadow(ToOklab(ToShader(e.baseColor)));
            }

            var order = new List<int>();
            for (int i = 0; i < n; i++) order.Add(i);
            order.Sort((a, b) => entries[a].share.CompareTo(entries[b].share));

            foreach (int a in order)
            {
                int bestB = -1;
                float bestScore = float.MaxValue;
                for (int b = 0; b < n; b++)
                {
                    if (b == a) continue;
                    if (entries[b].share <= entries[a].share) continue;
                    if (!Similar(entries[a].lab, entries[b].lab, out float score)) continue;
                    if (score < bestScore)
                    {
                        bestScore = score;
                        bestB = b;
                    }
                }
                entries[a].mergeInto = bestB;
            }

            for (int i = 0; i < n; i++)
            {
                int root = Root(i);
                entries[i].mergeInto = root == i ? -1 : root;
            }
            for (int r = 0; r < n; r++)
            {
                if (entries[r].mergeInto < 0) AssignShadow(r);
            }
            // ベース色より暗い色 (= 書き影・シワの影) は、境界に線を引く
            for (int i = 0; i < n; i++)
            {
                entries[i].lineFlag = entries[i].mergeInto >= 0 && entries[i].lab.x < entries[entries[i].mergeInto].lab.x - 0.02f;
            }
        }

        // MToon の影色があればそれ (グループ平均)、なければ暗い側に統合された書き影の色
        public void AssignShadow(int r)
        {
            var root = entries[r];
            if (HasMaterialShade)
            {
                Vector3 sum = Vector3.zero;
                int cnt = 0;
                for (int i = 0; i < entries.Count; i++)
                {
                    if (i != r && Root(i) != r) continue;
                    sum += entries[i].matShadeSum;
                    cnt += entries[i].count;
                }
                if (cnt > 0)
                {
                    Vector3 avg = sum / cnt;
                    root.shadowColor = ToDisplay(Clamp01(new Color(avg.x, avg.y, avg.z, 1f)));
                    root.shadowFromPaint = false;
                    return;
                }
            }

            int bestShadow = -1;
            for (int i = 0; i < entries.Count; i++)
            {
                if (i == r || Root(i) != r) continue;
                if (entries[i].lab.x >= root.lab.x) continue;
                if (bestShadow < 0 || entries[i].share > entries[bestShadow].share) bestShadow = i;
            }
            if (bestShadow >= 0)
            {
                root.shadowColor = Tinted(entries[bestShadow]);
                root.shadowFromPaint = true;
            }
        }

        bool Similar(Vector3 a, Vector3 b, out float score)
        {
            float dL = Mathf.Abs(a.x - b.x);
            float ca = Mathf.Sqrt(a.y * a.y + a.z * a.z);
            float cb = Mathf.Sqrt(b.y * b.y + b.z * b.z);
            bool achA = ca < settings.achromaticChroma;
            bool achB = cb < settings.achromaticChroma;
            score = dL;

            if (achA && achB) return dL <= settings.maxLDiffAchromatic;
            if (achA != achB) return false;

            float ha = Mathf.Atan2(a.z, a.y) * Mathf.Rad2Deg;
            float hb = Mathf.Atan2(b.z, b.y) * Mathf.Rad2Deg;
            float dh = Mathf.Abs(Mathf.DeltaAngle(ha, hb));
            if (dh > settings.hueTolerance) return false;
            float ratio = Mathf.Min(ca, cb) / Mathf.Max(ca, cb);
            if (ratio < 0.35f) return false;
            score = dL + dh / 180f;
            return dL <= settings.maxLDiff;
        }

        public int Root(int i)
        {
            int cur = i;
            for (int step = 0; step <= entries.Count; step++)
            {
                int next = entries[cur].mergeInto;
                if (next < 0 || next >= entries.Count) return cur;
                cur = next;
            }
            entries[i].mergeInto = -1; // 循環していたら解除
            return i;
        }

        public void SetMerge(int i, int target)
        {
            if (target == i) target = -1;
            entries[i].mergeInto = target;
            if (target < 0) return;

            int root = Root(i);
            if (root == i)
            {
                entries[i].mergeInto = -1;
                return;
            }
            var r = entries[root];
            if (HasMaterialShade) AssignShadow(root);
            else if (!r.shadowFromPaint && entries[i].lab.x < r.lab.x)
            {
                r.shadowColor = Tinted(entries[i]);
                r.shadowFromPaint = true;
            }
        }

        Color Tinted(PaletteEntry e)
        {
            Color c = FromOklab(e.lab);
            return ToDisplay(Clamp01(new Color(c.r * _tint.r, c.g * _tint.g, c.b * _tint.b, 1f)));
        }

        static Color AutoShadow(Vector3 lab)
        {
            // 少し暗く・少し鮮やかに・少し青紫寄りに
            var s = new Vector3(lab.x * 0.8f, lab.y * 1.1f - 0.004f, lab.z * 1.1f - 0.02f);
            return ToDisplay(Clamp01(FromOklab(s)));
        }

        // ------------------------------------------------------------
        // 書き出し
        // ------------------------------------------------------------
        public void FillPalette(Texture2D tex)
        {
            var px = new Color[MaxPalette * PaletteRows];
            int n = Mathf.Min(entries.Count, MaxPalette);
            for (int i = 0; i < n; i++)
            {
                var e = entries[i];
                var r = entries[Root(i)];
                px[i] = new Color(e.lab.x, e.lab.y, e.lab.z, 1f);
                px[MaxPalette + i] = ToShader(r.baseColor);
                px[MaxPalette * 2 + i] = ToShader(r.shadowColor);
                px[MaxPalette * 3 + i] = new Color(e.lineFlag ? 1f : 0f, 0f, 0f, 0f);
            }
            if (tex.width != MaxPalette || tex.height != PaletteRows) tex.Reinitialize(MaxPalette, PaletteRows);
            tex.SetPixels(px);
            tex.Apply(false);
        }

        /// <summary>フラット版を作成 (flat が null なら新規) してパレットを保存する</summary>
        public Material CreateOrUpdateFlat(Material flat, bool saveAssets = true)
        {
            if (flat == null) flat = IsFlat(Source) ? Source : CreateFlatMaterial(Source, DirFor(Source));

            string path = PalettePath(flat);
            var tex = AssetDatabase.LoadAssetAtPath<Texture2D>(path);
            if (tex == null)
            {
                tex = new Texture2D(MaxPalette, PaletteRows, TextureFormat.RGBAFloat, false, true)
                {
                    filterMode = FilterMode.Point,
                    wrapMode = TextureWrapMode.Clamp,
                    name = flat.name + "_Palette",
                };
                FillPalette(tex);
                AssetDatabase.CreateAsset(tex, path);
            }
            else
            {
                FillPalette(tex);
                EditorUtility.SetDirty(tex);
            }

            Undo.RecordObject(flat, "Apply Palette");
            if (BakedTexture != null)
            {
                flat.SetTexture("_MainTex", BakedTexture);
                flat.SetColor("_Color", Color.white);
            }
            flat.SetTexture("_PaletteTex", tex);
            flat.SetFloat("_PaletteCount", Mathf.Min(entries.Count, MaxPalette));
            flat.SetFloat("_WeightL", settings.weightL);
            flat.SetFloat("_WeightC", settings.weightC);
            flat.SetFloat("_UsePalette", 1f);
            flat.EnableKeyword("_PALETTE_ON");
            EditorUtility.SetDirty(flat);
            if (saveAssets) AssetDatabase.SaveAssets();
            return flat;
        }

        /// <summary>作成済みのフラット版にパレットだけ即反映 (保存はしない)</summary>
        public bool LiveWrite(Material flat)
        {
            if (flat == null) return false;
            var tex = flat.GetTexture("_PaletteTex") as Texture2D;
            if (tex == null || tex.width != MaxPalette) return false;
            FillPalette(tex);
            EditorUtility.SetDirty(tex);
            flat.SetFloat("_PaletteCount", Mathf.Min(entries.Count, MaxPalette));
            return true;
        }

        static string PalettePath(Material m)
        {
            string p = AssetDatabase.GetAssetPath(m);
            string dir = Path.GetDirectoryName(p).Replace('\\', '/');
            return $"{dir}/{m.name}_Palette.asset";
        }

        public string DirFor(Material m)
        {
            if (!string.IsNullOrEmpty(outputDir))
            {
                EnsureFolder(outputDir);
                return outputDir.TrimEnd('/');
            }
            string p = AssetDatabase.GetAssetPath(m);
            return (!string.IsNullOrEmpty(p) && p.StartsWith("Assets"))
                ? Path.GetDirectoryName(p).Replace('\\', '/')
                : "Assets";
        }

        public static void EnsureFolder(string path)
        {
            path = path.TrimEnd('/');
            if (AssetDatabase.IsValidFolder(path)) return;
            string parent = Path.GetDirectoryName(path).Replace('\\', '/');
            string name = Path.GetFileName(path);
            EnsureFolder(parent);
            AssetDatabase.CreateFolder(parent, name);
        }

        public static Material CreateFlatMaterial(Material src, string dir)
        {
            MaterialInfo info = MaterialAdapter.Read(src);
            var m = new Material(Shader.Find(FlatShaderName));
            m.SetTexture("_MainTex", info.mainTex);
            m.SetTextureScale("_MainTex", new Vector2(info.mainST.x, info.mainST.y));
            m.SetTextureOffset("_MainTex", new Vector2(info.mainST.z, info.mainST.w));
            m.SetColor("_Color", info.color);
            m.SetFloat("_Cull", info.cull);

            if (info.alpha != AlphaKind.Opaque)
            {
                m.SetFloat("_AlphaTest", 1f);
                m.EnableKeyword("_ALPHATEST_ON");
                m.SetFloat("_Cutoff", info.alpha == AlphaKind.Cutout ? info.cutoff : 0.5f);
                m.renderQueue = 2450;
            }

            // MToon は影の境界位置を引き継いでセル影モードにする
            if (info.hasShadeThreshold)
            {
                m.SetFloat("_ShadeMode", 1f);
                m.SetFloat("_ShadeThreshold", Mathf.Clamp(info.shadeThreshold, -1f, 1f));
            }

            string path = AssetDatabase.GenerateUniqueAssetPath($"{dir}/{src.name}_Flat.mat");
            AssetDatabase.CreateAsset(m, path);
            return m;
        }

        // ------------------------------------------------------------
        // OKLab (シェーダーと同じ式)
        // ------------------------------------------------------------
        static float Cbrt(float x) => Mathf.Pow(Mathf.Max(x, 0f), 1f / 3f);

        public static Vector3 ToOklab(Color c)
        {
            float r = Mathf.Max(c.r, 0f), g = Mathf.Max(c.g, 0f), b = Mathf.Max(c.b, 0f);
            float l = Cbrt(0.4122214708f * r + 0.5363325363f * g + 0.0514459929f * b);
            float m = Cbrt(0.2119034982f * r + 0.6806995451f * g + 0.1073969566f * b);
            float s = Cbrt(0.0883024619f * r + 0.2817188376f * g + 0.6299787005f * b);
            return new Vector3(
                0.2104542553f * l + 0.7936177850f * m - 0.0040720468f * s,
                1.9779984951f * l - 2.4285922050f * m + 0.4505937099f * s,
                0.0259040371f * l + 0.7827717662f * m - 0.8086757660f * s);
        }

        public static Color FromOklab(Vector3 lab)
        {
            float l_ = lab.x + 0.3963377774f * lab.y + 0.2158037573f * lab.z;
            float m_ = lab.x - 0.1055613458f * lab.y - 0.0638541728f * lab.z;
            float s_ = lab.x - 0.0894841775f * lab.y - 1.2914855480f * lab.z;
            float l = l_ * l_ * l_, m = m_ * m_ * m_, s = s_ * s_ * s_;
            return new Color(
                4.0767416621f * l - 3.3077115913f * m + 0.2309699292f * s,
                -1.2684380046f * l + 2.6097574011f * m - 0.3413193965f * s,
                -0.0041960863f * l - 0.7034186147f * m + 1.7076147010f * s,
                1f);
        }

        public static Color Clamp01(Color c) => new Color(Mathf.Clamp01(c.r), Mathf.Clamp01(c.g), Mathf.Clamp01(c.b), 1f);
    }
}
