#!/usr/bin/env python3
"""Reusable modular 3D humanoid character builder on the Quaternius 65-bone rig.

Builds a self-contained binary GLB from a character recipe (JSON or CLI flags)
on top of ``Superhero_Female_FullBody.gltf`` or ``Superhero_Male_FullBody.gltf``:

- Preserves the full 65-bone skeleton (including all 10 fingers) matching
  ``UAL1_Standard.glb`` 1:1, grounded cleanly at ``y = 0.0`` and scaled to
  ``height_m``.
- Sculpted 3D clothing silhouette volume (smoothed torso/leg drape + normal
  inflation for jacket, collar, belt, cargo pants, chunky boots) while keeping
  the sculpted head, face, ears, upper neck, and 5-finger hands at 0 mm delta.
- Crisp 2048x2048 UV-space outfit texture + matched fabric normal & roughness
  maps while preserving the native painted Vice-City-level face/hand textures.
- Embeds sculpted 3D hair (``long``, ``ponytail``, ``parted``, ``buns``,
  ``buzzed``) and optional beard skinned rigidly to the ``Head`` bone.
- Injects 7 standard glTF Morph Targets (BlendShapes) on both body and eyebrows:
  ``jawOpen``, ``mouthFunnel``, ``mouthSmile``, ``mouthFrown``, ``eyeBlink``,
  ``browDown``, ``browUp``.
"""
from __future__ import annotations

import argparse
import copy
import io
import json
import math
import struct
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import shortest_path

ROOT = Path(__file__).resolve().parents[1]
QUATERNIUS_DIR = (
    ROOT / "media" / "assets3d" / "cc0" / "quaternius_universal_base_characters"
)

BLEND_SHAPE_NAMES = [
    "jawOpen",
    "mouthFunnel",
    "mouthSmile",
    "mouthFrown",
    "eyeBlink",
    "browDown",
    "browUp",
]

_GLB_MAGIC = b"glTF"
_JSON_CHUNK = 0x4E4F534A
_BIN_CHUNK = 0x004E4942
_DTYPES = {
    5120: "<i1",
    5121: "<u1",
    5122: "<i2",
    5123: "<u2",
    5125: "<u4",
    5126: "<f4",
}
_COMPS = {
    "SCALAR": 1,
    "VEC2": 2,
    "VEC3": 3,
    "VEC4": 4,
    "MAT2": 4,
    "MAT3": 9,
    "MAT4": 16,
}

# Authored crown heights (in raw Quaternius meters) for cross-gender hair fit.
_FEMALE_HEAD_ANCHOR = np.array([0.0, 1.656, 0.000], dtype=np.float64)
_MALE_HEAD_ANCHOR = np.array([0.0, 1.699, 0.005], dtype=np.float64)

_HAIR_SPECS = {
    "long": ("Hair_Long.gltf", "female"),
    "buns": ("Hair_Buns.gltf", "female"),
    "ponytail": ("Hair_Buns.gltf", "female"),
    "parted": ("Hair_SimpleParted.gltf", "male"),
    "buzzed_male": ("Hair_Buzzed.gltf", "male"),
    "buzzed_female": ("Hair_BuzzedFemale.gltf", "female"),
}

DEFAULT_CAST_RECIPES: dict[str, dict[str, Any]] = {
    "linlan": {
        "name": "linlan",
        "base": "female",
        "height_m": 1.65,
        "skin_tint": "#f4d4bc",
        "hair": {
            "style": "ponytail",
            "color": "#18151d",
            "beard": False,
        },
        "outfit": {
            "style": "jumpsuit",
            "jacket_color": "#2b5998",
            "accent_color": "#e87a24",
            "collar_color": "#e87a24",
            "pants_color": "#234373",
            "boots_color": "#1c2028",
            "belt_color": "#232830",
            "shirt_color": "#e8ecf2",
        },
    },
    "chen": {
        "name": "chen",
        "base": "male",
        "height_m": 1.76,
        "skin_tint": "#ecc7aa",
        "hair": {
            "style": "parted",
            "color": "#16161b",
            "beard": False,
        },
        "outfit": {
            "style": "jacket",
            "jacket_color": "#1f2e47",
            "accent_color": "#ea7b24",
            "collar_color": "#243552",
            "pants_color": "#2d3540",
            "boots_color": "#1a1d22",
            "belt_color": "#22252b",
            "shirt_color": "#eceef2",
        },
    },
    "director_zhou": {
        "name": "director_zhou",
        "base": "male",
        "height_m": 1.80,
        "skin_tint": "#e5be9e",
        "hair": {
            "style": "buzzed",
            "color": "#24252a",
            "beard": True,
        },
        "outfit": {
            "style": "tactical",
            "jacket_color": "#333a44",
            "accent_color": "#d4a644",
            "collar_color": "#282e37",
            "pants_color": "#262b33",
            "boots_color": "#16181d",
            "belt_color": "#1d2026",
            "shirt_color": "#2c313a",
        },
    },
    "dr_su": {
        "name": "dr_su",
        "base": "female",
        "height_m": 1.62,
        "skin_tint": "#f6dac4",
        "hair": {
            "style": "long",
            "color": "#3b261d",
            "beard": False,
        },
        "outfit": {
            "style": "labcoat",
            "jacket_color": "#eef2f5",
            "accent_color": "#1e8c8c",
            "collar_color": "#e2e8ed",
            "pants_color": "#25303d",
            "boots_color": "#222830",
            "belt_color": "#1e8c8c",
            "shirt_color": "#238082",
        },
    },
}


class PS2CharacterArtProfile:
    """Opt-in grounded PS2 human proportions and matte fabric finish.

    Default recipes stay on the original sculpt. Enable with ``ps2_profile``
    or ``--ps2-profile``. The 65-bone UAL skeleton and y=0 grounding are unchanged.
    """

    SPECULAR = 0.12
    METALLIC = 0.04
    ROUGHNESS_MIN = 0.82
    ROUGHNESS_MAX = 0.94

    _PALETTES: dict[str, dict[str, str]] = {
        "male": {
            "jacket_color": "#5d6c7a",
            "accent_color": "#e07a22",
            "collar_color": "#465460",
            "pants_color": "#3e4650",
            "boots_color": "#323840",
            "belt_color": "#2e343c",
            "shirt_color": "#d8dce2",
        },
        "female": {
            "jacket_color": "#426484",
            "accent_color": "#d4893a",
            "collar_color": "#c47a32",
            "pants_color": "#355270",
            "boots_color": "#2c3238",
            "belt_color": "#2a3036",
            "shirt_color": "#d5dae0",
        },
    }

    def __init__(self, gender: str) -> None:
        self.gender = "male" if str(gender).lower() == "male" else "female"
        if self.gender == "male":
            # Sturdy industrial engineer: narrower deltoids, filled waist, smaller hands.
            self.shoulder_taper = 0.10
            self.waist_fill = 0.09
            self.hand_scale = 0.91
        else:
            # Balanced silhouette; hands refined about 7%.
            self.shoulder_taper = 0.05
            self.waist_fill = 0.04
            self.hand_scale = 0.93

    @classmethod
    def apply_palette(cls, outfit: dict[str, Any], gender: str) -> None:
        """Replace crushed-black superhero dyes with readable weathered kit colors."""
        palette = cls._PALETTES["male" if str(gender).lower() == "male" else "female"]
        for key, value in palette.items():
            outfit[key] = value
        outfit["ps2_profile"] = True
        outfit["ps2_gender"] = "male" if str(gender).lower() == "male" else "female"

    def apply_proportions(
        self,
        pos: np.ndarray,
        nrm: np.ndarray,
        faces: np.ndarray,
        j0: np.ndarray,
        w0: np.ndarray,
        bgroups: dict[str, set[int]],
        joint_world_pos: dict[str, np.ndarray],
        lm: dict[str, float],
        fields: dict[str, np.ndarray],
    ) -> tuple[np.ndarray, np.ndarray]:
        """Reshape cloth/shoulder/hand volume. Bone translations are not touched."""
        pos = np.array(pos, dtype=np.float64, copy=True)
        upper = _bone_weight_sum(j0, w0, bgroups["upper_arm"])
        torso = _bone_weight_sum(j0, w0, bgroups["torso"])
        pelvis = _bone_weight_sum(j0, w0, bgroups["pelvis"])
        hand = _bone_weight_sum(j0, w0, bgroups["hand"])
        skin = fields["skin_protect"]
        y = pos[:, 1]
        ax = np.abs(pos[:, 0])

        shoulder_y = (
            smoothstep(lm["shoulder_y"] - 0.16, lm["shoulder_y"] - 0.03, y)
            * (1.0 - smoothstep(lm["shoulder_y"] + 0.015, lm["shoulder_y"] + 0.07, y))
        )
        lateral = smoothstep(0.09, 0.20, ax)
        shoulder_mask = np.clip(upper * 0.9 + torso * lateral * 0.7, 0.0, 1.0)
        shoulder_mask *= shoulder_y * (1.0 - skin)
        pos[:, 0] *= 1.0 - self.shoulder_taper * shoulder_mask

        rib_y = smoothstep(lm["waist_y"] - 0.03, lm["waist_y"] + 0.02, y) * (
            1.0 - smoothstep(lm["chest_y"] + 0.01, lm["chest_y"] + 0.07, y)
        )
        arm = fields["arm_w"]
        rib_mask = rib_y * np.clip(torso + pelvis, 0.0, 1.0) * (1.0 - smoothstep(0.25, 0.6, arm))
        rib_mask *= 1.0 - skin
        pos[:, 0] *= 1.0 + self.waist_fill * rib_mask
        pos[:, 2] *= 1.0 + self.waist_fill * 0.62 * rib_mask

        for name in ("hand_l", "hand_r"):
            center = joint_world_pos[name]
            side = (pos[:, 0] * float(center[0]) > 0.0).astype(np.float64)
            weight = np.clip(hand * side, 0.0, 1.0)
            delta = pos - center.reshape(1, 3)
            pos -= delta * ((1.0 - self.hand_scale) * weight)[:, None]

        if self.gender == "female":
            z = pos[:, 2]
            x = pos[:, 0]
            front = smoothstep(0.0, 0.045, z - lm["neck_z"])
            cloth = 1.0 - skin
            pouch = (
                smoothstep(0.085, 0.115, ax)
                * (1.0 - smoothstep(0.155, 0.185, ax))
                * np.exp(-0.5 * ((y - (lm["waist_y"] - 0.035)) / 0.028) ** 2)
                * front
                * cloth
            )
            pos[:, 0] += np.sign(x) * 0.011 * pouch
            pos[:, 2] += 0.008 * pouch
            strap = (
                np.exp(-0.5 * ((ax - 0.072) / 0.014) ** 2)
                * smoothstep(lm["waist_y"] + 0.01, lm["chest_y"], y)
                * (1.0 - smoothstep(lm["shoulder_y"] - 0.01, lm["shoulder_y"] + 0.03, y))
                * front
                * cloth
            )
            pos[:, 2] += 0.0065 * strap

        moved = np.clip(shoulder_mask + rib_mask + hand, 0.0, 1.0)
        welded = compute_welded_normals(pos, faces)
        blend = smoothstep(0.04, 0.35, moved)[:, None]
        out_n = nrm * (1.0 - blend) + welded * blend
        lens = np.linalg.norm(out_n, axis=1, keepdims=True)
        out_n = np.where(lens > 1e-8, out_n / np.maximum(lens, 1e-8), nrm)
        return pos, out_n

    @staticmethod
    def paint_fabric(
        cloth_rgb: np.ndarray,
        height_field: np.ndarray,
        cloth_rough: np.ndarray,
        cloth_metal: np.ndarray,
        lm: dict[str, float],
        gender: str,
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
        ax: np.ndarray,
        nrm_y: np.ndarray,
        is_upper: np.ndarray,
        is_front: np.ndarray,
        is_arm: np.ndarray,
        is_boot: np.ndarray,
    ) -> None:
        """Baked PS2 folds, stitching, pockets, patches, harness, and matte ORM."""
        fold_a = 0.5 + 0.5 * np.sin(y * 78.0 + ax * 36.0)
        fold_b = 0.5 + 0.5 * np.sin(y * 46.0 - z * 28.0 + 1.3)
        crease = np.clip((fold_a * fold_b - 0.28) * 1.35, 0.0, 1.0) * is_upper
        crease = np.maximum(crease, np.clip((fold_b - 0.62) * 1.6, 0.0, 1.0) * (1.0 - is_upper) * (1.0 - is_boot))
        cloth_rgb *= 1.0 - 0.18 * crease[:, :, None]
        height_field -= 0.42 * crease

        stitch = (np.sin(y * 380.0) > 0.45).astype(np.float32)
        seam = np.exp(-0.5 * (x / 0.0032) ** 2) * is_front * is_upper
        flank = np.exp(-0.5 * ((ax - 0.012) / 0.0024) ** 2) * is_front * is_upper
        cloth_rgb *= 1.0 - 0.42 * (seam * stitch)[:, :, None]
        cloth_rgb *= 1.0 - 0.22 * flank[:, :, None]
        height_field -= 0.28 * seam

        def _rect_outline(x0: float, x1: float, y0: float, y1: float, front_only: bool = True) -> np.ndarray:
            in_x = smoothstep(x0, x0 + 0.004, ax) * (1.0 - smoothstep(x1 - 0.004, x1, ax))
            in_y = smoothstep(y0, y0 + 0.004, y) * (1.0 - smoothstep(y1 - 0.004, y1, y))
            edge = np.exp(-0.5 * ((ax - x0) / 0.0032) ** 2) + np.exp(-0.5 * ((ax - x1) / 0.0032) ** 2)
            edge = edge * in_y + (
                np.exp(-0.5 * ((y - y0) / 0.004) ** 2) + np.exp(-0.5 * ((y - y1) / 0.004) ** 2)
            ) * in_x
            mask = np.clip(edge, 0.0, 1.0) * (1.0 - is_arm)
            if front_only:
                mask *= is_front
            return mask

        chest = _rect_outline(0.048, 0.128, lm["chest_y"] - 0.055, lm["chest_y"] + 0.012)
        thigh_mid = 0.5 * (lm["thigh_y"] + lm["knee_y"])
        cargo = _rect_outline(0.145, 0.195, thigh_mid - 0.055, thigh_mid + 0.055, front_only=False)
        cargo *= 1.0 - is_upper
        cloth_rgb *= 1.0 - 0.40 * np.clip(chest + cargo, 0.0, 1.0)[:, :, None]
        height_field += 0.22 * np.clip(chest + cargo, 0.0, 1.0)

        insignia = (
            is_arm
            * smoothstep(lm["chest_y"] + 0.012, lm["chest_y"] + 0.02, y)
            * (1.0 - smoothstep(lm["chest_y"] + 0.048, lm["chest_y"] + 0.058, y))
            * smoothstep(0.12, 0.18, ax)
        )
        accent = hex_to_rgb01("#e07a22").astype(np.float32)
        cloth_rgb[:] = cloth_rgb * (1.0 - insignia[:, :, None]) + accent[None, None, :] * insignia[:, :, None]

        elbow = is_arm * np.exp(-0.5 * ((y - (lm["waist_y"] + 0.02)) / 0.032) ** 2) * smoothstep(0.12, 0.18, ax)
        knee = (
            (1.0 - is_upper)
            * (1.0 - is_boot)
            * np.exp(-0.5 * ((y - lm["knee_y"]) / 0.03) ** 2)
            * is_front
        )
        patch = np.clip(np.maximum(elbow, knee), 0.0, 1.0)
        cloth_rgb *= 1.0 - 0.16 * patch[:, :, None]
        height_field += 0.2 * patch

        if gender == "male":
            span = max(lm["shoulder_y"] - lm["waist_y"], 0.2)
            t = np.clip((y - lm["waist_y"]) / span, 0.0, 1.0)
            diag = np.abs(t - (0.5 + 0.45 * np.clip(x / 0.16, -1.0, 1.0)))
            harness = np.exp(-0.5 * (diag / 0.045) ** 2) * is_front * is_upper * (1.0 - is_arm)
            harness_col = np.array([0.23, 0.25, 0.28], dtype=np.float32)
            cloth_rgb[:] = cloth_rgb * (1.0 - 0.82 * harness[:, :, None]) + harness_col[None, None, :] * (0.82 * harness[:, :, None])
            yoke = (
                is_upper
                * smoothstep(lm["shoulder_y"] - 0.045, lm["shoulder_y"] - 0.012, y)
                * (1.0 - smoothstep(lm["shoulder_y"] + 0.012, lm["shoulder_y"] + 0.04, y))
            )
            cloth_rgb[:] = cloth_rgb * (1.0 - 0.72 * yoke[:, :, None]) + accent[None, None, :] * (0.72 * yoke[:, :, None])
            height_field += 0.18 * harness
        else:
            strap = (
                np.exp(-0.5 * ((ax - 0.07) / 0.012) ** 2)
                * smoothstep(lm["waist_y"], lm["chest_y"], y)
                * (1.0 - smoothstep(lm["shoulder_y"] - 0.01, lm["shoulder_y"] + 0.025, y))
                * is_front
                * (1.0 - is_arm)
            )
            strap_col = np.array([0.20, 0.23, 0.27], dtype=np.float32)
            cloth_rgb[:] = cloth_rgb * (1.0 - 0.8 * strap[:, :, None]) + strap_col[None, None, :] * (0.8 * strap[:, :, None])
            pouch = (
                smoothstep(0.09, 0.105, ax)
                * (1.0 - smoothstep(0.15, 0.168, ax))
                * smoothstep(lm["waist_y"] - 0.07, lm["waist_y"] - 0.055, y)
                * (1.0 - smoothstep(lm["waist_y"] - 0.005, lm["waist_y"] + 0.012, y))
                * is_front
                * (1.0 - is_arm)
            )
            pouch_col = np.array([0.24, 0.27, 0.31], dtype=np.float32)
            cloth_rgb[:] = cloth_rgb * (1.0 - 0.65 * pouch[:, :, None]) + pouch_col[None, None, :] * (0.65 * pouch[:, :, None])
            height_field += 0.26 * pouch + 0.16 * strap

        low = 1.0 - smoothstep(lm["knee_y"], lm["chest_y"], y)
        grime = np.clip(low * 0.85 + is_boot * 0.35, 0.0, 1.0)
        dirt = np.array([0.45, 0.40, 0.34], dtype=np.float32)
        cloth_rgb[:] = cloth_rgb * (1.0 - 0.16 * grime[:, :, None]) + (cloth_rgb * dirt) * (0.16 * grime[:, :, None])
        # Keep upward-facing panels a touch lighter so folds stay readable.
        cloth_rgb *= (0.94 + 0.06 * np.clip(nrm_y * 0.5 + 0.5, 0.0, 1.0))[:, :, None]

        lum = cloth_rgb[:, :, 0] * 0.30 + cloth_rgb[:, :, 1] * 0.57 + cloth_rgb[:, :, 2] * 0.13
        cloth_rgb *= 0.86
        cloth_rgb += lum[:, :, None] * 0.14
        np.clip(cloth_rgb, 0.0, 1.0, out=cloth_rgb)
        cloth_rough[:] = np.clip(cloth_rough, PS2CharacterArtProfile.ROUGHNESS_MIN, PS2CharacterArtProfile.ROUGHNESS_MAX)
        cloth_metal[:] = PS2CharacterArtProfile.METALLIC

    @staticmethod
    def bake_contact_ao(
        albedo: np.ndarray,
        lm: dict[str, float],
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
        ax: np.ndarray,
    ) -> np.ndarray:
        neck_r = np.hypot(x, z - lm["neck_z"])
        neck = np.exp(-0.5 * ((y - lm["neck_y"]) / 0.02) ** 2) * np.exp(-0.5 * (neck_r / 0.055) ** 2)
        collar = np.exp(-0.5 * ((y - lm["collar_bot_y"]) / 0.012) ** 2) * np.exp(-0.5 * (neck_r / 0.07) ** 2)
        under = np.exp(-0.5 * ((y - (lm["shoulder_y"] - 0.055)) / 0.028) ** 2) * np.exp(
            -0.5 * ((ax - 0.155) / 0.045) ** 2
        )
        ao = np.clip(0.20 * neck + 0.22 * collar + 0.16 * under, 0.0, 0.42)
        return albedo * (1.0 - ao[:, :, None])


