"""Generate the ChairMotion PressurePad v1 PCB (KiCad 7) from code.

One board serves both the chair pad (J1-J4: 28 rows x 16 cols) and the floor pad (J1/J2 only).
Everything is SMD except the four IDC box headers, so it can be ordered fully assembled (JLCPCB).

    /usr/bin/python3 gen_board.py          # writes build/pressure_pad.kicad_pcb (unrouted)

Circuit summary (see README.md in this folder):
  - RP2040 + W25Q128 flash + 12 MHz crystal, USB-C (5.1k CC pull-downs, USBLC6 ESD), AMS1117-3.3
  - Rows: 4 x 74HC595 -> 28 x AO3400A low-side switches, 2.2k pull-up of every row to Vb
  - Columns: 16 transimpedance amplifiers (4 x MCP6004, Rf 2.2k || 1nF) held at Vb,
    read through 2 x 74HC4051 into ADC0 (cols 0-7) and ADC2 (cols 8-15)
  - Vb: PWM (GP22) -> 2-stage RC -> MCP6004 buffer -> 22 ohm -> Vb rail, sensed on ADC1.
    Lowering Vb extends the range for strongly pressed cells (firmware auto-range).
  - LSM6DS3TR-C IMU on I2C (GP4/GP5) to measure chair rotation, status LED on GP25
"""

from __future__ import annotations

import json
from pathlib import Path

import pcbnew
from pcbnew import VECTOR2I, FromMM

FP = "/usr/share/kicad/footprints"
HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
OX, OY = 100.0, 100.0  # board origin in KiCad coordinates [mm]
W, H = 120.0, 85.0  # board size [mm]

board = pcbnew.BOARD()
board.SetCopperLayerCount(4)
board.SetLayerType(pcbnew.In1_Cu, pcbnew.LT_POWER)
board.SetLayerType(pcbnew.In2_Cu, pcbnew.LT_POWER)
ds = board.GetDesignSettings()
nc = ds.m_NetSettings.m_DefaultNetClass
nc.SetClearance(FromMM(0.15))
nc.SetTrackWidth(FromMM(0.2))
nc.SetViaDiameter(FromMM(0.6))
nc.SetViaDrill(FromMM(0.3))
ds.m_TrackMinWidth = FromMM(0.127)
ds.m_MinClearance = FromMM(0.127)
ds.m_ViasMinSize = FromMM(0.45)
ds.m_MinThroughDrill = FromMM(0.2)
ds.m_CopperEdgeClearance = FromMM(0.3)

_nets: dict[str, pcbnew.NETINFO_ITEM] = {}
BOM: list[dict] = []


def net(name: str) -> pcbnew.NETINFO_ITEM:
    if name not in _nets:
        item = pcbnew.NETINFO_ITEM(board, name)
        board.Add(item)
        _nets[name] = item
    return _nets[name]


def place(ref, lib, name, value, x, y, rot=0.0, pins=None, lcsc=None, mpn=""):
    fp = pcbnew.FootprintLoad(f"{FP}/{lib}.pretty", name)
    fp.SetReference(ref)
    fp.SetValue(value)
    fp.SetPosition(VECTOR2I(FromMM(OX + x), FromMM(OY + y)))
    fp.SetOrientationDegrees(rot)
    board.Add(fp)
    pins = pins or {}
    names = {p.GetNumber() for p in fp.Pads()}
    unknown = set(pins) - names
    assert not unknown, f"{ref}: no pads named {unknown}"
    for pad in fp.Pads():
        n = pins.get(pad.GetNumber())
        if n:
            pad.SetNet(net(n))
    fp.Reference().SetTextSize(VECTOR2I(FromMM(0.8), FromMM(0.8)))
    fp.Reference().SetTextThickness(FromMM(0.12))
    if lcsc:
        BOM.append({"ref": ref, "value": value, "footprint": name, "lcsc": lcsc, "mpn": mpn})
    return fp


