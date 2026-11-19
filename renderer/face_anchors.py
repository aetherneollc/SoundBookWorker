#!/usr/bin/env python3
"""Locate editable facial features in flat-shaded anime character art.

The public API uses *image coordinates*: ``eye_left`` and ``brow_left`` are
the features on the left side of the image, not the character's anatomical
left side.  Every box is ``{"x", "y", "w", "h"}`` in source-image pixels.

``rtmlib.Wholebody`` is used when available.  Its 68 face landmarks provide
good coarse geometry on Kolors artwork; colour segmentation and local dark
blob searches then tighten the boxes to the painted features.  A pure
skin/blob path remains available for machines without rtmlib (or with
``FACE_ANCHORS_DISABLE_RTMLIB=1``).

Running this file without positional arguments probes the known local Kolors
assets and writes overlays plus ``results.json`` to ``media/face_probe``::

    python renderer/face_anchors.py
    python renderer/face_anchors.py portrait.png --output-dir media/face_probe
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
import os
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

import cv2
import numpy as np
from PIL import Image


KNOWN_ASSETS = (
    "public/media/char-819cfd7d269b490b-head.png",
    "public/media/char-000c8a14f0f22053-head.png",
    "public/media/char-2318fcad5438b584-head.png",
    "public/media/char-888c29d8aae865f7-head.png",
    "manju/demo/cells/linmo-neutral.png",
    "manju/demo/cells/linmo-talk.png",
    "manju/demo/cells/linmo-shock.png",
)

_RTM_MODEL: Any | None = None
_RTM_ATTEMPTED = False


def _as_rgba(image: np.ndarray) -> np.ndarray:
    if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] not in (3, 4):
        raise ValueError("rgba must be an HxWx3 or HxWx4 numpy array")
    if image.dtype == np.uint8:
        out = image.copy()
    else:
        values = np.asarray(image)
        scale = 255.0 if np.issubdtype(values.dtype, np.floating) and float(np.nanmax(values)) <= 1.0 else 1.0
        out = np.nan_to_num(values * scale, nan=0.0, posinf=255.0, neginf=0.0)
        out = np.clip(out, 0, 255).astype(np.uint8)
    if out.shape[2] == 3:
        alpha = np.full((*out.shape[:2], 1), 255, dtype=np.uint8)
        out = np.concatenate((out, alpha), axis=2)
    return out


def _clip_box(
    box: Sequence[float | int], image_w: int, image_h: int, minimum: int = 1
) -> tuple[int, int, int, int]:
    if len(box) != 4:
        raise ValueError("box must contain x, y, w, h")
    x, y, w, h = (float(v) for v in box)
    x0 = max(0, min(image_w - 1, int(math.floor(x))))
    y0 = max(0, min(image_h - 1, int(math.floor(y))))
    x1 = max(x0 + minimum, min(image_w, int(math.ceil(x + max(minimum, w)))))
    y1 = max(y0 + minimum, min(image_h, int(math.ceil(y + max(minimum, h)))))
    x0 = max(0, min(x0, x1 - minimum))
    y0 = max(0, min(y0, y1 - minimum))
    return x0, y0, x1 - x0, y1 - y0


def _box_dict(box: Sequence[float | int], image_w: int, image_h: int) -> dict[str, int]:
    x, y, w, h = _clip_box(box, image_w, image_h)
    return {"x": x, "y": y, "w": w, "h": h}


def _box_tuple(box: dict[str, int] | Sequence[int]) -> tuple[int, int, int, int]:
    if isinstance(box, dict):
        return int(box["x"]), int(box["y"]), int(box["w"]), int(box["h"])
    return tuple(int(v) for v in box)  # type: ignore[return-value]


def _expand_box(
    box: Sequence[float | int], pad_x: float, pad_y: float, image_w: int, image_h: int
) -> tuple[int, int, int, int]:
    x, y, w, h = (float(v) for v in box)
    return _clip_box((x - pad_x, y - pad_y, w + 2 * pad_x, h + 2 * pad_y), image_w, image_h)


def _points_box(
    points: np.ndarray, image_w: int, image_h: int, pad_x: float = 0.0, pad_y: float = 0.0
) -> tuple[int, int, int, int]:
    points = np.asarray(points, dtype=np.float32).reshape(-1, 2)
    x0, y0 = np.min(points, axis=0)
    x1, y1 = np.max(points, axis=0)
    return _clip_box(
        (x0 - pad_x, y0 - pad_y, x1 - x0 + 2 * pad_x, y1 - y0 + 2 * pad_y),
        image_w,
        image_h,
    )


def _box_center(box: Sequence[int] | dict[str, int]) -> tuple[float, float]:
    x, y, w, h = _box_tuple(box)
    return x + w * 0.5, y + h * 0.5


def _box_union(
    first: Sequence[int], second: Sequence[int], image_w: int, image_h: int
) -> tuple[int, int, int, int]:
    ax, ay, aw, ah = _box_tuple(first)
    bx, by, bw, bh = _box_tuple(second)
    x0, y0 = min(ax, bx), min(ay, by)
    x1, y1 = max(ax + aw, bx + bw), max(ay + ah, by + bh)
    return _clip_box((x0, y0, x1 - x0, y1 - y0), image_w, image_h)


def _load_rtm_model() -> Any | None:
    """Load one process-wide Wholebody model; rtmlib downloads it on first use."""
    global _RTM_ATTEMPTED, _RTM_MODEL
    if os.getenv("FACE_ANCHORS_DISABLE_RTMLIB", "").strip().lower() in {"1", "true", "yes"}:
        return None
    if _RTM_ATTEMPTED:
        return _RTM_MODEL
    _RTM_ATTEMPTED = True
    try:
        # rtmw currently contains three harmless unused ONNX initializers.
        # Hiding those graph warnings keeps CLI JSON and unittest output clean.
        import onnxruntime as ort

        ort.set_default_logger_severity(3)
        from rtmlib import Wholebody

        _RTM_MODEL = Wholebody(
            mode="balanced",
            backend="onnxruntime",
            device="cpu",
            to_openpose=False,
        )
    except Exception:
        _RTM_MODEL = None
    return _RTM_MODEL


def _rtm_face_landmarks(
    rgba: np.ndarray,
    head_box: tuple[int, int, int, int] | None,
    nose_xy: tuple[float, float] | None,
) -> dict[str, Any] | None:
    model = _load_rtm_model()
    if model is None:
        return None
    image_h, image_w = rgba.shape[:2]
    rgb = rgba[:, :, :3]
    alpha = rgba[:, :, 3:4].astype(np.float32) / 255.0
    # A dark neutral matte preserves the strong silhouette of transparent
    # character sheets without introducing a false skin-coloured background.
    composited = np.clip(rgb.astype(np.float32) * alpha + 18.0 * (1.0 - alpha), 0, 255).astype(np.uint8)
    bgr = cv2.cvtColor(composited, cv2.COLOR_RGB2BGR)
    try:
        keypoints, scores = model(bgr)
    except Exception:
        return None
    keypoints = np.asarray(keypoints)
    scores = np.asarray(scores)
    if keypoints.ndim != 3 or scores.ndim != 2 or keypoints.shape[0] == 0 or keypoints.shape[1] < 91:
        return None

    best: tuple[float, int, np.ndarray, np.ndarray] | None = None
    for index in range(keypoints.shape[0]):
        face = keypoints[index, 23:91].astype(np.float32)
        face_scores = scores[index, 23:91].astype(np.float32)
        finite = np.isfinite(face).all(axis=1) & np.isfinite(face_scores)
        good = finite & (face_scores >= 0.25)
        if int(good.sum()) < 44:
            continue
        visible = face[good]
        span_w = float(np.ptp(visible[:, 0]))
        span_h = float(np.ptp(visible[:, 1]))
        if span_w < 10 or span_h < 10 or not (0.35 <= span_w / max(1.0, span_h) <= 2.4):
            continue
        center = visible.mean(axis=0)
        if not (-0.1 * image_w <= center[0] <= 1.1 * image_w and -0.1 * image_h <= center[1] <= 1.1 * image_h):
            continue
        median_score = float(np.median(face_scores[finite]))
        rank = median_score
        if head_box is not None:
            hx, hy, hw, hh = head_box
            inside = hx <= center[0] <= hx + hw and hy <= center[1] <= hy + hh
            rank += 0.35 if inside else -0.8
        if nose_xy is not None:
            distance = math.hypot(center[0] - nose_xy[0], center[1] - nose_xy[1])
            rank -= min(0.8, distance / max(20.0, math.hypot(image_w, image_h) * 0.25))
        if best is None or rank > best[0]:
            best = (rank, index, face, face_scores)
    if best is None:
        return None
    _, index, face, face_scores = best
    median_score = float(np.clip(np.median(face_scores), 0.0, 1.0))
    if median_score < 0.42:
        return None
    return {
        "points": face,
        "scores": face_scores,
        "confidence": median_score,
        "body_points": keypoints[index, :17].astype(np.float32),
        "body_scores": scores[index, :17].astype(np.float32),
    }


def _skin_threshold(rgba: np.ndarray, region: tuple[int, int, int, int]) -> np.ndarray:
    """Broad HSV + YCrCb mask for bright cel-shaded skin."""
    image_h, image_w = rgba.shape[:2]
    x, y, w, h = _clip_box(region, image_w, image_h)
    crop = rgba[y : y + h, x : x + w]
    rgb = crop[:, :, :3]
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    ycrcb = cv2.cvtColor(rgb, cv2.COLOR_RGB2YCrCb)
    hue, sat, val = (hsv[:, :, channel] for channel in range(3))
    yy, cr, cb = (ycrcb[:, :, channel] for channel in range(3))
    red = rgb[:, :, 0].astype(np.int16)
    green = rgb[:, :, 1].astype(np.int16)
    blue = rgb[:, :, 2].astype(np.int16)

    warm_hue = (hue <= 30) | (hue >= 172)
    pale = (sat <= 62) & (red >= green - 10) & (green >= blue - 13)
    ycc_skin = (cr >= 124) & (cr <= 198) & (cb >= 68) & (cb <= 151)
    channel_order = (red >= blue + 3) & (red >= green - 28) & (green >= blue - 34)
    valid = (crop[:, :, 3] >= 24) & (val >= 58) & (yy >= 48)
    mask = valid & ycc_skin & channel_order & (warm_hue | pale)

    local = mask.astype(np.uint8) * 255
    scale = max(1, int(round(min(w, h) / 110)))
    close_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * scale + 1, 2 * scale + 1))
    local = cv2.morphologyEx(local, cv2.MORPH_CLOSE, close_kernel)
    if scale > 1:
        local = cv2.morphologyEx(
            local,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
        )
    limited = np.zeros((image_h, image_w), dtype=np.uint8)
    limited[y : y + h, x : x + w] = local
    return limited


def _component_from_mask(
    mask: np.ndarray,
    region: tuple[int, int, int, int],
    target_xy: tuple[float, float] | None,
) -> tuple[np.ndarray, tuple[int, int, int, int] | None]:
    image_h, image_w = mask.shape
    x, y, w, h = _clip_box(region, image_w, image_h)
    local = mask[y : y + h, x : x + w]
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(local, 8)
    best_index = 0
    best_score = -1e9
    minimum_area = max(12, int(w * h * 0.0007))
    for index in range(1, count):
        bx, by, bw, bh, area = (int(v) for v in stats[index])
        if area < minimum_area or bw < 3 or bh < 3:
            continue
        aspect = bw / max(1.0, float(bh))
        if not (0.18 <= aspect <= 4.5):
            continue
        cx, cy = centroids[index]
        area_term = math.log1p(area)
        compactness = area / max(1.0, bw * bh)
        score = area_term + 0.8 * compactness
        if target_xy is not None:
            tx, ty = target_xy[0] - x, target_xy[1] - y
            distance = math.hypot(cx - tx, cy - ty) / max(8.0, math.hypot(w, h))
            score += 3.2 * max(0.0, 1.0 - 3.0 * distance)
            ix, iy = int(round(tx)), int(round(ty))
            if 0 <= ix < w and 0 <= iy < h and labels[iy, ix] == index:
                score += 4.0
        else:
            score += 0.8 * (1.0 - abs(cx / max(1, w) - 0.5))
            score += 0.5 * (1.0 - cy / max(1, h))
        if area > w * h * 0.72:
            score -= 3.0
        if score > best_score:
            best_score, best_index = score, index
    selected = np.zeros_like(mask)
    if best_index == 0:
        return selected, None
    local_selected = labels == best_index
    selected[y : y + h, x : x + w] = local_selected.astype(np.uint8) * 255
    bx, by, bw, bh, _ = (int(v) for v in stats[best_index])
    return selected, _clip_box((x + bx, y + by, bw, bh), image_w, image_h)


def _foreground_box(rgba: np.ndarray) -> tuple[int, int, int, int]:
    image_h, image_w = rgba.shape[:2]
    alpha = rgba[:, :, 3] >= 24
    ys, xs = np.nonzero(alpha)
    if xs.size == 0:
        return 0, 0, image_w, image_h
    return _clip_box(
        (int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)),
        image_w,
        image_h,
    )


def _auto_head_box(
    rgba: np.ndarray, nose_xy: tuple[float, float] | None
) -> tuple[int, int, int, int]:
    image_h, image_w = rgba.shape[:2]
    fx, fy, fw, fh = _foreground_box(rgba)
    search_h = max(20, min(fh, int(round(fh * 0.52))))
    search = _clip_box((fx, fy, fw, search_h), image_w, image_h)
    all_skin = _skin_threshold(rgba, search)
    component, skin_box = _component_from_mask(all_skin, search, nose_xy)
    del component
    if skin_box is not None:
        x, y, w, h = skin_box
        # A face skin component can include ears or a short neck.  Expansion
        # primarily adds hair above and to the sides.
        head_w = max(w * 1.55, h * 1.05, 24.0)
        head_h = max(h * 1.55, w * 1.35, 28.0)
        center_x = x + w * 0.5
        return _clip_box(
            (center_x - head_w * 0.5, y - h * 0.48, head_w, head_h),
            image_w,
            image_h,
        )
    if nose_xy is not None:
        size = max(32.0, min(fw * 0.5, fh * 0.28, min(image_w, image_h) * 0.55))
        return _clip_box(
            (nose_xy[0] - size * 0.55, nose_xy[1] - size * 0.55, size * 1.1, size * 1.35),
            image_w,
            image_h,
        )
    head_h = max(32.0, min(fh * 0.30, fw * 0.95))
    head_w = max(28.0, min(fw, head_h * 0.92))
    return _clip_box(
        (fx + (fw - head_w) * 0.5, fy, head_w, head_h),
        image_w,
        image_h,
    )


def _needs_head_crop(
    rgba: np.ndarray,
    head_box: tuple[int, int, int, int],
) -> bool:
    """Whether the face is too small for a full-frame 256x192 pose pass."""
    image_h, image_w = rgba.shape[:2]
    if max(image_h, image_w) < 720:
        return False
    _fx, _fy, fw, fh = _foreground_box(rgba)
    foreground_aspect = fh / max(1.0, float(fw))
    return foreground_aspect > 1.35 or head_box[3] < image_h * 0.40


def _head_context_crop(
    head_box: tuple[int, int, int, int],
    image_w: int,
    image_h: int,
    coarse_rtm: dict[str, Any] | None = None,
) -> tuple[int, int, int, int]:
    """Head + shoulders crop where the head occupies roughly 56% of height."""
    x, y, w, h = head_box
    crop_h = max(h * 1.78, 96.0)
    center_x = x + w * 0.5
    crop_top = y - h * 0.10
    if coarse_rtm is not None:
        body = np.asarray(coarse_rtm.get("body_points"), dtype=np.float32)
        scores = np.asarray(coarse_rtm.get("body_scores"), dtype=np.float32)
        if body.ndim == 2 and scores.ndim == 1 and body.shape[0] >= 7 and scores.shape[0] >= 7:
            if scores[3] >= 0.35 and scores[4] >= 0.35:
                ear_center_x = float((body[3, 0] + body[4, 0]) * 0.5)
                center_x = center_x * 0.75 + ear_center_x * 0.25
            if scores[5] >= 0.35 and scores[6] >= 0.35:
                shoulder_y = float((body[5, 1] + body[6, 1]) * 0.5)
                desired_bottom = shoulder_y + h * 0.16
                crop_h = max(crop_h, min(h * 2.15, desired_bottom - crop_top))
    crop_w = max(w * 1.65, crop_h * 0.90, 96.0)
    return _clip_box(
        (center_x - crop_w * 0.5, crop_top, crop_w, crop_h),
        image_w,
        image_h,
    )


def _offset_rtm_result(
    result: dict[str, Any],
    offset_x: int,
    offset_y: int,
) -> dict[str, Any]:
    mapped = dict(result)
    shift = np.array([offset_x, offset_y], dtype=np.float32)
    mapped["points"] = np.asarray(result["points"], dtype=np.float32) + shift
    mapped["body_points"] = np.asarray(result["body_points"], dtype=np.float32) + shift
    return mapped


def _rtm_result_matches_head(
    result: dict[str, Any],
    head_box: tuple[int, int, int, int],
) -> bool:
    points = np.asarray(result["points"], dtype=np.float32)
    center = np.mean(points, axis=0)
    hx, hy, hw, hh = head_box
    margin_x, margin_y = hw * 0.28, hh * 0.24
    span_w = float(np.ptp(points[:, 0]))
    span_h = float(np.ptp(points[:, 1]))
    return bool(
        hx - margin_x <= center[0] <= hx + hw + margin_x
        and hy - margin_y <= center[1] <= hy + hh + margin_y
        and span_w >= max(12.0, hw * 0.22)
        and span_h >= max(12.0, hh * 0.22)
    )


def _head_from_landmarks(points: np.ndarray, image_w: int, image_h: int) -> tuple[int, int, int, int]:
    x0, y0 = np.min(points, axis=0)
    x1, y1 = np.max(points, axis=0)
    face_w, face_h = max(12.0, float(x1 - x0)), max(12.0, float(y1 - y0))
    return _clip_box(
        (
            x0 - face_w * 0.34,
            y0 - face_h * 0.88,
            face_w * 1.68,
            face_h * 2.14,
        ),
        image_w,
        image_h,
    )


def _face_box_from_landmarks(
    points: np.ndarray,
    skin_component: np.ndarray,
    component_box: tuple[int, int, int, int] | None,
    image_w: int,
    image_h: int,
) -> tuple[int, int, int, int]:
    jaw = points[:17]
    upper = points[17:48]
    x0 = float(np.min(jaw[:, 0]))
    x1 = float(np.max(jaw[:, 0]))
    y0 = float(np.min(upper[:, 1]))
    y1 = float(np.max(jaw[:, 1]))
    face_w, face_h = max(10.0, x1 - x0), max(10.0, y1 - y0)
    landmark_box = _clip_box(
        (x0 - face_w * 0.04, y0 - face_h * 0.06, face_w * 1.08, face_h * 1.11),
        image_w,
        image_h,
    )
    if component_box is None or not np.any(skin_component):
        return landmark_box
    lx, ly, lw, lh = landmark_box
    band_y0 = max(0, int(round(ly - 0.04 * lh)))
    band_y1 = min(image_h, int(round(ly + 1.05 * lh)))
    restricted = skin_component[band_y0:band_y1] > 0
    ys, xs = np.nonzero(restricted)
    if xs.size < 16:
        return landmark_box
    skin_x0, skin_x1 = int(xs.min()), int(xs.max())
    skin_y0, skin_y1 = int(ys.min() + band_y0), int(ys.max() + band_y0)
    # Ears often extend just beyond the 68-point jaw, while connected neck
    # skin can be much wider; only accept a modest horizontal extension.
    if skin_x1 - skin_x0 <= lw * 1.55:
        x0 = min(lx, skin_x0)
        x1 = max(lx + lw - 1, skin_x1)
    else:
        x0, x1 = lx, lx + lw - 1
    y0 = min(ly, skin_y0)
    y1 = max(ly + lh - 1, min(skin_y1, int(round(ly + lh * 1.04))))
    return _clip_box((x0, y0, x1 - x0 + 1, y1 - y0 + 1), image_w, image_h)


def _fallback_face_box(
    component_box: tuple[int, int, int, int] | None,
    head_box: tuple[int, int, int, int],
    image_w: int,
    image_h: int,
) -> tuple[int, int, int, int]:
    if component_box is not None:
        x, y, w, h = component_box
        return _clip_box((x - 0.03 * w, y - 0.02 * h, w * 1.06, h * 1.04), image_w, image_h)
    x, y, w, h = head_box
    return _clip_box((x + 0.16 * w, y + 0.18 * h, w * 0.68, h * 0.70), image_w, image_h)


def _robust_rgb(pixels: np.ndarray, fallback: Sequence[int]) -> list[int]:
    values = np.asarray(pixels, dtype=np.uint8).reshape(-1, 3)
    if len(values) < 4:
        return [int(v) for v in fallback]
    lum = values.astype(np.float32) @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    if len(values) >= 24:
        low, high = np.percentile(lum, (7, 93))
        trimmed = values[(lum >= low) & (lum <= high)]
        if len(trimmed) >= 8:
            values = trimmed
    median = np.median(values.astype(np.float32), axis=0)
    distance = np.linalg.norm(values.astype(np.float32) - median, axis=1)
    close = values[distance <= np.percentile(distance, 72)]
    if len(close) >= 4:
        median = np.median(close.astype(np.float32), axis=0)
    return [int(round(float(v))) for v in median]


def _provisional_skin_rgb(
    rgba: np.ndarray, face_mask: np.ndarray, face_box: tuple[int, int, int, int]
) -> list[int]:
    x, y, w, h = face_box
    inner = np.zeros(face_mask.shape, dtype=bool)
    inner[
        y + int(0.22 * h) : y + max(int(0.23 * h) + 1, int(0.80 * h)),
        x + int(0.12 * w) : x + max(int(0.13 * w) + 1, int(0.88 * w)),
    ] = True
    selected = (face_mask > 0) & inner
    pixels = rgba[:, :, :3][selected]
    if len(pixels) < 12:
        pixels = rgba[:, :, :3][face_mask > 0]
    return _robust_rgb(pixels, (232, 190, 160))


def _feature_pixels_mask(
    rgba: np.ndarray,
    skin_rgb: Sequence[int],
    region: tuple[int, int, int, int] | None = None,
) -> np.ndarray:
    image_h, image_w = rgba.shape[:2]
    x, y, w, h = (0, 0, image_w, image_h) if region is None else _clip_box(region, image_w, image_h)
    crop = rgba[y : y + h, x : x + w]
    rgb = crop[:, :, :3].astype(np.float32)
    skin = np.asarray(skin_rgb, dtype=np.float32)
    lum = rgb @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    skin_lum = float(skin @ np.array([0.299, 0.587, 0.114], dtype=np.float32))
    distance = np.linalg.norm(rgb - skin, axis=2)
    red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    chroma = np.max(rgb, axis=2) - np.min(rgb, axis=2)
    dark_limit = min(184.0, max(62.0, skin_lum - 27.0))
    dark = ((lum <= dark_limit) & (distance >= 18.0)) | ((lum < 112.0) & (chroma < 82.0))
    lip = (red >= green + 15.0) & (red >= blue + 8.0) & (chroma >= 24.0) & (lum < skin_lum - 7.0)
    local = (dark | lip) & (crop[:, :, 3] >= 24)
    result = np.zeros((image_h, image_w), dtype=bool)
    result[y : y + h, x : x + w] = local
    return result


def _nearest_component_box(
    feature_mask: np.ndarray,
    search_box: tuple[int, int, int, int],
    target: tuple[float, float],
    image_w: int,
    image_h: int,
    horizontal: bool = False,
) -> tuple[int, int, int, int] | None:
    x, y, w, h = _clip_box(search_box, image_w, image_h)
    local = feature_mask[y : y + h, x : x + w].astype(np.uint8) * 255
    if horizontal:
        local = cv2.dilate(local, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1)))
    count, _labels, stats, centroids = cv2.connectedComponentsWithStats(local, 8)
    best: tuple[float, tuple[int, int, int, int]] | None = None
    for index in range(1, count):
        bx, by, bw, bh, area = (int(v) for v in stats[index])
        if area < 1 or bw * bh > w * h * 0.72:
            continue
        cx, cy = centroids[index]
        gx, gy = x + cx, y + cy
        dx = abs(gx - target[0]) / max(2.0, w)
        dy = abs(gy - target[1]) / max(2.0, h)
        aspect_bonus = min(1.5, bw / max(1.0, bh)) if horizontal else 0.0
        score = math.log1p(area) + 0.25 * aspect_bonus - 5.0 * dx - 7.0 * dy
        if best is None or score > best[0]:
            best = (score, (x + bx, y + by, bw, bh))
    return None if best is None else best[1]


def _minimum_feature_box(
    box: tuple[int, int, int, int],
    center: tuple[float, float],
    min_w: float,
    min_h: float,
    image_w: int,
    image_h: int,
) -> tuple[int, int, int, int]:
    x, y, w, h = box
    width, height = max(float(w), min_w), max(float(h), min_h)
    cx, cy = center
    return _clip_box((cx - width * 0.5, cy - height * 0.5, width, height), image_w, image_h)


def _rtm_feature_boxes(
    rgba: np.ndarray,
    points: np.ndarray,
    skin_rgb: Sequence[int],
    face_mask: np.ndarray,
    face_box: tuple[int, int, int, int],
) -> dict[str, tuple[int, int, int, int]]:
    image_h, image_w = rgba.shape[:2]
    x0, y0 = np.min(points, axis=0)
    x1, y1 = np.max(points, axis=0)
    face_w, face_h = max(12.0, float(x1 - x0)), max(12.0, float(y1 - y0))
    feature_region = _expand_box(face_box, face_w * 0.12, face_h * 0.12, image_w, image_h)
    feature_mask = _feature_pixels_mask(rgba, skin_rgb, feature_region)
    feature_mask &= _face_hull(face_mask, face_box) > 0

    def refined_brow(
        group: np.ndarray,
    ) -> tuple[int, int, int, int]:
        center = tuple(float(v) for v in np.mean(group, axis=0))
        seed = _points_box(group, image_w, image_h, 1.0, 1.0)
        search = _expand_box(seed, face_w * 0.045, face_h * 0.045, image_w, image_h)
        found = _nearest_component_box(feature_mask, search, center, image_w, image_h, horizontal=True)
        pad = max(1.5, face_w * 0.008)
        if found is None:
            result_center = center
            width = seed[2] + pad * 2
            height = seed[3] + pad * 2
        else:
            found_center = _box_center(found)
            result_center = (
                found_center[0],
                found_center[1]
                if abs(found_center[1] - center[1]) <= face_h * 0.06
                else center[1],
            )
            width = max(found[2] + pad * 2, seed[2] * 0.86)
            height = max(found[3] + pad * 2, face_h * 0.025)
        # Eyebrows are thin strokes.  Capping their vertical extent prevents
        # nearby fringe/hair pixels from making a brow box look like an eye.
        height = min(height, max(5.0, face_h * 0.085))
        return _clip_box(
            (
                result_center[0] - width * 0.5,
                result_center[1] - height * 0.5,
                width,
                height,
            ),
            image_w,
            image_h,
        )

    eye_groups = [points[36:42], points[42:48]]
    brow_groups = [points[17:22], points[22:27]]
    eye_groups.sort(key=lambda group: float(np.mean(group[:, 0])))
    brow_groups.sort(key=lambda group: float(np.mean(group[:, 0])))
    brows = [refined_brow(group) for group in brow_groups]

    eyes: list[tuple[int, int, int, int]] = []
    for group, brow in zip(eye_groups, brows):
        landmark_center = tuple(float(v) for v in np.mean(group, axis=0))
        seed = _points_box(group, image_w, image_h, 1.5, 1.5)
        search = _expand_box(seed, face_w * 0.085, face_h * 0.08, image_w, image_h)
        sx, sy, sw, sh = search
        brow_center_y = _box_center(brow)[1]
        minimum_y = int(math.floor(brow_center_y + max(1.0, face_h * 0.006)))
        if sy < minimum_y < sy + sh:
            search = _clip_box((sx, minimum_y, sw, sy + sh - minimum_y), image_w, image_h)
        found = _nearest_component_box(feature_mask, search, landmark_center, image_w, image_h)
        result = seed if found is None else _box_union(seed, found, image_w, image_h)
        result = _expand_box(result, 1.5, 1.5, image_w, image_h)
        minimum_h = max(
            face_h * 0.075,
            min(face_h * 0.13, brow[3] * 1.15),
        )
        eye_center_y = max(landmark_center[1], brow_center_y + minimum_h * 0.58)
        result = _minimum_feature_box(
            result,
            (landmark_center[0], eye_center_y),
            face_w * 0.105,
            minimum_h,
            image_w,
            image_h,
        )
        eyes.append(result)

    mouth_points = points[48:68]
    seed = _points_box(mouth_points, image_w, image_h)
    seed_x, seed_y, seed_w, seed_h = seed
    eye_centers = [np.mean(group, axis=0) for group in eye_groups]
    eye_mid = (eye_centers[0] + eye_centers[1]) * 0.5
    eye_distance = max(8.0, float(abs(eye_centers[1][0] - eye_centers[0][0])))
    mouth_prior = np.mean(mouth_points, axis=0)
    frontal = eye_distance / max(1.0, face_w) >= 0.22
    center_x = float(0.68 * eye_mid[0] + 0.32 * mouth_prior[0]) if frontal else float(mouth_prior[0])
    nose_bottom = float(np.max(points[27:36, 1]))
    jaw_bottom = float(np.max(points[:17, 1]))
    roi_half_w = max(seed_w * 0.85, eye_distance * 0.42, face_w * 0.24)
    roi_y0 = max(nose_bottom + face_h * 0.015, seed_y - face_h * 0.08)
    roi_y1 = min(jaw_bottom - face_h * 0.035, seed_y + seed_h + face_h * 0.10)
    if roi_y1 - roi_y0 < max(6.0, seed_h * 0.55):
        roi_y0 = float(mouth_prior[1] - max(4.0, face_h * 0.06))
        roi_y1 = float(mouth_prior[1] + max(5.0, face_h * 0.08))
    mouth_search = _clip_box(
        (center_x - roi_half_w, roi_y0, roi_half_w * 2.0, roi_y1 - roi_y0),
        image_w,
        image_h,
    )
    mx, my, mw, mh = mouth_search
    local = feature_mask[my : my + mh, mx : mx + mw].astype(np.uint8) * 255
    bridge = max(3, int(round(face_w * 0.025)))
    if bridge % 2 == 0:
        bridge += 1
    joined = cv2.morphologyEx(
        local,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (bridge, 3)),
    )
    count, _labels, stats, centroids = cv2.connectedComponentsWithStats(joined, 8)
    best_component: tuple[float, tuple[int, int, int, int]] | None = None
    for index in range(1, count):
        bx, by, bw, bh, area = (int(v) for v in stats[index])
        if area < 2 or bw * bh > mw * mh * 0.70:
            continue
        cx, cy = centroids[index]
        gx, gy = mx + cx, my + cy
        score = (
            math.log1p(area)
            + 1.8 * min(1.0, bw / max(1.0, eye_distance * 0.35))
            + 0.4 * min(2.0, bw / max(1.0, bh))
            - 5.0 * abs(gx - center_x) / max(2.0, mw)
            - 7.0 * abs(gy - mouth_prior[1]) / max(2.0, mh)
        )
        if best_component is None or score > best_component[0]:
            best_component = (score, (mx + bx, my + by, bw, bh))

    if best_component is not None:
        _score, component = best_component
        component_center_y = _box_center(component)[1]
        band_half = max(component[3] * 0.75, seed_h * 0.38, face_h * 0.025)
        band_y0 = max(my, int(math.floor(component_center_y - band_half)))
        band_y1 = min(my + mh, int(math.ceil(component_center_y + band_half)) + 1)
        band = feature_mask[band_y0:band_y1, mx : mx + mw]
        ys, xs = np.nonzero(band)
        if xs.size:
            candidate_x0 = mx + int(xs.min())
            candidate_x1 = mx + int(xs.max()) + 1
            candidate_y0 = band_y0 + int(ys.min())
            candidate_y1 = band_y0 + int(ys.max()) + 1
        else:
            candidate_x0, candidate_y0, cw, ch = component
            candidate_x1, candidate_y1 = candidate_x0 + cw, candidate_y0 + ch
    else:
        candidate_x0, candidate_y0 = seed_x, seed_y
        candidate_x1, candidate_y1 = seed_x + seed_w, seed_y + seed_h

    minimum_half = max(seed_w * 0.50, eye_distance * 0.17, face_w * 0.09)
    maximum_half = max(seed_w * 0.80, eye_distance * 0.34, face_w * 0.22)
    horizontal_pad = max(3.0, face_w * 0.015)
    half_width = max(
        minimum_half,
        center_x - candidate_x0,
        candidate_x1 - center_x,
    )
    half_width = min(maximum_half, half_width + horizontal_pad)
    vertical_pad = max(3.0, face_h * 0.014)
    mouth_top = max(nose_bottom + 1.0, candidate_y0 - vertical_pad)
    mouth_bottom = min(jaw_bottom - face_h * 0.02, candidate_y1 + vertical_pad)
    if mouth_bottom - mouth_top < 5:
        mouth_top = float(mouth_prior[1] - 3)
        mouth_bottom = float(mouth_prior[1] + 3)
    mouth = _clip_box(
        (center_x - half_width, mouth_top, half_width * 2.0, mouth_bottom - mouth_top),
        image_w,
        image_h,
    )
    return {
        "eye_left": eyes[0],
        "eye_right": eyes[1],
        "brow_left": brows[0],
        "brow_right": brows[1],
        "mouth": mouth,
    }


def _face_hull(face_mask: np.ndarray, face_box: tuple[int, int, int, int]) -> np.ndarray:
    hull_mask = np.zeros_like(face_mask)
    contours, _ = cv2.findContours(face_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        contour = max(contours, key=cv2.contourArea)
        hull = cv2.convexHull(contour)
        cv2.fillConvexPoly(hull_mask, hull, 255)
    else:
        x, y, w, h = face_box
        cv2.ellipse(hull_mask, (x + w // 2, y + h // 2), (max(1, w // 2), max(1, h // 2)), 0, 0, 360, 255, -1)
    return hull_mask


def _fallback_feature_boxes(
    rgba: np.ndarray,
    face_mask: np.ndarray,
    face_box: tuple[int, int, int, int],
    skin_rgb: Sequence[int],
) -> dict[str, tuple[int, int, int, int]]:
    image_h, image_w = rgba.shape[:2]
    fx, fy, fw, fh = face_box
    hull = _face_hull(face_mask, face_box) > 0
    dark = _feature_pixels_mask(
        rgba,
        skin_rgb,
        _expand_box(face_box, fw * 0.08, fh * 0.08, image_w, image_h),
    ) & hull

    eye_band = np.zeros_like(dark)
    ex0, ex1 = fx + int(0.08 * fw), fx + int(0.92 * fw)
    ey0, ey1 = fy + int(0.22 * fh), fy + int(0.58 * fh)
    eye_band[max(0, ey0) : min(image_h, ey1), max(0, ex0) : min(image_w, ex1)] = True
    joined = (dark & eye_band).astype(np.uint8) * 255
    joined = cv2.dilate(joined, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 2)))
    count, _labels, stats, centroids = cv2.connectedComponentsWithStats(joined, 8)
    candidates: list[tuple[tuple[int, int, int, int], tuple[float, float], int]] = []
    for index in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[index])
        if area < 2 or area > fw * fh * 0.075 or w < 2:
            continue
        cx, cy = (float(v) for v in centroids[index])
        candidates.append(((x, y, w, h), (cx, cy), area))

    best_pair: tuple[float, Any, Any] | None = None
    for left_index, left in enumerate(candidates):
        for right in candidates[left_index + 1 :]:
            a, b = sorted((left, right), key=lambda item: item[1][0])
            separation = b[1][0] - a[1][0]
            if not (0.18 * fw <= separation <= 0.72 * fw):
                continue
            y_delta = abs(a[1][1] - b[1][1])
            if y_delta > 0.15 * fh:
                continue
            target_y = fy + 0.39 * fh
            symmetry = abs(((a[1][0] + b[1][0]) * 0.5) - (fx + fw * 0.5)) / max(1.0, fw)
            score = (
                math.log1p(a[2] + b[2])
                - 4.0 * y_delta / max(1.0, fh)
                - 2.2 * abs((a[1][1] + b[1][1]) * 0.5 - target_y) / max(1.0, fh)
                - 1.4 * symmetry
            )
            if best_pair is None or score > best_pair[0]:
                best_pair = (score, a, b)

    eye_w, eye_h = max(5.0, fw * 0.16), max(4.0, fh * 0.085)
    if best_pair is None:
        eye_centers = ((fx + fw * 0.30, fy + fh * 0.39), (fx + fw * 0.70, fy + fh * 0.39))
        eye_boxes = [
            _clip_box((cx - eye_w * 0.5, cy - eye_h * 0.5, eye_w, eye_h), image_w, image_h)
            for cx, cy in eye_centers
        ]
    else:
        eye_boxes = []
        for item in best_pair[1:]:
            box, center, _area = item
            padded = _expand_box(box, 2.0, 2.0, image_w, image_h)
            eye_boxes.append(_minimum_feature_box(padded, center, eye_w, eye_h, image_w, image_h))
        eye_boxes.sort(key=lambda box: _box_center(box)[0])

    brow_boxes: list[tuple[int, int, int, int]] = []
    for eye in eye_boxes:
        cx, _cy = _box_center(eye)
        ex, ey, ew, eh = eye
        search = _clip_box(
            (ex - 0.08 * fw, ey - 0.18 * fh, ew + 0.16 * fw, 0.18 * fh),
            image_w,
            image_h,
        )
        target = (cx, ey - 0.07 * fh)
        found = _nearest_component_box(dark, search, target, image_w, image_h, horizontal=True)
        if (
            found is None
            or found[2] > 0.40 * fw
            or found[3] > 0.11 * fh
        ):
            found = _clip_box(
                (cx - ew * 0.55, ey - 0.09 * fh, ew * 1.1, max(3, 0.035 * fh)),
                image_w,
                image_h,
            )
        brow_boxes.append(_expand_box(found, 2.0, 2.0, image_w, image_h))

    left_center, right_center = (_box_center(box) for box in eye_boxes)
    eye_distance = max(8.0, right_center[0] - left_center[0])
    expected_mouth = (
        (left_center[0] + right_center[0]) * 0.5,
        (left_center[1] + right_center[1]) * 0.5 + eye_distance * 0.88,
    )
    mouth_y0 = max(fy + 0.52 * fh, expected_mouth[1] - 0.42 * eye_distance)
    mouth_y1 = min(fy + 0.91 * fh, expected_mouth[1] + 0.50 * eye_distance)
    mouth_search = _clip_box(
        (
            expected_mouth[0] - 0.52 * eye_distance,
            mouth_y0,
            eye_distance * 1.04,
            max(5.0, mouth_y1 - mouth_y0),
        ),
        image_w,
        image_h,
    )
    mouth_found = _nearest_component_box(dark, mouth_search, expected_mouth, image_w, image_h, horizontal=True)
    if mouth_found is None:
        mouth_found = _clip_box(
            (expected_mouth[0] - eye_distance * 0.20, expected_mouth[1] - 2, eye_distance * 0.40, 4),
            image_w,
            image_h,
        )
    mouth = _expand_box(mouth_found, 3.0, 3.0, image_w, image_h)
    return {
        "eye_left": eye_boxes[0],
        "eye_right": eye_boxes[1],
        "brow_left": brow_boxes[0],
        "brow_right": brow_boxes[1],
        "mouth": mouth,
    }


def _sample_skin_rgb(
    rgba: np.ndarray,
    face_mask: np.ndarray,
    face_box: tuple[int, int, int, int],
    features: dict[str, tuple[int, int, int, int]],
    fallback: Sequence[int],
) -> list[int]:
    image_h, image_w = rgba.shape[:2]
    fx, fy, fw, fh = face_box
    safe = face_mask > 0
    kernel_size = max(3, 2 * int(round(min(fw, fh) / 80)) + 1)
    eroded = cv2.erode(
        safe.astype(np.uint8) * 255,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size)),
    ) > 0
    mouth = features["mouth"]
    mx, my, mw, mh = mouth
    local = np.zeros_like(safe)
    ring_box = _expand_box(mouth, max(7.0, mw * 0.75), max(7.0, mh * 1.1), image_w, image_h)
    rx, ry, rw, rh = ring_box
    local[ry : ry + rh, rx : rx + rw] = True
    exclude = np.zeros_like(safe)
    for key in ("eye_left", "eye_right", "brow_left", "brow_right", "mouth"):
        x, y, w, h = _expand_box(features[key], 3, 3, image_w, image_h)
        exclude[y : y + h, x : x + w] = True
    # Local skin around the mouth best matches a patch at that vertical
    # position.  It is still eroded and feature-free, so lip and outline
    # colours cannot contaminate the sample.
    selected = eroded & local & ~exclude
    pixels = rgba[:, :, :3][selected]
    if len(pixels) < 18:
        cheek = np.zeros_like(safe)
        y0, y1 = fy + int(0.38 * fh), fy + int(0.70 * fh)
        cheek[max(0, y0) : min(image_h, y1), fx + int(0.10 * fw) : fx + int(0.90 * fw)] = True
        pixels = rgba[:, :, :3][eroded & cheek & ~exclude]
    if len(pixels) < 12:
        pixels = rgba[:, :, :3][safe & ~exclude]
    return _robust_rgb(pixels, fallback)


def _sample_ink_rgb(
    rgba: np.ndarray,
    face_mask: np.ndarray,
    features: dict[str, tuple[int, int, int, int]],
) -> list[int]:
    image_h, image_w = rgba.shape[:2]
    candidate = np.zeros(face_mask.shape, dtype=bool)
    for key in ("eye_left", "eye_right", "brow_left", "brow_right", "mouth"):
        x, y, w, h = _expand_box(features[key], 2, 2, image_w, image_h)
        candidate[y : y + h, x : x + w] = True
    gradient = cv2.morphologyEx(
        face_mask,
        cv2.MORPH_GRADIENT,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
    ) > 0
    candidate = (candidate | gradient) & (rgba[:, :, 3] >= 24)
    pixels = rgba[:, :, :3][candidate]
    if len(pixels) < 10:
        return [28, 28, 32]
    lum = pixels.astype(np.float32) @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    count = max(5, int(math.ceil(len(pixels) * 0.10)))
    darkest = pixels[np.argpartition(lum, min(count - 1, len(lum) - 1))[:count]]
    return _robust_rgb(darkest, (28, 28, 32))


def _geometry_confidence(
    features: dict[str, tuple[int, int, int, int]],
    face_box: tuple[int, int, int, int],
) -> float:
    left = _box_center(features["eye_left"])
    right = _box_center(features["eye_right"])
    mouth = _box_center(features["mouth"])
    _fx, _fy, fw, fh = face_box
    distance = max(1.0, right[0] - left[0])
    separation_score = float(np.clip((distance / max(1.0, fw) - 0.12) / 0.32, 0.0, 1.0))
    level_score = float(np.clip(1.0 - abs(left[1] - right[1]) / max(2.0, 0.18 * fh), 0.0, 1.0))
    mouth_score = float(np.clip((mouth[1] - max(left[1], right[1])) / max(2.0, 0.30 * fh), 0.0, 1.0))
    return 0.38 * separation_score + 0.31 * level_score + 0.31 * mouth_score


def detect_face_anchors(
    rgba: np.ndarray,
    head_box: tuple[int, int, int, int] | None = None,
    nose_xy: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Detect anime face controls and representative skin/ink colours.

    Args:
        rgba: Source image in RGB or RGBA channel order.
        head_box: Optional ``(x, y, w, h)`` head search box.  Whole-body
            transparent character sheets are also supported without this hint.
        nose_xy: Optional image-coordinate nose hint, useful when several
            people are present.

    Returns:
        A JSON-serialisable dictionary.  ``eye_left``/``brow_left`` always
        mean the feature on the *left side of the image*.
    """
    image = _as_rgba(rgba)
    image_h, image_w = image.shape[:2]
    if image_h < 24 or image_w < 24:
        raise ValueError("image is too small for face detection")
    supplied_head = None if head_box is None else _clip_box(head_box, image_w, image_h)
    coarse_rtm = _rtm_face_landmarks(image, supplied_head, nose_xy)
    if supplied_head is not None:
        resolved_head = supplied_head
    elif coarse_rtm is not None:
        resolved_head = _head_from_landmarks(coarse_rtm["points"], image_w, image_h)
    else:
        resolved_head = _auto_head_box(image, nose_xy)

    rtm = coarse_rtm
    if _needs_head_crop(image, resolved_head):
        crop_x, crop_y, crop_w, crop_h = _head_context_crop(
            resolved_head, image_w, image_h, coarse_rtm
        )
        local_nose = (
            None
            if nose_xy is None
            else (nose_xy[0] - crop_x, nose_xy[1] - crop_y)
        )
        cropped = image[crop_y : crop_y + crop_h, crop_x : crop_x + crop_w]
        refined = _rtm_face_landmarks(cropped, None, local_nose)
        if refined is not None:
            mapped = _offset_rtm_result(refined, crop_x, crop_y)
            if _rtm_result_matches_head(mapped, resolved_head):
                rtm = mapped

    raw_skin = _skin_threshold(image, resolved_head)
    if nose_xy is not None:
        target = nose_xy
    elif rtm is not None:
        nose_points = rtm["points"][27:36]
        target = tuple(float(v) for v in np.mean(nose_points, axis=0))
    else:
        target = None
    face_mask, component_box = _component_from_mask(raw_skin, resolved_head, target)
    if rtm is not None:
        face_box = _face_box_from_landmarks(
            rtm["points"], face_mask, component_box, image_w, image_h
        )
    else:
        face_box = _fallback_face_box(component_box, resolved_head, image_w, image_h)
    if not np.any(face_mask):
        # Keep colour sampling and blob constraints meaningful even when an
        # unusual skin palette misses the broad threshold.
        face_mask = np.zeros((image_h, image_w), dtype=np.uint8)
        fx, fy, fw, fh = face_box
        cv2.ellipse(
            face_mask,
            (fx + fw // 2, fy + fh // 2),
            (max(1, int(fw * 0.47)), max(1, int(fh * 0.49))),
            0,
            0,
            360,
            255,
            -1,
        )
        face_mask[image[:, :, 3] < 24] = 0

    provisional_skin = _provisional_skin_rgb(image, face_mask, face_box)
    if rtm is not None:
        feature_boxes = _rtm_feature_boxes(
            image,
            rtm["points"],
            provisional_skin,
            face_mask,
            face_box,
        )
        method = "rtmlib_refined"
    else:
        feature_boxes = _fallback_feature_boxes(image, face_mask, face_box, provisional_skin)
        method = "skin_blob"
    skin_rgb = _sample_skin_rgb(
        image, face_mask, face_box, feature_boxes, provisional_skin
    )
    ink_rgb = _sample_ink_rgb(image, face_mask, feature_boxes)
    geometry = _geometry_confidence(feature_boxes, face_box)
    mask_support = float(np.count_nonzero(face_mask)) / max(1.0, face_box[2] * face_box[3])
    if rtm is not None:
        confidence = 0.62 * float(rtm["confidence"]) + 0.27 * geometry + 0.11 * min(1.0, mask_support * 2.0)
    else:
        confidence = 0.28 + 0.48 * geometry + 0.24 * min(1.0, mask_support * 1.8)
    confidence = round(float(np.clip(confidence, 0.0, 0.99)), 3)

    result: dict[str, Any] = {
        "head_box": list(resolved_head),
        "face_box": list(face_box),
    }
    for key in ("eye_left", "eye_right", "brow_left", "brow_right", "mouth"):
        result[key] = _box_dict(feature_boxes[key], image_w, image_h)
    result.update(
        {
            "skin_rgb": [int(v) for v in skin_rgb],
            "ink_rgb": [int(v) for v in ink_rgb],
            "method": method,
            "confidence": confidence,
        }
    )
    return result


def draw_face_overlay(rgba: np.ndarray, anchors: dict[str, Any]) -> np.ndarray:
    """Draw feature boxes and a side-by-side skin-patched mouth comparison."""
    source = _as_rgba(rgba)
    image_h, image_w = source.shape[:2]
    annotated = source.copy()
    patched = source.copy()
    colours = {
        "eye_left": (38, 224, 92, 255),
        "eye_right": (38, 224, 92, 255),
        "brow_left": (42, 132, 255, 255),
        "brow_right": (42, 132, 255, 255),
        "mouth": (255, 55, 55, 255),
    }
    _hx, _hy, head_w, head_h = _box_tuple(anchors["head_box"])
    feature_scale = min(head_w, head_h)
    thickness = max(1, int(round(feature_scale / 180)))
    font_scale = max(0.27, min(0.52, feature_scale / 250.0))
    labels = {
        "eye_left": "EL",
        "eye_right": "ER",
        "brow_left": "BL",
        "brow_right": "BR",
        "mouth": "mouth",
    }
    for key, colour in colours.items():
        x, y, w, h = _clip_box(_box_tuple(anchors[key]), image_w, image_h)
        cv2.rectangle(annotated, (x, y), (x + w - 1, y + h - 1), colour, thickness, cv2.LINE_AA)
        label_y = max(10, y - 3)
        cv2.putText(
            annotated,
            labels[key],
            (x, label_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            colour,
            thickness,
            cv2.LINE_AA,
        )
    for key, colour in (("head_box", (255, 211, 45, 255)), ("face_box", (205, 80, 255, 255))):
        x, y, w, h = _clip_box(anchors[key], image_w, image_h)
        cv2.rectangle(annotated, (x, y), (x + w - 1, y + h - 1), colour, thickness, cv2.LINE_AA)

    swatch = max(14, min(34, image_w // 18))
    right = image_w - 8
    for index, key in enumerate(("skin_rgb", "ink_rgb")):
        rgb = tuple(int(v) for v in anchors[key]) + (255,)
        y0 = 8 + index * (swatch + 5)
        cv2.rectangle(annotated, (right - swatch, y0), (right, y0 + swatch), rgb, -1)
        cv2.rectangle(annotated, (right - swatch, y0), (right, y0 + swatch), (255, 255, 255, 255), 1)
        cv2.putText(
            annotated,
            key,
            (max(2, right - swatch - int(88 * font_scale / 0.5)), y0 + swatch - 3),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (255, 255, 255, 255),
            thickness,
            cv2.LINE_AA,
        )

    mx, my, mw, mh = _clip_box(_box_tuple(anchors["mouth"]), image_w, image_h)
    skin = tuple(int(v) for v in anchors["skin_rgb"]) + (255,)
    center = (mx + mw // 2, my + mh // 2)
    axes = (max(2, mw // 2 + 2), max(2, mh // 2 + 2))
    cv2.ellipse(patched, center, axes, 0, 0, 360, skin, -1, cv2.LINE_AA)
    label_colour = (255, 255, 255, 255)
    cv2.putText(
        annotated,
        "detected anchors",
        (8, max(16, int(22 * font_scale / 0.5))),
        cv2.FONT_HERSHEY_SIMPLEX,
        font_scale,
        label_colour,
        thickness,
        cv2.LINE_AA,
    )
    cv2.putText(
        patched,
        "skin patch over original mouth",
        (8, max(16, int(22 * font_scale / 0.5))),
        cv2.FONT_HERSHEY_SIMPLEX,
        font_scale,
        label_colour,
        thickness,
        cv2.LINE_AA,
    )
    divider = np.full((image_h, max(2, thickness + 1), 4), (32, 32, 32, 255), dtype=np.uint8)
    return np.concatenate((annotated, divider, patched), axis=1)


def _load_rgba_file(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGBA"), dtype=np.uint8)


def _save_rgba_file(path: Path, rgba: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(_as_rgba(rgba), mode="RGBA").save(path)


def _anchor_metrics(anchors: dict[str, Any]) -> dict[str, float]:
    left = _box_center(anchors["eye_left"])
    right = _box_center(anchors["eye_right"])
    brow_left = _box_center(anchors["brow_left"])
    brow_right = _box_center(anchors["brow_right"])
    mouth = _box_center(anchors["mouth"])
    eye_distance = math.hypot(right[0] - left[0], right[1] - left[1])
    eye_mid = ((left[0] + right[0]) * 0.5, (left[1] + right[1]) * 0.5)
    return {
        "eye_distance_px": round(eye_distance, 2),
        "eye_level_delta_px": round(abs(left[1] - right[1]), 2),
        "eye_below_brow_px": round(
            ((left[1] - brow_left[1]) + (right[1] - brow_right[1])) * 0.5,
            2,
        ),
        "mouth_below_eyes_px": round(mouth[1] - eye_mid[1], 2),
        "mouth_eye_distance_ratio": round((mouth[1] - eye_mid[1]) / max(1.0, eye_distance), 3),
        "mouth_width_px": float(anchors["mouth"]["w"]),
        "mouth_width_eye_ratio": round(anchors["mouth"]["w"] / max(1.0, eye_distance), 3),
        "mouth_center_offset_px": round(mouth[0] - eye_mid[0], 2),
    }


def _summary_mosaic(items: Iterable[tuple[str, np.ndarray]]) -> np.ndarray:
    entries = list(items)
    cell_w, cell_h, title_h, columns = 640, 410, 34, 2
    rows = max(1, math.ceil(len(entries) / columns))
    canvas = np.full((rows * (cell_h + title_h), columns * cell_w, 4), (24, 27, 31, 255), dtype=np.uint8)
    for index, (name, overlay) in enumerate(entries):
        row, column = divmod(index, columns)
        x0, y0 = column * cell_w, row * (cell_h + title_h)
        scale = min(cell_w / overlay.shape[1], cell_h / overlay.shape[0])
        width = max(1, int(round(overlay.shape[1] * scale)))
        height = max(1, int(round(overlay.shape[0] * scale)))
        resized = cv2.resize(overlay, (width, height), interpolation=cv2.INTER_AREA)
        ox, oy = x0 + (cell_w - width) // 2, y0 + title_h + (cell_h - height) // 2
        canvas[oy : oy + height, ox : ox + width] = resized
        cv2.putText(
            canvas,
            name,
            (x0 + 8, y0 + 23),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.52,
            (235, 238, 242, 255),
            1,
            cv2.LINE_AA,
        )
    return canvas


def _face_comparison_crop(
    overlay: np.ndarray,
    anchors: dict[str, Any],
    source_w: int,
) -> np.ndarray:
    """Crop both overlay panels around the head so full-body probes stay legible."""
    source_h = overlay.shape[0]
    hx, hy, hw, hh = _box_tuple(anchors["head_box"])
    pad_x, pad_y = max(8, int(round(hw * 0.16))), max(8, int(round(hh * 0.12)))
    x, y, w, h = _expand_box((hx, hy, hw, hh), pad_x, pad_y, source_w, source_h)
    right_offset = overlay.shape[1] - source_w
    left_crop = overlay[y : y + h, x : x + w]
    right_crop = overlay[y : y + h, right_offset + x : right_offset + x + w]
    divider = np.full((h, 3, 4), (32, 32, 32, 255), dtype=np.uint8)
    return np.concatenate((left_crop, divider, right_crop), axis=1)


def run_probe(paths: Sequence[Path], output_dir: Path) -> list[dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    overlays: list[tuple[str, np.ndarray]] = []
    existing_paths = [path for path in paths if path.is_file()]
    stem_counts = Counter(path.stem for path in existing_paths)
    for path in paths:
        if not path.is_file():
            continue
        rgba = _load_rgba_file(path)
        started = time.perf_counter()
        anchors = detect_face_anchors(rgba)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        overlay = draw_face_overlay(rgba, anchors)
        unique_stem = (
            path.stem
            if stem_counts[path.stem] == 1
            else f"{path.parent.name}-{path.stem}"
        )
        overlay_name = f"{unique_stem}-overlay.png"
        _save_rgba_file(output_dir / overlay_name, overlay)
        metrics = _anchor_metrics(anchors)
        records.append(
            {
                "source": path.as_posix(),
                "size": [int(rgba.shape[1]), int(rgba.shape[0])],
                "elapsed_ms": round(elapsed_ms, 1),
                "overlay": overlay_name,
                "metrics": metrics,
                "anchors": anchors,
            }
        )
        display_name = (
            path.name
            if stem_counts[path.stem] == 1
            else f"{path.parent.name}/{path.name}"
        )
        overlays.append((display_name, _face_comparison_crop(overlay, anchors, rgba.shape[1])))
        print(
            f"{path.name}: {anchors['method']} conf={anchors['confidence']:.3f} "
            f"eye={metrics['eye_distance_px']:.1f}px mouth/eye={metrics['mouth_eye_distance_ratio']:.2f} "
            f"{elapsed_ms:.1f}ms"
        )
    if not records:
        raise FileNotFoundError("none of the requested probe images exist")
    _save_rgba_file(output_dir / "summary_overlay.png", _summary_mosaic(overlays))
    payload = {
        "generated_at_unix": round(time.time(), 3),
        "image_count": len(records),
        "coordinate_convention": "left/right are image-left/image-right",
        "results": records,
    }
    (output_dir / "results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return records


def _parse_box(text: str) -> tuple[int, int, int, int]:
    values = tuple(int(part.strip()) for part in text.split(","))
    if len(values) != 4:
        raise argparse.ArgumentTypeError("head box must be x,y,w,h")
    return values  # type: ignore[return-value]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="*", type=Path, help="PNG images to probe")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("media/face_probe"),
        help="overlay/results directory (default: media/face_probe)",
    )
    parser.add_argument(
        "--head-box",
        type=_parse_box,
        help="optional x,y,w,h hint; only valid for one input image",
    )
    args = parser.parse_args(argv)
    paths = args.images or [Path(item) for item in KNOWN_ASSETS]
    if args.head_box is not None:
        if len(paths) != 1:
            parser.error("--head-box requires exactly one input image")
        path = paths[0]
        rgba = _load_rgba_file(path)
        anchors = detect_face_anchors(rgba, head_box=args.head_box)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        _save_rgba_file(args.output_dir / f"{path.stem}-overlay.png", draw_face_overlay(rgba, anchors))
        (args.output_dir / "results.json").write_text(
            json.dumps({"source": path.as_posix(), "anchors": anchors}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(anchors, ensure_ascii=False))
        return 0
    run_probe(paths, args.output_dir)
    print(f"wrote {args.output_dir / 'summary_overlay.png'}")
    print(f"wrote {args.output_dir / 'results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