# ------------------------------------------------------------------ utilities


def hex_to_rgb01(value: str | list[float] | tuple[float, ...]) -> np.ndarray:
    if isinstance(value, (list, tuple)):
        arr = np.asarray(value[:3], dtype=np.float64)
        return arr / 255.0 if arr.max() > 1.0 else np.clip(arr, 0.0, 1.0)
    s = str(value).strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        raise ValueError(f"invalid hex color: {value}")
    return np.array(
        [int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)], dtype=np.float64
    ) / 255.0


def smoothstep(
    edge0: float | np.ndarray, edge1: float | np.ndarray, x: np.ndarray | float
) -> np.ndarray:
    diff = np.asarray(edge1, dtype=np.float64) - np.asarray(edge0, dtype=np.float64)
    denom = np.where(np.abs(diff) > 1e-8, diff, 1e-8)
    t = np.clip((x - edge0) / denom, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def load_gltf(path: Path) -> tuple[dict, bytes]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    uri = doc["buffers"][0]["uri"]
    blob = (path.parent / uri).read_bytes()
    return doc, blob


def read_accessor(doc: dict, blob: bytes, idx: int) -> np.ndarray:
    acc = doc["accessors"][idx]
    bv = doc["bufferViews"][acc["bufferView"]]
    dt = np.dtype(_DTYPES[int(acc["componentType"])])
    comps = _COMPS[acc["type"]]
    cnt = int(acc["count"])
    off = int(bv.get("byteOffset", 0)) + int(acc.get("byteOffset", 0))
    width = comps * dt.itemsize
    stride = int(bv.get("byteStride") or width)
    if stride == width:
        arr = np.frombuffer(blob[off : off + cnt * width], dtype=dt).reshape(cnt, comps)
    else:
        rows = [
            np.frombuffer(blob[off + i * stride : off + i * stride + width], dtype=dt)
            for i in range(cnt)
        ]
        arr = np.vstack(rows)
    return arr.copy()


def quat_to_mat4(q: list[float] | np.ndarray) -> np.ndarray:
    x, y, z, w = (float(v) for v in q)
    m = np.eye(4, dtype=np.float64)
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    m[0, 0] = 1.0 - 2.0 * (yy + zz)
    m[0, 1] = 2.0 * (xy - wz)
    m[0, 2] = 2.0 * (xz + wy)
    m[1, 0] = 2.0 * (xy + wz)
    m[1, 1] = 1.0 - 2.0 * (xx + zz)
    m[1, 2] = 2.0 * (yz - wx)
    m[2, 0] = 2.0 * (xz - wy)
    m[2, 1] = 2.0 * (yz + wx)
    m[2, 2] = 1.0 - 2.0 * (xx + yy)
    return m


def node_local_matrix(node: dict) -> np.ndarray:
    if "matrix" in node:
        return np.asarray(node["matrix"], dtype=np.float64).reshape((4, 4), order="F")
    t = np.eye(4, dtype=np.float64)
    t[:3, 3] = np.asarray(node.get("translation", [0.0, 0.0, 0.0]), dtype=np.float64)
    r = quat_to_mat4(node.get("rotation", [0.0, 0.0, 0.0, 1.0]))
    s = np.diag(list(node.get("scale", [1.0, 1.0, 1.0])) + [1.0])
    return t @ r @ s


def compute_world_matrices(nodes: list[dict]) -> tuple[list[np.ndarray], dict[int, int]]:
    parent: dict[int, int] = {}
    for i, node in enumerate(nodes):
        for ch in node.get("children") or []:
            parent[int(ch)] = i
    cache: dict[int, np.ndarray] = {}

    def world(i: int) -> np.ndarray:
        if i in cache:
            return cache[i]
        m = node_local_matrix(nodes[i])
        cache[i] = world(parent[i]) @ m if i in parent else m
        return cache[i]

    return [world(i) for i in range(len(nodes))], parent


def compute_welded_normals(
    positions: np.ndarray, faces: np.ndarray, decimals: int = 5
) -> np.ndarray:
    verts = np.asarray(positions, dtype=np.float64)
    tris = np.asarray(faces, dtype=np.int64)
    p = verts[tris]
    cross = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    scale = 10.0**decimals
    keys = np.ascontiguousarray(np.rint(verts * scale).astype(np.int64))
    packed = keys.view(np.dtype((np.void, keys.dtype.itemsize * 3))).ravel()
    _, inv = np.unique(packed, return_inverse=True)
    accum = np.zeros((int(inv.max()) + 1, 3), dtype=np.float64)
    g = inv[tris]
    np.add.at(accum, g[:, 0], cross)
    m1 = g[:, 1] != g[:, 0]
    np.add.at(accum, g[m1, 1], cross[m1])
    m2 = (g[:, 2] != g[:, 0]) & (g[:, 2] != g[:, 1])
    np.add.at(accum, g[m2, 2], cross[m2])
    lens = np.linalg.norm(accum, axis=1)
    ok = lens > 1e-12
    accum[ok] /= lens[ok, None]
    accum[~ok] = np.array([0.0, 1.0, 0.0])
    return np.ascontiguousarray(accum[inv], dtype=np.float64)


def _laplacian_smooth_welded(
    positions: np.ndarray,
    faces: np.ndarray,
    weights: np.ndarray,
    iterations: int = 3,
    lam: float = 0.45,
) -> np.ndarray:
    """Smooth vertex positions across welded duplicates with per-vertex weight."""
    pos = positions.astype(np.float64).copy()
    keys = np.ascontiguousarray(np.rint(pos * 1e5).astype(np.int64))
    packed = keys.view(np.dtype((np.void, keys.dtype.itemsize * 3))).ravel()
    _, inv = np.unique(packed, return_inverse=True)
    n_u = int(inv.max()) + 1
    upos = np.zeros((n_u, 3), dtype=np.float64)
    uw = np.zeros(n_u, dtype=np.float64)
    counts = np.zeros(n_u, dtype=np.float64)
    np.add.at(upos, inv, pos)
    np.add.at(uw, inv, weights)
    np.add.at(counts, inv, 1.0)
    upos /= np.maximum(counts, 1.0)[:, None]
    uw /= np.maximum(counts, 1.0)

    wf = inv[faces]
    edges = np.vstack(
        [
            wf[:, [0, 1]],
            wf[:, [1, 0]],
            wf[:, [1, 2]],
            wf[:, [2, 1]],
            wf[:, [2, 0]],
            wf[:, [0, 2]],
        ]
    )
    edges = edges[edges[:, 0] != edges[:, 1]]
    src = edges[:, 0]
    dst = edges[:, 1]
    deg = np.bincount(src, minlength=n_u).astype(np.float64)
    valid = deg > 0

    for _ in range(iterations):
        nbr_sum = np.zeros_like(upos)
        np.add.at(nbr_sum, src, upos[dst])
        avg = upos.copy()
        avg[valid] = nbr_sum[valid] / deg[valid, None]
        upos = upos + (lam * uw[:, None]) * (avg - upos)

    return upos[inv]


# ---------------------------------------------------------- region & anatomy


def _bone_weight_sum(
    joints_attr: np.ndarray, weights_attr: np.ndarray, bone_indices: set[int]
) -> np.ndarray:
    if not bone_indices:
        return np.zeros(len(joints_attr), dtype=np.float64)
    mask = np.isin(joints_attr, list(bone_indices))
    return np.sum(weights_attr * mask, axis=1)


def _classify_bones(jnames: list[str]) -> dict[str, set[int]]:
    groups: dict[str, set[int]] = {
        "head": set(),
        "neck": set(),
        "torso": set(),
        "pelvis": set(),
        "upper_arm": set(),
        "lower_arm": set(),
        "hand": set(),
        "thigh": set(),
        "calf": set(),
        "foot": set(),
    }
    for i, raw in enumerate(jnames):
        n = raw.lower()
        if n == "head":
            groups["head"].add(i)
        elif "neck" in n:
            groups["neck"].add(i)
        elif "spine" in n or "clavicle" in n:
            groups["torso"].add(i)
        elif "pelvis" in n or "hips" in n:
            groups["pelvis"].add(i)
        elif "upperarm" in n:
            groups["upper_arm"].add(i)
        elif "lowerarm" in n:
            groups["lower_arm"].add(i)
        elif any(
            k in n
            for k in ("hand", "thumb", "index", "middle", "ring", "pinky")
        ):
            groups["hand"].add(i)
        elif "thigh" in n:
            groups["thigh"].add(i)
        elif "calf" in n:
            groups["calf"].add(i)
        elif "foot" in n or "ball" in n or "toe" in n:
            groups["foot"].add(i)
    return groups


def _compute_landmarks(
    jnames: list[str], joint_world_pos: dict[str, np.ndarray], body_pos: np.ndarray
) -> dict[str, float]:
    neck = joint_world_pos["neck_01"]
    head = joint_world_pos["Head"]
    pelvis = joint_world_pos["pelvis"]
    spine1 = joint_world_pos["spine_01"]
    spine2 = joint_world_pos["spine_02"]
    spine3 = joint_world_pos["spine_03"]
    hand_l = joint_world_pos["hand_l"]
    lowerarm_l = joint_world_pos["lowerarm_l"]
    thigh_l = joint_world_pos["thigh_l"]
    calf_l = joint_world_pos["calf_l"]
    foot_l = joint_world_pos["foot_l"]

    neck_h = float(head[1] - neck[1])
    return {
        "y_min": float(body_pos[:, 1].min()),
        "y_max": float(body_pos[:, 1].max()),
        "pelvis_y": float(pelvis[1]),
        "waist_y": float(0.55 * pelvis[1] + 0.45 * spine1[1]),
        "chest_y": float(0.45 * spine2[1] + 0.55 * spine3[1]),
        "shoulder_y": float(spine3[1] + 0.65 * (neck[1] - spine3[1])),
        "neck_y": float(neck[1]),
        "neck_z": float(neck[2]),
        "head_y": float(head[1]),
        "collar_bot_y": float(neck[1] - 0.35 * neck_h),
        "collar_top_y": float(neck[1] + 0.32 * neck_h),
        "cuff_x": float(abs(hand_l[0]) - 0.14 * abs(hand_l[0] - lowerarm_l[0])),
        "hand_x": float(abs(hand_l[0])),
        "thigh_y": float(thigh_l[1]),
        "knee_y": float(calf_l[1]),
        "boot_top_y": float(foot_l[1] + 0.36 * (calf_l[1] - foot_l[1])),
        "foot_y": float(foot_l[1]),
    }


def _compute_vertex_clothing_fields(
    pos: np.ndarray,
    j0: np.ndarray,
    w0: np.ndarray,
    bgroups: dict[str, set[int]],
    lm: dict[str, float],
    style: str,
) -> dict[str, np.ndarray]:
    head_w = _bone_weight_sum(j0, w0, bgroups["head"])
    neck_w = _bone_weight_sum(j0, w0, bgroups["neck"])
    torso_w = _bone_weight_sum(j0, w0, bgroups["torso"])
    pelvis_w = _bone_weight_sum(j0, w0, bgroups["pelvis"])
    arm_w = _bone_weight_sum(j0, w0, bgroups["upper_arm"] | bgroups["lower_arm"])
    hand_w = _bone_weight_sum(j0, w0, bgroups["hand"])
    thigh_w = _bone_weight_sum(j0, w0, bgroups["thigh"])
    calf_w = _bone_weight_sum(j0, w0, bgroups["calf"])
    foot_w = _bone_weight_sum(j0, w0, bgroups["foot"])

    x = pos[:, 0]
    y = pos[:, 1]
    z = pos[:, 2]
    ax = np.abs(x)

    # Neckline height varies slightly by style (stand collar vs V-collar)
    collar_top = lm["collar_top_y"]
    if style in ("jumpsuit", "tactical"):
        collar_top = lm["collar_top_y"] + 0.010
    elif style in ("jacket", "labcoat"):
        collar_top = lm["collar_top_y"] + 0.008
    elif style == "casual":
        collar_top = lm["collar_top_y"] - 0.012

    # Front V-dip for jacket/labcoat/casual open neckline
    v_dip = 0.0
    if style in ("jacket", "labcoat"):
        front = smoothstep(0.015, 0.075, z - lm["neck_z"])
        v_dip = 0.030 * front * np.clip(1.0 - ax / 0.072, 0.0, 1.0)
    elif style == "casual":
        front = smoothstep(0.015, 0.075, z - lm["neck_z"])
        v_dip = 0.025 * front * np.clip(1.0 - ax / 0.070, 0.0, 1.0)

    local_collar_top = collar_top - v_dip

    # Skin protection: entire head + upper neck above collar + hands beyond cuff
    above_collar = smoothstep(local_collar_top - 0.008, local_collar_top + 0.010, y)
    near_neck = 1.0 - smoothstep(0.068, 0.095, np.hypot(x, z - lm["neck_z"]))
    head_or_neck_skin = np.clip(
        head_w * 1.3 + above_collar * (neck_w + 0.85 * near_neck), 0.0, 1.0
    )
    # Also strictly protect any vertex at head level
    head_or_neck_skin = np.maximum(
        head_or_neck_skin, smoothstep(lm["head_y"] - 0.012, lm["head_y"] + 0.005, y)
    )

    beyond_cuff = smoothstep(lm["cuff_x"] - 0.015, lm["cuff_x"] + 0.015, ax)
    arm_or_hand = np.clip(arm_w + hand_w, 0.0, 1.0)
    hand_skin = np.clip(
        np.maximum(hand_w * 1.4, beyond_cuff * arm_or_hand), 0.0, 1.0
    )

    skin_protect = np.clip(np.maximum(head_or_neck_skin, hand_skin), 0.0, 1.0)
    cloth_w = np.clip(1.0 - skin_protect, 0.0, 1.0)

    # Collar ridge weight
    collar_band = (
        smoothstep(lm["collar_bot_y"] - 0.010, lm["collar_bot_y"] + 0.010, y)
        * (1.0 - smoothstep(local_collar_top - 0.005, local_collar_top + 0.012, y))
        * near_neck
        * cloth_w
    )

    # Belt ridge weight
    belt_band = (
        np.exp(-0.5 * ((y - lm["waist_y"]) / 0.022) ** 2)
        * np.clip(torso_w + pelvis_w, 0.0, 1.0)
        * cloth_w
    )

    # Boot weight
    boot_h = 1.0 - smoothstep(lm["boot_top_y"] - 0.025, lm["boot_top_y"] + 0.025, y)
    boot_region = np.clip(np.maximum(foot_w, boot_h * (calf_w + foot_w)), 0.0, 1.0)

    # Jacket / coat upper region
    hem_y = lm["waist_y"] - 0.075
    if style == "labcoat":
        hem_y = lm["knee_y"] + 0.14
    elif style == "tactical":
        hem_y = lm["waist_y"] - 0.10
    upper_body = smoothstep(hem_y - 0.03, hem_y + 0.03, y) * cloth_w

    # Pants region
    pants_region = np.clip((1.0 - upper_body) * (1.0 - boot_region), 0.0, 1.0) * cloth_w

    return {
        "skin_protect": skin_protect,
        "cloth_w": cloth_w,
        "collar_w": collar_band,
        "belt_w": belt_band,
        "upper_w": upper_body,
        "pants_w": pants_region,
        "boot_w": boot_region,
        "torso_w": torso_w,
        "pelvis_w": pelvis_w,
        "arm_w": arm_w,
        "leg_w": np.clip(thigh_w + calf_w, 0.0, 1.0),
    }


def sculpt_clothing_geometry(
    pos: np.ndarray,
    orig_nrm: np.ndarray,
    faces: np.ndarray,
    fields: dict[str, np.ndarray],
    lm: dict[str, float],
    style: str,
) -> tuple[np.ndarray, np.ndarray]:
    """Smooth superhero anatomy under cloth and inflate structured 3D volume."""
    cloth_w = fields["cloth_w"]
    collar_w = fields["collar_w"]
    belt_w = fields["belt_w"]
    upper_w = fields["upper_w"]
    pants_w = fields["pants_w"]
    boot_w = fields["boot_w"]

    # Soften hyper-defined superhero abs/pecs/thighs under fabric (zero on head/hands/soles)
    sole_protect = 1.0 - smoothstep(lm["y_min"] + 0.015, lm["y_min"] + 0.045, pos[:, 1])
    smooth_mask = np.clip(
        cloth_w * (1.0 - sole_protect) * (1.0 - 0.6 * collar_w), 0.0, 1.0
    )
    smoothed = _laplacian_smooth_welded(pos, faces, smooth_mask, iterations=4, lam=0.45)

    # Extra smoothing on boot toe-boxes so sculpted bare toes fuse into solid work boots
    toe_box = (
        boot_w
        * smoothstep(-0.01, 0.04, pos[:, 2])
        * (1.0 - smoothstep(lm["foot_y"] + 0.005, lm["foot_y"] + 0.045, pos[:, 1]))
    )
    if np.any(toe_box > 0.1):
        smoothed = _laplacian_smooth_welded(smoothed, faces, toe_box, iterations=12, lam=0.65)
        # Fill toe crevices gently toward each foot's convex toe-cap envelope
        for side_sign in (-1.0, 1.0):
            fmask = (toe_box > 0.25) & (pos[:, 0] * side_sign > 0.0)
            if np.any(fmask):
                pts = smoothed[fmask]
                fcx = float(np.median(pts[:, 0]))
                fcz = float(np.percentile(pts[:, 2], 45))
                # Push inward toe-gap vertices slightly outward radially
                dx_f = pts[:, 0] - fcx
                dz_f = np.maximum(pts[:, 2] - fcz, 0.0)
                ang = np.arctan2(dx_f, dz_f + 1e-5)
                r_curr = np.hypot(dx_f, dz_f)
                # Target rounded boot-toe radius along angle
                r_env = 0.056 * (0.85 + 0.22 * np.cos(ang) ** 2)
                pull = np.clip((r_env - r_curr) * 0.55, 0.0, 0.014) * toe_box[fmask]
                dir_x = dx_f / np.maximum(r_curr, 1e-4)
                dir_z = dz_f / np.maximum(r_curr, 1e-4)
                smoothed[fmask, 0] += dir_x * pull
                smoothed[fmask, 2] += dir_z * pull

    # Compute welded smooth normals on the smoothed mesh so UV seams inflate together
    weld_nrm = compute_welded_normals(smoothed, faces)

    # Per-region radial inflation (meters)
    jacket_thick = 0.0115 if style in ("jacket", "labcoat", "tactical") else 0.0095
    pants_thick = 0.0095
    boot_thick = 0.0135
    collar_extra = 0.0065 if style in ("jumpsuit", "tactical", "jacket") else 0.0040
    belt_extra = 0.0070 if style != "labcoat" else 0.0030

    inflate = (
        upper_w * jacket_thick
        + pants_w * pants_thick
        + boot_w * boot_thick
        + collar_w * collar_extra
        + belt_w * belt_extra
    )

    # Labcoat skirt subtle flare around upper thighs
    if style == "labcoat":
        coat_skirt = (
            smoothstep(lm["knee_y"] + 0.11, lm["knee_y"] + 0.22, pos[:, 1])
            * (1.0 - smoothstep(lm["waist_y"] - 0.02, lm["waist_y"] + 0.06, pos[:, 1]))
            * cloth_w
        )
        inflate += coat_skirt * 0.0065

    # Taper inflation smoothly to zero at soles so boots stay flat on floor
    inflate *= 1.0 - 0.65 * sole_protect
    # Guarantee zero inflation on protected skin (head/face/neck/hands)
    inflate *= cloth_w

    new_pos = smoothed + weld_nrm * inflate[:, None]
    # Never push boot soles below original y_min
    new_pos[:, 1] = np.maximum(new_pos[:, 1], lm["y_min"])

    # Recompute normals on clothed areas while preserving original normals on skin
    cloth_nrm = compute_welded_normals(new_pos, faces)
    blend = smoothstep(0.05, 0.35, cloth_w)[:, None]
    new_nrm = orig_nrm * (1.0 - blend) + cloth_nrm * blend
    lens = np.linalg.norm(new_nrm, axis=1, keepdims=True)
    new_nrm = np.where(lens > 1e-8, new_nrm / np.maximum(lens, 1e-8), orig_nrm)
    return new_pos, new_nrm


# ------------------------------------------------ 2048x2048 UV outfit baking


def _rasterize_uv_fields(
    pos: np.ndarray,
    nrm: np.ndarray,
    uv: np.ndarray,
    faces: np.ndarray,
    fields: dict[str, np.ndarray],
    size: int = 2048,
) -> dict[str, np.ndarray]:
    """Rasterize 3D coordinates & weights into a 2048x2048 UV map via cv2 + numpy."""
    tri_img = np.zeros((size, size, 3), dtype=np.uint8)
    uv_px = np.empty((len(uv), 2), dtype=np.float64)
    uv_px[:, 0] = uv[:, 0] * (size - 1)
    uv_px[:, 1] = uv[:, 1] * (size - 1)

    pts_int = np.rint(uv_px).astype(np.int32)
    for ti in range(len(faces)):
        i0, i1, i2 = faces[ti]
        poly = np.array([pts_int[i0], pts_int[i1], pts_int[i2]], dtype=np.int32)
        val = ti + 1
        color = (val & 0xFF, (val >> 8) & 0xFF, (val >> 16) & 0xFF)
        cv2.fillConvexPoly(tri_img, poly, color)

    tri_id = (
        tri_img[:, :, 0].astype(np.int32)
        | (tri_img[:, :, 1].astype(np.int32) << 8)
        | (tri_img[:, :, 2].astype(np.int32) << 16)
    ) - 1

    valid = tri_id >= 0
    ys, xs = np.where(valid)
    tids = tri_id[ys, xs]

    f0 = faces[tids, 0]
    f1 = faces[tids, 1]
    f2 = faces[tids, 2]

    p0 = uv_px[f0]
    p1 = uv_px[f1]
    p2 = uv_px[f2]

    v0 = p1 - p0
    v1 = p2 - p0
    vp = np.stack([xs.astype(np.float64), ys.astype(np.float64)], axis=1) - p0

    denom = v0[:, 0] * v1[:, 1] - v1[:, 0] * v0[:, 1]
    safe = np.where(np.abs(denom) > 1e-8, denom, 1.0)
    b1 = (vp[:, 0] * v1[:, 1] - v1[:, 0] * vp[:, 1]) / safe
    b2 = (v0[:, 0] * vp[:, 1] - vp[:, 0] * v0[:, 1]) / safe
    b0 = 1.0 - b1 - b2
    # Clamp slight integer-boundary overshoot
    b0 = np.clip(b0, 0.0, 1.0)
    b1 = np.clip(b1, 0.0, 1.0)
    b2 = np.clip(b2, 0.0, 1.0)
    bsum = np.maximum(b0 + b1 + b2, 1e-8)
    b0 /= bsum
    b1 /= bsum
    b2 /= bsum

    def interp3(arr: np.ndarray) -> np.ndarray:
        out = np.zeros((size, size, 3), dtype=np.float32)
        vals = (
            b0[:, None] * arr[f0] + b1[:, None] * arr[f1] + b2[:, None] * arr[f2]
        ).astype(np.float32)
        out[ys, xs] = vals
        return out

    def interp1(arr: np.ndarray) -> np.ndarray:
        out = np.zeros((size, size), dtype=np.float32)
        out[ys, xs] = (b0 * arr[f0] + b1 * arr[f1] + b2 * arr[f2]).astype(np.float32)
        return out

    pos_map = interp3(pos)
    nrm_map = interp3(nrm)
    cloth_map = interp1(fields["cloth_w"])
    skin_map = interp1(fields["skin_protect"])
    boot_map = interp1(fields["boot_w"])
    arm_map = interp1(fields["arm_w"])
    leg_map = interp1(fields["leg_w"])
    # Final bind-pose world X when the caller supplies sculpted positions.
    # Falls back to the pre-sculpt X so the map is always present.
    x3d_map = interp1(fields["x_3d"]) if "x_3d" in fields else pos_map[:, :, 0].copy()

    # Dilate valid pixels 8px into UV gutters so mipmaps never bleed black/skin seams
    mask = valid.copy()
    k3 = np.ones((3, 3), dtype=np.uint8)
    for _ in range(8):
        dil = cv2.dilate(mask.astype(np.uint8), k3) > 0
        ring = dil & (~mask)
        if not np.any(ring):
            break
        ry, rx = np.where(ring)
        # Pick first valid 4-connected or 8-connected neighbor
        filled = np.zeros(len(ry), dtype=bool)
        for dy_n, dx_n in (
            (-1, 0),
            (1, 0),
            (0, -1),
            (0, 1),
            (-1, -1),
            (-1, 1),
            (1, -1),
            (1, 1),
        ):
            ny = np.clip(ry + dy_n, 0, size - 1)
            nx = np.clip(rx + dx_n, 0, size - 1)
            ok = (~filled) & mask[ny, nx]
            if not np.any(ok):
                continue
            oy, ox = ry[ok], rx[ok]
            sy, sx = ny[ok], nx[ok]
            pos_map[oy, ox] = pos_map[sy, sx]
            nrm_map[oy, ox] = nrm_map[sy, sx]
            cloth_map[oy, ox] = cloth_map[sy, sx]
            skin_map[oy, ox] = skin_map[sy, sx]
            boot_map[oy, ox] = boot_map[sy, sx]
            arm_map[oy, ox] = arm_map[sy, sx]
            leg_map[oy, ox] = leg_map[sy, sx]
            x3d_map[oy, ox] = x3d_map[sy, sx]
            filled[ok] = True
        mask = dil

    return {
        "valid": mask,
        "pos": pos_map,
        "nrm": nrm_map,
        "cloth": cloth_map,
        "skin": skin_map,
        "boot": boot_map,
        "arm": arm_map,
        "leg": leg_map,
        "x_3d": x3d_map,
    }


def _tint_skin_image(base_rgb: np.ndarray, target_hex: str) -> np.ndarray:
    """Gently tint native painted skin toward target_hex while preserving shading."""
    target = hex_to_rgb01(target_hex)
    img = base_rgb.astype(np.float64) / 255.0
    # Sample clean cheek skin region on Quaternius UV (top-left face island)
    h, w = img.shape[:2]
    cheek = img[int(0.19 * h) : int(0.24 * h), int(0.08 * w) : int(0.13 * w)]
    ref = np.median(cheek.reshape(-1, 3), axis=0)
    gain = np.clip(target / np.maximum(ref, 0.08), 0.72, 1.38)
    # Slightly soften gain so rosy cheeks, lips, and eye shadows stay vivid
    gain = 0.25 + 0.75 * gain
    tinted = np.clip(img * gain[None, None, :], 0.0, 1.0)
    return (tinted * 255.0).astype(np.uint8)


def paint_outfit_textures(
    base_skin_path: Path,
    base_normal_path: Path,
    base_rough_path: Path,
    uv_maps: dict[str, np.ndarray],
    lm: dict[str, float],
    skin_tint: str,
    outfit: dict[str, Any],
) -> tuple[Image.Image, Image.Image, Image.Image]:
    """Paint crisp 2048x2048 outfit albedo, normal, and metallic-roughness maps."""
    base_albedo = np.array(Image.open(base_skin_path).convert("RGB"))
    base_normal = np.array(Image.open(base_normal_path).convert("RGB"))
    base_rough = np.array(Image.open(base_rough_path).convert("RGB"))
    size = base_albedo.shape[0]

    albedo = _tint_skin_image(base_albedo, skin_tint).astype(np.float32) / 255.0
    normal_f = base_normal.astype(np.float32) / 255.0
    rough_f = base_rough.astype(np.float32) / 255.0

    style = str(outfit.get("style", "jumpsuit")).lower()
    c_jacket = hex_to_rgb01(outfit.get("jacket_color", "#2b5998")).astype(np.float32)
    c_accent = hex_to_rgb01(outfit.get("accent_color", "#e87a24")).astype(np.float32)
    c_collar = hex_to_rgb01(outfit.get("collar_color", "#e87a24")).astype(np.float32)
    c_pants = hex_to_rgb01(outfit.get("pants_color", "#234373")).astype(np.float32)
    c_boots = hex_to_rgb01(outfit.get("boots_color", "#1c2028")).astype(np.float32)
    c_belt = hex_to_rgb01(outfit.get("belt_color", "#232830")).astype(np.float32)
    c_shirt = hex_to_rgb01(outfit.get("shirt_color", "#e8ecf2")).astype(np.float32)

    P = uv_maps["pos"]
    N = uv_maps["nrm"]
    X = P[:, :, 0]
    Y = P[:, :, 1]
    Z = P[:, :, 2]
    AX = np.abs(X)
    cloth_mask = np.clip(
        smoothstep(0.35, 0.65, uv_maps["cloth"]) * (1.0 - smoothstep(0.45, 0.70, uv_maps["skin"])),
        0.0,
        1.0,
    )
    valid = uv_maps["valid"]
    cloth_mask = np.where(valid, cloth_mask, 0.0).astype(np.float32)

    # Subtle twill fabric micro-weave + height field for crisp seam normals
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    weave = (
        0.5 * np.sin((xx + yy) * 0.85) + 0.5 * np.sin((xx - yy) * 0.85)
    ) * 0.018
    cloth_rgb = np.zeros((size, size, 3), dtype=np.float32)
    height_field = weave * 0.35
    cloth_rough = np.full((size, size), 0.86, dtype=np.float32)
    cloth_metal = np.zeros((size, size), dtype=np.float32)

    # Base upper vs lower garment split
    hem_y = lm["waist_y"] - 0.055
    if style == "labcoat":
        hem_y = lm["knee_y"] + 0.14
    elif style == "tactical":
        hem_y = lm["waist_y"] - 0.085

    is_upper = smoothstep(hem_y - 0.008, hem_y + 0.008, Y)
    is_boot = smoothstep(lm["boot_top_y"] + 0.006, lm["boot_top_y"] - 0.006, Y)
    is_front = smoothstep(-0.01, 0.03, Z - lm["neck_z"])
    is_back = 1.0 - is_front
    is_arm = smoothstep(0.35, 0.65, uv_maps["arm"])

    # Fill pants then jacket on top
    cloth_rgb[:] = c_pants[None, None, :]
    cloth_rgb = cloth_rgb * (1.0 - is_upper[:, :, None]) + c_jacket[None, None, :] * is_upper[:, :, None]

    # Subtle tonal shading from surface curvature & vertical AO
    ao_shade = 0.92 + 0.10 * np.clip(N[:, :, 1] * 0.5 + 0.5, 0.0, 1.0)
    # Armpit / inner thigh soft shadow
    inner_leg = (1.0 - smoothstep(0.015, 0.085, AX)) * (1.0 - is_upper)
    ao_shade -= 0.14 * inner_leg
    cloth_rgb *= ao_shade[:, :, None]

    # ---- Style-specific upper-body details ----
    if style == "jumpsuit":
        # Shoulder yoke / upper chest contrast panel
        yoke = (
            smoothstep(lm["chest_y"] + 0.055, lm["chest_y"] + 0.065, Y)
            * is_upper
            * (1.0 - is_arm * 0.5)
        )
        cloth_rgb = cloth_rgb * (1.0 - 0.22 * yoke[:, :, None]) + (c_jacket * 0.72)[None, None, :] * (0.22 * yoke[:, :, None])
        # Crisp accent chest stripe across upper chest (front & back)
        stripe = (
            smoothstep(lm["chest_y"] + 0.042, lm["chest_y"] + 0.047, Y)
            * (1.0 - smoothstep(lm["chest_y"] + 0.062, lm["chest_y"] + 0.067, Y))
            * (1.0 - is_arm)
        )
        cloth_rgb = cloth_rgb * (1.0 - stripe[:, :, None]) + c_accent[None, None, :] * stripe[:, :, None]
        height_field += 0.25 * stripe
        # Vertical center front zipper seam
        zip_seam = np.exp(-0.5 * (X / 0.0045) ** 2) * is_front * smoothstep(lm["waist_y"] - 0.12, lm["waist_y"] - 0.08, Y)
        zip_flank = (np.exp(-0.5 * ((AX - 0.011) / 0.0022) ** 2)) * is_front * is_upper
        cloth_rgb *= (1.0 - 0.55 * zip_seam[:, :, None])
        cloth_rgb *= (1.0 - 0.20 * zip_flank[:, :, None])
        cloth_rough = np.where(zip_seam > 0.4, 0.35, cloth_rough)
        cloth_metal = np.where(zip_seam > 0.4, 0.55, cloth_metal)
        height_field -= 0.45 * zip_seam
        # Chest utility pockets + left-chest bright mission badge
        pocket = (
            smoothstep(0.045, 0.050, AX)
            * (1.0 - smoothstep(0.125, 0.130, AX))
            * smoothstep(lm["chest_y"] - 0.055, lm["chest_y"] - 0.048, Y)
            * (1.0 - smoothstep(lm["chest_y"] + 0.015, lm["chest_y"] + 0.022, Y))
            * is_front
            * (1.0 - is_arm)
        )
        cloth_rgb = cloth_rgb * (1.0 - 0.28 * pocket[:, :, None]) + (c_jacket * 0.78)[None, None, :] * (0.28 * pocket[:, :, None])
        height_field += 0.35 * pocket
        badge = (
            smoothstep(0.052, 0.056, X)
            * (1.0 - smoothstep(0.118, 0.122, X))
            * smoothstep(lm["chest_y"] - 0.008, lm["chest_y"] - 0.004, Y)
            * (1.0 - smoothstep(lm["chest_y"] + 0.024, lm["chest_y"] + 0.028, Y))
            * is_front
            * (1.0 - is_arm)
        )
        cloth_rgb = cloth_rgb * (1.0 - badge[:, :, None]) + c_accent[None, None, :] * badge[:, :, None]
        height_field += 0.35 * badge

    elif style in ("jacket", "labcoat"):
        # Open front showing the undershirt. Jacket stops cleanly above the belt;
        # the labcoat opening continues through the coat hem.
        above_belt = smoothstep(lm["waist_y"] + 0.008, lm["waist_y"] + 0.018, Y) if style == "jacket" else is_upper
        if style == "jacket":
            # Straight vertical lapels. x_3d is interpolated sculpted rest-pose world
            # X (the bind pose that renders), so abs(x_3d) < lapel_half_width is a
            # plane. A Y-varying opening on the pre-sculpt chest followed the pecs
            # and read as a wavy shirt contour.
            lapel_half_width = 0.050
            x_3d = uv_maps["x_3d"] if "x_3d" in uv_maps else X
            ax_cut = np.abs(x_3d)
            in_shirt = (
                (1.0 - smoothstep(lapel_half_width - 0.0012, lapel_half_width + 0.0012, ax_cut))
                * is_front
                * above_belt
            )
            lapel = (
                smoothstep(lapel_half_width + 0.0008, lapel_half_width + 0.0022, ax_cut)
                * (1.0 - smoothstep(lapel_half_width + 0.009, lapel_half_width + 0.012, ax_cut))
                * is_front
                * above_belt
            )
            seam_shadow = (
                np.exp(-0.5 * ((ax_cut - lapel_half_width) / 0.0016) ** 2) * is_front * above_belt
            )
        else:
            placket_w = 0.046
            v_open = placket_w + 0.020 * smoothstep(lm["chest_y"] + 0.02, lm["collar_bot_y"], Y)
            in_shirt = (1.0 - smoothstep(v_open - 0.004, v_open + 0.004, AX)) * is_front * above_belt
            lapel = (
                smoothstep(v_open - 0.003, v_open + 0.003, AX)
                * (1.0 - smoothstep(v_open + 0.026, v_open + 0.032, AX))
                * is_front
                * above_belt
            )
            seam_shadow = np.exp(-0.5 * ((AX - v_open) / 0.004) ** 2) * is_front * above_belt
        cloth_rgb = cloth_rgb * (1.0 - in_shirt[:, :, None]) + c_shirt[None, None, :] * in_shirt[:, :, None]
        cloth_rgb = cloth_rgb * (1.0 - 0.22 * lapel[:, :, None]) + (c_jacket * 0.78)[None, None, :] * (0.22 * lapel[:, :, None])
        seam_strength = 0.72 if style == "jacket" else 0.50
        cloth_rgb *= (1.0 - seam_strength * seam_shadow[:, :, None])
        height_field += 0.40 * lapel - 0.55 * seam_shadow
        # Chest patch / ID badge on left chest
        badge = (
            smoothstep(0.068, 0.072, X)
            * (1.0 - smoothstep(0.128, 0.132, X))
            * smoothstep(lm["chest_y"] - 0.010, lm["chest_y"] - 0.005, Y)
            * (1.0 - smoothstep(lm["chest_y"] + 0.025, lm["chest_y"] + 0.030, Y))
            * is_front
            * (1.0 - is_arm)
        )
        if style == "jacket":
            badge = badge * (ax_cut >= lapel_half_width)
        cloth_rgb = cloth_rgb * (1.0 - badge[:, :, None]) + c_accent[None, None, :] * badge[:, :, None]
        height_field += 0.35 * badge
        # Lower coat pockets
        if style == "labcoat":
            lpocket = (
                smoothstep(0.065, 0.070, AX)
                * (1.0 - smoothstep(0.155, 0.160, AX))
                * smoothstep(lm["waist_y"] - 0.14, lm["waist_y"] - 0.13, Y)
                * (1.0 - smoothstep(lm["waist_y"] - 0.03, lm["waist_y"] - 0.02, Y))
                * is_front
            )
            cloth_rgb *= (1.0 - 0.08 * lpocket[:, :, None])
            # Coat hem edge dark shadow line
            hem_line = np.exp(-0.5 * ((Y - hem_y) / 0.006) ** 2)
            cloth_rgb *= (1.0 - 0.35 * hem_line[:, :, None])
            height_field += 0.30 * lpocket - 0.35 * hem_line
            # Accent piping along labcoat lapel & cuffs
            piping = np.exp(-0.5 * ((AX - v_open - 0.025) / 0.0035) ** 2) * is_front * is_upper
            cloth_rgb = cloth_rgb * (1.0 - 0.75 * piping[:, :, None]) + c_accent[None, None, :] * (0.75 * piping[:, :, None])

    elif style == "tactical":
        # Structured commander chest yoke + gold piping + shoulder epaulets
        yoke = smoothstep(lm["chest_y"] + 0.05, lm["chest_y"] + 0.06, Y) * is_upper
        cloth_rgb = cloth_rgb * (1.0 - 0.25 * yoke[:, :, None]) + (c_jacket * 0.76)[None, None, :] * (0.25 * yoke[:, :, None])
        # Center buttoned/zipped storm flap
        flap = (1.0 - smoothstep(0.018, 0.024, AX)) * is_front * is_upper
        cloth_rgb = cloth_rgb * (1.0 - 0.18 * flap[:, :, None]) + (c_jacket * 0.80)[None, None, :] * (0.18 * flap[:, :, None])
        flap_edge = np.exp(-0.5 * ((AX - 0.021) / 0.0035) ** 2) * is_front * is_upper
        cloth_rgb *= (1.0 - 0.40 * flap_edge[:, :, None])
        height_field += 0.25 * flap - 0.35 * flap_edge
        # Gold commander trim bars on chest & shoulders
        gold_bar = (
            smoothstep(lm["chest_y"] + 0.044, lm["chest_y"] + 0.048, Y)
            * (1.0 - smoothstep(lm["chest_y"] + 0.058, lm["chest_y"] + 0.062, Y))
            * is_front
            * (1.0 - is_arm)
        )
        epaulet = (
            smoothstep(0.11, 0.13, AX)
            * (1.0 - smoothstep(0.20, 0.22, AX))
            * smoothstep(lm["shoulder_y"] - 0.02, lm["shoulder_y"] + 0.01, Y)
            * smoothstep(0.3, 0.7, N[:, :, 1])
        )
        trim = np.clip(np.maximum(gold_bar, epaulet), 0.0, 1.0)
        cloth_rgb = cloth_rgb * (1.0 - trim[:, :, None]) + c_accent[None, None, :] * trim[:, :, None]
        cloth_rough = np.where(trim > 0.4, 0.42, cloth_rough)
        cloth_metal = np.where(trim > 0.4, 0.45, cloth_metal)
        height_field += 0.30 * trim
        # Rank insignia bar on left chest
        rank = (
            smoothstep(0.055, 0.060, X)
            * (1.0 - smoothstep(0.115, 0.120, X))
            * smoothstep(lm["chest_y"] - 0.005, lm["chest_y"], Y)
            * (1.0 - smoothstep(lm["chest_y"] + 0.016, lm["chest_y"] + 0.021, Y))
            * is_front
            * (1.0 - is_arm)
        )
        cloth_rgb = cloth_rgb * (1.0 - rank[:, :, None]) + c_accent[None, None, :] * rank[:, :, None]

    else:  # casual
        # Horizontal chest accent stripe
        stripe = (
            smoothstep(lm["chest_y"] - 0.015, lm["chest_y"] - 0.010, Y)
            * (1.0 - smoothstep(lm["chest_y"] + 0.020, lm["chest_y"] + 0.025, Y))
            * is_upper
            * (1.0 - is_arm)
        )
        cloth_rgb = cloth_rgb * (1.0 - stripe[:, :, None]) + c_accent[None, None, :] * stripe[:, :, None]

    # ---- Collar trim ----
    collar_band = (
        smoothstep(lm["collar_bot_y"] - 0.006, lm["collar_bot_y"] + 0.006, Y)
        * (1.0 - smoothstep(lm["collar_top_y"] + 0.012, lm["collar_top_y"] + 0.022, Y))
        * (1.0 - smoothstep(0.070, 0.098, np.hypot(X, Z - lm["neck_z"])))
    )
    cloth_rgb = cloth_rgb * (1.0 - collar_band[:, :, None]) + c_collar[None, None, :] * collar_band[:, :, None]
    collar_seam = np.exp(-0.5 * ((Y - lm["collar_bot_y"]) / 0.004) ** 2) * (1.0 - smoothstep(0.075, 0.105, np.hypot(X, Z - lm["neck_z"])))
    cloth_rgb *= (1.0 - 0.35 * collar_seam[:, :, None])
    height_field += 0.40 * collar_band - 0.35 * collar_seam

    # ---- Sleeve cuff trim ----
    cuff_band = (
        smoothstep(lm["cuff_x"] - 0.042, lm["cuff_x"] - 0.036, AX)
        * (1.0 - smoothstep(lm["cuff_x"] - 0.008, lm["cuff_x"] - 0.002, AX))
        * is_arm
    )
    cloth_rgb = cloth_rgb * (1.0 - cuff_band[:, :, None]) + c_accent[None, None, :] * cuff_band[:, :, None]
    height_field += 0.25 * cuff_band

    # ---- Waist belt + metallic buckle ----
    if style != "labcoat":
        belt = (
            smoothstep(lm["waist_y"] - 0.026, lm["waist_y"] - 0.020, Y)
            * (1.0 - smoothstep(lm["waist_y"] + 0.020, lm["waist_y"] + 0.026, Y))
            * (1.0 - is_arm)
        )
        belt_shadow = (
            np.exp(-0.5 * ((Y - (lm["waist_y"] - 0.026)) / 0.005) ** 2)
            + np.exp(-0.5 * ((Y - (lm["waist_y"] + 0.026)) / 0.005) ** 2)
        ) * (1.0 - is_arm)
        cloth_rgb = cloth_rgb * (1.0 - belt[:, :, None]) + c_belt[None, None, :] * belt[:, :, None]
        cloth_rgb *= (1.0 - 0.38 * np.clip(belt_shadow, 0.0, 1.0)[:, :, None])
        cloth_rough = np.where(belt > 0.4, 0.52, cloth_rough)
        height_field += 0.55 * belt - 0.40 * np.clip(belt_shadow, 0.0, 1.0)
        # Metallic belt buckle
        buckle = (
            (1.0 - smoothstep(0.028, 0.033, AX))
            * smoothstep(lm["waist_y"] - 0.021, lm["waist_y"] - 0.017, Y)
            * (1.0 - smoothstep(lm["waist_y"] + 0.017, lm["waist_y"] + 0.021, Y))
            * is_front
        )
        buckle_col = np.array([0.72, 0.74, 0.78], dtype=np.float32)
        if style == "tactical":
            buckle_col = c_accent
        cloth_rgb = cloth_rgb * (1.0 - buckle[:, :, None]) + buckle_col[None, None, :] * buckle[:, :, None]
        cloth_rough = np.where(buckle > 0.4, 0.28, cloth_rough)
        cloth_metal = np.where(buckle > 0.4, 0.78, cloth_metal)
        height_field += 0.40 * buckle

    # ---- Cargo pants pockets + knee pads ----
    thigh_mid = 0.5 * (lm["thigh_y"] + lm["knee_y"])
    cargo_pocket = (
        smoothstep(0.145, 0.160, AX)
        * smoothstep(thigh_mid - 0.06, thigh_mid - 0.05, Y)
        * (1.0 - smoothstep(thigh_mid + 0.06, thigh_mid + 0.07, Y))
        * (1.0 - is_upper)
        * (1.0 - is_boot)
    )
    knee_pad = (
        smoothstep(lm["knee_y"] - 0.055, lm["knee_y"] - 0.045, Y)
        * (1.0 - smoothstep(lm["knee_y"] + 0.055, lm["knee_y"] + 0.065, Y))
        * is_front
        * (1.0 - is_upper)
        * (1.0 - is_boot)
    )
    cloth_rgb = cloth_rgb * (1.0 - 0.18 * cargo_pocket[:, :, None]) + (c_pants * 0.78)[None, None, :] * (0.18 * cargo_pocket[:, :, None])
    cloth_rgb = cloth_rgb * (1.0 - 0.22 * knee_pad[:, :, None]) + (c_pants * 0.72)[None, None, :] * (0.22 * knee_pad[:, :, None])
    height_field += 0.30 * cargo_pocket + 0.25 * knee_pad

    # ---- Boots (leather upper, rubber sole, toe cap, ankle cuff shadow) ----
    cloth_rgb = cloth_rgb * (1.0 - is_boot[:, :, None]) + c_boots[None, None, :] * is_boot[:, :, None]
    cloth_rough = np.where(is_boot > 0.5, 0.56, cloth_rough)
    boot_cuff_seam = np.exp(-0.5 * ((Y - lm["boot_top_y"]) / 0.006) ** 2)
    cloth_rgb *= (1.0 - 0.45 * boot_cuff_seam[:, :, None])
    height_field += 0.35 * is_boot - 0.45 * boot_cuff_seam
    # Rubber sole band at bottom of boots
    sole = 1.0 - smoothstep(lm["y_min"] + 0.028, lm["y_min"] + 0.036, Y)
    sole_col = np.clip(c_boots * 0.55, 0.04, 0.16)
    cloth_rgb = cloth_rgb * (1.0 - sole[:, :, None]) + sole_col[None, None, :] * sole[:, :, None]
    cloth_rough = np.where(sole > 0.5, 0.90, cloth_rough)
    # Subtle boot accent strap near ankle
    ankle_strap = (
        smoothstep(lm["boot_top_y"] - 0.042, lm["boot_top_y"] - 0.036, Y)
        * (1.0 - smoothstep(lm["boot_top_y"] - 0.018, lm["boot_top_y"] - 0.012, Y))
        * is_boot
    )
    cloth_rgb = cloth_rgb * (1.0 - 0.45 * ankle_strap[:, :, None]) + (c_boots * 0.65 + c_accent * 0.35)[None, None, :] * (0.45 * ankle_strap[:, :, None])

    if bool(outfit.get("ps2_profile", False)):
        PS2CharacterArtProfile.paint_fabric(
            cloth_rgb,
            height_field,
            cloth_rough,
            cloth_metal,
            lm,
            str(outfit.get("ps2_gender", "female")),
            X,
            Y,
            Z,
            AX,
            N[:, :, 1],
            is_upper,
            is_front,
            is_arm,
            is_boot,
        )

    # Add subtle fabric weave luminance variation
    cloth_rgb = np.clip(cloth_rgb + weave[:, :, None], 0.0, 1.0)

    # Composite clothed pixels cleanly over tinted native skin texture
    out_albedo = albedo * (1.0 - cloth_mask[:, :, None]) + cloth_rgb * cloth_mask[:, :, None]
    if bool(outfit.get("ps2_profile", False)):
        out_albedo = PS2CharacterArtProfile.bake_contact_ao(out_albedo, lm, X, Y, Z, AX)

    # Darken ONLY the interior mouth pocket UV island (v > 0.42, strictly separated from outer face UV v < 0.36)
    mouth_y_approx = lm["head_y"] + 0.030
    v_norm = yy / float(size)
    mouth_inner = (
        valid
        & (v_norm > 0.42)
        & (uv_maps["skin"] > 0.7)
        & (AX < 0.032)
        & (np.abs(Y - mouth_y_approx) < 0.025)
        & (Z > 0.050)
        & (Z < 0.084)
    )
    if np.any(mouth_inner):
        depth_w = smoothstep(0.084, 0.072, Z) * mouth_inner.astype(np.float32)
        dark_mouth = np.array([0.22, 0.09, 0.10], dtype=np.float32)[None, None, :]
        out_albedo = out_albedo * (1.0 - 0.85 * depth_w[:, :, None]) + dark_mouth * (0.85 * depth_w[:, :, None])

    # Build fabric normal map from height_field gradients and blend on clothed pixels
    gx = cv2.Sobel(height_field.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3) * 0.35
    gy = cv2.Sobel(height_field.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3) * 0.35
    nx = -gx
    ny = -gy
    nz = np.ones_like(nx)
    nlen = np.sqrt(nx * nx + ny * ny + nz * nz)
    cloth_nrm = np.stack(
        [0.5 + 0.5 * (nx / nlen), 0.5 + 0.5 * (ny / nlen), 0.5 + 0.5 * (nz / nlen)],
        axis=2,
    )
    # Damp bare-skin anatomy normal map on clothed pixels (keeps 100% native normal on face/neck/hands!)
    neutral_nrm = np.array([0.5, 0.5, 1.0], dtype=np.float32)[None, None, :]
    damped_base_nrm = normal_f * 0.15 + neutral_nrm * 0.85
    fab_nrm = np.clip(damped_base_nrm + (cloth_nrm - neutral_nrm), 0.0, 1.0)
    out_normal = normal_f * (1.0 - cloth_mask[:, :, None]) + fab_nrm * cloth_mask[:, :, None]

    # glTF metallicRoughnessTexture: G = roughness, B = metallic, R = 1.0
    out_rough = rough_f.copy()
    out_rough[:, :, 0] = 1.0
    out_rough[:, :, 1] = rough_f[:, :, 1] * (1.0 - cloth_mask) + cloth_rough * cloth_mask
    out_rough[:, :, 2] = 0.0 * (1.0 - cloth_mask) + cloth_metal * cloth_mask
    if bool(outfit.get("ps2_profile", False)):
        skin_only = np.clip(uv_maps["skin"], 0.0, 1.0) * (1.0 - cloth_mask)
        out_rough[:, :, 1] = np.clip(
            np.maximum(out_rough[:, :, 1], 0.78 * skin_only + out_rough[:, :, 1] * (1.0 - skin_only)),
            0.0,
            1.0,
        )

    return (
        Image.fromarray(np.clip(out_albedo * 255.0, 0, 255).astype(np.uint8), "RGB"),
        Image.fromarray(np.clip(out_normal * 255.0, 0, 255).astype(np.uint8), "RGB"),
        Image.fromarray(np.clip(out_rough * 255.0, 0, 255).astype(np.uint8), "RGB"),
    )


def _tint_hair_image(hair_path: Path, hair_hex: str) -> Image.Image:
    """Tint a Quaternius strand-shaded hair texture while preserving highlights."""
    im = np.array(Image.open(hair_path).convert("RGB")).astype(np.float64) / 255.0
    lum = 0.299 * im[:, :, 0] + 0.587 * im[:, :, 1] + 0.114 * im[:, :, 2]
    med = max(float(np.median(lum)), 0.15)
    # Normalize strand contrast around 1.0 with crisp glossy variation
    shade = np.clip(0.55 + 0.65 * (lum / med), 0.35, 1.45)
    target = hex_to_rgb01(hair_hex)
    # Prevent pure pitch-black crush so sculpted hair strands still read clearly
    target = np.maximum(target, np.array([0.055, 0.050, 0.062]))
    out = np.clip(target[None, None, :] * shade[:, :, None], 0.0, 1.0)
    return Image.fromarray((out * 255.0).astype(np.uint8), "RGB")


# ------------------------------------------------------------ morph targets


def _detect_mouth_slit_and_lower_mask(
    pos: np.ndarray,
    faces: np.ndarray,
    head_w: np.ndarray,
    eye_cy: float,
    eye_cz: float,
) -> tuple[float, float, float, np.ndarray]:
    """Locate exact mouth slit Y, lip front Z, mouth half-width, and smooth geodesic lower-lip weights."""
    front_head = (head_w > 0.35) & (pos[:, 2] > eye_cz - 0.02) & (np.abs(pos[:, 0]) < 0.065)
    fh_pos = pos[front_head]
    chin_y = float(fh_pos[:, 1].min())
    mid_head = (
        (head_w > 0.35)
        & (np.abs(pos[:, 0]) < 0.008)
        & (pos[:, 1] > chin_y + 0.025)
        & (pos[:, 1] < eye_cy - 0.045)
    )
    mpos = pos[mid_head]
    lip_z_front = float(np.percentile(mpos[:, 2], 85))
    pocket = mpos[(mpos[:, 2] < lip_z_front - 0.014) & (mpos[:, 2] > lip_z_front - 0.035)]
    if len(pocket) >= 2:
        mouth_y = float(np.median(pocket[:, 1]))
    else:
        mouth_y = chin_y + 0.37 * (eye_cy - chin_y)

    slit_band = (
        (head_w > 0.35)
        & (np.abs(pos[:, 1] - mouth_y) < 0.006)
        & (pos[:, 2] > lip_z_front - 0.022)
        & (np.abs(pos[:, 0]) < 0.042)
    )
    mouth_half_w = (
        float(np.percentile(np.abs(pos[slit_band, 0]), 92))
        if np.any(slit_band)
        else 0.028
    )
    mouth_half_w = float(np.clip(mouth_half_w, 0.024, 0.036))

    # Weld at 0.05mm so UV seams merge while the 0.2mm upper/lower lip slit stays strictly open!
    keys = np.ascontiguousarray(np.rint(pos * 2e4).astype(np.int64))
    packed = keys.view(np.dtype((np.void, keys.dtype.itemsize * 3))).ravel()
    _, inv = np.unique(packed, return_inverse=True)
    n_u = int(inv.max()) + 1
    upos = np.zeros((n_u, 3), dtype=np.float64)
    upos[inv] = pos
    wf = inv[faces]
    edges = np.vstack(
        [
            wf[:, [0, 1]],
            wf[:, [1, 2]],
            wf[:, [2, 0]],
            wf[:, [1, 0]],
            wf[:, [2, 1]],
            wf[:, [0, 2]],
        ]
    )
    elens = np.linalg.norm(upos[edges[:, 0]] - upos[edges[:, 1]], axis=1) + 1e-6
    graph = csr_matrix((elens, (edges[:, 0], edges[:, 1])), shape=(n_u, n_u))

    nose_seed = int(
        np.argmin(np.linalg.norm(upos - np.array([0.0, mouth_y + 0.016, lip_z_front]), axis=1))
    )
    chin_seed = int(
        np.argmin(np.linalg.norm(upos - np.array([0.0, mouth_y - 0.016, lip_z_front - 0.005]), axis=1))
    )
    d_up = shortest_path(graph, directed=False, indices=nose_seed)
    d_lo = shortest_path(graph, directed=False, indices=chin_seed)
    # Smooth geodesic transition: 1.0 on entire lower lip + chin, 0.0 on upper lip + nose!
    lower_lip_w = smoothstep(-0.004, 0.004, (d_up - d_lo)[inv])
    return mouth_y, lip_z_front, mouth_half_w, lower_lip_w


def compute_morph_targets(
    body_pos: np.ndarray,
    body_faces: np.ndarray,
    body_j0: np.ndarray,
    body_w0: np.ndarray,
    head_joint_idx: int,
    eyes_pos: np.ndarray,
    brows_pos: np.ndarray,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    """Compute the 7 standard BlendShapes for BOTH body and eyebrows meshes."""
    head_w = np.sum(body_w0 * (body_j0 == head_joint_idx), axis=1).astype(np.float64)
    head_gate = smoothstep(0.15, 0.60, head_w)

    # Eye centers & radii from 3D eyeball mesh
    le = eyes_pos[eyes_pos[:, 0] > 0.0]
    re = eyes_pos[eyes_pos[:, 0] < 0.0]
    eye_cx = 0.5 * (float(le[:, 0].mean()) - float(re[:, 0].mean()))
    eye_cy = 0.5 * (float(le[:, 1].mean()) + float(re[:, 1].mean()))
    eye_cz = 0.5 * (float(le[:, 2].mean()) + float(re[:, 2].mean()))
    eye_r = float(np.mean(0.5 * (le.max(0) - le.min(0))))

    mouth_y, lip_z, mouth_w, lower_lip_w = _detect_mouth_slit_and_lower_mask(
        body_pos, body_faces, head_w, eye_cy, eye_cz
    )
    front_head = (head_w > 0.35) & (body_pos[:, 2] > eye_cz - 0.02) & (np.abs(body_pos[:, 0]) < 0.065)
    chin_y = float(body_pos[front_head, 1].min())

    x = body_pos[:, 0]
    y = body_pos[:, 1]
    z = body_pos[:, 2]
    ax = np.abs(x)

    body_targets: dict[str, np.ndarray] = {}
    brow_targets: dict[str, np.ndarray] = {
        name: np.zeros_like(brows_pos, dtype=np.float64) for name in BLEND_SHAPE_NAMES
    }

    # ---- 1. jawOpen: hinge rotation of lower lip, mouth pocket & chin ----
    jaw_pivot = np.array([0.0, eye_cy - 0.032, eye_cz - 0.058], dtype=np.float64)
    lat_w = np.cos(0.5 * np.pi * np.clip(ax / 0.064, 0.0, 1.0)) ** 1.4
    z_w = smoothstep(eye_cz - 0.052, eye_cz - 0.008, z)
    y_bot = smoothstep(chin_y - 0.022, chin_y + 0.006, y)
    # Smooth geodesic lower-lip gate: 1.0 on lower lip/chin, 0.0 on upper lip/nose
    jaw_w = head_gate * lat_w * z_w * y_bot * lower_lip_w
    theta = math.radians(11.5)
    ct, st = math.cos(theta), math.sin(theta)
    rel = body_pos - jaw_pivot[None, :]
    rot_y = rel[:, 1] * ct - rel[:, 2] * st
    rot_z = rel[:, 1] * st + rel[:, 2] * ct
    jaw_delta = np.zeros_like(body_pos)
    jaw_delta[:, 1] = (rot_y - rel[:, 1]) * jaw_w
    jaw_delta[:, 2] = (rot_z - rel[:, 2]) * jaw_w
    lip_extra = (
        head_gate
        * lower_lip_w
        * np.exp(-0.5 * (ax / (mouth_w * 0.72)) ** 2)
        * np.exp(-0.5 * ((y - (mouth_y - 0.004)) / 0.011) ** 2)
        * smoothstep(lip_z - 0.025, lip_z - 0.003, z)
    )
    jaw_delta[:, 1] -= 0.0052 * lip_extra
    ulip_lift = (
        head_gate
        * (1.0 - lower_lip_w)
        * np.exp(-0.5 * (ax / (mouth_w * 0.65)) ** 2)
        * np.exp(-0.5 * ((y - (mouth_y + 0.005)) / 0.007) ** 2)
        * smoothstep(lip_z - 0.018, lip_z - 0.002, z)
    )
    jaw_delta[:, 1] += 0.0018 * ulip_lift
    body_targets["jawOpen"] = jaw_delta

    # ---- 2. mouthFunnel: purse lips inward/forward + slight O opening ----
    funnel_xy = (
        np.exp(-0.5 * (ax / (mouth_w * 1.05)) ** 2)
        * np.exp(-0.5 * ((y - mouth_y) / 0.015) ** 2)
        * smoothstep(lip_z - 0.024, lip_z - 0.003, z)
        * head_gate
    )
    funnel_delta = np.zeros_like(body_pos)
    funnel_delta[:, 0] = -x * 0.24 * funnel_xy
    funnel_delta[:, 2] = 0.0068 * funnel_xy
    center_o = np.exp(-0.5 * (ax / (mouth_w * 0.55)) ** 2) * funnel_xy
    funnel_delta[:, 1] = (
        -0.0045 * lower_lip_w + 0.0022 * (1.0 - lower_lip_w)
    ) * center_o
    body_targets["mouthFunnel"] = funnel_delta

    # ---- 3. mouthSmile: broad smooth curved grin + subtle cheek lift ----
    corner_env = (
        np.exp(-0.5 * ((ax - mouth_w * 1.02) / 0.020) ** 2)
        * np.exp(-0.5 * ((y - mouth_y) / 0.018) ** 2)
        * smoothstep(lip_z - 0.036, lip_z - 0.008, z)
        * head_gate
    )
    cheek_env = (
        np.exp(-0.5 * ((ax - 0.038) / 0.018) ** 2)
        * np.exp(-0.5 * ((y - (mouth_y + 0.026)) / 0.017) ** 2)
        * smoothstep(lip_z - 0.038, lip_z - 0.010, z)
        * head_gate
    )
    smile_delta = np.zeros_like(body_pos)
    smile_delta[:, 0] = np.sign(x) * (0.0030 * corner_env + 0.0010 * cheek_env)
    smile_delta[:, 1] = 0.0058 * corner_env + 0.0025 * cheek_env
    smile_delta[:, 2] = -0.0032 * corner_env + 0.0014 * cheek_env
    body_targets["mouthSmile"] = smile_delta

    # ---- 4. mouthFrown: smooth downward mouth-corner curve ----
    frown_delta = np.zeros_like(body_pos)
    frown_delta[:, 0] = -np.sign(x) * 0.0014 * corner_env
    frown_delta[:, 1] = -0.0054 * corner_env
    frown_delta[:, 2] = 0.0012 * corner_env
    frown_low = (
        head_gate
        * lower_lip_w
        * smoothstep(0.006, mouth_w * 0.85, ax)
        * (1.0 - smoothstep(mouth_w * 1.0, mouth_w * 1.45, ax))
        * np.exp(-0.5 * ((y - (mouth_y - 0.004)) / 0.013) ** 2)
        * smoothstep(lip_z - 0.024, lip_z - 0.005, z)
    )
    frown_delta[:, 1] -= 0.0018 * frown_low
    body_targets["mouthFrown"] = frown_delta

    # ---- 5. eyeBlink: upper eyelid glides smoothly OVER 3D eyeball sphere ----
    blink_delta = np.zeros_like(body_pos)
    nose_guard = smoothstep(0.013, 0.020, ax)
    for side in (-1.0, 1.0):
        ecx = side * eye_cx
        dx = x - ecx
        dy = y - eye_cy
        dz = z - eye_cz
        # Horizontal arch over eye socket (0 at corners and nose)
        hx = np.cos(0.5 * np.pi * np.clip(np.abs(dx) / (eye_r * 1.25), 0.0, 1.0)) ** 1.25
        # Only vertices on this eye side, front of socket
        z_eye = smoothstep(-eye_r * 0.35, eye_r * 0.35, dz)
        # Upper eyelid band: from eye center slightly below lid rim up to brow fold
        up_band = (
            smoothstep(-0.0025, 0.0020, dy)
            * (1.0 - smoothstep(0.0055, eye_r * 1.25, dy))
        )
        up_w = head_gate * nose_guard * hx * z_eye * up_band
        # Lower eyelid subtle lift
        lo_band = (
            smoothstep(-eye_r * 0.95, -0.0055, dy)
            * (1.0 - smoothstep(-0.0045, -0.0010, dy))
        )
        lo_w = head_gate * nose_guard * hx * z_eye * lo_band

        # Target closed position: upper lid drops to lower rim (~eye_cy - 0.0065)
        # while gliding OVER spherical eyeball shell (radius = eye_r + 0.0026)
        drop_y = -0.0118 * up_w + 0.0022 * lo_w
        cand_y = y + drop_y
        # Compute minimum Z needed to clear the eyeball sphere at (dx, cand_y - eye_cy)
        r_shell = eye_r + 0.0028
        r2_xy = dx * dx + (cand_y - eye_cy) ** 2
        inside_disc = r2_xy < (r_shell * r_shell)
        sphere_z = eye_cz + np.sqrt(np.maximum(r_shell * r_shell - r2_xy, 0.0))
        push_z = np.where(inside_disc & (up_w > 0.02), np.maximum(0.0, sphere_z - z), 0.0)
        # Blend push_z smoothly by lid weight so crease transitions gently
        lid_mask = np.clip(up_w * 2.2 + lo_w, 0.0, 1.0)
        blink_delta[:, 1] += drop_y
        blink_delta[:, 2] += push_z * lid_mask

    body_targets["eyeBlink"] = blink_delta
    # Very subtle brow drop on blink
    brow_targets["eyeBlink"][:, 1] = -0.0012

    # ---- 6 & 7. browDown and browUp (on BOTH Eyebrows mesh AND Body brow ridge) ----
    brow_cy = float(brows_pos[:, 1].mean())
    brow_cz = float(brows_pos[:, 2].mean())
    brow_max_x = float(np.abs(brows_pos[:, 0]).max())

    def _brow_deltas(pts: np.ndarray, is_body: bool) -> tuple[np.ndarray, np.ndarray]:
        px = pts[:, 0]
        py = pts[:, 1]
        pz = pts[:, 2]
        pax = np.abs(px)
        if is_body:
            env = (
                head_gate
                * np.exp(-0.5 * ((py - brow_cy) / 0.015) ** 2)
                * (1.0 - smoothstep(brow_max_x * 0.85, brow_max_x * 1.30, pax))
                * smoothstep(brow_cz - 0.032, brow_cz - 0.006, pz)
                # Protect eyes below brow ridge
                * smoothstep(eye_cy + 0.006, eye_cy + 0.017, py)
            )
        else:
            env = np.ones(len(pts), dtype=np.float64)

        inner_w = 1.0 - smoothstep(0.014, brow_max_x * 0.85, pax)
        arch_w = np.exp(-0.5 * ((pax - brow_max_x * 0.55) / (brow_max_x * 0.45)) ** 2)

        down = np.zeros_like(pts, dtype=np.float64)
        down[:, 1] = -(0.0060 * inner_w + 0.0022 * (1.0 - inner_w)) * env
        down[:, 0] = -np.sign(px) * 0.0022 * inner_w * env
        down[:, 2] = 0.0016 * inner_w * env

        up = np.zeros_like(pts, dtype=np.float64)
        up[:, 1] = (0.0052 * inner_w + 0.0068 * arch_w) * env
        up[:, 2] = 0.0008 * env
        return down, up

    b_down, b_up = _brow_deltas(body_pos, is_body=True)
    e_down, e_up = _brow_deltas(brows_pos, is_body=False)
    body_targets["browDown"] = b_down
    body_targets["browUp"] = b_up
    brow_targets["browDown"] = e_down
    brow_targets["browUp"] = e_up

    return body_targets, brow_targets


# ----------------------------------------------------------- hair & ponytail


def _build_ponytail_lock(
    attach_center: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Build a sculpted curved cascading 3D ponytail mesh with strand UVs."""
    # Spine curve starting at back crown going slightly up-out then cascading smoothly down
    n_rings = 22
    n_seg = 16
    ts = np.linspace(0.0, 1.0, n_rings)

    centers = np.zeros((n_rings, 3), dtype=np.float64)
    radii_x = np.zeros(n_rings, dtype=np.float64)
    radii_z = np.zeros(n_rings, dtype=np.float64)
    for i, t in enumerate(ts):
        # Arc back and down smoothly behind the neck/upper back
        dy = 0.018 * math.sin(min(t * 2.5, 1.0) * math.pi * 0.5) - 0.255 * (t**1.35)
        dz = -0.052 * math.sin(min(t * 1.4, 1.0) * math.pi * 0.55) - 0.028 * t + 0.015 * (t**2)
        centers[i] = attach_center + np.array([0.0, dy, dz])
        # Pinch at hair-tie base (t~0.08), swell into full volume (t~0.35), taper to tip
        if t < 0.10:
            r = 0.018 + 0.006 * (1.0 - t / 0.10)
        else:
            u = (t - 0.10) / 0.90
            r = 0.018 + 0.025 * math.sin(u * math.pi) ** 0.75 * (1.0 - 0.35 * u)
            r *= max(0.06, 1.0 - u**2.2)
        radii_x[i] = r * 1.12
        radii_z[i] = r * 0.92

    # Compute tangent frames along spine
    verts = []
    uvs = []
    for i in range(n_rings):
        if i == 0:
            tang = centers[1] - centers[0]
        elif i == n_rings - 1:
            tang = centers[-1] - centers[-2]
        else:
            tang = centers[i + 1] - centers[i - 1]
        tang = tang / max(np.linalg.norm(tang), 1e-8)
        right = np.array([1.0, 0.0, 0.0])
        fwd = np.cross(right, tang)
        fwd = fwd / max(np.linalg.norm(fwd), 1e-8)
        right = np.cross(tang, fwd)

        t = ts[i]
        for s in range(n_seg + 1):
            ang = 2.0 * math.pi * (s / n_seg)
            # Subtle fluted hair-strand ridges along the ponytail
            flute = 1.0 + 0.10 * math.sin(ang * 5.0 + t * 2.0) * math.sin(t * math.pi)
            vx = math.cos(ang) * radii_x[i] * flute
            vz = math.sin(ang) * radii_z[i] * flute
            pt = centers[i] + right * vx + fwd * vz
            verts.append(pt)
            uvs.append([s / n_seg, 0.05 + 0.90 * t])

    verts_arr = np.asarray(verts, dtype=np.float64)
    uvs_arr = np.asarray(uvs, dtype=np.float64)
    stride = n_seg + 1
    faces = []
    for i in range(n_rings - 1):
        for s in range(n_seg):
            a = i * stride + s
            b = a + 1
            c = (i + 1) * stride + s
            d = c + 1
            faces.append([a, c, b])
            faces.append([b, c, d])
    faces_arr = np.asarray(faces, dtype=np.int64)
    nrms_arr = compute_welded_normals(verts_arr, faces_arr)
    return verts_arr, nrms_arr, uvs_arr, faces_arr


def _load_hair_mesh(
    hair_style: str,
    base_gender: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, Path, Path]:
    """Load hair geometry in world space aligned to the target gender's head."""
    key = hair_style.lower()
    if key == "buzzed":
        key = "buzzed_female" if base_gender == "female" else "buzzed_male"
    if key not in _HAIR_SPECS:
        raise ValueError(f"unsupported hair style '{hair_style}' (choose long|ponytail|parted|buns|buzzed)")
    fname, authored_gender = _HAIR_SPECS[key]
    hdoc, hblob = load_gltf(QUATERNIUS_DIR / fname)
    wm, _ = compute_world_matrices(hdoc["nodes"])
    m_node = [i for i, n in enumerate(hdoc["nodes"]) if "mesh" in n][0]
    prim = hdoc["meshes"][0]["primitives"][0]
    raw_pos = read_accessor(hdoc, hblob, prim["attributes"]["POSITION"]).astype(np.float64)
    raw_nrm = read_accessor(hdoc, hblob, prim["attributes"]["NORMAL"]).astype(np.float64)
    uv = read_accessor(hdoc, hblob, prim["attributes"]["TEXCOORD_0"]).astype(np.float64)
    faces = read_accessor(hdoc, hblob, prim["indices"]).reshape(-1, 3).astype(np.int64)

    W = wm[m_node]
    pos = (W[:3, :3] @ raw_pos.T).T + W[:3, 3]
    nrm_mat = np.linalg.inv(W[:3, :3]).T
    nrm = (nrm_mat @ raw_nrm.T).T
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-8)

    # For "ponytail": strip the two side buns from Hair_Buns and attach a sculpted ponytail
    if hair_style.lower() == "ponytail":
        import trimesh

        # Group faces by connected components on welded positions
        keys = np.ascontiguousarray(np.rint(pos * 1e4).astype(np.int64))
        packed = keys.view(np.dtype((np.void, keys.dtype.itemsize * 3))).ravel()
        _, inv = np.unique(packed, return_inverse=True)
        n_u = int(inv.max()) + 1
        # Union-find over welded vertices
        parent_uf = np.arange(n_u, dtype=np.int64)

        def find(a: int) -> int:
            while parent_uf[a] != a:
                parent_uf[a] = parent_uf[parent_uf[a]]
                a = parent_uf[a]
            return a

        wf = inv[faces]
        for e0, e1 in np.vstack([wf[:, [0, 1]], wf[:, [1, 2]], wf[:, [2, 0]]]):
            r0, r1 = find(int(e0)), find(int(e1))
            if r0 != r1:
                parent_uf[r1] = r0
        roots = np.array([find(i) for i in range(n_u)], dtype=np.int64)
        face_comp = roots[wf[:, 0]]
        keep_face = np.ones(len(faces), dtype=bool)
        for cid in np.unique(face_comp):
            fmask = face_comp == cid
            v_ids = np.unique(faces[fmask])
            cx = float(pos[v_ids, 0].mean())
            cz = float(pos[v_ids, 2].mean())
            # Side buns sit at |cx| > 0.08 and cz < -0.05
            if abs(cx) > 0.075 and cz < -0.045:
                keep_face[fmask] = False
        faces = faces[keep_face]
        # Compact vertices
        used = np.unique(faces)
        remap = np.full(len(pos), -1, dtype=np.int64)
        remap[used] = np.arange(len(used))
        pos = pos[used]
        nrm = nrm[used]
        uv = uv[used]
        faces = remap[faces]
        # Attach sculpted high ponytail to back crown
        pt_v, pt_n, pt_uv, pt_f = _build_ponytail_lock(
            np.array([0.0, 1.695, -0.105], dtype=np.float64)
        )
        offset = len(pos)
        pos = np.vstack([pos, pt_v])
        nrm = np.vstack([nrm, pt_n])
        uv = np.vstack([uv, pt_uv])
        faces = np.vstack([faces, pt_f + offset])

    # Cross-gender alignment if hair was authored for the other base gender
    if authored_gender != base_gender:
        delta = (
            (_MALE_HEAD_ANCHOR - _FEMALE_HEAD_ANCHOR)
            if base_gender == "male"
            else (_FEMALE_HEAD_ANCHOR - _MALE_HEAD_ANCHOR)
        )
        pos = pos + delta[None, :]

    # Resolve hair textures from source glTF
    mat = hdoc["materials"][0]
    texs = hdoc["textures"]
    imgs = hdoc["images"]
    albedo_uri = imgs[texs[mat["pbrMetallicRoughness"]["baseColorTexture"]["index"]]["source"]]["uri"]
    normal_uri = imgs[texs[mat["normalTexture"]["index"]]["source"]]["uri"]
    return pos, nrm, uv, faces, QUATERNIUS_DIR / albedo_uri, QUATERNIUS_DIR / normal_uri


def _load_beard_mesh(
    base_gender: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, Path, Path]:
    hdoc, hblob = load_gltf(QUATERNIUS_DIR / "Hair_Beard.gltf")
    wm, _ = compute_world_matrices(hdoc["nodes"])
    prim = hdoc["meshes"][0]["primitives"][0]
    raw_pos = read_accessor(hdoc, hblob, prim["attributes"]["POSITION"]).astype(np.float64)
    raw_nrm = read_accessor(hdoc, hblob, prim["attributes"]["NORMAL"]).astype(np.float64)
    uv = read_accessor(hdoc, hblob, prim["attributes"]["TEXCOORD_0"]).astype(np.float64)
    faces = read_accessor(hdoc, hblob, prim["indices"]).reshape(-1, 3).astype(np.int64)
    W = wm[0]
    pos = (W[:3, :3] @ raw_pos.T).T + W[:3, 3]
    nrm = (np.linalg.inv(W[:3, :3]).T @ raw_nrm.T).T
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-8)
    if base_gender == "female":
        pos = pos + (_FEMALE_HEAD_ANCHOR - _MALE_HEAD_ANCHOR)[None, :]
    mat = hdoc["materials"][0]
    texs = hdoc["textures"]
    imgs = hdoc["images"]
    albedo_uri = imgs[texs[mat["pbrMetallicRoughness"]["baseColorTexture"]["index"]]["source"]]["uri"]
    normal_uri = imgs[texs[mat["normalTexture"]["index"]]["source"]]["uri"]
    return pos, nrm, uv, faces, QUATERNIUS_DIR / albedo_uri, QUATERNIUS_DIR / normal_uri


# --------------------------------------------------------------- GLB builder


class _GLBBuilder:
    def __init__(self) -> None:
        self.blob = bytearray()
        self.buffer_views: list[dict] = []
        self.accessors: list[dict] = []
        self.images: list[dict] = []
        self.textures: list[dict] = []
        self.samplers: list[dict] = [
            {"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}
        ]
        self.materials: list[dict] = []
        self.meshes: list[dict] = []

    def _pad4(self) -> None:
        rem = len(self.blob) % 4
        if rem:
            self.blob.extend(b"\x00" * (4 - rem))

    def add_bytes(self, data: bytes, target: int | None = None) -> int:
        self._pad4()
        off = len(self.blob)
        self.blob.extend(data)
        bv: dict[str, Any] = {
            "buffer": 0,
            "byteOffset": off,
            "byteLength": len(data),
        }
        if target is not None:
            bv["target"] = target
        self.buffer_views.append(bv)
        return len(self.buffer_views) - 1

    def add_accessor(
        self,
        array: np.ndarray,
        component_type: int,
        acc_type: str,
        *,
        target: int | None = None,
        with_minmax: bool = False,
    ) -> int:
        dt = np.dtype(_DTYPES[component_type])
        arr = np.ascontiguousarray(array, dtype=dt)
        bv_idx = self.add_bytes(arr.tobytes(), target=target)
        acc: dict[str, Any] = {
            "bufferView": bv_idx,
            "byteOffset": 0,
            "componentType": component_type,
            "count": int(arr.shape[0]),
            "type": acc_type,
        }
        if with_minmax and len(arr) > 0:
            if arr.ndim == 1:
                acc["min"] = [float(arr.min())]
                acc["max"] = [float(arr.max())]
            else:
                acc["min"] = [float(v) for v in arr.min(axis=0)]
                acc["max"] = [float(v) for v in arr.max(axis=0)]
        self.accessors.append(acc)
        return len(self.accessors) - 1

    def add_image_png(self, img: Image.Image, name: str) -> int:
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=False)
        bv_idx = self.add_bytes(buf.getvalue())
        self.images.append(
            {"bufferView": bv_idx, "mimeType": "image/png", "name": name}
        )
        img_idx = len(self.images) - 1
        self.textures.append({"sampler": 0, "source": img_idx})
        return len(self.textures) - 1

    def add_skinned_mesh(
        self,
        name: str,
        pos: np.ndarray,
        nrm: np.ndarray,
        uv: np.ndarray,
        faces: np.ndarray,
        joints: np.ndarray,
        weights: np.ndarray,
        material_idx: int,
        morph_targets: dict[str, np.ndarray] | None = None,
    ) -> int:
        pos32 = np.ascontiguousarray(pos, dtype=np.float32)
        nrm32 = np.ascontiguousarray(nrm, dtype=np.float32)
        uv32 = np.ascontiguousarray(uv, dtype=np.float32)
        flat_idx = np.ascontiguousarray(faces.reshape(-1), dtype=np.uint32)
        idx_ct = 5123 if int(flat_idx.max()) < 65535 else 5125
        if idx_ct == 5123:
            flat_idx = flat_idx.astype(np.uint16)

        j_u8 = np.ascontiguousarray(joints, dtype=np.uint8)
        w_f32 = np.ascontiguousarray(weights, dtype=np.float32)

        attrs = {
            "POSITION": self.add_accessor(
                pos32, 5126, "VEC3", target=34962, with_minmax=True
            ),
            "NORMAL": self.add_accessor(nrm32, 5126, "VEC3", target=34962),
            "TEXCOORD_0": self.add_accessor(uv32, 5126, "VEC2", target=34962),
            "JOINTS_0": self.add_accessor(j_u8, 5121, "VEC4", target=34962),
            "WEIGHTS_0": self.add_accessor(w_f32, 5126, "VEC4", target=34962),
        }
        idx_acc = self.add_accessor(flat_idx, idx_ct, "SCALAR", target=34963)
        prim: dict[str, Any] = {
            "attributes": attrs,
            "indices": idx_acc,
            "material": material_idx,
            "mode": 4,
        }
        mesh_dict: dict[str, Any] = {"name": name, "primitives": [prim]}
        if morph_targets:
            targets_list = []
            for tname in BLEND_SHAPE_NAMES:
                delta = np.ascontiguousarray(morph_targets[tname], dtype=np.float32)
                if not np.all(np.isfinite(delta)):
                    raise ValueError(f"NaN/Inf in morph target {name}.{tname}")
                t_acc = self.add_accessor(
                    delta, 5126, "VEC3", target=34962, with_minmax=True
                )
                targets_list.append({"POSITION": t_acc})
            prim["targets"] = targets_list
            mesh_dict["weights"] = [0.0] * len(BLEND_SHAPE_NAMES)
            mesh_dict["extras"] = {"targetNames": list(BLEND_SHAPE_NAMES)}
        self.meshes.append(mesh_dict)
        return len(self.meshes) - 1


def write_glb(path: Path, doc: dict, blob: bytes) -> None:
    json_bytes = json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    json_bytes += b" " * ((4 - (len(json_bytes) % 4)) % 4)
    bin_bytes = bytes(blob)
    bin_bytes += b"\x00" * ((4 - (len(bin_bytes) % 4)) % 4)
    total = 12 + 8 + len(json_bytes) + 8 + len(bin_bytes)
    header = struct.pack("<4sII", _GLB_MAGIC, 2, total)
    j_hdr = struct.pack("<II", len(json_bytes), _JSON_CHUNK)
    b_hdr = struct.pack("<II", len(bin_bytes), _BIN_CHUNK)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + j_hdr + json_bytes + b_hdr + bin_bytes)


