import numpy as np

from protocol import BOARD_PCB_V1, KIND_FLOOR, KIND_TEST, Electrical, Frame, StreamParser, conductance_us, crc16_ccitt, encode_frame


def make_frame(seq=7, rows=28, cols=16):
    raw = (np.arange(rows * cols, dtype=np.uint16) % 4096).reshape(rows, cols)
    return Frame(seq, 123_456_789_012, 0, 2048, 16, raw)


def test_crc_reference_value():
    # CRC-16/CCITT-FALSE check value
    assert crc16_ccitt(b"123456789") == 0x29B1


def test_roundtrip():
    frame = make_frame()
    (out,) = StreamParser().feed(encode_frame(frame))
    assert out.seq == 7 and out.device_t_us == frame.device_t_us and out.vb_raw == 2048
    np.testing.assert_array_equal(out.raw, frame.raw)
    assert out.main.shape == (16, 16) and out.back.shape == (12, 16)


def test_floor_kind_roundtrip():
    raw = np.zeros((16, 16), dtype=np.uint16)
    (out,) = StreamParser().feed(encode_frame(Frame(1, 2, 0, 2048, 16, raw, KIND_FLOOR)))
    assert out.kind == KIND_FLOOR and out.main.shape == (16, 16) and out.back.shape == (0, 16)


def test_split_chunks_text_and_garbage():
    data = b"\x00\x13garbageCM#PONG 1 2\n" + encode_frame(make_frame(1)) + b"#INFO {}\n" + encode_frame(make_frame(2))
    parser = StreamParser()
    items = []
    for i in range(0, len(data), 37):
        items += parser.feed(data[i : i + 37])
    frames = [x for x in items if isinstance(x, Frame)]
    texts = [x for x in items if isinstance(x, str)]
    assert [f.seq for f in frames] == [1, 2]
    assert texts == ["PONG 1 2", "INFO {}"]


def test_corrupted_frame_is_dropped_and_stream_recovers():
    bad = bytearray(encode_frame(make_frame(1)))
    bad[100] ^= 0xFF
    parser = StreamParser()
    items = parser.feed(bytes(bad) + encode_frame(make_frame(2)))
    assert [f.seq for f in items if isinstance(f, Frame)] == [2]
    assert parser.crc_errors == 1


def test_conductance_matches_single_cell():
    raw = np.full((28, 16), 2048, dtype=np.uint16)
    raw[3, 5] = 2048 + 1241  # ~1.0 V across Rf
    el = Electrical(rf_ohm=1000.0, row_driver_ohm=0.0)
    g = conductance_us(Frame(0, 0, 0, 2048, 16, raw), el)
    # I = 1.0 V / 1 kohm = 1 mA, Vcell = Vb = 1.65 V -> G = 606 uS
    assert abs(g[3, 5] - 606) < 2
    assert g.sum() == g[3, 5]


def test_row_driver_drop_increases_conductance():
    raw = np.full((28, 16), 2048 + 600, dtype=np.uint16)
    g0 = conductance_us(Frame(0, 0, 0, 2048, 16, raw), Electrical(row_driver_ohm=0.0))
    g1 = conductance_us(Frame(0, 0, 0, 2048, 16, raw), Electrical(row_driver_ohm=5.0))
    assert np.all(g1 > g0)


def test_test_kind_4x4_roundtrip():
    raw = np.full((4, 4), 2100, dtype=np.uint16)
    (out,) = StreamParser().feed(encode_frame(Frame(1, 2, 0, 2048, 4, raw, KIND_TEST)))
    assert out.kind == KIND_TEST and out.raw.shape == (4, 4)


def test_debug_dump_lines_are_text():
    dump = b"#DUMP vb=2047 (about 2048 is normal)\n#row0 :    0   12  300    0\n"
    items = StreamParser().feed(dump)
    assert items == ["DUMP vb=2047 (about 2048 is normal)", "row0 :    0   12  300    0"]


def test_board_nibble_roundtrip_and_defaults():
    raw = np.full((28, 16), 2048, dtype=np.uint16)
    (out,) = StreamParser().feed(encode_frame(Frame(1, 2, 0, 2048, 16, raw, KIND_FLOOR, BOARD_PCB_V1)))
    assert out.kind == KIND_FLOOR and out.board == BOARD_PCB_V1
    el = Electrical.for_board(BOARD_PCB_V1)
    assert el.rf_ohm == 2200.0 and el.row_driver_ohm < 0.1
    assert Electrical.for_board(0, 680.0).rf_ohm == 680.0


def test_conductance_is_independent_of_vb_range():
    # Same cell (G = 200 uS) read at Vb = 1.65 V and at Vb = 0.35 V on the PCB (Rf = 2.2k)
    el = Electrical.for_board(BOARD_PCB_V1)
    counts_per_v = 4095 / 3.3
    gs = []
    for vb in (1.65, 0.35):
        raw = np.full((28, 16), round(vb * counts_per_v), dtype=np.uint16)
        raw[0, 0] = round((vb + vb * 200e-6 * 2200) * counts_per_v)
        gs.append(conductance_us(Frame(0, 0, 0, round(vb * counts_per_v), 16, raw, 0, BOARD_PCB_V1), el)[0, 0])
    assert abs(gs[0] - 200) < 3 and abs(gs[1] - 200) < 8
