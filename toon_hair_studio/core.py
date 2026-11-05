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


def compute_frames(points, center, twist=0.0, tilt_sign=1.0, smooth_passes=2, roll=0.0,
                   head_follow=0.0):
    """Frames along a guide -> list of (P, T, N, B).

    The root faces away from the head; from there the frame is carried along
    the curve with minimal twist, like a Blender curve with 'Minimum' twist.
    head_follow (0..1) pulls the outward direction back towards the head
    where the lock runs over it (1 = hug the head)."""
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

    # Outward direction: near the head it follows the head (radial direction
    # projected off the tangent). Where that becomes unreliable - the lock
    # runs along the radial direction (hanging straight down below the head)
    # or is far from the head (long locks, curls at chest level) - the frame
    # is parallel-transported instead (rotation minimising), so long locks
    # keep the orientation they left the head with and never flip / twist.
    N = []
    prev = None
    for i in range(n):
        r = center.radial(P[i])
        v = r - T[i] * r.dot(T[i])
        vl = v.length
        dist = (center.inv @ P[i]).length          # 1 = on the head surface
        w = head_follow * smoothstep(0.15, 0.5, vl) * (1.0 - smoothstep(1.6, 2.6, dist))
        if prev is None:
            if vl > 1e-4:
                cur = v / vl
            else:
                cur = T[i].orthogonal().normalized()
        else:
            tr = prev - T[i] * prev.dot(T[i])
            tr = tr.normalized() if tr.length > 1e-6 else (v / vl if vl > 1e-4 else T[i].orthogonal().normalized())
            if vl > 1e-4 and w > 0.0:
                cur = tr * (1.0 - w) + (v / vl) * w
                cur = cur - T[i] * cur.dot(T[i])
                cur = cur.normalized() if cur.length > 1e-6 else tr
            else:
                cur = tr
        N.append(cur)
        prev = cur

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
        self.face_mat = []     # per face: material slot
        self.face_grid = []    # per face: (lock, row, column) for front strip faces, else None
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
        self.face_mat += other.face_mat
        self.face_grid += other.face_grid
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

    def add_face(self, f, uv, kind='S', mat=0, grid=None):
        self.faces.append(tuple(f))
        self.face_uv.append(tuple(uv))
        self.face_kind.append(kind)
        self.face_mat.append(mat)
        self.face_grid.append(grid)

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
    plane_rows = True         # joined guides share row planes (HairHarpy-style)
    unify = False             # whole group as one body (unify.py) instead of glued locks
    unify_scope = 'AUTO'      # AUTO: every touching lock / SELECTED: only linked ones
    merge_overlaps = True     # joined locks that overlap from the root are laid side by side there
    auto_merge_roots = False  # also join unjoined locks whose roots overlap
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
    head_follow = 0.0         # 0 = plain curve (minimal twist), 1 = hug the head
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
    __slots__ = ("frames", "widths", "ts", "rev", "unit", "sub", "nsub", "uvmap", "trunk", "prof_span")

    def __init__(self, frames, widths, ts, rev):
        self.frames = frames
        self.widths = widths
        self.ts = ts
        self.rev = rev
        self.unit = 0       # id of the guide (Unit) this lock grew from
        self.sub = 0        # index among its branch siblings (order space)
        self.nsub = 1
        self.uvmap = None   # per row (o0, o1, v, rev) UV override, see build_sheet
        self.trunk = 0.0    # attached curve: param where the original curve starts
        self.prof_span = None  # per row (x_lo, x_hi, W, m): part of a shared cross-section


class Unit:
    """Everything grown from one guide spline: 1 lock, or N branched sub-locks
    (ordered by increasing azimuth). Sub-locks are joined up to branch_row."""
    __slots__ = ("locks", "mid", "az", "mid_width", "order", "branch_row", "src", "mirrored",
                 "path", "length", "remake")

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
        self.path = []             # dense guide polyline [(position, t)]
        self.length = 0.0
        self.remake = None         # callable(align) -> Unit with aligned rows


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
    frames = compute_frames(pts, center, twist=0.0, tilt_sign=tilt_sign, head_follow=prm.head_follow)
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


