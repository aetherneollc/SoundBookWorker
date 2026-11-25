#!/usr/bin/env python3
"""Unit tests for tools/build_humanoid_rig.py and tools/prepare_rover_wheels.py."""
from __future__ import annotations

import io
import json
import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from tools.build_humanoid_rig import (
    BLEND_SHAPE_NAMES,
    DEFAULT_CAST_RECIPES,
    build_humanoid_rig,
    read_accessor,
)
from tools.prepare_rover_wheels import WHEEL_NAMES, prepare_rover_wheels


def _read_glb(path: Path) -> tuple[dict, bytes]:
    data = path.read_bytes()
    assert data[:4] == b"glTF", "missing glTF magic"
    off = 12
    doc = None
    blob = b""
    while off + 8 <= len(data):
        clen, ctype = struct.unpack_from("<II", data, off)
        off += 8
        payload = data[off : off + clen]
        off += clen
        if ctype == 0x4E4F534A:
            doc = json.loads(payload.decode("utf-8"))
        elif ctype == 0x004E4942:
            blob = payload
    assert isinstance(doc, dict), "missing JSON chunk"
    return doc, blob


class TestBuildHumanoidRig(unittest.TestCase):
    def _assert_valid_character_glb(self, glb_path: Path, expected_height: float) -> None:
        self.assertTrue(glb_path.is_file(), f"GLB not created: {glb_path}")
        doc, blob = _read_glb(glb_path)

        # 1. Exact 65-bone skeleton preserved (with fingers!)
        self.assertEqual(len(doc.get("skins", [])), 1)
        joints = doc["skins"][0]["joints"]
        self.assertEqual(len(joints), 65)
        jnames = [doc["nodes"][j]["name"] for j in joints]
        for required_bone in (
            "root",
            "pelvis",
            "Head",
            "hand_l",
            "hand_r",
            "thumb_01_l",
            "index_01_l",
            "middle_01_l",
            "ring_01_l",
            "pinky_01_l",
            "thumb_01_r",
            "pinky_03_r",
        ):
            self.assertIn(required_bone, jnames)

        # 2. Embedded textures (no external URIs), body albedo is 2048x2048
        images = doc.get("images", [])
        self.assertGreaterEqual(len(images), 7)
        for im in images:
            self.assertNotIn("uri", im, "GLB must be self-contained with bufferView images")
            self.assertIn("bufferView", im)
        body_im_bv = doc["bufferViews"][images[0]["bufferView"]]
        raw_png = blob[body_im_bv["byteOffset"] : body_im_bv["byteOffset"] + body_im_bv["byteLength"]]
        pil_im = Image.open(io.BytesIO(raw_png))
        self.assertEqual(pil_im.size, (2048, 2048))

        # 3. Meshes: zero NaNs, grounded at y=0, height == expected_height, 7 BlendShapes
        mesh_by_name = {m["name"]: m for m in doc["meshes"]}
        for req_mesh in ("Body", "Eyes", "Eyebrows", "Hair"):
            self.assertIn(req_mesh, mesh_by_name)

        for mname, mesh in mesh_by_name.items():
            prim = mesh["primitives"][0]
            pos = read_accessor(doc, blob, prim["attributes"]["POSITION"])
            nrm = read_accessor(doc, blob, prim["attributes"]["NORMAL"])
            self.assertTrue(np.all(np.isfinite(pos)), f"NaN/Inf in {mname} POSITION")
            self.assertTrue(np.all(np.isfinite(nrm)), f"NaN/Inf in {mname} NORMAL")
            self.assertIn("JOINTS_0", prim["attributes"])
            self.assertIn("WEIGHTS_0", prim["attributes"])
            w0 = read_accessor(doc, blob, prim["attributes"]["WEIGHTS_0"])
            self.assertTrue(np.all(np.isfinite(w0)), f"NaN/Inf in {mname} WEIGHTS_0")

        body_pos = read_accessor(doc, blob, mesh_by_name["Body"]["primitives"][0]["attributes"]["POSITION"])
        self.assertAlmostEqual(float(body_pos[:, 1].min()), 0.0, places=3)
        actual_h = float(body_pos[:, 1].max() - body_pos[:, 1].min())
        self.assertAlmostEqual(actual_h, expected_height, delta=0.01)

        # 4. Morph targets (BlendShapes) on BOTH Body and Eyebrows
        for mname in ("Body", "Eyebrows"):
            mesh = mesh_by_name[mname]
            self.assertEqual(mesh.get("extras", {}).get("targetNames"), BLEND_SHAPE_NAMES)
            targets = mesh["primitives"][0].get("targets", [])
            self.assertEqual(len(targets), len(BLEND_SHAPE_NAMES))
            for tname, tdict in zip(BLEND_SHAPE_NAMES, targets):
                self.assertIn("POSITION", tdict)
                delta = read_accessor(doc, blob, tdict["POSITION"])
                self.assertTrue(np.all(np.isfinite(delta)), f"NaN/Inf in {mname}.{tname}")
                acc = doc["accessors"][tdict["POSITION"]]
                self.assertIn("min", acc)
                self.assertIn("max", acc)
                if mname == "Body":
                    # Every body morph target must have non-trivial movement (> 2 mm)
                    self.assertGreater(
                        float(np.linalg.norm(delta, axis=1).max()),
                        0.002,
                        f"Body morph {tname} has zero displacement",
                    )

    def test_female_jumpsuit_ponytail(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "linlan_test.glb"
            rep = build_humanoid_rig(DEFAULT_CAST_RECIPES["linlan"], out)
            self.assertEqual(rep["bones"], 65)
            self._assert_valid_character_glb(out, expected_height=1.65)

    def test_male_tactical_buzzed_with_beard(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "zhou_test.glb"
            rep = build_humanoid_rig(DEFAULT_CAST_RECIPES["director_zhou"], out)
            self.assertTrue(rep["beard"])
            self._assert_valid_character_glb(out, expected_height=1.80)
            doc, _ = _read_glb(out)
            names = [m["name"] for m in doc["meshes"]]
            self.assertIn("Beard", names)

    def test_cross_gender_hair_and_labcoat(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "custom_test.glb"
            recipe = {
                "base": "female",
                "height_m": 1.62,
                "skin_tint": "#f5dcc8",
                "hair": {"style": "long", "color": "#3b261d", "beard": False},
                "outfit": {
                    "style": "labcoat",
                    "jacket_color": "#eef2f5",
                    "accent_color": "#1e8c8c",
                    "pants_color": "#25303d",
                    "boots_color": "#222830",
                    "shirt_color": "#238082",
                },
            }
            build_humanoid_rig(recipe, out)
            self._assert_valid_character_glb(out, expected_height=1.62)

    def test_rover_rigged_wheels_and_turret(self) -> None:
        src = Path(__file__).resolve().parents[1] / "media" / "assets3d" / "atuo" / "atuo_norm.glb"
        if not src.is_file():
            self.skipTest("atuo_norm.glb not present")
        with tempfile.TemporaryDirectory() as td:
            out_glb = Path(td) / "atuo_rigged.glb"
            out_meta = Path(td) / "rover_meta.json"
            meta = prepare_rover_wheels(src, out_glb, out_meta)
            self.assertAlmostEqual(meta["wheel_y_min"], 0.0, delta=0.005)
            self.assertEqual(set(meta["wheels"].keys()), set(WHEEL_NAMES))
            doc, _ = _read_glb(out_glb)
            node_names = {n["name"] for n in doc["nodes"]}
            for req in ["AT1_Rover", "Chassis", "Turret"] + WHEEL_NAMES:
                self.assertIn(req, node_names)


if __name__ == "__main__":
    unittest.main()
