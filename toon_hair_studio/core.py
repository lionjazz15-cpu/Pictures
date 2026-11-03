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
Independent strands are simply sheets with one lock.
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


def compute_frames(points, center, twist=0.0, tilt_sign=1.0, smooth_passes=2):
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
        ang = points[i][2] * tilt_sign + twist * points[i][3]
        Ni = N[i]
        if abs(ang) > 1e-6:
            Ni = Ni * math.cos(ang) + T[i].cross(Ni) * math.sin(ang)
        frames.append((P[i], T[i], Ni, T[i].cross(Ni).normalized()))
    return frames


# --------------------------------------------------------------------------
# shape functions
# --------------------------------------------------------------------------

def width_factor(t, root_ratio, bulge_pos, tip_shape, tip_min=0.0):
    """Anime lock silhouette: root -> swell -> tip."""
    b = min(max(bulge_pos, 0.0), 0.95)
    if t < b and b > 1e-4:
        s = t / b
        w = root_ratio + (1.0 - root_ratio) * math.sin(s * math.pi * 0.5)
    else:
        s = (t - b) / (1.0 - b) if b < 1.0 else 1.0
        s = min(max(s, 0.0), 1.0)
        w = max(0.0, 1.0 - s ** max(tip_shape, 0.05))
    return max(w, tip_min)


def profile_funcs(kind, curvature, thickness):
    """Return (xs builder, mid(x), env(x)) for a cross-section type."""
    if kind == 'RIDGE':
        def mid(x):
            return curvature * (1.0 - abs(x))
    else:
        def mid(x):
            return curvature * (1.0 - x * x)
    if kind == 'ROUND':
        def env(x):
            return math.sqrt(max(0.0, 1.0 - x * x))
    elif kind == 'RIDGE':
        def env(x):
            return 1.0 - abs(x)
    elif kind == 'FLAT':
        def env(x):
            return 1.0 - x ** 4
    else:
        def env(x):
            return 1.0 - x * x
    return mid, env


# --------------------------------------------------------------------------
# geometry container
# --------------------------------------------------------------------------

class Geo:
    """Flat mesh arrays; per-vertex attributes + per-corner UVs."""

    def __init__(self):
        self.verts = []
        self.faces = []
        self.face_uv = []      # per face: tuple of (u, v) per corner
        self.t = []            # root -> tip 0..1
        self.inner = []        # 1 = inner / back side
        self.head = []         # (azimuth, height, t)
        self.normals = []      # custom normal per vertex

    def extend(self, other):
        off = len(self.verts)
        self.verts += other.verts
        self.faces += [tuple(i + off for i in f) for f in other.faces]
        self.face_uv += other.face_uv
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

    def add_face(self, f, uv):
        self.faces.append(tuple(f))
        self.face_uv.append(tuple(uv))

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
    normal_mode = 'VOLUME'    # VOLUME / SMOOTH / PROXY
    normal_blend = 0.6
    outline = True
    outline_width = 0.0012
    outline_tip_ratio = 0.15
    outline_taper_start = 0.5
    # connected sheets
    connect = False
    connect_ratio = 1.8
    split = 0.55
    notch_soft = 0.5
    seam_thickness = 0.6
    order = 'AZIMUTH'         # AZIMUTH / CURVE

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


# --------------------------------------------------------------------------
# locks
# --------------------------------------------------------------------------