# ---- parts library (LCSC numbers verified against jlcpcb.com / lcsc.com listings, 2026-10) ----
R0603 = ("Resistor_SMD", "R_0603_1608Metric")
C0603 = ("Capacitor_SMD", "C_0603_1608Metric")
C0805 = ("Capacitor_SMD", "C_0805_2012Metric")
PARTS = {
    "1k": (R0603, "C21190", "0603WAF1001T5E"),
    "2.2k": (R0603, "C4190", "0603WAF2201T5E"),
    "5.1k": (R0603, "C23186", "0603WAF5101T5E"),
    "10k": (R0603, "C25804", "0603WAF1002T5E"),
    "22R": (R0603, "C23345", "0603WAF220JT5E"),
    "100nF": (C0603, "C14663", "CC0603KRX7R9BB104"),
    "1uF": (C0603, "C15849", "CL10A105KB8NNNC"),
    "1nF": (C0603, "C1588", "CL10B102KB8NNNC"),
    "15pF": (C0603, "C1644", "CL10C150JB8NNNC"),
    "10uF": (C0805, "C15850", "CL21A106KAYNNNE"),
}
_counter = {"R": 0, "C": 0}


def passive(kind, value, a, b, x, y, rot=0.0, ref=None):
    (lib, fpname), lcsc, mpn = PARTS[value]
    if ref is None:
        _counter[kind] += 1
        ref = f"{kind}{_counter[kind]}"
    return place(ref, lib, fpname, value, x, y, rot, {"1": a, "2": b}, lcsc, mpn)


def decap(value, rail, x, y, rot=0.0):
    return passive("C", value, rail, "GND", x, y, rot)


# ---- connectors to the mats (top edge) ----
IDC = ("Connector_IDC", "IDC-Header_2x08_P2.54mm_Vertical")
place("J1", *IDC, "SEAT/FLOOR ROW", 6, 9, 90, {str(i + 1): f"ROW{i}" for i in range(16)}, "C3406", "2x8P box header")
place("J3", *IDC, "BACK ROW", 35.5, 9, 90,
      {**{str(i + 1): f"ROW{16 + i}" for i in range(12)}, **{str(i): "GND" for i in range(13, 17)}}, "C3406", "2x8P box header")
place("J2", *IDC, "SEAT/FLOOR COL", 65, 9, 90, {str(i + 1): f"COL{i}" for i in range(16)}, "C3406", "2x8P box header")
place("J4", *IDC, "BACK COL", 94.5, 9, 90, {str(i + 1): f"COL{i}" for i in range(16)}, "C3406", "2x8P box header")

# ---- row drivers: 74HC595 chain -> AO3400A low-side switches, rows pulled up to Vb ----
SOIC16 = ("Package_SO", "SOIC-16_3.9x9.9mm_P1.27mm")
q_pins = [15, 1, 2, 3, 4, 5, 6, 7]  # QA..QH
for k in range(4):
    x = 8 + 11 * k
    pins = {"16": "+3V3", "8": "GND", "13": "GND", "10": "+3V3", "11": "SR_CLK", "12": "SR_LATCH",
            "14": "SR_SER" if k == 0 else f"SR_CHAIN{k}"}
    if k < 3:
        pins["9"] = f"SR_CHAIN{k + 1}"
    for b, p in enumerate(q_pins):
        row = 8 * k + b
        if row < 28:
            pins[str(p)] = f"ROW_G{row}"
    place(f"U{10 + k}", *SOIC16, "74HC595D", x, 23, 0, pins, "C5947", "74HC595D,118")
    decap("100nF", "+3V3", x + 4.6, 18.5, 90)

for row in range(28):
    col, line = row % 14, row // 14
    x = 4 + 3.7 * col
    y = 33 + 12 * line
    place(f"Q{row + 1}", "Package_TO_SOT_SMD", "SOT-23", "AO3400A", x, y, 90,
          {"1": f"ROW_G{row}", "2": "GND", "3": f"ROW{row}"}, "C20917", "AO3400A")
    passive("R", "2.2k", f"ROW{row}", "VB", x, y + 4.5, 90)

