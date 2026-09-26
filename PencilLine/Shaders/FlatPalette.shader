// 単色 (フラット) シェーダー
// パレットモードでは、テクスチャの色を「色の数」だけに丸めます。
// 模様は別の色として残り、グラデーションや書き影はベース色に統合されます。
// パレットは Tools > PencilLine > Flat Palette Maker で作成します。
Shader "PencilLine/Flat Palette"
{
    Properties
    {
        _MainTex ("Texture", 2D) = "white" {}
        _Color ("Color", Color) = (1, 1, 1, 1)

        [Header(Palette)]
        [Toggle(_PALETTE_ON)] _UsePalette ("Use Palette", Float) = 0
        [NoScaleOffset] _PaletteTex ("Palette Texture (generated)", 2D) = "black" {}
        _PaletteCount ("Palette Count", Float) = 0
        _WeightL ("Lightness Weight", Range(0, 4)) = 1
        _WeightC ("Chroma Weight", Range(0, 4)) = 1
        [Toggle(_LABELMAP_ON)] _UseLabelMap ("Use Label Map (境界を整えた番号マップ)", Float) = 0
        [NoScaleOffset] _LabelTex ("Label Map (generated)", 2D) = "black" {}
        [HideInInspector] _LabelSize ("Label Map Size", Vector) = (1, 1, 1, 1)
        _Flatness ("Flatness (0 = 元の塗りの濃淡を残す)", Range(0, 1)) = 1

        [Header(Shading)]
        [Enum(Flat, 0, Cel, 1)] _ShadeMode ("Shade Mode", Float) = 0
        _ShadeThreshold ("Cel Threshold", Range(-1, 1)) = 0
        _ShadowTint ("Shadow Tint (no palette)", Color) = (0.72, 0.68, 0.82, 1)
        [ToggleUI] _ReceiveShadows ("Receive Shadows", Float) = 0
        _LightColorInfluence ("Light Color Influence", Range(0, 1)) = 0

        [Header(Alpha and Culling)]
        [Toggle(_ALPHATEST_ON)] _AlphaTest ("Alpha Test (Cutout)", Float) = 0
        _Cutoff ("Alpha Cutoff", Range(0, 1)) = 0.5
        [Enum(UnityEngine.Rendering.CullMode)] _Cull ("Cull", Float) = 2
    }

    CGINCLUDE
    #include "UnityCG.cginc"
    sampler2D _MainTex;
    float4 _MainTex_ST;
    float4 _MainTex_TexelSize;
    float4 _Color;
    float _Cutoff;
    ENDCG

    SubShader
    {
        Tags { "RenderType" = "Opaque" "Queue" = "Geometry" }

        Pass
        {
            Tags { "LightMode" = "ForwardBase" }
            Cull [_Cull]

            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #pragma target 4.5
            #pragma multi_compile_fwdbase
            #pragma shader_feature_local _PALETTE_ON
            #pragma shader_feature_local _LABELMAP_ON
            #pragma shader_feature_local _ALPHATEST_ON
            #include "Lighting.cginc"
            #include "AutoLight.cginc"
            #include "PencilLineCommon.cginc"

            Texture2D<float4> _PaletteTex; // 32 x 4 : 元色(OKLab) / ベース色 / 1影色 / 線フラグ
            Texture2D<float> _LabelTex;    // 境界を整えたパレット番号マップ (ミップアトラス)
            float4 _LabelSize;             // x,y: ミップ0の大きさ z: ミップの数 w: リピートなら1
            float _PaletteCount;
            float _Flatness;
            float _WeightL;
            float _WeightC;
            float _ShadeMode;
            float _ShadeThreshold;
            float4 _ShadowTint;
            float _ReceiveShadows;
            float _LightColorInfluence;

            struct appdata
            {
                float4 vertex : POSITION;
                float3 normal : NORMAL;
                float2 uv : TEXCOORD0;
            };

            struct v2f
            {
                float4 pos : SV_POSITION;
                float2 uv : TEXCOORD0;
                float3 worldNormal : TEXCOORD1;
                float3 worldPos : TEXCOORD2;
                SHADOW_COORDS(3)
            };

            v2f vert(appdata v)
            {
                v2f o;
                o.pos = UnityObjectToClipPos(v.vertex);
                o.uv = TRANSFORM_TEX(v.uv, _MainTex);
                o.worldNormal = UnityObjectToWorldNormal(v.normal);
                o.worldPos = mul(unity_ObjectToWorld, v.vertex).xyz;
                TRANSFER_SHADOW(o);
                return o;
            }

            // 一番近いパレット色を探して、その出力色 (ベース / 1影) と元色 (OKLab) を返す
            void Snap(float3 lin, out float3 baseCol, out float3 shadeCol, out float3 centerLab)
            {
                float3 lab = PL_LinearToOklab(lin);
                int count = (int)_PaletteCount;
                float best = 1e20;
                int bi = 0;
                [loop]
                for (int k = 0; k < count; k++)
                {
                    float3 d = lab - _PaletteTex.Load(int3(k, 0, 0)).xyz;
                    float dist = d.x * d.x * _WeightL + (d.y * d.y + d.z * d.z) * _WeightC;
                    if (dist < best)
                    {
                        best = dist;
                        bi = k;
                    }
                }
                centerLab = _PaletteTex.Load(int3(bi, 0, 0)).xyz;
                baseCol = _PaletteTex.Load(int3(bi, 1, 0)).rgb;
                shadeCol = _PaletteTex.Load(int3(bi, 2, 0)).rgb;
            }

            // 周囲4テクセルをそれぞれパレット化してから補間する
            // (色の境目に中間色のフチが出ないようにするため)
            // (番号マップがない古いフラット版用)
            float4 SampleFlat(float2 uv, out float3 shadeCol, out float3 centerLab)
            {
                float2 texSize = _MainTex_TexelSize.zw;
                float2 dx = ddx(uv * texSize);
                float2 dy = ddy(uv * texSize);
                float lod = floor(max(0.0, 0.5 * log2(max(dot(dx, dx), dot(dy, dy)))));
                float2 mipSize = max(floor(texSize / exp2(lod)), 1.0);
                float2 st = uv * mipSize - 0.5;
                float2 f = frac(st);
                float2 b = floor(st);

                float4 acc = float4(0, 0, 0, 0);
                float3 sacc = float3(0, 0, 0);
                float3 cacc = float3(0, 0, 0);
                [unroll]
                for (int k = 0; k < 4; k++)
                {
                    float2 o = float2(k & 1, k >> 1);
                    float2 cuv = (b + o + 0.5) / mipSize;
                    float4 t = tex2Dlod(_MainTex, float4(cuv, 0, lod));
                    float3 bc;
                    float3 sc;
                    float3 cl;
                    Snap(t.rgb, bc, sc, cl);
                    float2 wv = lerp(1.0 - f, f, o);
                    float wgt = wv.x * wv.y;
                    acc += float4(bc, t.a) * wgt;
                    sacc += sc * wgt;
                    cacc += cl * wgt;
                }
                shadeCol = sacc;
                centerLab = cacc;
                return acc;
            }

            // 境界を整えた番号マップから色を決める (境界だけ 2 色をなめらかに補間)
            float3 SampleLabelFlat(float2 uv, out float3 shadeCol, out float3 centerLab)
            {
                PL_Label lb = PL_SampleLabel(_LabelTex, _LabelSize, uv, ddx(uv), ddy(uv));
                int last = max((int)_PaletteCount, 1) - 1;
                int a = min(lb.best, last);
                int b = min(lb.second, last);
                float t = lb.blend;
                centerLab = lerp(_PaletteTex.Load(int3(b, 0, 0)).xyz, _PaletteTex.Load(int3(a, 0, 0)).xyz, t);
                shadeCol = lerp(_PaletteTex.Load(int3(b, 2, 0)).rgb, _PaletteTex.Load(int3(a, 2, 0)).rgb, t);
                return lerp(_PaletteTex.Load(int3(b, 1, 0)).rgb, _PaletteTex.Load(int3(a, 1, 0)).rgb, t);
            }

            // フラット度 < 1 なら、元の塗りの濃淡 (パレットの元色からのずれ) を OKLab で足し戻す
            float3 AddPaintDetail(float3 col, float3 detailLab)
            {
                return max(PL_OklabToLinear(PL_LinearToOklab(col) + detailLab), 0.0);
            }

            float4 frag(v2f i, float facing : VFACE) : SV_Target
            {
                float3 baseCol;
                float3 shadeCol;
                float alpha;

            #if defined(_PALETTE_ON)
                float3 centerLab;
                float4 raw = tex2D(_MainTex, i.uv);
              #if defined(_LABELMAP_ON)
                baseCol = SampleLabelFlat(i.uv, shadeCol, centerLab);
                alpha = raw.a * _Color.a;
              #else
                float4 fs = SampleFlat(i.uv, shadeCol, centerLab);
                baseCol = fs.rgb;
                alpha = fs.a * _Color.a;
              #endif
                if (_Flatness < 0.999)
                {
                    float3 detail = (PL_LinearToOklab(raw.rgb) - centerLab) * (1.0 - _Flatness);
                    baseCol = AddPaintDetail(baseCol, detail);
                    shadeCol = AddPaintDetail(shadeCol, detail);
                }
            #else
                float4 t = tex2D(_MainTex, i.uv) * _Color;
                baseCol = t.rgb;
                shadeCol = t.rgb * _ShadowTint.rgb;
                alpha = t.a;
            #endif

            #if defined(_ALPHATEST_ON)
                clip(alpha - _Cutoff);
            #endif

                float3 col = baseCol;
                if (_ShadeMode > 0.5)
                {
                    float3 n = normalize(i.worldNormal) * (facing > 0 ? 1.0 : -1.0);
                    float3 l = _WorldSpaceLightPos0.xyz;
                    float ndl = dot(l, l) > 1e-6 ? dot(n, normalize(l)) : 1.0;
                    // fwidth で 1px だけアンチエイリアス = カリッとしつつジャギらない
                    float x = ndl - _ShadeThreshold;
                    float lit = saturate(x / max(fwidth(x), 1e-4) + 0.5);
                    if (_ReceiveShadows > 0.5)
                    {
                        float sa = SHADOW_ATTENUATION(i);
                        lit *= saturate((sa - 0.5) / max(fwidth(sa), 1e-4) + 0.5);
                    }
                    col = lerp(shadeCol, baseCol, lit);
                }
                col *= lerp(float3(1, 1, 1), _LightColor0.rgb, _LightColorInfluence);
                return float4(col, 1.0);
            }
            ENDCG
        }

        Pass
        {
            Tags { "LightMode" = "ShadowCaster" }
            Cull [_Cull]

            CGPROGRAM
            #pragma vertex vertS
            #pragma fragment fragS
            #pragma target 3.5
            #pragma multi_compile_shadowcaster
            #pragma shader_feature_local _ALPHATEST_ON

            struct v2fS
            {
                V2F_SHADOW_CASTER;
                float2 uv : TEXCOORD1;
            };

            v2fS vertS(appdata_base v)
            {
                v2fS o;
                TRANSFER_SHADOW_CASTER_NORMALOFFSET(o)
                o.uv = TRANSFORM_TEX(v.texcoord, _MainTex);
                return o;
            }

            float4 fragS(v2fS i) : SV_Target
            {
            #if defined(_ALPHATEST_ON)
                clip(tex2D(_MainTex, i.uv).a * _Color.a - _Cutoff);
            #endif
                SHADOW_CASTER_FRAGMENT(i)
            }
            ENDCG
        }
    }
}
