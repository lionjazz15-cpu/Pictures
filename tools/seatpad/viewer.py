"""Real-time heatmap viewer and recorder for the SeatPad v0.

    python viewer.py --port COM5          # Windows
    python viewer.py --port /dev/ttyACM0  # Linux
    python viewer.py --fake               # no hardware: synthetic sitter

Keys: z = capture empty-chair baseline (stand up first), r = start/stop recording, q = quit.
"""

from __future__ import annotations

import argparse
import queue
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from protocol import Electrical, Frame, StreamParser, conductance_us, encode_frame

SEAT_ROWS, BACK_ROWS, COLS = 16, 12, 16
BASELINE_FRAMES = 30


class TimeSync:
    """Device-to-host clock offset from PING/PONG, keeping the lowest-RTT samples."""

    def __init__(self) -> None:
        self.samples: list[tuple[int, int, int]] = []  # (host_send_ns, host_recv_ns, dev_us)
        self.offset_ns: float | None = None

    def on_pong(self, host_send_ns: int, dev_us: int) -> None:
        self.samples.append((host_send_ns, time.monotonic_ns(), dev_us))
        recent = sorted(self.samples[-30:], key=lambda s: s[1] - s[0])[:5]
        self.offset_ns = float(np.median([(s + r) / 2 - d * 1000 for s, r, d in recent]))

    def device_to_host_ns(self, dev_us: int) -> float | None:
        return None if self.offset_ns is None else dev_us * 1000 + self.offset_ns


class SerialSource:
    def __init__(self, port: str) -> None:
        import serial  # pyserial

        self.ser = serial.Serial(port, 115200, timeout=0.05)
        self.ser.write(b"STOP\n")
        time.sleep(0.1)
        self.ser.reset_input_buffer()
        self.ser.write(b"INFO\nSTART\n")

    def read(self) -> bytes:
        return self.ser.read(4096)

    def send(self, line: str) -> None:
        self.ser.write((line + "\n").encode())

    def close(self) -> None:
        self.ser.write(b"STOP\n")
        self.ser.close()


class FakeSource:
    """Synthetic sitter that leans, shifts weight and crosses legs, encoded through the real protocol."""

    def __init__(self, rate_hz: float = 60.0) -> None:
        self.period = 1.0 / rate_hz
        self.t0 = time.monotonic()
        self.next = self.t0
        self.seq = 0
        self.pending: list[str] = ['#INFO {"fw":"fake"}']
        yy, xx = np.mgrid[0:SEAT_ROWS, 0:COLS]
        self.seat_grid = (yy, xx)
        yy, xx = np.mgrid[0:BACK_ROWS, 0:COLS]
        self.back_grid = (yy, xx)

    @staticmethod
    def _blob(grid, cy, cx, sy, sx, amp):
        yy, xx = grid
        return amp * np.exp(-(((yy - cy) / sy) ** 2 + ((xx - cx) / sx) ** 2))

    def read(self) -> bytes:
        now = time.monotonic()
        if now < self.next:
            time.sleep(self.next - now)
        self.next += self.period
        t = self.next - self.t0
        shift = 1.5 * np.sin(t * 0.7)  # weight shift left/right
        lean = 0.5 + 0.5 * np.sin(t * 0.3)  # 0 = upright, 1 = leaning back
        cross = max(0.0, np.sin(t * 0.15))  # right leg crossed over left
        # Seat rows run back (row 0) to front (row 15); ischial tuberosities near row 4.
        seat = (
            self._blob(self.seat_grid, 4, 5.5 + shift, 2.0, 1.8, 1.0 + 0.3 * shift)
            + self._blob(self.seat_grid, 4, 10.5 + shift, 2.0, 1.8, 1.0 - 0.3 * shift)
            + self._blob(self.seat_grid, 10, 4.5, 4.0, 1.8, 0.45 * (1 + 0.6 * cross))
            + self._blob(self.seat_grid, 10, 11.5, 4.0, 1.8, 0.45 * (1 - 0.8 * cross))
        )
        back = self._blob(self.back_grid, 3 + 3 * lean, 7.5 + 0.5 * shift, 2.5 + 2 * lean, 4.0, 0.15 + 0.5 * lean)
        vb_raw = 2048
        signal = np.vstack([seat, back]) * 1500 + np.random.normal(0, 6, (SEAT_ROWS + BACK_ROWS, COLS))
        raw = np.clip(vb_raw + signal, 0, 4095).astype(np.uint16)
        frame = Frame(self.seq, int(t * 1e6), 0, vb_raw, SEAT_ROWS, raw)
        self.seq += 1
        text = "".join(line + "\n" for line in self.pending).encode()
        self.pending.clear()
        return text + encode_frame(frame)

    def send(self, line: str) -> None:
        if line.startswith("PING "):
            self.pending.append(f"#PONG {line[5:]} {int((time.monotonic() - self.t0) * 1e6)}")

    def close(self) -> None:
        pass


class Recorder:
    def __init__(self, out_dir: Path, electrical: Electrical) -> None:
        self.out_dir = out_dir
        self.electrical = electrical
        self.rows: list[tuple[int, int, int, int, np.ndarray]] = []
        self.active = False

    def toggle(self, sync: TimeSync) -> str:
        if not self.active:
            self.rows.clear()
            self.active = True
            return "recording..."
        self.active = False
        if not self.rows:
            return "nothing recorded"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / f"seatpad_{datetime.now():%Y%m%d_%H%M%S}.npz"
        seq, dev_us, host_ns, vb, raw = zip(*self.rows)
        np.savez_compressed(
            path, seq=np.array(seq), device_t_us=np.array(dev_us), host_recv_ns=np.array(host_ns),
            vb_raw=np.array(vb), raw=np.stack(raw), seat_rows=SEAT_ROWS,
            pongs=np.array(sync.samples, dtype=np.int64).reshape(-1, 3),
            rf_ohm=self.electrical.rf_ohm, row_driver_ohm=self.electrical.row_driver_ohm,
        )
        return f"saved {path} ({len(self.rows)} frames)"

    def add(self, frame: Frame, host_ns: int) -> None:
        if self.active:
            self.rows.append((frame.seq, frame.device_t_us, host_ns, frame.vb_raw, frame.raw))