# ---- column TIAs: 4 x MCP6004, Rf 2.2k || Cf 1nF ----
SOIC14 = ("Package_SO", "SOIC-14_3.9x8.7mm_P1.27mm")
amp_pins = [("1", "2", "3"), ("7", "6", "5"), ("8", "9", "10"), ("14", "13", "12")]  # (out, -in, +in)
for k in range(4):
    x = 67 + 11 * k
    pins = {"4": "+3V3", "11": "GND"}
    for a, (o, n, p) in enumerate(amp_pins):
        c = 4 * k + a
        pins.update({o: f"TIA{c}", n: f"COL{c}", p: "VB"})
        passive("R", "2.2k", f"TIA{c}", f"COL{c}", x - 3.3 + 2.2 * a, 32, 90)
        passive("C", "1nF", f"TIA{c}", f"COL{c}", x - 3.3 + 2.2 * a, 36, 90)
    place(f"U{20 + k}", *SOIC14, "MCP6004", x, 23, 0, pins, "C7378", "MCP6004T-I/SL")
    decap("100nF", "+3V3", x + 4.6, 18.5, 90)

# ---- column multiplexers: 2 x 74HC4051 -> ADC0 / ADC2 ----
y_pins = ["13", "14", "15", "12", "1", "5", "2", "4"]  # Y0..Y7
for k, (x, adc) in enumerate([(71, "ADC_A"), (91, "ADC_B")]):
    pins = {"16": "+3V3", "8": "GND", "7": "GND", "6": "GND", "11": "MUX_S0", "10": "MUX_S1", "9": "MUX_S2", "3": adc}
    pins.update({p: f"TIA{8 * k + i}" for i, p in enumerate(y_pins)})
    place(f"U{30 + k}", *SOIC16, "74HC4051D", x, 47, 0, pins, "C9386", "74HC4051D,653")
    decap("100nF", "+3V3", x + 4.6, 42, 90)

# ---- Vb: PWM -> RC -> RC -> buffer -> 22R -> rail ----
place("U24", *SOIC14, "MCP6004", 107, 47, 0, {
    "3": "VB_SET", "2": "VB_BUF", "1": "VB_BUF",
    "5": "GND", "6": "U24_B", "7": "U24_B",
    "10": "GND", "9": "U24_C", "8": "U24_C",
    "12": "GND", "13": "U24_D", "14": "U24_D",
    "4": "+3V3", "11": "GND"}, "C7378", "MCP6004T-I/SL")
decap("100nF", "+3V3", 111.6, 42, 90)
passive("R", "10k", "VB_PWM", "VB_RC1", 101, 54, 0)
decap("1uF", "VB_RC1", 101, 56.5, 0)
passive("R", "10k", "VB_RC1", "VB_SET", 105, 54, 0)
decap("1uF", "VB_SET", 105, 56.5, 0)
passive("R", "22R", "VB_BUF", "VB", 109, 54, 0)
decap("1uF", "VB", 109, 56.5, 0)
decap("100nF", "VB", 112.5, 54, 90)
passive("R", "1k", "VB", "VB_SENSE", 112.5, 58, 90)

# ---- USB-C, ESD, regulator ----
place("J5", "Connector_USB", "USB_C_Receptacle_HRO_TYPE-C-31-M-12", "USB-C", 3.8, 67, 270, {
    "A4": "VBUS", "A9": "VBUS", "B4": "VBUS", "B9": "VBUS",
    "A1": "GND", "A12": "GND", "B1": "GND", "B12": "GND", "S1": "GND",
    "A5": "CC1", "B5": "CC2", "A6": "USB_DP_C", "B6": "USB_DP_C", "A7": "USB_DM_C", "B7": "USB_DM_C"},
    "C165948", "TYPE-C-31-M-12")
