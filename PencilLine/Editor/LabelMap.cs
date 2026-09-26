using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using UnityEngine;

namespace PencilLine.EditorTools
{
    /// <summary>
    /// 境界を整えた「パレット番号マップ (ラベルマップ)」を作る処理。
    ///
    /// 厚塗り・なめらかなグラデーションをそのまま色の数に丸めると、色の境目がノイズ混じりの等高線になり、
    /// テクセル単位でガタガタする。そこで次の順に整えてから焼き込む。
    ///   1. エッジを残して塗りのムラをならす (バイラテラル)
    ///   2. パレット番号を割り当てる
    ///   3. 最頻値フィルタ + 小さい島の除去で整理する
    ///   4. 境界をなめらかにしながら 2 倍の解像度に広げる
    /// シェーダー側は周囲 4 テクセルの番号を多数決で選ぶので、拡大しても境界がなめらか。
    ///
    /// ここでは Unity の API を使わない (Parallel.For で別スレッドから呼ぶため)。
    /// </summary>
    public static class LabelMap
    {
        public const byte Unassigned = 255;
        public const int MaxOutputSize = 4096;

        public struct Params
        {
            public int smoothRadius;
            public float smoothSigma;
            public int smoothIterations;
            public int modeRadius;
            public int modeIterations;
            public int minIsland;
            public float upSigma;
            public float maxDelta;   // これより色の差 (OKLab) が大きい番号同士は整理しない (線や模様を守る)
        }

        /// <summary>
        /// 「整理の強さ」(0〜1) から各処理の強さを決める。
        /// 半径や面積は 1024px のテクスチャを基準にして、解像度に合わせて増やす。
        /// </summary>
        public static Params ParamsFor(float strength, int w, int h)
        {
            float s = Mathf.Clamp01(strength);
            float f = Mathf.Clamp(Mathf.Max(w, h) / 1024f, 0.25f, 4f);
            return new Params
            {
                smoothRadius = Mathf.Clamp(Mathf.RoundToInt((1f + 5f * s) * f), 1, 16),
                smoothSigma = 0.02f + 0.06f * s,
                smoothIterations = 1 + Mathf.RoundToInt(2f * s),
                modeRadius = Mathf.Clamp(Mathf.RoundToInt((0.75f + 1.5f * s) * f), 1, 6),
                modeIterations = 1 + Mathf.RoundToInt(2f * s),
                minIsland = Mathf.Max(1, Mathf.RoundToInt((4f + 400f * s * s) * f * f)),
                upSigma = 0.55f + 0.2f * s,
                maxDelta = 0.1f + 0.1f * s,
            };
        }

        // ------------------------------------------------------------
        // 1. 塗りのムラをならす (OKLab 上の分離型バイラテラル)
        // ------------------------------------------------------------
        public static void Smooth(float[] lab, bool[] opaque, int w, int h, int radius, float sigma, int iterations)
        {
            if (radius < 1 || iterations < 1) return;
            var tmp = new float[lab.Length];
            var spatial = new float[radius + 1];
            float ss = Math.Max(0.5f, radius * 0.6f);
            for (int d = 0; d <= radius; d++) spatial[d] = (float)Math.Exp(-d * d / (2.0 * ss * ss));
            float inv = 1f / (2f * sigma * sigma);

            for (int it = 0; it < iterations; it++)
            {
                BilateralPass(lab, tmp, opaque, w, h, radius, spatial, inv, true);
                BilateralPass(tmp, lab, opaque, w, h, radius, spatial, inv, false);
            }
        }

        static void BilateralPass(float[] src, float[] dst, bool[] opaque, int w, int h, int r,
                                  float[] spatial, float inv, bool horizontal)
        {
            Parallel.For(0, h, y =>
            {
                for (int x = 0; x < w; x++)
                {
                    int i = y * w + x;
                    int i3 = i * 3;
                    float cL = src[i3], ca = src[i3 + 1], cb = src[i3 + 2];
                    if (!opaque[i])
                    {
                        dst[i3] = cL;
                        dst[i3 + 1] = ca;
                        dst[i3 + 2] = cb;
                        continue;
                    }
                    float sL = 0f, sa = 0f, sb = 0f, sw = 0f;
                    for (int d = -r; d <= r; d++)
                    {
                        int xx = horizontal ? x + d : x;
                        int yy = horizontal ? y : y + d;
                        if (xx < 0 || xx >= w || yy < 0 || yy >= h) continue;
                        int j = yy * w + xx;
                        if (!opaque[j]) continue;
                        int j3 = j * 3;
                        float dL = src[j3] - cL, da = src[j3 + 1] - ca, db = src[j3 + 2] - cb;
                        float q = (dL * dL + da * da + db * db) * inv;
                        if (q > 9f) continue; // exp(-9) ≒ 0
                        float wgt = spatial[d < 0 ? -d : d] * (float)Math.Exp(-q);
                        sL += src[j3] * wgt;
                        sa += src[j3 + 1] * wgt;
                        sb += src[j3 + 2] * wgt;
                        sw += wgt;
                    }
                    // 自分自身の重みは 1 なので sw > 0
                    dst[i3] = sL / sw;
                    dst[i3 + 1] = sa / sw;
                    dst[i3 + 2] = sb / sw;
                }
            });
        }

