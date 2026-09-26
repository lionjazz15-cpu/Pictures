using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace PencilLine.EditorTools
{
    /// <summary>
    /// lilToon のメイン色調補正とメイン2nd/3rd (デカール) を、
    /// メインテクスチャと同じ UV 空間の PNG に焼き込む
    /// </summary>
    public static class LilToonBaker
    {
        const string BakeShaderName = "Hidden/PencilLine/LilToonBake";

        public static bool IsLilToon(Material m)
        {
            return m != null && (m.HasProperty("_UseMain2ndTex") || m.HasProperty("_MainTexHSVG"));
        }

        /// <summary>焼き込みが必要な設定があるか (理由を reasons に入れる)</summary>
        public static bool NeedsBake(Material m, List<string> reasons)
        {
            if (!IsLilToon(m)) return false;
            bool need = false;
            if (IsLayerOn(m, "2nd")) { need = true; reasons?.Add("メイン2nd"); }
            if (IsLayerOn(m, "3rd")) { need = true; reasons?.Add("メイン3rd"); }
            if (HasToneCorrection(m)) { need = true; reasons?.Add("色調補正"); }
            return need;
        }

        static bool IsLayerOn(Material m, string n)
        {
            return F(m, "_UseMain" + n + "Tex", 0f) > 0.5f;
        }

        static bool HasToneCorrection(Material m)
        {
            if (!m.HasProperty("_MainTexHSVG")) return false;
            Vector4 v = m.GetVector("_MainTexHSVG");
            return Mathf.Abs(v.x) > 1e-4f || Mathf.Abs(v.y - 1f) > 1e-4f || Mathf.Abs(v.z - 1f) > 1e-4f || Mathf.Abs(v.w - 1f) > 1e-4f;
        }

        /// <summary>
        /// 焼き込んで PNG アセットとして保存し、そのテクスチャを返す。
        /// 結果には _Color も掛かっているので、使う側の色は白にすること。
        /// </summary>
        public static Texture2D Bake(Material src, int resolutionScale, List<string> warnings, string outputDir = null, bool uniqueName = false)
        {
            var shader = Shader.Find(BakeShaderName);
            if (shader == null)
            {
                warnings.Add("焼き込み用シェーダーが見つかりません");
                return null;
            }

            bool linear = PlayerSettings.colorSpace == ColorSpace.Linear;
            var mat = new Material(shader) { hideFlags = HideFlags.HideAndDontSave };
            try
            {
                // メイン
                Texture main = src.HasProperty("_MainTex") ? src.GetTexture("_MainTex") : null;
                Vector4 mainST = GetST(src, "_MainTex");
                mat.SetTexture("_PLB_MainTex", main != null ? main : Texture2D.whiteTexture);
                mat.SetVector("_PLB_MainST", mainST);
                Color col = src.HasProperty("_Color") ? src.GetColor("_Color") : Color.white;
                mat.SetVector("_PLB_Color", linear ? (Vector4)col.linear : (Vector4)col);

                bool tone = HasToneCorrection(src);
                mat.SetFloat("_PLB_UseAdjust", tone ? 1f : 0f);
                if (tone)
                {
                    mat.SetVector("_PLB_HSVG", src.GetVector("_MainTexHSVG"));
                    Texture adj = src.HasProperty("_MainColorAdjustMask") ? src.GetTexture("_MainColorAdjustMask") : null;
                    mat.SetTexture("_PLB_AdjustMask", adj != null ? adj : Texture2D.whiteTexture);
                }
                if (F(src, "_MainGradationStrength", 0f) > 1e-4f)
                {
                    warnings.Add("グラデーションマップは焼き込みに未対応です (色調補正のみ反映)");
                }

                // メイン2nd / 3rd
                SetupLayer(src, mat, "2nd", "_PLB_L1", linear, warnings);
                SetupLayer(src, mat, "3rd", "_PLB_L2", linear, warnings);

                // 解像度: メインテクスチャと同じ × 倍率
                int w = main != null ? main.width : 1024;
                int h = main != null ? main.height : 1024;
                int scale = Mathf.Clamp(resolutionScale, 1, 4);
                w = Mathf.Min(w * scale, 8192);
                h = Mathf.Min(h * scale, 8192);

                var rt = RenderTexture.GetTemporary(w, h, 0, RenderTextureFormat.ARGB32, RenderTextureReadWrite.sRGB);
                var prev = RenderTexture.active;
                Graphics.Blit(main != null ? main : Texture2D.whiteTexture, rt, mat, 0);
                RenderTexture.active = rt;
                var tex = new Texture2D(w, h, TextureFormat.RGBA32, false, false);
                tex.ReadPixels(new Rect(0, 0, w, h), 0, 0, false);
                tex.Apply(false);
                RenderTexture.active = prev;
                RenderTexture.ReleaseTemporary(rt);

                byte[] png = tex.EncodeToPNG();
                Object.DestroyImmediate(tex);

                string path = BakedPath(src, outputDir);
                if (uniqueName) path = AssetDatabase.GenerateUniqueAssetPath(path);
                File.WriteAllBytes(Path.GetFullPath(path), png);
                AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceUpdate);

                if (AssetImporter.GetAtPath(path) is TextureImporter imp)
                {
                    imp.textureType = TextureImporterType.Default;
                    imp.sRGBTexture = true;
                    imp.alphaSource = TextureImporterAlphaSource.FromInput;
                    imp.alphaIsTransparency = true;
                    imp.mipmapEnabled = true;
                    // 圧縮ノイズでパレットの判定がぶれないよう無圧縮
                    imp.textureCompression = TextureImporterCompression.Uncompressed;
                    imp.maxTextureSize = Mathf.Clamp(Mathf.NextPowerOfTwo(Mathf.Max(w, h)), 32, 8192);
                    if (main != null)
                    {
                        imp.wrapMode = main.wrapMode;
                        imp.filterMode = main.filterMode;
                    }
                    imp.SaveAndReimport();
                }
                return AssetDatabase.LoadAssetAtPath<Texture2D>(path);
            }
            finally
            {
                Object.DestroyImmediate(mat);
            }
        }

        static void SetupLayer(Material src, Material mat, string n, string prefix, bool linear, List<string> warnings)
        {
            string label = "メイン" + n;
            if (!IsLayerOn(src, n))
            {
                mat.SetVector(prefix + "P0", Vector4.zero);
                mat.SetTexture(prefix + "Tex", Texture2D.whiteTexture);
                mat.SetTexture(prefix + "Mask", Texture2D.whiteTexture);
                return;
            }

            int uvMode = Mathf.RoundToInt(F(src, "_Main" + n + "Tex_UVMode", 0f));
            if (uvMode == 4)
            {
                warnings.Add(label + ": MatCap モードは焼き込めないため除外しました");
                mat.SetVector(prefix + "P0", Vector4.zero);
                mat.SetTexture(prefix + "Tex", Texture2D.whiteTexture);
                mat.SetTexture(prefix + "Mask", Texture2D.whiteTexture);
                return;
            }
            if (uvMode != 0)
            {
                warnings.Add(label + ": UV" + uvMode + " を使っていますが、UV0 として焼き込みました");
            }

            string texName = "_Main" + n + "Tex";
            Texture tex = src.HasProperty(texName) ? src.GetTexture(texName) : null;
            mat.SetTexture(prefix + "Tex", tex != null ? tex : Texture2D.whiteTexture);
            mat.SetVector(prefix + "ST", GetST(src, texName));

            Color c = src.HasProperty("_Color" + n) ? src.GetColor("_Color" + n) : Color.white;
            mat.SetVector(prefix + "Color", linear ? (Vector4)c.linear : (Vector4)c);

            string maskName = "_Main" + n + "BlendMask";
            Texture mask = src.HasProperty(maskName) ? src.GetTexture(maskName) : null;
            mat.SetTexture(prefix + "Mask", mask != null ? mask : Texture2D.whiteTexture);

            string p = "_Main" + n + "Tex";
            mat.SetVector(prefix + "P0", new Vector4(
                1f,
                F(src, p + "BlendMode", 0f),
                F(src, p + "AlphaMode", 0f),
                F(src, p + "IsDecal", 0f)));
            mat.SetVector(prefix + "P1", new Vector4(
                F(src, p + "Angle", 0f),
                F(src, p + "ShouldCopy", 0f),
                F(src, p + "ShouldFlipCopy", 0f),
                F(src, p + "IsMSDF", 0f)));

            if (F(src, p + "IsLeftOnly", 0f) > 0.5f || F(src, p + "IsRightOnly", 0f) > 0.5f || F(src, p + "ShouldFlipMirror", 0f) > 0.5f)
            {
                warnings.Add(label + ": 左右別の設定 (Left/Right Only, Flip Mirror) は焼き込めないため、両側に同じ模様が入ります");
            }
            if (src.HasProperty(p + "DecalAnimation"))
            {
                Vector4 anim = src.GetVector(p + "DecalAnimation");
                if (anim.x * anim.y > 1.5f) warnings.Add(label + ": デカールアニメーションは最初のコマで焼き込みました");
            }
            if (src.HasProperty(p + "_ScrollRotate"))
            {
                Vector4 sr = src.GetVector(p + "_ScrollRotate");
                if (sr.sqrMagnitude > 1e-8f) warnings.Add(label + ": スクロール/回転アニメーションは止まった状態で焼き込みました");
            }
            if (src.HasProperty("_Main" + n + "DissolveParams"))
            {
                Vector4 d = src.GetVector("_Main" + n + "DissolveParams");
                if (d.x > 0.5f) warnings.Add(label + ": ディゾルブは焼き込みに未対応です");
            }
        }

        static Vector4 GetST(Material m, string name)
        {
            if (!m.HasProperty(name)) return new Vector4(1f, 1f, 0f, 0f);
            Vector2 sc = m.GetTextureScale(name);
            Vector2 of = m.GetTextureOffset(name);
            if (Mathf.Abs(sc.x) < 1e-6f) sc.x = 1f;
            if (Mathf.Abs(sc.y) < 1e-6f) sc.y = 1f;
            return new Vector4(sc.x, sc.y, of.x, of.y);
        }

        static float F(Material m, string name, float fallback)
        {
            return m.HasProperty(name) ? m.GetFloat(name) : fallback;
        }

        static string BakedPath(Material m, string outputDir)
        {
            string dir = outputDir;
            if (string.IsNullOrEmpty(dir))
            {
                string p = AssetDatabase.GetAssetPath(m);
                dir = (!string.IsNullOrEmpty(p) && p.StartsWith("Assets"))
                    ? Path.GetDirectoryName(p).Replace('\\', '/')
                    : "Assets";
            }
            return $"{dir}/{m.name}_Baked.png";
        }
    }
}
