"""Minimal Specctra SES importer (KiCad 7's ImportSpecctraSES needs the GUI's global board)."""

import re

import pcbnew
from pcbnew import VECTOR2I, FromMM

_TOKEN = re.compile(r'\(|\)|"[^"]*"|[^\s()"]+')


def _parse(text):
    stack = [[]]
    for tok in _TOKEN.findall(text):
        if tok == "(":
            stack.append([])
        elif tok == ")":
            done = stack.pop()
            stack[-1].append(done)
        else:
            stack[-1].append(tok.strip('"'))
    return stack[0][0]


def _find(node, key):
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


def import_ses(board: pcbnew.BOARD, path: str, via_mm=(0.6, 0.3)) -> tuple[int, int]:
    """Add the wires and vias of a session file to `board`. Returns (segments, vias)."""
    root = _parse(open(path).read())
    routes = _find(root, "routes")[0]
    res_unit, res_value = _find(routes, "resolution")[0][1:3]
    assert res_unit == "um", res_unit
    scale = 1.0 / (1000.0 * float(res_value))  # session units -> mm
    layer_ids = {board.GetLayerName(i): i for i in range(pcbnew.PCB_LAYER_ID_COUNT)}
    nets = board.GetNetsByName()
    # The session repeats copper that was already on the board (e.g. fanout vias); skip duplicates.
    key = lambda x, y: (round(x, 3), round(y, 3))  # noqa: E731
    via_buckets: dict[tuple[int, int], list[tuple[float, float]]] = {}

    def via_exists(x, y):
        bx, by = int(x * 10), int(y * 10)
        for i in (bx - 1, bx, bx + 1):
            for j in (by - 1, by, by + 1):
                if any(abs(x - vx) < 0.01 and abs(y - vy) < 0.01 for vx, vy in via_buckets.get((i, j), [])):
                    return True
        return False

    def remember_via(x, y):
        via_buckets.setdefault((int(x * 10), int(y * 10)), []).append((x, y))

    for t in board.GetTracks():
        if t.GetClass() == "PCB_VIA":
            remember_via(pcbnew.ToMM(t.GetPosition().x), pcbnew.ToMM(t.GetPosition().y))
    have_segs = set()
    for t in board.GetTracks():
        if t.GetClass() == "PCB_TRACK":
            a = key(pcbnew.ToMM(t.GetStart().x), pcbnew.ToMM(t.GetStart().y))
            b = key(pcbnew.ToMM(t.GetEnd().x), pcbnew.ToMM(t.GetEnd().y))
            have_segs.add((t.GetLayer(), min(a, b), max(a, b)))
    n_seg = n_via = 0
    for net_node in _find(_find(routes, "network_out")[0], "net"):
        netinfo = nets[net_node[1]]
        for wire in _find(net_node, "wire"):
            path_node = _find(wire, "path")[0]
            layer, width = path_node[1], float(path_node[2]) * scale
            coords = [float(v) * scale for v in path_node[3:] if not isinstance(v, list)]
            pts = [(coords[i], -coords[i + 1]) for i in range(0, len(coords), 2)]
            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                a, b = key(x1, y1), key(x2, y2)
                seg_key = (layer_ids[layer], min(a, b), max(a, b))
                if seg_key in have_segs or a == b:
                    continue
                have_segs.add(seg_key)
                t = pcbnew.PCB_TRACK(board)
                t.SetStart(VECTOR2I(FromMM(x1), FromMM(y1)))
                t.SetEnd(VECTOR2I(FromMM(x2), FromMM(y2)))
                t.SetWidth(FromMM(width))
                t.SetLayer(layer_ids[layer])
                t.SetNet(netinfo)
                board.Add(t)
                n_seg += 1
        for via in _find(net_node, "via"):
            x, y = float(via[2]) * scale, -float(via[3]) * scale
            if via_exists(x, y):
                continue
            remember_via(x, y)
            v = pcbnew.PCB_VIA(board)
            v.SetPosition(VECTOR2I(FromMM(x), FromMM(y)))
            v.SetWidth(FromMM(via_mm[0]))
            v.SetDrill(FromMM(via_mm[1]))
            v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
            v.SetNet(netinfo)
            board.Add(v)
            n_via += 1
    return n_seg, n_via
