from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from renderer.preview_rig import (
    STANDARD_POSES,
    forward_kinematics,
    linear_blend_skin,
    preview_rig,
)
from renderer.rig_mesh import build_mesh_rig


EXPECTED_PARENTS = {
    "root": None,
    "pelvis": "root",
    "torso": "pelvis",
    "neck": "torso",
    "head": "neck",
    "upper_arm_l": "torso",
    "lower_arm_l": "upper_arm_l",
    "hand_l": "lower_arm_l",
    "upper_arm_r": "torso",
    "lower_arm_r": "upper_arm_r",
    "hand_r": "lower_arm_r",
    "upper_leg_l": "pelvis",
    "lower_leg_l": "upper_leg_l",
    "foot_l": "lower_leg_l",
    "upper_leg_r": "pelvis",
    "lower_leg_r": "upper_leg_r",
    "foot_r": "lower_leg_r",
}


def _line(
    image: np.ndarray,
    points: list[tuple[int, int]],
    fill: tuple[int, int, int],
    width: int,
) -> None:
    contour = np.asarray(points, dtype=np.int32)
    cv2.polylines(
        image,
        [contour],
        False,
        (31, 28, 40),
        width + 10,
        lineType=cv2.LINE_AA,
    )
    cv2.polylines(
        image, [contour], False, fill, width, lineType=cv2.LINE_AA
    )


def _make_synthetic_character(path: Path) -> None:
    """Draw a connected, outlined A-pose character on a pure green screen."""
    image = np.full((520, 360, 3), (0, 255, 0), dtype=np.uint8)
    ink = (31, 28, 40)
    skin = (244, 202, 181)
    jacket = (58, 105, 207)
    sleeve = (77, 128, 226)
    trousers = (52, 58, 92)
    boot = (62, 45, 55)

    # Back-to-front drawing keeps every limb joined at the shoulder/hip while
    # preserving large transparent armpit and crotch gaps.
    _line(image, [(128, 151), (78, 188), (28, 218)], sleeve, 25)
    _line(image, [(232, 151), (282, 188), (332, 218)], sleeve, 25)
    cv2.circle(image, (24, 221), 18, ink, -1, lineType=cv2.LINE_AA)
    cv2.circle(image, (24, 221), 12, skin, -1, lineType=cv2.LINE_AA)
    cv2.circle(image, (336, 221), 18, ink, -1, lineType=cv2.LINE_AA)
    cv2.circle(image, (336, 221), 12, skin, -1, lineType=cv2.LINE_AA)

    _line(image, [(154, 279), (145, 380), (133, 474)], trousers, 34)
    _line(image, [(206, 279), (215, 380), (227, 474)], trousers, 34)
    _line(image, [(134, 474), (105, 490)], boot, 25)
    _line(image, [(226, 474), (255, 490)], boot, 25)

    torso = np.asarray(
        [(127, 141), (233, 141), (222, 282), (138, 282)],
        dtype=np.int32,
    )
    cv2.fillConvexPoly(image, torso, ink, lineType=cv2.LINE_AA)
    inner_torso = np.asarray(
        [(135, 150), (225, 150), (214, 275), (146, 275)],
        dtype=np.int32,
    )
    cv2.fillConvexPoly(image, inner_torso, jacket, lineType=cv2.LINE_AA)
    cv2.rectangle(image, (164, 116), (196, 153), ink, -1)
    cv2.rectangle(image, (170, 116), (190, 153), skin, -1)

    cv2.ellipse(image, (180, 72), (48, 58), 0, 0, 360, ink, -1, cv2.LINE_AA)
    cv2.ellipse(
        image, (180, 74), (40, 50), 0, 0, 360, skin, -1, cv2.LINE_AA
    )
    # Graphic anime hair and face details.
    hair = np.asarray(
        [(140, 70), (144, 32), (180, 15), (216, 32), (220, 67),
         (204, 55), (193, 76), (181, 55), (167, 76), (155, 55)],
        dtype=np.int32,
    )
    cv2.fillPoly(image, [hair], (48, 52, 81), lineType=cv2.LINE_AA)
    cv2.ellipse(image, (164, 82), (7, 10), 0, 0, 360, ink, -1, cv2.LINE_AA)
    cv2.ellipse(image, (196, 82), (7, 10), 0, 0, 360, ink, -1, cv2.LINE_AA)
    cv2.circle(image, (166, 79), 2, (244, 248, 255), -1)
    cv2.circle(image, (198, 79), 2, (244, 248, 255), -1)
    cv2.ellipse(image, (180, 102), (9, 4), 0, 0, 180, (147, 54, 73), 2)

    # A cel-shaded panel helps verify that affine texture mapping is visible.
    cv2.rectangle(image, (151, 173), (209, 190), (238, 164, 55), -1)
    cv2.line(image, (151, 192), (209, 192), ink, 4, cv2.LINE_AA)
    Image.fromarray(image, "RGB").save(path)


