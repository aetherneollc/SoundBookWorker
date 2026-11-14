#!/usr/bin/env python3
"""Auto-rig side-view AI robot master into body, spinning wheels, mast, and arm.

Usage:
    python renderer/rig_robot.py manifest.json

Caches parts by SHA-256 (`sha256[:16]`) under `media/rigs/robot-<hash>/`,
detects wheels via `cv2.HoughCircles` plus circular contour / distance-peak
fallback, outputs `'wheeled'` (`body`, `wheels`, optional `mast`, optional `arm`)
or `'sprite'` fallback, and attaches `robot_rig_json` onto manifest and shots.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

# Fraction of the rover bbox height (from the top) below which orange is treated as wheel hub, not chassis paint.
WHEEL_ZONE_Y = 0.64

def _ensure_rgba_matte(rgba: np.ndarray) -> np.ndarray:
    out = rgba.copy()
    alpha = out[:, :, 3]
    if float((alpha < 200).mean()) <= 0.10:
        r = out[:, :, 0].astype(np.float32)
        g = out[:, :, 1].astype(np.float32)
        b = out[:, :, 2].astype(np.float32)
        excess_g = g - np.maximum(r, b)
        border = np.concatenate([
            out[0, :, :3], out[-1, :, :3], out[:, 0, :3], out[:, -1, :3]
        ], axis=0).astype(np.float32)
        border_green = float(np.median(border[:, 1] - np.maximum(border[:, 0], border[:, 2])))
        if border_green > 22.0:
            fg = 1.0 - np.clip((excess_g - 18.0) / max(24.0, border_green * 0.75), 0.0, 1.0)
            low_val = (g < 85.0).astype(np.float32)
            alpha = np.clip(np.maximum(fg, low_val) * 255.0, 0, 255).astype(np.uint8)
            spill = np.maximum(0.0, g - np.maximum(r, b) * 1.04)
            edge = (alpha > 0) & (alpha < 250)
            out[:, :, 1] = np.where(edge, np.clip(g - spill * 0.75, 0, 255), g).astype(np.uint8)
            out[:, :, 3] = alpha

    binary = (out[:, :, 3] >= 48).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    if count <= 1:
        return out
    main_idx = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    main_area = int(stats[main_idx, cv2.CC_STAT_AREA])
    keep = labels == main_idx
    for idx in range(1, count):
        if idx != main_idx and int(stats[idx, cv2.CC_STAT_AREA]) >= max(32, int(main_area * 0.01)):
            keep |= (labels == idx)
    out[:, :, 3] = np.where(keep, out[:, :, 3], 0)
    return out


def _segment_wheel_ellipse(
    rgba: np.ndarray,
    fg_nonpaint: np.ndarray,
    tire_clean: np.ndarray,
    bbox: tuple[int, int, int, int],
    wheel_seed: tuple[float, float, float],
    x_lo: int,
    x_hi: int,
) -> tuple[np.ndarray, float, float, float, float, float]:
    """Segment the connected tire+rim contour around a wheel seed and fit (cx, cy, rx, ry, tilt_deg)."""
    h, w = rgba.shape[:2]
    bx, by, bw, bh = bbox
    cx_seed, cy_seed, r_seed = wheel_seed
    x0 = max(0, x_lo)
    x1 = min(w, x_hi)
    cx_s = int(round(cx_seed)) - x0
    cy_s = int(round(cy_seed))

    def _seg_row(row_mask: np.ndarray, target_x: float, max_gap: int = 6) -> tuple[int, int] | None:
        rxs = np.where(row_mask > 0)[0]
        if len(rxs) == 0:
            return None
        diffs = np.diff(rxs)
        splits = np.where(diffs > max_gap)[0] + 1
        segs = np.split(rxs, splits)
        best = min(
            segs,
            key=lambda s: 0 if (s[0] <= target_x <= s[-1]) else min(abs(s[0] - target_x), abs(s[-1] - target_x)),
        )
        if not (best[0] - max(12, r_seed * 0.25) <= target_x <= best[-1] + max(12, r_seed * 0.25)):
            return None
        return int(best[0]), int(best[-1])

    # Trace outer tire silhouette from widest clean mid-band downward (clipping floor shadow flare) and upward (stopping at fender jump)
    mid_seg = _seg_row(tire_clean[cy_s, x0:x1], cx_s) or _seg_row(fg_nonpaint[cy_s, x0:x1], cx_s)
    mid_w = (mid_seg[1] - mid_seg[0] + 1) if mid_seg else max(16, int(round(r_seed * 2.0)))
    mid_cx = ((mid_seg[0] + mid_seg[1]) * 0.5) if mid_seg else float(cx_s)
    jump_lim = max(12, int(round(r_seed * 0.22)))

    down_rows: list[tuple[int, int, int, int]] = []
    min_w_seen = mid_w
    prev_l, prev_r = (mid_seg[0], mid_seg[1]) if mid_seg else (cx_s - int(r_seed), cx_s + int(r_seed))
    for y in range(cy_s, min(h, by + bh + 2)):
        seg = _seg_row(fg_nonpaint[y, x0:x1], mid_cx)
        if seg is None:
            break
        sl, sr = seg
        if y > cy_s:
            if sl < prev_l - jump_lim:
                sl = prev_l
            if sr > prev_r + jump_lim:
                sr = prev_r
        ww = sr - sl + 1
        # Stop if bottom row flares horizontally into floor contact shadow
        if y > cy_s + r_seed * 0.42 and ww > max(mid_w * 1.08, min_w_seen + 12):
            break
        down_rows.append((y, x0 + sl, x0 + sr, ww))
        if y > cy_s + r_seed * 0.32:
            min_w_seen = min(min_w_seen, ww)
        prev_l, prev_r = sl, sr
        mid_cx = (sl + sr) * 0.5

    mid_cx = ((mid_seg[0] + mid_seg[1]) * 0.5) if mid_seg else float(cx_s)
    prev_l, prev_r = (mid_seg[0], mid_seg[1]) if mid_seg else (cx_s - int(r_seed), cx_s + int(r_seed))
    up_rows: list[tuple[int, int, int, int]] = []
    for y in range(cy_s - 1, max(0, int(cy_s - r_seed * 1.85)), -1):
        seg = _seg_row(fg_nonpaint[y, x0:x1], mid_cx)
        if seg is None:
            break
        sl, sr = seg
        if sl < prev_l - jump_lim or sr > prev_r + jump_lim:
            break
        ww = sr - sl + 1
        if ww < 6:
            break
        up_rows.append((y, x0 + sl, x0 + sr, ww))
        prev_l, prev_r = sl, sr
        mid_cx = (sl + sr) * 0.5

    all_rows = up_rows[::-1] + down_rows
    if len(all_rows) < 10:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        fallback_mask = (((xx - cx_seed) ** 2 + (yy - cy_seed) ** 2) <= (r_seed * 1.04) ** 2).astype(np.uint8)
        return fallback_mask, cx_seed, cy_seed, r_seed, r_seed, 0.0

    bot_y = down_rows[-1][0] if down_rows else int(round(cy_seed + r_seed))
    mid_rows = [rw for rw in all_rows if rw[0] < bot_y - r_seed * 0.25] or all_rows
    max_w = max(rw[3] for rw in mid_rows)
    wide = [rw for rw in mid_rows if rw[3] >= max_w * 0.94]
    ex_rough = float(np.median([(rw[1] + rw[2]) * 0.5 for rw in wide]))
    ey_rough = float(np.median([rw[0] for rw in wide]))
    ry_rough = max(r_seed * 0.85, bot_y - ey_rough)

    # Build connected tire+rim mask (plus reflected upper contour if fender clipped the top edge)
    tire_mask = np.zeros((h, w), dtype=np.uint8)
    sym_mask = np.zeros((h, w), dtype=np.uint8)
    contour_pts: list[list[float]] = []
    for y, xl, xr, _ in all_rows:
        if abs(y - ey_rough) <= ry_rough * 1.08:
            tire_mask[y, xl:xr + 1] = 1
            sym_mask[y, xl:xr + 1] = 1
            contour_pts.append([float(xl), float(y)])
            contour_pts.append([float(xr), float(y)])
            ym = int(round(2.0 * ey_rough - y))
            if y > ey_rough and 0 <= ym < h and not any(ry_u == ym for ry_u, *_ in up_rows):
                sym_mask[ym, xl:xr + 1] = 1
                contour_pts.append([float(xl), float(ym)])
                contour_pts.append([float(xr), float(ym)])

    # Fit ellipse via cv2.fitEllipse on contour points, cross-checked against covariance axes
    my, mx = np.where(sym_mask > 0)
    if len(mx) >= 24:
        coords = np.column_stack([mx.astype(np.float64), my.astype(np.float64)])
        mean = coords.mean(axis=0)
        cov = np.cov((coords - mean).T)
        eigvals, eigvecs = np.linalg.eigh(cov)
        order = np.argsort(eigvals)[::-1]
        eigvals = eigvals[order]
        eigvecs = eigvecs[:, order]
        cov_r1 = 2.0 * math.sqrt(max(1.0, float(eigvals[0])))
        cov_r2 = 2.0 * math.sqrt(max(1.0, float(eigvals[1])))
        vx, vy = float(eigvecs[0, 0]), float(eigvecs[1, 0])
        if abs(vx) >= abs(vy):
            cov_rx, cov_ry = cov_r1, cov_r2
            cov_tilt = math.degrees(math.atan2(vy, vx))
        else:
            cov_rx, cov_ry = cov_r2, cov_r1
            ux, uy = float(eigvecs[0, 1]), float(eigvecs[1, 1])
            cov_tilt = math.degrees(math.atan2(uy, ux))
        while cov_tilt > 90.0:
            cov_tilt -= 180.0
        while cov_tilt <= -90.0:
            cov_tilt += 180.0
        ex, ey, rx, ry, tilt_deg = float(mean[0]), float(mean[1]), cov_rx, cov_ry, cov_tilt

        # Refine center & rim alignment using bright rim/hub pixels inside the wheel when present
        rgb_f = rgba[:, :, :3].astype(np.float32)
        lum_f = 0.299 * rgb_f[:, :, 0] + 0.587 * rgb_f[:, :, 1] + 0.114 * rgb_f[:, :, 2]
        sat_f = np.maximum(np.maximum(rgb_f[:, :, 0], rgb_f[:, :, 1]), rgb_f[:, :, 2]) - np.minimum(
            np.minimum(rgb_f[:, :, 0], rgb_f[:, :, 1]), rgb_f[:, :, 2]
        )
        hub_candidates = (sym_mask > 0) & (lum_f >= 68.0) & (sat_f < 64.0)
        if int(hub_candidates.sum()) >= 40:
            hys, hxs = np.where(hub_candidates)
            # Filter to central 70% around rough center so outer tread highlights don't pull hub center
            in_core = ((hxs - ex) / max(1.0, rx * 0.72)) ** 2 + ((hys - ey) / max(1.0, ry * 0.72)) ** 2 <= 1.0
            if int(in_core.sum()) >= 30:
                h_cx = float(np.median(hxs[in_core]))
                h_cy = float(np.median(hys[in_core]))
                if math.hypot(h_cx - ex, h_cy - ey) <= max(rx, ry) * 0.25:
                    ex = 0.55 * h_cx + 0.45 * ex
                    ey = 0.55 * h_cy + 0.45 * ey

        if len(contour_pts) >= 12:
            try:
                (fx, fy), (ma, mi), ang = cv2.fitEllipse(np.array(contour_pts, dtype=np.float32))
                # Only blend cv2.fitEllipse center/axes if fit is well-behaved (within 25% of covariance)
                if math.hypot(fx - ex, fy - ey) <= max(rx, ry) * 0.25 and min(ma, mi) > 10.0:
                    # OpenCV fitEllipse returns (minor_axis, major_axis, angle_of_major_from_vertical)
                    rad_a, rad_b = ma * 0.5, mi * 0.5
                    ang_rad = math.radians(ang)
                    # Major axis unit vector is (sin(ang), -cos(ang))
                    maj_x = abs(math.sin(ang_rad))
                    maj_y = abs(math.cos(ang_rad))
                    fit_rx = rad_b if maj_x >= maj_y else rad_a
                    fit_ry = rad_a if maj_x >= maj_y else rad_b
                    if 0.72 <= fit_rx / max(1.0, cov_rx) <= 1.32 and 0.72 <= fit_ry / max(1.0, cov_ry) <= 1.32:
                        ex = 0.5 * (ex + float(fx))
                        ey = 0.5 * (ey + float(fy))
                        rx = 0.5 * (rx + fit_rx)
                        ry = 0.5 * (ry + fit_ry)
            except Exception:
                pass
    else:
        ex, ey, rx, ry, tilt_deg = cx_seed, cy_seed, r_seed, r_seed, 0.0

    # Clamp extreme aspect outliers; keep tire_mask covering full segmented wheel + inner fitted ellipse
    rx = max(10.0, min(float(w) * 0.30, rx))
    ry = max(10.0, min(float(h) * 0.30, ry))
    if abs(tilt_deg) > 28.0:
        tilt_deg = 0.0
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    rad_t = math.radians(tilt_deg)
    ct, st = math.cos(rad_t), math.sin(rad_t)
    dx = xx - ex
    dy = yy - ey
    ux = dx * ct + dy * st
    uy = -dx * st + dy * ct
    fitted_ellipse = (((ux / max(1.0, rx)) ** 2 + (uy / max(1.0, ry)) ** 2) <= 1.03)
    tire_mask = ((tire_mask > 0) | (fitted_ellipse & (fg_nonpaint > 0))).astype(np.uint8)
    return tire_mask, ex, ey, rx, ry, tilt_deg


def _render_concentric_wheel_rgba(
    src_rgba: np.ndarray,
    tire_mask: np.ndarray,
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    tilt_deg: float,
    r_disk: float,
    is_orthographic: bool,
) -> tuple[np.ndarray, np.ndarray | None]:
    """Build crisp concentric wheel_i.png (and static outer tire_i.png when angled < 0.86)."""
    h, w = src_rgba.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dx = xx - cx
    dy = yy - cy
    dist = np.sqrt(dx * dx + dy * dy)
    theta = np.arctan2(dy, dx)

    # Sample authentic tire rubber & alloy rim palette from the source wheel pixels
    rgb_f = src_rgba[:, :, :3].astype(np.float32)
    lum = 0.299 * rgb_f[:, :, 0] + 0.587 * rgb_f[:, :, 1] + 0.114 * rgb_f[:, :, 2]
    valid_px = (src_rgba[:, :, 3] >= 128) & ((tire_mask > 0) | (dist <= r_disk))
    dark_px = valid_px & (lum < 75.0)
    mid_px = valid_px & (lum >= 55.0) & (lum <= 165.0)
    bright_px = valid_px & (lum > 115.0)

    tire_rgb = (
        np.median(rgb_f[dark_px], axis=0)
        if int(dark_px.sum()) > 16
        else np.array([28.0, 35.0, 42.0], dtype=np.float32)
    )
    tire_rgb = np.clip(tire_rgb, 18.0, 56.0)
    rim_rgb = (
        np.median(rgb_f[bright_px], axis=0)
        if int(bright_px.sum()) > 16
        else (np.median(rgb_f[mid_px], axis=0) if int(mid_px.sum()) > 16 else np.array([124.0, 146.0, 158.0], dtype=np.float32))
    )
    rim_rgb = np.clip(rim_rgb * 1.08 + 14.0, 82.0, 228.0)
    rim_shadow = np.clip(rim_rgb * 0.58, 36.0, 145.0)
    outline_rgb = np.array([14.0, 19.0, 26.0], dtype=np.float32)
    hub_cap_rgb = np.array([228.0, 152.0, 64.0], dtype=np.float32)

    def _paint_hub_pattern(canvas: np.ndarray, hub_r: float) -> np.ndarray:
        """Composite crisp concentric 2D anime alloy rim + 5-spoke/lug hub inside hub_r."""
        # Base dark recessed brake drum / inner wheel well inside rim
        in_rim = dist <= hub_r
        in_bevel = (dist >= hub_r * 0.80) & (dist <= hub_r * 0.96)
        in_outer_ring = (dist >= hub_r * 0.93) & (dist <= hub_r * 1.02)
        in_inner_ring = (dist >= hub_r * 0.76) & (dist <= hub_r * 0.83)

        # 5 symmetric spokes radiating from 0.24*hub_r to 0.84*hub_r
        spoke_sector = np.abs(((theta + math.pi) % (2.0 * math.pi / 5.0)) - (math.pi / 5.0))
        spoke_half_w = np.clip((hub_r * 0.13) / np.maximum(dist, hub_r * 0.18), 0.14, 0.34)
        in_spoke = (dist >= hub_r * 0.20) & (dist <= hub_r * 0.85) & (spoke_sector <= spoke_half_w)
        spoke_edge = (
            (dist >= hub_r * 0.19)
            & (dist <= hub_r * 0.86)
            & (spoke_sector > spoke_half_w)
            & (spoke_sector <= spoke_half_w + 0.06)
        )

        # 5 lug bolts between spokes or on hub ring at 0.38 * hub_r
        lug_mask = np.zeros((h, w), dtype=bool)
        lug_r = max(1.8, hub_r * 0.075)
        for k in range(5):
            ang = k * (2.0 * math.pi / 5.0)
            lx = cx + math.cos(ang) * hub_r * 0.37
            ly = cy + math.sin(ang) * hub_r * 0.37
            lug_mask |= ((xx - lx) ** 2 + (yy - ly) ** 2) <= (lug_r ** 2)

        in_cap = dist <= hub_r * 0.23
        cap_ring = (dist >= hub_r * 0.19) & (dist <= hub_r * 0.25)

        out = canvas.copy().astype(np.float32)
        # Fill dark cavity inside rim
        for c in range(3):
            out[:, :, c] = np.where(in_rim, np.clip(tire_rgb[c] * 0.72, 12.0, 38.0), out[:, :, c])
            out[:, :, c] = np.where(in_spoke, rim_rgb[c], out[:, :, c])
            out[:, :, c] = np.where(spoke_edge, outline_rgb[c], out[:, :, c])
            out[:, :, c] = np.where(in_bevel, rim_rgb[c] * 0.94, out[:, :, c])
            out[:, :, c] = np.where(in_inner_ring, rim_shadow[c], out[:, :, c])
            out[:, :, c] = np.where(in_outer_ring, outline_rgb[c], out[:, :, c])
            out[:, :, c] = np.where(lug_mask, np.clip(rim_rgb[c] * 1.18, 120.0, 245.0), out[:, :, c])
            out[:, :, c] = np.where(in_cap, hub_cap_rgb[c], out[:, :, c])
            out[:, :, c] = np.where(cap_ring, outline_rgb[c], out[:, :, c])
        return np.clip(out, 0, 255).astype(np.uint8)

    hub_r = max(6.0, r_disk * 0.62)

    if is_orthographic:
        # Clean circular tire + subtle concentric tread notches + alloy rim + 5-spoke hub
        wheel_canvas = np.zeros_like(src_rgba)
        outer_r = r_disk * 0.98
        edge_alpha = np.clip((outer_r - dist) / 1.8, 0.0, 1.0)
        # 16 evenly spaced concentric tread blocks on outer ring [0.84*outer_r, outer_r]
        tread_ang = ((theta + math.pi) % (2.0 * math.pi / 16.0)) / (2.0 * math.pi / 16.0)
        in_groove = (dist >= outer_r * 0.84) & (dist <= outer_r * 0.99) & ((tread_ang < 0.24) | (tread_ang > 0.88))
        in_sidewall = (dist >= hub_r * 0.98) & (dist < outer_r * 0.84)
        in_outline = (dist >= outer_r * 0.955) & (dist <= outer_r)

        sidewall_rgb = np.clip(tire_rgb * 1.24 + 6.0, 24.0, 72.0)
        groove_rgb = np.clip(tire_rgb * 0.56, 8.0, 24.0)
        for c in range(3):
            col = np.where(in_groove, groove_rgb[c], tire_rgb[c])
            col = np.where(in_sidewall, sidewall_rgb[c], col)
            col = np.where(in_outline, outline_rgb[c], col)
            wheel_canvas[:, :, c] = np.clip(col, 0, 255).astype(np.uint8)
        wheel_canvas[:, :, 3] = np.clip(edge_alpha * 255.0, 0, 255).astype(np.uint8)
        wheel_canvas = _paint_hub_pattern(wheel_canvas, hub_r)
        return wheel_canvas, None

    # Angled / 3/4 perspective tire (aspect < 0.86):
    # 1) Static outer elliptical tire_i.png (outer tire silhouette preserved cleanly, dark recessed inner rim pocket)
    tire_canvas = np.zeros_like(src_rgba)
    tire_canvas[:, :, :3] = src_rgba[:, :, :3]
    # Keep strictly inside tire_mask & ~painted_body so zero orange bumper/chassis pixels leak into tire_i.png!
    r_f = src_rgba[:, :, 0].astype(np.float32)
    g_f = src_rgba[:, :, 1].astype(np.float32)
    b_f = src_rgba[:, :, 2].astype(np.float32)
    lum_f = 0.299 * r_f + 0.587 * g_f + 0.114 * b_f
    non_tire_paint = ((r_f > g_f + 22.0) & (r_f > 110.0)) | ((b_f > r_f + 45.0) & (g_f > 145.0) & (lum_f > 145.0))
    rad_t = math.radians(tilt_deg)
    ct, st = math.cos(rad_t), math.sin(rad_t)
    ux = dx * ct + dy * st
    uy = -dx * st + dy * ct
    ell_norm = np.sqrt((ux / max(1.0, rx)) ** 2 + (uy / max(1.0, ry)) ** 2)
    # Use the fitted ellipse boundary (ell_norm <= 1.04) intersected with segmented tire_mask (or smooth ellipse fill)
    # to eliminate any stray undercarriage/bumper pixels outside the tire oval
    tire_fg = ((tire_mask > 0) & (ell_norm <= 1.06) & (~non_tire_paint)) | ((ell_norm <= 0.96) & (src_rgba[:, :, 3] >= 64) & (~non_tire_paint))
    tire_alpha = np.where(tire_fg, np.maximum(src_rgba[:, :, 3], np.where(ell_norm <= 0.95, 255, 0)), 0)
    tire_canvas[:, :, 3] = tire_alpha.astype(np.uint8)
    in_inner_pocket = (ell_norm <= 0.66) & tire_fg
    for c in range(3):
        tire_canvas[:, :, c] = np.where(in_inner_pocket, np.clip(tire_rgb[c] * 0.68, 10.0, 32.0), tire_canvas[:, :, c])

    # 2) Circular concentric inner hub/spoke disk wheel_i.png (radius = r_disk, hub_r = 0.62 * r_disk)
    # Drawn as a crisp circular rim+spoke disk; robot.tsx applies rotate(tilt) scaleX(projAspect) rotate(localWheelDeg)
    hub_canvas = np.zeros_like(src_rgba)
    hub_edge_alpha = np.clip((hub_r * 1.02 - dist) / 1.6, 0.0, 1.0)
    hub_canvas[:, :, 3] = np.clip(hub_edge_alpha * 255.0, 0, 255).astype(np.uint8)
    hub_canvas = _paint_hub_pattern(hub_canvas, hub_r)
    return hub_canvas, tire_canvas


def _detect_wheels(rgba: np.ndarray, bbox: tuple[int, int, int, int]) -> list[tuple[float, float, float]]:
    """Detect wheels via inscribed dark-tire distance peaks + HoughCircles without grabbing chassis fenders."""
    h, w = rgba.shape[:2]
    bx, by, bw, bh = bbox
    alpha = rgba[:, :, 3]
    fg = (alpha >= 64).astype(np.uint8)
    r_ch = rgba[:, :, 0].astype(np.float32)
    g_ch = rgba[:, :, 1].astype(np.float32)
    b_ch = rgba[:, :, 2].astype(np.float32)
    lum = 0.299 * r_ch + 0.587 * g_ch + 0.114 * b_ch

    # Exclude bright painted chassis panels (orange/yellow/upper cyan cabin glass)
    yy_grid = np.arange(h)[:, None]
    # Orange only counts as chassis paint above the wheel zone: generated rovers often have orange hubs.
    orange_or_glass = (
        ((yy_grid < by + bh * WHEEL_ZONE_Y) & (r_ch > g_ch + 22.0) & (r_ch > 110.0))
        | ((yy_grid < by + bh * 0.60) & (b_ch > r_ch + 45.0) & (g_ch > 145.0) & (lum > 145.0))
    )
    # Strip thin horizontal ground shadow streak along bottom 10% so distance peaks don't shift downward
    fg_no_shadow = fg.copy()
    shadow_y0 = int(by + bh * 0.90)
    vert_k = max(13, int(round(bh * 0.035)) | 1)
    if shadow_y0 - vert_k >= 0 and shadow_y0 < h:
        opened_bot = cv2.morphologyEx(fg[shadow_y0 - vert_k:, :], cv2.MORPH_OPEN, np.ones((vert_k, 3), np.uint8))
        fg_no_shadow[shadow_y0:, :] = opened_bot[vert_k:, :]

    tire_raw = ((fg_no_shadow > 0) & (~orange_or_glass) & (lum < 165.0)).astype(np.uint8)
    tire_raw[:max(0, int(by + bh * 0.50)), :] = 0
    close_k = max(15, int(round(min(bw, bh) * 0.030)) | 1)
    # Morphological close bridges thin specular tread highlight lines inside dark tires
    tire_mask = (
        cv2.morphologyEx(
            tire_raw,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_k, close_k)),
        )
        & fg_no_shadow
        & (~orange_or_glass).astype(np.uint8)
    )
    open_k = max(11, int(round(min(bw, bh) * 0.022)) | 1)
    tire_mask = cv2.morphologyEx(
        tire_mask,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (open_k, open_k)),
    )

    min_r = max(14, int(round(min(bw, bh) * 0.052)))
    max_r = max(min_r + 8, int(round(min(bw, bh) * 0.22)))
    min_dist = max(min_r * 1.85, bw * 0.14)

    candidates: list[tuple[float, float, float, float]] = []

    # 1) Primary: Inscribed Euclidean distance peaks inside dark tire regions (100% inside tire, zero fender overlap!)
    if int(tire_mask.sum()) > 120:
        dist = cv2.distanceTransform(tire_mask, cv2.DIST_L2, 5)
        nms_k = max(21, int(round(min_dist * 0.55)) | 1)
        dil = cv2.dilate(dist, np.ones((nms_k, nms_k), np.uint8))
        peaks = (dist == dil) & (dist >= min_r * 0.85) & (dist <= max_r * 1.08)
        pys, pxs = np.where(peaks)
        for py, px in zip(pys, pxs):
            rad = float(dist[py, px])
            cx, cy = float(px), float(py)
            if cy + rad < by + bh * 0.68:
                continue
            ground_prox = 1.0 - min(1.0, abs((by + bh) - (cy + rad)) / max(16.0, bh * 0.28))
            sc = 0.72 + 0.28 * ground_prox + 0.08 * min(1.0, rad / max(1.0, float(max_r)))
            candidates.append((cx, cy, rad, sc))

    # 2) Fallback: HoughCircles on lower silhouette when tire color isn't dark (e.g. monochrome/light wheels)
    if len(candidates) < 2:
        band_y0 = max(0, int(by + bh * 0.42))
        band_y1 = min(h, int(by + bh + max(8, bh * 0.04)))
        band_x0 = max(0, int(bx - max(6, bw * 0.03)))
        band_x1 = min(w, int(bx + bw + max(6, bw * 0.03)))
        crop_rgba = rgba[band_y0:band_y1, band_x0:band_x1]
        crop_fg = fg[band_y0:band_y1, band_x0:band_x1]
        gray = cv2.cvtColor(crop_rgba[:, :, :3], cv2.COLOR_RGB2GRAY)
        gray_masked = np.where(crop_fg > 0, gray, 245).astype(np.uint8)
        blurred = cv2.GaussianBlur(gray_masked, (5, 5), 1.3)
        circles = cv2.HoughCircles(
            blurred, cv2.HOUGH_GRADIENT, dp=1.2, minDist=float(min_dist),
            param1=90, param2=24, minRadius=min_r, maxRadius=max_r,
        )
        if circles is not None and len(circles) > 0:
            for c in circles[0]:
                cx = float(c[0]) + band_x0
                cy = float(c[1]) + band_y0
                rad = float(c[2])
                if rad < min_r or rad > max_r or cy + rad < by + bh * 0.74 or cy + rad > by + bh + 6:
                    continue
                x0, y0 = max(0, int(cx - rad)), max(0, int(cy - rad))
                x1, y1 = min(w, int(cx + rad + 1)), min(h, int(cy + rad + 1))
                yy, xx = np.mgrid[y0:y1, x0:x1]
                disk = ((xx - cx) ** 2 + (yy - cy) ** 2) <= (rad ** 2)
                if int(disk.sum()) < 20:
                    continue
                occ = float(fg[y0:y1, x0:x1][disk].mean())
                paint_hit = float(orange_or_glass[y0:y1, x0:x1][disk].mean())
                if occ >= 0.86 and paint_hit <= 0.12:
                    candidates.append((cx, cy, rad * 0.92, occ * 0.7))

    if not candidates:
        return []

    # Non-maximum suppression by horizontal spacing / circle overlap
    candidates.sort(key=lambda item: item[3], reverse=True)
    picked: list[tuple[float, float, float, float]] = []
    for cx, cy, r, sc in candidates:
        if any(
            math.hypot(cx - px, cy - py) < max(min_dist * 0.80, (r + pr) * 0.92)
            or abs(cx - px) < max(r, pr) * 1.15
            for px, py, pr, _ in picked
        ):
            continue
        picked.append((cx, cy, r, sc))
        if len(picked) >= 4:
            break

    if len(picked) < 2:
        return []

    max_picked_r = max(p[2] for p in picked)
    med_bottom = float(np.median([p[1] + p[2] for p in picked]))
    filtered = [
        (cx, cy, r)
        for cx, cy, r, _ in picked
        if r >= max(min_r * 0.85, max_picked_r * 0.50) and abs((cy + r) - med_bottom) <= max(28.0, bh * 0.18)
    ]
    if len(filtered) < 2:
        return []
    filtered.sort(key=lambda item: item[0])
    return filtered


def _detect_mast(rgba: np.ndarray, bbox: tuple[int, int, int, int]) -> tuple[np.ndarray, tuple[float, float]] | None:
    """Detect an optional upper sensor mast tower above the main chassis."""
    h, w = rgba.shape[:2]
    bx, by, bw, bh = bbox
    fg = (rgba[:, :, 3] >= 48).astype(np.uint8)

    # Check if top 26% of robot silhouette is narrow (< 38% of bw) while mid hull is wide
    top_y1 = int(by + bh * 0.26)
    mid_y0 = int(by + bh * 0.34)
    mid_y1 = int(by + bh * 0.62)
    if top_y1 <= by + 10:
        return None

    top_xs = np.where(fg[by:top_y1, :] > 0)[1]
    mid_xs = np.where(fg[mid_y0:max(mid_y0 + 4, mid_y1), :] > 0)[1]
    if len(top_xs) < 20 or len(mid_xs) < 40:
        return None

    top_span = float(top_xs.max() - top_xs.min() + 1)
    mid_span = float(mid_xs.max() - mid_xs.min() + 1)
    if top_span > mid_span * 0.42:
        return None

    # Find mast base row where width widens into hull
    mast_base_y = top_y1
    for y in range(top_y1, min(h, mid_y0 + 1)):
        xs = np.where(fg[y, :] > 0)[0]
        if len(xs) > 0 and (xs[-1] - xs[0] + 1) > top_span * 1.45:
            mast_base_y = y
            break

    base_xs = np.where(fg[max(by, mast_base_y - 3):mast_base_y + 2, :] > 0)[1]
    mast_cx = float(np.median(base_xs)) if len(base_xs) > 0 else float(np.median(top_xs))
    cap_r = max(8.0, top_span * 0.22)

    mast_mask = np.zeros((h, w), dtype=np.uint8)
    x_lo = max(0, int(top_xs.min() - 6))
    x_hi = min(w, int(top_xs.max() + 7))
    mast_mask[by:mast_base_y, x_lo:x_hi] = fg[by:mast_base_y, x_lo:x_hi]
    cv2.circle(mast_mask, (int(round(mast_cx)), int(round(mast_base_y))), int(round(cap_r)), 1, -1)
    mast_mask &= fg
    if int(mast_mask.sum()) < 30:
        return None
    return mast_mask, (mast_cx, float(mast_base_y))


def _prepare_optional_arm(
    arm_path: Path,
    canvas_size: tuple[int, int],
    robot_bbox: tuple[int, int, int, int],
    native_facing: str,
    out_dir: Path,
) -> dict | None:
    if not arm_path.is_file():
        return None
    w, h = canvas_size
    bx, by, bw, bh = robot_bbox
    arm_raw = np.asarray(Image.open(arm_path).convert('RGBA'))
    arm_rgba = _ensure_rgba_matte(arm_raw)
    ah, aw = arm_rgba.shape[:2]
    ays, axs = np.where(arm_rgba[:, :, 3] >= 48)
    if len(axs) < 20:
        return None

    # Crop tight arm sprite; Kolors draws side-view arm from mount base on left -> gripper on right,
    # so mirror horizontally when robot's native_facing is 'left' so gripper extends outward ahead of the bumper!
    ax0, ay0, ax1, ay1 = int(axs.min()), int(ays.min()), int(axs.max() + 1), int(ays.max() + 1)
    crop = Image.fromarray(arm_rgba[ay0:ay1, ax0:ax1], 'RGBA')
    if native_facing == 'left':
        crop = crop.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    target_len = max(32, int(round(bw * 0.25)))
    scale = target_len / max(1, max(crop.width, crop.height))
    resized = crop.resize((max(8, round(crop.width * scale)), max(8, round(crop.height * scale))), Image.Resampling.LANCZOS)

    mount_x = int(round(bx + bw * (0.86 if native_facing == 'right' else 0.14)))
    mount_y = int(round(by + bh * 0.56))
    paste_x = mount_x - (int(round(resized.width * 0.14)) if native_facing == 'right' else int(round(resized.width * 0.86)))
    paste_y = mount_y - int(round(resized.height * 0.5))

    canvas = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    paste_x_clamped = max(0, min(w - resized.width, paste_x))
    paste_y_clamped = max(0, min(h - resized.height, paste_y))
    canvas.alpha_composite(resized, (paste_x_clamped, paste_y_clamped))

    arm_out = out_dir / 'arm.png'
    canvas.save(arm_out, optimize=True)
    pivot_x = paste_x_clamped + resized.width * (0.14 if native_facing == 'right' else 0.86)
    pivot_y = paste_y_clamped + resized.height * 0.50
    return {
        'file': str(arm_out),
        'pivot': [
            round(float(np.clip(pivot_x / w, 0.0, 1.0)), 5),
            round(float(np.clip(pivot_y / h, 0.0, 1.0)), 5),
        ],
    }


def _cache_valid(rig_json_path: Path) -> bool:
    if not rig_json_path.is_file():
        return False
    try:
        data = json.loads(rig_json_path.read_text(encoding='utf-8'))
        body_file = data.get('body_file') or (data.get('body') if isinstance(data.get('body'), str) else None)
        if not body_file or not Path(body_file).is_file():
            return False
        for w_item in data.get('wheels') or []:
            wf = w_item.get('file') or w_item.get('src')
            if not wf or not Path(wf).is_file():
                return False
            tf = w_item.get('tire_file') or w_item.get('tire_src')
            if tf and not Path(tf).is_file():
                return False
        for opt in ('mast', 'arm'):
            part = data.get(opt)
            if isinstance(part, dict):
                pf = part.get('file') or part.get('src')
                if not pf or not Path(pf).is_file():
                    return False
        return True
    except Exception:
        return False


def rig_robot_master(
    master_path: Path,
    arm_path: Path | None = None,
    cache_root: Path = Path('media/rigs'),
    wheel_hints: list[list[float]] | None = None,
    facing_hint: str | None = None,
) -> Path:
    hasher = hashlib.sha256(b'robot-rig-v3-true-roll')
    if wheel_hints:
        hasher.update(b'|hints|' + json.dumps(wheel_hints).encode() + str(facing_hint).encode())
    hasher.update(master_path.read_bytes())
    if arm_path and arm_path.is_file():
        hasher.update(b'|arm|')
        hasher.update(arm_path.read_bytes())
    digest = hasher.hexdigest()[:16]
    out_dir = cache_root / f'robot-{digest}'
    rig_json_path = out_dir / 'rig.json'
    if _cache_valid(rig_json_path):
        return rig_json_path

    out_dir.mkdir(parents=True, exist_ok=True)
    raw = np.asarray(Image.open(master_path).convert('RGBA'))
    rgba = _ensure_rgba_matte(raw)
    h, w = rgba.shape[:2]
    alpha = rgba[:, :, 3]
    fg_ys, fg_xs = np.where(alpha >= 48)
    if len(fg_xs) < 64:
        body_path = out_dir / 'body.png'
        Image.fromarray(rgba, 'RGBA').save(body_path, optimize=True)
        doc = {
            'level': 'sprite',
            'width': w,
            'height': h,
            'anchor_x': 0.5,
            'foot_y': 0.92,
            'native_facing': 'right',
            'body_file': str(body_path),
            'body_pivot': [0.5, 0.72],
            'wheels': [],
            'signal_point': [0.5, 0.24],
        }
        rig_json_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding='utf-8')
        return rig_json_path

    bx = int(fg_xs.min())
    by = int(fg_ys.min())
    bw = max(1, int(fg_xs.max() - bx + 1))
    bh = max(1, int(fg_ys.max() - by + 1))
    bbox = (bx, by, bw, bh)

    # Estimate native facing: in 3/4 perspective, the front wheels closer to the viewer have larger radius
    # Explicit orthographic wheel circles (cx/W, cy/H, r/H) win over auto-detection: Kolors rovers with
    # coloured hubs and ground shadows defeat the colour/shape heuristics, and flat wheels only need a circle.
    wheels = sorted(
        ((float(hx) * w, float(hy) * h, float(hr) * h) for hx, hy, hr in (wheel_hints or [])),
        key=lambda item: item[0],
    )
    ortho_wheels = len(wheels) >= 2
    if not ortho_wheels:
        wheels = _detect_wheels(rgba, bbox)
    if ortho_wheels:
        native_facing = facing_hint if facing_hint in ('left', 'right') else 'right'
    elif len(wheels) >= 2 and wheels[0][2] > wheels[-1][2] * 1.18:
        native_facing = 'left'
    elif len(wheels) >= 2 and wheels[-1][2] > wheels[0][2] * 1.18:
        native_facing = 'right'
    else:
        native_facing = 'right'
    anchor_x = round(float(np.clip((bx + bw * 0.5) / w, 0.05, 0.95)), 5)
    foot_y = round(float(np.clip((by + bh) / h, 0.2, 1.0)), 5)

    if len(wheels) < 2:
        body_path = out_dir / 'body.png'
        Image.fromarray(rgba, 'RGBA').save(body_path, optimize=True)
        arm_meta = _prepare_optional_arm(arm_path, (w, h), bbox, native_facing, out_dir) if arm_path else None
        doc = {
            'level': 'sprite',
            'width': w,
            'height': h,
            'anchor_x': anchor_x,
            'foot_y': foot_y,
            'content_top': round(by / h, 5),
            'native_facing': native_facing,
            'body_file': str(body_path),
            'body_pivot': [anchor_x, round(float(np.clip((by + bh * 0.65) / h, 0.1, 0.98)), 5)],
            'wheels': [],
            'signal_point': [anchor_x, round(float(np.clip((by + bh * 0.14) / h, 0.04, 0.9)), 5)],
            **({'arm': arm_meta} if arm_meta else {}),
        }
        rig_json_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'rig_robot: {master_path.name} -> {out_dir.name} level=sprite (no wheel pair)', flush=True)
        return rig_json_path

    body_rgba = rgba.copy()
    wheels_meta: list[dict] = []
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)

    r_ch = rgba[:, :, 0].astype(np.float32)
    g_ch = rgba[:, :, 1].astype(np.float32)
    b_ch = rgba[:, :, 2].astype(np.float32)
    lum = 0.299 * r_ch + 0.587 * g_ch + 0.114 * b_ch
    yy_grid = np.arange(h)[:, None]
    painted_body = (
        ((yy_grid < by + bh * WHEEL_ZONE_Y) & (r_ch > g_ch + 22.0) & (r_ch > 110.0))
        | ((yy_grid < by + bh * 0.60) & (b_ch > r_ch + 45.0) & (g_ch > 145.0) & (lum > 145.0))
    )
    fg_nonpaint = (alpha >= 64) & (~painted_body)

    # Compute horizontal gap boundaries between consecutive wheels so segmentation stays local to each tire
    low_y0 = max(0, int(by + bh * 0.78))
    low_y1 = min(h, int(by + bh * 0.94))
    col_mass = fg_nonpaint[low_y0:max(low_y0 + 1, low_y1), :].sum(axis=0)
    gaps = [bx]
    for i in range(len(wheels) - 1):
        lx, rx_c = int(round(wheels[i][0])), int(round(wheels[i + 1][0]))
        if rx_c > lx + 2:
            sub_mass = col_mass[lx:rx_c]
            min_xs = np.where(sub_mass <= sub_mass.min() + 2)[0]
            gaps.append(lx + int(np.median(min_xs)))
        else:
            gaps.append((lx + rx_c) // 2)
    gaps.append(bx + bw)

    # Also build clean dark-tire mask for mid-row seed anchoring
    tire_clean = ((fg_nonpaint) & (lum < 165.0)).astype(np.uint8)
    tire_clean[:max(0, int(by + bh * 0.50)), :] = 0
    open_k = max(11, int(round(min(bw, bh) * 0.022)) | 1)
    tire_clean = cv2.morphologyEx(
        tire_clean,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (open_k, open_k)),
    )

    # Erase horizontal floor contact shadow strip along the very bottom outside the wheel patches
    # so body.png has no floating shadow bar left behind after wheel separation
    floor_band_y = by + bh * 0.905
    shadow_strip = (yy >= floor_band_y) & (alpha > 0) & (~painted_body)
    body_rgba[:, :, 3] = np.where(shadow_strip, 0, body_rgba[:, :, 3])

    for idx, (seed_cx, seed_cy, seed_r) in enumerate(wheels):
        x_lo = max(0, gaps[idx] - 2)
        x_hi = min(w, gaps[idx + 1] + 2)
        if ortho_wheels:
            # Flat orthographic wheel: exact circle from the ground-contact arc fit.
            tire_mask = (((xx - seed_cx) ** 2 + (yy - seed_cy) ** 2) <= (seed_r * 1.03) ** 2).astype(np.uint8)
            cx, cy, rx, ry, tilt_deg = seed_cx, seed_cy, seed_r, seed_r, 0.0
        else:
            tire_mask, cx, cy, rx, ry, tilt_deg = _segment_wheel_ellipse(
                rgba, fg_nonpaint.astype(np.uint8), tire_clean, bbox, (seed_cx, seed_cy, seed_r), x_lo, x_hi,
            )
        r_eff = max(8.0, float(max(rx, ry) * 0.95 if min(rx, ry) / max(1.0, max(rx, ry)) >= 0.86 else ry))
        aspect_ratio = min(rx, ry) / max(1.0, max(rx, ry))
        is_orthographic = aspect_ratio >= 0.86

        wheel_rgba, tire_rgba = _render_concentric_wheel_rgba(
            rgba, tire_mask, cx, cy, rx, ry, tilt_deg, r_eff, is_orthographic,
        )
        w_path = out_dir / f'wheel_{idx}.png'
        Image.fromarray(wheel_rgba, 'RGBA').save(w_path, optimize=True)

        wheel_entry: dict = {
            'file': str(w_path),
            'cx': round(float(cx / w), 5),
            'cy': round(float(cy / h), 5),
            'r': round(float(r_eff / h), 5),
            'rx': round(float(rx / w), 5),
            'ry': round(float(ry / h), 5),
            'tilt_deg': round(float(tilt_deg), 3),
        }
        if tire_rgba is not None:
            t_path = out_dir / f'tire_{idx}.png'
            Image.fromarray(tire_rgba, 'RGBA').save(t_path, optimize=True)
            wheel_entry['tire_file'] = str(t_path)
        wheels_meta.append(wheel_entry)

        # Clean wheel-well separation on body.png:
        # 1) Erase (alpha = 0) all exposed lower tire pixels (yy >= cy - r * 0.12 inside tire_mask)
        #    so static jagged tire teeth NEVER stick out behind the spinning wheel!
        dist_body = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        rad_t = math.radians(tilt_deg)
        ct_b, st_b = math.cos(rad_t), math.sin(rad_t)
        ux_b = (xx - cx) * ct_b + (yy - cy) * st_b
        uy_b = -(xx - cx) * st_b + (yy - cy) * ct_b
        ell_body = np.sqrt((ux_b / max(1.0, rx)) ** 2 + (uy_b / max(1.0, ry)) ** 2)
        # Cover full outer tire silhouette (tire_mask, fitted ellipse, and orthographic outer_r circle)
        tire_region = (
            (tire_mask > 0)
            | ((ell_body <= 1.06) & fg_nonpaint)
            | ((dist_body <= r_eff * 1.06) & fg_nonpaint)
        ) & (~painted_body)
        erase_lower = tire_region & (yy >= cy - r_eff * 0.12)
        body_rgba[:, :, 3] = np.where(erase_lower, 0, body_rgba[:, :, 3])

        # 2) In the upper fender arch (yy < cy + r * 0.22), paint a clean smooth dark recessed
        #    wheel-well backing arc (rgb = [22, 28, 38], radius 0.88 * r) so suspension bob +-1.5px
        #    never opens a transparent hole!
        #    Erase exposed upper tire teeth outside the recessed 0.88 * r arch first.
        erase_upper_teeth = (
            tire_region
            & (yy < cy - r_eff * 0.12)
            & (dist_body > r_eff * 0.88)
            & ((ell_body > 0.88) if not is_orthographic else True)
        )
        body_rgba[:, :, 3] = np.where(erase_upper_teeth, 0, body_rgba[:, :, 3])

        arch_zone = (dist_body <= r_eff * 0.88) if is_orthographic else ((dist_body <= r_eff * 0.88) | (ell_body <= 0.88))
        # Paint clean recessed wheel-well backing arc in upper arch (above cy - 0.12*r so lower half stays transparent)
        arch_backing = (yy < cy - r_eff * 0.12) & (yy >= cy - r_eff * 1.05) & arch_zone & (~painted_body)
        arch_solid = arch_backing & ((body_rgba[:, :, 3] >= 48) | (tire_mask > 0))
        body_rgba[:, :, 0] = np.where(arch_solid, 22, body_rgba[:, :, 0])
        body_rgba[:, :, 1] = np.where(arch_solid, 28, body_rgba[:, :, 1])
        body_rgba[:, :, 2] = np.where(arch_solid, 38, body_rgba[:, :, 2])
        body_rgba[:, :, 3] = np.where(arch_solid, 255, body_rgba[:, :, 3])

    mast_meta = None
    signal_pt = [anchor_x, round(float(np.clip((by + bh * 0.14) / h, 0.04, 0.9)), 5)]
    mast_det = _detect_mast(rgba, bbox)
    if mast_det is not None:
        mast_mask, (mcx, mby) = mast_det
        mast_part = np.empty_like(rgba)
        mast_part[:, :, :3] = rgba[:, :, :3]
        soft_mast = cv2.GaussianBlur(mast_mask.astype(np.float32), (3, 3), 0.6)
        mast_part[:, :, 3] = np.clip(alpha.astype(np.float32) * soft_mast, 0, 255).astype(np.uint8)
        mast_path = out_dir / 'mast.png'
        Image.fromarray(mast_part, 'RGBA').save(mast_path, optimize=True)
        mast_meta = {
            'file': str(mast_path),
            'pivot': [round(float(mcx / w), 5), round(float(mby / h), 5)],
        }
        # Subtract upper mast above pivot cap from body
        cut_above = (mast_mask > 0) & (yy < mby - 5.0)
        body_rgba[:, :, 3] = np.where(cut_above, 0, body_rgba[:, :, 3])
        signal_pt = [round(float(mcx / w), 5), round(float(np.clip((by + 8.0) / h, 0.03, 0.85)), 5)]

    body_path = out_dir / 'body.png'
    Image.fromarray(body_rgba, 'RGBA').save(body_path, optimize=True)

    arm_meta = _prepare_optional_arm(arm_path, (w, h), bbox, native_facing, out_dir) if arm_path else None
    axle_y = float(np.mean([w_item['cy'] for w_item in wheels_meta]))

    doc = {
        'level': 'wheeled',
        'width': w,
        'height': h,
        'anchor_x': anchor_x,
        'foot_y': foot_y,
        'content_top': round(by / h, 5),
        'native_facing': native_facing,
        'body_file': str(body_path),
        'body_pivot': [anchor_x, round(float(np.clip(axle_y - 0.12, 0.1, 0.95)), 5)],
        'wheels': wheels_meta,
        'signal_point': signal_pt,
        **({'mast': mast_meta} if mast_meta else {}),
        **({'arm': arm_meta} if arm_meta else {}),
    }
    rig_json_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'rig_robot: {master_path.name} -> {out_dir.name} level=wheeled wheels={len(wheels_meta)} mast={bool(mast_meta)} arm={bool(arm_meta)}', flush=True)
    return rig_json_path


def process_manifest(manifest_path: Path, cache_dir: str | None = None) -> None:
    doc = json.loads(manifest_path.read_text(encoding='utf-8'))
    cache_root = Path(cache_dir or os.environ.get('SOUNDBOOK_RIG_CACHE', 'media/rigs'))
    pair_cache: dict[tuple[Path, Path | None], str] = {}

    def _rig(master_str: str | None, arm_str: str | None = None, hints: list | None = None, facing: str | None = None) -> str | None:
        if not master_str:
            return None
        mp = Path(master_str)
        if not mp.is_file():
            return None
        ap = Path(arm_str) if arm_str and Path(arm_str).is_file() else None
        key = (mp.resolve(), ap.resolve() if ap else None)
        if key not in pair_cache:
            pair_cache[key] = str(rig_robot_master(mp, ap, cache_root, hints, facing))
        return pair_cache[key]

    default_rig_json = None
    robot_cfg = doc.get('robot_rig')
    if isinstance(robot_cfg, dict):
        default_rig_json = _rig(
            robot_cfg.get('master_file') or robot_cfg.get('file') or robot_cfg.get('rig_file'),
            robot_cfg.get('arm_file'),
            robot_cfg.get('wheel_hints'),
            robot_cfg.get('native_facing'),
        )
        if default_rig_json:
            robot_cfg['rig_json'] = default_rig_json
            doc['robot_rig_json'] = default_rig_json
    elif doc.get('robot_rig_file'):
        default_rig_json = _rig(doc.get('robot_rig_file'), doc.get('robot_arm_file'))
        if default_rig_json:
            doc['robot_rig_json'] = default_rig_json

    for shot in doc.get('shots') or []:
        if not isinstance(shot, dict):
            continue
        sm = shot.get('story_motion') if isinstance(shot.get('story_motion'), dict) else None
        rv = shot.get('rover') if isinstance(shot.get('rover'), dict) else None

        master_f = (
            shot.get('robot_rig_file')
            or (sm.get('robot_rig_file') if sm else None)
            or (rv.get('rig_file') if rv else None)
        )
        arm_f = (
            shot.get('robot_arm_file')
            or (sm.get('robot_arm_file') if sm else None)
            or (rv.get('arm_file') if rv else None)
        )
        shot_rig_json = _rig(master_f, arm_f) or default_rig_json
        if shot_rig_json:
            if not default_rig_json:
                default_rig_json = shot_rig_json
                doc['robot_rig_json'] = shot_rig_json
            if rv is not None or shot.get('robot_rig_file') or (sm and (sm.get('robot_rig_file') or sm.get('robot_rig_key'))):
                shot['robot_rig_json'] = shot_rig_json
            if rv is not None:
                rv['rig_json'] = shot_rig_json
            if sm is not None and (rv is not None or sm.get('robot_rig_file') or sm.get('robot_rig_key')):
                sm['robot_rig_json'] = shot_rig_json

    manifest_path.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')
    print(f'rig_robot: rigged {len(pair_cache)} unique robot master(s)', flush=True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest')
    parser.add_argument('--cache-dir', default=None, help='rig cache root (default: $SOUNDBOOK_RIG_CACHE or media/rigs)')
    cli = parser.parse_args()
    process_manifest(Path(cli.manifest), cli.cache_dir)