        // ------------------------------------------------------------
        // 2. パレット番号の割り当て (不透明でない所は Unassigned)
        // ------------------------------------------------------------
        public static byte[] Assign(float[] lab, bool[] opaque, int w, int h, Vector3[] centers, float weightL, float weightC)
        {
            var labels = new byte[w * h];
            int k = centers.Length;
            Parallel.For(0, h, y =>
            {
                for (int x = 0; x < w; x++)
                {
                    int i = y * w + x;
                    if (!opaque[i])
                    {
                        labels[i] = Unassigned;
                        continue;
                    }
                    int i3 = i * 3;
                    int best = 0;
                    float bd = float.MaxValue;
                    for (int j = 0; j < k; j++)
                    {
                        float dL = lab[i3] - centers[j].x;
                        float da = lab[i3 + 1] - centers[j].y;
                        float db = lab[i3 + 2] - centers[j].z;
                        float dist = dL * dL * weightL + (da * da + db * db) * weightC;
                        if (dist < bd)
                        {
                            bd = dist;
                            best = j;
                        }
                    }
                    labels[i] = (byte)best;
                }
            });
            return labels;
        }

        /// <summary>透明な所を一番近い不透明な所の番号で埋める (境界の補間で透明部分の色が混ざらないように)</summary>
        public static void FillUnassigned(byte[] labels, int w, int h)
        {
            int n = w * h;
            var queue = new int[n];
            int head = 0, tail = 0;
            for (int i = 0; i < n; i++)
            {
                if (labels[i] != Unassigned) queue[tail++] = i;
            }
            if (tail == 0)
            {
                for (int i = 0; i < n; i++) labels[i] = 0;
                return;
            }
            while (head < tail)
            {
                int i = queue[head++];
                int x = i % w, y = i / w;
                byte l = labels[i];
                if (x > 0 && labels[i - 1] == Unassigned) { labels[i - 1] = l; queue[tail++] = i - 1; }
                if (x < w - 1 && labels[i + 1] == Unassigned) { labels[i + 1] = l; queue[tail++] = i + 1; }
                if (y > 0 && labels[i - w] == Unassigned) { labels[i - w] = l; queue[tail++] = i - w; }
                if (y < h - 1 && labels[i + w] == Unassigned) { labels[i + w] = l; queue[tail++] = i + w; }
            }
        }

        /// <summary>
        /// 番号同士が「似た色」か (k×k)。ジャギーの原因はグラデーションの隣り合う段なので、整理は似た色の間だけで行う。
        /// 色の差が大きい境界 (テクスチャに描かれた線・模様) は形をそのまま残す。
        /// </summary>
        public static bool[] Similarity(Vector3[] centers, float maxDelta)
        {
            int k = centers.Length;
            var sim = new bool[k * k];
            float max2 = maxDelta * maxDelta;
            for (int a = 0; a < k; a++)
            {
                for (int b = 0; b < k; b++)
                {
                    float dL = centers[a].x - centers[b].x;
                    float da = centers[a].y - centers[b].y;
                    float db = centers[a].z - centers[b].z;
                    sim[a * k + b] = dL * dL + da * da + db * db <= max2;
                }
            }
            return sim;
        }

        // ------------------------------------------------------------
        // 3. 最頻値フィルタ (似た色の番号の中で多数決。同数なら自分の番号を残す)
        // ------------------------------------------------------------
        public static byte[] ModeFilter(byte[] src, int w, int h, int k, bool[] sim, int r)
        {
            var dst = new byte[src.Length];
            Parallel.For(0, h, () => new int[256], (y, state, cnt) =>
            {
                for (int x = 0; x < w; x++)
                {
                    int x0 = Math.Max(0, x - r), x1 = Math.Min(w - 1, x + r);
                    int y0 = Math.Max(0, y - r), y1 = Math.Min(h - 1, y + r);
                    for (int yy = y0; yy <= y1; yy++)
                    {
                        int row = yy * w;
                        for (int xx = x0; xx <= x1; xx++) cnt[src[row + xx]]++;
                    }
                    byte own = src[y * w + x];
                    byte best = own;
                    int bc = cnt[own];
                    int simRow = own * k;
                    for (int yy = y0; yy <= y1; yy++)
                    {
                        int row = yy * w;
                        for (int xx = x0; xx <= x1; xx++)
                        {
                            byte l = src[row + xx];
                            if (cnt[l] > bc && sim[simRow + l])
                            {
                                bc = cnt[l];
                                best = l;
                            }
                        }
                    }
                    for (int yy = y0; yy <= y1; yy++)
                    {
                        int row = yy * w;
                        for (int xx = x0; xx <= x1; xx++) cnt[src[row + xx]] = 0;
                    }
                    dst[y * w + x] = best;
                }
                return cnt;
            }, _ => { });
            return dst;
        }

