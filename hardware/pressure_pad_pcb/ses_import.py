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
    n_seg = n_via = 0
    for net_node in _find(_find(routes, "network_out")[0], "net"):
        netinfo = nets[net_node[1]]
        for wire in _find(net_node, "wire"):
            path_node = _find(wire, "path")[0]
            layer, width = path_node[1], float(path_node[2]) * scale
            coords = [float(v) * scale for v in path_node[3:] if not isinstance(v, list)]
            pts = [(coords[i], -coords[i + 1]) for i in range(0, len(coords), 2)]
            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
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
            v = pcbnew.PCB_VIA(board)
            v.SetPosition(VECTOR2I(FromMM(x), FromMM(y)))
            v.SetWidth(FromMM(via_mm[0]))
            v.SetDrill(FromMM(via_mm[1]))
            v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
            v.SetNet(netinfo)
            board.Add(v)
            n_via += 1
    return n_seg, n_via