# ------------------------------------------------------------ main builder


def normalize_recipe(recipe: dict[str, Any]) -> dict[str, Any]:
    r = copy.deepcopy(recipe)
    base = str(r.get("base", "female")).lower()
    if base not in ("female", "male"):
        raise ValueError(f"base must be 'female' or 'male', got {base!r}")
    r["base"] = base
    r["height_m"] = float(r.get("height_m", 1.65 if base == "female" else 1.76))
    r["skin_tint"] = str(r.get("skin_tint", "#f4d4bc"))
    hair = r.get("hair")
    if not isinstance(hair, dict):
        hair = {
            "style": str(r.get("hair_style", "ponytail" if base == "female" else "parted")),
            "color": str(r.get("hair_color", "#1a181e")),
            "beard": bool(r.get("beard", False)),
        }
    else:
        hair = {
            "style": str(hair.get("style", "ponytail" if base == "female" else "parted")),
            "color": str(hair.get("color", "#1a181e")),
            "beard": bool(hair.get("beard", False)),
        }
    r["hair"] = hair
    outfit = r.get("outfit") if isinstance(r.get("outfit"), dict) else {}
    r["outfit"] = {
        "style": str(outfit.get("style", "jumpsuit")),
        "jacket_color": outfit.get("jacket_color", "#2b5998"),
        "accent_color": outfit.get("accent_color", "#e87a24"),
        "collar_color": outfit.get("collar_color", outfit.get("accent_color", "#e87a24")),
        "pants_color": outfit.get("pants_color", "#234373"),
        "boots_color": outfit.get("boots_color", "#1c2028"),
        "belt_color": outfit.get("belt_color", "#232830"),
        "shirt_color": outfit.get("shirt_color", "#e8ecf2"),
    }
    r["ps2_profile"] = bool(r.get("ps2_profile", False))
    if r["ps2_profile"]:
        PS2CharacterArtProfile.apply_palette(r["outfit"], r["base"])
    return r