passive("R", "5.1k", "CC1", "GND", 11, 73, 90)
passive("R", "5.1k", "CC2", "GND", 13, 73, 90)
place("U4", "Package_TO_SOT_SMD", "SOT-23-6", "USBLC6-2SC6", 14, 64, 0,
      {"1": "USB_DP_C", "6": "USB_DP_C", "3": "USB_DM_C", "4": "USB_DM_C", "2": "GND", "5": "VBUS"},
      "C7519", "USBLC6-2SC6")
passive("R", "22R", "USB_DP_C", "USB_DP", 18.5, 63, 0)
passive("R", "22R", "USB_DM_C", "USB_DM", 18.5, 65, 0)
place("U3", "Package_TO_SOT_SMD", "SOT-223-3_TabPin2", "AMS1117-3.3", 12, 80, 90,
      {"1": "GND", "2": "+3V3", "3": "VBUS"}, "C6186", "AMS1117-3.3")
decap("10uF", "VBUS", 6, 77, 90)
decap("10uF", "+3V3", 18, 78, 90)
decap("10uF", "+3V3", 20.5, 78, 90)

# ---- RP2040 + flash + crystal ----
rp = {str(p): "+3V3" for p in (1, 10, 22, 33, 42, 49, 44, 48, 43)}
rp.update({"23": "+1V1", "50": "+1V1", "45": "+1V1", "57": "GND", "19": "GND",
           "20": "XIN", "21": "XOUT", "26": "RUN", "46": "USB_DM", "47": "USB_DP",
           "51": "QSPI_SD3", "52": "QSPI_SCLK", "53": "QSPI_SD0", "54": "QSPI_SD2", "55": "QSPI_SD1", "56": "QSPI_SS",
           "4": "SR_CLK", "5": "SR_SER", "9": "SR_LATCH", "13": "MUX_S0", "14": "MUX_S1", "15": "MUX_S2",
           "6": "I2C_SDA", "7": "I2C_SCL", "11": "IMU_INT1", "34": "VB_PWM", "37": "LED_DRV",
           "38": "ADC_A", "39": "VB_SENSE", "40": "ADC_B"})
place("U1", "Package_DFN_QFN", "QFN-56-1EP_7x7mm_P0.4mm_EP3.2x3.2mm", "RP2040", 34, 68, 0, rp, "C2040", "RP2040")
for x, y, rail in [(28.5, 63, "+3V3"), (28.5, 66, "+3V3"), (28.5, 70, "+3V3"), (28.5, 73, "+1V1"),
                   (39.5, 63, "+3V3"), (39.5, 66, "+3V3"), (39.5, 70, "+3V3"), (39.5, 73, "+1V1"),
                   (32.5, 61.5, "+3V3"), (35.5, 61.5, "+3V3")]:
    decap("100nF", rail, x, y, 90)
decap("1uF", "+3V3", 30.5, 61.5, 90)
decap("1uF", "+1V1", 37.5, 61.5, 90)
place("Y1", "Crystal", "Crystal_SMD_3225-4Pin_3.2x2.5mm", "12MHz", 33, 77, 0,
      {"1": "XIN", "3": "XOUT_X", "2": "GND", "4": "GND"}, "C9002", "X322512MSB4SI")
decap("15pF", "XIN", 29.5, 77, 90)
decap("15pF", "XOUT_X", 36.5, 77, 90)
passive("R", "1k", "XOUT", "XOUT_X", 39.5, 77, 90)
place("U2", "Package_SO", "SOIC-8_5.23x5.23mm_P1.27mm", "W25Q128JVSIQ", 47, 63, 0,
      {"1": "QSPI_SS", "2": "QSPI_SD1", "3": "QSPI_SD2", "4": "GND", "5": "QSPI_SD0", "6": "QSPI_SCLK",
       "7": "QSPI_SD3", "8": "+3V3"}, "C97521", "W25Q128JVSIQ")
decap("100nF", "+3V3", 53.5, 60, 90)
passive("R", "10k", "QSPI_SS", "+3V3", 53.5, 64, 90)
passive("R", "1k", "QSPI_SS", "BOOT_SW", 53.5, 68, 90)

