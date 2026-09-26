// lilToon の「メイン色調補正 + メイン2nd/3rd (デカール含む)」を
// メインテクスチャの UV 空間に焼き込む内部シェーダー
Shader "Hidden/PencilLine/LilToonBake"
{
    SubShader
    {
        Cull Off
        ZWrite Off
        ZTest Always

        Pass
        {
            CGPROGRAM
            #pragma vertex vertQ
            #pragma fragment frag
            #pragma target 3.5
            #include "UnityCG.cginc"

            sampler2D _PLB_MainTex;
            float4 _PLB_MainST;
            float4 _PLB_Color;      // リニア
            float4 _PLB_HSVG;
            sampler2D _PLB_AdjustMask;
            float _PLB_UseAdjust;

            // P0: x=有効 y=ブレンドモード z=アルファモード w=デカール
            // P1: x=角度(rad) y=Copy z=FlipCopy w=MSDF
            sampler2D _PLB_L1Tex;
            sampler2D _PLB_L1Mask;
            float4 _PLB_L1ST;
            float4 _PLB_L1Color;
            float4 _PLB_L1P0;
            float4 _PLB_L1P1;

            sampler2D _PLB_L2Tex;
            sampler2D _PLB_L2Mask;
            float4 _PLB_L2ST;
            float4 _PLB_L2Color;
            float4 _PLB_L2P0;
            float4 _PLB_L2P1;

            struct appq
            {
                float4 vertex : POSITION;
                float2 uv : TEXCOORD0;
            };

            struct v2fq
            {
                float4 pos : SV_POSITION;
                float2 uv : TEXCOORD0;
            };

            v2fq vertQ(appq v)
            {
                v2fq o;
                o.pos = UnityObjectToClipPos(v.vertex);
                o.uv = v.uv;
                return o;
            }

            // lilToon と同じ色調補正 (HSV + ガンマ)
            float3 PLB_ToneCorrection(float3 c, float4 hsvg)
            {
                c = pow(abs(c), hsvg.w);
                float4 p = (c.b > c.g) ? float4(c.bg, -1.0, 2.0 / 3.0) : float4(c.gb, 0.0, -1.0 / 3.0);
                float4 q = (p.x > c.r) ? float4(p.xyw, c.r) : float4(c.r, p.yzx);
                float d = q.x - min(q.w, q.y);
                float e = 1.0e-10;
                float3 hsv = float3(abs(q.z + (q.w - q.y) / (6.0 * d + e)), d / (q.x + e), q.x);
                hsv = float3(hsv.x + hsvg.x, saturate(hsv.y * hsvg.y), saturate(hsv.z * hsvg.z));
                return hsv.z - hsv.z * hsv.y + hsv.z * hsv.y * saturate(abs(frac(hsv.x + float3(1.0, 2.0 / 3.0, 1.0 / 3.0)) * 6.0 - 3.0) - 1.0);
            }

            float2 PLB_Rotate(float2 uv, float angle)
            {
                if (angle == 0.0) return uv;
                float si, co;
                sincos(angle, si, co);
                float2 o = uv - 0.5;
                o = float2(o.x * co - o.y * si, o.x * si + o.y * co);
                return o + 0.5;
            }

            // lilToon のデカール UV 計算 (左右判定が必要な機能は除く)
            float2 PLB_DecalUV(float2 uv, float4 st, float4 p1)
            {
                float2 o = uv;
                if (p1.y > 0.5) o.x = abs(o.x - 0.5) + 0.5;
                o = o * st.xy + st.zw;
                if (p1.z > 0.5 && uv.x < 0.5) o.x = 1.0 - o.x;
                o = (o - st.zw) / st.xy;
                o = PLB_Rotate(o, p1.x);
                o = o * st.xy + st.zw;
                return o;
            }

            float PLB_Median(float3 c)
            {
                return max(min(c.r, c.g), min(max(c.r, c.g), c.b));
            }

            float4 PLB_Apply(float4 dst, float4 s, float2 uv, float mask, float4 color, float4 p0, float4 p1)
            {
                if (p0.x < 0.5) return dst;
                if (p1.w > 0.5)
                {
                    float sd = PLB_Median(s.rgb);
                    s = float4(1.0, 1.0, 1.0, saturate((sd - 0.5) / clamp(fwidth(sd), 0.01, 1.0)));
                }
                s *= color;
                if (p0.w > 0.5)
                {
                    s.a *= step(0.0, uv.x) * step(uv.x, 1.0) * step(0.0, uv.y) * step(uv.y, 1.0);
                }
                s.a *= mask;

                float3 ad = dst.rgb + s.rgb;
                float3 mu = dst.rgb * s.rgb;
                float3 o = s.rgb;              // 0: 通常
                int bm = (int)p0.y;
                if (bm == 1) o = ad;           // 1: 加算
                else if (bm == 2) o = max(ad - mu, dst.rgb); // 2: スクリーン
                else if (bm == 3) o = mu;      // 3: 乗算
                dst.rgb = lerp(dst.rgb, o, s.a);

                int am = (int)p0.z;
                if (am == 1) dst.a = s.a;
                else if (am == 2) dst.a *= s.a;
                else if (am == 3) dst.a = saturate(dst.a + s.a);
                else if (am == 4) dst.a = saturate(dst.a - s.a);
                return dst;
            }

            float4 frag(v2fq i) : SV_Target
            {
                float2 t = i.uv;                                       // メインテクスチャ上の座標
                float2 meshUV = (t - _PLB_MainST.zw) / _PLB_MainST.xy; // メッシュの UV0

                float4 col = tex2D(_PLB_MainTex, t);
                if (_PLB_UseAdjust > 0.5)
                {
                    float3 baseCol = col.rgb;
                    col.rgb = PLB_ToneCorrection(col.rgb, _PLB_HSVG);
                    col.rgb = lerp(baseCol, col.rgb, tex2D(_PLB_AdjustMask, t).r);
                }
                col *= _PLB_Color;

                float2 uv1 = PLB_DecalUV(meshUV, _PLB_L1ST, _PLB_L1P1);
                float4 s1 = tex2D(_PLB_L1Tex, uv1);
                col = PLB_Apply(col, s1, uv1, tex2D(_PLB_L1Mask, t).r, _PLB_L1Color, _PLB_L1P0, _PLB_L1P1);

                float2 uv2 = PLB_DecalUV(meshUV, _PLB_L2ST, _PLB_L2P1);
                float4 s2 = tex2D(_PLB_L2Tex, uv2);
                col = PLB_Apply(col, s2, uv2, tex2D(_PLB_L2Mask, t).r, _PLB_L2Color, _PLB_L2P0, _PLB_L2P1);

                return col;
            }
            ENDCG
        }
    }
}
