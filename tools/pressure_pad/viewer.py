"""Real-time heatmap viewer and recorder for the chair pad (seat + back) and the floor pad.

    python viewer.py --port COM5               # Windows; the pad kind is read from the device
    python viewer.py --port /dev/ttyACM0       # Linux
    python viewer.py --fake chair              # no hardware: synthetic sitter
    python viewer.py --fake floor              # no hardware: synthetic feet
    python viewer.py --fake test               # no hardware: 4x4 practice circuit

Keys: z = capture empty baseline (step off / stand up first), r = start/stop recording, q = quit.
"""

from __future__ import annotations

import argparse
import queue
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from features import FLOOR_GEOMETRY, SEAT_GEOMETRY, FootTracker, center_of_pressure, detect_feet
from protocol import FLAG_SATURATED, FLAG_SETTLING, KIND_CHAIR, KIND_FLOOR, KIND_TEST, Electrical, Frame, StreamParser, conductance_us, encode_frame

BASELINE_FRAMES = 30
KIND_NAMES = {KIND_CHAIR: "chair", KIND_FLOOR: "floor", KIND_TEST: "test"}


class TimeSync:
    """Device-to-host clock offset from PING/PONG, keeping the lowest-RTT samples."""

    def __init__(self) -> None:
        self.samples: list[tuple[int, int, int]] = []  # (host_send_ns, host_recv_ns, dev_us)
        self.offset_ns: float | None = None

    def on_pong(self, host_send_ns: int, dev_us: int) -> None:
        self.samples.append((host_send_ns, time.monotonic_ns(), dev_us))
        recent = sorted(self.samples[-30:], key=lambda s: s[1] - s[0])[:5]
        self.offset_ns = float(np.median([(s + r) / 2 - d * 1000 for s, r, d in recent]))


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


def _blob(shape, cy, cx, sy, sx, amp):
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]]
    return amp * np.exp(-(((yy - cy) / sy) ** 2 + ((xx - cx) / sx) ** 2))


