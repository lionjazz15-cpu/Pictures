using UnityEngine;

namespace PencilLine
{
    public enum ShaderFamily
    {
        Generic,  // Standard / lilToon / Poiyomi など (_Cull と renderQueue で判定)
        MToon0,   // VRM 0.x "VRM/MToon"
        MToon10,  // VRM 1.0 "VRM10/MToon10"
    }

    public enum AlphaKind
    {
        Opaque,
        Cutout,
        Transparent,
    }

    /// <summary>シェーダーの違いを吸収して読み出したマテリアル情報</summary>
    public struct MaterialInfo
    {
        public ShaderFamily family;

        public Texture mainTex;
        public Vector4 mainST;
        public Color color;                 // sRGB (マテリアルに保存されている値)

        public bool hasShade;               // 影色の指定を持っているか (MToon)
        public Texture shadeTex;
        public Color shadeColor;            // sRGB

        public float cull;                  // 0:Off 1:Front 2:Back
        public AlphaKind alpha;
        public float cutoff;
        public bool transparentWithZWrite;

        public bool hasOutlineColor;
        public Color outlineColor;          // sRGB

        public bool hasShadeThreshold;      // セル影の境界 (N・L 基準) に換算した値を持っているか
        public float shadeThreshold;

        public Texture bumpMap;             // ノーマルマップ (シワ線の検出に使う)
        public Vector4 bumpST;
        public float bumpScale;

        public Texture paletteTex;          // Flat Palette のパレット (パレット境界の線に使う)
        public float paletteCount;
        public float paletteWeightL;
        public float paletteWeightC;
        public Texture labelTex;            // 境界を整えたパレット番号マップ (なければ null)
        public Vector4 labelSize;           // x,y: ミップ0の大きさ z: ミップの数 w: リピートなら1
    }

    public static class MaterialAdapter
    {
        public static ShaderFamily Detect(Material m)
        {
            if (m == null) return ShaderFamily.Generic;
            if (m.HasProperty("_AlphaMode") && m.HasProperty("_ShadeTex")) return ShaderFamily.MToon10;
            if (m.HasProperty("_BlendMode") && m.HasProperty("_ShadeTexture")) return ShaderFamily.MToon0;
            return ShaderFamily.Generic;
        }

        public static MaterialInfo Read(Material m)
        {
            var info = new MaterialInfo
            {
                family = Detect(m),
                mainST = new Vector4(1f, 1f, 0f, 0f),
                color = Color.white,
                shadeColor = Color.white,
                cull = 2f,
                alpha = AlphaKind.Opaque,
                cutoff = 0.5f,
            };

            if (m.HasProperty("_MainTex"))
            {
                info.mainTex = m.GetTexture("_MainTex");
                Vector2 sc = m.GetTextureScale("_MainTex");
                Vector2 of = m.GetTextureOffset("_MainTex");
                info.mainST = new Vector4(sc.x, sc.y, of.x, of.y);
            }
            if (m.HasProperty("_Color")) info.color = m.GetColor("_Color");
            if (m.HasProperty("_Cutoff")) info.cutoff = m.GetFloat("_Cutoff");
            if (m.HasProperty("_OutlineColor"))
            {
                info.hasOutlineColor = true;
                info.outlineColor = m.GetColor("_OutlineColor");
            }

            if (m.HasProperty("_BumpMap"))
            {
                bool use = !m.HasProperty("_UseBumpMap") || m.GetFloat("_UseBumpMap") > 0.5f;
                Texture bt = m.GetTexture("_BumpMap");
                if (use && bt != null)
                {
                    info.bumpMap = bt;
                    Vector2 bsc = m.GetTextureScale("_BumpMap");
                    Vector2 bof = m.GetTextureOffset("_BumpMap");
                    info.bumpST = new Vector4(bsc.x, bsc.y, bof.x, bof.y);
                    info.bumpScale = GetFloat(m, "_BumpScale", 1f);
                }
            }
            if (m.HasProperty("_PaletteTex") && m.IsKeywordEnabled("_PALETTE_ON"))
            {
                info.paletteTex = m.GetTexture("_PaletteTex");
                info.paletteCount = info.paletteTex != null ? GetFloat(m, "_PaletteCount", 0f) : 0f;
                info.paletteWeightL = GetFloat(m, "_WeightL", 1f);
                info.paletteWeightC = GetFloat(m, "_WeightC", 1f);
                if (m.HasProperty("_LabelTex") && m.IsKeywordEnabled("_LABELMAP_ON"))
                {
                    info.labelTex = m.GetTexture("_LabelTex");
                    info.labelSize = m.HasProperty("_LabelSize") ? m.GetVector("_LabelSize") : Vector4.one;
                }
            }

            switch (info.family)
            {
                case ShaderFamily.MToon0:
                {
                    info.hasShade = true;
                    info.shadeTex = m.GetTexture("_ShadeTexture");
                    info.shadeColor = GetColor(m, "_ShadeColor", Color.white);
                    info.cull = GetFloat(m, "_CullMode", 2f);
                    int blend = Mathf.RoundToInt(GetFloat(m, "_BlendMode", 0f));
                    // 0:Opaque 1:Cutout 2:Transparent 3:TransparentWithZWrite
                    info.alpha = blend == 0 ? AlphaKind.Opaque
                               : blend == 1 ? AlphaKind.Cutout
                               : AlphaKind.Transparent;
                    info.transparentWithZWrite = blend == 3;
                    // MToon 0.x は smoothstep(shift, shift + (1 - toony), N・L) なので中央を境界にする
                    float shift = GetFloat(m, "_ShadeShift", 0f);
                    float toony = GetFloat(m, "_ShadeToony", 0.9f);
                    info.hasShadeThreshold = true;
                    info.shadeThreshold = shift + (1f - toony) * 0.5f;
                    break;
                }
                case ShaderFamily.MToon10:
                {
                    info.hasShade = true;
                    info.shadeTex = m.GetTexture("_ShadeTex");
                    info.shadeColor = GetColor(m, "_ShadeColor", Color.white);
                    info.cull = GetFloat(m, "_DoubleSided", 0f) > 0.5f ? 0f : 2f;
                    int alphaMode = Mathf.RoundToInt(GetFloat(m, "_AlphaMode", 0f));
                    // 0:Opaque 1:Cutout 2:Blend
                    info.alpha = alphaMode == 0 ? AlphaKind.Opaque
                               : alphaMode == 1 ? AlphaKind.Cutout
                               : AlphaKind.Transparent;
                    info.transparentWithZWrite = alphaMode == 2 && GetFloat(m, "_TransparentWithZWrite", 0f) > 0.5f;
                    // MToon 1.0 は N・L + shadingShift が 0 の所が境界
                    info.hasShadeThreshold = true;
                    info.shadeThreshold = -GetFloat(m, "_ShadingShiftFactor", 0f);
                    break;
                }
                default:
                {
                    info.cull = GetFloat(m, "_Cull", 2f);
                    int q = m.renderQueue;
                    if (q >= 3000) info.alpha = AlphaKind.Transparent;
                    else if (q >= 2450 || m.IsKeywordEnabled("_ALPHATEST_ON")) info.alpha = AlphaKind.Cutout;
                    else info.alpha = AlphaKind.Opaque;
                    break;
                }
            }
            return info;
        }

        static float GetFloat(Material m, string name, float fallback)
        {
            return m.HasProperty(name) ? m.GetFloat(name) : fallback;
        }

        static Color GetColor(Material m, string name, Color fallback)
        {
            return m.HasProperty(name) ? m.GetColor(name) : fallback;
        }
    }
}