        /// <summary>面積が minArea 未満の島を、一番長く接している「似た色の」隣の番号に塗り替える</summary>
        public static void RemoveIslands(byte[] labels, int w, int h, int k, bool[] sim, int minArea)
        {
            if (minArea <= 1) return;
            int n = w * h;
            var visited = new bool[n];
            var stack = new int[n];
            var members = new List<int>();
            var border = new int[256];
            var nb = new int[4];

            for (int start = 0; start < n; start++)
            {
                if (visited[start]) continue;
                byte l = labels[start];
                members.Clear();
                int sp = 0;
                stack[sp++] = start;
                visited[start] = true;
                while (sp > 0)
                {
                    int i = stack[--sp];
                    members.Add(i);
                    int c = Neighbors(i, w, h, nb);
                    for (int t = 0; t < c; t++)
                    {
                        int j = nb[t];
                        if (visited[j] || labels[j] != l) continue;
                        visited[j] = true;
                        stack[sp++] = j;
                    }
                }
                if (members.Count >= minArea) continue;

                // 接している番号を数えて、一番多いものに塗り替える
                Array.Clear(border, 0, border.Length);
                foreach (int i in members)
                {
                    int c = Neighbors(i, w, h, nb);
                    for (int t = 0; t < c; t++)
                    {
                        byte o = labels[nb[t]];
                        if (o != l && sim[l * k + o]) border[o]++;
                    }
                }
                int best = -1, bc = 0;
                for (int j = 0; j < border.Length; j++)
                {
                    if (border[j] > bc)
                    {
                        bc = border[j];
                        best = j;
                    }
                }
                if (best < 0) continue; // 似た色が隣にない (模様・線) か、画像全体が1つの島
                foreach (int i in members) labels[i] = (byte)best;
            }
        }

        static int Neighbors(int i, int w, int h, int[] nb)
        {
            int x = i % w, y = i / w, c = 0;
            if (x > 0) nb[c++] = i - 1;
            if (x < w - 1) nb[c++] = i + 1;
            if (y > 0) nb[c++] = i - w;
            if (y < h - 1) nb[c++] = i + w;
            return c;
        }

        // ------------------------------------------------------------
        // 4. 境界をなめらかにしながら拡大
        //    番号ごとにガウス重みを合計して一番重いものを選ぶ。ただし候補は「一番近いテクセルの番号と似た色」だけ
        //    (細い線が周りの色に負けて消えないように)
        // ------------------------------------------------------------
        public static byte[] Upsample(byte[] labels, int w, int h, int k, bool[] sim, int scale, float sigma, out int ow, out int oh)
        {
            if (scale <= 1)
            {
                ow = w;
                oh = h;
                return (byte[])labels.Clone();
            }
            ow = w * scale;
            oh = h * scale;
            int[] bx, by;
            float[] wx, wy;
            Taps(ow, scale, sigma, out bx, out wx);
            Taps(oh, scale, sigma, out by, out wy);
            int[] nx = Nearest(ow, scale, w);
            int[] ny = Nearest(oh, scale, h);

            var dst = new byte[ow * oh];
            int outW = ow;
            Parallel.For(0, oh, () => new float[256], (y, state, acc) =>
            {
                var touched = new int[16];
                for (int x = 0; x < outW; x++)
                {
                    int own = labels[ny[y] * w + nx[x]];
                    int simRow = own * k;
                    int nt = 0;
                    for (int ty = 0; ty < 4; ty++)
                    {
                        int sy = Mathf.Clamp(by[y] + ty - 1, 0, h - 1);
                        float wgy = wy[y * 4 + ty];
                        for (int tx = 0; tx < 4; tx++)
                        {
                            int sx = Mathf.Clamp(bx[x] + tx - 1, 0, w - 1);
                            byte l = labels[sy * w + sx];
                            if (!sim[simRow + l]) continue;
                            if (acc[l] == 0f) touched[nt++] = l;
                            acc[l] += wgy * wx[x * 4 + tx];
                        }
                    }
                    int best = touched[0];
                    float bw = -1f;
                    for (int t = 0; t < nt; t++)
                    {
                        int l = touched[t];
                        if (acc[l] > bw)
                        {
                            bw = acc[l];
                            best = l;
                        }
                        acc[l] = 0f;
                    }
                    dst[y * outW + x] = (byte)best;
                }
                return acc;
            }, _ => { });
            return dst;
        }

