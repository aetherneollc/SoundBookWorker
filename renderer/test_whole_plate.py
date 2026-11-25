"""Whole-plate checks: a cut leg fails, and a small bend does not open a hole."""
from __future__ import annotations

import unittest

import cv2
import numpy as np

from renderer.whole_plate import (
    accept_plate,
    build_rig,
    hole_budget,
    interior_hole_count,
    pose_points,
    render_pose,
)


def _figure(gap: int = 0) -> np.ndarray:
    """Side-view contact pose on green. ``gap`` detaches the trailing shin."""
    image = np.full((360, 220, 3), (0, 220, 0), dtype=np.uint8)
    skin = (236, 196, 176)
    jacket = (48, 104, 210)
    trousers = (58, 64, 96)
    boot = (92, 58, 42)
    cv2.circle(image, (108, 48), 24, skin, -1, lineType=cv2.LINE_AA)
    cv2.circle(image, (108, 30), 16, (20, 18, 22), -1, lineType=cv2.LINE_AA)
    cv2.rectangle(image, (86, 68), (132, 168), jacket, -1)
    cv2.rectangle(image, (88, 150), (112, 168), jacket, -1)
    shin_top = 214 + gap
    cv2.rectangle(image, (90, 164), (112, 214), trousers, -1)
    cv2.rectangle(image, (90, shin_top), (114, 318), trousers, -1)
    cv2.rectangle(image, (86, 306), (120, 324), boot, -1)
    front = np.array([[124, 164], [148, 164], [176, 318], [148, 318]], dtype=np.int32)
    cv2.fillConvexPoly(image, front, trousers, lineType=cv2.LINE_AA)
    cv2.rectangle(image, (158, 308), (186, 326), boot, -1)
    return image


class WholePlateTests(unittest.TestCase):
    def test_enclosed_hole_is_counted_and_an_open_gap_is_not(self):
        alpha = np.full((80, 80), 255, dtype=np.uint8)
        alpha[30:50, 30:50] = 0
        self.assertGreater(interior_hole_count(alpha), 200)
        opened = alpha.copy()
        opened[30:50, 0:30] = 0
        self.assertEqual(interior_hole_count(opened), 0)

    def test_solid_figure_is_accepted_and_a_cut_shin_is_not(self):
        solid = accept_plate(_figure(0), require_face=False)
        self.assertTrue(solid["ok"], solid["reasons"])
        self.assertTrue(solid["stride"])
        kept = solid["image"]
        self.assertGreater(int(kept[30, 108, 3]), 32)
        self.assertGreater(int(kept[316, 100, 3]), 32)
        self.assertLess(int(kept[8, 8, 3]), 32)
        cut = accept_plate(_figure(18), require_face=False)
        self.assertFalse(cut["ok"])
        self.assertIn("disconnected", cut["reasons"])

    def test_small_bend_lifts_the_rear_foot_without_a_new_hole(self):
        plate = accept_plate(_figure(0), require_face=False)
        rig = build_rig(plate["image"], plate["feet"], stride=8)
        self.assertIsNotNone(rig)
        assert rig is not None
        rest = rig.points[rig.trail_index]
        posed = pose_points(rig, 12.0, 0.0)[rig.trail_index]
        self.assertGreater(float(np.hypot(posed[0] - rest[0], posed[1] - rest[1])), 3.0)
        rendered = render_pose(plate["image"], rig, 12.0, 0.0)
        opened = interior_hole_count(rendered[:, :, 3]) - plate["holes"]
        self.assertLessEqual(opened, hole_budget(plate["image"][:, :, 3]))


if __name__ == "__main__":
    unittest.main()