def make_unit(samples, center, prm, mirrored=False, order=0.0, rng=None, layer=None, align=None,
              trunk=0.0):
    """Build the lock(s) of one guide.

    align: optional row params for rows 0..m (from plane_align) so that the
    first rows lie on a neighbour's row planes; later rows keep this guide's
    own density layout."""
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

    embed_start = min(max(-(trunk or 0.0), 0.0), 0.95)
    trunk = min(max(trunk or 0.0, 0.0), 0.9)
    if embed_start > 0.0:
        # only the visible part is generated: keep the original row spacing
        segs = max(3, int(round(segs * (1.0 - embed_start))))

    def parent_w(t, rad):
        # an attached curve keeps its own silhouette: the width profile runs
        # over the original curve only (from `trunk`); the trunk part that
        # was added inside the host keeps the root width. An embedded curve
        # (generated from embed_start on) maps back to the original param.
        if trunk > 0.0:
            t = max(0.0, (t - trunk) / (1.0 - trunk))
        elif embed_start > 0.0:
            t = embed_start + t * (1.0 - embed_start)
        return prm.width * width_factor(t, prm.root_ratio, prm.bulge_pos, prm.tip_shape, tip_min,
                                        cut) * max(rad, 0.0)

    tb = min(max(prm.branch_pos, 0.05), 0.9)
    n_pre = min(max(int(round(tb * segs)), 1), segs - 2) if segs >= 4 else 1
    n_post = segs - n_pre
    # every guide of a group that may branch uses the same row layout
    # (uniform up to the branch point, tip-biased after it), so rows line up
    # across laterally joined guides whether or not each one branches
    branchy = segs >= 4 and (prm.branches > 1 or prm.branch_jitter > 0)

    al = None
    if align and len(align) >= 2:
        limit = (tb - 1e-3) if branchy else 0.97
        cap = (n_pre - 1) if branchy else (segs - 1)
        al = [0.0]
        end = tb if branchy else 1.0
        rows_to_end = n_pre if branchy else segs
        for t in align[1:cap + 1]:
            if t <= al[-1] + 1e-3 or t >= limit:
                break
            # keep room for the remaining rows: stop aligning before they
            # would have to bunch up (at least half the average spacing)
            left = rows_to_end - len(al)
            if (end - t) < 0.5 * left * end / rows_to_end:
                break
            al.append(t)
        if len(al) < 2:
            al = None

    def row_ts(t_end):
        if not branchy:
            if al:
                m = len(al) - 1
                return al + _layout(dens, al[-1], 1.0, segs - m)[1:]
            return _layout(dens, 0.0, 1.0, segs)
        if al:
            m = len(al) - 1
            pre = al + _layout(dens, al[-1], tb, n_pre - m)[1:]
        else:
            pre = _layout(dens, 0.0, tb, n_pre)
        post = _layout(dens, tb, 1.0, n_post)
        k = (t_end - tb) / (1.0 - tb)
        return pre + [tb + (x - tb) * k for x in post[1:]]

    path = [(samples[i][0], lengths[i] / total) for i in range(len(samples))]

    # ---- single lock -------------------------------------------------------
    if nb == 1:
        tl = row_ts(1.0)
        pts = [(*_eval_at(samples, lengths, total, t), t) for t in tl]
        frames = compute_frames(pts, center, twist=prm.twist, tilt_sign=tilt_sign, roll=prm.roll,
                                head_follow=prm.head_follow)
        widths = [parent_w(p[3], p[1]) for p in pts]
        P, _T, _N, B = frames[len(frames) // 3]
        lk = Lock(frames, widths, tl, _azimuth_rev(center, P, B, widths[len(frames) // 3]))
        im = len(frames) // 2
        Pm = frames[im][0]
        lk.trunk = trunk
        if embed_start > 0.0:
            lk.uvmap = [(0.0, 1.0, embed_start + t * (1.0 - embed_start), lk.rev) for t in tl]
        u = Unit([lk], Pm, center.head_coord(Pm)[0], widths[im], order, -1)
        u.path, u.length = path, total
        return u

    # ---- branched lock -------------------------------------------------------
    # parent frames on a fine grid, interpolated at any t
    fine = 64
    fpts = [(*_eval_at(samples, lengths, total, i / fine), i / fine) for i in range(fine + 1)]
    fframes = compute_frames(fpts, center, twist=prm.twist, tilt_sign=tilt_sign, roll=prm.roll,
                             head_follow=prm.head_follow)

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
                                head_follow=prm.head_follow,
                                roll=prm.roll)
        locks.append(Lock(frames, widths, tl, rev))
    if rev:
        locks.reverse()
    for idx, lk in enumerate(locks):
        lk.sub, lk.nsub = idx, len(locks)
        lk.trunk = trunk
        if embed_start > 0.0:
            lk.uvmap = [(0.0, 1.0, embed_start + t * (1.0 - embed_start), lk.rev) for t in lk.ts]
    Pm, _Bm, radm, _ = frame_at(0.5)
    u = Unit(locks, Pm, center.head_coord(Pm)[0], parent_w(0.5, radm), order, n_pre)
    u.path, u.length = path, total
    return u


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


def _path_at(path, t):
    """Position and unit tangent of a dense guide polyline at param t."""
    n = len(path)
    lo, hi = 0, n - 1
    while hi - lo > 1:
        m = (lo + hi) // 2
        if path[m][1] < t:
            lo = m
        else:
            hi = m
    (pa, ta), (pb, tb) = path[lo], path[hi]
    f = (t - ta) / (tb - ta) if tb > ta else 0.0
    d = pb - pa
    tan = d.normalized() if d.length > EPS else Vector((0.0, 0.0, -1.0))
    return pa.lerp(pb, min(max(f, 0.0), 1.0)), tan


def plane_align(ref, nb, t_band_end, reach):
    """Row params for `nb` whose rows sit on `ref`'s row planes.

    For each row of `ref` up to t_band_end, intersect the plane through the
    row centre (normal = guide tangent) with nb's guide, walking forward so
    the result stays monotonic. Stops where the plane no longer meets nb
    nearby or meets it at a grazing angle. Returns [0, t1, ..., tm] or None."""
    ts_ref = ref.locks[0].ts
    path = nb.path
    if len(path) < 2 or len(ref.path) < 2:
        return None
    out = [0.0]
    k = 0
    for i in range(1, len(ts_ref)):
        if ts_ref[i] > t_band_end + 1e-9:
            break
        P0, n = _path_at(ref.path, ts_ref[i])
        hit = None
        dprev = (path[k][0] - P0).dot(n)
        for j in range(k, len(path) - 1):
            d1 = (path[j + 1][0] - P0).dot(n)
            if dprev <= 0.0 < d1:
                f = -dprev / (d1 - dprev) if d1 != dprev else 0.0
                q = path[j][0].lerp(path[j + 1][0], f)
                seg = path[j + 1][0] - path[j][0]
                if (q - P0).length <= reach and seg.length > EPS and abs(seg.normalized().dot(n)) >= 0.3:
                    hit = (j, path[j][1] + (path[j + 1][1] - path[j][1]) * f)
                break
            dprev = d1
        if hit is None or hit[1] <= out[-1] + 1e-3:
            break
        k = hit[0]
        out.append(hit[1])
    return out if len(out) >= 2 else None


def align_run(run, band_ends, reach_scale=4.0):
    """Re-make the units of a joined run so that, inside each join band,
    neighbours share the row planes of the longest guide (propagated along
    the chain). Returns (units, ref_side) where ref_side[b] is 0 if the left
    unit of boundary b is the reference, 1 if the right one is."""
    n = len(run)
    run = list(run)
    ref_side = [0] * (n - 1)
    if n < 2:
        return run, ref_side
    root = max(range(n), key=lambda i: run[i].length)
    order = [(j, j - 1) for j in range(root + 1, n)] + [(j, j + 1) for j in range(root - 1, -1, -1)]
    for j, r in order:
        b = min(j, r)
        ref_side[b] = 0 if r < j else 1
        u, ref = run[j], run[r]
        if u.remake is None:
            continue
        reach = reach_scale * (ref.mid_width + u.mid_width)
        al = plane_align(ref, u, band_ends[b], reach)
        if al:
            nu = u.remake(al)
            if nu is not None:
                run[j] = nu
    return run, ref_side


def _order_dir(la, lb, i):
    Ba = la.frames[i][3] * (-1.0 if la.rev else 1.0)
    Bb = lb.frames[i][3] * (-1.0 if lb.rev else 1.0)
    d = Ba + Bb
    return d.normalized() if d.length > EPS else Ba


def overlap_ratio(la, lb, i):
    """Side-by-side spacing of two neighbouring locks at row i, in units of
    their mean half... i.e. 1 = edges just touch, < 1 = they overlap."""
    d = _order_dir(la, lb, i)
    sep = (lb.frames[i][0] - la.frames[i][0]).dot(d)
    half = 0.5 * (la.widths[i] + lb.widths[i])
    return sep / half if half > EPS else 1.0


def overlap_runs(units, prm, root_rows=3):
    """Chain units whose roots overlap (penetrating locks growing from about
    the same spot). Returns [(units, {b: (0, t_end)})]: each boundary is
    joined from the root until shortly after the locks stop overlapping."""
    seq = sorted(units, key=lambda u: u.az)
    if len(seq) > 2:
        gaps = [((seq[(i + 1) % len(seq)].az - seq[i].az) % 1.0, i) for i in range(len(seq))]
        _g, i = max(gaps)
        seq = seq[i + 1:] + seq[:i + 1]
    runs = []
    cur, ranges = [], {}
    for u in seq:
        if cur:
            la, lb = cur[-1].locks[-1], u.locks[0]
            rc = min(len(la.frames), len(lb.frames)) - (1 if prm.tip_mode == 'POINT' else 0)
            near = min(abs(overlap_ratio(la, lb, i)) for i in range(min(root_rows, rc)))
            touching = (la.frames[0][0] - lb.frames[0][0]).length < 0.75 * (la.widths[1] + lb.widths[1])
            if near < 0.9 and touching:
                sep_row = rc - 2
                for i in range(rc):
                    if overlap_ratio(la, lb, i) >= 1.0:
                        sep_row = i
                        break
                end_row = min(sep_row + 1, rc - 2)
                ranges[len(cur) - 1] = (0.0, 0.5 * (la.ts[end_row] + lb.ts[end_row]), None)
                cur.append(u)
                continue
            runs.append((cur, ranges))
        cur, ranges = [u], {}
    if cur:
        runs.append((cur, ranges))
    return runs


def tile_root_overlaps(locks, transition=2, max_spread=0.7, limits=None):
    """Where neighbouring locks of a sheet overlap from the root down, lay
    them out side by side across their combined span (slots proportional to
    their widths, on one shared line), then blend back to their own layout
    over a few rows. Penetrating / buried locks become one merged lock that
    branches apart where the guides separate.

    Only clusters of consecutive overlapping locks that run roughly the same
    way are tiled (the outermost tangents within ~45 degrees, cos >=
    max_spread); fans such as a crown whorl are left alone."""
    K = len(locks)
    if K < 2:
        return
    rc = min(len(lk.frames) for lk in locks)
    # limits[b]: last joined row of boundary b - past it the locks are apart
    # (e.g. a stray that came out now lies ON TOP of a neighbour)
    over = [[(limits is None or i <= limits[b]) and overlap_ratio(locks[b], locks[b + 1], i) < 1.0
             for b in range(K - 1)] for i in range(rc)]
    rows = []
    for i in range(rc):
        if any(over[i]):
            rows.append(i)
        else:
            break
    if not rows:
        return
    i_sep = rows[-1] + 1
    for i in range(min(rc, i_sep + transition)):
        m = 1.0 if i < i_sep else 1.0 - (i - i_sep + 1) / (transition + 1)
        m = m * m * (3.0 - 2.0 * m)
        # clusters of consecutive overlapping locks (use the last overlapping
        # row's clusters while blending out)
        flags = over[min(i, i_sep - 1)]
        clusters, cur = [], [0]
        for b in range(K - 1):
            if flags[b]:
                cur.append(b + 1)
            else:
                clusters.append(cur)
                cur = [b + 1]
        clusters.append(cur)
        for cl in clusters:
            if len(cl) < 2:
                continue
            group = [locks[k] for k in cl]
            if group[0].frames[i][1].dot(group[-1].frames[i][1]) < max_spread:
                continue
            _tile_row(group, i, m)


def attach_pair(left, right, r1, stray_right, transition=2):
    """Host + attached curve: inside the join band the curve becomes a strip
    within the host's own span (host silhouette unchanged, neighbours
    untouched); UVs: host keeps its 0..1 range over its full width and the
    strip continues the host's UV, after the band the curve has its own UV
    over the original curve (trunk mapping)."""
    host, stray = (left, right) if stray_right else (right, left)
    rc = min(len(left.frames), len(right.frames))
    for i in range(min(rc, max(r1, 0) + 1 + transition)):
        m = 1.0 if i <= r1 else 1.0 - (i - r1) / (transition + 1)
        m = m * m * (3.0 - 2.0 * m)
        _tile_row([left, right], i, m)
    nr_h, nr_s = len(host.ts), len(stray.ts)
    host.prof_span = [None] * nr_h
    stray.prof_span = [None] * nr_s
    for i in range(min(rc, max(r1, 0) + 1 + transition)):
        m = 1.0 if i <= r1 else 1.0 - (i - r1) / (transition + 1)
        m = m * m * (3.0 - 2.0 * m)
        wh, ws = host.widths[i], stray.widths[i]
        if wh + ws <= EPS:
            continue
        f = 2.0 * ws / (wh + ws)
        if stray_right:
            host.prof_span[i] = (-1.0, 1.0 - f, wh + ws, m)
            stray.prof_span[i] = (1.0 - f, 1.0, wh + ws, m)
        else:
            stray.prof_span[i] = (-1.0, -1.0 + f, wh + ws, m)
            host.prof_span[i] = (-1.0 + f, 1.0, wh + ws, m)
    host.uvmap = [None] * nr_h
    stray.uvmap = [None] * nr_s
    tr = stray.trunk
    for i in range(nr_s):
        if i <= r1 and i < nr_h:
            wh, ws = host.widths[i], stray.widths[i]
            f = ws / (wh + ws) if wh + ws > EPS else 0.0
            v = host.ts[i]
            if stray_right:
                host.uvmap[i] = (0.0, 1.0 - f, v, host.rev)
                stray.uvmap[i] = (1.0 - f, 1.0, v, host.rev)
            else:
                host.uvmap[i] = (f, 1.0, v, host.rev)
                stray.uvmap[i] = (0.0, f, v, host.rev)
        else:
            t = stray.ts[i]
            v = max(0.0, (t - tr) / (1.0 - tr)) if tr > 0.0 else t
            stray.uvmap[i] = (0.0, 1.0, v, stray.rev)


def _tile_row(locks, i, m):
    ws = [lk.widths[i] for lk in locks]
    wsum = sum(ws)
    if wsum <= EPS:
        return
    Pc = sum((lk.frames[i][0] * w for lk, w in zip(locks, ws)), Vector()) / wsum
    T = sum((lk.frames[i][1] for lk in locks), Vector())
    T = T.normalized() if T.length > EPS else locks[0].frames[i][1]
    d = sum((lk.frames[i][3] * (-1.0 if lk.rev else 1.0) for lk in locks), Vector())
    d = d - T * d.dot(T)
    if d.length < EPS:
        return
    d.normalize()
    xs = [(lk.frames[i][0] - Pc).dot(d) for lk in locks]
    lo = min(x - w * 0.5 for x, w in zip(xs, ws))
    hi = max(x + w * 0.5 for x, w in zip(xs, ws))
    total = max(hi - lo, wsum * 0.5)
    acc = 0.0
    for k, lk in enumerate(locks):
        w_new = total * ws[k] / wsum
        c = lo + (acc + 0.5 * ws[k]) / wsum * total
        acc += ws[k]
        P, Tk, Nk, Bk = lk.frames[i]
        Pn = Pc + d * c
        Bn = d * (-1.0 if lk.rev else 1.0)
        Nn = Bn.cross(Tk)                    # our frames use B = T x N  ->  N = B x T
        Nn = Nn.normalized() if Nn.length > EPS else Nk
        if Nn.dot(Nk) < 0.0:
            Nn = -Nn
        B2 = Bk.lerp(Bn, m)
        N2 = Nk.lerp(Nn, m)
        lk.frames[i] = (P.lerp(Pn, m), Tk,
                        N2.normalized() if N2.length > EPS else Nk,
                        B2.normalized() if B2.length > EPS else Bk)
        lk.widths[i] = lk.widths[i] + (w_new - lk.widths[i]) * m


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
    for link in links:
        a, b, t0, t1 = link[:4]
        kind = link[4] if len(link) > 4 else None     # (stray spline, host spline) or None
        for mir in (False, True):
            ia, ib = by_key.get((a, mir)), by_key.get((b, mir))
            if ia is None or ib is None or ia == ib:
                continue
            pair_range[frozenset((ia, ib))] = (t0, t1, kind)
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
        # width the cross-section is actually built on (a host + attached
        # curve share one wider section), so neighbours match at the seam
        eff = []
        for k in range(K):
            ps_ = locks[k].prof_span[i] if locks[k].prof_span is not None and i < len(locks[k].prof_span) else None
            eff.append(wk[k] + (ps_[2] - wk[k]) * ps_[3] if ps_ is not None else wk[k])
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
            wnb_l = eff[k - 1] if k > 0 else wk[k]
            wnb_r = eff[k + 1] if k < K - 1 else wk[k]
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
                ps = lk.prof_span[i] if lk.prof_span is not None and i < len(lk.prof_span) else None
                if ps is not None:
                    # host + attached curve share one cross-section here
                    x_lo, x_hi, Wun, mb = ps
                    xs_ = x_lo + (x_hi - x_lo) * (x + 1.0) * 0.5
                    my_s, he_s = prof(xs_, Wun, wnb_l, wnb_r,
                                      lat_l if x_lo <= -0.999 else 0.0,
                                      lat_r if x_hi >= 0.999 else 0.0)
                    ym += (my_s - ym) * mb
                    ye += (he_s - ye) * mb
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

    def uv_col(lk, i, c):
        """UV of order column c on row i (uvmap override or the default)."""
        m = lk.uvmap[i] if lk.uvmap is not None and i < len(lk.uvmap) else None
        if m is None:
            o0, o1, v, rv = 0.0, 1.0, lk.ts[i], lk.rev
        else:
            o0, o1, v, rv = m
        o = o0 + (o1 - o0) * c / S
        return ((1.0 - o) if rv else o, v)

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
            for j in range(S):
                ca, cb = cols[j], cols[j + 1]
                a0, b0 = uv_col(lk, i, ca), uv_col(lk, i, cb)
                a1, b1 = uv_col(lk, i + 1, ca), uv_col(lk, i + 1, cb)
                g.add_face((V('F', k, i, ca), V('F', k, i, cb), V('F', k, i + 1, cb), V('F', k, i + 1, ca)),
                           (a0, b0, b1, a1), 'F', grid=(k, i, min(ca, cb)))
                if closed:
                    g.add_face((V('B', k, i, cb), V('B', k, i, ca), V('B', k, i + 1, ca), V('B', k, i + 1, cb)),
                               (b0, a0, a1, b1), 'B')
            if bevel:
                # bevel band along each outer (not joined) edge
                for c in (0, S):
                    if closed_edge(k, i, c) and closed_edge(k, i + 1, c):
                        f = [V('F', k, i, c), V('F', k, i + 1, c), V('B', k, i + 1, c), V('B', k, i, c)]
                        p0, p1 = uv_col(lk, i, c), uv_col(lk, i + 1, c)
                        band_faces.append(len(g.faces))
                        g.add_face(f, [p0, p1, p1, p0])
        i = rc - 1
        loop = [(V('F', k, i, cols[j]), uv_col(lk, i, cols[j]), 'F') for j in range(S + 1)]
        if closed:
            back = range(S, -1, -1) if bevel else range(S - 1, 0, -1)
            loop += [(V('B', k, i, cols[j]), uv_col(lk, i, cols[j]), 'B') for j in back]
        if point_tip:
            tip = index[('T', k)]
            ut = uv_col(lk, min(nr - 1, len(ts) - 1), S * 0.5)
            span = len(loop) if closed else len(loop) - 1
            for j in range(span):
                a, ua, sa = loop[j]
                b, ub, sb = loop[(j + 1) % len(loop)]
                kind = 'F' if sa == sb == 'F' else ('B' if sa == sb == 'B' else 'S')
                g.add_face((a, b, tip), (ua, ub, ut), kind)
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

    finish_sheet(g, center, prm, hair, outline, closed)


def finish_sheet(g, center, prm, hair, outline, closed):
    """Normals, optional outline hull and flipped back faces; append to hair."""
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


# --------------------------------------------------------------------------
# weld / cleanup pass (stitch separate pieces, fill small holes, orient)
# --------------------------------------------------------------------------

def _components(nv, faces):
    parent = list(range(nv))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for f in faces:
        r = find(f[0])
        for q in f[1:]:
            rq = find(q)
            if rq != r:
                parent[rq] = r
    return [find(i) for i in range(nv)]


def _boundary_loops(faces):
    """Closed loops of open (single-use) edges, following face winding."""
    nxt = {}
    count = {}
    for f in faces:
        for a in range(len(f)):
            u, v = f[a], f[(a + 1) % len(f)]
            k = (u, v) if u < v else (v, u)
            count[k] = count.get(k, 0) + 1
    for f in faces:
        for a in range(len(f)):
            u, v = f[a], f[(a + 1) % len(f)]
            k = (u, v) if u < v else (v, u)
            if count[k] == 1:
                nxt.setdefault(v, []).append(u)      # walk the hole the other way
    loops = []
    used = set()
    for start in list(nxt):
        for first in nxt[start]:
            if (start, first) in used:
                continue
            loop = [start]
            cur, prv = first, start
            used.add((start, first))
            ok = False
            for _ in range(10000):
                if cur == start:
                    ok = True
                    break
                loop.append(cur)
                cands = [c for c in nxt.get(cur, []) if (cur, c) not in used]
                if not cands:
                    break
                nx = cands[0]
                used.add((cur, nx))
                prv, cur = cur, nx
            if ok and len(loop) >= 3:
                loops.append(loop)
    return loops


def orient_consistent(g):
    """Make winding consistent inside every connected piece, keeping the
    orientation most of its faces already had."""
    nf = len(g.faces)
    edge_faces = {}
    for fi, f in enumerate(g.faces):
        for a in range(len(f)):
            u, v = f[a], f[(a + 1) % len(f)]
            edge_faces.setdefault((u, v) if u < v else (v, u), []).append(fi)
    flipped = [None] * nf
    for seed in range(nf):
        if flipped[seed] is not None:
            continue
        flipped[seed] = False
        comp = [seed]
        stack = [seed]
        while stack:
            fi = stack.pop()
            f = g.faces[fi]
            if flipped[fi]:
                f = tuple(reversed(f))
            for a in range(len(f)):
                u, v = f[a], f[(a + 1) % len(f)]
                for fj in edge_faces[(u, v) if u < v else (v, u)]:
                    if fj == fi or flipped[fj] is not None:
                        continue
                    h = g.faces[fj]
                    same = any(h[b] == u and h[(b + 1) % len(h)] == v for b in range(len(h)))
                    flipped[fj] = same            # same direction -> must flip
                    comp.append(fj)
                    stack.append(fj)
        if sum(1 for fi in comp if flipped[fi]) * 2 > len(comp):
            for fi in comp:
                flipped[fi] = not flipped[fi]
    for fi in range(nf):
        if flipped[fi]:
            g.faces[fi] = tuple(reversed(g.faces[fi]))
            g.face_uv[fi] = tuple(reversed(g.face_uv[fi]))


def weld_geo(g, dist, max_hole=16, root_t=0.02):
    """Stitch separate pieces whose open borders come within `dist`.

    - only vertices on open borders are candidates, and two vertices of the
      same piece are never merged (so notches between joined locks and thin
      tips stay open); a merge cluster holds at most one vertex per piece
    - faces that collapse or become duplicates are removed
    - small holes (<= max_hole edges) left by the stitch are filled with
      quad ladders; root openings (t ~ 0) are left open
    - winding is made consistent afterwards
    Returns the number of merged vertices."""
    from mathutils import kdtree
    nv = len(g.verts)
    if nv == 0 or dist <= 0.0:
        return 0
    count = {}
    for f in g.faces:
        for a in range(len(f)):
            u, v = f[a], f[(a + 1) % len(f)]
            k = (u, v) if u < v else (v, u)
            count[k] = count.get(k, 0) + 1
    border = sorted({q for k, c in count.items() if c == 1 for q in k})
    if not border:
        return 0
    comp = _components(nv, g.faces)
    kd = kdtree.KDTree(len(border))
    for i in border:
        kd.insert(g.verts[i], i)
    kd.balance()
    pairs = []
    for i in border:
        for _co, j, d in kd.find_range(g.verts[i], dist):
            if j > i and comp[i] != comp[j]:
                pairs.append((d, i, j))
    if not pairs:
        return 0
    pairs.sort()
    parent = {}
    members = {}

    def find(x):
        while parent.get(x, x) != x:
            x = parent[x]
        return x
    for _d, i, j in pairs:
        ri, rj = find(i), find(j)
        if ri == rj:
            continue
        ci = members.get(ri, {comp[ri]: ri})
        cj = members.get(rj, {comp[rj]: rj})
        if set(ci) & set(cj):
            continue                       # would put two vertices of one piece together
        parent[rj] = ri
        ci.update(cj)
        members[ri] = ci
        members.pop(rj, None)
    remap = list(range(nv))
    merged = 0
    for root, cl in members.items():
        idx = list(cl.values())
        if len(idx) < 2:
            continue
        co = sum((g.verts[q] for q in idx), Vector()) / len(idx)
        n = sum((g.normals[q] for q in idx), Vector()) if g.normals else None
        for q in idx:
            remap[q] = root
            merged += 1
        g.verts[root] = co
        if n is not None and n.length > EPS:
            g.normals[root] = n.normalized()
        merged -= 1
    if not merged:
        return 0
    faces, uvs, kinds, mats, grids = [], [], [], [], []
    seen = {}
    for f, uv, kind, mat, grid in zip(g.faces, g.face_uv, g.face_kind, g.face_mat, g.face_grid):
        nf, nuv = [], []
        for q, u in zip(f, uv):
            r = remap[q]
            if r not in nf:
                nf.append(r)
                nuv.append(u)
        if len(nf) < 3:
            continue
        key = tuple(sorted(nf))
        if key in seen:
            seen[key] = None                   # duplicated wall: drop both copies
            continue
        seen[key] = len(faces)
        faces.append(tuple(nf))
        uvs.append(tuple(nuv))
        kinds.append(kind)
        mats.append(mat)
        grids.append(grid)
    dup_keys = {k for k, i in seen.items() if i is None}
    keep = [i for i, f in enumerate(faces) if tuple(sorted(f)) not in dup_keys]
    g.faces = [faces[i] for i in keep]
    g.face_uv = [uvs[i] for i in keep]
    g.face_kind = [kinds[i] for i in keep]
    g.face_mat = [mats[i] for i in keep]
    g.face_grid = [grids[i] for i in keep]
    # fill small holes created by the stitch
    for loop in _boundary_loops(g.faces):
        if len(loop) > max_hole or any(g.t[q] <= root_t for q in loop):
            continue
        if not any(remap[q] != q or q in members for q in loop):
            continue                           # untouched border (sheet edge, notch)
        n = len(loop)
        lo, hi = 0, n - 1
        while hi - lo >= 3:
            g.add_face((loop[lo], loop[lo + 1], loop[hi - 1], loop[hi]),
                       [(0.5, g.t[q]) for q in (loop[lo], loop[lo + 1], loop[hi - 1], loop[hi])])
            lo += 1
            hi -= 1
        if hi - lo == 2:
            g.add_face((loop[lo], loop[lo + 1], loop[hi]),
                       [(0.5, g.t[q]) for q in (loop[lo], loop[lo + 1], loop[hi])])
    orient_consistent(g)
    compact_unused(g)
    return merged


def compact_unused(g):
    """Drop vertices no face uses any more (keeps per-vertex lists in sync)."""
    used = sorted({q for f in g.faces for q in f})
    if len(used) == len(g.verts):
        return
    new = {old: i for i, old in enumerate(used)}
    g.verts = [g.verts[q] for q in used]
    g.t = [g.t[q] for q in used]
    g.inner = [g.inner[q] for q in used]
    g.head = [g.head[q] for q in used] if g.head else g.head
    if g.normals:
        g.normals = [g.normals[q] for q in used]
    g.faces = [tuple(new[q] for q in f) for f in g.faces]


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


def _zip_loops(g, A, B):
    """Bridge two closed vertex loops with quads (triangles where their
    vertex counts differ), matching them by arc-length fraction."""
    n, m = len(A), len(B)
    j0 = min(range(m), key=lambda j: (g.verts[B[j]] - g.verts[A[0]]).length)
    B = B[j0:] + B[:j0]
    if (g.verts[B[1]] - g.verts[A[1]]).length > (g.verts[B[-1]] - g.verts[A[1]]).length:
        B = [B[0]] + B[1:][::-1]

    def params(L):
        d = [0.0]
        for k in range(1, len(L) + 1):
            d.append(d[-1] + (g.verts[L[k % len(L)]] - g.verts[L[k - 1]]).length)
        tot = d[-1] or 1.0
        return [x / tot for x in d]
    pa, pb = params(A), params(B)
    faces = []
    i = j = 0
    tol = 0.5 * min(1.0 / n, 1.0 / m)
    while i < n or j < m:
        a0, a1 = A[i % n], A[(i + 1) % n]
        b0, b1 = B[j % m], B[(j + 1) % m]
        na = pa[i + 1] if i < n else 2.0
        nb = pb[j + 1] if j < m else 2.0
        if i < n and j < m and abs(na - nb) < tol:
            faces.append((a0, a1, b1, b0))
            i += 1
            j += 1
        elif na <= nb:
            faces.append((a0, a1, b0))
            i += 1
        else:
            faces.append((a0, b1, b0))
            j += 1
    return faces


def _bridge_cost(g, A, B):
    n = len(A)
    best = None
    for rev in (False, True):
        L = B[::-1] if rev else B
        for r in range(n):
            cost = sum((g.verts[A[k]] - g.verts[L[(k + r) % n]]).length for k in range(n))
            if best is None or cost < best:
                best = cost
    return best


def _bridge_equal(g, A, B):
    """Quads between two loops with the same vertex count, using the
    rotation / direction of B that keeps the bridge shortest (no twist)."""
    n = len(A)
    best = None
    for rev in (False, True):
        L = B[::-1] if rev else B
        for r in range(n):
            cost = sum((g.verts[A[k]] - g.verts[L[(k + r) % n]]).length for k in range(n))
            if best is None or cost < best[0]:
                best = (cost, rev, r)
    _c, rev, r = best
    L = B[::-1] if rev else B
    L = [L[(k + r) % n] for k in range(n)]
    return [(A[k], A[(k + 1) % n], L[(k + 1) % n], L[k]) for k in range(n)]


def _grid_patch(g, front, C, ring, ring_w):
    """A rows x columns window of the host's front grid around the root ring
    whose border has exactly as many vertices as the ring (2 * (rows +
    columns)). The window centred over the ring and closest to square is
    used, so the bridge quads fan out evenly instead of forming slivers."""
    ring_n = len(ring)
    grid = {g.face_grid[fi]: fi for fi in front if g.face_grid[fi] is not None}
    if not grid or ring_n % 2:
        return None

    def cen(fi):
        f = g.faces[fi]
        return sum((g.verts[q] for q in f), Vector()) / len(f)
    near = min(grid.values(), key=lambda fi: (cen(fi) - C).length)
    k0, i0, c0 = g.face_grid[near]
    ncol = 1 + max(c for (k, _i, c) in grid if k == k0)
    f = g.faces[near]
    col_w = max((g.verts[f[0]] - g.verts[f[1]]).length, (g.verts[f[2]] - g.verts[f[3]]).length, 1e-6)
    half = ring_n // 2
    best = None
    for kc in range(1, min(ncol, half - 1) + 1):
        m = half - kc
        for di in range(-1, m + 1):
            for dc in range(-1, kc + 1):
                cells = [(k0, i0 - di + a, c0 - dc + b) for a in range(m) for b in range(kc)]
                if not all(cell in grid for cell in cells):
                    continue
                fis = [grid[cell] for cell in cells]
                holes = _boundary_loops([g.faces[fi] for fi in fis])
                if len(holes) != 1 or len(holes[0]) != ring_n:
                    continue
                # centred over the ring, as square as possible: the bridge
                # then fans out evenly from the hole's sides to the ring
                cc = sum((cen(fi) for fi in fis), Vector()) / len(fis)
                score = (cc - C).length + abs(kc - m) * col_w * 0.6
                if best is None or score < best[0]:
                    best = (score, fis)
    return best[1] if best else None


def bridge_embedded(g, pieces, hosts):
    """Connect embedded curves to their host: open a hole in the host's front
    surface right over the curve's root ring and bridge the hole to the ring.

    The hole is a rows x columns window of the host's grid with exactly as
    many border vertices as the root ring, so the bridge is all quads. Host
    and curve keep their shape and UVs; only the faces of that window are
    replaced. If no such window fits, a nearby patch is bridged with a
    quad/triangle zip instead."""
    remove = set()
    adds = []
    for srcs, mir, li, v0, v1, f0, f1 in pieces:
        if len(srcs) != 1:
            continue
        s_src = next(iter(srcs))
        h_src = hosts.get(s_src)
        if h_src is None:
            continue
        hp = [p for p in pieces if h_src in p[0] and p[1] == mir and p[2] == li]
        if not hp:
            continue
        _hs, _hm, _hl, hv0, hv1, hf0, hf1 = hp[0]
        loops = _boundary_loops(g.faces[f0:f1])
        if not loops:
            continue
        ring = min(loops, key=lambda L: sum(g.t[q] for q in L) / len(L))
        C = sum((g.verts[q] for q in ring), Vector()) / len(ring)
        rr = sum((g.verts[q] - C).length for q in ring) / len(ring)
        ring_w = max((g.verts[a] - g.verts[b]).length for a in ring for b in ring)

        def cen(fi):
            f = g.faces[fi]
            return sum((g.verts[q] for q in f), Vector()) / len(f)
        front = [fi for fi in range(hf0, hf1) if g.face_kind[fi] == 'F' and fi not in remove]
        if not front:
            continue
        host_edges = {}
        for fi in range(hf0, hf1):
            f = g.faces[fi]
            for a in range(len(f)):
                u, v = f[a], f[(a + 1) % len(f)]
                k = (u, v) if u < v else (v, u)
                host_edges[k] = host_edges.get(k, 0) + 1

        def interior(loop):
            return all(host_edges.get((min(u, v), max(u, v)), 0) >= 2
                       for u, v in zip(loop, loop[1:] + loop[:1]))
        patch = _grid_patch(g, front, C, ring, ring_w)
        hole = None
        if patch:
            holes = _boundary_loops([g.faces[fi] for fi in patch])
            if len(holes) == 1 and len(holes[0]) == len(ring) and interior(holes[0]):
                hole = holes[0]
                remove.update(patch)
                adds += _bridge_equal(g, hole, ring)
        if hole is None:
            patch = [fi for fi in front if (cen(fi) - C).length <= rr * 1.1]
            if not patch:
                patch = [min(front, key=lambda fi: (cen(fi) - C).length)]
            holes = _boundary_loops([g.faces[fi] for fi in patch])
            if len(holes) != 1 or not interior(holes[0]):
                continue
            remove.update(patch)
            adds += _zip_loops(g, holes[0], ring)
    if not remove:
        return 0
    uv_of = {}
    for f, uv in zip(g.faces, g.face_uv):
        for q, u in zip(f, uv):
            uv_of.setdefault(q, u)
    keep = [i for i in range(len(g.faces)) if i not in remove]
    g.faces = [g.faces[i] for i in keep]
    g.face_uv = [g.face_uv[i] for i in keep]
    g.face_kind = [g.face_kind[i] for i in keep]
    g.face_mat = [g.face_mat[i] for i in keep]
    g.face_grid = [g.face_grid[i] for i in keep]
    for f in adds:
        g.add_face(f, [uv_of.get(q, (0.5, 0.0)) for q in f], 'S')
    orient_consistent(g)
    compact_unused(g)          # the hole's inner vertices are no longer used
    return len(adds)


def trim_samples(samples, start):
    """The part of a guide from arc param `start` to its end."""
    if start <= 0.0 or len(samples) < 2:
        return samples
    lengths, total = _arc(samples)
    if total < 1e-9:
        return samples
    n = max(8, len(samples))
    out = []
    for k in range(n + 1):
        t = start + (1.0 - start) * k / n
        out.append(_eval_at(samples, lengths, total, t))
    return out


def build_group(spline_samples, center, prm, mirror=False, with_outline=True, links=None, trunks=None,
                embed_starts=None, embed_hosts=None):
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
    pieces = []      # (srcs, mirrored, layer, v0, v1, f0, f1) of every sheet
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
                trunk = (trunks or {}).get(n, 0.0)
                start = (embed_starts or {}).get(n, 0.0)
                if start > 0.0:
                    # embedded curve: generate only the visible part, but keep
                    # its silhouette and UVs in the original curve's terms
                    variants = [(trim_samples(vs, start), mir, od) for vs, mir, od in variants]
                    trunk = -start
                for vs, mirrored, order in variants:
                    u = make_unit(vs, center, prm, mirrored, order, random.Random(seed), layer=layer,
                                  trunk=trunk)
                    if u:
                        u.src, u.mirrored = n, mirrored

                        def remake(al, vs=vs, mirrored=mirrored, order=order, seed=seed, n=n, trunk=trunk):
                            nu = make_unit(vs, center, prm, mirrored, order, random.Random(seed),
                                           layer=layer, align=al, trunk=trunk)
                            if nu is not None:
                                nu.src, nu.mirrored = n, mirrored
                            return nu
                        u.remake = remake
                        units.append(u)
            if prm.unify:
                # one clean body per layer; embedded curves stay separate and
                # are bridged into it afterwards
                from .unify import build_layer
                emb = set((embed_starts or {}).keys())
                main = [u for u in units if u.src not in emb]
                pv0, pf0 = len(hair.verts), len(hair.faces)
                uid = build_layer(main, center, prm, hair, outline, links, uid)
                for mir in (False, True):
                    pieces.append(({u.src for u in main}, mir, li, pv0, len(hair.verts), pf0, len(hair.faces)))
                for u in units:
                    if u.src not in emb:
                        continue
                    pv0, pf0 = len(hair.verts), len(hair.faces)
                    uid = build_layer([u], center, prm, hair, outline, None, uid)
                    pieces.append(({u.src}, u.mirrored, li, pv0, len(hair.verts), pf0, len(hair.faces)))
                continue
            if links:
                runs = manual_runs(units, links)       # manual joins win over auto
            elif prm.connect:
                runs = [(run, None) for run in cluster_units(units, prm)]
            elif prm.auto_merge_roots:
                runs = overlap_runs(units, prm)        # penetrating roots: merge, then branch
            else:
                runs = [([u], {}) for u in units]
            aligned = []
            for run, ranges in runs:
                if ranges is None:
                    ends = [prm.split] * (len(run) - 1)
                else:
                    ends = [ranges[b][1] for b in range(len(run) - 1)]
                if prm.plane_rows and len(run) > 1:
                    run, ref_side = align_run(run, ends)
                else:
                    ref_side = [None] * (len(run) - 1)
                aligned.append((run, ranges, ref_side))
            for run, _r, _s in aligned:
                for u in run:
                    for lk in u.locks:
                        lk.unit = uid
                    uid += 1
            for run, ranges, ref_side in aligned:
                locks, rows, starts = [], [], []
                strays = {}            # boundary -> True if the stray is the right-hand lock
                for ui, u in enumerate(run):
                    if locks:
                        rc = len(u.locks[0].frames) - (1 if prm.tip_mode == 'POINT' else 0)
                        a, bl = locks[-1], u.locks[0]
                        side = ref_side[ui - 1]
                        if side is not None:
                            # rows are plane-aligned: measure the band on the reference
                            a = bl = (a if side == 0 else bl)
                        if ranges is None:
                            rows.append(_lateral_row(a, bl, prm.split, rc))
                            starts.append(0)
                        else:
                            t0, t1, kind = ranges[ui - 1]
                            r0, r1 = _band_rows(a, bl, t0, t1, rc)
                            if kind is not None:
                                strays[len(rows)] = (u.src == kind[0])
                            rows.append(r1)
                            starts.append(r0)
                    for j, lk in enumerate(u.locks):
                        if j > 0:
                            rows.append(u.branch_row)
                            starts.append(0)
                        locks.append(lk)
                for b, stray_right in strays.items():
                    attach_pair(locks[b], locks[b + 1], rows[b], stray_right)
                if prm.merge_overlaps and ranges is not None:
                    # manual joins (and auto-merged roots): lay buried locks side by side
                    stray_locks = {id(locks[b + 1] if right else locks[b]) for b, right in strays.items()}
                    lim = [(-1 if (b in strays or id(locks[b]) in stray_locks
                                   or id(locks[b + 1]) in stray_locks) else r)
                           for b, r in enumerate(rows)]
                    tile_root_overlaps(locks, limits=lim)
                pv0, pf0 = len(hair.verts), len(hair.faces)
                build_sheet(locks, rows, center, prm, hair, outline, start_rows=starts)
                pieces.append(({u.src for u in run}, run[0].mirrored, li,
                               pv0, len(hair.verts), pf0, len(hair.faces)))
    finally:
        prm.width = base_width
    if embed_hosts and prm.backface == 'CLOSED':
        bridge_embedded(hair, pieces, embed_hosts)
    return hair, outline
