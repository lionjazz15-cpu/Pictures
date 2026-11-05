"""Unified group mesh (一体化).

All locks of a group (per layer) sit on one lateral chain, ordered around the
head. Row by row, neighbours that touch or overlap form a *component*: its
cross-section is ONE smooth profile blended from the member locks in
curvilinear (polar) coordinates around the head, sampled with every member's
columns. Where locks separate, the component splits with a clean crotch (two
quads); where they meet again it merges the same way. The result is one quad
body per group instead of locks glued at their edges: overlapping, buried and
crossing-at-the-root locks simply melt into it and branch out where their
guides part.

No bpy dependency (mathutils only), like core.py.
"""

import math

from mathutils import Vector

from .core import (EPS, CENTER_COLUMN_PROFILES, Geo, finish_sheet, profile_funcs, smoothstep,
                   _orient_like_neighbours, _relax)

XT = [-2.5 + 5.0 * j / 50 for j in range(51)]   # lock section samples (with extension)


def _bo(lk, i):
    """Lateral axis of a lock at row i, pointing along the chain order."""
    B = lk.frames[i][3]
    return -B if lk.rev else B


# --------------------------------------------------------------------------
# which neighbours are joined on which row
# --------------------------------------------------------------------------

def _clean_runs(row):
    n = len(row)
    row = list(row)
    for i in range(1, n - 1):                      # no one-row gaps
        if row[i - 1] and row[i + 1] and not row[i]:
            row[i] = True
    i = 0
    while i < n:
        if not row[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and row[j + 1]:
            j += 1
        # a join needs two rows: otherwise the merge and the split crotch (or
        # the root border) would meet in one apex (non-manifold)
        if j - i < 1:
            for r in range(i, j + 1):
                row[r] = False
        i = j + 1
    return row


def join_table(locks, rc, prm, force=None):
    """J[b][i]: locks b and b+1 are one body on row i.

    force(b, i) -> None (join where they touch / overlap), a float (join while
    closer than that many half widths), True (always join) or False (never)."""
    K = len(locks)
    J = []
    point_tip = prm.tip_mode == 'POINT'
    for b in range(K - 1):
        la, lb = locks[b], locks[b + 1]
        row = []
        for i in range(rc):
            f = force(b, i) if force else None
            if f is True or f is False:
                row.append(f)
                continue
            Pa, Ta, Na, _ = la.frames[i]
            Pb, Tb, Nb, _ = lb.frames[i]
            d = _bo(la, i) + _bo(lb, i)
            if Ta.dot(Tb) < 0.5 or d.length < EPS:
                row.append(False)
                continue
            d.normalize()
            D = Pb - Pa
            half = 0.5 * (la.widths[i] + lb.widths[i])
            if half < 1e-7:
                row.append(False)
                continue
            n = Na + Nb
            n = n.normalized() if n.length > EPS else Na
            # stacked far apart (one lock floating over the other): not one body
            if abs(D.dot(n)) > prm.thickness * half * 1.5 + 0.35 * half:
                row.append(False)
                continue
            # outside the join zone (or with joining off) neighbours become
            # one body only where they clearly overlap (buried / drawn on top
            # of each other); touching is not enough
            if not f and min(la.widths[i], lb.widths[i]) < 0.45 * max(la.widths[i], lb.widths[i]):
                # a thin stray stuck into a lock does not melt into it by
                # itself (that would reshape the host): use attach for that
                row.append(False)
                continue
            lim = half * (f if f else (0.3 if prm.connect else 0.6))
            row.append(D.dot(d) < lim)
        if point_tip and rc > 0:
            row[rc - 1] = False                    # every lock ends in its own tip
        J.append(_clean_runs(row))
    return J


def spread_parted(locks, J, rc, passes=4, keep=0.92):
    """Where neighbours are separate but still overlap (just after a split,
    or locks drawn on top of each other down to the tips), push them apart
    sideways until they only touch, so every parted lock shows as its own
    clean spike instead of cutting through its neighbour."""
    K = len(locks)
    if K < 2:
        return
    nr = len(locks[0].frames)
    shift = [[0.0] * nr for _ in range(K)]
    # only neighbours that were one body further up (locks that never
    # touched keep exactly the shape they were drawn with)
    parted = [[any(J[b][:i]) and not J[b][i] for i in range(rc)] for b in range(K - 1)]
    for i in range(rc):
        for _ in range(passes):
            moved = False
            for b in range(K - 1):
                if not parted[b][i]:
                    continue
                la, lb = locks[b], locks[b + 1]
                if la.frames[i][1].dot(lb.frames[i][1]) < 0.5:
                    continue
                d = _bo(la, i) + _bo(lb, i)
                if d.length < EPS:
                    continue
                d.normalize()
                Pa = la.frames[i][0] + _bo(la, i) * shift[b][i]
                Pb = lb.frames[i][0] + _bo(lb, i) * shift[b + 1][i]
                need = keep * 0.5 * (la.widths[i] + lb.widths[i]) - (Pb - Pa).dot(d)
                if need > 1e-7:
                    # never more than about one width: the guides keep their shape
                    lim_a, lim_b = 0.8 * la.widths[i], 0.8 * lb.widths[i]
                    sa = max(shift[b][i] - 0.5 * need, -lim_a) - shift[b][i]
                    sb = min(shift[b + 1][i] + 0.5 * need, lim_b) - shift[b + 1][i]
                    if sa < -1e-9 or sb > 1e-9:
                        shift[b][i] += sa
                        shift[b + 1][i] += sb
                        moved = True
            if not moved:
                break
    for k, lk in enumerate(locks):
        if not any(shift[k]):
            continue
        if rc < nr:
            shift[k][nr - 1] = shift[k][rc - 1]       # the tip point follows its ring
        sm = list(shift[k])
        for i in range(1, nr - 1):                    # no kinks along the lock
            if shift[k][i] == 0.0:
                continue
            sm[i] = 0.25 * shift[k][i - 1] + 0.5 * shift[k][i] + 0.25 * shift[k][i + 1]
        lk.frames = [(P + _bo(lk, i) * sm[i], T, N, B) for i, (P, T, N, B) in enumerate(lk.frames)]


# --------------------------------------------------------------------------
# cross-section of one component on one row
# --------------------------------------------------------------------------

class _Section:
    """Polar coordinates around a pivot behind the component, in the plane of
    the members' mean tangent; d = offset along that tangent."""

    def __init__(self, locks, members, i, center):
        Ps = [locks[k].frames[i][0] for k in members]
        P = sum(Ps, Vector()) / len(Ps)
        T = sum((locks[k].frames[i][1] for k in members), Vector())
        T = T.normalized() if T.length > EPS else locks[members[0]].frames[i][1]
        N = sum((locks[k].frames[i][2] for k in members), Vector())
        N = N - T * N.dot(T)
        N = N.normalized() if N.length > EPS else locks[members[0]].frames[i][2]
        B = T.cross(N)
        if sum((_bo(locks[k], i) for k in members), Vector()).dot(B) < 0.0:
            B = -B
        self.P = P
        R = center.radius
        rho0 = min(max((P - center.location).dot(N), 0.6 * R), 3.0 * R)
        self.O = P - N * rho0
        self.T, self.N, self.B = T, N, B

    def polar(self, p):
        q = p - self.O
        u, v = q.dot(self.B), q.dot(self.N)
        return math.atan2(u, v), math.sqrt(u * u + v * v), q.dot(self.T)

    def point(self, th, rho, d):
        return self.O + (self.B * math.sin(th) + self.N * math.cos(th)) * rho + self.T * d


def _on_plane(lk, i, P0, T0, last):
    """(P, N, Bo, W, t) of a lock where its guide crosses the plane (P0, T0)
    near row i, so the rows of a joined body lie on one plane (straight
    across) even where the locks' own row layouts differ (branches, lengths).
    Stays within +-0.45 rows so consecutive rows never fold."""
    best = None
    for j in range(max(0, i - 2), min(last, i + 2)):
        da = (lk.frames[j][0] - P0).dot(T0)
        db = (lk.frames[j + 1][0] - P0).dot(T0)
        if (da <= 0.0 < db) or (da < 0.0 <= db):
            s = j + (-da / (db - da) if db != da else 0.0)
            if best is None or abs(s - i) < abs(best - i):
                best = s
    s = i if (best is None or i == 0) else min(max(best, i - 0.45), i + 0.45)   # roots stay put
    s = min(max(s, 0.0), float(last))
    j = min(int(s), last - 1) if last > 0 else 0
    f = s - j
    a, b = lk.frames[j], lk.frames[min(j + 1, last)]
    P = a[0].lerp(b[0], f)
    N = a[2].lerp(b[2], f)
    N = N.normalized() if N.length > EPS else a[2]
    Bo = _bo(lk, j).lerp(_bo(lk, min(j + 1, last)), f)
    Bo = Bo.normalized() if Bo.length > EPS else _bo(lk, j)
    W = lk.widths[j] + (lk.widths[min(j + 1, last)] - lk.widths[j]) * f
    t = lk.ts[j] + (lk.ts[min(j + 1, last)] - lk.ts[j]) * f
    return P, N, Bo, W, t


def _monotone(vals):
    out = [vals[0]]
    for v in vals[1:]:
        out.append(max(v, out[-1] + 1e-9))
    return out


def _interp(xs, ys, x):
    """Piecewise linear y(x) on increasing xs (clamped)."""
    if x <= xs[0]:
        return 0, 0.0
    if x >= xs[-1]:
        return len(xs) - 2, 1.0
    lo, hi = 0, len(xs) - 1
    while hi - lo > 1:
        m = (lo + hi) // 2
        if xs[m] <= x:
            lo = m
        else:
            hi = m
    span = xs[hi] - xs[lo]
    return lo, ((x - xs[lo]) / span if span > 0.0 else 0.0)


def component_columns(locks, ka, kb, i, off, prm, center, closed, bevel, lam=None, last=None):
    """Front / back positions of the columns off[ka] .. off[kb+1] on row i
    (lock k owns the columns off[k] .. off[k+1]).

    lam(b) in 0..1: how far the territory boundary between locks b and b+1
    follows the real seam (1, near a split) instead of an even share (0)."""
    members = list(range(ka, kb + 1))
    sec = _Section(locks, members, i, center)
    if len(members) > 1 and last:
        fr = {k: _on_plane(locks[k], i, sec.P, sec.T, last) for k in members}
    else:
        fr = {k: (locks[k].frames[i][0], locks[k].frames[i][2], _bo(locks[k], i), locks[k].widths[i],
                  locks[k].ts[i]) for k in members}
    mid, env, back_ratio = profile_funcs(prm.profile, prm.curvature, prm.thickness)
    k_front, k_back = 2.0 - back_ratio, back_ratio
    half_t = 0.5 * prm.thickness
    smooth = prm.connect_style == 'SMOOTH'
    mid0 = mid(0.0)

    # lateral extent of every member (edges without the arch)
    ext = {}
    for k in members:
        P, _N, Bo, W, _t = fr[k]
        hw = 0.5 * W
        lo = sec.polar(P - Bo * hw)[0]
        hi = sec.polar(P + Bo * hw)[0]
        ext[k] = (min(lo, hi), max(lo, hi))
    thL = min(e[0] for e in ext.values())
    thR = max(e[1] for e in ext.values())
    span = max(thR - thL, 1e-9)
    tol = 0.02 * span

    tables = {}
    for k in members:
        P, Nk, Bo, W, tk = fr[k]
        open_l = ext[k][0] <= thL + tol
        open_r = ext[k][1] >= thR - tol
        ths, rhos, ds = [], [], []
        for x in XT:
            xc = min(max(x, -1.0), 1.0)
            inner = (x < 0.0 and not open_l) or (x >= 0.0 and not open_r)
            a = mid0 if (smooth and inner) else mid(xc)
            th, rho, d = sec.polar(P + Bo * (x * 0.5 * W) + Nk * (a * W))
            ths.append(th)
            rhos.append(rho)
            ds.append(d)
        tables[k] = (_monotone(ths), rhos, ds, Nk, half_t * W, tk)

    def x_at(k, th):
        ths = tables[k][0]
        j, f = _interp(ths, None, th)
        return j, f, XT[j] + (XT[j + 1] - XT[j]) * f

    # envelope: thickness tapers only at the component's outer edges
    kl = min(members, key=lambda k: ext[k][0])
    kr = max(members, key=lambda k: ext[k][1])
    hl = max(0.5 * (ext[kl][1] - ext[kl][0]), 1e-9)
    hr = max(0.5 * (ext[kr][1] - ext[kr][0]), 1e-9)

    # groove style: dips where neighbouring members meet
    seams = []
    if not smooth and len(members) > 1:
        for k in members[:-1]:
            s = 0.5 * (ext[k][1] + ext[k + 1][0])
            hm = 0.25 * (ext[k][1] - ext[k][0] + ext[k + 1][1] - ext[k + 1][0])
            seams.append((s, max(hm, 1e-9)))
    g_seam = prm.seam_thickness

    def envelope(th):
        xl = -1.0 + (th - thL) / hl
        xr = 1.0 - (thR - th) / hr
        e = 1.0
        if xl < 0.0:
            e = min(e, env(max(xl, -1.0)))
        if xr > 0.0:
            e = min(e, env(min(xr, 1.0)))
        for s, hm in seams:
            e *= 1.0 - (1.0 - g_seam) * math.exp(-((th - s) / (0.6 * hm)) ** 2)
        return e

    def evaluate(th, edge):
        wsum = rho = d = tau = tt = 0.0
        Nn = Vector()
        for k in members:
            ths, rhos, ds, Nk, tk, tv = tables[k]
            j, f, x = x_at(k, th)
            w = math.exp(-(x ** 4) / 1.5) + 1e-9
            rho += w * (rhos[j] + (rhos[j + 1] - rhos[j]) * f)
            d += w * (ds[j] + (ds[j + 1] - ds[j]) * f)
            tau += w * tk
            tt += w * tv
            Nn += Nk * w
            wsum += w
        rho, d, tau, tt = rho / wsum, d / wsum, tau / wsum, tt / wsum
        Nn = Nn.normalized() if Nn.length > EPS else sec.N
        base = sec.point(th, rho, d)
        if not closed:
            return base, base, tt
        e = (prm.bevel if bevel else 0.0) if edge else envelope(th)
        h = tau * e
        return base + Nn * (h * k_front), base - Nn * (h * k_back), tt

    # territories: each member's columns spread over its own share of the body
    # (even share by width far from splits -> evenly spaced columns; the
    # real seam near a split so the crotch sits where the locks part)
    n = len(members)
    ws = [max(fr[k][3], 1e-9) for k in members]
    acc, tot = 0.0, sum(ws)
    bounds = [thL]
    for m in range(n - 1):
        k = members[m]
        acc += ws[m]
        uni = thL + span * acc / tot
        geo = 0.5 * (ext[k][1] + ext[k + 1][0])
        f = lam(k) if lam else 0.8
        bounds.append(uni + (geo - uni) * f)
    bounds.append(thR)
    gap = 0.25 * span / n
    for m in range(1, n):
        bounds[m] = max(bounds[m], bounds[m - 1] + gap)
    for m in range(n - 1, 0, -1):
        bounds[m] = min(bounds[m], bounds[m + 1] - gap)

    cols = {}
    for m, k in enumerate(members):
        a, b = bounds[m], bounds[m + 1]
        Sk = off[k + 1] - off[k]
        for c in range(Sk + 1):
            gi = off[k] + c
            if gi in cols:
                continue
            edge = (gi == off[ka]) or (gi == off[kb + 1])
            cols[gi] = evaluate(a + (b - a) * c / Sk, edge)
    return cols


# --------------------------------------------------------------------------
# mesh
# --------------------------------------------------------------------------

def build_unified(locks, center, prm, hair, outline=None, force=None):
    """locks: one group layer in chain order -> appended to hair as one body.
    Returns the join table (for tests / diagnostics)."""
    K = len(locks)
    if K == 0:
        return []
    closed = prm.backface == 'CLOSED'
    S = max(1, prm.profile_segments)
    if prm.profile in CENTER_COLUMN_PROFILES and S % 2:
        S += 1
    if closed and S < 2:
        S = 2
    # columns per lock follow its width, so narrow (branched / thin) locks are
    # not denser than wide ones: even column spacing over the whole body
    even = prm.profile in CENTER_COLUMN_PROFILES
    off = [0]
    for lk in locks:
        n = int(round(S * max(lk.widths) / max(prm.width, 1e-9)))
        n = min(max(n, 2), 2 * S)
        if even and n % 2:
            n += 1
        off.append(off[-1] + n)
    bevel = closed and prm.bevel > 1e-6
    nr = min(len(lk.frames) for lk in locks)
    point_tip = prm.tip_mode == 'POINT'
    rc = nr - 1 if point_tip else nr
    if rc < 2:
        return []
    J = join_table(locks, rc, prm, force)
    spread_parted(locks, J, rc)

    def joined(b, i):
        return 0 <= b < K - 1 and J[b][i]

    def components(i):
        out, ka = [], 0
        for k in range(1, K + 1):
            if k == K or not joined(k - 1, i):
                out.append((ka, k - 1))
                ka = k
        return out

    g = Geo()
    index = {}

    def add(key, co, t, inner):
        index[key] = len(g.verts)
        g.verts.append(co)
        g.t.append(t)
        g.inner.append(inner)
        g.head.append((0.0, 0.0, t))

    # territory boundaries follow the real seam only near splits / merges
    ramp = max(2.0, 0.3 * rc)
    lam_t = []
    for b in range(K - 1):
        apex = [i if J[b][i] else i + 1 for i in range(rc - 1) if J[b][i] != J[b][i + 1]]
        row = []
        for i in range(rc):
            dist = min((abs(i - a) for a in apex), default=None)
            row.append(0.0 if dist is None else 1.0 - smoothstep(0.0, ramp, dist))
        lam_t.append(row)

    for i in range(rc):
        for ka, kb in components(i):
            cols = component_columns(locks, ka, kb, i, off, prm, center, closed, bevel,
                                     lam=lambda b, i=i: lam_t[b][i], last=rc - 1)
            # one v per row inside a body: straight UV rows, even outline taper
            t_row = sum(c[2] for c in cols.values()) / len(cols)
            for gi, (F, Bk, _t) in cols.items():
                t = t_row
                var = 'l' if (gi == off[ka] and ka > 0) else ('r' if (gi == off[kb + 1] and kb < K - 1) else None)
                edge = gi in (off[ka], off[kb + 1])
                if not closed:
                    add(('F', i, gi, var), F, t, 0.0)
                elif edge and not bevel:
                    add(('E', i, gi, var), F, t, 0.0)
                else:
                    add(('F', i, gi, var), F, t, 0.0)
                    add(('B', i, gi, var), Bk, t, 1.0)

    def is_edge(i, gi, k):
        return ((gi == off[k] and not joined(k - 1, i)) or
                (gi == off[k + 1] and not joined(k, i)))

    def V(side, i, gi, k):
        var = None
        if gi == off[k] and k > 0 and not joined(k - 1, i):
            var = 'l'
        elif gi == off[k + 1] and k < K - 1 and not joined(k, i):
            var = 'r'
        if not closed:
            side = 'F'
        elif not bevel and is_edge(i, gi, k):
            side = 'E'
        return index[(side, i, gi, var)]

    total = float(off[K])

    def uv(lk, i, gi):
        q = index.get(('F', i, gi, None)) or index.get(('E', i, gi, None))
        if q is None:
            q = index.get(('F', i, gi, 'l')) or index.get(('E', i, gi, 'l'))
        return (gi / total, g.t[q] if q is not None else lk.ts[i])

    extra = []           # bands, walls, tips, caps: oriented by their neighbours
    for k, lk in enumerate(locks):
        im = rc // 2
        _P, T, N, _B = lk.frames[im]
        flip = _bo(lk, im).cross(T).dot(N) < 0.0
        for i in range(rc - 1):
            for c in range(off[k + 1] - off[k]):
                ja, jb = off[k] + c, off[k] + c + 1
                f = [V('F', i, ja, k), V('F', i, jb, k), V('F', i + 1, jb, k), V('F', i + 1, ja, k)]
                u = [uv(lk, i, ja), uv(lk, i, jb), uv(lk, i + 1, jb), uv(lk, i + 1, ja)]
                if flip:
                    f.reverse()
                    u.reverse()
                g.add_face(f, u, 'F', grid=(k, i, c))
                if closed:
                    f = [V('B', i, jb, k), V('B', i, ja, k), V('B', i + 1, ja, k), V('B', i + 1, jb, k)]
                    u = [uv(lk, i, jb), uv(lk, i, ja), uv(lk, i + 1, ja), uv(lk, i + 1, jb)]
                    if flip:
                        f.reverse()
                        u.reverse()
                    g.add_face(f, u, 'B')
            if bevel:
                for gi in (off[k], off[k + 1]):
                    if is_edge(i, gi, k) and is_edge(i + 1, gi, k):
                        f = [V('F', i, gi, k), V('F', i + 1, gi, k), V('B', i + 1, gi, k), V('B', i, gi, k)]
                        a, b = uv(lk, i, gi), uv(lk, i + 1, gi)
                        extra.append(len(g.faces))
                        g.add_face(f, [a, b, b, a])

    # crotches where two joined locks part (towards the tip) or meet
    if closed:
        for b in range(K - 1):
            gi = off[b + 1]
            for i in range(rc - 1):
                if J[b][i] == J[b][i + 1]:
                    continue
                ia, io = (i, i + 1) if J[b][i] else (i + 1, i)      # apex row, open row
                Fa, Ka = index[('F', ia, gi, None)], index[('B', ia, gi, None)]
                v0, v1 = (gi / total, locks[b].ts[ia]), (gi / total, locks[b].ts[io])
                if bevel:
                    F1, B1 = V('F', io, gi, b), V('B', io, gi, b)
                    F2, B2 = V('F', io, gi, b + 1), V('B', io, gi, b + 1)
                    quads = [[Fa, F2, B2, Ka], [Fa, Ka, B1, F1]]
                else:
                    E1, E2 = V('E', io, gi, b), V('E', io, gi, b + 1)
                    quads = [[Fa, E2, Ka, E1]]
                for f in quads:
                    extra.append(len(g.faces))
                    g.add_face(f, [v0, v1, v1, v0])

    def cap(i, ka, kb):
        """Close a ring (blunt / cut ends, root cap) with front-back quads."""
        for k in range(ka, kb + 1):
            for c in range(off[k + 1] - off[k]):
                ja, jb = off[k] + c, off[k] + c + 1
                f = [V('F', i, ja, k), V('F', i, jb, k), V('B', i, jb, k), V('B', i, ja, k)]
                uniq = []
                for q in f:
                    if q not in uniq:
                        uniq.append(q)
                if len(uniq) < 3:
                    continue
                extra.append(len(g.faces))
                g.add_face(uniq, [uv(locks[k], i, ja)] * len(uniq))

    last = rc - 1
    if point_tip:
        for k, lk in enumerate(locks):
            tip = len(g.verts)
            g.verts.append(lk.frames[nr - 1][0].copy())
            g.t.append(lk.ts[nr - 1])
            g.inner.append(0.0)
            g.head.append((0.0, 0.0, lk.ts[nr - 1]))
            Sk = off[k + 1] - off[k]
            loop = [(V('F', last, off[k] + c, k), 'F') for c in range(Sk + 1)]
            if closed:
                back = range(Sk, -1, -1) if bevel else range(Sk - 1, 0, -1)
                loop += [(V('B', last, off[k] + c, k), 'B') for c in back]
            span = len(loop) if closed else len(loop) - 1
            ut = (off[k] + 0.5 * Sk) / total, lk.ts[nr - 1]
            for j in range(span):
                a, sa = loop[j]
                b, sb = loop[(j + 1) % len(loop)]
                kind = 'F' if sa == sb == 'F' else ('B' if sa == sb == 'B' else 'S')
                extra.append(len(g.faces))
                g.add_face((a, b, tip), (ut, ut, ut), kind)
    elif closed:
        for ka, kb in components(last):
            cap(last, ka, kb)
    if closed and prm.root_cap:
        for ka, kb in components(0):
            cap(0, ka, kb)

    # tip fans take their corner UVs from the ring
    uv_of = {}
    for f, u in zip(g.faces, g.face_uv):
        if len(f) == 4:
            for q, x in zip(f, u):
                uv_of.setdefault(q, x)
    for fi in extra:
        f = g.faces[fi]
        if len(f) == 3 and g.face_kind[fi] in ('F', 'B', 'S'):
            g.face_uv[fi] = tuple(uv_of.get(q, g.face_uv[fi][n]) for n, q in enumerate(f))

    _orient_like_neighbours(g, extra)

    if prm.connect_style == 'SMOOTH' and prm.smooth_iterations > 0 and K > 1:
        # even out the row spacing inside multi-lock bodies (no shrinking)
        w = [0.0] * len(g.verts)
        for (side, i, gi, var), q in index.items():
            if side not in ('F', 'B') or var is not None or not 0 < i < rc - 1:
                continue
            if gi in off and not joined(off.index(gi) - 1, i):
                continue                     # outer edges keep the silhouette
            w[q] = 0.6
        _relax(g, w, max(1, prm.smooth_iterations // 2))

    finish_sheet(g, center, prm, hair, outline, closed)
    return J


# --------------------------------------------------------------------------
# group entry point
# --------------------------------------------------------------------------

def _around(items, az):
    seq = sorted(items, key=az)
    if len(seq) > 2:
        gaps = [((az(seq[(i + 1) % len(seq)]) - az(seq[i])) % 1.0, i) for i in range(len(seq))]
        _g, i = max(gaps)
        seq = seq[i + 1:] + seq[:i + 1]
    return seq


def chain_order(units, links=None):
    """Units sorted around the head, the chain starting after the widest gap.
    Manually linked units stay next to each other (a lock lying between them,
    e.g. a stray stuck into the sheet, goes beside the linked block)."""
    parent = list(range(len(units)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    key = {(u.src, u.mirrored): n for n, u in enumerate(units)}
    for link in links or []:
        for mir in (False, True):
            ia, ib = key.get((link[0], mir)), key.get((link[1], mir))
            if ia is not None and ib is not None:
                parent[find(ia)] = find(ib)
    blocks = {}
    for n in range(len(units)):
        blocks.setdefault(find(n), []).append(units[n])
    blocks = [_around(b, lambda u: u.az) for b in blocks.values()]

    def block_az(b):
        # circular mean of the members' azimuths
        x = sum(math.cos(2.0 * math.pi * u.az) for u in b)
        y = sum(math.sin(2.0 * math.pi * u.az) for u in b)
        return (math.atan2(y, x) / (2.0 * math.pi)) % 1.0
    return [u for b in _around(blocks, block_az) for u in b]


def _flatten(seq):
    locks, owner = [], []
    for ui, u in enumerate(seq):
        for lk in u.locks:
            locks.append(lk)
            owner.append(ui)
    return locks, owner


def build_layer(units, center, prm, hair, outline=None, links=None, uid=0):
    """Build one group layer as a single unified body. Returns the next uid."""
    from .core import align_run
    if not units:
        return uid
    seq = chain_order(units, links)
    link_rng = {}
    for link in links or []:
        a, b, t0, t1 = link[:4]
        auto = len(link) > 5 and bool(link[5])
        link_rng[frozenset((a, b))] = (t0, t1, auto)
    selected_only = getattr(prm, "unify_scope", 'AUTO') == 'SELECTED'

    def auto_rule(t):
        if prm.connect and t <= prm.split:
            return max(prm.connect_ratio, 1.02)
        return None

    def make_force(seq, locks, owner):
        def force(b, i):
            la, lb = locks[b], locks[b + 1]
            ua, ub = seq[owner[b]], seq[owner[b + 1]]
            if ua is ub:
                return True if i <= ua.branch_row else None
            t = 0.5 * (la.ts[i] + lb.ts[i])
            r = link_rng.get(frozenset((ua.src, ub.src))) if ua.mirrored == ub.mirrored else None
            if r is not None and r[2]:
                return auto_rule(t)               # unify selected: join where they touch
            if r is not None and r[0] - 1e-6 <= t <= r[1] + 1e-6:
                return True
            if selected_only:
                return False                      # only chosen pairs become one body
            return auto_rule(t)
        return force

    def rows_of(locks):
        nr = min(len(lk.frames) for lk in locks)
        return nr - 1 if prm.tip_mode == 'POINT' else nr

    locks, owner = _flatten(seq)
    if prm.plane_rows and len(seq) > 1:
        # rows of joined neighbours on shared planes (from the longest guide)
        J = join_table(locks, rows_of(locks), prm, make_force(seq, locks, owner))
        ends = []
        for ui in range(len(seq) - 1):
            b = max(k for k in range(len(locks)) if owner[k] == ui)
            row = J[b]
            if not row[0]:
                ends.append(0.0)
                continue
            r = 0
            while r + 1 < len(row) and row[r + 1]:
                r += 1
            r = min(r + 1, len(row) - 1)
            ends.append(0.5 * (locks[b].ts[r] + locks[b + 1].ts[r]))
        seq, _side = align_run(seq, ends)
        locks, owner = _flatten(seq)
    for u in seq:
        for lk in u.locks:
            lk.unit = uid
        uid += 1
    build_unified(locks, center, prm, hair, outline, make_force(seq, locks, owner))
    return uid
