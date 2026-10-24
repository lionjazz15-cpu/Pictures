"""SeatPad v0 wire protocol (must match firmware/seatpad_pico/seatpad_pico.ino).

Binary frame, little endian:
    offset size  field
    0      4     magic b"CMSP"
    4      1     version (1)
    5      1     flags (bit0: an ADC reading saturated)
    6      1     rows (seat + back)
    7      1     cols
    8      4     seq
    12     8     device time at scan start [us]
    20     2     vb_raw (ADC reading of the Vb reference)
    22     2     seat_rows (rows [0, seat_rows) are seat, the rest back)
    24     2*R*C raw ADC values, row-major
    end-2  2     CRC-16/CCITT-FALSE over bytes [4, end-2)

Text replies are lines starting with b"#" (e.g. "#INFO {...}", "#PONG <host_ns> <dev_us>").
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

MAGIC = b"CMSP"
VERSION = 1
HEADER = struct.Struct("<4sBBBBIQHH")
FLAG_SATURATED = 0x01
MAX_TEXT_LINE = 256


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


@dataclass
class Frame:
    seq: int
    device_t_us: int
    flags: int
    vb_raw: int
    seat_rows: int
    raw: np.ndarray  # (rows, cols) uint16

    @property
    def seat(self) -> np.ndarray:
        return self.raw[: self.seat_rows]

    @property
    def back(self) -> np.ndarray:
        return self.raw[self.seat_rows :]


def frame_len(rows: int, cols: int) -> int:
    return HEADER.size + rows * cols * 2 + 2


def encode_frame(frame: Frame) -> bytes:
    rows, cols = frame.raw.shape
    body = HEADER.pack(
        MAGIC, VERSION, frame.flags, rows, cols, frame.seq & 0xFFFFFFFF,
        frame.device_t_us, frame.vb_raw, frame.seat_rows,
    ) + frame.raw.astype("<u2").tobytes()
    return body + struct.pack("<H", crc16_ccitt(body[4:]))


class StreamParser:
    """Splits a USB CDC byte stream into Frames and text lines, resyncing on errors."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self.crc_errors = 0

    def feed(self, data: bytes) -> list[Frame | str]:
        self._buf += data
        out: list[Frame | str] = []
        while self._buf:
            if self._buf[0] == ord("#"):
                end = self._buf.find(b"\n", 0, MAX_TEXT_LINE)
                line = self._buf[1 : end if end >= 0 else MAX_TEXT_LINE]
                # A '#' inside binary data is not a reply: replies are printable ASCII.
                if not all(0x20 <= b < 0x7F or b == 0x0D for b in line):
                    del self._buf[0]
                    continue
                if end < 0:
                    if len(self._buf) >= MAX_TEXT_LINE:
                        del self._buf[0]
                        continue
                    break
                out.append(line.decode("ascii").strip())
                del self._buf[: end + 1]
                continue
            if self._buf[:4] == MAGIC[: len(self._buf[:4])] and len(self._buf) < HEADER.size:
                break  # possibly a frame, wait for the header
            if self._buf[:4] != MAGIC:
                self._skip_to_candidate()
                continue
            _, version, flags, rows, cols, seq, t_us, vb, seat_rows = HEADER.unpack_from(self._buf)
            n = frame_len(rows, cols)
            if version != VERSION or rows == 0 or cols == 0 or seat_rows > rows:
                del self._buf[0]
                continue
            if len(self._buf) < n:
                break
            payload = bytes(self._buf[:n])
            (crc,) = struct.unpack_from("<H", payload, n - 2)
            if crc != crc16_ccitt(payload[4 : n - 2]):
                self.crc_errors += 1
                del self._buf[0]
                continue
            raw = np.frombuffer(payload, dtype="<u2", count=rows * cols, offset=HEADER.size)
            out.append(Frame(seq, t_us, flags, vb, seat_rows, raw.reshape(rows, cols).copy()))
            del self._buf[:n]
        return out

    def _skip_to_candidate(self) -> None:
        i = 1
        while i < len(self._buf) and self._buf[i] not in (ord("#"), MAGIC[0]):
            i += 1
        del self._buf[:i]


@dataclass
class Electrical:
    """Front-end constants used to turn raw ADC counts into cell conductance."""

    rf_ohm: float = 1000.0  # TIA feedback resistor
    adc_vref: float = 3.3
    adc_max: int = 4095
    row_driver_ohm: float = 2.0  # on-resistance of the low-side row driver


def conductance_us(frame: Frame, el: Electrical = Electrical()) -> np.ndarray:
    """Cell conductance in microsiemens, corrected for the row driver's voltage drop."""
    volts_per_count = el.adc_vref / el.adc_max
    vb = frame.vb_raw * volts_per_count
    current = np.clip(frame.raw.astype(np.float64) - frame.vb_raw, 0, None) * volts_per_count / el.rf_ohm
    # The selected row sits at I_row * R_driver instead of 0 V, so every cell in it sees
    # (Vb - V_row) rather than Vb.
    v_row = current.sum(axis=1, keepdims=True) * el.row_driver_ohm
    return current / np.maximum(vb - v_row, 1e-3) * 1e6