# ---- buttons, LED ----
place("SW1", "Button_Switch_SMD", "SW_Push_1P1T_XKB_TS-1187A", "BOOTSEL", 47, 77, 0,
      {"1": "BOOT_SW", "2": "GND"}, "C318884", "TS-1187A-B-A-B")
place("SW2", "Button_Switch_SMD", "SW_Push_1P1T_XKB_TS-1187A", "RESET", 58, 77, 0,
      {"1": "RUN", "2": "GND"}, "C318884", "TS-1187A-B-A-B")
passive("R", "10k", "RUN", "+3V3", 56, 70, 90)
passive("R", "1k", "LED_DRV", "LED_A", 64, 70, 90)
place("D1", "LED_SMD", "LED_0603_1608Metric", "LED", 64, 73.5, 90, {"1": "GND", "2": "LED_A"}, "C2286", "KT-0603R")

# ---- IMU ----
place("U40", "Package_LGA", "LGA-14_3x2.5mm_P0.5mm_LayoutBorder3x4y", "LSM6DS3TR-C", 78, 70, 0,
      {"1": "GND", "2": "GND", "3": "GND", "4": "IMU_INT1", "5": "+3V3", "6": "GND", "7": "GND",
       "8": "+3V3", "12": "+3V3", "13": "I2C_SCL", "14": "I2C_SDA"}, "C967633", "LSM6DS3TR-C")
decap("100nF", "+3V3", 75, 66, 90)
decap("100nF", "+3V3", 81, 66, 90)
passive("R", "2.2k", "I2C_SDA", "+3V3", 74, 74.5, 90)
passive("R", "2.2k", "I2C_SCL", "+3V3", 76.5, 74.5, 90)

# ---- mounting holes ----
for i, (x, y) in enumerate([(3.5, 56), (116.5, 22), (98, 81.5), (116.5, 81.5)]):
    place(f"H{i + 1}", "MountingHole", "MountingHole_3.2mm_M3", "M3", x, y)

# ---- outline, text, planes ----
def seg(layer, a, b, width=0.1):
    s = pcbnew.PCB_SHAPE(board)
    s.SetShape(pcbnew.SHAPE_T_SEGMENT)
    s.SetStart(VECTOR2I(FromMM(OX + a[0]), FromMM(OY + a[1])))
    s.SetEnd(VECTOR2I(FromMM(OX + b[0]), FromMM(OY + b[1])))
    s.SetLayer(layer)
    s.SetWidth(FromMM(width))
    board.Add(s)


corners = [(0, 0), (W, 0), (W, H), (0, H)]
for a, b in zip(corners, corners[1:] + corners[:1]):
    seg(pcbnew.Edge_Cuts, a, b, 0.05)


def text(s, x, y, size=1.0, layer=pcbnew.F_SilkS):
    t = pcbnew.PCB_TEXT(board)
    t.SetText(s)
    t.SetPosition(VECTOR2I(FromMM(OX + x), FromMM(OY + y)))
    t.SetTextSize(VECTOR2I(FromMM(size), FromMM(size)))
    t.SetTextThickness(FromMM(size * 0.15))
    t.SetLayer(layer)
    board.Add(t)


text("J1 SEAT/FLOOR ROW", 15, 14.5, 0.9)
text("J3 BACK ROW", 44.5, 14.5, 0.9)
text("J2 SEAT/FLOOR COL", 74, 14.5, 0.9)
text("J4 BACK COL", 103.5, 14.5, 0.9)
text("ChairMotion PressurePad v1", 88, 63, 1.2)
text("BOOT", 47, 81, 0.9)
text("RESET", 58, 81, 0.9)


def plane(layer, netname, margin=0.4):
    z = pcbnew.ZONE(board)
    z.SetLayer(layer)
    z.SetNet(net(netname))
    pts = [(margin, margin), (W - margin, margin), (W - margin, H - margin), (margin, H - margin)]
    outline = z.Outline()
    outline.NewOutline()
    for x, y in pts:
        outline.Append(FromMM(OX + x), FromMM(OY + y))
    z.SetLocalClearance(FromMM(0.25))
    z.SetMinThickness(FromMM(0.2))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
    z.SetThermalReliefGap(FromMM(0.25))
    z.SetThermalReliefSpokeWidth(FromMM(0.3))
    board.Add(z)
    return z


