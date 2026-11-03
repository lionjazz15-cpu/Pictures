# SPDX-License-Identifier: GPL-3.0-or-later
"""Geometry core: guide curve -> clean-topology toon hair strand mesh.

Everything here is pure math on mathutils types so it can be unit-tested
headless. The Blender-facing glue lives in ``build.py``.

Coordinate conventions for one strand ring (cross-section):
    T  tangent along the strand (root -> tip)
    N  "outward" direction (away from the head centre, perpendicular to T)
    B  width direction, B = T x N
A profile point (x, y) maps to  P + B * (x * width / 2) + N * (y * width).
x runs -1..1 across the strand, y is measured in units of strand width.
With this ordering, quads (i,j)->(i,j+1)->(i+1,j+1)->(i+1,j) face +N.
"""

import math
from mathutils import Vector, Matrix

EPS = 1e-8


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def _smoothstep(e0, e1, x):
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


class Center:
    """Head 'volume' used for orientation and spherical normals.

    The centre object's world matrix (including non-uniform scale) defines an
    ellipsoid; the outward direction at p is the ellipsoid gradient.
    """

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
        # mirror plane: through the centre, normal = centre's local X axis
        ax = matrix.to_3x3() @ Vector((1.0, 0.0, 0.0))
        self.mirror_axis = ax.normalized() if ax.length > EPS else Vector((1.0, 0.0, 0.0))

    def local(self, p):
        return self.inv @ p

    def radial(self, p):
        n = self.inv_t3 @ (self.inv @ p)
        if n.length < EPS:
            return Vector((0.0, 0.0, 1.0))
        return n.normalized()

    def head_coord(self, p):
        """(azimuth 0..1, height -1..1) on the unit head sphere."""
        d = self.inv @ p
        if d.length < EPS:
            return 0.0, 0.0
        d.normalize()
        az = math.atan2(d.x, -d.y) / (2.0 * math.pi) + 0.5
        return az, d.z

    def mirror(self, p):
        d = (p - self.location).dot(self.mirror_axis)
        return p - self.mirror_axis * (2.0 * d)


# --------------------------------------------------------------------------
# Curve sampling
# --------------------------------------------------------------------------

def sample_spline(spline, matrix, dense=32):
    """Densely sample a Blender spline in world space.

    Returns a list of (position, radius, tilt). Bezier and Poly are exact;
    NURBS is approximated with a Catmull-Rom through its control points.
    """
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
        out.append((matrix @ cos[-1], pts[-1].radius, pts[-1].tilt))
        return out

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
    """Arc-length resample to segments+1 rings.

    tip_bias > 0 packs rings slightly towards the tip, where the width changes
    fastest, so low-poly tips keep a clean silhouette.
    Returns list of (position, radius, tilt, t) with t = arc-length 0..1.
    """
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


# --------------------------------------------------------------------------
# Frames
# --------------------------------------------------------------------------

def compute_frames(points, center, twist=0.0, tilt_sign=1.0, smooth_passes=2):
    """Head-oriented, twist-free frames for each resampled ring.

    points: list of (position, radius, tilt, t)
    Returns list of (P, T, N, B).
    """
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
            # strand points straight out of the head: transport previous normal
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
            a = N[max(i - 1, 0)]
            c = N[min(i + 1, n - 1)]
            v = a + N[i] * 2.0 + c
            v = v - T[i] * v.dot(T[i])
            sm.append(v.normalized() if v.length > EPS else N[i])
        N = sm

    frames = []
    for i in range(n):
        ang = points[i][2] * tilt_sign + twist * points[i][3]
        Ni = N[i]
        if abs(ang) > 1e-6:
            Ni = Ni * math.cos(ang) + T[i].cross(Ni) * math.sin(ang)
        Bi = T[i].cross(Ni).normalized()
        frames.append((P[i], T[i], Ni, Bi))
    return frames


# --------------------------------------------------------------------------
# Shape functions
# --------------------------------------------------------------------------

def width_factor(t, root_ratio, bulge_pos, tip_shape, tip_min=0.0):
    """Anime strand silhouette: root -> swell -> pointed tip."""
    b = min(max(bulge_pos, 0.0), 0.95)
    if t < b and b > 1e-4:
        s = t / b
        w = root_ratio + (1.0 - root_ratio) * math.sin(s * math.pi * 0.5)
    else:
        s = (t - b) / (1.0 - b) if b < 1.0 else 1.0
        s = min(max(s, 0.0), 1.0)
        w = max(0.0, 1.0 - s ** max(tip_shape, 0.05))
    return max(w, tip_min)


def profile(kind, segments, curvature, thickness, closed):
    """Cross-section points for one ring.

    Returns (points, inner_flags, unit_offset_normals) where offset normals are
    2D outward directions (used for the outline hull) computed on the unit
    profile so they stay valid when the width shrinks to zero.
    """
    S = max(1, segments)
    if kind == 'RIDGE' and S % 2:
        S += 1
    xs = [-1.0 + 2.0 * j / S for j in range(S + 1)]

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
    else:  # LENS
        def env(x):
            return 1.0 - x * x

    if closed:
        front = [(x, mid(x) + 0.5 * thickness * env(x)) for x in xs]
        back = [(x, mid(x) - 0.5 * thickness * env(x)) for x in reversed(xs[1:-1])]
        pts = front + back
        inner = [0.0] * len(front) + [1.0] * len(back)
    else:
        pts = [(x, mid(x)) for x in xs]
        inner = [0.0] * len(pts)
    return pts, inner