def reader_thread(source, frames: queue.Queue, sync: TimeSync, stop: threading.Event) -> None:
    parser = StreamParser()
    last_ping = 0.0
    while not stop.is_set():
        if time.monotonic() - last_ping > 1.0:
            source.send(f"PING {time.monotonic_ns()}")
            last_ping = time.monotonic()
        for item in parser.feed(source.read()):
            if isinstance(item, Frame):
                frames.put((item, time.monotonic_ns()))
            elif item.startswith("PONG "):
                host_ns, dev_us = item.split()[1:3]
                sync.on_pong(int(host_ns), int(dev_us))
            else:
                print(item)


def center_of_pressure(m: np.ndarray) -> tuple[float, float] | None:
    total = m.sum()
    if total <= 0:
        return None
    yy, xx = np.mgrid[0 : m.shape[0], 0 : m.shape[1]]
    return float((xx * m).sum() / total), float((yy * m).sum() / total)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="serial port of the Pico")
    ap.add_argument("--fake", action="store_true", help="use a synthetic sitter instead of hardware")
    ap.add_argument("--rf", type=float, default=1000.0, help="TIA feedback resistor [ohm]")
    ap.add_argument("--vmax", type=float, default=1500.0, help="color scale max [uS]")
    ap.add_argument("--out", type=Path, default=Path("recordings"))
    args = ap.parse_args()
    if not args.fake and not args.port:
        ap.error("--port or --fake is required")

    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation

    electrical = Electrical(rf_ohm=args.rf)
    source = FakeSource() if args.fake else SerialSource(args.port)
    frames: queue.Queue = queue.Queue()
    sync = TimeSync()
    stop = threading.Event()
    threading.Thread(target=reader_thread, args=(source, frames, sync, stop), daemon=True).start()

    recorder = Recorder(args.out, electrical)
    state = {"baseline": None, "collect": [], "status": "z: zero (stand up first)  r: record  q: quit"}
    fps = {"n": 0, "t": time.monotonic(), "value": 0.0}

    fig, (ax_back, ax_seat) = plt.subplots(2, 1, figsize=(6, 9), gridspec_kw={"height_ratios": [BACK_ROWS, SEAT_ROWS]})
    im_back = ax_back.imshow(np.zeros((BACK_ROWS, COLS)), vmin=0, vmax=args.vmax, cmap="inferno", origin="lower")
    im_seat = ax_seat.imshow(np.zeros((SEAT_ROWS, COLS)), vmin=0, vmax=args.vmax, cmap="inferno", origin="lower")
    ax_back.set_title("Back (bottom = lumbar)")
    ax_seat.set_title("Seat (bottom = rear edge)")
    (cop_marker,) = ax_seat.plot([], [], "c+", markersize=18, mew=2)
    info = fig.text(0.02, 0.01, "", family="monospace", fontsize=9)
    fig.colorbar(im_seat, ax=[ax_back, ax_seat], label="conductance [uS]", shrink=0.6)

    def on_key(event) -> None:
        if event.key == "z":
            state["collect"] = []
            state["status"] = "capturing baseline..."
        elif event.key == "r":
            state["status"] = recorder.toggle(sync)
        elif event.key == "q":
            plt.close(fig)

    fig.canvas.mpl_connect("key_press_event", on_key)

    def update(_):
        latest = None
        while not frames.empty():
            frame, host_ns = frames.get()
            recorder.add(frame, host_ns)
            latest = frame
            fps["n"] += 1
            g = conductance_us(frame, electrical)
            if isinstance(state["collect"], list):
                state["collect"].append(g)
                if len(state["collect"]) >= BASELINE_FRAMES:
                    state["baseline"] = np.mean(state["collect"], axis=0)
                    state["collect"] = None
                    state["status"] = "baseline captured"
        if latest is None:
            return im_seat, im_back, cop_marker, info
        g = conductance_us(latest, electrical)
        if state["baseline"] is not None:
            g = np.clip(g - state["baseline"], 0, None)
        seat, back = g[:SEAT_ROWS], g[SEAT_ROWS:]
        im_seat.set_data(seat)
        im_back.set_data(back)
        cop = center_of_pressure(seat)
        cop_marker.set_data(([cop[0]], [cop[1]]) if cop else ([], []))
        now = time.monotonic()
        if now - fps["t"] >= 1.0:
            fps["value"] = fps["n"] / (now - fps["t"])
            fps["n"], fps["t"] = 0, now
        sat = " SATURATED" if latest.flags & 1 else ""
        offset = "-" if sync.offset_ns is None else f"{sync.offset_ns / 1e6:.1f}ms"
        info.set_text(
            f"{fps['value']:5.1f} fps  seq {latest.seq}  seat {seat.sum():8.0f}  back {back.sum():7.0f}"
            f"  clk-offset {offset}{sat}\n{state['status']}{'  [REC]' if recorder.active else ''}"
        )
        return im_seat, im_back, cop_marker, info

    _anim = FuncAnimation(fig, update, interval=16, blit=False, cache_frame_data=False)
    plt.show()
    stop.set()
    if recorder.active:
        print(recorder.toggle(sync))
    source.close()


if __name__ == "__main__":
    main()
