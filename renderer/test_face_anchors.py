from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image

from renderer.face_anchors import detect_face_anchors, draw_face_overlay


ROOT = Path(__file__).resolve().parents[1]
REAL_ASSETS = (
    ROOT / "manju/demo/cells/linmo-neutral.png",
    ROOT / "public/media/char-888c29d8aae865f7-head.png",
    ROOT / "public/media/char-819cfd7d269b490b-head.png",
)
FULL_BODY_ASSET = ROOT / "media/m0_real_gate/linlan_1011/texture.png"


def _synthetic_anime_face() -> np.ndarray:
    rgba = np.zeros((320, 320, 4), dtype=np.uint8)
    skin = (246, 205, 175, 255)
    ink = (26, 30, 39, 255)
    white = (250, 248, 239, 255)
    # Neck and ears deliberately share the face colour to exercise component
    # selection rather than a trivial isolated oval.
    cv2.rectangle(rgba, (137, 228), (183, 307), skin, -1)
    cv2.ellipse(rgba, (160, 157), (91, 112), 0, 0, 360, skin, -1, cv2.LINE_AA)
    cv2.ellipse(rgba, (69, 160), (12, 26), 0, 0, 360, skin, -1, cv2.LINE_AA)
    cv2.ellipse(rgba, (251, 160), (12, 26), 0, 0, 360, skin, -1, cv2.LINE_AA)

    # Hair overlaps the forehead and is much larger/darker than facial ink.
    hair = (30, 38, 53, 255)
    cv2.ellipse(rgba, (160, 75), (94, 68), 0, 180, 360, hair, -1, cv2.LINE_AA)
    cv2.fillConvexPoly(
        rgba,
        np.array([(67, 83), (87, 43), (233, 43), (254, 87), (223, 105), (202, 78), (180, 108), (151, 75), (119, 109), (96, 78)], np.int32),
        hair,
        cv2.LINE_AA,
    )

    # Brows, outlined white eyes, pupils, a small nose shadow, and a flat
    # neutral mouth mimic the colour organization of Kolors cel shading.
    cv2.line(rgba, (91, 118), (133, 112), ink, 5, cv2.LINE_AA)
    cv2.line(rgba, (187, 112), (229, 118), ink, 5, cv2.LINE_AA)
    cv2.ellipse(rgba, (112, 143), (23, 11), 0, 0, 360, ink, -1, cv2.LINE_AA)
    cv2.ellipse(rgba, (208, 143), (23, 11), 0, 0, 360, ink, -1, cv2.LINE_AA)
    cv2.ellipse(rgba, (112, 143), (19, 7), 0, 0, 360, white, -1, cv2.LINE_AA)
    cv2.ellipse(rgba, (208, 143), (19, 7), 0, 0, 360, white, -1, cv2.LINE_AA)
    cv2.circle(rgba, (112, 143), 6, ink, -1, cv2.LINE_AA)
    cv2.circle(rgba, (208, 143), 6, ink, -1, cv2.LINE_AA)
    cv2.line(rgba, (160, 157), (156, 182), (206, 149, 128, 255), 2, cv2.LINE_AA)
    cv2.line(rgba, (146, 216), (174, 216), (105, 49, 47, 255), 4, cv2.LINE_AA)
    return rgba


def _center(box: dict[str, int]) -> tuple[float, float]:
    return box["x"] + box["w"] / 2, box["y"] + box["h"] / 2


