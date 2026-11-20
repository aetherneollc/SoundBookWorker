from __future__ import annotations

import json
import math
import unittest
from pathlib import Path
from typing import Any


REPOSITORY = Path(__file__).resolve().parents[1]
MOTION_DIR = REPOSITORY / "godot" / "motions"
EXPECTED_NAMES = (
    "idle",
    "talk_calm",
    "talk_emphatic",
    "talk_explain",
    "listen",
    "nod",
    "point",
    "wave",
    "react",
    "walk_medium",
    "step_shift",
    "sit",
    "stand",
    "operate",
    "carry",
    "cross_arms",
    "angry",
    "sad",
    "surprised",
)
REQUIRED_FIELDS = {
    "name",
    "loop",
    "duration",
    "fps",
    "foot_lock",
    "tags",
    "keyframes",
}
LEGAL_BONES = {
    "root",
    "pelvis",
    "torso",
    "neck",
    "head",
    "upper_arm_l",
    "lower_arm_l",
    "hand_l",
    "upper_arm_r",
    "lower_arm_r",
    "hand_r",
    "upper_leg_l",
    "lower_leg_l",
    "foot_l",
    "upper_leg_r",
    "lower_leg_r",
    "foot_r",
}
# The first seven ranges are the M0 visual-gate limits.  Root is kept fixed,
# while hands and feet use conservative distal-joint limits for these clips.
ANGLE_LIMITS = {
    "root": (0.0, 0.0),
    "pelvis": (-14.0, 14.0),
    "torso": (-14.0, 14.0),
    "neck": (-16.0, 16.0),
    "head": (-16.0, 16.0),
    "upper_arm_l": (-52.0, 52.0),
    "upper_arm_r": (-52.0, 52.0),
    "lower_arm_l": (-88.0, 88.0),
    "lower_arm_r": (-88.0, 88.0),
    "hand_l": (-35.0, 35.0),
    "hand_r": (-35.0, 35.0),
    "upper_leg_l": (-35.0, 35.0),
    "upper_leg_r": (-35.0, 35.0),
    "lower_leg_l": (-60.0, 60.0),
    "lower_leg_r": (-60.0, 60.0),
    "foot_l": (-20.0, 20.0),
    "foot_r": (-20.0, 20.0),
}


class MotionClipTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.paths = {
            path.stem: path for path in MOTION_DIR.glob("*.json")
        }
        cls.motions: dict[str, dict[str, Any]] = {}
        for name, path in cls.paths.items():
            cls.motions[name] = json.loads(path.read_text(encoding="utf-8"))

    def test_all_nineteen_motion_files_exist(self) -> None:
        self.assertEqual(set(self.paths), set(EXPECTED_NAMES))

    def test_required_document_and_keyframe_fields(self) -> None:
        for name in EXPECTED_NAMES:
            with self.subTest(motion=name):
                motion = self.motions[name]
                self.assertTrue(REQUIRED_FIELDS.issubset(motion))
                self.assertEqual(motion["name"], name)
                self.assertIsInstance(motion["loop"], bool)
                self.assertIsInstance(motion["foot_lock"], bool)
                self.assertIsInstance(motion["fps"], int)
                self.assertGreater(motion["fps"], 0)
                self.assertGreater(float(motion["duration"]), 0.0)
                self.assertIsInstance(motion["tags"], list)
                self.assertTrue(motion["tags"])
                self.assertTrue(
                    all(isinstance(tag, str) and tag for tag in motion["tags"])
                )
                self.assertIsInstance(motion["keyframes"], list)
                self.assertGreaterEqual(len(motion["keyframes"]), 2)
                for index, frame in enumerate(motion["keyframes"]):
                    self.assertEqual(
                        set(frame), {"t", "root_offset", "bones"}, index
                    )
                    self.assertTrue(math.isfinite(float(frame["t"])))
                    self.assertEqual(len(frame["root_offset"]), 2)
                    self.assertTrue(
                        all(
                            math.isfinite(float(value))
                            and abs(float(value)) <= 0.12
                            for value in frame["root_offset"]
                        )
                    )
                    self.assertIsInstance(frame["bones"], dict)
                    self.assertTrue(frame["bones"])

    def test_bone_names_and_angles_are_safe(self) -> None:
        for motion_name in EXPECTED_NAMES:
            motion = self.motions[motion_name]
            for frame_index, frame in enumerate(motion["keyframes"]):
                for bone_name, raw_angle in frame["bones"].items():
                    with self.subTest(
                        motion=motion_name,
                        frame=frame_index,
                        bone=bone_name,
                    ):
                        self.assertIn(bone_name, LEGAL_BONES)
                        angle = float(raw_angle)
                        self.assertTrue(math.isfinite(angle))
                        lower, upper = ANGLE_LIMITS[bone_name]
                        self.assertGreaterEqual(angle, lower)
                        self.assertLessEqual(angle, upper)

    def test_keyframe_times_are_strictly_increasing(self) -> None:
        for name in EXPECTED_NAMES:
            with self.subTest(motion=name):
                motion = self.motions[name]
                times = [float(frame["t"]) for frame in motion["keyframes"]]
                self.assertAlmostEqual(times[0], 0.0)
                self.assertAlmostEqual(times[-1], float(motion["duration"]))
                self.assertTrue(
                    all(left < right for left, right in zip(times, times[1:]))
                )

    def test_looping_clips_close_exactly(self) -> None:
        for name in EXPECTED_NAMES:
            motion = self.motions[name]
            if not motion["loop"]:
                continue
            with self.subTest(motion=name):
                first = motion["keyframes"][0]
                last = motion["keyframes"][-1]
                self.assertEqual(first["root_offset"], last["root_offset"])
                self.assertEqual(first["bones"], last["bones"])

    def test_requested_loop_and_foot_lock_contracts(self) -> None:
        non_looping = {
            name for name, motion in self.motions.items() if not motion["loop"]
        }
        unlocked = {
            name
            for name, motion in self.motions.items()
            if not motion["foot_lock"]
        }
        self.assertEqual(non_looping, {"nod", "react", "stand", "surprised"})
        self.assertEqual(
            unlocked, {"walk_medium", "step_shift", "sit", "stand"}
        )
        self.assertEqual(float(self.motions["nod"]["duration"]), 1.0)
        self.assertEqual(float(self.motions["react"]["duration"]), 1.2)
        self.assertEqual(float(self.motions["stand"]["duration"]), 1.0)
        self.assertEqual(float(self.motions["surprised"]["duration"]), 1.1)

    def test_second_batch_pose_contracts(self) -> None:
        sitting = self.motions["sit"]
        self.assertTrue(sitting["loop"])
        self.assertFalse(sitting["foot_lock"])
        self.assertTrue(
            all(
                0.09 <= float(frame["root_offset"][1]) <= 0.11
                for frame in sitting["keyframes"]
            )
        )

        stand = self.motions["stand"]
        self.assertFalse(stand["loop"])
        self.assertFalse(stand["foot_lock"])
        self.assertGreaterEqual(float(stand["keyframes"][0]["root_offset"][1]), 0.09)
        self.assertEqual(float(stand["keyframes"][-1]["root_offset"][1]), 0.0)
        self.assertEqual(stand["keyframes"][-1]["bones"]["upper_arm_l"], -25.0)
        self.assertEqual(stand["keyframes"][-1]["bones"]["upper_arm_r"], 25.0)

        surprised = self.motions["surprised"]
        self.assertFalse(surprised["loop"])
        self.assertTrue(surprised["foot_lock"])
        self.assertLess(
            min(frame["bones"]["torso"] for frame in surprised["keyframes"]),
            0.0,
        )
        self.assertGreater(
            max(frame["bones"]["upper_arm_l"] for frame in surprised["keyframes"]),
            0.0,
        )
        self.assertLess(
            min(frame["bones"]["upper_arm_r"] for frame in surprised["keyframes"]),
            0.0,
        )

        for name in ("operate", "carry", "cross_arms", "angry", "sad"):
            motion = self.motions[name]
            self.assertTrue(motion["loop"], name)
            self.assertTrue(motion["foot_lock"], name)

    def test_folded_arm_clips_release_the_arm_guard(self) -> None:
        # The player's arm guard stops a forearm from swinging in past vertical.
        # A clip that folds the forearms across the body must switch it off, or
        # the pose flattens into idle (the Python preview has no such guard).
        released = {
            name
            for name, motion in self.motions.items()
            if motion.get("arm_guard", True) is False
        }
        self.assertEqual(
            released, {"operate", "carry", "cross_arms", "angry", "sit"}
        )
        for name in released:
            for frame in self.motions[name]["keyframes"]:
                bones = frame["bones"]
                self.assertLessEqual(bones["lower_arm_l"], -35.0, name)
                self.assertGreaterEqual(bones["lower_arm_r"], 35.0, name)
        for name, motion in self.motions.items():
            self.assertIsInstance(motion.get("arm_guard", True), bool, name)

    def test_screen_space_direction_conventions(self) -> None:
        idle = self.motions["idle"]["keyframes"][0]["bones"]
        self.assertLess(idle["upper_arm_l"], 0.0)
        self.assertGreater(idle["upper_arm_r"], 0.0)

        point_frames = self.motions["point"]["keyframes"]
        self.assertGreaterEqual(
            max(frame["bones"]["upper_arm_l"] for frame in point_frames),
            40.0,
        )

        for frame in self.motions["wave"]["keyframes"]:
            self.assertLess(frame["bones"]["upper_arm_r"], 0.0)
            self.assertLess(frame["bones"]["lower_arm_r"], 0.0)

    def test_locomotion_has_required_weight_shifts(self) -> None:
        walk_frames = self.motions["walk_medium"]["keyframes"]
        walk_y = [frame["root_offset"][1] for frame in walk_frames]
        upward_peaks = sum(
            walk_y[index] < walk_y[index - 1]
            and walk_y[index] < walk_y[index + 1]
            for index in range(1, len(walk_y) - 1)
        )
        self.assertEqual(upward_peaks, 2)

        step_frames = self.motions["step_shift"]["keyframes"]
        step_x = [frame["root_offset"][0] for frame in step_frames]
        self.assertGreater(max(step_x), 0.0)
        self.assertLess(min(step_x), 0.0)
        self.assertGreater(
            max(frame["bones"]["lower_leg_l"] for frame in step_frames),
            20.0,
        )
        self.assertLess(
            min(frame["bones"]["lower_leg_r"] for frame in step_frames),
            -20.0,
        )


if __name__ == "__main__":
    unittest.main()
