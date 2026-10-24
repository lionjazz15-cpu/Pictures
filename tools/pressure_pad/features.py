"""Hand-crafted features from pressure maps (v0 baselines, no learning).

Floor pad coordinates: x = toward the sitter's right, y = away from the chair (toward the desk),
origin at the corner of cell (row 0, col 0). Units are cm.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class PadGeometry:
    pitch_x_cm: float  # column pitch
    pitch_y_cm: float  # row pitch


FLOOR_GEOMETRY = PadGeometry(pitch_x_cm=3.75, pitch_y_cm=3.0)
SEAT_GEOMETRY = PadGeometry(pitch_x_cm=2.4, pitch_y_cm=2.4)


@dataclass
class Foot:
    """One foot's contact on the floor pad in a single frame."""

    side: str  # "L", "R" or "?" (a single foot cannot be assigned from position alone)
    x_cm: float  # center of pressure
    y_cm: float
    y_min_cm: float  # rear (chair-side) and front edges of the contact
    y_max_cm: float
    load: float  # sum of conductance-like units over the foot
    cells: int

    @property
    def length_cm(self) -> float:
        return self.y_max_cm - self.y_min_cm


def _components(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    """4-connected components of a boolean grid."""
    seen = np.zeros_like(mask, dtype=bool)
    comps = []
    for start in zip(*np.nonzero(mask)):
        if seen[start]:
            continue
        stack, comp = [start], []
        seen[start] = True
        while stack:
            r, c = stack.pop()
            comp.append((r, c))
            for nr, nc in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
                if 0 <= nr < mask.shape[0] and 0 <= nc < mask.shape[1] and mask[nr, nc] and not seen[nr, nc]:
                    seen[nr, nc] = True
                    stack.append((nr, nc))
        comps.append(comp)
    return comps


def _split_wide(comp, pressure, geometry, max_width_cm):
    """Split a component wider than one foot into two by weighted 2-means on x."""
    rows, cols = np.array(comp).T
    x = (cols + 0.5) * geometry.pitch_x_cm
    if x.max() - x.min() + geometry.pitch_x_cm <= max_width_cm:
        return [comp]
    w = pressure[rows, cols]
    centers = np.array([x.min(), x.max()], dtype=float)
    for _ in range(10):
        left = np.abs(x - centers[0]) <= np.abs(x - centers[1])
        if left.all() or not left.any():
            return [comp]
        centers = np.array([np.average(x[left], weights=w[left]), np.average(x[~left], weights=w[~left])])
    return [[rc for rc, l in zip(comp, left) if l], [rc for rc, l in zip(comp, left) if not l]]


def detect_feet(
    pressure: np.ndarray,
    geometry: PadGeometry = FLOOR_GEOMETRY,
    threshold: float = 50.0,
    min_load: float = 300.0,
    max_foot_width_cm: float = 13.0,
    merge_dx_cm: float = 9.0,
    merge_dy_cm: float = 24.0,
) -> list[Foot]:
    """Find up to two feet on the floor pad in one frame.

    `pressure` is a baseline-subtracted map (e.g. conductance in uS). Feet placed side by side can
    touch, so components wider than a foot are split. A foot often shows up as two blobs (heel and
    forefoot, with the arch off the floor), so blobs that are close sideways and within a foot
    length front-to-back are merged before picking the two heaviest groups.
    """
    blobs = []
    for comp in _components(pressure > threshold):
        for part in _split_wide(comp, pressure, geometry, max_foot_width_cm):
            rows, cols = np.array(part).T
            w = pressure[rows, cols]
            blobs.append({
                "cells": part,
                "load": float(w.sum()),
                "x": float(((cols + 0.5) * geometry.pitch_x_cm * w).sum() / w.sum()),
                "y": float(((rows + 0.5) * geometry.pitch_y_cm * w).sum() / w.sum()),
            })

    groups: list[list[dict]] = []
    for blob in sorted(blobs, key=lambda b: -b["load"]):
        for group in groups:
            gx = sum(b["x"] * b["load"] for b in group) / sum(b["load"] for b in group)
            if abs(blob["x"] - gx) < merge_dx_cm and all(abs(blob["y"] - b["y"]) < merge_dy_cm for b in group):
                group.append(blob)
                break
        else:
            groups.append([blob])

    feet = []
    for group in groups:
        rows, cols = np.array([rc for b in group for rc in b["cells"]]).T
        w = pressure[rows, cols]
        load = float(w.sum())
        if load < min_load:
            continue
        y = (rows + 0.5) * geometry.pitch_y_cm
        feet.append(Foot(
            side="?",
            x_cm=float(((cols + 0.5) * geometry.pitch_x_cm * w).sum() / load),
            y_cm=float((y * w).sum() / load),
            y_min_cm=float(y.min() - geometry.pitch_y_cm / 2),
            y_max_cm=float(y.max() + geometry.pitch_y_cm / 2),
            load=load, cells=len(rows),
        ))
    feet = sorted(feet, key=lambda f: -f.load)[:2]
    feet.sort(key=lambda f: f.x_cm)
    if len(feet) == 2:
        feet[0].side, feet[1].side = "L", "R"
    return feet


@dataclass
class FootState:
    side: str
    contact: str  # "full", "toe", "heel", "partial" (unknown part) or "lifted"
    x_cm: float | None
    y_cm: float | None
    load: float


class FootTracker:
    """Keeps left/right identity over time and labels toe/heel contact.

    Toe- vs heel-only contact cannot be told from a single frame: a short contact patch looks the
    same either way. The tracker remembers where each foot's full sole was last seen and compares.
    """

    def __init__(
        self, full_length_cm: float = 15.0, max_jump_cm: float = 20.0,
        center_x_cm: float = 8 * FLOOR_GEOMETRY.pitch_x_cm,
    ) -> None:
        self.full_length_cm = full_length_cm
        self.max_jump_cm = max_jump_cm
        self.center_x_cm = center_x_cm  # fallback split when there is no history
        self.last: dict[str, Foot] = {}
        self.last_full: dict[str, Foot] = {}

    def _assign(self, feet: list[Foot]) -> dict[str, Foot]:
        if len(feet) == 2:
            return {"L": feet[0], "R": feet[1]}
        if len(feet) == 1:
            f = feet[0]
            if self.last:
                side = min(self.last, key=lambda s: abs(self.last[s].x_cm - f.x_cm))
                if abs(self.last[side].x_cm - f.x_cm) <= self.max_jump_cm:
                    return {side: f}
            return {"L" if f.x_cm < self.center_x_cm else "R": f}
        return {}

    def update(self, feet: list[Foot]) -> dict[str, FootState]:
        assigned = self._assign(feet)
        states = {}
        for side in ("L", "R"):
            f = assigned.get(side)
            if f is None:
                states[side] = FootState(side, "lifted", None, None, 0.0)
                continue
            f.side = side
            if f.length_cm >= self.full_length_cm:
                contact = "full"
                self.last_full[side] = f
            elif side in self.last_full:
                ref = self.last_full[side]
                contact = "toe" if f.y_cm > (ref.y_min_cm + ref.y_max_cm) / 2 else "heel"
            else:
                contact = "partial"
            self.last[side] = f
            states[side] = FootState(side, contact, f.x_cm, f.y_cm, f.load)
        return states


def center_of_pressure(m: np.ndarray, geometry: PadGeometry) -> tuple[float, float] | None:
    total = m.sum()
    if total <= 0:
        return None
    rows, cols = np.mgrid[0 : m.shape[0], 0 : m.shape[1]]
    return (
        float(((cols + 0.5) * geometry.pitch_x_cm * m).sum() / total),
        float(((rows + 0.5) * geometry.pitch_y_cm * m).sum() / total),
    )
