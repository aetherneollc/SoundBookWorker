"""Unit tests for renderer/scene_layout.py (SceneLayoutSystem)."""
from __future__ import annotations

import unittest
from pathlib import Path

from renderer.scene_layout import (
    DEFAULT_KENNEY_DIR,
    DEFAULT_WALL_CLEARANCE,
    SUPPORTED_SCENE_TYPES,
    ObstacleAABB,
    PlacedProp,
    SceneLayoutSystem,
)


class SceneLayoutSystemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.system = SceneLayoutSystem()

    def test_all_four_scene_types_pass_full_validation(self) -> None:
        self.assertEqual(
            set(self.system.supported_scene_types()),
            {"observation_bay", "corridor", "med_lab", "command_deck"},
        )
        for scene_type in SUPPORTED_SCENE_TYPES:
            with self.subTest(scene_type=scene_type):
                layout = self.system.solve(scene_type, validate=True)
                report = layout.validate(min_walkway_m=1.0)
                self.assertTrue(report.valid, f"{scene_type}: {report.issues}")
                self.assertEqual(report.overlapping_pairs, [])
                self.assertEqual(report.doorway_violations, [])
                self.assertEqual(report.out_of_bounds_props, [])
                self.assertEqual(report.unreachable_anchors, [])
                self.assertEqual(report.invalid_anchors, [])
                self.assertGreaterEqual(report.min_walkway_width_m, 0.75)

    def test_required_semantic_zones_and_anchors_exist(self) -> None:
        expected_zones = {
            "observation_bay": {"entry", "workstation", "window_view", "center_stage", "rover_bay"},
            "corridor": {"entry", "center_stage", "workstation", "window_view"},
            "med_lab": {"entry", "exam_zone", "workstation", "center_stage"},
            "command_deck": {"entry", "command_table", "workstation", "window_view", "center_stage"},
        }
        for scene_type, required in expected_zones.items():
            with self.subTest(scene_type=scene_type):
                layout = self.system.solve(scene_type)
                self.assertTrue(required.issubset(set(layout.zones.keys())))
                self.assertIn("entry_threshold", layout.standing_anchors)
                self.assertGreaterEqual(len(layout.standing_anchors), 5)
                self.assertGreaterEqual(len(layout.surface_anchors), 2)
                self.assertGreaterEqual(len(layout.camera_regions), 3)

                # Every standing anchor must be reachable from entry via a >= 1.0m wide lane.
                entry = layout.standing_anchors["entry_threshold"]
                for aid, anchor in layout.standing_anchors.items():
                    if aid == "entry_threshold":
                        continue
                    path = layout.find_path(
                        (entry.position[0], entry.position[2]),
                        (anchor.position[0], anchor.position[2]),
                        clearance_radius=0.50,
                    )
                    self.assertIsNotNone(path, f"{scene_type}: no >=1.0m path from entry to {aid}")

    def test_surface_anchors_have_exact_tabletop_y(self) -> None:
        bay = self.system.solve("observation_bay")
        self.assertIn("console_main_top", bay.surface_anchors)
        console_surf = bay.surface_anchors["console_main_top"]
        # desk_computer.glb unscaled height 0.375 * scale 2.6 = 0.975 m
        self.assertAlmostEqual(console_surf.surface_y, 0.975, places=3)
        self.assertAlmostEqual(console_surf.position[1], console_surf.surface_y, places=4)

        med = self.system.solve("med_lab")
        self.assertAlmostEqual(med.surface_anchors["exam_bed_main_top"].surface_y, 0.88, places=3)
        self.assertAlmostEqual(med.surface_anchors["lab_bench_main_top"].surface_y, 0.90, places=3)

        deck = self.system.solve("command_deck")
        self.assertAlmostEqual(deck.surface_anchors["command_table_main_top"].surface_y, 0.95, places=3)

    def test_stage3d_set_export_contains_complete_schema(self) -> None:
        for scene_type in SUPPORTED_SCENE_TYPES:
            with self.subTest(scene_type=scene_type):
                layout = self.system.solve(scene_type)
                stage_set = layout.to_stage3d_set(resolve_asset_paths=True)
                self.assertEqual(stage_set["scene_type"], scene_type)
                self.assertIn("room", stage_set)
                self.assertIn("door", stage_set)
                self.assertIn("doors", stage_set)
                self.assertIn("camera_bounds", stage_set)
                self.assertIn("lighting_profile", stage_set)
                self.assertIn("props", stage_set)
                self.assertIn("zones", stage_set)
                self.assertIn("obstacles", stage_set)
                self.assertIn("standing_anchors", stage_set)
                self.assertIn("surface_anchors", stage_set)
                self.assertIn("camera_regions", stage_set)
                # Verify any referenced Kenney GLB model exists on disk when asset dir is present.
                if DEFAULT_KENNEY_DIR.is_dir():
                    for prop in stage_set["props"]:
                        if "model" in prop:
                            self.assertTrue(Path(prop["model"]).is_file(), f"Missing Kenney model: {prop['model']}")

    def test_camera_regions_respect_wall_clearance(self) -> None:
        for scene_type in SUPPORTED_SCENE_TYPES:
            layout = self.system.solve(scene_type)
            for region in layout.camera_regions:
                self.assertGreaterEqual(region.min_xyz[0], layout.room_min[0] + DEFAULT_WALL_CLEARANCE - 1e-4)
                self.assertLessEqual(region.max_xyz[0], layout.room_max[0] - DEFAULT_WALL_CLEARANCE + 1e-4)
                self.assertGreaterEqual(region.min_xyz[2], layout.room_min[2] + DEFAULT_WALL_CLEARANCE - 1e-4)
                self.assertLessEqual(region.max_xyz[2], layout.room_max[2] - DEFAULT_WALL_CLEARANCE + 1e-4)

    def test_parametric_custom_room_size_and_lighting_override(self) -> None:
        layout = self.system.solve(
            "command_deck",
            room_size=(16.0, 4.0, 12.0),
            lighting_profile="night_alert",
        )
        self.assertEqual(layout.lighting_profile, "night_alert")
        self.assertEqual(layout.room_min[0], -8.0)
        self.assertEqual(layout.room_max[0], 8.0)
        self.assertTrue(layout.validate().valid)

    def test_validator_catches_overlap_doorway_block_and_narrow_walkway(self) -> None:
        layout = self.system.solve("med_lab")
        # Inject an overlapping obstacle on top of exam_bed_main.
        bed_obs = next(o for o in layout.obstacles if o.id == "exam_bed_main")
        layout.obstacles.append(
            ObstacleAABB(
                "bad_overlap",
                "crate",
                (bed_obs.min_xyz[0] + 0.1, 0.0, bed_obs.min_xyz[2] + 0.1),
                (bed_obs.min_xyz[0] + 0.8, 0.8, bed_obs.min_xyz[2] + 0.8),
            )
        )
        # Inject a doorway-blocking obstacle right inside the back door apron.
        layout.obstacles.append(
            ObstacleAABB(
                "door_blocker",
                "crate",
                (-0.4, 0.0, layout.room_max[2] - 1.0),
                (0.4, 0.9, layout.room_max[2] - 0.2),
            )
        )
        report = layout.validate()
        self.assertFalse(report.valid)
        self.assertTrue(any("bad_overlap" in pair for pair in report.overlapping_pairs))
        self.assertTrue(any("door_blocker" in v for v in report.doorway_violations))

    def test_validator_catches_blocked_corridor_walkway(self) -> None:
        layout = self.system.solve("corridor")
        # Place a barricade wall spanning across the middle of the corridor so north anchor is unreachable.
        layout.obstacles.append(
            ObstacleAABB("barricade", "crate", (-2.2, 0.0, -0.5), (2.2, 1.2, 0.1))
        )
        report = layout.validate(min_walkway_m=1.0)
        self.assertFalse(report.valid)
        self.assertIn("corridor_north", report.unreachable_anchors)

    def test_extra_prop_validation_rejects_overlap(self) -> None:
        with self.assertRaises(ValueError):
            self.system.solve(
                "command_deck",
                extra_props=[{"id": "bad_crate", "kind": "crate", "pos": [0.0, -0.3], "size": [1.0, 1.0, 1.0]}],
            )

    def test_unknown_scene_type_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.system.solve("unknown_room")


if __name__ == "__main__":
    unittest.main()
