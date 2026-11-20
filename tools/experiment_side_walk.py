#!/usr/bin/env python3
"""Controlled CPU experiment for a layered side-view walk cycle.

The experiment intentionally remains separate from the production renderer.
It accepts explicit arm/torso masks when real side art is available, can try
an optional LaMa ONNX model, and otherwise compares OpenCV TELEA and Navier-
Stokes fills followed by a flat-colour repair suited to cel-shaded artwork.

Without arguments the script searches for a local ``*-side.png`` asset.  The
current repository does not always contain one, so the deterministic fallback
runs both a controlled synthetic profile and a synthetic side-motion proxy
built from real character pixels.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
import time
from typing import Any, Iterable, Sequence

import cv2
import numpy as np
from PIL import Image


REPOSITORY = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPOSITORY / "media" / "m2_side_walk"


@dataclass
class SideSample:
    """One source image plus the masks needed by the controlled experiment."""

    name: str
    source_rgba: np.ndarray
    arm_mask: np.ndarray
    torso_mask: np.ndarray
    shoulder: tuple[float, float]
    hip: tuple[float, float]
    ground_truth_rgba: np.ndarray | None = None
    source_path: str | None = None
    mask_source: str = "explicit"


@dataclass
class LayeredSideWalk:
    """Separated back/front layers and measured restoration quality."""

    name: str
    source_rgba: np.ndarray
    body_rgba: np.ndarray
    arm_rgba: np.ndarray
    arm_mask: np.ndarray
    torso_mask: np.ndarray
    fill_mask: np.ndarray
    shoulder: tuple[float, float]
    hip: tuple[float, float]
    method: str
    cpu_ms: float
    metrics: dict[str, Any]


def _as_rgba(image: np.ndarray) -> np.ndarray:
    values = np.asarray(image)
    if values.ndim != 3 or values.shape[2] not in (3, 4):
        raise ValueError("image must be an HxWx3 or HxWx4 array")
    if values.dtype != np.uint8:
        scale = (
            255.0
            if np.issubdtype(values.dtype, np.floating)
            and float(np.nanmax(values, initial=0.0)) <= 1.0
            else 1.0
        )
        values = np.clip(
            np.nan_to_num(values * scale, nan=0.0), 0, 255
        ).astype(np.uint8)
    else:
        values = values.copy()
    if values.shape[2] == 3:
        alpha = np.full((*values.shape[:2], 1), 255, dtype=np.uint8)
        values = np.concatenate((values, alpha), axis=2)
    return np.ascontiguousarray(values)


def _as_mask(mask: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    values = np.asarray(mask)
    if values.ndim == 3:
        values = values[:, :, 3] if values.shape[2] == 4 else values.max(axis=2)
    if values.ndim != 2:
        raise ValueError("mask must be a 2D or colour image")
    if values.shape != shape:
        values = cv2.resize(
            values.astype(np.uint8),
            (shape[1], shape[0]),
            interpolation=cv2.INTER_NEAREST,
        )
    if values.dtype != np.uint8:
        values = np.clip(values, 0, 255).astype(np.uint8)
    if int(values.max(initial=0)) <= 1:
        values = values * 255
    return np.ascontiguousarray(values)


def load_rgba(path: Path) -> np.ndarray:
    """Load artwork and convert a green screen to alpha when necessary."""
    rgba = np.asarray(Image.open(path).convert("RGBA"))
    alpha = rgba[:, :, 3].copy()
    rgb = rgba[:, :, :3]
    if int(alpha.min()) == 255:
        red, green, blue = (rgb[:, :, index] for index in range(3))
        chroma = (
            (green > 150)
            & (green.astype(np.int16) > red.astype(np.int16) + 45)
            & (green.astype(np.int16) > blue.astype(np.int16) + 45)
        )
        if float(chroma.mean()) >= 0.08:
            alpha[chroma] = 0
            alpha = cv2.GaussianBlur(alpha, (3, 3), 0)
    result = rgba.copy()
    result[:, :, 3] = alpha
    return np.ascontiguousarray(result)


def load_mask(path: Path, shape: tuple[int, int]) -> np.ndarray:
    return _as_mask(np.asarray(Image.open(path)), shape)


def _alpha_over(back: np.ndarray, front: np.ndarray) -> np.ndarray:
    back_f = _as_rgba(back).astype(np.float32) / 255.0
    front_f = _as_rgba(front).astype(np.float32) / 255.0
    front_a = front_f[:, :, 3:4]
    back_a = back_f[:, :, 3:4]
    out_a = front_a + back_a * (1.0 - front_a)
    premultiplied = (
        front_f[:, :, :3] * front_a
        + back_f[:, :, :3] * back_a * (1.0 - front_a)
    )
    out_rgb = np.divide(
        premultiplied,
        np.maximum(out_a, 1e-6),
        out=np.zeros_like(premultiplied),
        where=out_a > 1e-6,
    )
    return np.clip(
        np.concatenate((out_rgb, out_a), axis=2) * 255.0 + 0.5,
        0,
        255,
    ).astype(np.uint8)


def _foreground_mask(rgba: np.ndarray) -> np.ndarray:
    return (rgba[:, :, 3] >= 32).astype(np.uint8) * 255


def _mask_bbox(mask: np.ndarray) -> tuple[int, int, int, int]:
    rows, columns = np.where(mask > 0)
    if not len(rows):
        raise ValueError("mask is empty")
    return (
        int(columns.min()),
        int(rows.min()),
        int(columns.max()) + 1,
        int(rows.max()) + 1,
    )


def _draw_poly(
    canvas: np.ndarray,
    points: Sequence[tuple[int, int]],
    colour: tuple[int, int, int, int],
) -> None:
    cv2.fillPoly(
        canvas,
        [np.asarray(points, dtype=np.int32)],
        colour,
        lineType=cv2.LINE_AA,
    )


def _draw_limb(
    canvas: np.ndarray,
    points: Sequence[tuple[int, int]],
    fill: tuple[int, int, int, int],
    width: int,
) -> None:
    line = np.asarray(points, dtype=np.int32)
    cv2.polylines(
        canvas,
        [line],
        False,
        (29, 34, 55, 255),
        width + 9,
        lineType=cv2.LINE_AA,
    )
    cv2.polylines(
        canvas,
        [line],
        False,
        fill,
        width,
        lineType=cv2.LINE_AA,
    )


def make_synthetic_side_sample() -> SideSample:
    """Create a true-profile cel-shaded sample with known hidden torso pixels."""
    height, width = 520, 300
    body = np.zeros((height, width, 4), dtype=np.uint8)
    torso_mask = np.zeros((height, width), dtype=np.uint8)
    ink = (29, 34, 55, 255)
    suit = (43, 91, 151, 255)
    suit_light = (60, 117, 181, 255)
    orange = (242, 111, 44, 255)
    skin = (244, 192, 160, 255)
    hair = (38, 43, 60, 255)

    # Far limbs are part of the backing layer.
    _draw_limb(body, [(143, 304), (122, 394), (108, 478)], suit, 30)
    _draw_limb(body, [(157, 304), (172, 393), (187, 478)], suit_light, 31)
    cv2.ellipse(body, (99, 483), (32, 12), 0, 0, 360, ink, -1, cv2.LINE_AA)
    cv2.ellipse(body, (194, 483), (34, 12), 0, 0, 360, ink, -1, cv2.LINE_AA)
    _draw_limb(body, [(130, 178), (99, 236), (82, 294)], suit, 21)
    cv2.ellipse(body, (79, 301), (12, 16), -15, 0, 360, skin, -1, cv2.LINE_AA)

    torso_points = [(124, 150), (174, 151), (190, 307), (119, 307)]
    _draw_poly(body, torso_points, ink)
    inner_torso = [(130, 158), (168, 159), (181, 298), (126, 298)]
    _draw_poly(body, inner_torso, suit)
    _draw_poly(torso_mask, torso_points, (255, 255, 255, 255))
    cv2.line(body, (135, 165), (145, 294), orange, 7, cv2.LINE_AA)
    cv2.line(body, (132, 220), (177, 223), (225, 231, 237, 255), 4, cv2.LINE_AA)
    cv2.rectangle(body, (130, 287), (181, 303), ink, -1)
    cv2.rectangle(body, (142, 289), (156, 301), orange, -1)

    cv2.rectangle(body, (139, 126), (158, 160), skin, -1)
    cv2.ellipse(body, (151, 88), (40, 57), 0, 0, 360, ink, -1, cv2.LINE_AA)
    cv2.ellipse(body, (158, 91), (33, 48), 0, 0, 360, skin, -1, cv2.LINE_AA)
    # Nose and chin make the orientation unambiguously side-on.
    _draw_poly(body, [(187, 76), (209, 90), (184, 98)], skin)
    cv2.ellipse(body, (174, 78), (6, 9), 0, 0, 360, ink, -1, cv2.LINE_AA)
    cv2.circle(body, (176, 76), 2, (245, 249, 255, 255), -1)
    cv2.line(body, (182, 111), (195, 109), (142, 54, 68, 255), 2, cv2.LINE_AA)
    _draw_poly(
        body,
        [(119, 90), (122, 42), (157, 24), (187, 42), (189, 68),
         (168, 57), (154, 75), (142, 57), (132, 104)],
        hair,
    )

    arm = np.zeros_like(body)
    shoulder = (170, 176)
    arm_points = [shoulder, (137, 244), (172, 294)]
    _draw_limb(arm, arm_points, suit_light, 24)
    cv2.line(arm, (150, 218), (139, 241), orange, 7, cv2.LINE_AA)
    cv2.ellipse(arm, (176, 302), (13, 17), -30, 0, 360, ink, -1, cv2.LINE_AA)
    cv2.ellipse(arm, (176, 301), (9, 13), -30, 0, 360, skin, -1, cv2.LINE_AA)

    source = _alpha_over(body, arm)
    return SideSample(
        name="controlled_profile",
        source_rgba=source,
        arm_mask=arm[:, :, 3],
        torso_mask=torso_mask,
        shoulder=(float(shoulder[0]), float(shoulder[1])),
        hip=(150.0, 304.0),
        ground_truth_rgba=body,
        source_path="generated controlled profile",
        mask_source="known synthetic layers",
    )


def _group_mask(rig: dict[str, Any], group_name: str, shape: tuple[int, int]) -> np.ndarray:
    mask = np.zeros(shape, dtype=np.uint8)
    groups = {str(group["name"]): group for group in rig.get("groups", [])}
    if group_name not in groups:
        raise ValueError(f"rig group is missing: {group_name}")
    group = groups[group_name]
    vertices = np.asarray(group["vertices"], dtype=np.float32)
    for triangle in group["triangles"]:
        points = np.round(vertices[np.asarray(triangle)] * 16.0).astype(np.int32)
        cv2.fillConvexPoly(mask, points, 255, lineType=cv2.LINE_AA, shift=4)
    return mask


def _bone_points(
    rig: dict[str, Any], arm_side: str
) -> tuple[np.ndarray, np.ndarray]:
    bones = {str(bone["name"]): bone for bone in rig.get("bones", [])}
    upper = bones[f"upper_arm_{arm_side}"]
    hand = bones.get(f"hand_{arm_side}", bones[f"lower_arm_{arm_side}"])
    shoulder = np.asarray(upper["head"], dtype=np.float32)
    endpoint = np.asarray(hand.get("tail", hand["head"]), dtype=np.float32)
    return shoulder, endpoint


def _similarity_transform(
    source_start: np.ndarray,
    source_end: np.ndarray,
    target_start: np.ndarray,
    target_end: np.ndarray,
) -> np.ndarray:
    source_vector = source_end - source_start
    target_vector = target_end - target_start
    denominator = float(np.dot(source_vector, source_vector))
    if denominator < 1e-6:
        raise ValueError("arm endpoints are coincident")
    a = float(np.dot(target_vector, source_vector) / denominator)
    b = float(
        (target_vector[1] * source_vector[0]
         - target_vector[0] * source_vector[1])
        / denominator
    )
    matrix = np.asarray(
        [
            [a, -b, 0.0],
            [b, a, 0.0],
        ],
        dtype=np.float32,
    )
    matrix[:, 2] = target_start - matrix[:, :2] @ source_start
    return matrix


def _warp(
    image: np.ndarray,
    matrix: np.ndarray,
    size: tuple[int, int],
    interpolation: int = cv2.INTER_LINEAR,
) -> np.ndarray:
    return cv2.warpAffine(
        image,
        matrix,
        size,
        flags=interpolation,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )


def make_real_pixel_proxy(texture_path: Path, rig_path: Path) -> SideSample:
    """Build a side-motion occlusion proxy from real generated character pixels.

    This is deliberately labelled a proxy: it can test detailed-clothing
    inpainting, but a squeezed frontal face is not evidence that a generated
    side view is identity-consistent.
    """
    source = load_rgba(texture_path)
    rig = json.loads(rig_path.read_text(encoding="utf-8"))
    height, width = source.shape[:2]
    foreground = _foreground_mask(source)
    x0, y0, x1, y1 = _mask_bbox(foreground)
    body_width = float(x1 - x0)
    body_height = float(y1 - y0)
    center_x = (x0 + x1) / 2.0

    # The image-right arm is the near layer in the proxy.
    arm_side = "r"
    original_arm_mask = cv2.bitwise_and(
        _group_mask(rig, f"arm_{arm_side}", (height, width)),
        foreground,
    )
    original_arm = source.copy()
    original_arm[:, :, 3] = np.minimum(
        original_arm[:, :, 3], original_arm_mask
    )

    torso_support = np.zeros((height, width), dtype=np.uint8)
    torso_polygon = np.asarray(
        [
            (center_x - body_width * 0.20, y0 + body_height * 0.18),
            (center_x + body_width * 0.20, y0 + body_height * 0.18),
            (center_x + body_width * 0.17, y0 + body_height * 0.61),
            (center_x - body_width * 0.17, y0 + body_height * 0.61),
        ],
        dtype=np.int32,
    )
    cv2.fillConvexPoly(torso_support, torso_polygon, 255, lineType=cv2.LINE_AA)

    # Remove the original arm.  Only its shoulder overlap needs colour fill;
    # all pixels outside the body become transparent.
    shoulder_hole = cv2.bitwise_and(original_arm_mask, torso_support)
    shoulder_hole = cv2.dilate(
        (shoulder_hole >= 24).astype(np.uint8),
        np.ones((5, 5), dtype=np.uint8),
        iterations=1,
    )
    restored_rgb = cv2.cvtColor(
        cv2.inpaint(
            cv2.cvtColor(source[:, :, :3], cv2.COLOR_RGB2BGR),
            shoulder_hole,
            4,
            cv2.INPAINT_TELEA,
        ),
        cv2.COLOR_BGR2RGB,
    )
    body = source.copy()
    body[:, :, 3][original_arm_mask >= 24] = 0
    body[:, :, :3][shoulder_hole > 0] = restored_rgb[shoulder_hole > 0]
    body[:, :, 3][shoulder_hole > 0] = torso_support[shoulder_hole > 0]

    source_shoulder, source_hand = _bone_points(rig, arm_side)
    target_shoulder = np.asarray(
        [center_x + body_width * 0.09, y0 + body_height * 0.27],
        dtype=np.float32,
    )
    target_hand = np.asarray(
        [center_x - body_width * 0.04, y0 + body_height * 0.56],
        dtype=np.float32,
    )
    arm_transform = _similarity_transform(
        source_shoulder, source_hand, target_shoulder, target_hand
    )
    moved_arm = _warp(original_arm, arm_transform, (width, height))
    moved_arm_mask = moved_arm[:, :, 3]
    composite = _alpha_over(body, moved_arm)

    # A horizontal squeeze makes the proxy silhouette profile-like while
    # retaining the real face/clothing pixels that expose consistency limits.
    squeeze = 0.72
    squeeze_matrix = np.asarray(
        [[squeeze, 0.0, center_x * (1.0 - squeeze)], [0.0, 1.0, 0.0]],
        dtype=np.float32,
    )
    body = _warp(body, squeeze_matrix, (width, height))
    composite = _warp(composite, squeeze_matrix, (width, height))
    moved_arm_mask = _warp(
        moved_arm_mask,
        squeeze_matrix,
        (width, height),
        interpolation=cv2.INTER_NEAREST,
    )
    torso_support = _warp(
        torso_support,
        squeeze_matrix,
        (width, height),
        interpolation=cv2.INTER_NEAREST,
    )
    shoulder = squeeze_matrix[:, :2] @ target_shoulder + squeeze_matrix[:, 2]
    hip_point = np.asarray(
        [center_x, y0 + body_height * 0.57], dtype=np.float32
    )
    hip = squeeze_matrix[:, :2] @ hip_point + squeeze_matrix[:, 2]

    return SideSample(
        name="real_pixel_proxy",
        source_rgba=composite,
        arm_mask=moved_arm_mask,
        torso_mask=torso_support,
        shoulder=(float(shoulder[0]), float(shoulder[1])),
        hip=(float(hip[0]), float(hip[1])),
        ground_truth_rgba=body,
        source_path=str(texture_path),
        mask_source=f"rig group arm_{arm_side}, then synthetic reposition",
    )


def _automatic_masks(
    rgba: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, tuple[float, float], tuple[float, float]]:
    """Estimate masks for a right-facing side image.

    Explicit masks are preferred for a decision-grade trial.  This geometric
    fallback is intentionally conservative and is reported as estimated.
    """
    foreground = _foreground_mask(rgba)
    x0, y0, x1, y1 = _mask_bbox(foreground)
    width = float(x1 - x0)
    height = float(y1 - y0)
    shoulder = (x0 + width * 0.56, y0 + height * 0.28)
    elbow = (x0 + width * 0.42, y0 + height * 0.43)
    wrist = (x0 + width * 0.58, y0 + height * 0.57)
    arm_prior = np.zeros(rgba.shape[:2], dtype=np.uint8)
    cv2.polylines(
        arm_prior,
        [np.round([shoulder, elbow, wrist]).astype(np.int32)],
        False,
        255,
        max(9, int(round(width * 0.10))),
        lineType=cv2.LINE_AA,
    )
    arm_mask = cv2.bitwise_and(arm_prior, foreground)

    torso_mask = np.zeros_like(arm_mask)
    torso_points = np.asarray(
        [
            (x0 + width * 0.34, y0 + height * 0.20),
            (x0 + width * 0.68, y0 + height * 0.20),
            (x0 + width * 0.66, y0 + height * 0.62),
            (x0 + width * 0.36, y0 + height * 0.62),
        ],
        dtype=np.int32,
    )
    cv2.fillConvexPoly(torso_mask, torso_points, 255, lineType=cv2.LINE_AA)
    return (
        arm_mask,
        torso_mask,
        shoulder,
        (x0 + width * 0.50, y0 + height * 0.60),
    )


def make_input_sample(
    image_path: Path,
    arm_mask_path: Path | None = None,
    torso_mask_path: Path | None = None,
) -> SideSample:
    rgba = load_rgba(image_path)
    auto_arm, auto_torso, shoulder, hip = _automatic_masks(rgba)
    arm_mask = (
        load_mask(arm_mask_path, rgba.shape[:2])
        if arm_mask_path is not None
        else auto_arm
    )
    torso_mask = (
        load_mask(torso_mask_path, rgba.shape[:2])
        if torso_mask_path is not None
        else auto_torso
    )
    sources = []
    sources.append("explicit arm mask" if arm_mask_path else "estimated arm mask")
    sources.append(
        "explicit torso mask" if torso_mask_path else "estimated torso mask"
    )
    return SideSample(
        name="local_side_asset",
        source_rgba=rgba,
        arm_mask=arm_mask,
        torso_mask=torso_mask,
        shoulder=shoulder,
        hip=hip,
        source_path=str(image_path),
        mask_source=" + ".join(sources),
    )


def _edge_ring(mask: np.ndarray, radius: int = 3) -> np.ndarray:
    binary = (mask > 0).astype(np.uint8)
    size = radius * 2 + 1
    dilated = cv2.dilate(
        binary, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
    )
    return (dilated > binary).astype(np.uint8)


def _flat_colour_repair(
    restored_rgb: np.ndarray,
    source_rgb: np.ndarray,
    fill_mask: np.ndarray,
    context_mask: np.ndarray,
) -> np.ndarray:
    """Snap deep fill pixels toward the local cel-shading palette."""
    fill = (fill_mask > 0).astype(np.uint8)
    ring = (_edge_ring(fill, 5) > 0) & (context_mask > 0)
    colours = source_rgb[ring]
    if len(colours) < 12:
        return restored_rgb
    if len(colours) > 4000:
        step = max(1, len(colours) // 4000)
        colours = colours[::step]
    colours_f = colours.astype(np.float32)
    cluster_count = min(6, max(1, len(colours_f) // 24))
    cv2.setRNGSeed(2026)
    _compactness, _labels, palette = cv2.kmeans(
        colours_f,
        cluster_count,
        None,
        (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.4),
        1,
        cv2.KMEANS_PP_CENTERS,
    )

    repaired = restored_rgb.copy()
    rows, columns = np.where(fill > 0)
    pixels = restored_rgb[rows, columns].astype(np.float32)
    distances = ((pixels[:, None, :] - palette[None, :, :]) ** 2).sum(axis=2)
    snapped = palette[np.argmin(distances, axis=1)]
    depth = cv2.distanceTransform(fill, cv2.DIST_L2, 3)
    blend = np.clip((depth[rows, columns] - 0.5) / 4.0, 0.0, 0.68)
    mixed = pixels * (1.0 - blend[:, None]) + snapped * blend[:, None]
    repaired[rows, columns] = np.clip(mixed + 0.5, 0, 255).astype(np.uint8)
    return repaired


def _gradient_magnitude(rgb: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    return cv2.magnitude(gx, gy)


def _restoration_metrics(
    restored_rgb: np.ndarray,
    source_rgb: np.ndarray,
    fill_mask: np.ndarray,
    ground_truth_rgb: np.ndarray | None,
) -> dict[str, Any]:
    region = fill_mask > 0
    if not bool(region.any()):
        raise ValueError("fill mask is empty")
    restored_gradient = _gradient_magnitude(restored_rgb)
    if ground_truth_rgb is not None:
        truth_gradient = _gradient_magnitude(ground_truth_rgb)
        colour_error = float(
            np.abs(
                restored_rgb[region].astype(np.float32)
                - ground_truth_rgb[region].astype(np.float32)
            ).mean()
        )
        edge_error = float(
            np.abs(restored_gradient[region] - truth_gradient[region]).mean()
        )
        restored_energy = float(restored_gradient[region].mean())
        truth_energy = float(truth_gradient[region].mean())
        retention = restored_energy / max(1.0, truth_energy)
        show_through = colour_error > 14.0 or edge_error > 24.0
        mush = retention < 0.55
        noisy = retention > 1.90
        reasons = []
        if colour_error > 14.0:
            reasons.append("hidden garment colour/detail was not reconstructed")
        if edge_error > 24.0:
            reasons.append("hidden edge continuity differs from reference")
        if mush:
            reasons.append("gradient energy collapsed into a blurred patch")
        if noisy:
            reasons.append("fill introduced excessive edge energy")
        return {
            "reference": "known hidden torso",
            "colour_mae_0_255": round(colour_error, 3),
            "edge_smoothness_error": round(edge_error, 3),
            "gradient_retention": round(retention, 3),
            "show_through_or_wrong_fill": bool(show_through),
            "blur_or_mush": bool(mush),
            "visible_failure": bool(show_through or mush or noisy),
            "failure_reasons": reasons,
        }

    # No hidden reference exists for arbitrary input art.  Measure the seam
    # against a narrow outside ring and clearly label it as a surrogate.
    inner = (cv2.dilate(region.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0)
    inner &= region
    outer = _edge_ring(region.astype(np.uint8), 2) > 0
    outer_colours = source_rgb[outer]
    inner_colours = restored_rgb[inner]
    if not len(outer_colours) or not len(inner_colours):
        seam_error = 255.0
    else:
        seam_error = float(
            np.linalg.norm(
                inner_colours.astype(np.float32).mean(axis=0)
                - outer_colours.astype(np.float32).mean(axis=0)
            )
        )
    patch_energy = float(restored_gradient[region].mean())
    ring_energy = float(restored_gradient[outer].mean()) if bool(outer.any()) else 1.0
    retention = patch_energy / max(1.0, ring_energy)
    failure = seam_error > 28.0 or retention < 0.35
    return {
        "reference": "boundary surrogate only",
        "colour_mae_0_255": None,
        "edge_smoothness_error": round(seam_error, 3),
        "gradient_retention": round(retention, 3),
        "show_through_or_wrong_fill": bool(seam_error > 28.0),
        "blur_or_mush": bool(retention < 0.35),
        "visible_failure": bool(failure),
        "failure_reasons": (
            ["boundary-only check failed; hidden detail remains unknowable"]
            if failure
            else ["no clean hidden-torso reference; result is not decision-grade"]
        ),
    }


def _candidate_score(metrics: dict[str, Any]) -> float:
    colour = metrics.get("colour_mae_0_255")
    colour_score = float(colour) if colour is not None else 0.0
    edge = float(metrics["edge_smoothness_error"])
    retention = float(metrics["gradient_retention"])
    return colour_score + edge * 0.65 + abs(math.log(max(retention, 0.05))) * 7.0


def _opencv_restore(
    source_rgb: np.ndarray,
    fill_mask: np.ndarray,
    torso_mask: np.ndarray,
    ground_truth_rgb: np.ndarray | None,
) -> tuple[np.ndarray, str, float, dict[str, Any]]:
    started = time.perf_counter()
    bgr = cv2.cvtColor(source_rgb, cv2.COLOR_RGB2BGR)
    radius = max(
        2,
        min(7, int(round(math.sqrt(float((fill_mask > 0).sum())) / 35.0))),
    )
    candidates: list[
        tuple[float, np.ndarray, str, dict[str, Any], float]
    ] = []
    timings: dict[str, float] = {}
    for method, label in (
        (cv2.INPAINT_TELEA, "cv2-telea"),
        (cv2.INPAINT_NS, "cv2-navier-stokes"),
    ):
        candidate_started = time.perf_counter()
        filled_bgr = cv2.inpaint(bgr, fill_mask, radius, method)
        filled = cv2.cvtColor(filled_bgr, cv2.COLOR_BGR2RGB)
        filled = _flat_colour_repair(
            filled, source_rgb, fill_mask, torso_mask
        )
        metrics = _restoration_metrics(
            filled, source_rgb, fill_mask, ground_truth_rgb
        )
        candidate_ms = (time.perf_counter() - candidate_started) * 1000.0
        timings[label] = round(candidate_ms, 3)
        candidates.append(
            (_candidate_score(metrics), filled, label, metrics, candidate_ms)
        )
    _score, restored, label, metrics, selected_ms = min(
        candidates, key=lambda item: item[0]
    )
    metrics["candidate_cpu_ms"] = timings
    metrics["selection_total_cpu_ms"] = round(
        (time.perf_counter() - started) * 1000.0, 3
    )
    return restored, f"{label}+flat-palette", selected_ms, metrics


def _shape_dimension(value: Any, fallback: int) -> int:
    if isinstance(value, int) and value > 0:
        return value
    return fallback


def _lama_restore(
    source_rgb: np.ndarray,
    fill_mask: np.ndarray,
    model_path: Path,
) -> tuple[np.ndarray, float]:
    """Run common two-input LaMa ONNX exports on CPU."""
    import onnxruntime as ort  # Optional dependency, imported only on demand.

    started = time.perf_counter()
    session = ort.InferenceSession(
        str(model_path), providers=["CPUExecutionProvider"]
    )
    inputs = session.get_inputs()
    if len(inputs) < 2:
        raise ValueError("LaMa ONNX model must expose image and mask inputs")
    image_input = next(
        (
            item
            for item in inputs
            if "image" in item.name.lower()
            or (len(item.shape) == 4 and item.shape[1] == 3)
        ),
        inputs[0],
    )
    mask_input = next(
        (
            item
            for item in inputs
            if item.name != image_input.name
            and (
                "mask" in item.name.lower()
                or (len(item.shape) == 4 and item.shape[1] == 1)
            )
        ),
        inputs[1],
    )
    source_h, source_w = source_rgb.shape[:2]
    fallback_h = int(math.ceil(source_h / 8.0) * 8)
    fallback_w = int(math.ceil(source_w / 8.0) * 8)
    image_shape = image_input.shape
    target_h = _shape_dimension(image_shape[-2], fallback_h)
    target_w = _shape_dimension(image_shape[-1], fallback_w)
    resized_rgb = cv2.resize(
        source_rgb, (target_w, target_h), interpolation=cv2.INTER_AREA
    )
    resized_mask = cv2.resize(
        fill_mask, (target_w, target_h), interpolation=cv2.INTER_NEAREST
    )
    image_tensor = (
        resized_rgb.astype(np.float32).transpose(2, 0, 1)[None, ...] / 255.0
    )
    mask_tensor = (
        (resized_mask >= 1).astype(np.float32)[None, None, :, :]
    )
    feeds = {
        image_input.name: image_tensor,
        mask_input.name: mask_tensor,
    }
    output = np.asarray(session.run(None, feeds)[0], dtype=np.float32)
    if output.ndim != 4:
        raise ValueError(f"unexpected LaMa output shape: {output.shape}")
    if output.shape[1] in (3, 4):
        output = output[0, :3].transpose(1, 2, 0)
    elif output.shape[-1] in (3, 4):
        output = output[0, :, :, :3]
    else:
        raise ValueError(f"unexpected LaMa channel shape: {output.shape}")
    if float(output.min()) < -0.05:
        output = (output + 1.0) * 0.5
    if float(output.max()) > 2.0:
        output = output / 255.0
    output = np.clip(output * 255.0 + 0.5, 0, 255).astype(np.uint8)
    output = cv2.resize(
        output, (source_w, source_h), interpolation=cv2.INTER_CUBIC
    )
    restored = source_rgb.copy()
    restored[fill_mask > 0] = output[fill_mask > 0]
    return restored, (time.perf_counter() - started) * 1000.0


def separate_arm_and_torso(
    source_rgba: np.ndarray,
    arm_mask: np.ndarray,
    torso_mask: np.ndarray,
    *,
    shoulder: tuple[float, float] | None = None,
    hip: tuple[float, float] | None = None,
    ground_truth_rgba: np.ndarray | None = None,
    lama_model: Path | None = None,
    sample_name: str = "sample",
) -> LayeredSideWalk:
    """Split the near arm and restore the torso hidden beneath it."""
    source = _as_rgba(source_rgba)
    shape = source.shape[:2]
    arm = _as_mask(arm_mask, shape)
    torso = _as_mask(torso_mask, shape)
    foreground = _foreground_mask(source)
    arm = cv2.bitwise_and(arm, foreground)
    overlap = ((arm >= 24) & (torso >= 24)).astype(np.uint8)
    if int(overlap.sum()) < 8:
        raise ValueError("arm and torso masks have no meaningful overlap")
    fill = cv2.dilate(
        overlap,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
        iterations=1,
    )
    fill = ((fill > 0) & (torso >= 24)).astype(np.uint8) * 255

    ground_truth_rgb = None
    if ground_truth_rgba is not None:
        truth = _as_rgba(ground_truth_rgba)
        if truth.shape != source.shape:
            raise ValueError("ground truth shape must match source")
        ground_truth_rgb = truth[:, :, :3]

    warning: str | None = None
    if lama_model is not None:
        try:
            if not Path(lama_model).is_file():
                raise FileNotFoundError(lama_model)
            restored, elapsed_ms = _lama_restore(
                source[:, :, :3], fill, Path(lama_model)
            )
            restored = _flat_colour_repair(
                restored, source[:, :, :3], fill, torso
            )
            metrics = _restoration_metrics(
                restored, source[:, :, :3], fill, ground_truth_rgb
            )
            method = "lama-onnx-cpu+flat-palette"
        except Exception as error:
            warning = f"LaMa unavailable ({type(error).__name__}: {error}); used OpenCV"
            restored, method, elapsed_ms, metrics = _opencv_restore(
                source[:, :, :3], fill, torso, ground_truth_rgb
            )
    else:
        restored, method, elapsed_ms, metrics = _opencv_restore(
            source[:, :, :3], fill, torso, ground_truth_rgb
        )

    arm_layer = source.copy()
    arm_layer[:, :, 3] = np.minimum(source[:, :, 3], arm)
    body_layer = source.copy()
    body_layer[:, :, :3][fill > 0] = restored[fill > 0]
    body_layer[:, :, 3][arm >= 24] = 0
    body_layer[:, :, 3][fill > 0] = np.maximum(
        body_layer[:, :, 3][fill > 0], torso[fill > 0]
    )

    x0, y0, x1, y1 = _mask_bbox(foreground)
    inferred_shoulder = (
        x0 + (x1 - x0) * 0.56,
        y0 + (y1 - y0) * 0.28,
    )
    inferred_hip = (
        x0 + (x1 - x0) * 0.50,
        y0 + (y1 - y0) * 0.60,
    )
    torso_area = int((torso >= 24).sum())
    occlusion_area = int(overlap.sum())
    metrics.update(
        {
            "inpaint_cpu_ms": round(float(elapsed_ms), 3),
            "arm_occlusion_pixels": occlusion_area,
            "torso_pixels": torso_area,
            "arm_occlusion_ratio": round(
                occlusion_area / max(1, torso_area), 5
            ),
            "method": method,
            "lama_warning": warning,
        }
    )
    return LayeredSideWalk(
        name=sample_name,
        source_rgba=source,
        body_rgba=body_layer,
        arm_rgba=arm_layer,
        arm_mask=arm,
        torso_mask=torso,
        fill_mask=fill,
        shoulder=shoulder or inferred_shoulder,
        hip=hip or inferred_hip,
        method=method,
        cpu_ms=float(elapsed_ms),
        metrics=metrics,
    )


def _resize_rgba(image: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    return cv2.resize(image, size, interpolation=cv2.INTER_AREA)


def _place_layer(
    layer: np.ndarray,
    canvas_shape: tuple[int, int],
    offset: tuple[int, int],
) -> np.ndarray:
    canvas = np.zeros((*canvas_shape, 4), dtype=np.uint8)
    x, y = offset
    height, width = layer.shape[:2]
    canvas[y : y + height, x : x + width] = layer
    return canvas


def _transform_layer(
    layer: np.ndarray,
    pivot: tuple[float, float],
    angle: float,
    translate: tuple[float, float] = (0.0, 0.0),
) -> np.ndarray:
    height, width = layer.shape[:2]
    matrix = cv2.getRotationMatrix2D(pivot, angle, 1.0)
    matrix[0, 2] += float(translate[0])
    matrix[1, 2] += float(translate[1])
    return _warp(layer, matrix, (width, height))


def _over_rgb(background: np.ndarray, layer: np.ndarray) -> np.ndarray:
    alpha = layer[:, :, 3:4].astype(np.float32) / 255.0
    return np.clip(
        layer[:, :, :3].astype(np.float32) * alpha
        + background.astype(np.float32) * (1.0 - alpha)
        + 0.5,
        0,
        255,
    ).astype(np.uint8)


def render_walk_cycle(
    layered: LayeredSideWalk,
    frame_count: int = 6,
    cell_size: tuple[int, int] = (270, 420),
) -> list[np.ndarray]:
    """Render a six-frame front-arm/back-torso walk loop."""
    if frame_count != 6:
        raise ValueError("the controlled experiment is fixed at six frames")
    cell_width, cell_height = cell_size
    combined_alpha = np.maximum(
        layered.body_rgba[:, :, 3], layered.arm_rgba[:, :, 3]
    )
    x0, y0, x1, y1 = _mask_bbox(combined_alpha)
    source_height = y1 - y0
    margin = max(4, int(round(source_height * 0.025)))
    x0 = max(0, x0 - margin)
    y0 = max(0, y0 - margin)
    x1 = min(layered.body_rgba.shape[1], x1 + margin)
    y1 = min(layered.body_rgba.shape[0], y1 + margin)
    body_crop = layered.body_rgba[y0:y1, x0:x1]
    arm_crop = layered.arm_rgba[y0:y1, x0:x1]
    scale = min(
        (cell_height - 58) / max(1, body_crop.shape[0]),
        (cell_width - 26) / max(1, body_crop.shape[1]),
    )
    scaled_size = (
        max(1, int(round(body_crop.shape[1] * scale))),
        max(1, int(round(body_crop.shape[0] * scale))),
    )
    body_scaled = _resize_rgba(body_crop, scaled_size)
    arm_scaled = _resize_rgba(arm_crop, scaled_size)
    offset = (
        (cell_width - scaled_size[0]) // 2,
        cell_height - scaled_size[1] - 18,
    )
    body = _place_layer(
        body_scaled, (cell_height, cell_width), offset
    )
    arm = _place_layer(arm_scaled, (cell_height, cell_width), offset)
    shoulder = (
        offset[0] + (layered.shoulder[0] - x0) * scale,
        offset[1] + (layered.shoulder[1] - y0) * scale,
    )
    hip = (
        offset[0] + (layered.hip[0] - x0) * scale,
        offset[1] + (layered.hip[1] - y0) * scale,
    )

    yy, xx = np.indices((cell_height, cell_width))
    lower = (yy >= hip[1] - 5) & (body[:, :, 3] > 0)
    lower_columns = xx[lower]
    split_x = (
        float(np.median(lower_columns))
        if len(lower_columns)
        else float(hip[0])
    )
    far_mask = lower & (xx < split_x)
    near_mask = lower & ~far_mask
    far_leg = body.copy()
    near_leg = body.copy()
    far_leg[:, :, 3][~far_mask] = 0
    near_leg[:, :, 3][~near_mask] = 0
    torso = body.copy()
    torso[:, :, 3][yy > hip[1] + 5] = 0

    frames: list[np.ndarray] = []
    for index in range(frame_count):
        phase = 2.0 * math.pi * index / frame_count
        stride = math.sin(phase)
        bob = -3.0 * abs(math.sin(phase))
        background = np.full(
            (cell_height, cell_width, 3), (246, 247, 249), dtype=np.uint8
        )
        cv2.line(
            background,
            (12, cell_height - 17),
            (cell_width - 12, cell_height - 17),
            (208, 211, 218),
            1,
            cv2.LINE_AA,
        )
        far = _transform_layer(
            far_leg, hip, -8.0 * stride, (-4.0 * stride, bob)
        )
        front_leg = _transform_layer(
            near_leg, hip, 9.0 * stride, (5.0 * stride, bob)
        )
        trunk = _transform_layer(torso, hip, 0.0, (0.0, bob))
        front_arm = _transform_layer(
            arm, shoulder, -22.0 * stride, (0.0, bob)
        )
        for layer in (far, trunk, front_leg, front_arm):
            background = _over_rgb(background, layer)
        cv2.putText(
            background,
            f"{layered.name}  {index + 1}/6",
            (10, 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.43,
            (48, 52, 62),
            1,
            cv2.LINE_AA,
        )
        frames.append(background)
    return frames


def _thumbnail(
    rgba: np.ndarray,
    label: str,
    size: tuple[int, int] = (270, 360),
) -> np.ndarray:
    width, height = size
    tile = np.full((height, width, 3), 246, dtype=np.uint8)
    alpha = rgba[:, :, 3]
    try:
        x0, y0, x1, y1 = _mask_bbox(alpha)
    except ValueError:
        x0, y0, x1, y1 = 0, 0, rgba.shape[1], rgba.shape[0]
    crop = rgba[y0:y1, x0:x1]
    scale = min(
        (width - 24) / max(1, crop.shape[1]),
        (height - 46) / max(1, crop.shape[0]),
    )
    resized = _resize_rgba(
        crop,
        (
            max(1, int(round(crop.shape[1] * scale))),
            max(1, int(round(crop.shape[0] * scale))),
        ),
    )
    x = (width - resized.shape[1]) // 2
    y = height - resized.shape[0] - 12
    placed = _place_layer(resized, (height, width), (x, y))
    tile = _over_rgb(tile, placed)
    cv2.putText(
        tile,
        label,
        (9, 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.43,
        (45, 48, 57),
        1,
        cv2.LINE_AA,
    )
    return tile


def render_diagnostics(
    samples: Sequence[SideSample],
    layers: Sequence[LayeredSideWalk],
    output_path: Path,
) -> Path:
    rows = []
    for sample, layered in zip(samples, layers):
        arm_view = sample.source_rgba.copy()
        arm_view[:, :, :3][sample.arm_mask < 24] = 235
        arm_view[:, :, 3] = np.maximum(
            arm_view[:, :, 3], (sample.arm_mask >= 24).astype(np.uint8) * 255
        )
        fill_view = layered.body_rgba.copy()
        contours, _hierarchy = cv2.findContours(
            (layered.fill_mask > 0).astype(np.uint8),
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )
        cv2.drawContours(
            fill_view,
            contours,
            -1,
            (238, 74, 70, 255),
            2,
            lineType=cv2.LINE_AA,
        )
        row = np.concatenate(
            [
                _thumbnail(sample.source_rgba, f"{sample.name}: source"),
                _thumbnail(arm_view, "near arm mask"),
                _thumbnail(fill_view, f"restored: {layered.method}"),
            ],
            axis=1,
        )
        rows.append(row)
    sheet = np.concatenate(rows, axis=0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(sheet, "RGB").save(output_path, optimize=True)
    return output_path


def render_experiment_strip(
    layers: Sequence[LayeredSideWalk], output_path: Path
) -> Path:
    rows = [
        np.concatenate(render_walk_cycle(layered), axis=1)
        for layered in layers
    ]
    sheet = np.concatenate(rows, axis=0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(sheet, "RGB").save(output_path, optimize=True)
    return output_path


def _find_local_side_asset() -> Path | None:
    roots = (
        REPOSITORY / "public" / "media",
        REPOSITORY.parent / "SoundBookCF" / "public" / "media",
    )
    patterns = (
        "char-*-side.png",
        "*-side.png",
        "*side-profile*.png",
        "*profile*.png",
    )
    for root in roots:
        if not root.is_dir():
            continue
        for pattern in patterns:
            for candidate in sorted(root.glob(pattern)):
                if candidate.is_file():
                    return candidate
    return None


def _find_real_proxy_assets() -> tuple[Path, Path]:
    candidates = (
        REPOSITORY / "media" / "m0_real_gate" / "linlan_1011",
        REPOSITORY / "media" / "m0_real_gate" / "chen_2003",
        REPOSITORY / "media" / "m0_real_gate" / "linlan_1008",
    )
    for directory in candidates:
        texture = directory / "texture.png"
        rig = directory / "rig.json"
        if texture.is_file() and rig.is_file():
            return texture, rig
    raise FileNotFoundError(
        "no local side asset and no real rig texture available for proxy"
    )


def run_experiment(
    *,
    input_path: Path | None = None,
    arm_mask_path: Path | None = None,
    torso_mask_path: Path | None = None,
    lama_model: Path | None = None,
    output_dir: Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    """Run the controlled and local/real-pixel cases and write visual evidence."""
    samples = [make_synthetic_side_sample()]
    source_mode: str
    if input_path is not None:
        samples.append(
            make_input_sample(input_path, arm_mask_path, torso_mask_path)
        )
        source_mode = "explicit local side asset"
    else:
        local_side = _find_local_side_asset()
        if local_side is not None:
            samples.append(make_input_sample(local_side))
            source_mode = "auto-discovered local side asset"
        else:
            texture, rig = _find_real_proxy_assets()
            samples.append(make_real_pixel_proxy(texture, rig))
            source_mode = (
                "no local side image found; controlled profile + real-pixel "
                "synthetic side-motion proxy"
            )

    separated = [
        separate_arm_and_torso(
            sample.source_rgba,
            sample.arm_mask,
            sample.torso_mask,
            shoulder=sample.shoulder,
            hip=sample.hip,
            ground_truth_rgba=sample.ground_truth_rgba,
            lama_model=lama_model,
            sample_name=sample.name,
        )
        for sample in samples
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    strip_path = render_experiment_strip(
        separated, output_dir / "side_walk_strip.png"
    )
    diagnostics_path = render_diagnostics(
        samples, separated, output_dir / "side_walk_diagnostics.png"
    )
    stats = {
        "experiment": "m2-side-walk",
        "source_mode": source_mode,
        "lama_model": str(lama_model) if lama_model else None,
        "six_frame_strip": str(strip_path),
        "diagnostics": str(diagnostics_path),
        "samples": [
            {
                "name": sample.name,
                "source_path": sample.source_path,
                "mask_source": sample.mask_source,
                **layered.metrics,
            }
            for sample, layered in zip(samples, separated)
        ],
    }
    stats["all_samples_clean"] = all(
        not bool(item["visible_failure"]) for item in stats["samples"]
    )
    stats_path = output_dir / "stats.json"
    stats_path.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"side_walk: source mode: {source_mode}", flush=True)
    for item in stats["samples"]:
        print(
            "side_walk: "
            f"{item['name']} method={item['method']} "
            f"cpu={item['inpaint_cpu_ms']:.2f}ms "
            f"occlusion={item['arm_occlusion_ratio']:.2%} "
            f"visible_failure={item['visible_failure']}",
            flush=True,
        )
    print(f"side_walk: wrote {strip_path}", flush=True)
    print(f"side_walk: wrote {diagnostics_path}", flush=True)
    print(f"side_walk: wrote {stats_path}", flush=True)
    return stats


def _path_or_none(value: str | None) -> Path | None:
    return Path(value).resolve() if value else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        help="real side-view RGBA/green-screen image (auto-discovered if omitted)",
    )
    parser.add_argument(
        "--arm-mask",
        type=Path,
        help="optional near-side arm mask; white means arm",
    )
    parser.add_argument(
        "--torso-mask",
        type=Path,
        help="optional expected torso backing mask; white means torso",
    )
    parser.add_argument(
        "--lama-onnx",
        type=Path,
        help="optional LaMa ONNX model; CPU OpenCV is the safe fallback",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="gitignored experiment output directory",
    )
    arguments = parser.parse_args()
    if (arguments.arm_mask or arguments.torso_mask) and not arguments.input:
        parser.error("--arm-mask/--torso-mask require --input")
    run_experiment(
        input_path=_path_or_none(
            str(arguments.input) if arguments.input else None
        ),
        arm_mask_path=_path_or_none(
            str(arguments.arm_mask) if arguments.arm_mask else None
        ),
        torso_mask_path=_path_or_none(
            str(arguments.torso_mask) if arguments.torso_mask else None
        ),
        lama_model=_path_or_none(
            str(arguments.lama_onnx) if arguments.lama_onnx else None
        ),
        output_dir=arguments.output_dir.resolve(),
    )


if __name__ == "__main__":
    main()
