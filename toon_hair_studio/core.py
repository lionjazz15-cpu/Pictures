# SPDX-License-Identifier: GPL-3.0-or-later
"""Geometry core: guide curves -> clean-topology toon hair mesh.

Pure mathutils code (no bpy) so it can be tested headless.

Per ring (cross-section) frame:
    T  tangent along the lock (root -> tip)
    N  outward, away from the head centre, perpendicular to T
    B  width direction, B = T x N
A profile point (x, y), x in -1..1, maps to
    lerp(L, R, (x + 1) / 2) + N * (y * width)
where L / R are the left (-B) and right (+B) edge points of the ring.
Quads (i,j)->(i,j+1)->(i+1,j+1)->(i+1,j) then face +N.

A *sheet* is a run of adjacent locks. Neighbouring locks share their
boundary vertices from the root down to the split point, then separate into
individual pointed tips, which gives the classic connected anime hair mass
with a serrated hem - still all quads, closed and manifold.
Independent strands are simply sheets with one lock, and a branched lock
(分岐毛) is a sheet of sub-locks grown from one guide curve that separate at
the branch point.
"""

import math
from mathutils import Vector, Matrix

EPS = 1e-9


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def smoothstep(e0, e1, x):
    if e1 <= e0:
        return 1.0 if x >= e1 else 0.0
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _bezier(p0, p1, p2, p3, t):
    u = 1.0 - t
    return p0 * (u * u * u) + p1 * (3.0 * u * u * t) + p2 * (3.0 * u * t * t) + p3 * (t * t * t)


def _catmull(p0, p1, p2, p3, t):
    t2 = t * t
    t3 = t2 * t
    return 0.5 * ((2.0 * p1) + (-p0 + p2) * t + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t2
                  + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t3)


def newell(pts):
    n = Vector((0.0, 0.0, 0.0))
    k = len(pts)
    for a in range(k):
        v1 = pts[a]
        v2 = pts[(a + 1) % k]
        n.x += (v1.y - v2.y) * (v1.z + v2.z)
        n.y += (v1.z - v2.z) * (v1.x + v2.x)
        n.z += (v1.x - v2.x) * (v1.y + v2.y)
    return n


class Center:
    """Head volume: an ellipsoid given by the centre object's world matrix."""

    def __init__(self, matrix=None):
        if matrix is None:
            matrix = Matrix.Identity(4)
        self.matrix = matrix.copy()
        try:
            self.inv = matrix.inverted()
        except ValueError:
            self.inv = Matrix.Identity(4)
        self.inv_t3 = self.inv.to_3x3().transposed()
        self.location = matrix.to_translation()
        ax = matrix.to_3x3() @ Vector((1.0, 0.0, 0.0))
        self.mirror_axis = ax.normalized() if ax.length > EPS else Vector((1.0, 0.0, 0.0))
        self.radius = max(max(matrix.to_scale()), 1e-6)

    def radial(self, p):
        n = self.inv_t3 @ (self.inv @ p)
        if n.length < EPS:
            return Vector((0.0, 0.0, 1.0))
        return n.normalized()

    def head_coord(self, p):
        """(azimuth 0..1 with 0.5 = front (-Y), height -1..1)."""
        d = self.inv @ p
        if d.length < EPS:
            return 0.5, 0.0
        d.normalize()
        return math.atan2(d.x, -d.y) / (2.0 * math.pi) + 0.5, d.z

    def mirror(self, p):
        d = (p - self.location).dot(self.mirror_axis)
        return p - self.mirror_axis * (2.0 * d)


# --------------------------------------------------------------------------
# curve sampling
# --------------------------------------------------------------------------

def sample_spline(spline, matrix, dense=32):
    """Dense world-space samples (position, radius, tilt) of a Blender spline."""
    out = []
    if spline.type == 'BEZIER':
        bps = spline.bezier_points
        if len(bps) < 2:
            return out
        for i in range(len(bps) - 1):
            a, b = bps[i], bps[i + 1]
            p0, p1, p2, p3 = a.co.copy(), a.handle_right.copy(), b.handle_left.copy(), b.co.copy()
            for k in range(dense):
                t = k / dense
                s = t * t * (3.0 - 2.0 * t)
                out.append((matrix @ _bezier(p0, p1, p2, p3, t),
                            a.radius + (b.radius - a.radius) * s,
                            a.tilt + (b.tilt - a.tilt) * s))
        b = bps[-1]
        out.append((matrix @ b.co, b.radius, b.tilt))
        return out

    pts = spline.points
    if len(pts) < 2:
        return out
    cos = [p.co.to_3d() for p in pts]
    if spline.type == 'NURBS' and len(cos) >= 3:
        n = len(cos)
        for i in range(n - 1):
            p0 = cos[max(i - 1, 0)]
            p1, p2 = cos[i], cos[i + 1]
            p3 = cos[min(i + 2, n - 1)]
            a, b = pts[i], pts[i + 1]
            for k in range(dense):
                t = k / dense
                out.append((matrix @ _catmull(p0, p1, p2, p3, t),
                            a.radius + (b.radius - a.radius) * t,
                            a.tilt + (b.tilt - a.tilt) * t))
    else:
        for i in range(len(cos) - 1):
            a, b = pts[i], pts[i + 1]
            for k in range(4):
                t = k / 4.0
                out.append((matrix @ cos[i].lerp(cos[i + 1], t),
                            a.radius + (b.radius - a.radius) * t,
                            a.tilt + (b.tilt - a.tilt) * t))
    out.append((matrix @ cos[-1], pts[-1].radius, pts[-1].tilt))
    return out


def resample(samples, segments, tip_bias=0.0):
    """Arc-length resample to segments+1 rings -> (pos, radius, tilt, t)."""
    if len(samples) < 2:
        return None
    lengths = [0.0]
    for i in range(1, len(samples)):
        lengths.append(lengths[-1] + (samples[i][0] - samples[i - 1][0]).length)
    total = lengths[-1]
    if total < 1e-6:
        return None
    k = 1.0 + max(0.0, tip_bias)
    out = []
    j = 0
    last = len(samples) - 1
    for i in range(segments + 1):
        s = i / segments
        t = 1.0 - (1.0 - s) ** k
        target = t * total
        while j < last - 1 and lengths[j + 1] < target:
            j += 1
        seg = lengths[j + 1] - lengths[j]
        f = (target - lengths[j]) / seg if seg > EPS else 0.0
        f = min(max(f, 0.0), 1.0)
        a, b = samples[j], samples[j + 1]
        out.append((a[0].lerp(b[0], f), a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f, t))
    return out


def compute_frames(points, center, twist=0.0, tilt_sign=1.0, smooth_passes=2, roll=0.0):
    """Head-oriented frames -> list of (P, T, N, B)."""
    P = [p[0] for p in points]
    n = len(P)
    T = []
    for i in range(n):
        if i == 0:
            d = P[1] - P[0]
        elif i == n - 1:
            d = P[-1] - P[-2]
        else:
            d = P[i + 1] - P[i - 1]
        if d.length < EPS:
            d = T[-1].copy() if T else Vector((0.0, 0.0, -1.0))
        T.append(d.normalized())

    N = []
    prev = None
    for i in range(n):
        r = center.radial(P[i])
        v = r - T[i] * r.dot(T[i])
        if v.length < 1e-4:
            if prev is not None:
                v = prev - T[i] * prev.dot(T[i])
            if v.length < 1e-4:
                v = T[i].orthogonal()
        v.normalize()
        N.append(v)
        prev = v

    for _ in range(smooth_passes):
        sm = []
        for i in range(n):
            v = N[max(i - 1, 0)] + N[i] * 2.0 + N[min(i + 1, n - 1)]
            v = v - T[i] * v.dot(T[i])
            sm.append(v.normalized() if v.length > EPS else N[i])
        N = sm

    frames = []
    for i in range(n):
        ang = points[i][2] * tilt_sign + twist * points[i][3] + roll * tilt_sign
        Ni = N[i]
        if abs(ang) > 1e-6:
            Ni = Ni * math.cos(ang) + T[i].cross(Ni) * math.sin(ang)
        frames.append((P[i], T[i], Ni, T[i].cross(Ni).normalized()))
    return frames