def _unit_2d(x, y):
    l = math.hypot(x, y)
    return (x / l, y / l) if l > EPS else (0.0, 1.0)


def hull_loop(pts, closed):
    """Closed 2D loop around the profile with outward offset directions.

    Coordinates are in 'world-ish' units of a unit width strand
    (x scaled by 0.5, y by 1.0). Returns list of ((x, y), (nx, ny)) where
    (x, y) are profile units (to be scaled by the ring width) and (nx, ny)
    the outward direction (scaled by the outline width).
    """
    q = [(x * 0.5, y) for x, y in pts]
    loop = []
    if closed:
        m = len(q)
        for j in range(m):
            ax, ay = q[j - 1]
            bx, by = q[j]
            cx, cy = q[(j + 1) % m]
            # loop is clockwise (front left->right, back right->left):
            # outward normal of edge d is (-dy, dx)
            n1 = _unit_2d(-(by - ay), bx - ax)
            n2 = _unit_2d(-(cy - by), cx - bx)
            loop.append((pts[j], _unit_2d(n1[0] + n2[0], n1[1] + n2[1])))
        return loop

    m = len(q)
    norms = []
    for j in range(m):
        ax, ay = q[max(j - 1, 0)]
        cx, cy = q[min(j + 1, m - 1)]
        norms.append(_unit_2d(-(cy - ay), cx - ax))
    for j in range(m):
        loop.append((pts[j], norms[j]))
    tx, ty = _unit_2d(q[-1][0] - q[-2][0], q[-1][1] - q[-2][1])
    loop.append((pts[-1], (tx, ty)))
    for j in range(m - 1, -1, -1):
        loop.append((pts[j], (-norms[j][0], -norms[j][1])))
    tx, ty = _unit_2d(q[0][0] - q[1][0], q[0][1] - q[1][1])
    loop.append((pts[0], (tx, ty)))
    return loop


# --------------------------------------------------------------------------
# Geometry container
# --------------------------------------------------------------------------

class Geo:
    """Flat arrays describing a mesh; can be merged and written to bpy."""

    def __init__(self):
        self.verts = []
        self.faces = []
        self.uv = []          # per-vertex (u, v)
        self.t = []           # per-vertex root->tip 0..1
        self.inner = []       # per-vertex 1 = inner/back side
        self.head = []        # per-vertex (azimuth, height, t)
        self.normals = []     # per-vertex custom normal

    def extend(self, other):
        off = len(self.verts)
        self.verts += other.verts
        self.faces += [tuple(i + off for i in f) for f in other.faces]
        self.uv += other.uv
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

    def add_vert(self, co, uv, t, inner, head):
        self.verts.append(co)
        self.uv.append(uv)
        self.t.append(t)
        self.inner.append(inner)
        self.head.append(head)
        return len(self.verts) - 1


def _vertex_normals(verts, faces):
    acc = [Vector((0.0, 0.0, 0.0)) for _ in verts]
    for f in faces:
        # Newell normal (area weighted)
        n = Vector((0.0, 0.0, 0.0))
        k = len(f)
        for a in range(k):
            v1 = verts[f[a]]
            v2 = verts[f[(a + 1) % k]]
            n.x += (v1.y - v2.y) * (v1.z + v2.z)
            n.y += (v1.z - v2.z) * (v1.x + v2.x)
            n.z += (v1.x - v2.x) * (v1.y + v2.y)
        for i in f:
            acc[i] += n
    return acc


# --------------------------------------------------------------------------
# Strand builder
# --------------------------------------------------------------------------

