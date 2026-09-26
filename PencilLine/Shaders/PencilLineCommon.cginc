#ifndef PENCILLINE_COMMON_INCLUDED
#define PENCILLINE_COMMON_INCLUDED

// ---------------------------------------------------------------
// 法線の八面体エンコード (float3 -> float2, 範囲 [-1,1])
// ---------------------------------------------------------------
float2 PL_SignNotZero(float2 v)
{
    return float2(v.x >= 0.0 ? 1.0 : -1.0, v.y >= 0.0 ? 1.0 : -1.0);
}

float2 PL_EncodeNormal(float3 n)
{
    n /= max(abs(n.x) + abs(n.y) + abs(n.z), 1e-6);
    float2 e = n.xy;
    if (n.z < 0.0)
    {
        e = (1.0 - abs(n.yx)) * PL_SignNotZero(n.xy);
    }
    return e;
}

float3 PL_DecodeNormal(float2 e)
{
    float3 n = float3(e.x, e.y, 1.0 - abs(e.x) - abs(e.y));
    float t = saturate(-n.z);
    n.x += n.x >= 0.0 ? -t : t;
    n.y += n.y >= 0.0 ? -t : t;
    return normalize(n);
}

// ---------------------------------------------------------------
// OKLab (知覚的に均等な色空間)。入力・出力はリニアRGB
// ---------------------------------------------------------------
float3 PL_LinearToOklab(float3 c)
{
    c = max(c, 0.0);
    float l = 0.4122214708 * c.r + 0.5363325363 * c.g + 0.0514459929 * c.b;
    float m = 0.2119034982 * c.r + 0.6806995451 * c.g + 0.1073969566 * c.b;
    float s = 0.0883024619 * c.r + 0.2817188376 * c.g + 0.6299787005 * c.b;
    l = pow(max(l, 0.0), 1.0 / 3.0);
    m = pow(max(m, 0.0), 1.0 / 3.0);
    s = pow(max(s, 0.0), 1.0 / 3.0);
    return float3(
        0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
        1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
        0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s);
}

float3 PL_OklabToLinear(float3 lab)
{
    float l_ = lab.x + 0.3963377774 * lab.y + 0.2158037573 * lab.z;
    float m_ = lab.x - 0.1055613458 * lab.y - 0.0638541728 * lab.z;
    float s_ = lab.x - 0.0894841775 * lab.y - 1.2914855480 * lab.z;
    float l = l_ * l_ * l_;
    float m = m_ * m_ * m_;
    float s = s_ * s_ * s_;
    return float3(
         4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s);
}

// ---------------------------------------------------------------
// パレット番号マップ (ラベルマップ) の読み出し
// R8 に「番号」(0〜31) が入っている。番号は平均できないので、ミップは 2x2 の多数決で作り、
// 1 枚に並べてある (PaletteBuilder.WriteLabelMap)。
//   左: ミップ0 (W×H)   右 (x = W): ミップ1, 2, 3 … を下から順に積む
// info = (W, H, ミップの数, リピートなら1)
//
// 周囲 4 テクセルのバイリニア重みを番号ごとに合計し、一番重い番号を選ぶ。
// 境界は重みが釣り合う曲線になるので、拡大してもテクセルの階段が出ない。
// best: 選ばれた番号 / second: 境界の向こう側の番号 / blend: best の割合 (境界だけ 1px でアンチエイリアス)
// ---------------------------------------------------------------
struct PL_Label
{
    int best;
    int second;
    float blend;
};

PL_Label PL_SampleLabel(Texture2D<float> tex, float4 info, float2 uv, float2 duvdx, float2 duvdy)
{
    float2 size0 = max(info.xy, 1.0);
    float2 dx = duvdx * size0;
    float2 dy = duvdy * size0;
    float rho2 = max(max(dot(dx, dx), dot(dy, dy)), 1e-12);
    float lod = min(floor(max(0.0, 0.5 * log2(rho2))), max(info.z - 1.0, 0.0));
    float2 size = max(floor(size0 / exp2(lod)), 1.0);
    float texPerPx = sqrt(rho2) / exp2(lod); // このミップでの 1px あたりのテクセル数

    // アトラス内でのこのミップの位置
    float2 origin = float2(0.0, 0.0);
    if (lod > 0.5)
    {
        origin.x = size0.x;
        int mip = (int)lod;
        [loop]
        for (int i = 1; i < mip; i++) origin.y += max(floor(size0.y / exp2((float)i)), 1.0);
    }

    float2 st = uv * size - 0.5;
    float2 f = frac(st);
    float2 b = floor(st);

    int lab[4];
    float wt[4];
    [unroll]
    for (int k = 0; k < 4; k++)
    {
        float2 o = float2(k & 1, k >> 1);
        float2 p = b + o;
        if (info.w > 0.5) p -= floor(p / size) * size; // リピート
        else p = clamp(p, 0.0, size - 1.0);
        lab[k] = (int)(tex.Load(int3((int2)(p + origin), 0)) * 255.0 + 0.5);
        float2 wv = lerp(1.0 - f, f, o);
        wt[k] = wv.x * wv.y;
    }

    // 番号ごとの重みの合計
    float tot[4];
    [unroll]
    for (int a = 0; a < 4; a++)
    {
        tot[a] = 0.0;
        [unroll]
        for (int c = 0; c < 4; c++) tot[a] += lab[c] == lab[a] ? wt[c] : 0.0;
    }

    PL_Label r;
    r.best = lab[0];
    float bw = tot[0];
    [unroll]
    for (int a2 = 1; a2 < 4; a2++)
    {
        if (tot[a2] > bw)
        {
            bw = tot[a2];
            r.best = lab[a2];
        }
    }
    r.second = r.best;
    float sw = 0.0;
    [unroll]
    for (int a3 = 0; a3 < 4; a3++)
    {
        if (lab[a3] != r.best && tot[a3] > sw)
        {
            sw = tot[a3];
            r.second = lab[a3];
        }
    }
    // 重みの差は 1 テクセル進むと約 2 変わる → 境界からの距離 (px) ≒ 差 / 2 / texPerPx
    r.blend = sw > 0.0 ? saturate(0.5 + (bw - sw) / max(2.0 * texPerPx, 1e-4)) : 1.0;
    return r;
}

#endif
