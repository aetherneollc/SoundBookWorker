import unittest

import numpy as np

from renderer.calibrate_plate import calibrate_rgb, default_metrics


def _plate(fill=(186, 214, 230)) -> np.ndarray:
    rgb = np.empty((720, 1280, 3), dtype=np.uint8)
    rgb[:] = np.array(fill, dtype=np.uint8)
    return rgb


class CalibratePlateFurnitureTests(unittest.TestCase):
    def test_horizon_fields_stay_on_the_default_metrics(self):
        metrics = default_metrics()
        for key in ('horizon_y', 'wall_base_y', 'far_y', 'mid_y', 'near_y', 'floor_rgb', 'ambient_rgb'):
            self.assertIn(key, metrics)
        self.assertEqual(metrics['screens'], [])
        self.assertIsNone(metrics['cabinet_top_y'])
        self.assertEqual(metrics['cabinets'], [])

    def test_dark_wall_rectangles_and_cabinet_lip_are_measured(self):
        rgb = _plate()
        rgb[620:, :] = (230, 232, 234)
        # Two filled monitors on the back wall.
        rgb[230:330, 200:360] = (12, 18, 24)
        rgb[250:340, 860:1040] = (16, 22, 28)
        # Waist-high runs with a floor-colored gap between them (open bay).
        rgb[468:580, 80:520] = (70, 108, 118)
        rgb[468:580, 700:1200] = (70, 108, 118)
        rgb[468:620, 520:700] = (230, 232, 234)

        metrics = calibrate_rgb(rgb)

        self.assertIsInstance(metrics['horizon_y'], float)
        self.assertIsInstance(metrics['wall_base_y'], float)
        self.assertIsInstance(metrics['mid_y'], float)
        self.assertEqual(len(metrics['floor_rgb']), 3)
        self.assertGreaterEqual(len(metrics['screens']), 2)
        centers = sorted(screen['x'] + screen['w'] / 2 for screen in metrics['screens'])
        self.assertLess(centers[0], 0.4)
        self.assertGreater(centers[-1], 0.6)
        for screen in metrics['screens']:
            self.assertTrue({'x', 'y', 'w', 'h'} <= set(screen))
            self.assertGreater(screen['w'], 0.04)
            self.assertLess(screen['y'], 0.58)
        self.assertIsNotNone(metrics['cabinet_top_y'])
        self.assertGreater(metrics['cabinet_top_y'], 0.58)
        self.assertLess(metrics['cabinet_top_y'], 0.72)
        self.assertGreaterEqual(len(metrics['cabinets']), 2)
        spans = sorted(metrics['cabinets'], key=lambda cab: cab['x'])
        self.assertLess(spans[0]['x'] + spans[0]['w'], spans[-1]['x'])

    def test_a_bright_window_frame_is_not_a_screen_and_a_blank_wall_has_no_cabinet(self):
        window = _plate()
        window[620:, :] = (230, 232, 234)
        window[220:400, 400:780] = (8, 12, 16)
        window[232:388, 412:768] = (230, 240, 250)
        framed = calibrate_rgb(window)
        self.assertEqual(framed['screens'], [])
        self.assertIn('horizon_y', framed)

        blank = _plate((210, 214, 218))
        blank[620:, :] = (232, 232, 232)
        empty = calibrate_rgb(blank)
        self.assertEqual(empty['screens'], [])
        self.assertIsNone(empty['cabinet_top_y'])
        self.assertEqual(empty['cabinets'], [])
        self.assertIn('floor_rgb', empty)


if __name__ == '__main__':
    unittest.main()
