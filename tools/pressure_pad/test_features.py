import numpy as np

from features import FLOOR_GEOMETRY, FootTracker, detect_feet


def blob(cy, cx, sy, sx, amp, shape=(16, 16)):
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]]
    return amp * np.exp(-(((yy - cy) / sy) ** 2 + ((xx - cx) / sx) ** 2))


def foot(cx, heel_row=3.0, heel=1.0, fore=1.0):
    # heel and forefoot as separate blobs with the arch (about 2 rows) off the floor
    return blob(heel_row, cx, 0.9, 0.9, 600 * heel) + blob(heel_row + 5, cx, 1.1, 1.0, 600 * fore)


def test_two_feet_with_split_heel_and_forefoot():
    feet = detect_feet(foot(4.5) + foot(11.5))
    assert [f.side for f in feet] == ["L", "R"]
    assert abs(feet[0].x_cm - 4.5 * 3.75 - 1.875) < 1.0
    assert abs(feet[1].x_cm - 11.5 * 3.75 - 1.875) < 1.0
    assert all(f.length_cm >= 15 for f in feet)


def test_one_foot_lifted_gives_unknown_side():
    feet = detect_feet(foot(5))
    assert len(feet) == 1 and feet[0].side == "?"


def test_tracker_toe_and_heel_after_full_contact():
    tracker = FootTracker()
    states = tracker.update(detect_feet(foot(4) + foot(12)))
    assert states["L"].contact == states["R"].contact == "full"
    states = tracker.update(detect_feet(foot(4, heel=0.0) + foot(12, fore=0.0)))
    assert states["L"].contact == "toe" and states["R"].contact == "heel"


def test_tracker_keeps_side_when_one_foot_lifts():
    tracker = FootTracker()
    tracker.update(detect_feet(foot(4) + foot(12)))
    states = tracker.update(detect_feet(foot(12.3)))  # left leg crossed over, off the floor
    assert states["L"].contact == "lifted" and states["R"].contact == "full"
    states = tracker.update(detect_feet(foot(4.4)))
    assert states["R"].contact == "lifted" and states["L"].contact == "full"


def test_tracker_single_foot_without_history_uses_pad_center():
    states = FootTracker().update(detect_feet(foot(12)))
    assert states["L"].contact == "lifted" and states["R"].contact == "full"


def test_tracker_without_history_reports_partial():
    states = FootTracker().update(detect_feet(foot(4, heel=0.0) + foot(12, heel=0.0)))
    assert states["L"].contact == states["R"].contact == "partial"


def test_feet_close_together_stay_separate():
    feet = detect_feet(foot(6) + foot(9.5))  # ~13 cm apart
    assert len(feet) == 2


def test_empty_and_noise():
    assert detect_feet(np.zeros((16, 16))) == []
    rng = np.random.default_rng(0)
    assert detect_feet(np.abs(rng.normal(0, 10, (16, 16)))) == []


def test_geometry_units():
    feet = detect_feet(foot(8, heel_row=4))
    assert abs(feet[0].y_cm - FLOOR_GEOMETRY.pitch_y_cm * (4 + 2.5 + 0.5)) < 2.0
