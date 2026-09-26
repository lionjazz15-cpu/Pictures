// 線の検出・太らせ・合成を行う内部シェーダー
// Pass 0: エッジ検出 (1px の芯線 + 種類 + 太さ)
// Pass 1: 太さに応じて広げる (アンチエイリアス付き)
// Pass 2: 縮小して画面に重ねる (乗算済みアルファでブレンド)
// Pass 3: 縮小して線だけ出力 (ストレートアルファ・上書き)
Shader "Hidden/PencilLine/Edge"
{
    Properties
    {
        _MainTex ("Source", 2D) = "white" {}
    }

    CGINCLUDE
    #include "UnityCG.cginc"
    #include "PencilLineCommon.cginc"

    Texture2D<float4> _PL_G0;
    Texture2D<float4> _PL_G1;
    Texture2D<float4> _PL_Edge;
    Texture2D<float4> _PL_Lines;
    Texture2D<float4> _PL_MatParams; // 1024 x 2

    float4 _PL_Size;        // 高解像度バッファのサイズ
    float4 _PL_OutSize;     // 出力サイズ
    float4 _PL_Widths;      // x:外周 y:内側輪郭 z:交差 w:材質境界
    float4 _PL_Widths2;     // x:折れ目 y:シワ z:テクスチャ線
    float _PL_CreaseCos;
    float _PL_DepthThreshold;
    float _PL_DepthSlopeBias;
    float4 _PL_PixScale;    // x: 透視 1pxあたりの角度, y: 平行投影 1pxあたりの長さ
    float4 _PL_Reduce;      // x:near y:far z:最小倍率 w:有効
    float4 _PL_LightDirV;   // xyz: ライト方向(ビュー空間) w:有効
    float4 _PL_LightScale;  // x: 光側の倍率 y: 影側の倍率
    float _PL_PxScale;
    float4 _PL_TraceParams; // x: 色トレスの明度倍率 y: 彩度倍率
    float4 _PL_Wrinkle;     // x: 判定距離(px) y: しきい値
    float4 _PL_TexEdge;     // x: 色の境界のしきい値 (0でオフ)
    float4 _PL_Emphasis;    // x: 強弱の量 y: 最小の強さ z: 強い段差とみなす奥行き比
    float _PL_OccTol;
    float _PL_Radius;
    float _PL_SS;

    sampler2D _MainTex;
    float4 _MainTex_TexelSize;

    // 線の種類 (数値が大きいほど優先) とマスクのビット
    #define T_OUTLINE 7.0
    #define T_INNER 6.0
    #define T_INTERSECT 5.0
    #define T_MATERIAL 4.0
    #define T_CREASE 3.0
    #define T_WRINKLE 2.0
    #define T_TEXTURE 1.0
    #define B_OUTLINE 1
    #define B_INNER 2
    #define B_INTERSECT 4
    #define B_MATERIAL 8
    #define B_CREASE 16
    #define B_WRINKLE 32
    #define B_TEXTURE 64

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

    struct GSample
    {
        float3 n;
        float d;
        float id;
        float obj;
        float mat;
        float3 alb;
        float pal;     // パレット番号+1 (0 = パレットなし)
        float palFlag; // 1 = この色の境界に線を引く
    };

    GSample PL_Read(int2 p)
    {
        p = clamp(p, int2(0, 0), int2(_PL_Size.xy) - int2(1, 1));
        float4 g0 = _PL_G0.Load(int3(p, 0));
        float4 g1 = _PL_G1.Load(int3(p, 0));
        GSample s;
        s.id = g0.w;
        s.d = g0.z;
        s.n = PL_DecodeNormal(g0.xy);
        s.obj = floor(s.id / 1024.0);
        s.mat = s.id - s.obj * 1024.0;
        s.alb = g1.rgb;
        float code = floor(g1.a + 0.5);
        s.palFlag = code >= 64.0 ? 1.0 : 0.0;
        s.pal = code - s.palFlag * 64.0;
        return s;
    }

    float PL_Slope(float3 n)
    {
        float nz = max(abs(n.z), 0.05);
        return min(sqrt(saturate(1.0 - nz * nz)) / nz, 20.0);
    }

    // c が手前側で、n との間に「段差」があるか
    bool PL_DepthEdge(GSample c, GSample n)
    {
        float diff = n.d - c.d;
        if (diff <= 0.0) return false;
        float pix = c.d * _PL_PixScale.x + _PL_PixScale.y;
        float expected = pix * max(PL_Slope(c.n), PL_Slope(n.n));
        return diff > expected * _PL_DepthSlopeBias + c.d * _PL_DepthThreshold;
    }

    // 谷 (へこみ) の強さ。dir 方向に ±k px 離れた法線が向き合っているほど大きい
    float PL_Valley(int2 p, float cid, int2 dir, int k)
    {
        GSample m = PL_Read(p);
        GSample a = PL_Read(p + dir * k);
        GSample b = PL_Read(p - dir * k);
        if (m.id != cid || a.id != cid || b.id != cid) return -1.0;
        float allow = m.d * (_PL_PixScale.x * k * 8.0 + 0.01) + _PL_PixScale.y * k * 8.0;
        if (abs(a.d - m.d) > allow || abs(b.d - m.d) > allow) return -1.0;
        return dot(b.n.xy - a.n.xy, float2(dir));
    }

    // 谷線: しきい値を超え、かつ前後より谷が深い所 (細い1本の芯にする)
    float PL_WrinkleStrength(int2 p, float cid)
    {
        int k = max((int)_PL_Wrinkle.x, 1);
        float thr = _PL_Wrinkle.y;
        float best = 0.0;
        int2 dirs[2] = { int2(1, 0), int2(0, 1) };
        [unroll]
        for (int j = 0; j < 2; j++)
        {
            float v0 = PL_Valley(p, cid, dirs[j], k);
            if (v0 <= thr) continue;
            float vp = PL_Valley(p + dirs[j], cid, dirs[j], k);
            float vm = PL_Valley(p - dirs[j], cid, dirs[j], k);
            if (v0 >= vp && v0 > vm)
            {
                best = max(best, saturate((v0 - thr) / max(thr, 1e-3)));
            }
        }
        return best;
    }

    // ---------------------------------------------------------------
    // Pass 0: エッジ検出
    // 出力: x=種類 y=太さpx z=深度
    // ---------------------------------------------------------------
    float4 FragEdge(v2fq i) : SV_Target
    {
        int2 p = int2(i.uv * _PL_Size.xy);
        GSample c = PL_Read(p);
        if (c.id < 0.5) return float4(0, 0, 0, 0);

        float4 mp0 = _PL_MatParams.Load(int3((int)c.mat, 0, 0));
        float4 mp1 = _PL_MatParams.Load(int3((int)c.mat, 1, 0));
        int mask = (int)(mp1.x + 0.5);
        if (mask == 0) return float4(0, 0, 0, 0);

        int2 offs[4] = { int2(1, 0), int2(0, 1), int2(-1, 0), int2(0, -1) };
        float type = 0.0;
        float strength = 0.0;

        [unroll]
        for (int k = 0; k < 4; k++)
        {
            GSample n = PL_Read(p + offs[k]);
            float t = 0.0;
            float s = 1.0;
            if (n.id < 0.5)
            {
                if (mask & B_OUTLINE) t = T_OUTLINE;              // 背景との境界
            }
            else if (PL_DepthEdge(c, n))
            {
                if (mask & B_INNER)                               // 手前側の段差
                {
                    t = T_INNER;
                    s = saturate(((n.d - c.d) / max(c.d, 1e-4)) / max(_PL_Emphasis.z, 1e-4));
                }
            }
            else if (k < 2 && !PL_DepthEdge(n, c))
            {
                // 連続した面同士。二重にならないよう +x / +y 側だけ判定
                if (n.obj != c.obj)
                {
                    if (mask & B_INTERSECT) t = T_INTERSECT;
                }
                else if (n.mat != c.mat)
                {
                    if (mask & B_MATERIAL) t = T_MATERIAL;
                }
                else
                {
                    float dn = dot(c.n, n.n);
                    if ((mask & B_CREASE) && dn < _PL_CreaseCos)
                    {
                        t = T_CREASE;
                        s = saturate(0.4 + 2.0 * (_PL_CreaseCos - dn));
                    }
                    else if (mask & B_TEXTURE)
                    {
                        if (c.pal > 0.5 && n.pal > 0.5)
                        {
                            // フラット版: 線フラグの付いた色の境界
                            if (c.pal != n.pal && (c.palFlag + n.palFlag) > 0.5) t = T_TEXTURE;
                        }
                        else if (_PL_TexEdge.x > 0.0)
                        {
                            // その他: 色の差が大きい所
                            float dd = length(PL_LinearToOklab(c.alb) - PL_LinearToOklab(n.alb));
                            if (dd > _PL_TexEdge.x)
                            {
                                t = T_TEXTURE;
                                s = saturate((dd - _PL_TexEdge.x) / _PL_TexEdge.x);
                            }
                        }
                    }
                }
            }
            if (t > type)
            {
                type = t;
                strength = s;
            }
        }

        // シワ (谷線)
        if (type < T_WRINKLE && (mask & B_WRINKLE))
        {
            float ws = PL_WrinkleStrength(p, c.id);
            if (ws > 0.0)
            {
                type = T_WRINKLE;
                strength = ws;
            }
        }
        if (type < 0.5) return float4(0, 0, 0, 0);

        // 強弱: 弱い線は細く/消す、強い線は太く
        if (strength < _PL_Emphasis.y) return float4(0, 0, 0, 0);

        float w = (type > 6.5) ? _PL_Widths.x
                : (type > 5.5) ? _PL_Widths.y
                : (type > 4.5) ? _PL_Widths.z
                : (type > 3.5) ? _PL_Widths.w
                : (type > 2.5) ? _PL_Widths2.x
                : (type > 1.5) ? _PL_Widths2.y
                : _PL_Widths2.z;
        w *= mp0.a;
        w *= lerp(1.0, 0.25 + strength, _PL_Emphasis.x);

        if (_PL_Reduce.w > 0.5)
        {
            float rt = saturate((c.d - _PL_Reduce.x) / max(_PL_Reduce.y - _PL_Reduce.x, 1e-4));
            w *= lerp(1.0, _PL_Reduce.z, rt);
        }
        if (_PL_LightDirV.w > 0.5)
        {
            float shadowAmt = saturate(0.5 - 0.5 * dot(c.n, _PL_LightDirV.xyz));
            w *= lerp(_PL_LightScale.x, _PL_LightScale.y, shadowAmt);
        }
        w *= _PL_PxScale;
        if (w < 0.05) return float4(0, 0, 0, 0);
        return float4(type, w, c.d, 0.0);
    }

    // 線の色: 材質ごとの線色と、面の色から作る「色トレス」を混ぜる
    float3 PL_LineColor(int2 p)
    {
        GSample c = PL_Read(p);
        float4 mp0 = _PL_MatParams.Load(int3((int)c.mat, 0, 0));
        float4 mp1 = _PL_MatParams.Load(int3((int)c.mat, 1, 0));
        float3 lab = PL_LinearToOklab(c.alb);
        lab.x *= _PL_TraceParams.x;
        lab.yz *= _PL_TraceParams.y;
        float3 traced = max(PL_OklabToLinear(lab), 0.0);
        return lerp(mp0.rgb, traced, mp1.y);
    }

    // ---------------------------------------------------------------
    // Pass 1: 太らせ (円形の探索 + 前後関係のチェック)
    // 出力: 乗算済みアルファの線色
    // ---------------------------------------------------------------
    float4 FragDilate(v2fq i) : SV_Target
    {
        int2 p = int2(i.uv * _PL_Size.xy);
        int2 size = int2(_PL_Size.xy);
        float4 g0 = _PL_G0.Load(int3(p, 0));
        float cd = g0.w > 0.5 ? g0.z : 1e20;
        int R = (int)_PL_Radius;

        float bestCov = 0.0;
        float bestD = 1e20;
        int2 bestP = p;

        [loop]
        for (int y = -R; y <= R; y++)
        {
            [loop]
            for (int x = -R; x <= R; x++)
            {
                int2 q = p + int2(x, y);
                if (q.x < 0 || q.y < 0 || q.x >= size.x || q.y >= size.y) continue;
                float4 e = _PL_Edge.Load(int3(q, 0));
                if (e.x < 0.5) continue;
                float cov = saturate(e.y * 0.5 - length(float2(x, y)) + 0.5);
                if (cov <= 0.0) continue;
                // 奥にある線が手前の面に被らないようにする
                if (e.z > cd * (1.0 + _PL_OccTol) + 1e-3) continue;
                if (cov > bestCov + 1e-4 || (cov > bestCov - 1e-4 && e.z < bestD))
                {
                    bestCov = cov;
                    bestD = e.z;
                    bestP = q;
                }
            }
        }
        if (bestCov <= 0.0) return float4(0, 0, 0, 0);
        float3 col = PL_LineColor(bestP);
        return float4(col * bestCov, bestCov);
    }

    // ---------------------------------------------------------------
    // Pass 2/3: 縮小 (スーパーサンプリング)
    // ---------------------------------------------------------------
    float4 PL_Downsample(float2 uv)
    {
        float2 luv = uv;
        #if UNITY_UV_STARTS_AT_TOP
        if (_MainTex_TexelSize.y < 0.0) luv.y = 1.0 - luv.y;
        #endif
        int ss = max((int)_PL_SS, 1);
        int2 op = int2(luv * _PL_OutSize.xy);
        float4 acc = float4(0, 0, 0, 0);
        [loop]
        for (int y = 0; y < ss; y++)
        {
            [loop]
            for (int x = 0; x < ss; x++)
            {
                acc += _PL_Lines.Load(int3(op * ss + int2(x, y), 0));
            }
        }
        return acc / (float)(ss * ss);
    }

    // 乗算済みアルファ (画面に重ねる)
    float4 FragOverlay(v2fq i) : SV_Target
    {
        return PL_Downsample(i.uv);
    }

    // ストレートアルファ (線だけ・PNG書き出し)
    float4 FragLinesOnly(v2fq i) : SV_Target
    {
        float4 acc = PL_Downsample(i.uv);
        float3 rgb = acc.a > 1e-5 ? acc.rgb / acc.a : float3(0, 0, 0);
        return float4(rgb, acc.a);
    }
    ENDCG

    SubShader
    {
        Cull Off
        ZWrite Off
        ZTest Always

        Pass
        {
            CGPROGRAM
            #pragma vertex vertQ
            #pragma fragment FragEdge
            #pragma target 4.5
            ENDCG
        }

        Pass
        {
            CGPROGRAM
            #pragma vertex vertQ
            #pragma fragment FragDilate
            #pragma target 4.5
            ENDCG
        }

        Pass
        {
            Blend One OneMinusSrcAlpha, Zero One
            CGPROGRAM
            #pragma vertex vertQ
            #pragma fragment FragOverlay
            #pragma target 4.5
            ENDCG
        }

        Pass
        {
            CGPROGRAM
            #pragma vertex vertQ
            #pragma fragment FragLinesOnly
            #pragma target 4.5
            ENDCG
        }
    }
}
