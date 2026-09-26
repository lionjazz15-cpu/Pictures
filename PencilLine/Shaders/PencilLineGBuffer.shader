// 線検出用の情報 (法線・深度・ID・色・パレット番号) を書き出す内部シェーダー
// PencilLineEffect から CommandBuffer.DrawRenderer で使われます
Shader "Hidden/PencilLine/GBuffer"
{
    SubShader
    {
        Pass
        {
            // 裏表の判定は自前で行うので Cull Off
            Cull Off
            ZWrite On
            ZTest LEqual

            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #pragma target 4.5
            #include "UnityCG.cginc"
            #include "PencilLineCommon.cginc"

            sampler2D _PL_MainTex;
            float4 _PL_MainTex_ST;
            float4 _PL_Color;      // リニア値
            float _PL_Cutoff;      // < 0 ならアルファ抜きなし
            float _PL_CullMode;    // 0:Off 1:Front 2:Back (元マテリアルのカリング)
            float _PL_ID;          // objectIndex * 1024 + materialIndex
            float4x4 _PL_MatrixV;
            float4x4 _PL_MatrixVP;

            // ノーマルマップ (シワの検出用)
            sampler2D _PL_BumpMap;
            float4 _PL_BumpST;
            float _PL_BumpScale;
            float _PL_UseBump;

            // Flat Palette のパレット (パレット境界の線用)
            Texture2D<float4> _PL_PaletteTex; // row0: OKLab  row3: x=線フラグ
            float _PL_PaletteCount;
            float4 _PL_PaletteWeights;        // x: 明度の重み y: 色みの重み
            Texture2D<float> _PL_LabelTex;    // 境界を整えたパレット番号マップ (あればこちらを使う)
            float4 _PL_LabelSize;
            float _PL_UseLabel;

            struct appdata
            {
                float4 vertex : POSITION;
                float3 normal : NORMAL;
                float4 tangent : TANGENT;
                float2 uv : TEXCOORD0;
            };

            struct v2f
            {
                float4 pos : SV_POSITION;
                float4 uv : TEXCOORD0;          // xy: メイン zw: ノーマルマップ
                float3 viewPos : TEXCOORD1;
                float3 worldNormal : TEXCOORD2;
                float4 worldTangent : TEXCOORD3;
            };

            struct FragOut
            {
                float4 g0 : SV_Target0; // xy: 法線(八面体) z: 線形深度 w: ID
                float4 g1 : SV_Target1; // rgb: 色 (色トレス用)  a: パレット番号+1 (+64 で線フラグ)
            };

            v2f vert(appdata v)
            {
                v2f o;
                float4 worldPos = mul(unity_ObjectToWorld, float4(v.vertex.xyz, 1.0));
                o.pos = mul(_PL_MatrixVP, worldPos);
                o.viewPos = mul(_PL_MatrixV, worldPos).xyz;
                o.worldNormal = UnityObjectToWorldNormal(v.normal);
                o.worldTangent = float4(UnityObjectToWorldDir(v.tangent.xyz), v.tangent.w * unity_WorldTransformParams.w);
                o.uv.xy = v.uv * _PL_MainTex_ST.xy + _PL_MainTex_ST.zw;
                o.uv.zw = v.uv * _PL_BumpST.xy + _PL_BumpST.zw;
                return o;
            }

            float PL_PaletteCode(float3 raw)
            {
                int count = (int)_PL_PaletteCount;
                if (count <= 0) return 0.0;
                float3 lab = PL_LinearToOklab(raw);
                float best = 1e20;
                int bi = 0;
                [loop]
                for (int k = 0; k < count; k++)
                {
                    float3 d = lab - _PL_PaletteTex.Load(int3(k, 0, 0)).xyz;
                    float dist = d.x * d.x * _PL_PaletteWeights.x + (d.y * d.y + d.z * d.z) * _PL_PaletteWeights.y;
                    if (dist < best)
                    {
                        best = dist;
                        bi = k;
                    }
                }
                float flag = _PL_PaletteTex.Load(int3(bi, 3, 0)).x;
                return (float)(bi + 1) + (flag > 0.5 ? 64.0 : 0.0);
            }

            // 番号マップから (フラット版の見た目と同じ境界になる)
            float PL_LabelCode(float2 uv, float2 duvdx, float2 duvdy)
            {
                int count = (int)_PL_PaletteCount;
                if (count <= 0) return 0.0;
                PL_Label lb = PL_SampleLabel(_PL_LabelTex, _PL_LabelSize, uv, duvdx, duvdy);
                int bi = min(lb.best, count - 1);
                float flag = _PL_PaletteTex.Load(int3(bi, 3, 0)).x;
                return (float)(bi + 1) + (flag > 0.5 ? 64.0 : 0.0);
            }

            FragOut frag(v2f i)
            {
                // 微分は discard より前に取っておく
                float2 duvdx = ddx(i.uv.xy);
                float2 duvdy = ddy(i.uv.xy);

                // 画面上の面の向き (ジオメトリ法線) をカメラ側に向けて求める
                float3 cr = cross(ddy(i.viewPos), ddx(i.viewPos));
                float3 geoN = cr * rsqrt(max(dot(cr, cr), 1e-30));
                if (dot(geoN, -i.viewPos) < 0.0) geoN = -geoN;

                float4 raw = tex2D(_PL_MainTex, i.uv.xy);
                float4 tex = raw * _PL_Color;
                if (_PL_Cutoff >= 0.0) clip(tex.a - _PL_Cutoff);

                float3 wn = normalize(i.worldNormal);
                float3 n = normalize(mul((float3x3)_PL_MatrixV, wn));
                bool isBack = dot(n, geoN) < 0.0;

                // 元マテリアルのカリングを再現
                if (_PL_CullMode > 1.5 && isBack) discard;
                if (_PL_CullMode > 0.5 && _PL_CullMode < 1.5 && !isBack) discard;

                // ノーマルマップ (ワールド空間で合成してからビュー空間へ)
                if (_PL_UseBump > 0.5)
                {
                    float3 wt = i.worldTangent.xyz - wn * dot(wn, i.worldTangent.xyz);
                    if (dot(wt, wt) > 1e-8)
                    {
                        wt = normalize(wt);
                        float3 wb = cross(wn, wt) * (i.worldTangent.w < 0.0 ? -1.0 : 1.0);
                        float3 tn = UnpackNormalWithScale(tex2D(_PL_BumpMap, i.uv.zw), _PL_BumpScale);
                        float3 bn = normalize(wt * tn.x + wb * tn.y + wn * tn.z);
                        n = normalize(mul((float3x3)_PL_MatrixV, bn));
                    }
                }
                if (isBack) n = -n;

                FragOut o;
                o.g0 = float4(PL_EncodeNormal(n), -i.viewPos.z, _PL_ID);
                float code;
                if (_PL_UseLabel > 0.5) code = PL_LabelCode(i.uv.xy, duvdx, duvdy);
                else code = PL_PaletteCode(raw.rgb);
                o.g1 = float4(tex.rgb, code);
                return o;
            }
            ENDCG
        }
    }
}