class FakeSource:
    """Synthetic pad data encoded through the real protocol, for trying the tools without hardware."""

    def __init__(self, kind: int, rate_hz: float = 60.0) -> None:
        self.kind = kind
        self.period = 1.0 / rate_hz
        self.t0 = time.monotonic()
        self.next = self.t0
        self.seq = 0
        self.pending: list[str] = [f'#INFO {{"fw":"fake","kind":"{KIND_NAMES[kind]}"}}']

    def _chair(self, t: float) -> np.ndarray:
        shift = 1.5 * np.sin(t * 0.7)  # weight shift left/right
        lean = 0.5 + 0.5 * np.sin(t * 0.3)  # 0 = upright, 1 = leaning back
        cross = max(0.0, np.sin(t * 0.15))  # right leg crossed over left
        s = (16, 16)
        # Seat rows run back (row 0) to front (row 15); ischial tuberosities near row 4.
        seat = (
            _blob(s, 4, 5.5 + shift, 2.0, 1.8, 1.0 + 0.3 * shift)
            + _blob(s, 4, 10.5 + shift, 2.0, 1.8, 1.0 - 0.3 * shift)
            + _blob(s, 10, 4.5, 4.0, 1.8, 0.45 * (1 + 0.6 * cross))
            + _blob(s, 10, 11.5, 4.0, 1.8, 0.45 * (1 - 0.8 * cross))
        )
        back = _blob((12, 16), 3 + 3 * lean, 7.5 + 0.5 * shift, 2.5 + 2 * lean, 4.0, 0.15 + 0.5 * lean)
        return np.vstack([seat, back])

    def _floor(self, t: float) -> np.ndarray:
        s = (16, 16)
        phase = (t / 4.0) % 4  # cycles: both down, left lifted, right on toes, tapping
        out = np.zeros(s)
        for cx, side in ((4.5, "L"), (11.5, "R")):
            heel, fore = 1.0, 1.0
            if phase < 1 and side == "L":
                continue
            if 1 <= phase < 2 and side == "R":
                heel = 0.0
            if 2 <= phase < 3 and side == "L":
                fore = 0.5 + 0.5 * np.sin(t * 25)  # forefoot tapping
            out += _blob(s, 3, cx, 0.9, 0.9, 0.5 * heel) + _blob(s, 8, cx, 1.1, 1.0, 0.5 * fore)
        return out

    def _test(self, t: float) -> np.ndarray:
        # a finger pressing one cell after another
        out = np.zeros((4, 4))
        k = int(t) % 16
        out[k // 4, k % 4] = 0.5 + 0.5 * np.sin(t * np.pi) ** 2
        return out

    def read(self) -> bytes:
        now = time.monotonic()
        if now < self.next:
            time.sleep(self.next - now)
        self.next += self.period
        t = self.next - self.t0
        signal = {KIND_CHAIR: self._chair, KIND_FLOOR: self._floor, KIND_TEST: self._test}[self.kind](t)
        vb_raw = 2048
        raw = np.clip(vb_raw + signal * 1500 + np.random.normal(0, 6, signal.shape), 0, 4095).astype(np.uint16)
        frame = Frame(self.seq, int(t * 1e6), 0, vb_raw, signal.shape[0], raw, self.kind)
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
        self.kind = KIND_CHAIR
        self.main_rows = 16
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
        path = self.out_dir / f"{KIND_NAMES[self.kind]}pad_{datetime.now():%Y%m%d_%H%M%S}.npz"
        seq, dev_us, host_ns, vb, raw = zip(*self.rows)
        np.savez_compressed(
            path, seq=np.array(seq), device_t_us=np.array(dev_us), host_recv_ns=np.array(host_ns),
            vb_raw=np.array(vb), raw=np.stack(raw), kind=KIND_NAMES[self.kind], main_rows=self.main_rows,
            pongs=np.array(sync.samples, dtype=np.int64).reshape(-1, 3),
            rf_ohm=self.electrical.rf_ohm, row_driver_ohm=self.electrical.row_driver_ohm,
        )
        return f"saved {path} ({len(self.rows)} frames)"

    def add(self, frame: Frame, host_ns: int) -> None:
        self.kind, self.main_rows = frame.kind, frame.main_rows
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


class ChairView:
    def __init__(self, plt, vmax: float, rows_back: int) -> None:
        self.fig, (ax_back, ax_seat) = plt.subplots(
            2, 1, figsize=(6, 9), gridspec_kw={"height_ratios": [max(rows_back, 1), 16]}
        )
        self.im_back = ax_back.imshow(np.zeros((max(rows_back, 1), 16)), vmin=0, vmax=vmax, cmap="inferno", origin="lower")
        self.im_seat = ax_seat.imshow(np.zeros((16, 16)), vmin=0, vmax=vmax, cmap="inferno", origin="lower")
        ax_back.set_title("Back (bottom = lumbar)")
        ax_seat.set_title("Seat (bottom = rear edge)")
        (self.cop,) = ax_seat.plot([], [], "c+", markersize=18, mew=2)
        self.fig.colorbar(self.im_seat, ax=[ax_back, ax_seat], label="conductance [uS]", shrink=0.6)

    def draw(self, g: np.ndarray, main_rows: int) -> str:
        seat, back = g[:main_rows], g[main_rows:]
        self.im_seat.set_data(seat)
        if back.size:
            self.im_back.set_data(back)
        cop = center_of_pressure(seat, SEAT_GEOMETRY)
        if cop:
            self.cop.set_data([cop[0] / SEAT_GEOMETRY.pitch_x_cm - 0.5], [cop[1] / SEAT_GEOMETRY.pitch_y_cm - 0.5])
        else:
            self.cop.set_data([], [])
        lr = "-" if not cop else f"{cop[0] - 8 * SEAT_GEOMETRY.pitch_x_cm:+5.1f}cm"
        return f"seat {seat.sum():8.0f}  back {back.sum():7.0f}  COP L/R {lr}"


class TestView:
    """4x4 practice circuit: big cells with the value printed in each."""

    def __init__(self, plt, vmax: float) -> None:
        self.fig, ax = plt.subplots(figsize=(6, 6))
        self.im = ax.imshow(np.zeros((4, 4)), vmin=0, vmax=vmax, cmap="inferno", origin="lower")
        ax.set_title("Practice 4x4 (row 0 at the bottom)")
        ax.set_xticks(range(4))
        ax.set_yticks(range(4))
        ax.set_xlabel("column")
        ax.set_ylabel("row")
        self.texts = [[ax.text(c, r, "", ha="center", va="center", color="c", fontsize=12) for c in range(4)] for r in range(4)]
        self.fig.colorbar(self.im, ax=ax, label="conductance [uS]", shrink=0.8)

    def draw(self, g: np.ndarray, main_rows: int) -> str:
        self.im.set_data(g)
        for r in range(4):
            for c in range(4):
                self.texts[r][c].set_text(f"{g[r, c]:.0f}")
        r, c = np.unravel_index(np.argmax(g), g.shape)
        return f"max at row {r}, col {c}: {g[r, c]:.0f} uS (~{1e6 / max(g[r, c], 1e-3):.0f} ohm)"


class FloorView:
    def __init__(self, plt, vmax: float) -> None:
        self.fig, ax = plt.subplots(figsize=(7, 6))
        g = FLOOR_GEOMETRY
        extent = (0, 16 * g.pitch_x_cm, 0, 16 * g.pitch_y_cm)
        self.im = ax.imshow(np.zeros((16, 16)), vmin=0, vmax=vmax, cmap="inferno", origin="lower", extent=extent)
        ax.set_title("Floor (bottom = chair side, left = sitter's left)")
        ax.set_xlabel("cm")
        ax.set_ylabel("cm")
        self.markers = {s: ax.plot([], [], "+", color=c, markersize=20, mew=2)[0] for s, c in (("L", "cyan"), ("R", "lime"))}
        self.labels = {s: ax.text(0, 0, "", color="w", fontsize=10, fontweight="bold") for s in ("L", "R")}
        self.fig.colorbar(self.im, ax=ax, label="conductance [uS]", shrink=0.8)
        self.tracker = FootTracker()

    def draw(self, g: np.ndarray, main_rows: int) -> str:
        floor = g[:main_rows]
        self.im.set_data(floor)
        states = self.tracker.update(detect_feet(floor))
        parts = []
        for side, st in states.items():
            if st.x_cm is None:
                self.markers[side].set_data([], [])
                self.labels[side].set_text("")
            else:
                self.markers[side].set_data([st.x_cm], [st.y_cm])
                self.labels[side].set_position((st.x_cm + 2, st.y_cm + 2))
                self.labels[side].set_text(f"{side}:{st.contact}")
            pos = "" if st.x_cm is None else f"({st.x_cm:4.1f},{st.y_cm:4.1f})"
            parts.append(f"{side} {st.contact:7s}{pos:13s}")
        return "  ".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="serial port of the Pico")
    ap.add_argument("--fake", choices=["chair", "floor", "test"], help="use synthetic data instead of hardware")
    ap.add_argument("--rf", type=float, help="TIA feedback resistor [ohm] (default: 2200 on the PCB, 1000 hand-built)")
    ap.add_argument("--vmax", type=float, default=1500.0, help="color scale max [uS]")
    ap.add_argument("--out", type=Path, default=Path("recordings"))
    args = ap.parse_args()
    if not args.fake and not args.port:
        ap.error("--port or --fake is required")

    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation

    fake_kinds = {name: kind for kind, name in KIND_NAMES.items()}
    source = FakeSource(fake_kinds[args.fake]) if args.fake else SerialSource(args.port)
    frames: queue.Queue = queue.Queue()
    sync = TimeSync()
    stop = threading.Event()
    threading.Thread(target=reader_thread, args=(source, frames, sync, stop), daemon=True).start()

    # The layout depends on the pad kind, which the first frame tells us.
    try:
        first, first_ns = frames.get(timeout=5.0)
    except queue.Empty:
        stop.set()
        source.close()
        raise SystemExit("no frames received: check the port and that the firmware is flashed")
    frames.put((first, first_ns))
    electrical = Electrical.for_board(first.board, args.rf)
    if first.kind == KIND_FLOOR:
        view = FloorView(plt, args.vmax)
    elif first.kind == KIND_TEST:
        view = TestView(plt, args.vmax)
    else:
        view = ChairView(plt, args.vmax, first.raw.shape[0] - first.main_rows)
    fig = view.fig
    info = fig.text(0.02, 0.01, "", family="monospace", fontsize=9)

    recorder = Recorder(args.out, electrical)
    state = {"baseline": None, "collect": None, "status": "z: zero (empty pad)  r: record  q: quit"}
    fps = {"n": 0, "t": time.monotonic(), "value": 0.0}

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
            if state["collect"] is not None:
                state["collect"].append(conductance_us(frame, electrical))
                if len(state["collect"]) >= BASELINE_FRAMES:
                    state["baseline"] = np.mean(state["collect"], axis=0)
                    state["collect"] = None
                    state["status"] = "baseline captured"
        if latest is None:
            return
        g = conductance_us(latest, electrical)
        if state["baseline"] is not None:
            g = np.clip(g - state["baseline"], 0, None)
        summary = view.draw(g, latest.main_rows)
        now = time.monotonic()
        if now - fps["t"] >= 1.0:
            fps["value"] = fps["n"] / (now - fps["t"])
            fps["n"], fps["t"] = 0, now
        sat = (" SATURATED" if latest.flags & FLAG_SATURATED else "") + (" (range change)" if latest.flags & FLAG_SETTLING else "")
        offset = "-" if sync.offset_ns is None else f"{sync.offset_ns / 1e6:.1f}ms"
        info.set_text(
            f"{fps['value']:5.1f} fps  seq {latest.seq}  clk-offset {offset}{sat}\n{summary}\n"
            f"{state['status']}{'  [REC]' if recorder.active else ''}"
        )

    _anim = FuncAnimation(fig, update, interval=16, cache_frame_data=False)
    plt.show()
    stop.set()
    if recorder.active:
        print(recorder.toggle(sync))
    source.close()


if __name__ == "__main__":
    main()