        // 出力の各列 (行) が含まれる元のテクセル
        static int[] Nearest(int outSize, int scale, int srcSize)
        {
            var idx = new int[outSize];
            for (int o = 0; o < outSize; o++) idx[o] = Mathf.Clamp(o / scale, 0, srcSize - 1);
            return idx;
        }

        // 出力の各列 (行) について、元の 4 テクセル (base-1 〜 base+2) の位置と重み
        static void Taps(int outSize, int scale, float sigma, out int[] bases, out float[] weights)
        {
            bases = new int[outSize];
            weights = new float[outSize * 4];
            float inv = 1f / (2f * sigma * sigma);
            for (int o = 0; o < outSize; o++)
            {
                float s = (o + 0.5f) / scale - 0.5f;
                int b = Mathf.FloorToInt(s);
                bases[o] = b;
                for (int t = 0; t < 4; t++)
                {
                    float d = (b + t - 1) - s;
                    // 0 にならないよう下限を付ける (0 を「未使用」の印にしているため)
                    weights[o * 4 + t] = Mathf.Max(Mathf.Exp(-d * d * inv), 1e-6f);
                }
            }
        }

        // ------------------------------------------------------------
        // ミップ (2x2 の多数決で縮小。番号は平均できないので自前で作る)
        // ------------------------------------------------------------
        public static byte[] Downsample(byte[] src, int sw, int sh, int dw, int dh)
        {
            var dst = new byte[dw * dh];
            for (int y = 0; y < dh; y++)
            {
                int y0 = Math.Min(y * 2, sh - 1), y1 = Math.Min(y * 2 + 1, sh - 1);
                for (int x = 0; x < dw; x++)
                {
                    int x0 = Math.Min(x * 2, sw - 1), x1 = Math.Min(x * 2 + 1, sw - 1);
                    byte a = src[y0 * sw + x0], b = src[y0 * sw + x1], c = src[y1 * sw + x0], d = src[y1 * sw + x1];
                    int ca = 1 + (b == a ? 1 : 0) + (c == a ? 1 : 0) + (d == a ? 1 : 0);
                    int cb = 1 + (c == b ? 1 : 0) + (d == b ? 1 : 0);
                    int cc = 1 + (d == c ? 1 : 0);
                    byte best = a;
                    int bc = ca;
                    if (cb > bc) { bc = cb; best = b; }
                    if (cc > bc) { best = c; }
                    dst[y * dw + x] = best;
                }
            }
            return dst;
        }

        /// <summary>
        /// ミップアトラスを作る (1 チャンネル、下の行から)。
        ///   左: ミップ0 (w×h)   右 (x = w): ミップ1, 2, 3 … を下から順に積む
        /// シェーダー側の読み方は PencilLineCommon.cginc の PL_SampleLabel
        /// </summary>
        public static byte[] BuildAtlas(byte[] level0, int w, int h, out int aw, out int ah, out int levels)
        {
            levels = 1;
            while ((w >> levels) > 0 || (h >> levels) > 0) levels++;
            aw = w + Math.Max(1, (w + 1) / 2);
            int mipsH = 0;
            for (int m = 1; m < levels; m++) mipsH += Math.Max(1, h >> m);
            ah = Math.Max(h, mipsH); // 横長のテクスチャではミップの列の方が高くなることがある

            var atlas = new byte[aw * ah];
            Place(atlas, aw, level0, w, h, 0, 0);
            byte[] level = level0;
            int lw = w, lh = h, oy = 0;
            for (int m = 1; m < levels; m++)
            {
                int nw = Math.Max(1, w >> m);
                int nh = Math.Max(1, h >> m);
                level = Downsample(level, lw, lh, nw, nh);
                Place(atlas, aw, level, nw, nh, w, oy);
                oy += nh;
                lw = nw;
                lh = nh;
            }
            return atlas;
        }

        static void Place(byte[] atlas, int aw, byte[] src, int w, int h, int ox, int oy)
        {
            for (int y = 0; y < h; y++) Array.Copy(src, y * w, atlas, (oy + y) * aw + ox, w);
        }
    }
}
