from __future__ import annotations

import unittest

import numpy as np

from tools.experiment_side_walk import (
    make_synthetic_side_sample,
    render_walk_cycle,
    separate_arm_and_torso,
)


class SideWalkExperimentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sample = make_synthetic_side_sample()
        self.layers = separate_arm_and_torso(
            self.sample.source_rgba,
            self.sample.arm_mask,
            self.sample.torso_mask,
            shoulder=self.sample.shoulder,
            hip=self.sample.hip,
            ground_truth_rgba=self.sample.ground_truth_rgba,
            sample_name=self.sample.name,
        )

    def test_layered_inpaint_runs_and_keeps_nonempty_layers(self) -> None:
        self.assertEqual(self.layers.body_rgba.shape, self.sample.source_rgba.shape)
        self.assertEqual(self.layers.arm_rgba.shape, self.sample.source_rgba.shape)
        self.assertGreater(int(np.count_nonzero(self.layers.fill_mask)), 0)
        self.assertGreater(int(np.count_nonzero(self.layers.body_rgba[:, :, 3])), 0)
        self.assertGreater(int(np.count_nonzero(self.layers.arm_rgba[:, :, 3])), 0)
        self.assertGreaterEqual(self.layers.cpu_ms, 0.0)
        self.assertIn("colour_mae_0_255", self.layers.metrics)

    def test_six_frame_walk_cycle_renders(self) -> None:
        frames = render_walk_cycle(self.layers)
        self.assertEqual(len(frames), 6)
        self.assertTrue(all(frame.shape == (420, 270, 3) for frame in frames))
        self.assertTrue(all(float(frame.std()) > 5.0 for frame in frames))


if __name__ == "__main__":
    unittest.main()
