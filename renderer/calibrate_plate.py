#!/usr/bin/env python3
"""Measure a location plate's walkable floor, horizon and room light for metric staging.

The output `plate_metrics` lets Remotion scale humans, the rover and set props
with one perspective law on the plate's own floor:

    S(y) = clamp((y - horizon_y) / (mid_y - horizon_y), 0.68, 1.22)
    figure_height(y, stature) = ref_mid_figure_height * S(y) * stature

Usage: python renderer/calibrate_plate.py plate.png [more.png ...]
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image

try:
    import cv2
except Exception:  # pragma: no cover - the workflow installs opencv; keep light sampling working without it
    cv2 = None

WORK_W, WORK_H = 1280, 720
DEFAULT_HORIZON_Y = 0.46
DEFAULT_WALL_BASE_Y = 0.75
HORIZON_BOUNDS = (0.38, 0.54)
WALL_BASE_BOUNDS = (0.70, 0.80)
SCALE_BOUNDS = (0.68, 1.22)
REF_STATURE_M = 1.78
FIGURE_MEDIUM_ROOM = 0.56
FIGURE_DEEP_CORRIDOR = 0.51
NEAR_Y = 0.95
DEFAULT_AMBIENT = (168, 188, 214)
DEFAULT_FLOOR = (196, 204, 210)


def _clamp(value: float, low: float, high: float) -> float:
    return float(min(high, max(low, value)))


def perspective_scale(y: float, horizon_y: float, mid_y: float) -> float:
    return _clamp((y - horizon_y) / max(1e-3, mid_y - horizon_y), *SCALE_BOUNDS)


def _load_rgb(path: Path) -> np.ndarray:
    # PIL reads non-ASCII Windows paths that cv2.imread cannot.
    with Image.open(path) as img:
        rgb = img.convert('RGB')
        if rgb.size != (WORK_W, WORK_H):
            rgb = rgb.resize((WORK_W, WORK_H), Image.LANCZOS)
        return np.asarray(rgb, dtype=np.uint8)


def _hough_segments(gray: np.ndarray) -> np.ndarray:
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 40, 120)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=70,
                            minLineLength=int(WORK_W * 0.05), maxLineGap=10)
    if lines is None:
        return np.zeros((0, 4), dtype=np.float32)
    return lines.reshape(-1, 4).astype(np.float32)


def _segment_angles(seg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    dx = seg[:, 2] - seg[:, 0]
    dy = seg[:, 3] - seg[:, 1]
    angle = np.degrees(np.arctan2(dy, dx))
    angle = (angle + 90.0) % 180.0 - 90.0  # fold into [-90, 90)
    return angle, np.hypot(dx, dy)


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values)
    v, w = values[order], weights[order]
    cum = np.cumsum(w)
    return float(v[np.searchsorted(cum, cum[-1] * 0.5)])


def _detect_wall_base(rgb: np.ndarray, gray: np.ndarray, seg: np.ndarray, angle: np.ndarray,
                      length: np.ndarray) -> tuple[float, float]:
    """Strongest horizontal wall/floor boundary in y = [0.60H, 0.86H]; returns (y_norm, confidence)."""
    y0, y1 = int(WORK_H * 0.60), int(WORK_H * 0.86)
    x0, x1 = int(WORK_W * 0.06), int(WORK_W * 0.94)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    horiz = np.where(np.abs(gy) > 2.0 * np.abs(gx), np.abs(gy), 0.0)[:, x0:x1]
    sobel_rows = horiz.mean(axis=1)

    hough_rows = np.zeros(WORK_H, dtype=np.float32)
    flat = np.abs(angle) < 3.0
    for (ax, ay, bx, by), ln in zip(seg[flat], length[flat]):
        row = int(round((ay + by) * 0.5))
        if 0 <= row < WORK_H:
            hough_rows[max(0, row - 2):row + 3] += ln / WORK_W

    # A wall base separates two differently coloured bands (wall above, floor below).
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    row_lab = lab[:, x0:x1].mean(axis=1)
    band = 14
    contrast = np.zeros(WORK_H, dtype=np.float32)
    for row in range(band, WORK_H - band):
        contrast[row] = float(np.linalg.norm(row_lab[row - band:row].mean(axis=0) - row_lab[row:row + band].mean(axis=0)))

    def _norm(v: np.ndarray) -> np.ndarray:
        win = v[y0:y1]
        top = float(win.max()) if win.size else 0.0
        return v / top if top > 1e-6 else v * 0

    kernel = np.ones(5, dtype=np.float32) / 5
    score = (0.35 * _norm(np.convolve(sobel_rows, kernel, mode='same'))
             + 0.35 * _norm(np.convolve(hough_rows, kernel, mode='same'))
             + 0.30 * _norm(contrast))
    window = score[y0:y1]
    if window.size == 0 or float(window.max()) <= 1e-6:
        return DEFAULT_WALL_BASE_Y, 0.0
    best = int(np.argmax(window)) + y0
    confidence = float(window.max() / max(1e-6, float(np.median(window)) * 3.0))
    return best / WORK_H, min(1.0, confidence)


def _detect_vanishing(seg: np.ndarray, angle: np.ndarray, length: np.ndarray) -> dict:
    """Intersect converging slanted floor lines (12-68 deg, lower half) to estimate the vanishing point."""
    mid_y = (seg[:, 1] + seg[:, 3]) * 0.5
    slanted = (np.abs(angle) >= 12.0) & (np.abs(angle) <= 68.0) & (mid_y > WORK_H * 0.5)
    s, a, ln = seg[slanted], angle[slanted], length[slanted]
    result = {'count': int(len(s)), 'x': None, 'y': None, 'support': 0.0}
    if len(s) < 2:
        return result
    pos, neg = np.where(a > 0)[0], np.where(a < 0)[0]
    if len(pos) == 0 or len(neg) == 0:
        return result
    pts, wts = [], []
    for i in pos[:80]:
        for j in neg[:80]:
            p1, p2 = s[i, :2], s[i, 2:]
            p3, p4 = s[j, :2], s[j, 2:]
            d = (p1[0] - p2[0]) * (p3[1] - p4[1]) - (p1[1] - p2[1]) * (p3[0] - p4[0])
            if abs(d) < 1e-6:
                continue
            t1 = p1[0] * p2[1] - p1[1] * p2[0]
            t2 = p3[0] * p4[1] - p3[1] * p4[0]
            ix = (t1 * (p3[0] - p4[0]) - (p1[0] - p2[0]) * t2) / d
            iy = (t1 * (p3[1] - p4[1]) - (p1[1] - p2[1]) * t2) / d
            # Floor lines converge upward: the meeting point must sit above both segments.
            if not (-0.25 * WORK_W <= ix <= 1.25 * WORK_W and 0.15 * WORK_H <= iy <= 0.80 * WORK_H):
                continue
            if iy > min(s[i, 1], s[i, 3], s[j, 1], s[j, 3]) + 4:
                continue
            pts.append((ix, iy))
            wts.append(math.sqrt(ln[i] * ln[j]))
    if len(pts) < 2:
        return result
    p = np.asarray(pts, dtype=np.float32)
    w = np.asarray(wts, dtype=np.float32)
    vx, vy = _weighted_median(p[:, 0], w), _weighted_median(p[:, 1], w)
    near = (np.abs(p[:, 0] - vx) < WORK_W * 0.08) & (np.abs(p[:, 1] - vy) < WORK_H * 0.06)
    result.update({'x': vx / WORK_W, 'y': vy / WORK_H, 'support': float(w[near].sum() / max(1e-6, w.sum()))})
    return result


def _back_wall_span(seg: np.ndarray, angle: np.ndarray, vp: dict) -> float | None:
    """Width (fraction of W) of the first horizontal wall/floor run below the vanishing point."""
    if vp.get('x') is None:
        return None
    vx, vy = vp['x'] * WORK_W, vp['y'] * WORK_H
    flat = np.abs(angle) < 3.0
    rows = (seg[:, 1] + seg[:, 3]) * 0.5
    cand = flat & (rows > vy + WORK_H * 0.03) & (rows < WORK_H * 0.72)
    if not np.any(cand):
        return None
    top = float(rows[cand].min())
    run = cand & (np.abs(rows - top) < 7)
    xs = np.concatenate([seg[run, 0], seg[run, 2]])
    near = xs[np.abs(xs - vx) < WORK_W * 0.45]
    if near.size == 0:
        return None
    return float((near.max() - near.min()) / WORK_W)


def _median_chroma_rgb(pixels: np.ndarray, fallback: tuple[int, int, int]) -> list[int]:
    if pixels.size == 0:
        return list(fallback)
    px = pixels.reshape(-1, 3).astype(np.float32)
    lum = px.mean(axis=1)
    lo, hi = np.percentile(lum, 8), np.percentile(lum, 97)
    keep = px[(lum >= lo) & (lum <= hi)]
    if len(keep) < 32:
        keep = px
    total = keep.sum(axis=1, keepdims=True) + 1e-6
    chroma = np.median(keep / total, axis=0)
    chroma = chroma / max(1e-6, float(chroma.sum()))
    level = float(np.median(keep.sum(axis=1)))
    return [int(round(_clamp(c * level, 0, 255))) for c in chroma]


def _light_profile(ambient: list[int]) -> tuple[str, list[int], float]:
    r, g, b = (float(c) for c in ambient)
    mean = max(1.0, (r + g + b) / 3)
    warmth = (r - b) / mean
    light_temp = 'warm' if warmth > 0.08 else 'cool' if warmth < -0.08 else 'neutral'
    peak = max(1.0, r, g, b)
    tint = [int(round(255 * c / peak)) for c in (r, g, b)]
    saturation = (max(r, g, b) - min(r, g, b)) / peak
    # Puppets absorb 10-14% of the room light; more saturated rooms tint a little harder.
    strength = round(_clamp(0.10 + 0.08 * saturation, 0.10, 0.14), 3)
    return light_temp, tint, strength


def calibrate_rgb(rgb: np.ndarray) -> dict:
    horizon_raw = wall_raw = None
    wall_conf = 0.0
    vp: dict = {'count': 0, 'x': None, 'y': None, 'support': 0.0}
    span = None
    if cv2 is not None:
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        seg = _hough_segments(gray)
        angle, length = _segment_angles(seg) if len(seg) else (np.zeros(0), np.zeros(0))
        wall_raw, wall_conf = _detect_wall_base(rgb, gray, seg, angle, length)
        vp = _detect_vanishing(seg, angle, length)
        horizon_raw = vp['y']
        span = _back_wall_span(seg, angle, vp)

    horizon_y = _clamp(horizon_raw if horizon_raw is not None else DEFAULT_HORIZON_Y, *HORIZON_BOUNDS)
    wall_base_y = _clamp(wall_raw if wall_raw is not None else DEFAULT_WALL_BASE_Y, *WALL_BASE_BOUNDS)

    deep_corridor = bool(
        vp.get('x') is not None
        and 0.35 <= vp['x'] <= 0.65
        and 0.42 <= vp['y'] <= 0.56
        and vp['support'] >= 0.30
        and vp['count'] >= 8
        and span is not None
        and span < 0.5
    )

    far_y = _clamp(wall_base_y + 0.05, 0.77, 0.83)
    mid_y = _clamp(wall_base_y + 0.13, 0.86, 0.91)
    if deep_corridor:
        mid_y = max(mid_y, 0.90)
    ref_figure = FIGURE_DEEP_CORRIDOR if deep_corridor else FIGURE_MEDIUM_ROOM

    room = rgb[int(WORK_H * 0.06):int(WORK_H * max(0.2, wall_base_y - 0.04)), int(WORK_W * 0.04):int(WORK_W * 0.96)]
    floor = rgb[int(WORK_H * min(0.95, wall_base_y + 0.03)):int(WORK_H * 0.97), int(WORK_W * 0.10):int(WORK_W * 0.90)]
    ambient_rgb = _median_chroma_rgb(room, DEFAULT_AMBIENT)
    floor_rgb = _median_chroma_rgb(floor, DEFAULT_FLOOR)
    light_temp, tint_rgb, tint_strength = _light_profile(ambient_rgb)

    return {
        'version': 1,
        'horizon_y': round(horizon_y, 4),
        'wall_base_y': round(wall_base_y, 4),
        'far_y': round(far_y, 4),
        'mid_y': round(mid_y, 4),
        'near_y': NEAR_Y,
        'ref_mid_figure_height': ref_figure,
        'ref_stature_m': REF_STATURE_M,
        'scale_bounds': list(SCALE_BOUNDS),
        'deep_corridor': deep_corridor,
        'ambient_rgb': ambient_rgb,
        'floor_rgb': floor_rgb,
        'tint_rgb': tint_rgb,
        'tint_strength': tint_strength,
        'light_temp': light_temp,
        'detection': {
            'opencv': cv2 is not None,
            'horizon_raw': None if horizon_raw is None else round(float(horizon_raw), 4),
            'wall_base_raw': None if wall_raw is None else round(float(wall_raw), 4),
            'wall_base_confidence': round(float(wall_conf), 3),
            'vanishing_x': None if vp.get('x') is None else round(float(vp['x']), 4),
            'vanishing_support': round(float(vp.get('support') or 0.0), 3),
            'slanted_lines': int(vp.get('count') or 0),
            'back_wall_span': None if span is None else round(span, 4),
        },
    }


def default_metrics() -> dict:
    far_y = _clamp(DEFAULT_WALL_BASE_Y + 0.05, 0.77, 0.83)
    mid_y = _clamp(DEFAULT_WALL_BASE_Y + 0.13, 0.86, 0.91)
    light_temp, tint_rgb, tint_strength = _light_profile(list(DEFAULT_AMBIENT))
    return {
        'version': 1, 'horizon_y': DEFAULT_HORIZON_Y, 'wall_base_y': DEFAULT_WALL_BASE_Y,
        'far_y': far_y, 'mid_y': mid_y, 'near_y': NEAR_Y,
        'ref_mid_figure_height': FIGURE_MEDIUM_ROOM, 'ref_stature_m': REF_STATURE_M,
        'scale_bounds': list(SCALE_BOUNDS), 'deep_corridor': False,
        'ambient_rgb': list(DEFAULT_AMBIENT), 'floor_rgb': list(DEFAULT_FLOOR),
        'tint_rgb': tint_rgb, 'tint_strength': tint_strength, 'light_temp': light_temp,
    }


def calibrate_plate_file(path: Path) -> dict:
    """Calibrate one plate image; never raises, falling back to neutral stage defaults."""
    try:
        return calibrate_rgb(_load_rgb(Path(path)))
    except Exception as exc:
        print(f'calibrate_plate: {Path(path).name} fell back to defaults ({exc})', file=sys.stderr, flush=True)
        return default_metrics()


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    for arg in sys.argv[1:]:
        metrics = calibrate_plate_file(Path(arg))
        print(json.dumps({'plate': Path(arg).name, **metrics}, ensure_ascii=False))
