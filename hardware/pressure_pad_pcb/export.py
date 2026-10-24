"""DRC + JLCPCB fabrication/assembly outputs for the routed board.

    /usr/bin/python3 export.py

Inputs : build/pressure_pad_routed.kicad_pcb, build/bom_parts.json (from gen_board.py)
Outputs: jlcpcb/gerbers.zip, jlcpcb/bom.csv, jlcpcb/cpl.csv, jlcpcb/drc.txt, images/*.png
"""

import csv
import json
import re
import shutil
import subprocess
import zipfile
from collections import defaultdict
from pathlib import Path

import pcbnew

HERE = Path(__file__).resolve().parent
BUILD, OUT, IMG = HERE / "build", HERE / "jlcpcb", HERE / "images"
PCB = BUILD / "pressure_pad_routed.kicad_pcb"

# KiCad footprint orientation -> JLCPCB library orientation (degrees added to KiCad rotation).
# Common corrections used by the KiCad/JLCPCB community tools. JLCPCB shows a placement preview
# before production; check pin-1 marks there (docs/09 explains how).
ROTATION_FIX = [
    (r"^SOT-223", 180),
    (r"^SOT-23", 180),
    (r"^SOIC-", 270),
    (r"^QFN-", 270),
    (r"^LGA-14_3x2.5mm", 90),
]

OUT.mkdir(exist_ok=True)
IMG.mkdir(exist_ok=True)

# ---- DRC ----
board = pcbnew.LoadBoard(str(PCB))
pcbnew.ZONE_FILLER(board).Fill(board.Zones())
drc_path = OUT / "drc.txt"
pcbnew.WriteDRCReport(board, str(drc_path), pcbnew.EDA_UNITS_MILLIMETRES, True)
report = drc_path.read_text()
body = report.split("DRC violations")[1].split("unconnected pads")[0]
kinds = re.findall(r"\[(\w+)\]:", body)
# "lib_footprint_issues" only says the footprints were loaded from files rather than a library
# table; silkscreen findings are cosmetic. Everything else counts.
INFO_ONLY = {"lib_footprint_issues", "silk_overlap", "silk_over_copper", "silk_edge_clearance"}
errors = [k for k in kinds if k not in INFO_ONLY]
unconnected = int(re.search(r"\*\* Found (\d+) unconnected pads", report).group(1))
print(f"DRC: {len(errors)} errors {sorted(set(errors))}, {len(kinds) - len(errors)} cosmetic/info, "
      f"{unconnected} unconnected pads (details: {drc_path})")
board.Save(str(PCB))

# ---- gerbers + drill ----
gdir = BUILD / "gerbers"
shutil.rmtree(gdir, ignore_errors=True)
gdir.mkdir()
layers = "F.Cu,In1.Cu,In2.Cu,B.Cu,F.Paste,B.Paste,F.SilkS,B.SilkS,F.Mask,B.Mask,Edge.Cuts"
subprocess.run(["kicad-cli", "pcb", "export", "gerbers", "--layers", layers, "--subtract-soldermask",
                "--no-x2", "-o", str(gdir), str(PCB)], check=True, capture_output=True)
subprocess.run(["kicad-cli", "pcb", "export", "drill", "--format", "excellon", "--excellon-separate-th",
                "--generate-map", "--map-format", "gerberx2", "-o", str(gdir) + "/", str(PCB)],
               check=True, capture_output=True)
with zipfile.ZipFile(OUT / "gerbers.zip", "w", zipfile.ZIP_DEFLATED) as z:
    for f in sorted(gdir.iterdir()):
        z.write(f, f.name)
print("gerbers:", len(list(gdir.iterdir())), "files")

# ---- BOM (grouped by LCSC part) ----
parts = json.loads((BUILD / "bom_parts.json").read_text())
groups = defaultdict(list)
for p in parts:
    groups[(p["lcsc"], p["value"], p["footprint"])].append(p["ref"])
with open(OUT / "bom.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["Comment", "Designator", "Footprint", "LCSC Part #"])
    for (lcsc, value, fpname), refs in sorted(groups.items(), key=lambda kv: kv[1][0]):
        w.writerow([value, ",".join(sorted(refs, key=lambda r: (re.sub(r"\d", "", r), int(re.sub(r"\D", "", r))))),
                    fpname, lcsc])
print("BOM lines:", len(groups), "/ placements:", len(parts))

# ---- CPL (placement) ----
assembled = {p["ref"] for p in parts}
with open(OUT / "cpl.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        if ref not in assembled:
            continue
        name = fp.GetFPIDAsString().split(":")[-1]
        rot = fp.GetOrientationDegrees()
        for pattern, fix in ROTATION_FIX:
            if re.search(pattern, name):
                rot += fix
                break
        if name.startswith("IDC-Header"):
            # through-hole headers have their origin on pin 1; JLCPCB places by body centre
            xs = [p.GetPosition().x for p in fp.Pads()]
            ys = [p.GetPosition().y for p in fp.Pads()]
            pos = pcbnew.VECTOR2I((min(xs) + max(xs)) // 2, (min(ys) + max(ys)) // 2)
        else:
            pos = fp.GetPosition()
        # same coordinate system as the gerbers (absolute, y up)
        x = pcbnew.ToMM(pos.x)
        y = -pcbnew.ToMM(pos.y)
        w.writerow([ref, f"{x:.3f}mm", f"{y:.3f}mm", "Top", f"{rot % 360:.0f}"])

# ---- netlist for human review ----
by_net = defaultdict(list)
for fp in board.GetFootprints():
    for pad in fp.Pads():
        if pad.GetNetname():
            by_net[pad.GetNetname()].append(f"{fp.GetReference()}.{pad.GetNumber()}")
values = {fp.GetReference(): fp.GetValue() for fp in board.GetFootprints()}
with open(OUT / "netlist.md", "w") as f:
    f.write("# PressurePad v1 netlist (generated by export.py)\n\n")
    f.write("Pad names are `reference.pad`. Parts: " + ", ".join(
        f"{r}={v}" for r, v in sorted(values.items()) if not r[0] in "RCQH") + "\n\n")
    f.write("| Net | Pads |\n|---|---|\n")
    for name in sorted(by_net, key=lambda n: (re.sub(r"\d+$", "", n), int(re.search(r"(\d*)$", n).group(1) or 0))):
        pads = sorted(by_net[name])
        shown = ", ".join(pads) if len(pads) <= 40 else ", ".join(pads[:40]) + f", ... ({len(pads)} pads)"
        f.write(f"| {name} | {shown} |\n")

# ---- images ----
def render(layers, name, mirror=False):
    svg = BUILD / f"{name}.svg"
    cmd = ["kicad-cli", "pcb", "export", "svg", "--layers", layers, "--page-size-mode", "2",
           "--exclude-drawing-sheet", "-o", str(svg), str(PCB)]
    if mirror:
        cmd.insert(-3, "--mirror")
    subprocess.run(cmd, check=True, capture_output=True)
    import cairosvg

    cairosvg.svg2png(url=str(svg), write_to=str(IMG / f"{name}.png"), output_width=1800, background_color="white")


render("F.Cu,F.SilkS,F.Mask,Edge.Cuts", "top")
render("B.Cu,B.SilkS,Edge.Cuts", "bottom", mirror=True)
render("F.SilkS,F.Fab,Edge.Cuts", "assembly")
print("images:", sorted(p.name for p in IMG.iterdir()))