def _make_floor_character(path: Path) -> None:
    """Add a muted green backdrop, pale floor, clouds, and a watermark."""
    base_path = path.with_name(path.stem + "_base.png")
    _make_synthetic_character(base_path)
    base = np.asarray(Image.open(base_path).convert("RGB"))
    base_path.unlink()
    h, w = base.shape[:2]
    yy = np.arange(h, dtype=np.float32).reshape(-1, 1)
    background = np.empty_like(base)
    background[:, :, 0] = np.clip(112.0 + yy * 0.025, 0, 255)
    background[:, :, 1] = np.clip(168.0 + yy * 0.018, 0, 255)
    background[:, :, 2] = np.clip(126.0 + yy * 0.022, 0, 255)
    cv2.ellipse(
        background, (52, 78), (72, 30), 0, 0, 360, (154, 184, 158), -1
    )
    cv2.ellipse(
        background, (310, 112), (64, 38), 0, 0, 360, (142, 177, 150), -1
    )
    background[442:, :] = (190, 211, 188)
    cv2.line(background, (0, 460), (359, 454), (142, 174, 151), 2)
    cv2.ellipse(
        background, (180, 491), (118, 11), 0, 0, 360, (102, 145, 111), -1
    )

    r, g, b = (
        base[:, :, channel].astype(np.int16) for channel in range(3)
    )
    old_green = (g > r + 48) & (g > b + 48)
    image = background
    image[~old_green] = base[~old_green]
    # Enclosed white fabric must survive the broad pale-background candidate.
    cv2.rectangle(image, (160, 198), (200, 251), (31, 28, 40), -1)
    cv2.rectangle(image, (166, 204), (194, 245), (242, 242, 236), -1)
    cv2.putText(
        image,
        "AI",
        (314, 507),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.38,
        (145, 166, 149),
        1,
        cv2.LINE_AA,
    )
    Image.fromarray(image, "RGB").save(path)


class MeshRigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._temporary.cleanup)
        cls.work = Path(cls._temporary.name)
        cls.master_path = cls.work / "synthetic_a_pose.png"
        cls.rig_dir = cls.work / "rig"
        cls.floor_master_path = cls.work / "synthetic_floor.png"
        cls.floor_rig_dir = cls.work / "floor_rig"
        _make_synthetic_character(cls.master_path)
        _make_floor_character(cls.floor_master_path)

        old_skip = os.environ.get("SOUNDBOOK_SKIP_RTMLIB")
        old_face_skip = os.environ.get("FACE_ANCHORS_DISABLE_RTMLIB")
        os.environ["SOUNDBOOK_SKIP_RTMLIB"] = "1"
        os.environ["FACE_ANCHORS_DISABLE_RTMLIB"] = "1"
        try:
            cls.rig_path = build_mesh_rig(
                cls.master_path, cls.rig_dir, upscale=2.0
            )
            cls.floor_rig_path = build_mesh_rig(
                cls.floor_master_path, cls.floor_rig_dir, upscale=2.0
            )
        finally:
            if old_skip is None:
                os.environ.pop("SOUNDBOOK_SKIP_RTMLIB", None)
            else:
                os.environ["SOUNDBOOK_SKIP_RTMLIB"] = old_skip
            if old_face_skip is None:
                os.environ.pop("FACE_ANCHORS_DISABLE_RTMLIB", None)
            else:
                os.environ["FACE_ANCHORS_DISABLE_RTMLIB"] = old_face_skip
        cls.rig = json.loads(cls.rig_path.read_text(encoding="utf-8"))
        cls.floor_rig = json.loads(
            cls.floor_rig_path.read_text(encoding="utf-8")
        )

        # Keep one deterministic artifact in the requested gitignored probe dir.
        repository = Path(__file__).resolve().parents[1]
        cls.probe_path = (
            repository / "media" / "rig_mesh_probe" / "synthetic_preview.png"
        )
        preview_rig(cls.rig_path, cls.probe_path, skeleton=True)

    def test_common_bone_topology(self) -> None:
        bones = {bone["name"]: bone for bone in self.rig["bones"]}
        # The requested list contains one scene root plus 16 deform bones.
        self.assertEqual(len(bones) - 1, 16)
        self.assertEqual(set(bones), set(EXPECTED_PARENTS))
        for name, parent in EXPECTED_PARENTS.items():
            self.assertEqual(bones[name]["parent"], parent, name)
            self.assertEqual(len(bones[name]["head"]), 2)
            self.assertEqual(len(bones[name]["tail"]), 2)
            self.assertGreaterEqual(float(bones[name]["length"]), 0.0)

    def test_every_vertex_has_normalized_limited_weights(self) -> None:
        for group in self.rig["groups"]:
            vertex_count = len(group["vertices"])
            for values in group["weights"].values():
                self.assertEqual(len(values), vertex_count)
            for index in range(vertex_count):
                influences = [
                    float(values[index])
                    for values in group["weights"].values()
                    if float(values[index]) > 1e-8
                ]
                self.assertLessEqual(len(influences), 4)
                self.assertAlmostEqual(sum(influences), 1.0, places=5)

    def test_no_armpit_or_crotch_bridge_triangles(self) -> None:
        texture = np.asarray(
            Image.open(self.rig_dir / self.rig["texture"]).convert("RGBA")
        )
        foreground = (texture[:, :, 3] >= 32).astype(np.uint8)
        expanded = cv2.dilate(
            foreground,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13)),
            iterations=1,
        )
        expanded = cv2.dilate(
            expanded,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
            iterations=1,
        )

        def inside(point: np.ndarray) -> bool:
            x = int(np.clip(round(float(point[0])), 0, expanded.shape[1] - 1))
            y = int(np.clip(round(float(point[1])), 0, expanded.shape[0] - 1))
            return bool(expanded[y, x])

        for group in self.rig["groups"]:
            vertices = np.asarray(group["vertices"], dtype=np.float32)
            for triangle_indices in group["triangles"]:
                triangle = vertices[np.asarray(triangle_indices)]
                checks = [
                    triangle.mean(axis=0),
                ]
                for edge in range(3):
                    start = triangle[edge]
                    end = triangle[(edge + 1) % 3]
                    checks.extend(
                        (
                            0.75 * start + 0.25 * end,
                            0.5 * (start + end),
                            0.25 * start + 0.75 * end,
                        )
                    )
                self.assertTrue(all(inside(point) for point in checks))
                edge_lengths = [
                    float(
                        np.linalg.norm(
                            triangle[(edge + 1) % 3] - triangle[edge]
                        )
                    )
                    for edge in range(3)
                ]
                spacing = max(
                    9.0, round(float(self.rig["body_height"]) / 42.0)
                )
                self.assertLessEqual(max(edge_lengths), max(24.0, spacing * 2.2) + 0.1)

        bones = {bone["name"]: bone for bone in self.rig["bones"]}
        body_height = float(self.rig["body_height"])
        groups = {group["name"]: group for group in self.rig["groups"]}
        for side in ("l", "r"):
            group = groups[f"arm_{side}"]
            shoulder_x = float(bones[f"upper_arm_{side}"]["head"][0])
            for index, vertex in enumerate(group["vertices"]):
                is_outer = (
                    float(vertex[0]) < shoulder_x - body_height * 0.10
                    if side == "l"
                    else float(vertex[0]) > shoulder_x + body_height * 0.10
                )
                if not is_outer:
                    continue
                self.assertEqual(
                    float(group["weights"].get("torso", [0.0] * len(group["vertices"]))[index]),
                    0.0,
                )
                opposite = "r" if side == "l" else "l"
                for forbidden in (
                    f"upper_arm_{opposite}",
                    f"lower_arm_{opposite}",
                    f"hand_{opposite}",
                    "upper_leg_l",
                    "upper_leg_r",
                ):
                    self.assertEqual(
                        float(
                            group["weights"].get(
                                forbidden, [0.0] * len(group["vertices"])
                            )[index]
                        ),
                        0.0,
                    )

        legs = groups["legs"]
        center_x = float(self.rig["center_x"])
        hip_y = float(bones["pelvis"]["head"][1])
        for index, vertex in enumerate(legs["vertices"]):
            if float(vertex[1]) < hip_y + body_height * 0.12:
                continue
            side = "l" if float(vertex[0]) < center_x else "r"
            opposite = "r" if side == "l" else "l"
            for forbidden in (
                f"upper_leg_{opposite}",
                f"lower_leg_{opposite}",
                f"foot_{opposite}",
            ):
                self.assertEqual(
                    float(
                        legs["weights"].get(
                            forbidden, [0.0] * len(legs["vertices"])
                        )[index]
                    ),
                    0.0,
                )

    def test_five_draw_groups_are_populated(self) -> None:
        groups = {group["name"]: group for group in self.rig["groups"]}
        self.assertEqual(
            set(groups), {"legs", "torso", "head", "arm_l", "arm_r"}
        )
        self.assertEqual(
            [groups[name]["z_index"] for name in groups],
            [0, 1, 2, 3, 4],
        )
        for name, group in groups.items():
            self.assertGreater(len(group["vertices"]), 2, name)
            self.assertGreater(len(group["triangles"]), 0, name)

    def test_shoulders_have_torso_overlap_backing(self) -> None:
        bones = {bone["name"]: bone for bone in self.rig["bones"]}
        groups = {group["name"]: group for group in self.rig["groups"]}

        def triangle_map(group: dict) -> dict[tuple, list[int]]:
            uvs = np.asarray(group["uvs"], dtype=np.float32)
            result: dict[tuple, list[int]] = {}
            for triangle_index, triangle in enumerate(group["triangles"]):
                points = uvs[np.asarray(triangle)]
                key = tuple(
                    sorted(
                        (round(float(x), 3), round(float(y), 3))
                        for x, y in points
                    )
                )
                result.setdefault(key, []).append(triangle_index)
            return result

        torso = groups["torso"]
        torso_triangles = triangle_map(torso)
        for side in ("l", "r"):
            arm = groups[f"arm_{side}"]
            arm_triangles = triangle_map(arm)
            shoulder = np.asarray(
                bones[f"upper_arm_{side}"]["head"], dtype=np.float32
            )
            radius = float(bones[f"upper_arm_{side}"]["length"]) * 0.28
            overlap_keys = [
                key
                for key in set(torso_triangles) & set(arm_triangles)
                if float(
                    np.linalg.norm(
                        np.asarray(key, dtype=np.float32).mean(axis=0)
                        - shoulder
                    )
                )
                <= radius + 0.5
            ]
            self.assertGreater(len(overlap_keys), 0, side)

            pure_backing = False
            smooth_arm_transition = False
            for key in overlap_keys:
                for triangle_index in torso_triangles[key]:
                    indices = torso["triangles"][triangle_index]
                    torso_values = torso["weights"].get(
                        "torso", [0.0] * len(torso["vertices"])
                    )
                    if all(abs(float(torso_values[index]) - 1.0) < 1e-6 for index in indices):
                        if all(
                            all(abs(float(values[index])) < 1e-6 for index in indices)
                            for bone, values in torso["weights"].items()
                            if bone != "torso"
                        ):
                            pure_backing = True
                for triangle_index in arm_triangles[key]:
                    indices = arm["triangles"][triangle_index]
                    torso_values = arm["weights"].get(
                        "torso", [0.0] * len(arm["vertices"])
                    )
                    upper_values = arm["weights"].get(
                        f"upper_arm_{side}", [0.0] * len(arm["vertices"])
                    )
                    if any(
                        float(torso_values[index]) > 0.0
                        and float(upper_values[index]) > 0.0
                        for index in indices
                    ):
                        smooth_arm_transition = True
            self.assertTrue(pure_backing, side)
            self.assertTrue(smooth_arm_transition, side)

    def test_opaque_pixels_have_mesh_coverage(self) -> None:
        texture = np.asarray(
            Image.open(self.rig_dir / self.rig["texture"]).convert("RGBA")
        )
        covered = np.zeros(texture.shape[:2], dtype=np.uint8)
        for group in self.rig["groups"]:
            vertices = np.asarray(group["vertices"], dtype=np.float32)
            for triangle in group["triangles"]:
                cv2.fillConvexPoly(
                    covered,
                    np.round(vertices[np.asarray(triangle)] * 16.0).astype(
                        np.int32
                    ),
                    1,
                    lineType=cv2.LINE_8,
                    shift=4,
                )
        opaque = texture[:, :, 3] >= 128
        self.assertTrue(bool(opaque.any()))
        coverage = float((covered[opaque] > 0).mean())
        self.assertGreaterEqual(coverage, 0.99)

    def test_boundary_flood_removes_floor_clouds_and_watermark(self) -> None:
        texture = np.asarray(
            Image.open(
                self.floor_rig_dir / self.floor_rig["texture"]
            ).convert("RGBA")
        )
        alpha = texture[:, :, 3]
        h, w = alpha.shape
        self.assertEqual(int(np.count_nonzero(alpha[-12:, :])), 0)
        lower_sides = np.concatenate(
            (
                alpha[int(h * 0.84) :, : int(w * 0.18)].ravel(),
                alpha[int(h * 0.84) :, int(w * 0.82) :].ravel(),
            )
        )
        self.assertLess(
            int(np.count_nonzero(lower_sides >= 32)),
            max(4, int(lower_sides.size * 0.001)),
        )
        # The enclosed white chest panel remains opaque.
        self.assertGreater(
            int(alpha[int(204 * 2.0) : int(245 * 2.0),
                      int(166 * 2.0) : int(194 * 2.0)].mean()),
            240,
        )
        components, _ = cv2.connectedComponents((alpha >= 32).astype(np.uint8))
        self.assertEqual(components - 1, 1)

    def test_arm_raise_keeps_inner_chest_locked(self) -> None:
        bones = {bone["name"]: bone for bone in self.rig["bones"]}
        left_shoulder = np.asarray(bones["upper_arm_l"]["head"])
        right_shoulder = np.asarray(bones["upper_arm_r"]["head"])
        hip_y = float(bones["pelvis"]["head"][1])
        body_height = float(self.rig["body_height"])
        x0 = float(left_shoulder[0] + body_height * 0.022)
        x1 = float(right_shoulder[0] - body_height * 0.022)
        y0 = float(max(left_shoulder[1], right_shoulder[1]) + body_height * 0.035)
        y1 = float(hip_y - body_height * 0.075)
        rest_state = forward_kinematics(self.rig["bones"])
        raised_state = forward_kinematics(
            self.rig["bones"],
            {"upper_arm_l": 60.0, "upper_arm_r": -60.0},
        )
        checked = 0
        for group in self.rig["groups"]:
            vertices = np.asarray(group["vertices"], dtype=np.float32)
            chest = (
                (vertices[:, 0] >= x0)
                & (vertices[:, 0] <= x1)
                & (vertices[:, 1] >= y0)
                & (vertices[:, 1] <= y1)
            )
            if not bool(chest.any()):
                continue
            rest = linear_blend_skin(vertices, group["weights"], rest_state)
            raised = linear_blend_skin(vertices, group["weights"], raised_state)
            displacement = np.linalg.norm(raised[chest] - rest[chest], axis=1)
            self.assertLess(float(displacement.max()), 1e-3)
            for side in ("l", "r"):
                arm_weight = sum(
                    np.asarray(
                        group["weights"].get(name, [0.0] * len(vertices)),
                        dtype=np.float32,
                    )
                    for name in (
                        f"upper_arm_{side}",
                        f"lower_arm_{side}",
                        f"hand_{side}",
                    )
                )
                self.assertEqual(float(arm_weight[chest].max()), 0.0)
            checked += int(chest.sum())
        self.assertGreater(checked, 8)

    def test_no_distal_arm_to_body_weight_bridge(self) -> None:
        body_names = (
            "torso",
            "pelvis",
            "upper_leg_l",
            "lower_leg_l",
            "foot_l",
            "upper_leg_r",
            "lower_leg_r",
            "foot_r",
        )
        for group in self.rig["groups"]:
            count = len(group["vertices"])
            for triangle in group["triangles"]:
                for side in ("l", "r"):
                    distal = [
                        sum(
                            group["weights"].get(name, [0.0] * count)[index]
                            for name in (f"lower_arm_{side}", f"hand_{side}")
                        )
                        for index in triangle
                    ]
                    body = [
                        sum(
                            group["weights"].get(name, [0.0] * count)[index]
                            for name in body_names
                        )
                        for index in triangle
                    ]
                    self.assertFalse(max(distal) >= 0.70 and max(body) >= 0.72)

    def test_six_pose_preview_is_nonempty(self) -> None:
        self.assertEqual(len(STANDARD_POSES), 6)
        self.assertTrue(self.probe_path.is_file())
        preview = np.asarray(Image.open(self.probe_path).convert("RGB"))
        self.assertGreater(preview.shape[1], preview.shape[0])
        self.assertGreater(float(preview.std()), 8.0)
        dark_pixels = np.all(preview < 100, axis=2)
        self.assertGreater(int(dark_pixels.sum()), 500)

    def test_required_rig_fields(self) -> None:
        self.assertEqual(self.rig["version"], 1)
        self.assertEqual(self.rig["texture"], "texture.png")
        self.assertEqual(len(self.rig["texture_size"]), 2)
        self.assertGreater(float(self.rig["feet_y"]), 0)
        self.assertGreater(float(self.rig["body_height"]), 0)
        self.assertGreater(float(self.rig["center_x"]), 0)
        self.assertIn("face_anchors", self.rig)


if __name__ == "__main__":
    unittest.main()