def build_humanoid_rig(recipe: dict[str, Any], output_path: Path) -> dict[str, Any]:
    """Build a complete rigged, dressed, BlendShape-enabled character GLB."""
    cfg = normalize_recipe(recipe)
    base_gender = cfg["base"]
    height_m = cfg["height_m"]
    outfit = cfg["outfit"]
    hair_cfg = cfg["hair"]

    src_name = (
        "Superhero_Female_FullBody.gltf"
        if base_gender == "female"
        else "Superhero_Male_FullBody.gltf"
    )
    src_doc, src_blob = load_gltf(QUATERNIUS_DIR / src_name)
    src_nodes = copy.deepcopy(src_doc["nodes"])
    src_wm, src_parent = compute_world_matrices(src_nodes)

    skin = src_doc["skins"][0]
    joint_node_indices: list[int] = list(skin["joints"])
    jnames = [src_nodes[ni]["name"] for ni in joint_node_indices]
    head_joint_idx = jnames.index("Head")
    root_node_idx = joint_node_indices[jnames.index("root")]

    joint_world_pos = {
        jnames[ji]: src_wm[ni][:3, 3].copy()
        for ji, ni in enumerate(joint_node_indices)
    }

    # Read source meshes: 0=Eyebrows, 1=Eyes, 2=Body
    def _extract_mesh(mesh_idx: int):
        prim = src_doc["meshes"][mesh_idx]["primitives"][0]
        attrs = prim["attributes"]
        return (
            read_accessor(src_doc, src_blob, attrs["POSITION"]).astype(np.float64),
            read_accessor(src_doc, src_blob, attrs["NORMAL"]).astype(np.float64),
            read_accessor(src_doc, src_blob, attrs["TEXCOORD_0"]).astype(np.float64),
            read_accessor(src_doc, src_blob, prim["indices"]).reshape(-1, 3).astype(np.int64),
            read_accessor(src_doc, src_blob, attrs["JOINTS_0"]).astype(np.uint8),
            read_accessor(src_doc, src_blob, attrs["WEIGHTS_0"]).astype(np.float64),
        )

    brows_pos, brows_nrm, brows_uv, brows_faces, brows_j0, brows_w0 = _extract_mesh(0)
    eyes_pos, eyes_nrm, eyes_uv, eyes_faces, eyes_j0, eyes_w0 = _extract_mesh(1)
    body_pos, body_nrm, body_uv, body_faces, body_j0, body_w0 = _extract_mesh(2)

    # 1. Classify body regions & landmarks
    bgroups = _classify_bones(jnames)
    lm = _compute_landmarks(jnames, joint_world_pos, body_pos)
    fields = _compute_vertex_clothing_fields(
        body_pos, body_j0, body_w0, bgroups, lm, outfit["style"]
    )

    # 2. Sculpt 3D clothing silhouette volume (zero delta on head/face/neck/hands)
    sculpted_pos, sculpted_nrm = sculpt_clothing_geometry(
        body_pos, body_nrm, body_faces, fields, lm, outfit["style"]
    )
    if cfg.get("ps2_profile"):
        profile = PS2CharacterArtProfile(base_gender)
        sculpted_pos, sculpted_nrm = profile.apply_proportions(
            sculpted_pos,
            sculpted_nrm,
            body_faces,
            body_j0,
            body_w0,
            bgroups,
            joint_world_pos,
            lm,
            fields,
        )

    # 3. Paint crisp 2048x2048 UV outfit textures (preserving native face/neck/hand skin!)
    # x_3d is the sculpted bind-pose world X so jacket lapels cut on the mesh that renders.
    fields["x_3d"] = sculpted_pos[:, 0]
    uv_maps = _rasterize_uv_fields(
        body_pos, body_nrm, body_uv, body_faces, fields, size=2048
    )
    if base_gender == "female":
        skin_base_p = QUATERNIUS_DIR / "T_Superhero_Female_Light_BaseColor.png"
        skin_nrm_p = QUATERNIUS_DIR / "T_Superhero_Female_Normal.png"
        skin_rgh_p = QUATERNIUS_DIR / "T_Superhero_Female_Roughness.png"
    else:
        skin_base_p = QUATERNIUS_DIR / "T_Superhero_Male_Ligh.png"
        skin_nrm_p = QUATERNIUS_DIR / "T_Superhero_Male_Normal.png"
        skin_rgh_p = QUATERNIUS_DIR / "T_Superhero_Male_Roughness.png"

    body_albedo_im, body_normal_im, body_rough_im = paint_outfit_textures(
        skin_base_p, skin_nrm_p, skin_rgh_p, uv_maps, lm, cfg["skin_tint"], outfit
    )

    # 4. Compute 7 BlendShape morph targets on raw head & eyebrows coordinates
    body_morphs, brow_morphs = compute_morph_targets(
        sculpted_pos,
        body_faces,
        body_j0,
        body_w0,
        head_joint_idx,
        eyes_pos,
        brows_pos,
    )

    # 5. Load sculpted 3D hair (+ optional beard) skinned rigidly to Head bone
    hair_pos, hair_nrm, hair_uv, hair_faces, hair_alb_p, hair_nrm_p = _load_hair_mesh(
        hair_cfg["style"], base_gender
    )
    hair_j0 = np.zeros((len(hair_pos), 4), dtype=np.uint8)
    hair_j0[:, 0] = head_joint_idx
    hair_w0 = np.zeros((len(hair_pos), 4), dtype=np.float64)
    hair_w0[:, 0] = 1.0

    beard_data = None
    if hair_cfg.get("beard", False):
        bd_pos, bd_nrm, bd_uv, bd_faces, bd_alb_p, bd_nrm_p = _load_beard_mesh(
            base_gender
        )
        bd_j0 = np.zeros((len(bd_pos), 4), dtype=np.uint8)
        bd_j0[:, 0] = head_joint_idx
        bd_w0 = np.zeros((len(bd_pos), 4), dtype=np.float64)
        bd_w0[:, 0] = 1.0
        beard_data = (bd_pos, bd_nrm, bd_uv, bd_faces, bd_j0, bd_w0, bd_alb_p, bd_nrm_p)

    # 6. Ground cleanly at y = 0.0 and uniformly scale to height_m
    raw_y_min = float(sculpted_pos[:, 1].min())
    raw_y_max = float(sculpted_pos[:, 1].max())
    raw_height = max(raw_y_max - raw_y_min, 1e-4)
    scale = float(height_m) / raw_height
    dy = -raw_y_min * scale

    def _xform_pts(p: np.ndarray) -> np.ndarray:
        out = p * scale
        out[:, 1] += dy
        return out

    sculpted_pos = _xform_pts(sculpted_pos)
    eyes_pos = _xform_pts(eyes_pos)
    brows_pos = _xform_pts(brows_pos)
    hair_pos = _xform_pts(hair_pos)
    for k in BLEND_SHAPE_NAMES:
        body_morphs[k] = body_morphs[k] * scale
        brow_morphs[k] = brow_morphs[k] * scale
    if beard_data is not None:
        bd_pos, bd_nrm, bd_uv, bd_faces, bd_j0, bd_w0, bd_alb_p, bd_nrm_p = beard_data
        bd_pos = _xform_pts(bd_pos)
        beard_data = (bd_pos, bd_nrm, bd_uv, bd_faces, bd_j0, bd_w0, bd_alb_p, bd_nrm_p)

    # Update all 65 bone translations & recompute exact IBMs in scaled grounded space
    new_nodes = copy.deepcopy(src_nodes)
    desired_world_pos: dict[int, np.ndarray] = {}
    for ni in joint_node_indices:
        wp = src_wm[ni][:3, 3].copy()
        if ni == root_node_idx:
            desired_world_pos[ni] = np.zeros(3, dtype=np.float64)
        else:
            desired_world_pos[ni] = wp * scale + np.array([0.0, dy, 0.0], dtype=np.float64)

    for ni in joint_node_indices:
        par = src_parent.get(ni)
        if par is not None and par in desired_world_pos:
            r_par = src_wm[par][:3, :3]
            local_t = r_par.T @ (desired_world_pos[ni] - desired_world_pos[par])
        else:
            local_t = desired_world_pos[ni]
        new_nodes[ni]["translation"] = [float(v) for v in local_t]
        if "scale" in new_nodes[ni]:
            new_nodes[ni]["scale"] = [1.0, 1.0, 1.0]

    new_wm, _ = compute_world_matrices(new_nodes)
    ibms = np.zeros((len(joint_node_indices), 4, 4), dtype=np.float32)
    for ji, ni in enumerate(joint_node_indices):
        inv_m = np.linalg.inv(new_wm[ni])
        ibms[ji] = inv_m.T.astype(np.float32)  # glTF column-major MAT4

    # 7. Assemble clean self-contained binary GLB
    gb = _GLBBuilder()

    # Textures & Materials
    tex_body_alb = gb.add_image_png(body_albedo_im, "Body_BaseColor")
    tex_body_nrm = gb.add_image_png(body_normal_im, "Body_Normal")
    tex_body_rgh = gb.add_image_png(body_rough_im, "Body_MetallicRoughness")
    mat_body = len(gb.materials)
    body_material: dict[str, Any] = {
        "name": "MI_Character_Body",
        "doubleSided": True,
        "pbrMetallicRoughness": {
            "baseColorTexture": {"index": tex_body_alb},
            "metallicFactor": 1.0,
            "roughnessFactor": 1.0,
            "metallicRoughnessTexture": {"index": tex_body_rgh},
        },
        "normalTexture": {"index": tex_body_nrm},
    }
    if cfg.get("ps2_profile"):
        # Texture already stores cloth metallic 0.04 and roughness 0.82–0.94.
        # specularFactor keeps skin and fabric satin-matte instead of plastic.
        body_material["extensions"] = {
            "KHR_materials_specular": {"specularFactor": PS2CharacterArtProfile.SPECULAR}
        }
        body_material["extras"] = {
            "ps2_profile": True,
            "metallic": PS2CharacterArtProfile.METALLIC,
            "roughness_min": PS2CharacterArtProfile.ROUGHNESS_MIN,
            "roughness_max": PS2CharacterArtProfile.ROUGHNESS_MAX,
            "specular": PS2CharacterArtProfile.SPECULAR,
        }
    gb.materials.append(body_material)

    tex_eye_alb = gb.add_image_png(
        Image.open(QUATERNIUS_DIR / "T_Eye_Brown.png").convert("RGB"), "Eyes_BaseColor"
    )
    tex_eye_nrm = gb.add_image_png(
        Image.open(QUATERNIUS_DIR / "T_Eye_Normal.png").convert("RGB"), "Eyes_Normal"
    )
    mat_eyes = len(gb.materials)
    gb.materials.append(
        {
            "name": "MI_Eyes",
            "doubleSided": True,
            "pbrMetallicRoughness": {
                "baseColorTexture": {"index": tex_eye_alb},
                "metallicFactor": 0.0,
                "roughnessFactor": 0.28,
            },
            "normalTexture": {"index": tex_eye_nrm},
        }
    )

    hair_im = _tint_hair_image(hair_alb_p, hair_cfg["color"])
    hair_nrm_im = Image.open(hair_nrm_p).convert("RGB")
    tex_hair_alb = gb.add_image_png(hair_im, "Hair_BaseColor")
    tex_hair_nrm = gb.add_image_png(hair_nrm_im, "Hair_Normal")
    mat_hair = len(gb.materials)
    gb.materials.append(
        {
            "name": "MI_Hair",
            "doubleSided": True,
            "pbrMetallicRoughness": {
                "baseColorTexture": {"index": tex_hair_alb},
                "metallicFactor": 0.0,
                "roughnessFactor": 0.78,
            },
            "normalTexture": {"index": tex_hair_nrm},
        }
    )

    # Eyebrows material matches hair tint
    brow_src_alb = QUATERNIUS_DIR / (
        "T_Hair_2_BaseColor.png" if base_gender == "female" else "T_Hair_1_BaseColor.png"
    )
    brow_src_nrm = QUATERNIUS_DIR / (
        "T_Hair_2_Normal.png" if base_gender == "female" else "T_Hair_1_Normal.png"
    )
    tex_brow_alb = gb.add_image_png(
        _tint_hair_image(brow_src_alb, hair_cfg["color"]), "Eyebrows_BaseColor"
    )
    tex_brow_nrm = gb.add_image_png(
        Image.open(brow_src_nrm).convert("RGB"), "Eyebrows_Normal"
    )
    mat_brows = len(gb.materials)
    gb.materials.append(
        {
            "name": "MI_Eyebrows",
            "doubleSided": True,
            "pbrMetallicRoughness": {
                "baseColorTexture": {"index": tex_brow_alb},
                "metallicFactor": 0.0,
                "roughnessFactor": 0.82,
            },
            "normalTexture": {"index": tex_brow_nrm},
        }
    )

    # Add meshes (order matches node mesh indices 0=Eyebrows, 1=Eyes, 2=Body, 3=Hair, [4=Beard])
    m_brows = gb.add_skinned_mesh(
        "Eyebrows",
        brows_pos,
        brows_nrm,
        brows_uv,
        brows_faces,
        brows_j0,
        brows_w0,
        mat_brows,
        morph_targets=brow_morphs,
    )
    m_eyes = gb.add_skinned_mesh(
        "Eyes",
        eyes_pos,
        eyes_nrm,
        eyes_uv,
        eyes_faces,
        eyes_j0,
        eyes_w0,
        mat_eyes,
    )
    m_body = gb.add_skinned_mesh(
        "Body",
        sculpted_pos,
        sculpted_nrm,
        body_uv,
        body_faces,
        body_j0,
        body_w0,
        mat_body,
        morph_targets=body_morphs,
    )
    m_hair = gb.add_skinned_mesh(
        "Hair",
        hair_pos,
        hair_nrm,
        hair_uv,
        hair_faces,
        hair_j0,
        hair_w0,
        mat_hair,
    )

    # Wire up nodes under Armature
    armature_idx = src_doc["scenes"][0]["nodes"][0]
    for ni, node in enumerate(new_nodes):
        if node.get("mesh") == 0:
            node["mesh"] = m_brows
            node["name"] = "Eyebrows"
        elif node.get("mesh") == 1:
            node["mesh"] = m_eyes
            node["name"] = "Eyes"
        elif node.get("mesh") == 2:
            node["mesh"] = m_body
            node["name"] = "Body"

    hair_node_idx = len(new_nodes)
    new_nodes.append({"name": "Hair", "mesh": m_hair, "skin": 0})
    new_nodes[armature_idx]["children"].append(hair_node_idx)

    if beard_data is not None:
        bd_pos, bd_nrm, bd_uv, bd_faces, bd_j0, bd_w0, bd_alb_p, bd_nrm_p = beard_data
        tex_bd_alb = gb.add_image_png(
            _tint_hair_image(bd_alb_p, hair_cfg["color"]), "Beard_BaseColor"
        )
        tex_bd_nrm = gb.add_image_png(
            Image.open(bd_nrm_p).convert("RGB"), "Beard_Normal"
        )
        mat_beard = len(gb.materials)
        gb.materials.append(
            {
                "name": "MI_Beard",
                "doubleSided": True,
                "pbrMetallicRoughness": {
                    "baseColorTexture": {"index": tex_bd_alb},
                    "metallicFactor": 0.0,
                    "roughnessFactor": 0.82,
                },
                "normalTexture": {"index": tex_bd_nrm},
            }
        )
        m_beard = gb.add_skinned_mesh(
            "Beard",
            bd_pos,
            bd_nrm,
            bd_uv,
            bd_faces,
            bd_j0,
            bd_w0,
            mat_beard,
        )
        beard_node_idx = len(new_nodes)
        new_nodes.append({"name": "Beard", "mesh": m_beard, "skin": 0})
        new_nodes[armature_idx]["children"].append(beard_node_idx)

    ibm_acc = gb.add_accessor(ibms.reshape(-1, 16), 5126, "MAT4")
    skins = [
        {
            "name": "Armature",
            "inverseBindMatrices": ibm_acc,
            "joints": joint_node_indices,
        }
    ]

    out_doc = {
        "asset": {"version": "2.0", "generator": "SoundBookWorker/build_humanoid_rig.py"},
        "scene": 0,
        "scenes": [{"name": "Scene", "nodes": [armature_idx]}],
        "nodes": new_nodes,
        "meshes": gb.meshes,
        "skins": skins,
        "materials": gb.materials,
        "textures": gb.textures,
        "images": gb.images,
        "samplers": gb.samplers,
        "accessors": gb.accessors,
        "bufferViews": gb.buffer_views,
        "buffers": [{"byteLength": len(gb.blob)}],
    }
    if cfg.get("ps2_profile"):
        out_doc["extensionsUsed"] = ["KHR_materials_specular"]

    output_path = Path(output_path)
    write_glb(output_path, out_doc, bytes(gb.blob))

    total_tris = len(body_faces) + len(eyes_faces) + len(brows_faces) + len(hair_faces)
    if beard_data is not None:
        total_tris += len(beard_data[3])

    return {
        "output_path": str(output_path.resolve()),
        "base": base_gender,
        "height_m": round(float(sculpted_pos[:, 1].max() - sculpted_pos[:, 1].min()), 4),
        "y_min": round(float(sculpted_pos[:, 1].min()), 5),
        "y_max": round(float(sculpted_pos[:, 1].max()), 4),
        "bones": len(joint_node_indices),
        "blend_shapes": list(BLEND_SHAPE_NAMES),
        "triangles": int(total_tris),
        "hair": hair_cfg["style"],
        "beard": bool(hair_cfg.get("beard", False)),
        "outfit_style": outfit["style"],
        "ps2_profile": bool(cfg.get("ps2_profile", False)),
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Build a modular 65-bone clothed humanoid GLB with 7 BlendShapes."
    )
    p.add_argument("--recipe", type=Path, help="Path to character recipe JSON")
    p.add_argument(
        "--build-cast",
        action="store_true",
        help="Build the 4 default cast GLBs under media/assets3d/characters/",
    )
    p.add_argument("--output", "-o", type=Path, help="Output GLB path")
    p.add_argument("--base", choices=["female", "male"], default="female")
    p.add_argument("--height-m", type=float, default=None)
    p.add_argument("--skin-tint", default="#f4d4bc")
    p.add_argument(
        "--hair",
        choices=["long", "ponytail", "parted", "buns", "buzzed"],
        default=None,
    )
    p.add_argument("--hair-color", default="#18151d")
    p.add_argument("--beard", action="store_true")
    p.add_argument(
        "--outfit-style",
        choices=["jumpsuit", "jacket", "labcoat", "casual", "tactical"],
        default="jumpsuit",
    )
    p.add_argument("--jacket-color", default="#2b5998")
    p.add_argument("--accent-color", default="#e87a24")
    p.add_argument("--collar-color", default=None)
    p.add_argument("--pants-color", default="#234373")
    p.add_argument("--boots-color", default="#1c2028")
    p.add_argument("--belt-color", default="#232830")
    p.add_argument("--shirt-color", default="#e8ecf2")
    p.add_argument(
        "--ps2-profile",
        action="store_true",
        help="Apply PS2CharacterArtProfile (grounded proportions + matte baked kit). Does not replace default output names.",
    )
    p.add_argument(
        "--build-ps2-cast",
        action="store_true",
        help="Write linlan_ps2.glb and chen_ps2.glb. Never overwrites linlan.glb or chen.glb.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.build_cast and args.ps2_profile:
        print(
            "error: --ps2-profile cannot be combined with --build-cast "
            "(that would overwrite linlan.glb / chen.glb). Use --build-ps2-cast.",
            file=sys.stderr,
        )
        return 2
    if args.build_ps2_cast:
        out_dir = ROOT / "media" / "assets3d" / "characters"
        out_dir.mkdir(parents=True, exist_ok=True)
        for cid in ("linlan", "chen"):
            recipe = copy.deepcopy(DEFAULT_CAST_RECIPES[cid])
            recipe["ps2_profile"] = True
            destination = out_dir / f"{cid}_ps2.glb"
            if destination.name in ("linlan.glb", "chen.glb"):
                print(f"error: refused to overwrite {destination}", file=sys.stderr)
                return 2
            rep = build_humanoid_rig(recipe, destination)
            print(
                f"BUILT {cid}_ps2.glb: h={rep['height_m']}m y_min={rep['y_min']} "
                f"bones={rep['bones']} tris={rep['triangles']} hair={rep['hair']} "
                f"style={rep['outfit_style']} ps2={rep['ps2_profile']}"
            )
        return 0

    if args.build_cast:
        out_dir = ROOT / "media" / "assets3d" / "characters"
        out_dir.mkdir(parents=True, exist_ok=True)
        reports = []
        for cid, recipe in DEFAULT_CAST_RECIPES.items():
            rep = build_humanoid_rig(recipe, out_dir / f"{cid}.glb")
            reports.append(rep)
            print(
                f"BUILT {cid}.glb: h={rep['height_m']}m y_min={rep['y_min']} "
                f"bones={rep['bones']} tris={rep['triangles']} hair={rep['hair']} "
                f"style={rep['outfit_style']}"
            )
        return 0

    if not args.output:
        print("error: --output is required unless --build-cast is passed", file=sys.stderr)
        return 2

    if args.recipe:
        recipe = json.loads(args.recipe.read_text(encoding="utf-8"))
    else:
        recipe = {
            "base": args.base,
            "height_m": args.height_m
            if args.height_m is not None
            else (1.65 if args.base == "female" else 1.76),
            "skin_tint": args.skin_tint,
            "hair": {
                "style": args.hair or ("ponytail" if args.base == "female" else "parted"),
                "color": args.hair_color,
                "beard": bool(args.beard),
            },
            "ps2_profile": bool(args.ps2_profile),
            "outfit": {
                "style": args.outfit_style,
                "jacket_color": args.jacket_color,
                "accent_color": args.accent_color,
                "collar_color": args.collar_color or args.accent_color,
                "pants_color": args.pants_color,
                "boots_color": args.boots_color,
                "belt_color": args.belt_color,
                "shirt_color": args.shirt_color,
            },
        }
    if args.ps2_profile:
        recipe["ps2_profile"] = True
    rep = build_humanoid_rig(recipe, args.output)
    print(json.dumps(rep, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