# --------------------------------------------------------------------------
# shape functions
# --------------------------------------------------------------------------

def width_factor(t, root_ratio, bulge_pos, tip_shape, tip_min=0.0, cut_taper=None):
    """Anime lock silhouette: root -> swell -> tip.

    cut_taper (blunt-cut 'ぱっつん' tips): keep the width to the end, only
    narrowing linearly by that fraction."""
    b = min(max(bulge_pos, 0.0), 0.95)
    if t < b and b > 1e-4:
        s = t / b
        w = root_ratio + (1.0 - root_ratio) * math.sin(s * math.pi * 0.5)
    else:
        s = (t - b) / (1.0 - b) if b < 1.0 else 1.0
        s = min(max(s, 0.0), 1.0)
        if cut_taper is not None:
            return max(0.05, 1.0 - cut_taper * s)
        w = max(0.0, 1.0 - s ** max(tip_shape, 0.05))
    return max(w, tip_min)


CENTER_COLUMN_PROFILES = ('RIDGE', 'TRIANGLE')


def profile_funcs(kind, curvature, thickness):
    """Return (mid(x), env(x), back_ratio) for a cross-section type.

    mid: arch of the section's middle line, env: thickness envelope (0 at
    the outer edges). back_ratio splits the thickness between the front
    (2 - r) and the back (r): 1 = symmetric, 0 = flat back (half circle,
    triangle)."""
    if kind == 'RIDGE':
        def mid(x):
            return curvature * (1.0 - abs(x))
    else:
        def mid(x):
            return curvature * (1.0 - x * x)
    back = 1.0
    if kind in ('ROUND', 'HALF'):
        def env(x):
            return math.sqrt(max(0.0, 1.0 - x * x))
        if kind == 'HALF':
            back = 0.0
    elif kind in ('RIDGE', 'TRIANGLE'):
        def env(x):
            return 1.0 - abs(x)
        if kind == 'TRIANGLE':
            back = 0.0
    elif kind == 'TRAPEZOID':
        def env(x):
            return min(1.0, (1.0 - abs(x)) / 0.4)
        back = 0.25
    elif kind == 'FLAT':
        def env(x):
            return 1.0 - x ** 4
    else:  # LENS / CRESCENT
        def env(x):
            return 1.0 - x * x
    return mid, env, back


# --------------------------------------------------------------------------
# geometry container
# --------------------------------------------------------------------------

class Geo:
    """Flat mesh arrays; per-vertex attributes + per-corner UVs."""

    def __init__(self):
        self.verts = []
        self.faces = []
        self.face_uv = []      # per face: tuple of (u, v) per corner
        self.face_kind = []    # per face: 'F' front, 'B' back, 'S' side band / cap / wall
        self.t = []            # root -> tip 0..1
        self.inner = []        # 1 = inner / back side
        self.head = []         # (azimuth, height, t)
        self.normals = []      # custom normal per vertex

    def extend(self, other):
        off = len(self.verts)
        self.verts += other.verts
        self.faces += [tuple(i + off for i in f) for f in other.faces]
        self.face_uv += other.face_uv
        self.face_kind += other.face_kind
        self.t += other.t
        self.inner += other.inner
        self.head += other.head
        self.normals += other.normals

    def transform(self, matrix):
        m3 = matrix.to_3x3()
        try:
            nm = m3.inverted().transposed()
        except ValueError:
            nm = m3
        self.verts = [matrix @ v for v in self.verts]
        self.normals = [(nm @ n).normalized() for n in self.normals]

    def add_face(self, f, uv, kind='S'):
        self.faces.append(tuple(f))
        self.face_uv.append(tuple(uv))
        self.face_kind.append(kind)

    def seam_edges(self):
        """Edges between the front surface and everything else (back, bevel
        bands, caps, walls): cutting there unwraps the front as one island
        and the back (+ bands) as another."""
        kinds = {}
        for f, kind in zip(self.faces, self.face_kind):
            for a in range(len(f)):
                u, v = f[a], f[(a + 1) % len(f)]
                kinds.setdefault((u, v) if u < v else (v, u), set()).add(kind)
        return {e for e, ks in kinds.items() if 'F' in ks and len(ks) > 1}

    def vertex_normals(self):
        acc = [Vector((0.0, 0.0, 0.0)) for _ in self.verts]
        for f in self.faces:
            n = newell([self.verts[i] for i in f])
            for i in f:
                acc[i] += n
        return acc


# --------------------------------------------------------------------------
# parameters
# --------------------------------------------------------------------------

class StrandParams:
    length_segments = 12
    profile_segments = 4
    tip_bias = 0.3
    profile = 'LENS'
    backface = 'CLOSED'       # CLOSED / FLIPPED / SINGLE
    width = 0.03
    thickness = 0.25
    curvature = 0.12
    root_ratio = 0.7
    bulge_pos = 0.25
    tip_shape = 1.4
    tip_mode = 'POINT'        # POINT / BLUNT
    tip_min = 0.12
    root_cap = False
    twist = 0.0
    root_extend = 0.0
    flip_offset = 0.0003
    normal_mode = 'VOLUME'    # NONE / VOLUME / PROXY
    normal_blend = 0.6
    outline = False
    outline_width = 0.0012
    outline_tip_ratio = 0.15
    outline_taper_start = 0.5
    # lateral connection (横連結)
    connect = False
    connect_ratio = 1.8
    split = 0.55
    notch_soft = 0.5
    seam_thickness = 0.6
    order = 'AZIMUTH'         # AZIMUTH / CURVE
    connect_style = 'SMOOTH'  # SMOOTH (なめらか) / GROOVE (毛束の溝)
    smooth_iterations = 4
    cut_taper = 0.05          # 'CUT' tips: narrowing towards the hem
    cut_enable = False        # trim every guide at a horizontal plane
    cut_height = 0.2          # head-local height of that plane (-1..1)
    bevel = 0.3               # closed edges: side band height (fraction of thickness), 0 = knife edge
    # loop density (HairHarpy-style equidistribution)
    bend_density = 0.5
    # volume / layering (sizes relative to the head radius R)
    lift = 0.0                # push the whole group off the head
    puff = 0.0                # bulge outward along the length
    puff_pos = 0.6
    tip_flip = 0.0            # >0 flicks tips outward, <0 tucks them in
    roll = 0.0                # stand the locks up (rotate around the guide)
    layers = 'NONE'           # NONE / FRONT / BACK / BOTH
    layer_gap = 0.08
    layer_shift = 0.5         # lateral stagger, fraction of the lock width
    layer_front_len = 0.8
    layer_back_len = 0.92
    layer_width = 0.85
    layer_flip = 0.12
    # curl / wave
    curl_turns = 0.0
    curl_radius = 0.5
    curl_start = 0.3
    wave_amp = 0.0
    wave_freq = 2.0
    # branching (分岐毛)
    branches = 1
    branch_jitter = 0
    branch_pos = 0.5
    branch_spread = 0.25
    branch_length_var = 0.25
    branch_pattern = 'CENTER'  # CENTER / OUTER / RANDOM / EVEN
    seed = 0

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