class Lock:
    __slots__ = ("frames", "widths", "ts", "root", "az", "rev", "order")

    def __init__(self, frames, widths, ts, center, order):
        self.frames = frames
        self.widths = widths
        self.ts = ts
        self.root = frames[0][0]
        self.az = center.head_coord(self.root)[0]
        self.order = order
        # does +B point towards increasing azimuth?
        i = max(1, len(frames) // 3)
        P, _T, _N, B = frames[i]
        w = max(widths[i], 1e-4)
        a0 = center.head_coord(P)[0]
        a1 = center.head_coord(P + B * w)[0]
        d = (a1 - a0 + 0.5) % 1.0 - 0.5
        self.rev = d < 0.0


def make_lock(samples, center, prm, mirrored=False, order=0):
    if prm.root_extend > 0.0 and len(samples) >= 2:
        p0, r0, t0 = samples[0]
        d = p0 - samples[1][0]
        if d.length > EPS:
            samples = [(p0 + d.normalized() * prm.root_extend, r0, t0)] + list(samples)
    pts = resample(samples, max(2, prm.length_segments), prm.tip_bias)
    if not pts:
        return None
    frames = compute_frames(pts, center, twist=prm.twist, tilt_sign=-1.0 if mirrored else 1.0)
    tip_min = 0.0 if prm.tip_mode == 'POINT' else prm.tip_min
    widths = [prm.width * width_factor(p[3], prm.root_ratio, prm.bulge_pos, prm.tip_shape, tip_min)
              * max(p[1], 0.0) for p in pts]
    return Lock(frames, widths, [p[3] for p in pts], center, order)


def cluster_locks(locks, prm):
    """Split locks into sheets (runs of adjacent locks)."""
    if not prm.connect:
        return [[lk] for lk in locks]
    if prm.order == 'CURVE':
        seq = sorted(locks, key=lambda l: l.order)
    else:
        seq = sorted(locks, key=lambda l: l.az)
        if len(seq) > 2:
            # start the sequence after the largest azimuth gap
            gaps = [((seq[(i + 1) % len(seq)].az - seq[i].az) % 1.0, i) for i in range(len(seq))]
            _g, i = max(gaps)
            seq = seq[i + 1:] + seq[:i + 1]
    sheets = [[seq[0]]] if seq else []
    for lk in seq[1:]:
        prev = sheets[-1][-1]
        limit = prm.connect_ratio * 0.5 * (prev.widths[0] + lk.widths[0]) / max(prm.root_ratio, 0.05)
        limit = max(limit, prm.connect_ratio * prm.width)
        if (lk.root - prev.root).length <= limit:
            sheets[-1].append(lk)
        else:
            sheets.append([lk])
    return sheets


# --------------------------------------------------------------------------
# sheet builder
# --------------------------------------------------------------------------

def build_sheet(locks, center, prm, hair, outline=None):
    K = len(locks)
    closed = prm.backface == 'CLOSED'
    S = max(1, prm.profile_segments)
    if prm.profile == 'RIDGE' and S % 2:
        S += 1
    if closed and S < 2:
        S = 2
    xs = [-1.0 + 2.0 * j / S for j in range(S + 1)]
    mid, env = profile_funcs(prm.profile, prm.curvature, prm.thickness)

    nr = min(len(lk.frames) for lk in locks)
    point_tip = prm.tip_mode == 'POINT'
    rc = nr - 1 if point_tip else nr           # rings with full cross-section
    ts = locks[0].ts

    # last shared row (inclusive); -1 = nothing shared
    i_split = -1
    if K > 1:
        for i in range(rc):
            if ts[i] <= prm.split + 1e-9:
                i_split = i
        i_split = min(i_split, rc - 2)

    def shared(i):
        return i <= i_split

    def notch(i):
        if shared(i):
            return 0.0
        t0 = ts[i_split] if i_split >= 0 else 0.0
        span = max((1.0 - t0) * prm.notch_soft, 1e-4)
        return smoothstep(t0, t0 + span, ts[i])

    # ---- edges (in order space: oL = side towards previous lock) ----------
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
    for k in range(1, K):
        for i in range(rc):
            m = (oR[k - 1][i] + oL[k][i]) * 0.5
            s = notch(i)
            eR[k - 1][i] = m.lerp(oR[k - 1][i], s)
            eL[k][i] = m.lerp(oL[k][i], s)

    # ---- vertex keys --------------------------------------------------------
    def is_shared_col(k, i, c):
        return shared(i) and ((c == 0 and k > 0) or (c == S and k < K - 1))

    def key(side, k, i, c):
        if c == 0 and k > 0 and shared(i):
            return (side, i, k - 1, S)
        if closed and c in (0, S) and not is_shared_col(k, i, c):
            return ('E', i, k, c)
        return (side, i, k, c)

    acc = {}
    order = []
    meta = {}

    def put(kk, co, t, inner):
        if kk in acc:
            acc[kk][0] += co
            acc[kk][1] += 1
        else:
            acc[kk] = [co.copy(), 1]
            order.append(kk)
            meta[kk] = (t, inner)

    def env_at(k, i, c, x):
        e = env(x)
        g = prm.seam_thickness
        if is_shared_col(k, i, 0) and c * 2 <= S:
            e = g + (1.0 - g) * e
        elif is_shared_col(k, i, S) and c * 2 >= S:
            e = g + (1.0 - g) * e
        return e

    for k, lk in enumerate(locks):
        for i in range(rc):
            _P, _T, N, _B = lk.frames[i]
            L, R = eL[k][i], eR[k][i]
            w = (R - L).length
            t = ts[i]
            for c in range(S + 1):
                x = xs[c]
                base = L.lerp(R, (x + 1.0) * 0.5)
                ym = mid(x) * w
                if closed:
                    ye = 0.5 * prm.thickness * env_at(k, i, c, x) * w
                    put(key('F', k, i, c), base + N * (ym + ye), t, 0.0)
                    put(key('B', k, i, c), base + N * (ym - ye), t, 1.0)
                else:
                    put(key('F', k, i, c), base + N * ym, t, 0.0)
        if point_tip:
            put(('T', k), lk.frames[nr - 1][0], 1.0, 0.0)

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
        az, h = center.head_coord(co)
        g.head.append((az, h, t))

    def V(side, k, i, c):
        return index[key(side, k, i, c)]

    # ---- faces --------------------------------------------------------------
    for k, lk in enumerate(locks):
        # local column j runs along +B; order column c = j or S - j
        cols = list(range(S + 1)) if not lk.rev else list(range(S, -1, -1))
        us = [j / S for j in range(S + 1)]
        for i in range(rc - 1):
            v0, v1 = ts[i], ts[i + 1]
            for j in range(S):
                ca, cb = cols[j], cols[j + 1]
                ua, ub = us[j], us[j + 1]
                g.add_face((V('F', k, i, ca), V('F', k, i, cb), V('F', k, i + 1, cb), V('F', k, i + 1, ca)),
                           ((ua, v0), (ub, v0), (ub, v1), (ua, v1)))
                if closed:
                    g.add_face((V('B', k, i, cb), V('B', k, i, ca), V('B', k, i + 1, ca), V('B', k, i + 1, cb)),
                               ((ub, v0), (ua, v0), (ua, v1), (ub, v1)))
        # tip
        i = rc - 1
        vt = ts[i]
        if closed:
            loop = [(V('F', k, i, cols[j]), us[j]) for j in range(S + 1)]
            loop += [(V('B', k, i, cols[j]), us[j]) for j in range(S - 1, 0, -1)]
        else:
            loop = [(V('F', k, i, cols[j]), us[j]) for j in range(S + 1)]
        if point_tip:
            tip = index[('T', k)]
            span = len(loop) if closed else len(loop) - 1
            for j in range(span):
                a, ua = loop[j]
                b, ub = loop[(j + 1) % len(loop)]
                g.add_face((a, b, tip), ((ua, vt), (ub, vt), (0.5, 1.0)))
        elif closed:
            g.add_face([a for a, _u in loop], [(u, vt) for _a, u in loop])

    # notch walls: close the gap where two locks separate (closed only)
    if closed and i_split >= 0:
        i, i1 = i_split, i_split + 1
        for k in range(1, K):
            Fb = V('F', k - 1, i, S)
            Bb = V('B', k - 1, i, S)
            E1 = V('F', k - 1, i1, S)
            E2 = V('F', k, i1, 0)
            f = [Fb, E2, Bb, E1]
            n = newell([g.verts[q] for q in f])
            T = locks[k].frames[i][1] + locks[k - 1].frames[i][1]
            if n.dot(T) < 0.0:
                f.reverse()
            u, v = 0.5, ts[i]
            g.add_face(f, ((u, v), (u, ts[i1]), (u, v), (u, ts[i1])))

    # optional root cap (closed)
    if closed and prm.root_cap:
        front, back = [], []
        for k, lk in enumerate(locks):
            for c in range(S + 1):
                for side, lst in (('F', front), ('B', back)):
                    vi = V(side, k, 0, c)
                    if not lst or lst[-1] != vi:
                        lst.append(vi)
        ring = front + [b for b in reversed(back) if b not in (front[0], front[-1])]
        n = newell([g.verts[q] for q in ring])
        T0 = sum((lk.frames[0][1] for lk in locks), Vector())
        if n.dot(T0) > 0.0:
            ring.reverse()
        g.add_face(ring, [(0.5, 0.0)] * len(ring))

    # ---- custom normals -------------------------------------------------------
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
            g.add_face([q + nv for q in reversed(f)], list(reversed(uv)))

    hair.extend(g)


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

def build_group(spline_samples, center, prm, mirror=False, with_outline=True):
    """spline_samples: list of sample lists -> (hair Geo, outline Geo|None)."""
    locks = []
    for n, s in enumerate(spline_samples):
        lk = make_lock(s, center, prm, False, n)
        if lk:
            locks.append(lk)
        if mirror:
            ms = [(center.mirror(p), r, t) for p, r, t in s]
            lk = make_lock(ms, center, prm, True, n + 0.5)
            if lk:
                locks.append(lk)
    hair = Geo()
    outline = Geo() if (with_outline and prm.outline) else None
    for sheet in cluster_locks(locks, prm):
        build_sheet(sheet, center, prm, hair, outline)
    return hair, outline