class StrandParams:
    """Plain parameter bag (mirrors the PropertyGroup)."""

    length_segments = 12
    profile_segments = 4
    tip_bias = 0.3
    profile = 'LENS'
    backface = 'CLOSED'      # SINGLE / FLIPPED / CLOSED
    width = 0.03
    thickness = 0.25
    curvature = 0.12
    root_ratio = 0.7
    bulge_pos = 0.25
    tip_shape = 1.4
    tip_mode = 'POINT'       # POINT / BLUNT
    tip_min = 0.12
    twist = 0.0
    root_extend = 0.0
    flip_offset = 0.0003
    normal_mode = 'VOLUME'   # SMOOTH / VOLUME / PROXY
    normal_blend = 0.6
    outline = True
    outline_width = 0.0012
    outline_tip_ratio = 0.15
    outline_taper_start = 0.5

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def build_strand(samples, center, prm, hair, outline=None, mirrored=False):
    """Append one strand (and its outline hull) to the given Geo containers."""
    if prm.root_extend > 0.0 and len(samples) >= 2:
        p0, r0, t0 = samples[0]
        d = p0 - samples[1][0]
        if d.length > EPS:
            samples = [(p0 + d.normalized() * prm.root_extend, r0, t0)] + list(samples)

    pts = resample(samples, max(2, prm.length_segments), prm.tip_bias)
    if not pts:
        return
    frames = compute_frames(pts, center, twist=prm.twist, tilt_sign=-1.0 if mirrored else 1.0)

    closed = prm.backface == 'CLOSED'
    prof, inner_flags = profile(prm.profile, prm.profile_segments, prm.curvature, prm.thickness, closed)
    M = len(prof)
    point_tip = prm.tip_mode == 'POINT'
    nring = len(pts)
    ring_count = nring - 1 if point_tip else nring
    tip_min = 0.0 if point_tip else prm.tip_min

    widths = []
    for i in range(nring):
        _p, rad, _tilt, t = pts[i]
        wf = width_factor(t, prm.root_ratio, prm.bulge_pos, prm.tip_shape, tip_min)
        widths.append(prm.width * wf * max(rad, 0.0))

    g = Geo()
    rings = []
    for i in range(ring_count):
        P, T, N, B = frames[i]
        w = widths[i]
        t = pts[i][3]
        ring = []
        for j, (x, y) in enumerate(prof):
            co = P + B * (x * w * 0.5) + N * (y * w)
            az, h = center.head_coord(co)
            ring.append(g.add_vert(co, ((x + 1.0) * 0.5, t), t, inner_flags[j], (az, h, t)))
        rings.append(ring)
    tip = None
    if point_tip:
        P = frames[-1][0]
        az, h = center.head_coord(P)
        tip = g.add_vert(P.copy(), (0.5, 1.0), 1.0, 0.0, (az, h, 1.0))

    span = M if closed else M - 1
    for i in range(ring_count - 1):
        a, b = rings[i], rings[i + 1]
        for j in range(span):
            j2 = (j + 1) % M
            g.faces.append((a[j], a[j2], b[j2], b[j]))
    last = rings[-1]
    if point_tip:
        for j in range(span):
            g.faces.append((last[j], last[(j + 1) % M], tip))
    elif closed:
        g.faces.append(tuple(last))
    if closed and getattr(prm, 'root_cap', False):
        g.faces.append(tuple(reversed(rings[0])))

    # ---- normals -------------------------------------------------------
    vn = _vertex_normals(g.verts, g.faces)
    normals = []
    for idx, n in enumerate(vn):
        n = n.normalized() if n.length > EPS else frames[0][2].copy()
        if prm.normal_mode == 'VOLUME' and prm.normal_blend > 0.0:
            r = center.radial(g.verts[idx])
            if n.dot(r) < 0.0:
                r = -r
            n = n.lerp(r, prm.normal_blend)
            n = n.normalized() if n.length > EPS else r
        normals.append(n)
    g.normals = normals

    # ---- FLIPPED: duplicate sheet as back side polygons ------------------
    if prm.backface == 'FLIPPED':
        nv = len(g.verts)
        front_faces = list(g.faces)
        for idx in range(nv):
            co = g.verts[idx] - normals[idx] * prm.flip_offset
            g.add_vert(co, g.uv[idx], g.t[idx], 1.0, g.head[idx])
        g.normals = normals + [-n for n in normals]
        for f in front_faces:
            g.faces.append(tuple(i + nv for i in reversed(f)))

    hair.extend(g)

    if outline is None or not prm.outline or prm.outline_width <= 0.0:
        return

    # ---- outline hull (inverted hull, generated, never self-intersecting
    #      at the edges because it expands laterally as well) -------------
    loop = hull_loop(prof, closed)
    H = len(loop)
    o = Geo()
    orings = []
    for i in range(ring_count):
        P, T, N, B = frames[i]
        w = widths[i]
        t = pts[i][3]
        f = _smoothstep(prm.outline_taper_start, 1.0, t)
        ow = prm.outline_width * (1.0 + (prm.outline_tip_ratio - 1.0) * f)
        ring = []
        for (x, y), (nx, ny) in loop:
            co = P + B * (x * w * 0.5 + nx * ow) + N * (y * w + ny * ow)
            ring.append(o.add_vert(co, ((x + 1.0) * 0.5, t), t, 0.0, (0.0, 0.0, t)))
        orings.append(ring)
    otip = None
    if point_tip:
        P, T = frames[-1][0], frames[-1][1]
        ow_tip = prm.outline_width * prm.outline_tip_ratio
        otip = o.add_vert(P + T * ow_tip, (0.5, 1.0), 1.0, 0.0, (0.0, 0.0, 1.0))
    for i in range(ring_count - 1):
        a, b = orings[i], orings[i + 1]
        for j in range(H):
            j2 = (j + 1) % H
            o.faces.append((a[j], b[j], b[j2], a[j2]))   # reversed winding
    if point_tip:
        last = orings[-1]
        for j in range(H):
            o.faces.append((last[(j + 1) % H], last[j], otip))
    else:
        o.faces.append(tuple(reversed(orings[-1])))
    on = _vertex_normals(o.verts, o.faces)
    o.normals = [n.normalized() if n.length > EPS else Vector((0.0, 0.0, 1.0)) for n in on]
    outline.extend(o)