# --------------------------------------------------------------------------
# locks
# --------------------------------------------------------------------------

def _azimuth_rev(center, P, B, w):
    """True if +B points towards decreasing head azimuth."""
    a0 = center.head_coord(P)[0]
    a1 = center.head_coord(P + B * max(w, 1e-4))[0]
    return ((a1 - a0 + 0.5) % 1.0 - 0.5) < 0.0


class Lock:
    """One pointed lock: rings of frames + widths + arc params."""
    __slots__ = ("frames", "widths", "ts", "rev", "unit", "sub", "nsub")

    def __init__(self, frames, widths, ts, rev):
        self.frames = frames
        self.widths = widths
        self.ts = ts
        self.rev = rev
        self.unit = 0       # id of the guide (Unit) this lock grew from
        self.sub = 0        # index among its branch siblings (order space)
        self.nsub = 1


class Unit:
    """Everything grown from one guide spline: 1 lock, or N branched sub-locks
    (ordered by increasing azimuth). Sub-locks are joined up to branch_row."""
    __slots__ = ("locks", "mid", "az", "mid_width", "order", "branch_row", "src", "mirrored")

    def __init__(self, locks, mid, az, mid_width, order, branch_row):
        # ordering / adjacency use the middle of the guide, not the root, so
        # locks growing from one crown whorl still sort and connect correctly
        self.locks = locks
        self.mid = mid
        self.az = az
        self.mid_width = mid_width
        self.order = order
        self.branch_row = branch_row
        self.src = int(order)      # index of the guide spline
        self.mirrored = False


def _eval_at(samples, lengths, total, t):
    target = min(max(t, 0.0), 1.0) * total
    lo, hi = 0, len(lengths) - 1
    while hi - lo > 1:
        m = (lo + hi) // 2
        if lengths[m] < target:
            lo = m
        else:
            hi = m
    seg = lengths[hi] - lengths[lo]
    f = (target - lengths[lo]) / seg if seg > EPS else 0.0
    a, b = samples[lo], samples[hi]
    return a[0].lerp(b[0], f), a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f


def _arc(samples):
    lengths = [0.0]
    for i in range(1, len(samples)):
        lengths.append(lengths[-1] + (samples[i][0] - samples[i - 1][0]).length)
    return lengths, lengths[-1]


def _branch_count(prm, rng):
    n = max(1, int(prm.branches))
    j = int(prm.branch_jitter)
    if j > 0:
        n = max(1, n + rng.randint(-j, j))
    return n


# --------------------------------------------------------------------------
# loop layout: equidistribution of a density along the guide
# --------------------------------------------------------------------------

def density_profile(samples_list, prm, M=64):
    """Loop density f(t) on M+1 samples, shared by every guide of a group.

    f = (1 + bend_density * bend) * (1 + 3 * tip_bias * tip_zone), where bend
    is the turning angle per loop step (30 deg = 1), averaged over the
    group's guides in relative t, so laterally joined guides keep aligned
    rows while bends and tips get more loops."""
    segs = max(2, prm.length_segments)
    acc = [0.0] * (M + 1)
    cnt = 0
    for smp in samples_list:
        lengths, total = _arc(smp)
        if total < 1e-6:
            continue
        pts = [_eval_at(smp, lengths, total, i / M)[0] for i in range(M + 1)]
        for i in range(1, M):
            a = pts[i] - pts[i - 1]
            b = pts[i + 1] - pts[i]
            if a.length > EPS and b.length > EPS:
                acc[i] += a.angle(b) * (M / segs) / (math.pi / 6.0)
        cnt += 1
    bend = [min(1.0, x / cnt) for x in acc] if cnt else acc
    sm = []
    for i in range(M + 1):
        lo, hi = max(0, i - 3), min(M, i + 3)
        sm.append(sum(bend[lo:hi + 1]) / (hi - lo + 1))
    return [(1.0 + prm.bend_density * sm[i]) * (1.0 + 3.0 * prm.tip_bias * smoothstep(0.5, 1.0, i / M))
            for i in range(M + 1)]


def _layout(f, t0, t1, n, K=200):
    """n+1 params from t0 to t1 with equal integral of the density f."""
    M = len(f) - 1

    def fv(t):
        x = min(max(t, 0.0), 1.0) * M
        i = min(int(x), M - 1)
        a = x - i
        return f[i] * (1.0 - a) + f[i + 1] * a
    ts = [t0 + (t1 - t0) * k / K for k in range(K + 1)]
    G = [0.0]
    for k in range(1, K + 1):
        G.append(G[-1] + 0.5 * (fv(ts[k - 1]) + fv(ts[k])) * (ts[k] - ts[k - 1]))
    out = [t0]
    j = 0
    for m in range(1, n):
        target = G[-1] * m / n
        while j < K - 1 and G[j + 1] < target:
            j += 1
        d = G[j + 1] - G[j]
        a = (target - G[j]) / d if d > EPS else 0.0
        out.append(ts[j] + a * (ts[j + 1] - ts[j]))
    out.append(t1)
    return out


def _puff_bump(t, p):
    """0 at the root and the tip, 1 at p."""
    p = min(max(p, 0.05), 0.95)
    if t <= p:
        return math.sin(0.5 * math.pi * t / p) ** 2
    return math.cos(0.5 * math.pi * (t - p) / (1.0 - p)) ** 2


def deform_samples(samples, center, prm, tilt_sign=1.0, n=160, layer=None):
    """Shape the guide: lift / puff / tip flip (along the head normal),
    layer offset + stagger, curl (helix) and wave (side to side).

    layer = (side, length, flip): side -1 back / +1 front, length cuts the
    guide, flip adds to tip_flip. Returns a dense polyline; rows are then
    spread along the deformed path."""
    curl = prm.curl_turns > 0.0 and prm.curl_radius > 0.0
    wave = prm.wave_amp > 0.0 and prm.wave_freq > 0.0
    side, length, lflip = layer if layer else (0, 1.0, 0.0)
    flip = prm.tip_flip + lflip
    shaped = (prm.lift != 0.0 or prm.puff != 0.0 or flip != 0.0 or side != 0 or length < 0.999)
    if not (curl or wave or shaped):
        return samples
    lengths, total = _arc(samples)
    if total < 1e-6:
        return samples
    tmax = min(max(length, 0.05), 1.0)
    pts = [(*_eval_at(samples, lengths, total, tmax * i / n), i / n) for i in range(n + 1)]
    frames = compute_frames(pts, center, twist=0.0, tilt_sign=tilt_sign)
    W = prm.width
    R = center.radius
    s0 = min(max(prm.curl_start, 0.0), 0.95)
    out = []
    for (p, rad, tilt, t), (_P, _T, N, B) in zip(pts, frames):
        off = Vector((0.0, 0.0, 0.0))
        if shaped:
            ramp = smoothstep(0.0, 0.15, t)        # roots stay on the scalp
            h = prm.lift * ramp + prm.puff * _puff_bump(t, prm.puff_pos)
            h += flip * smoothstep(0.55, 1.0, t) ** 2
            h += side * prm.layer_gap * ramp
            off += N * (h * R)
            if side:
                off += B * (side * prm.layer_shift * W)
        if curl:
            ramp = smoothstep(s0, min(1.0, s0 + 0.15), t)
            ph = 2.0 * math.pi * prm.curl_turns * max(0.0, t - s0) / (1.0 - s0)
            off += (N * math.cos(ph) + B * math.sin(ph)) * (prm.curl_radius * W * ramp)
        if wave:
            off += B * (prm.wave_amp * W * math.sin(2.0 * math.pi * prm.wave_freq * t)
                        * smoothstep(0.0, 0.25, t))
        out.append((p + off, rad, tilt))
    return out


