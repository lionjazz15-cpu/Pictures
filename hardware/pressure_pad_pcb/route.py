"""Autoroute build/pressure_pad.kicad_pcb with Freerouting 1.9 (run under Xvfb), then fill zones.

    /usr/bin/python3 route.py /path/to/freerouting-1.9.0.jar [passes]
"""

import subprocess
import sys
from pathlib import Path

import pcbnew

from ses_import import import_ses

BUILD = Path(__file__).resolve().parent / "build"
jar = sys.argv[1]
passes = sys.argv[2] if len(sys.argv) > 2 else "40"
pcb = BUILD / "pressure_pad.kicad_pcb"
dsn, ses = BUILD / "pressure_pad.dsn", BUILD / "pressure_pad.ses"
if "--reuse" not in sys.argv:
    ses.unlink(missing_ok=True)

board = pcbnew.LoadBoard(str(pcb))
assert pcbnew.ExportSpecctraDSN(board, str(dsn)), "DSN export failed"
if "--reuse" not in sys.argv:
    subprocess.run(["xvfb-run", "-a", "java", "-jar", jar, "-de", str(dsn), "-do", str(ses), "-mp", passes],
                   check=True, timeout=7200)
assert ses.exists(), "Freerouting did not write a session file"
print("imported %d segments, %d vias" % import_ses(board, str(ses)))
pcbnew.ZONE_FILLER(board).Fill(board.Zones())
board.Save(str(BUILD / "pressure_pad_routed.kicad_pcb"))
print("routed:", len(board.GetTracks()), "track/via items")