plane(pcbnew.In1_Cu, "GND")
plane(pcbnew.In2_Cu, "+3V3")


# ---- fanout: every SMD pad on a plane net gets its own via, so the autorouter only routes signals ----
PLANE_NETS = {"GND", "+3V3"}


def add_via(x_nm, y_nm, netname):
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(VECTOR2I(int(x_nm), int(y_nm)))
    v.SetWidth(FromMM(0.6))
    v.SetDrill(FromMM(0.3))
    v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
    v.SetNet(net(netname))
    board.Add(v)


def add_track(a, b, netname, width):
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(VECTOR2I(int(a[0]), int(a[1])))
    t.SetEnd(VECTOR2I(int(b[0]), int(b[1])))
    t.SetWidth(FromMM(width))
    t.SetLayer(pcbnew.F_Cu)
    t.SetNet(net(netname))
    board.Add(t)


def fanout():
    """Give every SMD pad on a plane net a short stub to a via (or to a nearby same-net via).

    Candidate via spots are tried around each pad and rejected if the via or the stub would come
    too close to another net's copper, a hole, or the board edge.
    """
    from shapely.geometry import LineString, Point, box
    from shapely.strtree import STRtree

    mm = lambda v: pcbnew.ToMM(v)  # noqa: E731
    CLR, VIA_R, EDGE = 0.17, 0.3, 0.9  # clearance (a bit above the 0.15 rule), via radius, edge keepout
    pads = []  # (shapely geometry, net, footprint ref, pad number)
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            bb = pad.GetBoundingBox()
            geom = box(mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetRight()), mm(bb.GetBottom()))
            if pad.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
                geom = geom.buffer(0.2)  # drilled: keep vias further away
            pads.append((geom, pad.GetNetname(), fp.GetReference(), pad.GetNumber()))
    tree = STRtree([p[0] for p in pads])
    placed_vias = []  # (Point, net)
    stubs = []  # (LineString, net, width)

    def conflicts(geom, netname, extra):
        for i in tree.query(geom.buffer(extra + 0.5)):
            g, n = pads[i][0], pads[i][1]
            if n != netname and g.distance(geom) < extra:
                return True
        for pt, n in placed_vias:
            limit = (VIA_R + 0.25 + 0.15) if n == netname else (VIA_R + CLR)
            if pt.distance(geom) < (limit if geom.geom_type == "Point" and n == netname else extra + VIA_R):
                if n != netname or geom.geom_type == "Point":
                    return True
        for line, n, w in stubs:
            if n != netname and line.distance(geom) < extra + w / 2:
                return True
        return False

    x0, y0, x1, y1 = OX + EDGE, OY + EDGE, OX + W - EDGE, OY + H - EDGE
    for fp in board.GetFootprints():
        if fp.GetReference() == "J5":
            continue  # USB-C GND/VBUS go through its through-hole shell pins and routed traces
        cx, cy = mm(fp.GetPosition().x), mm(fp.GetPosition().y)
        for pad in fp.Pads():
            netname = pad.GetNetname()
            if netname not in PLANE_NETS or pad.GetAttribute() != pcbnew.PAD_ATTRIB_SMD:
                continue
            px, py = mm(pad.GetPosition().x), mm(pad.GetPosition().y)
            bb = pad.GetBoundingBox()
            w, h = mm(bb.GetWidth()), mm(bb.GetHeight())
            width = 0.2 if min(w, h) < 0.35 else 0.3
            if fp.GetReference() == "U1" and pad.GetNumber() == "57":
                for dx in (-1.0, 0.0, 1.0):  # ground vias inside the RP2040 exposed pad
                    for dy in (-1.0, 0.0, 1.0):
                        add_via(FromMM(px + dx), FromMM(py + dy), netname)
                        placed_vias.append((Point(px + dx, py + dy), netname))
                continue
            # 1) reuse a nearby via of the same net
            reused = False
            for pt, n in placed_vias:
                if n == netname and Point(px, py).distance(pt) < 1.6:
                    line = LineString([(px, py), (pt.x, pt.y)])
                    if not conflicts(line, netname, CLR + width / 2):
                        add_track((FromMM(px), FromMM(py)), (FromMM(pt.x), FromMM(pt.y)), netname, width)
                        stubs.append((line, netname, width))
                        reused = True
                        break
            if reused:
                continue
            # 2) new via: outward along the long axis first, then the other directions
            out_x = 1 if px >= cx else -1
            out_y = 1 if py >= cy else -1
            dirs = [(out_x, 0), (0, out_y)] if w >= h else [(0, out_y), (out_x, 0)]
            dirs += [(-d[0], -d[1]) for d in dirs] + [(out_x, out_y), (-out_x, out_y), (out_x, -out_y)]
            done = False
            for dist in (0.55, 0.8, 1.1, 1.5, 2.0):
                for dx, dy in dirs:
                    ext = (w / 2 if dx else 0) + (h / 2 if dy else 0)
                    norm = (dx * dx + dy * dy) ** 0.5
                    vx = px + dx / norm * (ext + dist + VIA_R - 0.3)
                    vy = py + dy / norm * (ext + dist + VIA_R - 0.3)
                    if not (x0 < vx < x1 and y0 < vy < y1):
                        continue
                    via_pt = Point(vx, vy)
                    line = LineString([(px, py), (vx, vy)])
                    if conflicts(via_pt.buffer(VIA_R), netname, CLR) or conflicts(line, netname, CLR + width / 2):
                        continue
                    add_track((FromMM(px), FromMM(py)), (FromMM(vx), FromMM(vy)), netname, width)
                    add_via(FromMM(vx), FromMM(vy), netname)
                    placed_vias.append((via_pt, netname))
                    stubs.append((line, netname, width))
                    done = True
                    break
                if done:
                    break
            if not done:
                print(f"fanout: no via spot for {fp.GetReference()} pad {pad.GetNumber()} ({netname}); left to the router")