def make_unit(samples, center, prm, mirrored=False, order=0.0, rng=None, layer=None):
    import random
    rng = rng or random.Random(0)
    if prm.root_extend > 0.0 and len(samples) >= 2:
        p0, r0, t0 = samples[0]
        d = p0 - samples[1][0]
        if d.length > EPS:
            samples = [(p0 + d.normalized() * prm.root_extend, r0, t0)] + list(samples)
    tilt_sign = -1.0 if mirrored else 1.0
    samples = deform_samples(samples, center, prm, tilt_sign, layer=layer)
    lengths, total = _arc(samples)
    if total < 1e-6:
        return None
    segs = max(2, prm.length_segments)
    if prm.curl_turns > 0.0 and prm.curl_radius > 0.0:
        segs = max(segs, int(math.ceil(prm.curl_turns * 10.0)) + 4)   # enough rows per turn
    point_tip = prm.tip_mode == 'POINT'
    tip_min = 0.0 if point_tip else prm.tip_min
    cut = prm.cut_taper if prm.tip_mode == 'CUT' else None
    if cut is not None:
        tip_min = 0.0
    dens = getattr(prm, "density_profile", None)
    if not dens:
        dens = density_profile([samples], prm)
    nb = _branch_count(prm, rng)
    if segs < 4:
        nb = 1

    def parent_w(t, rad):
        return prm.width * width_factor(t, prm.root_ratio, prm.bulge_pos, prm.tip_shape, tip_min,
                                        cut) * max(rad, 0.0)

    tb = min(max(prm.branch_pos, 0.05), 0.9)
    n_pre = min(max(int(round(tb * segs)), 1), segs - 2) if segs >= 4 else 1
    n_post = segs - n_pre
    # every guide of a group that may branch uses the same row layout
    # (uniform up to the branch point, tip-biased after it), so rows line up
    # across laterally joined guides whether or not each one branches
    branchy = segs >= 4 and (prm.branches > 1 or prm.branch_jitter > 0)

    def row_ts(t_end):
        if not branchy:
            return _layout(dens, 0.0, 1.0, segs)
        pre = _layout(dens, 0.0, tb, n_pre)
        post = _layout(dens, tb, 1.0, n_post)
        k = (t_end - tb) / (1.0 - tb)
        return pre + [tb + (x - tb) * k for x in post[1:]]

    # ---- single lock -------------------------------------------------------
    if nb == 1:
        tl = row_ts(1.0)
        pts = [(*_eval_at(samples, lengths, total, t), t) for t in tl]
        frames = compute_frames(pts, center, twist=prm.twist, tilt_sign=tilt_sign, roll=prm.roll)
        widths = [parent_w(p[3], p[1]) for p in pts]
        P, _T, _N, B = frames[len(frames) // 3]
        lk = Lock(frames, widths, tl, _azimuth_rev(center, P, B, widths[len(frames) // 3]))
        im = len(frames) // 2
        Pm = frames[im][0]
        return Unit([lk], Pm, center.head_coord(Pm)[0], widths[im], order, -1)

    # ---- branched lock -------------------------------------------------------
    # parent frames on a fine grid, interpolated at any t
    fine = 64
    fpts = [(*_eval_at(samples, lengths, total, i / fine), i / fine) for i in range(fine + 1)]
    fframes = compute_frames(fpts, center, twist=prm.twist, tilt_sign=tilt_sign, roll=prm.roll)

    def frame_at(t):
        f = min(max(t, 0.0), 1.0) * fine
        i = min(int(f), fine - 1)
        a = f - i
        A, C = fframes[i], fframes[i + 1]
        P = A[0].lerp(C[0], a)
        B = A[3].lerp(C[3], a).normalized()
        rad = fpts[i][1] + (fpts[i + 1][1] - fpts[i][1]) * a
        tilt = fpts[i][2] + (fpts[i + 1][2] - fpts[i][2]) * a
        return P, B, rad, tilt

    Pb, Bb, radb, _ = frame_at(tb)
    Wb = parent_w(tb, radb)
    xs = [-1.0 + (2.0 * k + 1.0) / nb for k in range(nb)]
    xmax = max(abs(x) for x in xs) or 1.0
    var = max(0.0, min(prm.branch_length_var, 0.95))
    rev = _azimuth_rev(center, *frame_at(0.3)[:2], parent_w(0.3, 1.0))

    locks = []
    for k, xk in enumerate(xs):
        if prm.branch_pattern == 'CENTER':
            f = abs(xk) / xmax
        elif prm.branch_pattern == 'OUTER':
            f = 1.0 - abs(xk) / xmax
        elif prm.branch_pattern == 'RANDOM':
            f = rng.random()
        else:
            f = 0.0
        # length variation is a fraction of the part after the branch point,
        # and every branch keeps at least 35% of it
        t_end = 1.0 - min(var, 0.65) * f * (1.0 - tb)
        blen = (t_end - tb) / (1.0 - tb)
        tl = row_ts(t_end)
        pts, widths = [], []
        for t in tl:
            P, B, rad, tilt = frame_at(t)
            if t <= tb + 1e-9:
                W = parent_w(t, rad)
                off = xk * W * 0.5
                w = W / nb
            else:
                u = (t - tb) / max(t_end - tb, 1e-6)
                off = xk * Wb * 0.5 + xk * prm.branch_spread * Wb * blen * (u ** 1.5)
                w = Wb / nb * width_factor(u, 1.0, 0.25, prm.tip_shape, tip_min, cut)
            pts.append((P + B * off, rad, tilt, t))
            widths.append(w)
        frames = compute_frames(pts, center, twist=prm.twist, tilt_sign=tilt_sign, smooth_passes=1,
                                roll=prm.roll)
        locks.append(Lock(frames, widths, tl, rev))
    if rev:
        locks.reverse()
    for idx, lk in enumerate(locks):
        lk.sub, lk.nsub = idx, len(locks)
    Pm, _Bm, radm, _ = frame_at(0.5)
    return Unit(locks, Pm, center.head_coord(Pm)[0], parent_w(0.5, radm), order, n_pre)


def cluster_units(units, prm):
    """Group units into laterally connected runs (横連結)."""
    if not prm.connect:
        return [[u] for u in units]
    if prm.order == 'CURVE':
        seq = sorted(units, key=lambda u: u.order)
    else:
        seq = sorted(units, key=lambda u: u.az)
        if len(seq) > 2:
            gaps = [((seq[(i + 1) % len(seq)].az - seq[i].az) % 1.0, i) for i in range(len(seq))]
            _g, i = max(gaps)
            seq = seq[i + 1:] + seq[:i + 1]
    runs = [[seq[0]]] if seq else []
    for u in seq[1:]:
        prev = runs[-1][-1]
        limit = prm.connect_ratio * 0.5 * (prev.mid_width + u.mid_width)
        if (u.mid - prev.mid).length <= limit:
            runs[-1].append(u)
        else:
            runs.append([u])
    return runs


def manual_runs(units, links):
    """Chains of units joined by manual links (spline a, spline b, t0, t1).

    Returns [(units_in_order, {(i, i+1): (t0, t1)})]. Units not touched by a
    link stay on their own. Links only join units of the same mirror side."""
    by_key = {(u.src, u.mirrored): idx for idx, u in enumerate(units)}
    parent = list(range(len(units)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    pair_range = {}
    for a, b, t0, t1 in links:
        for mir in (False, True):
            ia, ib = by_key.get((a, mir)), by_key.get((b, mir))
            if ia is None or ib is None or ia == ib:
                continue
            pair_range[frozenset((ia, ib))] = (t0, t1)
            parent[find(ia)] = find(ib)
    comps = {}
    for idx in range(len(units)):
        comps.setdefault(find(idx), []).append(idx)
    runs = []
    for members in comps.values():
        seq = sorted(members, key=lambda i: units[i].az)
        if len(seq) > 2:
            gaps = [((units[seq[(j + 1) % len(seq)]].az - units[seq[j]].az) % 1.0, j) for j in range(len(seq))]
            _g, j = max(gaps)
            seq = seq[j + 1:] + seq[:j + 1]
        # split the chain wherever two neighbours are not linked directly
        cur = [seq[0]]
        ranges = {}
        for i in seq[1:]:
            rng = pair_range.get(frozenset((cur[-1], i)))
            if rng is None:
                runs.append(([units[k] for k in cur], ranges))
                cur, ranges = [i], {}
            else:
                ranges[len(cur) - 1] = rng
                cur.append(i)
        runs.append(([units[k] for k in cur], ranges))
    return runs


def _band_rows(a, b, t0, t1, rc):
    """Rows of the join band [t0, t1] between two locks."""
    r1 = -1
    r0 = rc
    for i in range(rc):
        if a.ts[i] <= t1 + 1e-9 and b.ts[i] <= t1 + 1e-9:
            r1 = i
    for i in range(rc):
        if a.ts[i] >= t0 - 1e-9 and b.ts[i] >= t0 - 1e-9:
            r0 = i
            break
    if t0 <= 1e-6:
        r0 = 0
    return r0, r1


def _lateral_row(a, b, split, rc):
    row = -1
    for i in range(rc):
        if a.ts[i] <= split + 1e-9 and b.ts[i] <= split + 1e-9:
            row = i
    return row


# --------------------------------------------------------------------------
# sheet builder
# --------------------------------------------------------------------------

def build_sheet(locks, split_rows, center, prm, hair, outline=None, start_rows=None):
    """locks ordered by azimuth; split_rows[b] = last shared row between
    locks[b] and locks[b+1] (-1 = not shared).

    Boundary b is shared over the row band [r0[b], r1[b]]. r0 is the first
    row from which the two locks are properly side by side; above it (e.g.
    where roots converge at the crown) they stay separate and merge at r0,
    closed by a wall quad just like the notch at the tip side."""
    K = len(locks)
    closed = prm.backface == 'CLOSED'
    S = max(1, prm.profile_segments)
    if prm.profile in CENTER_COLUMN_PROFILES and S % 2:
        S += 1
    if closed and S < 2:
        S = 2
    xs = [-1.0 + 2.0 * j / S for j in range(S + 1)]
    mid, env, back_ratio = profile_funcs(prm.profile, prm.curvature, prm.thickness)
    k_front, k_back = 2.0 - back_ratio, back_ratio

    nr = min(len(lk.frames) for lk in locks)
    point_tip = prm.tip_mode == 'POINT'
    rc = nr - 1 if point_tip else nr
    # 'CUT' (ぱっつん) hems may stay joined through the last row
    r1 = [min(r, rc - 1 if prm.tip_mode == 'CUT' else rc - 2) for r in split_rows]
    r0 = []
    smooth_lat = prm.connect_style == 'SMOOTH'
    for b in range(K - 1):
        la, lb = locks[b], locks[b + 1]
        # smooth joins place the seam on a curve through the lock centres, so
        # they only need the centres in order; chord (groove) joins need the
        # locks to actually sit side by side
        min_sep = 0.08 if (smooth_lat or la.unit == lb.unit) else 0.35
        start = r1[b] + 1
        for i in range(r1[b], -1, -1):
            Pa, _Ta, _Na, Ba = la.frames[i]
            Pb, _Tb, _Nb, Bb = lb.frames[i]
            d = (-Ba if la.rev else Ba) + (-Bb if lb.rev else Bb)
            if d.length < EPS:
                break
            sep = (Pb - Pa).dot(d.normalized())
            if sep < min_sep * 0.5 * (la.widths[i] + lb.widths[i]):
                break
            start = i
        # need at least two shared rows when the band does not start at the
        # root, otherwise the root-side and tip-side notches would meet in a
        # single (non-manifold) vertex
        if start_rows is not None and b < len(start_rows):
            start = max(start, start_rows[b])     # manual join band starts here
        if start > r1[b] or (start > 0 and r1[b] - start < 1):
            r1[b] = -1
            start = 0
        r0.append(start)

    def shared_b(b, i):
        return r0[b] <= i <= r1[b]

    def left_shared(k, i):
        return k > 0 and shared_b(k - 1, i)

    def right_shared(k, i):
        return k < K - 1 and shared_b(k, i)

    def notch(b, i):
        """0 = edges merged at the shared boundary, 1 = own edges."""
        if shared_b(b, i):
            return 0.0
        ta, tb_ = locks[b].ts, locks[b + 1].ts

        def tt(r):
            return 0.5 * (ta[r] + tb_[r])
        if r1[b] < 0:
            return 1.0
        if i < r0[b]:
            return 1.0 - smoothstep(tt(0), tt(r0[b]), tt(i))
        t0 = tt(r1[b])
        span = max((tt(rc - 1) - t0) * prm.notch_soft, 1e-4)
        return smoothstep(t0, t0 + span, tt(i))

    same_unit = [locks[b].unit == locks[b + 1].unit for b in range(K - 1)]
    # branch siblings always join smoothly (they are one lock before the
    # branch point); lateral joins are smooth or grooved by style
    smooth_b = [same_unit[b] or smooth_lat for b in range(K - 1)]

    def bw(b, i):
        """merge weight of boundary b at row i (1 = fully joined)."""
        if b < 0 or b >= K - 1 or r1[b] < 0:
            return 0.0
        return 1.0 - notch(b, i)

    # ---- edges in order space ------------------------------------------------
    oL = [[None] * rc for _ in range(K)]
    oR = [[None] * rc for _ in range(K)]
    for k, lk in enumerate(locks):
        for i in range(rc):
            P, _T, _N, B = lk.frames[i]
            hw = lk.widths[i] * 0.5
            a, b = P - B * hw, P + B * hw
            if lk.rev:
                a, b = b, a
            oL[k][i], oR[k][i] = a, b
    eL = [row[:] for row in oL]
    eR = [row[:] for row in oR]
    for b in range(K - 1):
        la, lb = locks[b], locks[b + 1]
        for i in range(rc):
            wa, wb = la.widths[i], lb.widths[i]
            f = wa / (wa + wb) if wa + wb > EPS else 0.5
            if smooth_b[b] and i >= r0[b]:
                # smooth: boundary on a Catmull-Rom curve through the lock
                # centres, so it follows the head instead of sagging on a chord
                Pa, Pb = la.frames[i][0], lb.frames[i][0]
                A0 = locks[b - 1].frames[i][0] if b > 0 and bw(b - 1, i) > 0 else Pa * 2.0 - Pb
                A3 = locks[b + 2].frames[i][0] if b + 2 < K and bw(b + 1, i) > 0 else Pb * 2.0 - Pa
                m = _catmull(A0, Pa, Pb, A3, f)
            else:
                # sweep left -> right and split the span from this lock's
                # (already resolved) left edge to the neighbour's right edge by
                # width ratio: never folds even where locks overlap
                m = eL[b][i].lerp(oR[b + 1][i], f)
            s_ = notch(b, i)
            eR[b][i] = m.lerp(oR[b][i], s_)
            eL[b + 1][i] = m.lerp(oL[b + 1][i], s_)

    def is_shared_col(k, i, c):
        return (c == 0 and left_shared(k, i)) or (c == S and right_shared(k, i))

    bevel = closed and prm.bevel > 1e-6

    def closed_edge(k, i, c):
        return closed and c in (0, S) and not is_shared_col(k, i, c)

    def key(side, k, i, c):
        if c == 0 and left_shared(k, i):
            return (side, i, k - 1, S)
        if not bevel and closed_edge(k, i, c):
            return ('E', i, k, c)          # knife edge: front and back meet
        return (side, i, k, c)

    acc = {}
    order = []
    meta = {}
    relax_w = {}

    def put(kk, co, t, inner, rw=0.0):
        if kk in acc:
            acc[kk][0] += co
            acc[kk][1] += 1
        else:
            acc[kk] = [co.copy(), 1]
            order.append(kk)
            meta[kk] = (t, inner)
        relax_w[kk] = max(relax_w.get(kk, 0.0), rw)

    g_seam = prm.seam_thickness
    mid0 = mid(0.0)
    half_t = 0.5 * prm.thickness

    def prof(xx, W, W_nb_l, W_nb_r, wl, wr):
        """(mid offset, half thickness) for profile coordinate xx in a span
        of width W whose left/right lateral joins have weights wl / wr."""
        e = env(xx)
        mm = mid(xx)
        wlat = wl if xx < 0.0 else wr
        if wlat > 0.0:
            if smooth_lat:
                e += (1.0 - e) * wlat           # full thickness at the seam
                mm += (mid0 - mm) * wlat        # no arch dip at the seam
                W_nb = W_nb_l if xx < 0.0 else W_nb_r
                W = W + (0.5 * (W + W_nb) - W) * abs(xx) * wlat
            else:
                e += (g_seam + (1.0 - g_seam) * e - e) * wlat
        return mm * W, half_t * e * W

    unit_locks = {}
    for k, lk in enumerate(locks):
        unit_locks.setdefault(lk.unit, []).append(k)

    for i in range(rc):
        wk = [(eR[k][i] - eL[k][i]).length for k in range(K)]
        Wu = {u: sum(wk[k] for k in ks) for u, ks in unit_locks.items()}
        for k, lk in enumerate(locks):
            P, _T, Nk, _B = lk.frames[i]
            L, R = eL[k][i], eR[k][i]
            t = lk.ts[i]
            ks = unit_locks[lk.unit]
            kf, kl = ks[0], ks[-1]
            # lateral join weights (own span / unit span)
            lat_l = bw(k - 1, i) if k > 0 and not same_unit[k - 1] else 0.0
            lat_r = bw(k, i) if k < K - 1 and not same_unit[k] else 0.0
            ulat_l = bw(kf - 1, i) if kf > 0 else 0.0
            ulat_r = bw(kl, i) if kl < K - 1 else 0.0
            wnb_l = wk[k - 1] if k > 0 else wk[k]
            wnb_r = wk[k + 1] if k < K - 1 else wk[k]
            Wnb_l = Wu[locks[kf - 1].unit] if kf > 0 else Wu[lk.unit]
            Wnb_r = Wu[locks[kl + 1].unit] if kl < K - 1 else Wu[lk.unit]
            # branch join weights -> blend own profile with the unit profile
            if lk.nsub > 1:
                bl = bw(k - 1, i) if k > kf else None
                br = bw(k, i) if k < kl else None
                bl = br if bl is None else bl
                br = bl if br is None else br
            else:
                bl = br = 1.0
            # smooth-join weights for the base curve and the normal
            sl = bw(k - 1, i) if k > 0 and smooth_b[k - 1] else 0.0
            sr = bw(k, i) if k < K - 1 and smooth_b[k] else 0.0
            nl = bw(k - 1, i) if k > 0 else 0.0
            nr_ = bw(k, i) if k < K - 1 else 0.0
            A_l = L * 2.0 - P
            if k > 0 and sl > 0.0:
                A_l = A_l.lerp(locks[k - 1].frames[i][0], sl)
            A_r = R * 2.0 - P
            if k < K - 1 and sr > 0.0:
                A_r = A_r.lerp(locks[k + 1].frames[i][0], sr)
            N_l = (Nk + locks[k - 1].frames[i][2]).normalized() if k > 0 else Nk
            N_r = (Nk + locks[k + 1].frames[i][2]).normalized() if k < K - 1 else Nk
            rw = max(ulat_l, ulat_r) if smooth_lat else 0.0
            for c in range(S + 1):
                x = xs[c]
                if x < 0.0:
                    base = _catmull(A_l, L, P, R, x + 1.0)
                    N = Nk.lerp(N_l, -x * nl)
                else:
                    base = _catmull(L, P, R, A_r, x)
                    N = Nk.lerp(N_r, x * nr_)
                N = N.normalized() if N.length > EPS else Nk
                my_o, he_o = prof(x, wk[k], wnb_l, wnb_r, lat_l, lat_r)
                xu = -1.0 + (2.0 * lk.sub + x + 1.0) / lk.nsub
                my_u, he_u = prof(xu, Wu[lk.unit], Wnb_l, Wnb_r, ulat_l, ulat_r)
                wbv = bl + (br - bl) * (x + 1.0) * 0.5
                ym = my_o + (my_u - my_o) * wbv
                ye = he_o + (he_u - he_o) * wbv
                if bevel and closed_edge(k, i, c):
                    # bevel: the outer edge keeps a small side band
                    ye = half_t * prm.bevel * wk[k]
                if closed:
                    put(key('F', k, i, c), base + N * (ym + ye * k_front), t, 0.0, rw)
                    put(key('B', k, i, c), base + N * (ym - ye * k_back), t, 1.0, rw)
                else:
                    put(key('F', k, i, c), base + N * ym, t, 0.0, rw)
    if point_tip:
        for k, lk in enumerate(locks):
            put(('T', k), lk.frames[nr - 1][0], lk.ts[nr - 1], 0.0)

    g = Geo()
    index = {}
    for kk in order:
        co = acc[kk][0] / acc[kk][1]
        t, inner = meta[kk]
        if kk[0] == 'E':
            inner = 0.0
        index[kk] = len(g.verts)
        g.verts.append(co)
        g.t.append(t)
        g.inner.append(inner)
        g.head.append((0.0, 0.0, t))

    def V(side, k, i, c):
        return index[key(side, k, i, c)]

    band_faces = []

    def cap_strip(k, i):
        """Close a ring with a strip of quads between its front and back rows
        (triangles where front and back share an edge vertex)."""
        lk = locks[k]
        cols = list(range(S + 1)) if not lk.rev else list(range(S, -1, -1))
        ids = []
        v = lk.ts[i]
        for j in range(S):
            f = [V('F', k, i, cols[j]), V('F', k, i, cols[j + 1]),
                 V('B', k, i, cols[j + 1]), V('B', k, i, cols[j])]
            uniq = []
            for q in f:
                if q not in uniq:
                    uniq.append(q)
            if len(uniq) < 3:
                continue
            ids.append(len(g.faces))
            g.add_face(uniq, [(j / S, v)] * len(uniq))
        return ids

    for k, lk in enumerate(locks):
        cols = list(range(S + 1)) if not lk.rev else list(range(S, -1, -1))
        us = [j / S for j in range(S + 1)]
        ts = lk.ts
        for i in range(rc - 1):
            v0, v1 = ts[i], ts[i + 1]
            for j in range(S):
                ca, cb = cols[j], cols[j + 1]
                ua, ub = us[j], us[j + 1]
                g.add_face((V('F', k, i, ca), V('F', k, i, cb), V('F', k, i + 1, cb), V('F', k, i + 1, ca)),
                           ((ua, v0), (ub, v0), (ub, v1), (ua, v1)), 'F')
                if closed:
                    g.add_face((V('B', k, i, cb), V('B', k, i, ca), V('B', k, i + 1, ca), V('B', k, i + 1, cb)),
                               ((ub, v0), (ua, v0), (ua, v1), (ub, v1)), 'B')
            if bevel:
                # bevel band along each outer (not joined) edge
                for c, u in ((0, 0.0), (S, 1.0)):
                    if closed_edge(k, i, c) and closed_edge(k, i + 1, c):
                        f = [V('F', k, i, c), V('F', k, i + 1, c), V('B', k, i + 1, c), V('B', k, i, c)]
                        band_faces.append(len(g.faces))
                        g.add_face(f, [(u, v0), (u, v1), (u, v1), (u, v0)])
        i = rc - 1
        vt = ts[i]
        loop = [(V('F', k, i, cols[j]), us[j], 'F') for j in range(S + 1)]
        if closed:
            back = range(S, -1, -1) if bevel else range(S - 1, 0, -1)
            loop += [(V('B', k, i, cols[j]), us[j], 'B') for j in back]
        if point_tip:
            tip = index[('T', k)]
            span = len(loop) if closed else len(loop) - 1
            for j in range(span):
                a, ua, sa = loop[j]
                b, ub, sb = loop[(j + 1) % len(loop)]
                kind = 'F' if sa == sb == 'F' else ('B' if sa == sb == 'B' else 'S')
                g.add_face((a, b, tip), ((ua, vt), (ub, vt), (0.5, ts[nr - 1])), kind)
        elif closed:
            band_faces.extend(cap_strip(k, i))

    # walls where two joined locks separate (tip side) or merge (root side)
    wall_start = len(g.faces)
    if closed:
        for b in range(K - 1):
            if r1[b] < 0:
                continue
            T = locks[b].frames[r1[b]][1] + locks[b + 1].frames[r1[b]][1]
            for i, i1, sgn in ((r1[b], r1[b] + 1, 1.0), (r0[b], r0[b] - 1, -1.0)):
                if i1 < 0 or i1 >= rc:
                    continue
                Fb, Bb = V('F', b, i, S), V('B', b, i, S)
                v0, v1 = locks[b].ts[i], locks[b].ts[i1]
                if not bevel:
                    E1, E2 = V('F', b, i1, S), V('F', b + 1, i1, 0)
                    quads = [[Fb, E2, Bb, E1]]
                else:
                    # the two separated edges each have a front and a back
                    # vertex: the hexagonal gap closes with two quads
                    F1, B1 = V('F', b, i1, S), V('B', b, i1, S)
                    F2, B2 = V('F', b + 1, i1, 0), V('B', b + 1, i1, 0)
                    quads = [[Fb, F2, B2, Bb], [Fb, Bb, B1, F1]]
                for f in quads:
                    n = newell([g.verts[q] for q in f])
                    if n.dot(T) * sgn < 0.0:
                        f.reverse()
                    g.add_face(f, ((0.5, v0), (0.5, v1), (0.5, v1), (0.5, v0)))

    if closed and prm.root_cap:
        for k in range(K):
            band_faces.extend(cap_strip(k, 0))

    _orient_like_neighbours(g, band_faces + list(range(wall_start, len(g.faces))))

    # ---- smooth lateral joins: shrink-free Taubin relaxation -----------------
    if smooth_lat and prm.smooth_iterations > 0 and any(w > 0.0 for w in relax_w.values()):
        _relax(g, [relax_w.get(kk, 0.0) if kk[0] in ('F', 'B') else 0.0 for kk in order],
               prm.smooth_iterations)

    # ---- normals ---------------------------------------------------------------
    raw = g.vertex_normals()
    geo_n = []
    normals = []
    for idx, n in enumerate(raw):
        n = n.normalized() if n.length > EPS else Vector((0.0, 0.0, 1.0))
        geo_n.append(n)
        if prm.normal_mode == 'VOLUME' and prm.normal_blend > 0.0:
            r = center.radial(g.verts[idx])
            if n.dot(r) < 0.0:
                r = -r
            m = n.lerp(r, prm.normal_blend)
            n = m.normalized() if m.length > EPS else r
        normals.append(n)
    g.normals = normals

    if outline is not None and prm.outline and prm.outline_width > 0.0:
        outline.extend(build_hull(g, geo_n, prm, closed))

    if prm.backface == 'FLIPPED':
        nv = len(g.verts)
        faces = list(g.faces)
        fuv = list(g.face_uv)
        for idx in range(nv):
            g.verts.append(g.verts[idx] - geo_n[idx] * prm.flip_offset)
            g.t.append(g.t[idx])
            g.inner.append(1.0)
            g.head.append(g.head[idx])
        g.normals = normals + [-n for n in normals]
        for f, uv in zip(faces, fuv):
            g.add_face([q + nv for q in reversed(f)], list(reversed(uv)), 'B')

    hair.extend(g)


def _orient_like_neighbours(g, face_ids):
    """Flip the given faces (walls, caps) so that every edge they share with
    the rest of the mesh is walked in the opposite direction (consistent
    winding), instead of guessing from the lock direction."""
    face_ids = list(face_ids)
    if not face_ids:
        return
    todo = set(face_ids)
    directed = set()
    for fi, f in enumerate(g.faces):
        if fi in todo:
            continue
        for a in range(len(f)):
            directed.add((f[a], f[(a + 1) % len(f)]))
    for _ in range(len(face_ids) + 1):
        progressed = False
        for fi in sorted(todo):
            f = g.faces[fi]
            same = opp = 0
            for a in range(len(f)):
                e = (f[a], f[(a + 1) % len(f)])
                if e in directed:
                    same += 1
                elif (e[1], e[0]) in directed:
                    opp += 1
            if same == 0 and opp == 0:
                continue
            if same > opp:
                g.faces[fi] = tuple(reversed(f))
                g.face_uv[fi] = tuple(reversed(g.face_uv[fi]))
                f = g.faces[fi]
            for a in range(len(f)):
                directed.add((f[a], f[(a + 1) % len(f)]))
            todo.discard(fi)
            progressed = True
        if not todo or not progressed:
            break


def _relax(g, weights, iterations, lam=0.5, mu=-0.53):
    """Taubin smoothing (no shrinking). Vertices on open borders, tips and
    walls (triangles / notch quads) stay fixed; weights scale the motion."""
    nv = len(g.verts)
    nbrs = [set() for _ in range(nv)]
    edge_use = {}
    fixed = set()
    for f in g.faces:
        k = len(f)
        for a in range(k):
            u, v = f[a], f[(a + 1) % k]
            nbrs[u].add(v)
            nbrs[v].add(u)
            e = (u, v) if u < v else (v, u)
            edge_use[e] = edge_use.get(e, 0) + 1
        if k == 3:
            fixed.update(f)
    for (u, v), n in edge_use.items():
        if n == 1:
            fixed.add(u)
            fixed.add(v)
    movable = [i for i in range(nv) if weights[i] > 0.0 and i not in fixed and len(nbrs[i]) >= 3]
    if not movable:
        return
    for _ in range(iterations):
        for factor in (lam, mu):
            new = {}
            for i in movable:
                avg = Vector((0.0, 0.0, 0.0))
                for j in nbrs[i]:
                    avg += g.verts[j]
                avg /= len(nbrs[i])
                new[i] = g.verts[i] + (avg - g.verts[i]) * (factor * weights[i])
            for i, co in new.items():
                g.verts[i] = co


def build_hull(g, vn, prm, closed):
    """Inverted hull around a sheet; also expands open borders sideways so
    single-sided sheets get edge lines and tips get a pointed outline."""
    nv = len(g.verts)
    # boundary edges with their in-surface outward direction
    edge_count = {}
    for fi, f in enumerate(g.faces):
        for a in range(len(f)):
            e = (f[a], f[(a + 1) % len(f)])
            k = (min(e), max(e))
            edge_count.setdefault(k, []).append((fi, e))
    bdir = [Vector((0.0, 0.0, 0.0)) for _ in range(nv)]
    border = []
    for k, uses in edge_count.items():
        if len(uses) != 1:
            continue
        fi, (a, b) = uses[0]
        fn = newell([g.verts[q] for q in g.faces[fi]])
        out = (g.verts[b] - g.verts[a]).cross(fn)
        if out.length > EPS:
            out.normalize()
            bdir[a] += out
            bdir[b] += out
        border.append((a, b))
    for i in range(nv):
        if bdir[i].length > EPS:
            bdir[i].normalize()

    def width_at(t):
        f = smoothstep(prm.outline_taper_start, 1.0, t)
        return prm.outline_width * (1.0 + (prm.outline_tip_ratio - 1.0) * f)

    h = Geo()
    layers = 1 if closed else 2
    for layer in range(layers):
        sgn = 1.0 if layer == 0 else -1.0
        for i in range(nv):
            ow = width_at(g.t[i])
            h.verts.append(g.verts[i] + vn[i] * (sgn * ow) + bdir[i] * ow)
            h.t.append(g.t[i])
            h.inner.append(0.0)
            h.head.append((0.0, 0.0, g.t[i]))
    for f, uv in zip(g.faces, g.face_uv):
        h.add_face(list(reversed(f)), list(reversed(uv)))
        if layers == 2:
            h.add_face([q + nv for q in f], uv)
    if layers == 2:
        for a, b in border:
            f = [a, b, b + nv, a + nv]
            n = newell([h.verts[q] for q in f])
            if n.dot(bdir[a] + bdir[b]) > 0.0:
                f.reverse()
            h.add_face(f, [(0.0, h.t[q % nv]) for q in f])
    hn = h.vertex_normals()
    h.normals = [n.normalized() if n.length > EPS else Vector((0.0, 0.0, 1.0)) for n in hn]
    return h


# --------------------------------------------------------------------------
# public entry point
# --------------------------------------------------------------------------

def cut_at_height(samples, center, height):
    """Trim a guide where it first goes down through a horizontal plane at
    head-local height `height` (-1 = chin level, 1 = top of the head)."""
    zs = [center.inv @ p for p, _r, _t in samples]
    for i in range(1, len(samples)):
        z0, z1 = zs[i - 1].z, zs[i].z
        if z0 >= height > z1:
            f = (z0 - height) / (z0 - z1) if z0 != z1 else 0.0
            a, b = samples[i - 1], samples[i]
            end = (a[0].lerp(b[0], f), a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f)
            out = list(samples[:i]) + [end]
            return out if len(out) >= 2 else samples
    return samples


def layer_defs(prm):
    """(side, length, width scale, tip flip) for each generated layer:
    the middle layer plus optional front (+1) / back (-1) layers."""
    defs = [(0, 1.0, 1.0, 0.0)]
    if prm.layers in ('BACK', 'BOTH'):
        defs.append((-1, prm.layer_back_len, prm.layer_width, -prm.layer_flip))
    if prm.layers in ('FRONT', 'BOTH'):
        defs.append((1, prm.layer_front_len, prm.layer_width, prm.layer_flip))
    return defs


def build_group(spline_samples, center, prm, mirror=False, with_outline=True, links=None):
    """spline_samples: list of sample lists -> (hair Geo, outline Geo|None).

    Each layer (middle / back / front) is built from the same guides and
    joined laterally only within itself, so front and back layers overlap
    the middle one like hand-made layered hair."""
    import random
    if prm.cut_enable:
        spline_samples = [cut_at_height(smp, center, prm.cut_height) for smp in spline_samples]
    prm.density_profile = density_profile(spline_samples, prm)
    hair = Geo()
    outline = Geo() if (with_outline and prm.outline) else None
    base_width = prm.width
    uid = 0
    try:
        for li, (side, length, wscale, flip) in enumerate(layer_defs(prm)):
            prm.width = base_width * wscale
            layer = (side, length, flip) if side else None
            units = []
            for n, smp in enumerate(spline_samples):
                seed = prm.seed * 7919 + n * 2 + li * 104729
                variants = [(smp, False, float(n))]
                if mirror:
                    variants.append(([(center.mirror(p), r, t) for p, r, t in smp], True, n + 0.5))
                for vs, mirrored, order in variants:
                    u = make_unit(vs, center, prm, mirrored, order, random.Random(seed), layer=layer)
                    if u:
                        u.src, u.mirrored = n, mirrored
                        units.append(u)
            for u in units:
                for lk in u.locks:
                    lk.unit = uid
                uid += 1
            if links:
                runs = manual_runs(units, links)       # manual joins win over auto
            else:
                runs = [(run, None) for run in cluster_units(units, prm)]
            for run, ranges in runs:
                locks, rows, starts = [], [], []
                for ui, u in enumerate(run):
                    if locks:
                        rc = len(u.locks[0].frames) - (1 if prm.tip_mode == 'POINT' else 0)
                        if ranges is None:
                            rows.append(_lateral_row(locks[-1], u.locks[0], prm.split, rc))
                            starts.append(0)
                        else:
                            t0, t1 = ranges[ui - 1]
                            r0, r1 = _band_rows(locks[-1], u.locks[0], t0, t1, rc)
                            rows.append(r1)
                            starts.append(r0)
                    for j, lk in enumerate(u.locks):
                        if j > 0:
                            rows.append(u.branch_row)
                            starts.append(0)
                        locks.append(lk)
                build_sheet(locks, rows, center, prm, hair, outline, start_rows=starts)
    finally:
        prm.width = base_width
    return hair, outline
