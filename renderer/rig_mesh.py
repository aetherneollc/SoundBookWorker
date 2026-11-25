#!/usr/bin/env python3
"""Build a deformable 2D triangle mesh rig from one front-facing character.

The generated ``rig.json`` is deliberately renderer-neutral.  UV coordinates
are texture-space pixels, so each draw group can be copied directly into a
Godot ``Polygon2D``.

Usage:
    python renderer/rig_mesh.py character.png --out media/rig_mesh_probe/rig
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import json
import math
import os
from collections import deque
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np
from PIL import Image
from scipy.spatial import Delaunay

try:
    from renderer import rig_characters
except ImportError:  # Direct execution: ``python renderer/rig_mesh.py ...``
    import rig_characters  # type: ignore


# ``root`` is the non-deforming scene root; the remaining 16 entries are the
# common humanoid deformation bones requested by the mesh-rig format.
BONE_PARENTS: dict[str, str | None] = {
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

BRANCH_BONES: dict[str, tuple[str, ...]] = {
    "head": ("head",),
    "torso": ("pelvis", "torso", "neck"),
    "arm_l": ("upper_arm_l", "lower_arm_l", "hand_l"),
    "arm_r": ("upper_arm_r", "lower_arm_r", "hand_r"),
    "leg_l": ("upper_leg_l", "lower_leg_l", "foot_l"),
    "leg_r": ("upper_leg_r", "lower_leg_r", "foot_r"),
}

GROUP_Z: dict[str, int] = {
    "legs": 0,
    "torso": 1,
    "head": 2,
    "arm_l": 3,
    "arm_r": 4,
}


def _border_lab_centers(rgb: np.ndarray) -> list[np.ndarray]:
    """Return deterministic colour clusters sampled from all four borders."""
    h, w = rgb.shape[:2]
    band = max(3, min(18, min(h, w) // 48))
    border = np.zeros((h, w), dtype=bool)
    border[:band, :] = True
    border[-band:, :] = True
    border[:, :band] = True
    border[:, -band:] = True
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    samples = lab[border][::2]
    if len(samples) == 0:
        return []

    # Quantisation avoids non-deterministic k-means while still representing
    # gradients at the top, sides, and pale floor independently.
    quantized = (samples.astype(np.int32) // 12).astype(np.int16)
    codes = (
        quantized[:, 0].astype(np.int32) * 22 * 22
        + quantized[:, 1].astype(np.int32) * 22
        + quantized[:, 2].astype(np.int32)
    )
    unique, counts = np.unique(codes, return_counts=True)
    order = np.argsort(counts)[::-1]
    minimum = max(4, int(len(samples) * 0.003))
    centers: list[np.ndarray] = []
    covered = 0
    for index in order:
        if int(counts[index]) < minimum and len(centers) >= 6:
            break
        selected = samples[codes == unique[index]].astype(np.float32)
        centers.append(np.median(selected, axis=0))
        covered += int(counts[index])
        if len(centers) >= 16 or (
            len(centers) >= 6 and covered >= int(len(samples) * 0.985)
        ):
            break
    return centers


def _remove_boundary_background(rgba: np.ndarray) -> np.ndarray:
    """Flood edge-connected green/gray illustration backgrounds to alpha zero.

    Kolors-style masters often use several muted greens: a main backdrop,
    cloudy patches, and a pale floor.  Chroma-keying only the saturated green
    leaves those pieces attached to shoes.  This mask deliberately considers
    broad background colours, but removes only components reachable from one
    of the four image borders, so an outlined white shirt remains protected.
    """
    out = np.ascontiguousarray(rgba.copy())
    rgb = out[:, :, :3]
    alpha = out[:, :, 3]
    h, w = alpha.shape
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    hue = hsv[:, :, 0]
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]

    nearest_sq = np.full((h, w), np.float32(np.inf), dtype=np.float32)
    for center in _border_lab_centers(rgb):
        delta = lab - center.reshape(1, 1, 3)
        nearest_sq = np.minimum(
            nearest_sq, np.sum(delta * delta, axis=2, dtype=np.float32)
        )
    border_similar = nearest_sq <= 20.0 * 20.0

    muted_green = (
        (hue >= 28)
        & (hue <= 88)
        & (saturation >= 16)
        & (saturation <= 165)
        & (value >= 42)
    )
    yy = np.arange(h, dtype=np.int32).reshape(-1, 1)
    lower_ground = (yy >= int(round(h * 0.80))) & (
        (
            (nearest_sq <= 36.0 * 36.0)
            & (saturation <= 125)
            & (value >= 38)
        )
        | muted_green
    )
    candidate = (
        (alpha < 32)
        | border_similar
        | muted_green
        | lower_ground
    ).astype(np.uint8)

    count, labels = cv2.connectedComponents(candidate, connectivity=4)
    if count <= 1:
        return out
    edge_labels = np.unique(
        np.concatenate(
            (labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1])
        )
    )
    edge_labels = edge_labels[edge_labels > 0]
    if len(edge_labels) == 0:
        return out
    flooded = np.isin(labels, edge_labels)
    out[:, :, 3] = np.where(flooded, 0, alpha).astype(np.uint8)
    return out


def _punch_enclosed_chroma(rgba: np.ndarray) -> np.ndarray:
    """Drop saturated green pockets sealed between an arm and the torso.

    Border flood cannot reach a hole that the limbs pinch shut, and that
    leftover screen shows up as a green slit once the arm settles.
    """
    out = np.ascontiguousarray(rgba.copy())
    hsv = cv2.cvtColor(out[:, :, :3], cv2.COLOR_RGB2HSV)
    hue = hsv[:, :, 0]
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    chroma = (hue >= 42) & (hue <= 78) & (saturation >= 110) & (value >= 120)
    out[:, :, 3] = np.where(chroma, 0, out[:, :, 3]).astype(np.uint8)
    return out


def _drop_ground_stain(rgba: np.ndarray) -> np.ndarray:
    """Remove the pale yellow floor Kolors paints under the shoes.

    It survives the border flood once it touches a sole. Orange clothing sits
    higher and on a redder hue, so a tight yellow key in the bottom band
    leaves the boots alone.
    """
    out = np.ascontiguousarray(rgba.copy())
    height = out.shape[0]
    band_top = int(height * 0.86)
    hsv = cv2.cvtColor(out[:, :, :3], cv2.COLOR_RGB2HSV)
    hue = hsv[:, :, 0]
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    stain = np.zeros(out.shape[:2], dtype=bool)
    stain[band_top:] = (
        (hue[band_top:] >= 18)
        & (hue[band_top:] <= 40)
        & (saturation[band_top:] >= 50)
        & (value[band_top:] >= 140)
    )
    out[:, :, 3] = np.where(stain, 0, out[:, :, 3]).astype(np.uint8)
    return out


def _keep_main_island(rgba: np.ndarray) -> np.ndarray:
    """Keep the torso-containing component and discard watermarks/islands."""
    out = np.ascontiguousarray(rgba.copy())
    binary = (out[:, :, 3] >= 32).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    if count <= 1:
        return out
    h, w = binary.shape
    torso_window = labels[
        int(h * 0.23) : max(int(h * 0.23) + 1, int(h * 0.68)),
        int(w * 0.30) : max(int(w * 0.30) + 1, int(w * 0.70)),
    ]
    central_counts = np.bincount(torso_window.ravel(), minlength=count)
    central_counts[0] = 0
    if int(central_counts.max()) > 0:
        main_index = int(np.argmax(central_counts))
    else:
        main_index = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    main = (labels == main_index).astype(np.uint8)
    # Recover only translucent antialiasing around the chosen component.  An
    # adjacent opaque floor component must not be restored at the shoe edge.
    soft_edge = (
        cv2.dilate(main, np.ones((3, 3), np.uint8), iterations=1) > 0
    ) & (out[:, :, 3] < 192)
    support = (main > 0) | soft_edge
    out[:, :, 3] = np.where(support, out[:, :, 3], 0).astype(np.uint8)
    return out


def _suppress_green_spill(rgba: np.ndarray) -> np.ndarray:
    """Neutralize only strongly green edge pixels; interior green clothes stay."""
    out = rgba.copy()
    alpha = out[:, :, 3]
    transparent = (alpha < 24).astype(np.uint8)
    edge = (
        cv2.dilate(transparent, np.ones((7, 7), np.uint8), iterations=1) > 0
    ) & (alpha > 0)
    rgb = out[:, :, :3].astype(np.float32)
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    neutral_g = np.maximum(r, b) * 1.035 + 2.0
    strong_spill = edge & (g > neutral_g + 7.0)
    # Preserve a little green hue instead of producing a gray fringe.
    rgb[:, :, 1] = np.where(
        strong_spill, neutral_g + 0.18 * (g - neutral_g), g
    )
    out[:, :, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    return out


def _try_onnx_upscale(rgb: np.ndarray, target_size: tuple[int, int]) -> np.ndarray | None:
    """Optional OpenCV DNN-superres hook; never downloads model weights."""
    model_path = os.environ.get("SOUNDBOOK_RIG_MESH_ONNX", "").strip()
    if not model_path:
        return None
    path = Path(model_path)
    if not path.is_file():
        print(f"rig_mesh: ONNX model not found: {path}; using Lanczos", flush=True)
        return None
    try:
        factory = getattr(getattr(cv2, "dnn_superres"), "DnnSuperResImpl_create")
        model = factory()
        model.readModel(str(path))
        algorithm = os.environ.get("SOUNDBOOK_RIG_MESH_ONNX_KIND", "edsr").lower()
        scale = int(os.environ.get("SOUNDBOOK_RIG_MESH_ONNX_SCALE", "2"))
        model.setModel(algorithm, scale)
        result = model.upsample(np.ascontiguousarray(rgb))
        if result.shape[1::-1] != target_size:
            result = cv2.resize(result, target_size, interpolation=cv2.INTER_LANCZOS4)
        return result
    except Exception as exc:
        print(
            f"rig_mesh: optional ONNX upscaler failed "
            f"({type(exc).__name__}: {exc}); using Lanczos",
            flush=True,
        )
        return None


def _upscale_clean_rgba(rgba: np.ndarray, scale: float) -> np.ndarray:
    if scale <= 0:
        raise ValueError("upscale must be greater than zero")
    h, w = rgba.shape[:2]
    target = (max(1, int(round(w * scale))), max(1, int(round(h * scale))))

    alpha = rgba[:, :, 3].astype(np.float32) / 255.0
    rgb = rgba[:, :, :3].astype(np.float32)
    premultiplied = rgb * alpha[:, :, None]
    alpha_up = cv2.resize(alpha, target, interpolation=cv2.INTER_LANCZOS4)
    premul_up = cv2.resize(
        premultiplied, target, interpolation=cv2.INTER_LANCZOS4
    )
    alpha_up = np.clip(alpha_up, 0.0, 1.0)
    lanczos_rgb = np.divide(
        premul_up,
        np.maximum(alpha_up[:, :, None], 1.0 / 255.0),
        out=np.zeros_like(premul_up),
        where=alpha_up[:, :, None] > 1.0 / 255.0,
    )
    lanczos_rgb = np.clip(lanczos_rgb, 0, 255).astype(np.uint8)

    onnx_rgb = _try_onnx_upscale(rgba[:, :, :3], target)
    up_rgb = onnx_rgb if onnx_rgb is not None else lanczos_rgb
    up_f = up_rgb.astype(np.float32)
    blurred = cv2.GaussianBlur(up_f, (0, 0), 0.7)
    # A tightly capped unsharp detail keeps ink lines crisp without bright halos.
    detail = np.clip(up_f - blurred, -7.0, 7.0)
    sharpened = np.clip(up_f + detail * 0.32, 0, 255)
    sharpened[alpha_up < 1.0 / 255.0] = 0

    result = np.empty((*alpha_up.shape, 4), dtype=np.uint8)
    result[:, :, :3] = sharpened.astype(np.uint8)
    result[:, :, 3] = np.clip(alpha_up * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return _keep_main_island(result)


def _foreground_bbox(mask: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.where(mask > 0)
    if len(xs) < 16:
        raise ValueError("input does not contain a usable foreground character")
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    return x0, y0, x1 - x0 + 1, y1 - y0 + 1


def _band_center(
    mask: np.ndarray,
    center_x: float,
    y: float,
    half_band: int,
    side: str,
    default_x: float,
) -> float:
    h, w = mask.shape
    y0 = max(0, int(round(y)) - half_band)
    y1 = min(h, int(round(y)) + half_band + 1)
    xs = np.where(mask[y0:y1] > 0)[1]
    if side == "l":
        xs = xs[xs < center_x - 1]
    else:
        xs = xs[xs > center_x + 1]
    if len(xs) < 6:
        return float(default_x)
    # The median is stable for thick cel-shaded limbs and ignores pointed shoes.
    return float(np.median(xs))


def _ray_tip(
    mask: np.ndarray, start: np.ndarray, direction: np.ndarray, max_distance: float
) -> np.ndarray:
    norm = float(np.linalg.norm(direction))
    if norm < 1e-5:
        return start.copy()
    unit = direction / norm
    h, w = mask.shape
    last = start.copy()
    seen = False
    misses = 0
    for distance in np.linspace(0.0, max_distance, max(8, int(max_distance * 1.5))):
        point = start + unit * float(distance)
        x, y = int(round(float(point[0]))), int(round(float(point[1])))
        if not (0 <= x < w and 0 <= y < h):
            break
        if mask[y, x] > 0:
            last = point
            seen = True
            misses = 0
        elif seen:
            misses += 1
            if misses >= 3:
                break
    if float(np.linalg.norm(last - start)) < max_distance * 0.12:
        last = start + unit * max(4.0, max_distance * 0.38)
    return last.astype(np.float32)


def _complete_joints(
    analysis: dict[str, Any],
    mask: np.ndarray,
    bbox: tuple[int, int, int, int],
) -> dict[str, np.ndarray]:
    """Return a complete full-body joint set even for an upper-body fallback."""
    bx, by, bw, bh = bbox
    source = analysis.get("joints") or {}
    joints = {
        name: np.asarray(value, dtype=np.float32).copy()
        for name, value in source.items()
    }
    center_x = float(joints.get("hip_mid", [bx + bw * 0.5, 0.0])[0])
    sh_y = float(by + bh * 0.22)
    joints.setdefault(
        "neck", np.array([center_x, by + bh * 0.17], dtype=np.float32)
    )
    shoulder_half = max(bh * 0.065, min(bw * 0.18, bh * 0.12))
    joints.setdefault(
        "sh_l", np.array([center_x - shoulder_half, sh_y], dtype=np.float32)
    )
    joints.setdefault(
        "sh_r", np.array([center_x + shoulder_half, sh_y], dtype=np.float32)
    )
    joints.setdefault(
        "wr_l", np.array([bx + bw * 0.05, by + bh * 0.39], dtype=np.float32)
    )
    joints.setdefault(
        "wr_r",
        np.array([bx + bw * 0.95, by + bh * 0.39], dtype=np.float32),
    )
    joints.setdefault("el_l", 0.52 * joints["sh_l"] + 0.48 * joints["wr_l"])
    joints.setdefault("el_r", 0.52 * joints["sh_r"] + 0.48 * joints["wr_r"])

    hip_y = float(
        joints.get("hip_mid", np.array([center_x, by + bh * 0.54]))[1]
    )
    if hip_y < by + bh * 0.42 or hip_y > by + bh * 0.66:
        hip_y = by + bh * 0.54
    hip_half = max(bh * 0.035, min(bw * 0.10, bh * 0.075))
    half_band = max(3, int(round(bh * 0.018)))
    knee_y, ankle_y = by + bh * 0.76, by + bh * 0.94

    joints.setdefault(
        "hip_l", np.array([center_x - hip_half, hip_y], dtype=np.float32)
    )
    joints.setdefault(
        "hip_r", np.array([center_x + hip_half, hip_y], dtype=np.float32)
    )
    joints.setdefault(
        "kn_l",
        np.array(
            [
                _band_center(
                    mask,
                    center_x,
                    knee_y,
                    half_band,
                    "l",
                    center_x - hip_half,
                ),
                knee_y,
            ],
            dtype=np.float32,
        ),
    )
    joints.setdefault(
        "kn_r",
        np.array(
            [
                _band_center(
                    mask,
                    center_x,
                    knee_y,
                    half_band,
                    "r",
                    center_x + hip_half,
                ),
                knee_y,
            ],
            dtype=np.float32,
        ),
    )
    joints.setdefault(
        "an_l",
        np.array(
            [
                _band_center(
                    mask,
                    center_x,
                    ankle_y,
                    half_band,
                    "l",
                    float(joints["kn_l"][0]),
                ),
                ankle_y,
            ],
            dtype=np.float32,
        ),
    )
    joints.setdefault(
        "an_r",
        np.array(
            [
                _band_center(
                    mask,
                    center_x,
                    ankle_y,
                    half_band,
                    "r",
                    float(joints["kn_r"][0]),
                ),
                ankle_y,
            ],
            dtype=np.float32,
        ),
    )
    joints["hip_mid"] = 0.5 * (joints["hip_l"] + joints["hip_r"])
    return joints


def _bone_record(
    name: str, parent: str | None, head: np.ndarray, tail: np.ndarray
) -> dict[str, Any]:
    delta = tail - head
    length = float(np.linalg.norm(delta))
    angle = math.atan2(float(delta[1]), float(delta[0])) if length > 1e-6 else 0.0
    return {
        "name": name,
        "parent": parent,
        "head": [round(float(head[0]), 4), round(float(head[1]), 4)],
        "tail": [round(float(tail[0]), 4), round(float(tail[1]), 4)],
        "length": round(length, 5),
        "rest_angle": round(angle, 7),
    }


def _build_bones(
    joints: dict[str, np.ndarray],
    mask: np.ndarray,
    bbox: tuple[int, int, int, int],
) -> list[dict[str, Any]]:
    bx, by, _, bh = bbox
    hip = joints["hip_mid"].astype(np.float32)
    shoulder_mid = 0.5 * (joints["sh_l"] + joints["sh_r"])
    waist = hip * 0.78 + shoulder_mid * 0.22
    neck_top = joints["neck"].astype(np.float32)
    if float(neck_top[1]) >= float(shoulder_mid[1]) - 2.0:
        neck_top = shoulder_mid + np.array([0.0, -max(5.0, bh * 0.035)])
    head_center = np.array(
        [
            float(neck_top[0]),
            max(float(by + bh * 0.055), 0.52 * float(by) + 0.48 * float(neck_top[1])),
        ],
        dtype=np.float32,
    )

    hand_l = _ray_tip(
        mask, joints["wr_l"], joints["wr_l"] - joints["el_l"], bh * 0.11
    )
    hand_r = _ray_tip(
        mask, joints["wr_r"], joints["wr_r"] - joints["el_r"], bh * 0.11
    )
    foot_l = _ray_tip(
        mask,
        joints["an_l"],
        joints["an_l"] - joints["kn_l"] + np.array([-bh * 0.05, 0.0]),
        bh * 0.085,
    )
    foot_r = _ray_tip(
        mask,
        joints["an_r"],
        joints["an_r"] - joints["kn_r"] + np.array([bh * 0.05, 0.0]),
        bh * 0.085,
    )

    endpoints: dict[str, tuple[np.ndarray, np.ndarray]] = {
        "root": (hip, hip),
        "pelvis": (hip, waist),
        "torso": (waist, shoulder_mid),
        "neck": (shoulder_mid, neck_top),
        "head": (neck_top, head_center),
        "upper_arm_l": (joints["sh_l"], joints["el_l"]),
        "lower_arm_l": (joints["el_l"], joints["wr_l"]),
        "hand_l": (joints["wr_l"], hand_l),
        "upper_arm_r": (joints["sh_r"], joints["el_r"]),
        "lower_arm_r": (joints["el_r"], joints["wr_r"]),
        "hand_r": (joints["wr_r"], hand_r),
        "upper_leg_l": (joints["hip_l"], joints["kn_l"]),
        "lower_leg_l": (joints["kn_l"], joints["an_l"]),
        "foot_l": (joints["an_l"], foot_l),
        "upper_leg_r": (joints["hip_r"], joints["kn_r"]),
        "lower_leg_r": (joints["kn_r"], joints["an_r"]),
        "foot_r": (joints["an_r"], foot_r),
    }
    return [
        _bone_record(name, parent, *endpoints[name])
        for name, parent in BONE_PARENTS.items()
    ]


def _sample_mask(mask: np.ndarray, point: np.ndarray) -> bool:
    h, w = mask.shape
    x = int(np.clip(round(float(point[0])), 0, w - 1))
    y = int(np.clip(round(float(point[1])), 0, h - 1))
    return bool(mask[y, x])


def _mesh_points(
    mask: np.ndarray, bones: list[dict[str, Any]], body_height: int
) -> tuple[np.ndarray, int]:
    """Sample hole boundaries, a regular interior grid, and every joint."""
    spacing = max(9, int(round(body_height / 42.0)))
    contours, _ = cv2.findContours(
        mask.astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE
    )
    points: list[tuple[float, float]] = []
    # Concave ink outlines need substantially denser samples than the interior
    # grid; otherwise one rejected Delaunay chord leaves a visible sawtooth.
    boundary_stride = max(2, spacing // 6)
    for contour in contours:
        flat = contour.reshape(-1, 2)
        if len(flat) < 3:
            continue
        for point in flat[::boundary_stride]:
            points.append((float(point[0]), float(point[1])))

    bys, bxs = np.where(mask > 0)
    x0, x1, y0, y1 = int(bxs.min()), int(bxs.max()), int(bys.min()), int(bys.max())
    row = 0
    for y in range(y0 + spacing // 2, y1 + 1, spacing):
        offset = (spacing // 2) if row % 2 else 0
        for x in range(x0 + spacing // 2 + offset, x1 + 1, spacing):
            if mask[y, x]:
                points.append((float(x), float(y)))
        row += 1

    for bone in bones:
        for key in ("head", "tail"):
            point = np.asarray(bone[key], dtype=np.float32)
            if _sample_mask(mask, point):
                points.append((float(point[0]), float(point[1])))

    unique: dict[tuple[int, int], tuple[float, float]] = {}
    for x, y in points:
        unique[(int(round(x * 4.0)), int(round(y * 4.0)))] = (x, y)
    sampled = np.asarray(list(unique.values()), dtype=np.float64)
    if len(sampled) < 3:
        raise ValueError("foreground is too small to triangulate")
    return sampled, spacing


def _triangulate_inside(
    points: np.ndarray,
    mask: np.ndarray,
    max_edge_length: float,
    containment_mask: np.ndarray | None = None,
) -> np.ndarray:
    delaunay = Delaunay(points, qhull_options="QJ")
    guard = containment_mask if containment_mask is not None else mask
    accepted: list[np.ndarray] = []
    for simplex in delaunay.simplices:
        triangle = points[simplex]
        edge_a = triangle[1] - triangle[0]
        edge_b = triangle[2] - triangle[0]
        cross = float(edge_a[0] * edge_b[1] - edge_a[1] * edge_b[0])
        if abs(cross) < 0.05:
            continue
        edge_lengths = [
            float(np.linalg.norm(triangle[(index + 1) % 3] - triangle[index]))
            for index in range(3)
        ]
        if max(edge_lengths) > max_edge_length:
            continue
        checks = [triangle.mean(axis=0)]
        for index in range(3):
            start = triangle[index]
            end = triangle[(index + 1) % 3]
            checks.extend(
                (
                    0.75 * start + 0.25 * end,
                    0.5 * (start + end),
                    0.25 * start + 0.75 * end,
                )
            )
        # Quarter- and midpoint checks keep armpit/crotch gaps hard, while the
        # two-pixel guard admits only tiny boundary triangles near concave ink.
        if all(_sample_mask(guard, point) for point in checks):
            accepted.append(simplex.astype(np.int32))
    if not accepted:
        raise ValueError("no triangles survived foreground-mask filtering")
    return np.asarray(accepted, dtype=np.int32)


def _draw_branch_seed(
    canvas: np.ndarray,
    mask: np.ndarray,
    label: int,
    segments: Iterable[tuple[np.ndarray, np.ndarray]],
    thickness: int,
) -> None:
    branch = np.zeros_like(canvas, dtype=np.uint8)
    for head, tail in segments:
        p0 = (int(round(float(head[0]))), int(round(float(head[1]))))
        p1 = (int(round(float(tail[0]))), int(round(float(tail[1]))))
        cv2.line(branch, p0, p1, 1, thickness, lineType=cv2.LINE_8)
    canvas[(branch > 0) & (mask > 0)] = label


def _branch_labels(
    mask: np.ndarray, bones: list[dict[str, Any]], spacing: int
) -> tuple[np.ndarray, dict[int, str]]:
    """Multi-source geodesic flood inside the foreground mask."""
    bone_map = {bone["name"]: bone for bone in bones}
    names = ("torso", "head", "arm_l", "arm_r", "leg_l", "leg_r")
    label_to_branch = {index + 1: name for index, name in enumerate(names)}
    branch_to_label = {name: label for label, name in label_to_branch.items()}
    seeds = np.zeros(mask.shape, dtype=np.uint8)
    thickness = max(2, spacing // 4)
    for branch in names:
        segments = []
        for bone_name in BRANCH_BONES[branch]:
            bone = bone_map[bone_name]
            segments.append(
                (
                    np.asarray(bone["head"], dtype=np.float32),
                    np.asarray(bone["tail"], dtype=np.float32),
                )
            )
        _draw_branch_seed(
            seeds,
            mask,
            branch_to_label[branch],
            segments,
            thickness,
        )

    # One simultaneous 4-neighbour BFS measures path distance through the
    # character rather than straight-line distance across transparent gaps.
    h, w = mask.shape
    labels = seeds.copy()
    queue: deque[int] = deque(int(i) for i in np.flatnonzero(labels))
    flat_labels = labels.ravel()
    flat_mask = mask.ravel()
    while queue:
        index = queue.popleft()
        label = flat_labels[index]
        x = index % w
        if index >= w:
            nxt = index - w
            if flat_mask[nxt] and flat_labels[nxt] == 0:
                flat_labels[nxt] = label
                queue.append(nxt)
        if index + w < h * w:
            nxt = index + w
            if flat_mask[nxt] and flat_labels[nxt] == 0:
                flat_labels[nxt] = label
                queue.append(nxt)
        if x > 0:
            nxt = index - 1
            if flat_mask[nxt] and flat_labels[nxt] == 0:
                flat_labels[nxt] = label
                queue.append(nxt)
        if x + 1 < w:
            nxt = index + 1
            if flat_mask[nxt] and flat_labels[nxt] == 0:
                flat_labels[nxt] = label
                queue.append(nxt)
    return labels, label_to_branch


def _point_segment_distance(
    point: np.ndarray, head: np.ndarray, tail: np.ndarray
) -> tuple[float, float]:
    delta = tail - head
    denominator = float(np.dot(delta, delta))
    if denominator < 1e-8:
        return float(np.linalg.norm(point - head)), 0.0
    t = float(np.clip(np.dot(point - head, delta) / denominator, 0.0, 1.0))
    projection = head + delta * t
    return float(np.linalg.norm(point - projection)), t


def _chain_distance(
    point: np.ndarray,
    chain: tuple[str, ...],
    bone_map: dict[str, dict[str, Any]],
) -> tuple[float, int]:
    distances = [
        _point_segment_distance(
            point,
            np.asarray(bone_map[name]["head"], dtype=np.float32),
            np.asarray(bone_map[name]["tail"], dtype=np.float32),
        )[0]
        for name in chain
    ]
    index = min(range(len(chain)), key=distances.__getitem__)
    return distances[index], index


def _fallback_body_branch(
    point: np.ndarray,
    bone_map: dict[str, dict[str, Any]],
    body_height: int,
) -> str:
    hip_y = float(bone_map["pelvis"]["head"][1])
    if float(point[1]) < hip_y - body_height * 0.025:
        return "torso"
    candidates = ("torso", "leg_l", "leg_r")
    return min(
        candidates,
        key=lambda name: _chain_distance(
            point, BRANCH_BONES[name], bone_map
        )[0],
    )


def _arm_branch_allowed(
    point: np.ndarray,
    branch: str,
    bone_map: dict[str, dict[str, Any]],
    body_height: int,
) -> bool:
    """Hard anatomical gate preventing an arm flood entering chest or thighs."""
    side = branch[-1]
    chain = BRANCH_BONES[branch]
    arm_distance, segment_index = _chain_distance(point, chain, bone_map)
    upper = bone_map[f"upper_arm_{side}"]
    shoulder = np.asarray(upper["head"], dtype=np.float32)
    elbow = np.asarray(upper["tail"], dtype=np.float32)
    shoulder_distance = float(np.linalg.norm(point - shoulder))
    outward_sign = -1.0 if side == "l" else 1.0
    outward = outward_sign * float(point[0] - shoulder[0])

    upper_direction = elbow - shoulder
    upper_length = max(float(np.linalg.norm(upper_direction)), 1.0)
    upper_direction /= upper_length
    along_arm = float(np.dot(point - shoulder, upper_direction))
    shoulder_ball = max(5.0, body_height * 0.018)
    widths = (
        max(8.0, body_height * 0.075),
        max(7.0, body_height * 0.058),
        max(6.0, body_height * 0.048),
    )
    if arm_distance > widths[segment_index]:
        return False
    if along_arm < -body_height * 0.012:
        return False

    # The only arm pixels permitted slightly inward of the shoulder tangent
    # are those in the small ball-and-socket transition circle.
    if shoulder_distance <= shoulder_ball:
        return outward >= -shoulder_ball * 0.62
    if outward < -body_height * 0.004:
        return False

    other_distance = min(
        _chain_distance(point, BRANCH_BONES[name], bone_map)[0]
        for name in ("torso", "leg_l", "leg_r")
    )
    # At a hand/thigh contact, require the arm skeleton to win by a margin;
    # pixels on the trouser or jacket side of the contact remain body-owned.
    return arm_distance + body_height * 0.0045 < other_distance


def _smoothstep(value: float) -> float:
    value = float(np.clip(value, 0.0, 1.0))
    return value * value * (3.0 - 2.0 * value)


def _chain_weights(
    point: np.ndarray,
    chain: tuple[str, ...],
    bone_map: dict[str, dict[str, Any]],
    radius: float,
) -> dict[str, float]:
    candidates: list[tuple[float, float]] = []
    for name in chain:
        bone = bone_map[name]
        candidates.append(
            _point_segment_distance(
                point,
                np.asarray(bone["head"], dtype=np.float32),
                np.asarray(bone["tail"], dtype=np.float32),
            )
        )
    index = min(range(len(chain)), key=lambda i: candidates[i][0])
    name = chain[index]
    t = candidates[index][1]
    length = max(float(bone_map[name]["length"]), 1.0)
    transition = min(0.34, radius / length)
    weights = {name: 1.0}
    if index > 0 and t < transition:
        blend = 0.5 * (1.0 - _smoothstep(t / transition))
        weights[name] -= blend
        weights[chain[index - 1]] = blend
    elif index + 1 < len(chain) and t > 1.0 - transition:
        blend = 0.5 * (
            1.0 - _smoothstep((1.0 - t) / transition)
        )
        weights[name] -= blend
        weights[chain[index + 1]] = blend
    return weights


def _add_parent_transition(
    weights: dict[str, float],
    point: np.ndarray,
    joint: np.ndarray,
    radius: float,
    parent: str,
    maximum: float = 0.46,
) -> dict[str, float]:
    distance = float(np.linalg.norm(point - joint))
    if distance >= radius:
        return weights
    parent_weight = maximum * (1.0 - _smoothstep(distance / radius))
    result = {name: value * (1.0 - parent_weight) for name, value in weights.items()}
    result[parent] = result.get(parent, 0.0) + parent_weight
    return result


def _normalized_weights(weights: dict[str, float]) -> dict[str, float]:
    positive = {name: value for name, value in weights.items() if value > 1e-8}
    total = sum(positive.values())
    if total <= 1e-8:
        return {"torso": 1.0}
    result = {name: round(value / total, 6) for name, value in positive.items()}
    largest = max(result, key=result.get)
    result[largest] = round(result[largest] + (1.0 - sum(result.values())), 6)
    return result


def _vertex_weights(
    points: np.ndarray,
    labels: np.ndarray,
    label_to_branch: dict[int, str],
    bones: list[dict[str, Any]],
    body_height: int,
) -> list[dict[str, float]]:
    bone_map = {bone["name"]: bone for bone in bones}
    hip_radius = max(8.0, body_height * 0.042)
    joint_radius = max(6.0, body_height * 0.032)
    output: list[dict[str, float]] = []
    h, w = labels.shape
    for raw_point in points:
        point = raw_point.astype(np.float32)
        x = int(np.clip(round(float(point[0])), 0, w - 1))
        y = int(np.clip(round(float(point[1])), 0, h - 1))
        branch = label_to_branch.get(int(labels[y, x]), "torso")
        if branch in ("arm_l", "arm_r") and not _arm_branch_allowed(
            point, branch, bone_map, body_height
        ):
            branch = _fallback_body_branch(point, bone_map, body_height)
        chain = BRANCH_BONES[branch]
        weights = _chain_weights(point, chain, bone_map, joint_radius)
        if branch in ("arm_l", "arm_r"):
            upper = bone_map[f"upper_{branch}"]
            shoulder_radius = max(6.0, float(upper["length"]) * 0.28)
            weights = _add_parent_transition(
                weights,
                point,
                np.asarray(upper["head"], dtype=np.float32),
                shoulder_radius,
                "torso",
                maximum=0.32,
            )
        elif branch in ("leg_l", "leg_r"):
            upper = bone_map[f"upper_{branch}"]
            weights = _add_parent_transition(
                weights,
                point,
                np.asarray(upper["head"], dtype=np.float32),
                hip_radius,
                "pelvis",
            )
        elif branch == "head":
            weights = _add_parent_transition(
                weights,
                point,
                np.asarray(bone_map["head"]["head"], dtype=np.float32),
                joint_radius,
                "neck",
            )
        output.append(_normalized_weights(weights))
    return output


def _bone_group(name: str) -> str:
    if name.startswith(("upper_leg_", "lower_leg_", "foot_")):
        return "legs"
    if name.endswith("_l") and ("arm" in name or name.startswith("hand")):
        return "arm_l"
    if name.endswith("_r") and ("arm" in name or name.startswith("hand")):
        return "arm_r"
    if name in {"head", "neck"}:
        return "head"
    return "torso"


def _triangle_group(
    triangle: np.ndarray, weights: list[dict[str, float]]
) -> str:
    scores = {name: 0.0 for name in GROUP_Z}
    for vertex_index in triangle:
        for bone, value in weights[int(vertex_index)].items():
            scores[_bone_group(bone)] += value
    return max(scores, key=lambda name: (scores[name], -GROUP_Z[name]))


def _shoulder_overlap_triangles(
    points: np.ndarray,
    triangles: np.ndarray,
    weights: list[dict[str, float]],
    bones: list[dict[str, Any]],
    body_backfills: list[np.ndarray] | None = None,
) -> list[np.ndarray]:
    """Find the first arm-owned triangle ring along each torso shoulder seam."""
    bone_map = {bone["name"]: bone for bone in bones}
    owners = [_triangle_group(triangle, weights) for triangle in triangles]
    edge_adjacency: dict[tuple[int, int], list[int]] = {}
    for triangle_index, triangle in enumerate(triangles):
        for edge in range(3):
            key = tuple(
                sorted(
                    (
                        int(triangle[edge]),
                        int(triangle[(edge + 1) % 3]),
                    )
                )
            )
            edge_adjacency.setdefault(key, []).append(triangle_index)

    backfill_edges: set[tuple[int, int]] = set()
    for triangle in body_backfills or []:
        for edge in range(3):
            backfill_edges.add(
                tuple(
                    sorted(
                        (
                            int(triangle[edge]),
                            int(triangle[(edge + 1) % 3]),
                        )
                    )
                )
            )

    selected: set[int] = set()
    for side in ("l", "r"):
        arm_group = f"arm_{side}"
        upper = bone_map[f"upper_arm_{side}"]
        shoulder = np.asarray(upper["head"], dtype=np.float32)
        radius = max(6.0, float(upper["length"]) * 0.28)
        for edge_key, adjacent in edge_adjacency.items():
            adjacent_owners = {owners[index] for index in adjacent}
            touches_torso = (
                "torso" in adjacent_owners or edge_key in backfill_edges
            )
            if arm_group not in adjacent_owners or not touches_torso:
                continue
            for index in adjacent:
                if owners[index] != arm_group:
                    continue
                centroid = points[triangles[index]].mean(axis=0)
                if float(np.linalg.norm(centroid - shoulder)) <= radius:
                    selected.add(index)
    return [triangles[index] for index in sorted(selected)]


def _filter_anatomical_triangles(
    points: np.ndarray,
    triangles: np.ndarray,
    weights: list[dict[str, float]],
    bones: list[dict[str, Any]],
    body_height: int,
) -> tuple[np.ndarray, list[np.ndarray]]:
    """Remove contact bridges between distal arms and waist/legs."""
    bone_map = {bone["name"]: bone for bone in bones}
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
    kept: list[np.ndarray] = []
    body_backfills: list[np.ndarray] = []
    shoulder_limit = max(9.0, body_height * 0.034)
    for triangle in triangles:
        reject = False
        for side in ("l", "r"):
            arm_names = (
                f"upper_arm_{side}",
                f"lower_arm_{side}",
                f"hand_{side}",
            )
            distal_names = (f"lower_arm_{side}", f"hand_{side}")
            arm_values = [
                sum(weights[int(index)].get(name, 0.0) for name in arm_names)
                for index in triangle
            ]
            distal_values = [
                sum(
                    weights[int(index)].get(name, 0.0)
                    for name in distal_names
                )
                for index in triangle
            ]
            body_values = [
                sum(weights[int(index)].get(name, 0.0) for name in body_names)
                for index in triangle
            ]
            if max(distal_values) >= 0.70 and max(body_values) >= 0.72:
                reject = True
                break

            torso_values = [
                weights[int(index)].get("torso", 0.0)
                + weights[int(index)].get("pelvis", 0.0)
                for index in triangle
            ]
            if max(arm_values) >= 0.80 and max(torso_values) >= 0.80:
                shoulder = np.asarray(
                    bone_map[f"upper_arm_{side}"]["head"], dtype=np.float32
                )
                relevant = [
                    points[int(index)]
                    for local, index in enumerate(triangle)
                    if arm_values[local] >= 0.65
                    or torso_values[local] >= 0.65
                ]
                if relevant and max(
                    float(np.linalg.norm(point - shoulder))
                    for point in relevant
                ) > shoulder_limit:
                    reject = True
                    break
        if not reject:
            kept.append(triangle)
        else:
            body_backfills.append(triangle)
    if not kept:
        raise ValueError("anatomical filtering removed every mesh triangle")
    return np.asarray(kept, dtype=np.int32), body_backfills


def _build_groups(
    points: np.ndarray,
    triangles: np.ndarray,
    weights: list[dict[str, float]],
    body_backfills: list[np.ndarray] | None = None,
    shoulder_backfills: list[np.ndarray] | None = None,
    bones: list[dict[str, Any]] | None = None,
    body_height: int = 1,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[np.ndarray]] = {name: [] for name in GROUP_Z}
    for triangle in triangles:
        grouped[_triangle_group(triangle, weights)].append(triangle)

    groups: list[dict[str, Any]] = []
    for group_name, z_index in GROUP_Z.items():
        source_triangles = grouped[group_name]
        used = sorted({int(i) for triangle in source_triangles for i in triangle})
        remap = {source: local for local, source in enumerate(used)}
        local_weights = [weights[index] for index in used]
        active_bones = [
            bone
            for bone in BONE_PARENTS
            if any(vertex.get(bone, 0.0) > 0.0 for vertex in local_weights)
        ]
        vertices = [
            [round(float(points[index, 0]), 4), round(float(points[index, 1]), 4)]
            for index in used
        ]
        groups.append(
            {
                "name": group_name,
                "z_index": z_index,
                "vertices": vertices,
                "uvs": [list(vertex) for vertex in vertices],
                "triangles": [
                    [remap[int(i)] for i in triangle]
                    for triangle in source_triangles
                ],
                "weights": {
                    bone: [
                        round(vertex.get(bone, 0.0), 6)
                        for vertex in local_weights
                    ]
                    for bone in active_bones
                },
            }
        )

    group_map = {group["name"]: group for group in groups}
    if body_backfills and bones:
        bone_map = {bone["name"]: bone for bone in bones}
        joint_radius = max(6.0, body_height * 0.032)
        hip_radius = max(8.0, body_height * 0.042)
        for triangle in body_backfills:
            triangle_points = [points[int(index)].astype(np.float32) for index in triangle]
            branches = [
                _fallback_body_branch(point, bone_map, body_height)
                for point in triangle_points
            ]
            target_name = (
                "legs"
                if sum(branch.startswith("leg_") for branch in branches) >= 2
                else "torso"
            )
            target = group_map[target_name]
            start = len(target["vertices"])
            backfill_weights: list[dict[str, float]] = []
            for point, branch in zip(triangle_points, branches):
                vertex_weight = _chain_weights(
                    point, BRANCH_BONES[branch], bone_map, joint_radius
                )
                if branch.startswith("leg_"):
                    upper = bone_map[f"upper_{branch}"]
                    vertex_weight = _add_parent_transition(
                        vertex_weight,
                        point,
                        np.asarray(upper["head"], dtype=np.float32),
                        hip_radius,
                        "pelvis",
                    )
                backfill_weights.append(_normalized_weights(vertex_weight))

            active = set(target["weights"])
            active.update(
                bone
                for vertex_weight in backfill_weights
                for bone in vertex_weight
            )
            for bone in BONE_PARENTS:
                if bone in active and bone not in target["weights"]:
                    target["weights"][bone] = [0.0] * start
            for point in triangle_points:
                vertex = [round(float(point[0]), 4), round(float(point[1]), 4)]
                target["vertices"].append(vertex)
                target["uvs"].append(list(vertex))
            for bone, values in target["weights"].items():
                values.extend(
                    round(vertex_weight.get(bone, 0.0), 6)
                    for vertex_weight in backfill_weights
                )
            target["triangles"].append([start, start + 1, start + 2])

    if shoulder_backfills:
        target = group_map["torso"]
        for triangle in shoulder_backfills:
            start = len(target["vertices"])
            if "torso" not in target["weights"]:
                target["weights"]["torso"] = [0.0] * start
            triangle_points = [
                points[int(index)].astype(np.float32) for index in triangle
            ]
            for point in triangle_points:
                vertex = [round(float(point[0]), 4), round(float(point[1]), 4)]
                target["vertices"].append(vertex)
                target["uvs"].append(list(vertex))
            for bone, values in target["weights"].items():
                values.extend([1.0 if bone == "torso" else 0.0] * 3)
            target["triangles"].append([start, start + 1, start + 2])
    return groups


def _jsonable(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _face_anchor_module() -> Any | None:
    """Import the face detector under whichever name this process can see.

    ``python renderer/prepare_stage.py`` puts renderer/ itself on sys.path, so
    the package name only resolves under ``python -m unittest``. Trying only
    that name left every CLI-built rig without a mouth, eyes or brows.
    """
    for module_name in ("renderer.face_anchors", "face_anchors"):
        try:
            return importlib.import_module(module_name)
        except ImportError:
            continue
    return None


def _try_face_anchors(
    rgba: np.ndarray,
    texture_path: Path,
    bbox: tuple[int, int, int, int],
    bones: list[dict[str, Any]],
) -> Any | None:
    """Use a face-anchor module when another pipeline component provides one."""
    module = _face_anchor_module()
    if module is None:
        print("rig_mesh: face_anchors module not importable; leaving face_anchors null", flush=True)
        return None
    for function_name in (
        "extract_face_anchors",
        "detect_face_anchors",
        "analyze_face_anchors",
        "build_face_anchors",
    ):
        function = getattr(module, function_name, None)
        if not callable(function):
            continue
        try:
            if function_name == "detect_face_anchors":
                # The shared detector's optional head/nose hints may be omitted;
                # its own full-body fallback is more accurate than passing the
                # character-wide foreground bbox as a head box.
                result = function(rgba)
                if result is not None:
                    return _jsonable(result)
                continue
            signature = inspect.signature(function)
            arguments: list[Any] = []
            unsupported = False
            for parameter in signature.parameters.values():
                if parameter.kind in (
                    inspect.Parameter.VAR_POSITIONAL,
                    inspect.Parameter.VAR_KEYWORD,
                ):
                    continue
                key = parameter.name.lower()
                if "path" in key or "file" in key:
                    arguments.append(texture_path)
                elif "bbox" in key or "box" in key:
                    arguments.append(bbox)
                elif "bone" in key or "rig" in key:
                    arguments.append(bones)
                elif any(word in key for word in ("rgba", "image", "texture", "array")):
                    arguments.append(rgba)
                elif parameter.default is inspect.Parameter.empty:
                    unsupported = True
                    break
            if unsupported:
                continue
            result = function(*arguments)
            if result is not None:
                return _jsonable(result)
        except Exception as exc:
            print(
                f"rig_mesh: optional face anchors failed "
                f"({function_name}: {type(exc).__name__}); leaving null",
                flush=True,
            )
    return None


def build_mesh_rig(
    master_path: Path, out_dir: Path, upscale: float = 2.0
) -> Path:
    """Build ``texture.png`` and ``rig.json`` and return the JSON path."""
    master_path = Path(master_path)
    out_dir = Path(out_dir)
    if not master_path.is_file():
        raise FileNotFoundError(master_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    raw = np.asarray(Image.open(master_path).convert("RGBA"))
    cleaned = rig_characters._ensure_rgba_matte(raw)
    cleaned = _drop_ground_stain(_punch_enclosed_chroma(_remove_boundary_background(cleaned)))
    cleaned = _suppress_green_spill(_keep_main_island(cleaned))
    rgba = _upscale_clean_rgba(cleaned, float(upscale))
    rgba = _punch_enclosed_chroma(rgba)
    rgba = _suppress_green_spill(_keep_main_island(rgba))
    texture_path = out_dir / "texture.png"
    Image.fromarray(rgba, "RGBA").save(texture_path, optimize=True)

    alpha_mask = (rgba[:, :, 3] >= 32).astype(np.uint8)
    bbox = _foreground_bbox(alpha_mask)
    rtmlib_result = rig_characters._try_rtmlib_keypoints(rgba)
    analysis = None
    if rtmlib_result is not None:
        analysis = rig_characters._analyze_from_rtmlib(
            rgba, bbox, rtmlib_result[0], rtmlib_result[1]
        )
    if analysis is None:
        analysis = rig_characters._analyze_silhouette(rgba, bbox)

    joints = _complete_joints(analysis, alpha_mask, bbox)
    bones = _build_bones(joints, alpha_mask, bbox)
    body_height = bbox[3]
    # Six pixels at the default 2x scale protects antialiased outer ink.
    dilation_radius = max(4, min(6, int(round(float(upscale) * 3.0))))
    kernel_size = dilation_radius * 2 + 1
    expanded_mask = cv2.dilate(
        alpha_mask,
        cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (kernel_size, kernel_size)
        ),
        iterations=1,
    )
    points, spacing = _mesh_points(expanded_mask, bones, body_height)
    containment_mask = cv2.dilate(
        expanded_mask,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
        iterations=1,
    )
    triangles = _triangulate_inside(
        points,
        expanded_mask,
        max_edge_length=max(24.0, spacing * 2.2),
        containment_mask=containment_mask,
    )
    labels, label_to_branch = _branch_labels(expanded_mask, bones, spacing)
    weights = _vertex_weights(
        points, labels, label_to_branch, bones, body_height
    )
    triangles, body_backfills = _filter_anatomical_triangles(
        points, triangles, weights, bones, body_height
    )
    shoulder_backfills = _shoulder_overlap_triangles(
        points, triangles, weights, bones, body_backfills
    )
    groups = _build_groups(
        points,
        triangles,
        weights,
        body_backfills=body_backfills,
        shoulder_backfills=shoulder_backfills,
        bones=bones,
        body_height=body_height,
    )

    fg_ys, fg_xs = np.where(alpha_mask > 0)
    center_x = float(joints["hip_mid"][0])
    face_anchors = _try_face_anchors(rgba, texture_path, bbox, bones)
    rig_document = {
        "version": 1,
        "texture": "texture.png",
        "texture_size": [int(rgba.shape[1]), int(rgba.shape[0])],
        "feet_y": round(float(fg_ys.max()), 4),
        "body_height": round(float(fg_ys.max() - fg_ys.min() + 1), 4),
        "center_x": round(center_x, 4),
        "bones": bones,
        "groups": groups,
        "face_anchors": face_anchors,
    }
    rig_path = out_dir / "rig.json"
    temporary = out_dir / "rig.json.tmp"
    temporary.write_text(
        json.dumps(rig_document, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(rig_path)
    triangle_count = sum(len(group["triangles"]) for group in groups)
    print(
        f"rig_mesh: {master_path.name} -> {rig_path} "
        f"vertices={len(points)} triangles={triangle_count}",
        flush=True,
    )
    return rig_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="front-facing full-body PNG")
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    parser.add_argument("--upscale", type=float, default=2.0)
    parser.add_argument(
        "--onnx",
        type=Path,
        default=None,
        help="optional local OpenCV dnn_superres model (never downloaded)",
    )
    arguments = parser.parse_args()
    if arguments.onnx is not None:
        os.environ["SOUNDBOOK_RIG_MESH_ONNX"] = str(arguments.onnx)
    build_mesh_rig(arguments.input, arguments.out, arguments.upscale)


if __name__ == "__main__":
    main()