fanout()


def usb_shell_ground():
    """Tie the USB-C SMD ground pins (A1/B12, A12/B1) to the nearest through-hole shell pin."""
    j5 = board.FindFootprintByReference("J5")
    shell = [p for p in j5.Pads() if p.GetNumber() == "S1"]
    for pad in j5.Pads():
        if pad.GetNumber() in ("A1", "A12"):
            pos = pad.GetPosition()
            near = min(shell, key=lambda s: (s.GetPosition() - pos).EuclideanNorm())
            add_track((pos.x, pos.y), (near.GetPosition().x, near.GetPosition().y), "GND", 0.3)


usb_shell_ground()

# small passives and transistors: keep references on the fab layer only (less silkscreen clutter)
for fp in board.GetFootprints():
    if fp.GetReference()[0] in "RCQD" and not fp.GetReference().startswith("D"):
        fp.Reference().SetVisible(False)

if __name__ == "__main__":
    BUILD.mkdir(exist_ok=True)
    # sanity: every net must reach at least two pads
    counts: dict[str, int] = {}
    for fp in board.GetFootprints():
        for p in fp.Pads():
            if p.GetNetname():
                counts[p.GetNetname()] = counts.get(p.GetNetname(), 0) + 1
    lonely = sorted(n for n, c in counts.items() if c < 2 and n not in PLANE_NETS)
    assert not lonely, f"nets with a single pad: {lonely}"
    out = BUILD / "pressure_pad.kicad_pcb"
    board.Save(str(out))
    (BUILD / "bom_parts.json").write_text(json.dumps(BOM, indent=1))
    print(f"wrote {out}: {len(board.GetFootprints())} footprints, {len(counts)} nets, {len(BOM)} BOM lines")