class FaceAnchorTests(unittest.TestCase):
    def test_full_body_crop_refines_eyes_and_complete_mouth(self):
        if not FULL_BODY_ASSET.is_file():
            self.skipTest("full-body Kolors regression asset is not present")
        with Image.open(FULL_BODY_ASSET) as image:
            rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
        anchors = detect_face_anchors(rgba)

        hx, hy, hw, hh = anchors["head_box"]
        left, right = _center(anchors["eye_left"]), _center(anchors["eye_right"])
        brow_left = _center(anchors["brow_left"])
        brow_right = _center(anchors["brow_right"])
        mouth = _center(anchors["mouth"])
        eye_y = (left[1] + right[1]) / 2
        eye_distance = right[0] - left[0]

        # A one-pass whole-body RTMW inference put these features around
        # y=313/y=404.  The head-context pass must reach the painted eyes and
        # smile lower down in the 3072px source.
        self.assertGreater(eye_y, hy + hh * 0.55)
        self.assertGreater(mouth[1], hy + hh * 0.76)
        self.assertGreater(left[1], brow_left[1])
        self.assertGreater(right[1], brow_right[1])
        self.assertGreater(anchors["mouth"]["w"], eye_distance * 0.45)
        self.assertLess(abs(mouth[0] - (left[0] + right[0]) / 2), eye_distance * 0.18)

    def test_skin_blob_fallback_finds_synthetic_anime_features(self):
        rgba = _synthetic_anime_face()
        original = rgba.copy()
        with patch("renderer.face_anchors._rtm_face_landmarks", return_value=None):
            anchors = detect_face_anchors(rgba)

        self.assertEqual(anchors["method"], "skin_blob")
        self.assertEqual(rgba.tolist(), original.tolist(), "detector must not mutate its input")
        self.assertLess(_center(anchors["eye_left"])[0], _center(anchors["eye_right"])[0])
        for key, target_x in (("eye_left", 112), ("eye_right", 208)):
            cx, cy = _center(anchors[key])
            self.assertLess(abs(cx - target_x), 20, key)
            self.assertLess(abs(cy - 143), 18, key)
        self.assertLess(_center(anchors["brow_left"])[1], _center(anchors["eye_left"])[1])
        self.assertLess(_center(anchors["brow_right"])[1], _center(anchors["eye_right"])[1])
        mouth_x, mouth_y = _center(anchors["mouth"])
        self.assertLess(abs(mouth_x - 160), 16)
        self.assertLess(abs(mouth_y - 216), 16)
        self.assertLess(max(abs(a - b) for a, b in zip(anchors["skin_rgb"], (246, 205, 175))), 14)
        self.assertLess(sum(anchors["ink_rgb"]) / 3, 85)
        self.assertGreater(anchors["confidence"], 0.45)

        expected = {
            "head_box",
            "face_box",
            "eye_left",
            "eye_right",
            "brow_left",
            "brow_right",
            "mouth",
            "skin_rgb",
            "ink_rgb",
            "method",
            "confidence",
        }
        self.assertEqual(set(anchors), expected)

    def test_overlay_contains_annotation_and_patch_panels(self):
        rgba = _synthetic_anime_face()
        with patch("renderer.face_anchors._rtm_face_landmarks", return_value=None):
            anchors = detect_face_anchors(rgba, head_box=(45, 20, 230, 275))
        overlay = draw_face_overlay(rgba, anchors)
        self.assertEqual(overlay.shape[0], rgba.shape[0])
        self.assertGreaterEqual(overlay.shape[1], rgba.shape[1] * 2)
        self.assertLessEqual(overlay.shape[1], rgba.shape[1] * 2 + 5)
        self.assertEqual(overlay.shape[2], 4)
        # The right panel must actually replace pixels in the mouth area.
        mx, my = _center(anchors["mouth"])
        patched_x = rgba.shape[1] + (overlay.shape[1] - 2 * rgba.shape[1]) + int(mx)
        self.assertEqual(
            overlay[int(my), patched_x, :3].tolist(),
            anchors["skin_rgb"],
        )

    def test_real_kolors_asset_regression(self):
        path = next((candidate for candidate in REAL_ASSETS if candidate.is_file()), None)
        if path is None:
            self.skipTest("no real Kolors probe asset is present")
        with Image.open(path) as image:
            rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
        anchors = detect_face_anchors(rgba)
        height, width = rgba.shape[:2]

        self.assertIn(anchors["method"], {"rtmlib_refined", "skin_blob"})
        self.assertGreater(anchors["confidence"], 0.40)
        for key in ("eye_left", "eye_right", "brow_left", "brow_right", "mouth"):
            box = anchors[key]
            self.assertGreater(box["w"], 0, key)
            self.assertGreater(box["h"], 0, key)
            self.assertGreaterEqual(box["x"], 0, key)
            self.assertGreaterEqual(box["y"], 0, key)
            self.assertLessEqual(box["x"] + box["w"], width, key)
            self.assertLessEqual(box["y"] + box["h"], height, key)
        left, right = _center(anchors["eye_left"]), _center(anchors["eye_right"])
        mouth = _center(anchors["mouth"])
        eye_distance = max(1.0, right[0] - left[0])
        eye_y = (left[1] + right[1]) / 2
        self.assertLess(left[0], right[0])
        self.assertGreater(mouth[1], eye_y)
        self.assertGreater((mouth[1] - eye_y) / eye_distance, 0.55)
        self.assertLess((mouth[1] - eye_y) / eye_distance, 1.65)
        self.assertGreaterEqual(anchors["skin_rgb"][0], anchors["skin_rgb"][1] - 5)
        self.assertGreater(
            sum(anchors["skin_rgb"]) / 3 - sum(anchors["ink_rgb"]) / 3,
            45,
        )

    def test_rejects_tiny_or_malformed_input(self):
        with self.assertRaises(ValueError):
            detect_face_anchors(np.zeros((12, 12, 4), dtype=np.uint8))
        with self.assertRaises(ValueError):
            detect_face_anchors(np.zeros((64, 64), dtype=np.uint8))


if __name__ == "__main__":
    unittest.main()
