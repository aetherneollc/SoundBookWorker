#!/usr/bin/env python3
"""Auto-rig full-body anime character masters into reusable cutout puppet parts.

Usage:
    python renderer/rig_characters.py manifest.json

Collects character masters from `manifest['character_rigs'][].master_file` and
`shots[].story_motion.actors[].rig_file`, caches part PNGs + `rig.json` by
SHA-256 (`sha256[:16]`) under `media/rigs/char-<hash>/`, and attaches `rig_json`
onto character entries and story_motion actors in `manifest.json`.

Tries `rtmlib` Wholebody ONNX when installed and reachable, and falls back to
deterministic silhouette-geometry analysis so rigging never crashes or hangs on
network downloads.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import socket
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


FULL_PARTS = (
    'torso',
    'head',
    'upper_arm_l',
    'lower_arm_l',
    'upper_arm_r',
    'lower_arm_r',
    'upper_leg_l',
    'lower_leg_l',
    'foot_l',
    'upper_leg_r',
    'lower_leg_r',
    'foot_r',
)
UPPER_PARTS = (
    'body',
    'head',
    'upper_arm_l',
    'lower_arm_l',
    'upper_arm_r',
    'lower_arm_r',
)
SPRITE_PARTS = ('body',)


def _ensure_rgba_matte(rgba: np.ndarray) -> np.ndarray:
    """Keep existing alpha if already matted; otherwise chroma-key bright green background."""
    out = rgba.copy()
    alpha = out[:, :, 3]
    h, w = alpha.shape
    if float((alpha < 200).mean()) <= 0.10:
        r = out[:, :, 0].astype(np.float32)
        g = out[:, :, 1].astype(np.float32)
        b = out[:, :, 2].astype(np.float32)
        excess_g = g - np.maximum(r, b)
        # Sample border green to adapt to both pure #00ff00 and slightly soft anime greens
        border = np.concatenate([
            out[0, :, :3], out[-1, :, :3], out[:, 0, :3], out[:, -1, :3]
        ], axis=0).astype(np.float32)
        border_green = float(np.median(border[:, 1] - np.maximum(border[:, 0], border[:, 2])))
        if border_green > 22.0:
            fg = 1.0 - np.clip((excess_g - 18.0) / max(24.0, border_green * 0.75), 0.0, 1.0)
            low_val = (g < 85.0).astype(np.float32)
            alpha = np.clip(np.maximum(fg, low_val) * 255.0, 0, 255).astype(np.uint8)
            # Despill green fringe on semi-transparent edge pixels
            spill = np.maximum(0.0, g - np.maximum(r, b) * 1.04)
            edge = (alpha > 0) & (alpha < 250)
            out[:, :, 1] = np.where(edge, np.clip(g - spill * 0.75, 0, 255), g).astype(np.uint8)
            out[:, :, 3] = alpha

    binary = (out[:, :, 3] >= 48).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    if count <= 1:
        return out
    areas = stats[1:, cv2.CC_STAT_AREA]
    main_idx = 1 + int(np.argmax(areas))
    main_area = int(stats[main_idx, cv2.CC_STAT_AREA])
    mx = int(stats[main_idx, cv2.CC_STAT_LEFT])
    my = int(stats[main_idx, cv2.CC_STAT_TOP])
    mw = int(stats[main_idx, cv2.CC_STAT_WIDTH])
    mh = int(stats[main_idx, cv2.CC_STAT_HEIGHT])
    pad_x = max(12, int(w * 0.04))
    pad_y = max(12, int(h * 0.04))

    keep = labels == main_idx
    for idx in range(1, count):
        if idx == main_idx:
            continue
        area = int(stats[idx, cv2.CC_STAT_AREA])
        if area < max(24, int(main_area * 0.004)):
            continue
        cx = int(stats[idx, cv2.CC_STAT_LEFT])
        cy = int(stats[idx, cv2.CC_STAT_TOP])
        cw = int(stats[idx, cv2.CC_STAT_WIDTH])
        ch = int(stats[idx, cv2.CC_STAT_HEIGHT])
        if (cx + cw >= mx - pad_x and cx <= mx + mw + pad_x
                and cy + ch >= my - pad_y and cy <= my + mh + pad_y):
            keep |= (labels == idx)

    keep_mask = cv2.dilate(keep.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1)
    out[:, :, 3] = np.where(keep_mask > 0, out[:, :, 3], 0)

    # Strip any residual green-screen floor shadow sliver directly under the boots (bottom 7% of character bbox)
    fg_ys_now = np.where(out[:, :, 3] >= 48)[0]
    if len(fg_ys_now) > 32:
        bot_y = int(fg_ys_now.max())
        top_y = int(fg_ys_now.min())
        foot_zone = np.zeros((h, w), dtype=bool)
        foot_zone[max(0, bot_y - max(12, int((bot_y - top_y) * 0.065))):bot_y + 1, :] = True
        r_f = out[:, :, 0].astype(np.float32)
        g_f = out[:, :, 1].astype(np.float32)
        b_f = out[:, :, 2].astype(np.float32)
        green_shadow = foot_zone & (g_f > r_f + 16.0) & (g_f > b_f + 16.0) & (g_f > 52.0)
        out[:, :, 3] = np.where(green_shadow, 0, out[:, :, 3])

        # Dark desaturated cast-shadow bar painted under the soles: remove only
        # wide, flat components so dark boot outlines/soles stay intact.
        char_h = bot_y - top_y
        sole_zone = np.zeros((h, w), dtype=bool)
        sole_zone[max(0, bot_y - max(10, int(char_h * 0.036))):bot_y + 1, :] = True
        max_c = np.maximum(np.maximum(r_f, g_f), b_f)
        dark_shadow = (
            sole_zone & (out[:, :, 3] >= 16)
            & (g_f >= r_f + 7.0) & (g_f >= b_f + 1.0) & (max_c < 100.0) & (max_c > 18.0)
        ).astype(np.uint8)
        n_sh, lab_sh, st_sh, _ = cv2.connectedComponentsWithStats(dark_shadow, 8)
        for idx in range(1, n_sh):
            sw_ = int(st_sh[idx, cv2.CC_STAT_WIDTH])
            sh_ = int(st_sh[idx, cv2.CC_STAT_HEIGHT])
            if int(st_sh[idx, cv2.CC_STAT_AREA]) >= 30 and sw_ >= sh_ * 2:
                blob = cv2.dilate((lab_sh == idx).astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1) > 0
                out[:, :, 3] = np.where(blob & sole_zone & (max_c < 110.0), 0, out[:, :, 3])

        # Opaque flat pale backdrop trapped between the legs/shoes (matting
        # missed the enclosed gap).  Accept only large, uniform, low-chroma
        # components that touch existing transparency.
        legs_zone = np.zeros((h, w), dtype=bool)
        legs_zone[top_y + int(char_h * 0.70):bot_y + 1, :] = True
        lum_f = 0.299 * r_f + 0.587 * g_f + 0.114 * b_f
        chroma = max_c - np.minimum(np.minimum(r_f, g_f), b_f)
        pale = (legs_zone & (out[:, :, 3] >= 200) & (lum_f >= 180.0) & (chroma <= 60.0)).astype(np.uint8)
        transparent = (out[:, :, 3] < 16).astype(np.uint8)
        near_clear = cv2.dilate(transparent, np.ones((5, 5), np.uint8), iterations=1) > 0
        n_p, lab_p, st_p, _ = cv2.connectedComponentsWithStats(pale, 4)
        for idx in range(1, n_p):
            if int(st_p[idx, cv2.CC_STAT_AREA]) < 150:
                continue
            comp = lab_p == idx
            if not bool((comp & near_clear).any()):
                continue
            if float(out[:, :, :3][comp].astype(np.float32).std(axis=0).max()) > 16.0:
                continue
            grown = cv2.dilate(comp.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1) > 0
            out[:, :, 3] = np.where(grown & (lum_f >= 150.0) & (chroma <= 70.0), 0, out[:, :, 3])
    return out


_RTMLIB_MODEL = None
_RTMLIB_TRIED = False


def _try_rtmlib_keypoints(rgba: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    """Attempt RTMPose Wholebody 133-keypoint inference without hanging on network."""
    global _RTMLIB_MODEL, _RTMLIB_TRIED
    if os.environ.get('SOUNDBOOK_SKIP_RTMLIB', '').strip() in {'1', 'true', 'yes'}:
        return None
    if not _RTMLIB_TRIED:
        _RTMLIB_TRIED = True
        prev_timeout = socket.getdefaulttimeout()
        try:
            socket.setdefaulttimeout(float(os.environ.get('RTMLIB_TIMEOUT', '12')))
            from rtmlib import Wholebody  # type: ignore
            _RTMLIB_MODEL = Wholebody(to_openpose=False, mode='balanced', backend='onnxruntime', device='cpu')
        except Exception as exc:
            print(f'rig_characters: rtmlib unavailable ({type(exc).__name__}: {exc}); using silhouette geometry', flush=True)
            _RTMLIB_MODEL = None
        finally:
            socket.setdefaulttimeout(prev_timeout)

    if _RTMLIB_MODEL is None:
        return None

    try:
        # Composite over neutral gray so transparent regions don't confuse detector
        alpha = rgba[:, :, 3:4].astype(np.float32) / 255.0
        rgb = rgba[:, :, :3].astype(np.float32) * alpha + 196.0 * (1.0 - alpha)
        bgr = cv2.cvtColor(rgb.astype(np.uint8), cv2.COLOR_RGB2BGR)
        keypoints, scores = _RTMLIB_MODEL(bgr)
        if keypoints is None or scores is None or len(keypoints) == 0:
            return None
        # Pick person whose keypoints overlap foreground alpha best
        best_i = 0
        best_score = -1.0
        h, w = rgba.shape[:2]
        for i in range(len(keypoints)):
            kps = np.asarray(keypoints[i], dtype=np.float32)
            scs = np.asarray(scores[i], dtype=np.float32)
            valid = scs[:17] > 0.3
            if int(valid.sum()) < 5:
                continue
            pts = kps[:17][valid]
            xs = np.clip(pts[:, 0].astype(int), 0, w - 1)
            ys = np.clip(pts[:, 1].astype(int), 0, h - 1)
            fg_hits = float((rgba[ys, xs, 3] >= 48).mean())
            score = float(scs[:17].mean()) * (0.4 + 0.6 * fg_hits)
            if score > best_score:
                best_score = score
                best_i = i
        if best_score < 0.22:
            return None
        return np.asarray(keypoints[best_i], dtype=np.float32), np.asarray(scores[best_i], dtype=np.float32)
    except Exception as exc:
        print(f'rig_characters: rtmlib inference failed ({type(exc).__name__}); using silhouette geometry', flush=True)
        return None


def _sample_skin_and_line(rgba: np.ndarray, head_box: tuple[int, int, int, int]) -> tuple[list[int], list[int], tuple[int, int, int, int]]:
    """Estimate skin RGB, line-art RGB, and face sub-box inside head_box."""
    h, w = rgba.shape[:2]
    hx, hy, hw, hh = head_box
    x0 = max(0, hx)
    y0 = max(0, hy)
    x1 = min(w, hx + hw)
    y1 = min(h, hy + hh)
    crop = rgba[y0:y1, x0:x1]
    if crop.size == 0:
        return [242, 212, 194], [36, 28, 34], head_box

    alpha = crop[:, :, 3]
    rgb = crop[:, :, :3].astype(np.float32)
    fg = alpha >= 160
    if int(fg.sum()) < 30:
        return [242, 212, 194], [36, 28, 34], head_box

    lum = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
    fg_lum = lum[fg]
    dark_thresh = float(np.percentile(fg_lum, 14))
    line_mask = fg & (lum <= max(75.0, dark_thresh))
    if int(line_mask.sum()) >= 10:
        line_med = np.median(rgb[line_mask], axis=0)
        line_rgb = [int(np.clip(round(v), 10, 95)) for v in line_med]
    else:
        line_rgb = [36, 28, 34]

    # Sample central face zone for warm/light anime skin
    ch, cw = crop.shape[:2]
    cy0, cy1 = int(ch * 0.30), int(ch * 0.86)
    cx0, cx1 = int(cw * 0.20), int(cw * 0.80)
    center_fg = np.zeros_like(fg)
    center_fg[cy0:cy1, cx0:cx1] = True
    warm = (rgb[:, :, 0] >= rgb[:, :, 1] - 8) & (rgb[:, :, 1] >= rgb[:, :, 2] - 12)
    mid_bright = (lum >= max(115.0, float(np.percentile(fg_lum, 42)))) & (lum <= 250.0)
    skin_mask = fg & center_fg & warm & mid_bright
    if int(skin_mask.sum()) < 25:
        skin_mask = fg & center_fg & (lum >= float(np.percentile(fg_lum, 50)))
    if int(skin_mask.sum()) >= 15:
        skin_med = np.median(rgb[skin_mask], axis=0)
        skin_rgb = [int(np.clip(round(v), 90, 252)) for v in skin_med]
        ys, xs = np.where(skin_mask)
        fx0 = x0 + int(np.percentile(xs, 6))
        fy0 = y0 + int(np.percentile(ys, 6))
        fx1 = x0 + int(np.percentile(xs, 94))
        fy1 = y0 + int(np.percentile(ys, 96))
        if (fx1 - fx0) >= hw * 0.26 and (fy1 - fy0) >= hh * 0.24:
            face_box = (fx0, fy0, max(12, fx1 - fx0), max(12, fy1 - fy0))
        else:
            face_box = (hx + int(hw * 0.22), hy + int(hh * 0.30), max(12, int(hw * 0.56)), max(12, int(hh * 0.56)))
    else:
        skin_rgb = [242, 212, 194]
        face_box = (hx + int(hw * 0.22), hy + int(hh * 0.30), max(12, int(hw * 0.56)), max(12, int(hh * 0.56)))

    return skin_rgb, line_rgb, face_box


def _norm_box(x: float, y: float, bw: float, bh: float, width: int, height: int) -> list[float]:
    nx = float(np.clip(x / width, 0.0, 0.99))
    ny = float(np.clip(y / height, 0.0, 0.99))
    nw = float(np.clip(bw / width, 0.008, 1.0 - nx))
    nh = float(np.clip(bh / height, 0.006, 1.0 - ny))
    return [round(nx, 5), round(ny, 5), round(nw, 5), round(nh, 5)]


def _classify_view_angle(bw: float, bh: float, nose_offset: float) -> str:
    """'side' | 'three_quarter' | 'front' from body width/height ratio and nose offset in the head box."""
    ratio = float(bw) / max(1.0, float(bh))
    off = abs(float(nose_offset))
    if ratio < 0.34 or off > 0.22:
        return 'side'
    if off >= 0.06:
        return 'three_quarter'
    return 'front'


def resolve_slot_view_angle(slot: str | None, classified: str) -> str:
    """Master is always three-quarter so a standing front rig does not use the side gait.

    The side slot stays side. Any other caller keeps the silhouette heuristic.
    """
    if slot == 'master':
        return 'three_quarter'
    if slot == 'side':
        return 'side'
    return classified if classified in ('side', 'three_quarter', 'front') else 'front'


def _analyze_from_rtmlib(
    rgba: np.ndarray,
    bbox: tuple[int, int, int, int],
    kps: np.ndarray,
    scores: np.ndarray,
) -> dict | None:
    h, w = rgba.shape[:2]
    bx, by, bw, bh = bbox
    body_sc = scores[:17]

    # Need confident head + shoulders at minimum
    if float(body_sc[5]) < 0.30 or float(body_sc[6]) < 0.30 or float(body_sc[:5].max()) < 0.30:
        return None

    # Sort left/right body chains by screen X so _l is always screen-left (smaller X)
    # and _r is always screen-right (larger X), matching silhouette geometry & Remotion.
    if float(kps[5, 0]) <= float(kps[6, 0]):
        sh_l, el_l, wr_l, hip_l, kn_l, an_l = kps[5], kps[7], kps[9], kps[11], kps[13], kps[15]
        sh_r, el_r, wr_r, hip_r, kn_r, an_r = kps[6], kps[8], kps[10], kps[12], kps[14], kps[16]
        arm_sc_ok = min(float(body_sc[7]), float(body_sc[8]), float(body_sc[9]), float(body_sc[10])) >= 0.25
        leg_sc_ok = min(float(body_sc[11]), float(body_sc[12]), float(body_sc[13]), float(body_sc[14]), float(body_sc[15]), float(body_sc[16])) >= 0.28
    else:
        sh_l, el_l, wr_l, hip_l, kn_l, an_l = kps[6], kps[8], kps[10], kps[12], kps[14], kps[16]
        sh_r, el_r, wr_r, hip_r, kn_r, an_r = kps[5], kps[7], kps[9], kps[11], kps[13], kps[15]
        arm_sc_ok = min(float(body_sc[7]), float(body_sc[8]), float(body_sc[9]), float(body_sc[10])) >= 0.25
        leg_sc_ok = min(float(body_sc[11]), float(body_sc[12]), float(body_sc[13]), float(body_sc[14]), float(body_sc[15]), float(body_sc[16])) >= 0.28

    if not arm_sc_ok:
        return None

    sh_mid = 0.5 * (sh_l + sh_r)
    neck = np.array([sh_mid[0], sh_mid[1] - max(8.0, bh * 0.028)], dtype=np.float32)

    # Determine facing from nose vs shoulder/ear midpoint
    nose = kps[0]
    native_facing = 'right' if float(nose[0]) >= float(sh_mid[0]) - bw * 0.01 else 'left'

    head_bottom = int(min(h - 1, sh_mid[1] - bh * 0.01))
    head_mask = rgba[by:max(by + 10, head_bottom), bx:bx + bw, 3] >= 48
    if int(head_mask.sum()) > 20:
        hys, hxs = np.where(head_mask)
        head_box = (bx + int(hxs.min()), by + int(hys.min()),
                    max(16, int(hxs.max() - hxs.min())), max(16, int(hys.max() - hys.min())))
    else:
        head_box = (int(sh_mid[0] - bw * 0.22), by, int(bw * 0.44), max(20, head_bottom - by))

    skin_rgb, line_rgb, face_box = _sample_skin_and_line(rgba, head_box)
    fx, fy, fw, fh = face_box

    # Extract eyes & mouth from 68-pt face landmarks (indices 23..90) when confident
    if len(kps) >= 91 and float(scores[59:91].mean()) >= 0.30:
        eye_a = kps[59:65]
        eye_b = kps[65:71]
        if float(eye_a[:, 0].mean()) <= float(eye_b[:, 0].mean()):
            le_pts, re_pts = eye_a, eye_b
        else:
            le_pts, re_pts = eye_b, eye_a

        def _refine_anime_eye_box(pts: np.ndarray, skin_col: list[int]) -> list[float]:
            x0, y0 = float(pts[:, 0].min()), float(pts[:, 1].min())
            x1, y1 = float(pts[:, 0].max()), float(pts[:, 1].max())
            lm_w = max(8.0, x1 - x0)
            pw = min(fw * 0.28, max(fw * 0.19, lm_w * 1.25))
            ph = min(fh * 0.24, max(fh * 0.15, (y1 - y0) * 1.65, pw * 0.72))
            cx, cy = 0.5 * (x0 + x1), 0.5 * (y0 + y1)
            # Search tightly within the eye width for bright white sclera (R>220,G>220,B>220) + iris, excluding hair bangs!
            sx0 = max(0, int(round(cx - pw * 0.45)))
            sx1 = min(w, int(round(cx + pw * 0.45)) + 1)
            sy0 = max(0, int(round(cy - pw * 0.48)))
            sy1 = min(h, int(round(cy + pw * 0.65)) + 1)
            if sx1 > sx0 + 4 and sy1 > sy0 + 4:
                win = rgba[sy0:sy1, sx0:sx1]
                w_rgb = win[:, :, :3].astype(np.float32)
                sclera = (win[:, :, 3] >= 200) & (w_rgb[:, :, 0] > 218.0) & (w_rgb[:, :, 1] > 218.0) & (w_rgb[:, :, 2] > 218.0)
                eys, _ = np.where(sclera)
                if len(eys) >= 4:
                    ny0 = min(cy - ph * 0.48, sy0 + float(eys.min()) - 2.0)
                    ny1 = max(cy + ph * 0.48, sy0 + float(eys.max()) + 2.5)
                    final_h = min(fh * 0.26, ny1 - ny0)
                    return _norm_box(cx - pw * 0.5, ny0, pw, final_h, w, h)
            return _norm_box(cx - pw * 0.5, cy - ph * 0.5, pw, ph, w, h)

        def _pad_pts(pts: np.ndarray, px_ratio: float, py_ratio: float, min_w: float, min_h: float) -> list[float]:
            x0, y0 = float(pts[:, 0].min()), float(pts[:, 1].min())
            x1, y1 = float(pts[:, 0].max()), float(pts[:, 1].max())
            pw = max(min_w, (x1 - x0) * (1.0 + px_ratio))
            ph = max(min_h, (y1 - y0) * (1.0 + py_ratio))
            cx, cy = 0.5 * (x0 + x1), 0.5 * (y0 + y1)
            return _norm_box(cx - pw * 0.5, cy - ph * 0.5, pw, ph, w, h)

        m_pts = kps[71:91]
        mouth = _pad_pts(m_pts, 0.36, 0.55, fw * 0.15, fh * 0.075)

        # Refine skin_rgb directly from the bright lit cheek/nose bridge between eyes and mouth FIRST
        cheek_x0 = max(0, int(round(float(le_pts[:, 0].min()))))
        cheek_x1 = min(w, int(round(float(re_pts[:, 0].max()))) + 1)
        cheek_y0 = max(0, int(round(max(float(le_pts[:, 1].max()), float(re_pts[:, 1].max())) + 3)))
        cheek_y1 = min(h, int(round(float(m_pts[:, 1].min()) - 1)))
        if cheek_x1 > cheek_x0 + 6 and cheek_y1 > cheek_y0 + 4:
            cheek_crop = rgba[cheek_y0:cheek_y1, cheek_x0:cheek_x1]
            c_rgb = cheek_crop[:, :, :3].astype(np.float32)
            c_lum = 0.299 * c_rgb[:, :, 0] + 0.587 * c_rgb[:, :, 1] + 0.114 * c_rgb[:, :, 2]
            c_valid = (cheek_crop[:, :, 3] >= 200) & (c_lum >= 135.0) & (c_rgb[:, :, 0] >= c_rgb[:, :, 2])
            if int(c_valid.sum()) >= 12:
                lit_thresh = float(np.percentile(c_lum[c_valid], 55))
                c_lit = c_valid & (c_lum >= lit_thresh)
                sample_pool = c_rgb[c_lit] if int(c_lit.sum()) >= 6 else c_rgb[c_valid]
                c_med = np.median(sample_pool, axis=0)
                skin_rgb = [int(np.clip(round(v), 120, 253)) for v in c_med]

        left_eye = _refine_anime_eye_box(le_pts, skin_rgb)
        right_eye = _refine_anime_eye_box(re_pts, skin_rgb)
    else:
        shift = fw * (0.03 if native_facing == 'right' else -0.03)
        left_eye = _norm_box(fx + fw * 0.15 + shift, fy + fh * 0.33, fw * 0.23, fh * 0.15, w, h)
        right_eye = _norm_box(fx + fw * 0.58 + shift, fy + fh * 0.33, fw * 0.23, fh * 0.15, w, h)
        mouth = _norm_box(fx + fw * 0.37 + shift, fy + fh * 0.69, fw * 0.23, fh * 0.11, w, h)

    full_ok = leg_sc_ok and bh >= h * 0.56 and (float(an_l[1]) > float(hip_l[1]) + bh * 0.16)
    level = 'full' if full_ok else 'upper'
    hip_mid = 0.5 * (hip_l + hip_r) if full_ok else np.array([sh_mid[0], by + bh * 0.54], dtype=np.float32)

    joints = {
        'neck': neck,
        'sh_l': sh_l, 'el_l': el_l, 'wr_l': wr_l,
        'sh_r': sh_r, 'el_r': el_r, 'wr_r': wr_r,
        'hip_mid': hip_mid,
    }
    if full_ok:
        joints.update({
            'hip_l': hip_l, 'kn_l': kn_l, 'an_l': an_l,
            'hip_r': hip_r, 'kn_r': kn_r, 'an_r': an_r,
        })

    return {
        'level': level,
        'joints': joints,
        'native_facing': native_facing,
        'view_angle': _classify_view_angle(
            bw, bh, (float(nose[0]) - (head_box[0] + head_box[2] * 0.5)) / max(1.0, float(head_box[2]))
        ),
        'face': {
            'left_eye': left_eye,
            'right_eye': right_eye,
            'mouth': mouth,
            'skin_rgb': skin_rgb,
            'line_rgb': line_rgb,
        },
    }


def _analyze_silhouette(rgba: np.ndarray, bbox: tuple[int, int, int, int]) -> dict:
    """Deterministic silhouette-geometry rigger when rtmlib is absent or low-confidence."""
    h, w = rgba.shape[:2]
    bx, by, bw, bh = bbox
    mask = (rgba[:, :, 3] >= 48).astype(np.uint8)

    # Very compact/flat blobs fall back to breathing sprite
    if bh < h * 0.36 or (bw / max(1, bh)) > 0.92:
        head_box = (bx + int(bw * 0.2), by, max(16, int(bw * 0.6)), max(16, int(bh * 0.35)))
        skin_rgb, line_rgb, face_box = _sample_skin_and_line(rgba, head_box)
        fx, fy, fw, fh = face_box
        return {
            'level': 'sprite',
            'joints': {'hip_mid': np.array([bx + bw * 0.5, by + bh], dtype=np.float32)},
            'native_facing': 'right',
            'face': {
                'left_eye': _norm_box(fx + fw * 0.16, fy + fh * 0.34, fw * 0.22, fh * 0.15, w, h),
                'right_eye': _norm_box(fx + fw * 0.58, fy + fh * 0.34, fw * 0.22, fh * 0.15, w, h),
                'mouth': _norm_box(fx + fw * 0.38, fy + fh * 0.69, fw * 0.22, fh * 0.11, w, h),
                'skin_rgb': skin_rgb,
                'line_rgb': line_rgb,
            },
        }

    # 1. Locate neck row (minimum horizontal span between 11% and 21% of bh)
    y_neck_lo = by + max(4, int(bh * 0.11))
    y_neck_hi = by + max(8, int(bh * 0.21))
    row_widths = []
    for y in range(y_neck_lo, min(h, y_neck_hi + 1)):
        xs = np.where(mask[y, bx:bx + bw] > 0)[0]
        span = int(xs[-1] - xs[0] + 1) if len(xs) > 0 else bw
        row_widths.append((span, y))
    neck_y = float(min(row_widths, key=lambda item: item[0])[1]) if row_widths else float(by + bh * 0.16)
    neck_xs = np.where(mask[int(neck_y), :] > 0)[0]
    neck_x = float(np.mean(neck_xs)) if len(neck_xs) > 0 else float(bx + bw * 0.5)

    # Head box from by..neck_y
    head_slice = mask[by:max(by + 8, int(neck_y)), bx:bx + bw]
    hys, hxs = np.where(head_slice > 0)
    if len(hxs) > 10:
        head_box = (bx + int(hxs.min()), by + int(hys.min()),
                    max(16, int(hxs.max() - hxs.min())), max(16, int(hys.max() - hys.min())))
    else:
        head_box = (int(neck_x - bw * 0.22), by, max(16, int(bw * 0.44)), max(16, int(neck_y - by)))

    skin_rgb, line_rgb, face_box = _sample_skin_and_line(rgba, head_box)
    fx, fy, fw, fh = face_box
    head_cx = head_box[0] + head_box[2] * 0.5
    face_cx = fx + fw * 0.5
    native_facing = 'left' if face_cx < head_cx - head_box[2] * 0.04 else 'right'
    view_angle = _classify_view_angle(bw, bh, (face_cx - head_cx) / max(1.0, float(head_box[2])))

    shift = fw * (0.025 if native_facing == 'right' else -0.025)
    left_eye = _norm_box(fx + fw * 0.15 + shift, fy + fh * 0.34, fw * 0.23, fh * 0.15, w, h)
    right_eye = _norm_box(fx + fw * 0.58 + shift, fy + fh * 0.34, fw * 0.23, fh * 0.15, w, h)
    mouth = _norm_box(fx + fw * 0.37 + shift, fy + fh * 0.69, fw * 0.23, fh * 0.11, w, h)

    # 2. Estimate torso core & shoulders/arms in upper body [20%..52% of bh]
    sh_y = float(min(h - 4, max(neck_y + bh * 0.035, by + bh * 0.205)))
    waist_y = float(by + bh * 0.48)
    chest_y0 = int(by + bh * 0.22)
    chest_y1 = int(by + bh * 0.46)
    col_density = mask[chest_y0:max(chest_y0 + 4, chest_y1), :].mean(axis=0)
    core_cols = np.where(col_density >= 0.62)[0]
    if len(core_cols) >= 8:
        torso_x0 = float(np.percentile(core_cols, 8))
        torso_x1 = float(np.percentile(core_cols, 92))
    else:
        torso_x0 = neck_x - bw * 0.18
        torso_x1 = neck_x + bw * 0.18
    torso_half = max(bw * 0.12, min(bw * 0.26, 0.5 * (torso_x1 - torso_x0)))
    center_x = 0.5 * (torso_x0 + torso_x1)

    sh_l = np.array([center_x - torso_half * 0.88, sh_y], dtype=np.float32)
    sh_r = np.array([center_x + torso_half * 0.88, sh_y], dtype=np.float32)

    # Trace outer left and right arm extremities in [24%..54% bh]
    arm_band_y0 = int(by + bh * 0.25)
    arm_band_y1 = int(by + bh * 0.54)
    left_pts = []
    right_pts = []
    for y in range(arm_band_y0, min(h, arm_band_y1), 2):
        xs = np.where(mask[y, :] > 0)[0]
        if len(xs) == 0:
            continue
        if xs[0] < sh_l[0] - 3:
            left_pts.append((float(xs[0]), float(y)))
        if xs[-1] > sh_r[0] + 3:
            right_pts.append((float(xs[-1]), float(y)))

    if left_pts:
        # Wrist is near the outermost/lowest point of the left arm silhouette
        wr_l_pt = max(left_pts, key=lambda p: (sh_l[0] - p[0]) * 0.65 + (p[1] - sh_y) * 0.55)
        wr_l = np.array([wr_l_pt[0] + max(4.0, bw * 0.03), wr_l_pt[1]], dtype=np.float32)
    else:
        wr_l = np.array([max(bx + 6.0, sh_l[0] - bw * 0.14), by + bh * 0.47], dtype=np.float32)

    if right_pts:
        wr_r_pt = max(right_pts, key=lambda p: (p[0] - sh_r[0]) * 0.65 + (p[1] - sh_y) * 0.55)
        wr_r = np.array([wr_r_pt[0] - max(4.0, bw * 0.03), wr_r_pt[1]], dtype=np.float32)
    else:
        wr_r = np.array([min(bx + bw - 6.0, sh_r[0] + bw * 0.14), by + bh * 0.47], dtype=np.float32)

    el_l = 0.52 * sh_l + 0.48 * wr_l
    el_r = 0.52 * sh_r + 0.48 * wr_r

    # 3. Check lower body for full legs vs upper-body fallback
    is_full = bh >= h * 0.56 and (bw / max(1, bh)) <= 0.80
    lower_y0 = int(by + bh * 0.64)
    lower_y1 = int(by + bh * 0.96)
    lower_slice = mask[lower_y0:max(lower_y0 + 4, lower_y1), :]
    if is_full and int(lower_slice.sum()) > 40:
        hip_y = float(by + bh * 0.53)
        knee_y = float(by + bh * 0.75)
        ankle_y = float(by + bh * 0.95)
        mid_col = int(np.clip(round(center_x), 1, w - 2))

        left_leg_xs = np.where(lower_slice[:, :mid_col] > 0)[1]
        right_leg_xs = np.where(lower_slice[:, mid_col:] > 0)[1] + mid_col
        if len(left_leg_xs) >= 12 and len(right_leg_xs) >= 12:
            lx_hip = float(np.median(left_leg_xs))
            rx_hip = float(np.median(right_leg_xs))
            # Refine knee and ankle X centers per band
            kn_slice = mask[int(by + bh * 0.71):max(int(by + bh * 0.71) + 4, int(by + bh * 0.79)), :]
            an_slice = mask[int(by + bh * 0.90):max(int(by + bh * 0.90) + 4, int(by + bh * 0.98)), :]

            def _band_cx(sl: np.ndarray, lo: int, hi: int, default: float) -> float:
                xs = np.where(sl[:, lo:hi] > 0)[1]
                return float(np.median(xs) + lo) if len(xs) >= 4 else default

            hip_l = np.array([0.45 * (center_x - torso_half * 0.48) + 0.55 * lx_hip, hip_y], dtype=np.float32)
            hip_r = np.array([0.45 * (center_x + torso_half * 0.48) + 0.55 * rx_hip, hip_y], dtype=np.float32)
            kn_l = np.array([_band_cx(kn_slice, 0, mid_col, lx_hip), knee_y], dtype=np.float32)
            kn_r = np.array([_band_cx(kn_slice, mid_col, w, rx_hip), knee_y], dtype=np.float32)
            an_l = np.array([_band_cx(an_slice, 0, mid_col, float(kn_l[0])), ankle_y], dtype=np.float32)
            an_r = np.array([_band_cx(an_slice, mid_col, w, float(kn_r[0])), ankle_y], dtype=np.float32)
            hip_mid = 0.5 * (hip_l + hip_r)
            return {
                'level': 'full',
                'joints': {
                    'neck': np.array([neck_x, neck_y], dtype=np.float32),
                    'sh_l': sh_l, 'el_l': el_l, 'wr_l': wr_l,
                    'sh_r': sh_r, 'el_r': el_r, 'wr_r': wr_r,
                    'hip_mid': hip_mid,
                    'hip_l': hip_l, 'kn_l': kn_l, 'an_l': an_l,
                    'hip_r': hip_r, 'kn_r': kn_r, 'an_r': an_r,
                },
                'native_facing': native_facing,
                'view_angle': view_angle,
                'face': {
                    'left_eye': left_eye,
                    'right_eye': right_eye,
                    'mouth': mouth,
                    'skin_rgb': skin_rgb,
                    'line_rgb': line_rgb,
                },
            }

    return {
        'level': 'upper',
        'joints': {
            'neck': np.array([neck_x, neck_y], dtype=np.float32),
            'sh_l': sh_l, 'el_l': el_l, 'wr_l': wr_l,
            'sh_r': sh_r, 'el_r': el_r, 'wr_r': wr_r,
            'hip_mid': np.array([center_x, waist_y], dtype=np.float32),
        },
        'native_facing': native_facing,
        'view_angle': view_angle,
        'face': {
            'left_eye': left_eye,
            'right_eye': right_eye,
            'mouth': mouth,
            'skin_rgb': skin_rgb,
            'line_rgb': line_rgb,
        },
    }


def _segment_distance_sq(px: np.ndarray, py: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Squared distance from grid (px, py) to line segment a -> b."""
    abx = float(b[0] - a[0])
    aby = float(b[1] - a[1])
    denom = abx * abx + aby * aby
    if denom < 1e-4:
        return (px - float(a[0])) ** 2 + (py - float(a[1])) ** 2
    t = np.clip(((px - float(a[0])) * abx + (py - float(a[1])) * aby) / denom, 0.0, 1.0)
    proj_x = float(a[0]) + t * abx
    proj_y = float(a[1]) + t * aby
    return (px - proj_x) ** 2 + (py - proj_y) ** 2


def _soft_alpha(binary_mask: np.ndarray, master_alpha: np.ndarray) -> np.ndarray:
    # Smooth 1px anti-aliased interior cut boundaries while keeping the outer master silhouette crisp
    smooth_mask = cv2.GaussianBlur((binary_mask > 0).astype(np.float32) * 255.0, (3, 3), 0.55)
    return np.minimum(master_alpha.astype(np.float32), smooth_mask).round().astype(np.uint8)


def _circle_mask(shape: tuple[int, int], pt: np.ndarray, radius: float) -> np.ndarray:
    m = np.zeros(shape, dtype=np.uint8)
    cv2.circle(m, (int(round(float(pt[0]))), int(round(float(pt[1])))), max(3, int(round(radius))), 1, -1, lineType=cv2.LINE_AA)
    return m


def _split_articulated_foot(
    lower_mask: np.ndarray,
    ankle: np.ndarray,
    character_height: int,
    knee_radius: float,
) -> tuple[np.ndarray, np.ndarray | None]:
    """Conservatively split a shoe below an ankle, or retain the intact lower leg.

    Anime boots often have soft shading rather than a strong color seam, so the
    alpha silhouette is a safer cut criterion.  A split is accepted only when
    the below-ankle region is one substantial, ankle-connected component.
    Both resulting parts receive a small round overlap cap to prevent a torn
    seam during ankle pitch.
    """
    binary = (lower_mask > 0).astype(np.uint8)
    h, w = binary.shape
    ax = int(np.clip(round(float(ankle[0])), 0, w - 1))
    ay = int(np.clip(round(float(ankle[1])), 0, h - 1))
    split_y = min(h - 1, ay + 1)

    below = np.zeros_like(binary)
    below[split_y:, :] = binary[split_y:, :]
    below_pixels = int(below.sum())
    if below_pixels < 180:
        return binary, None

    count, labels, stats, _ = cv2.connectedComponentsWithStats(below, 8)
    if count <= 1:
        return binary, None
    component_areas = stats[1:, cv2.CC_STAT_AREA]
    largest_idx = 1 + int(np.argmax(component_areas))
    largest_area = int(stats[largest_idx, cv2.CC_STAT_AREA])
    second_area = int(np.partition(component_areas, -2)[-2]) if len(component_areas) > 1 else 0
    sx = int(stats[largest_idx, cv2.CC_STAT_LEFT])
    sy = int(stats[largest_idx, cv2.CC_STAT_TOP])
    sw = int(stats[largest_idx, cv2.CC_STAT_WIDTH])
    sh = int(stats[largest_idx, cv2.CC_STAT_HEIGHT])

    min_span = max(5, int(round(character_height * 0.010)))
    ankle_link = max(6, int(round(character_height * 0.016)))
    clean_component = (
        largest_area >= 180
        and largest_area >= int(below_pixels * 0.82)
        and second_area <= int(largest_area * 0.18)
        and sw >= max(7, min_span)
        and sh >= min_span
        and sy <= split_y + ankle_link
        and (sy + sh - 1) >= ay + min_span
    )
    if not clean_component:
        return binary, None

    # Keep tiny antialiased/toe islands only when they sit close to the main shoe.
    shoe_core = labels == largest_idx
    near_main = cv2.dilate(
        shoe_core.astype(np.uint8),
        np.ones((5, 5), dtype=np.uint8),
        iterations=2,
    ) > 0
    shoe_core |= (below > 0) & near_main

    ankle_radius = max(6.0, min(knee_radius * 0.62, character_height * 0.020))
    ankle_cap = _circle_mask((h, w), np.array([ax, ay], dtype=np.float32), ankle_radius) & binary
    y_grid = np.arange(h, dtype=np.int32).reshape(-1, 1)
    shin_core = binary & (y_grid <= ay + 1).astype(np.uint8)
    shin_mask = shin_core | ankle_cap
    foot_mask = shoe_core.astype(np.uint8) | ankle_cap

    # The cap itself must connect to enough real shoe pixels; otherwise the
    # detector likely placed the ankle below (or beside) the actual shoe seam.
    cap_contact = int((ankle_cap & shoe_core.astype(np.uint8)).sum())
    if cap_contact < max(12, int(ankle_radius * 0.7)):
        return binary, None
    return shin_mask.astype(np.uint8), foot_mask.astype(np.uint8)


def _cut_and_save_parts(
    rgba: np.ndarray,
    bbox: tuple[int, int, int, int],
    analysis: dict,
    out_dir: Path,
) -> dict[str, dict]:
    """Cut full-canvas RGBA part PNGs with circular joint overlap caps & full-res torso inpaint."""
    h, w = rgba.shape[:2]
    bx, by, bw, bh = bbox
    alpha = rgba[:, :, 3]
    fg = alpha >= 16
    level = analysis['level']
    joints = analysis['joints']
    out_dir.mkdir(parents=True, exist_ok=True)

    if level == 'sprite':
        body_path = out_dir / 'body.png'
        Image.fromarray(rgba, 'RGBA').save(body_path, optimize=True)
        hip_mid = joints['hip_mid']
        return {
            'body': {
                'file': str(body_path),
                'pivot': [round(float(hip_mid[0]) / w, 5), round(float(hip_mid[1]) / h, 5)],
            }
        }

    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    neck = joints['neck']
    sh_l, el_l, wr_l = joints['sh_l'], joints['el_l'], joints['wr_l']
    sh_r, el_r, wr_r = joints['sh_r'], joints['el_r'], joints['wr_r']
    hip_mid = joints['hip_mid']

    r_neck = max(13.0, bw * 0.088)
    r_shoulder = max(21.0, bw * 0.138)
    r_elbow = max(15.0, bw * 0.098)
    r_hip = max(22.0, bw * 0.136)
    r_knee = max(16.0, bw * 0.102)

    # Extend wrist segments past hands so fingers stay attached to lower_arm
    ext_l = wr_l + 0.48 * (wr_l - el_l)
    ext_r = wr_r + 0.48 * (wr_r - el_r)

    # Head region: above sloped neck curve, plus round neck cap
    sh_span = max(24.0, float(sh_r[0] - sh_l[0]))
    neck_curve = float(neck[1]) + 0.06 * ((xx - float(neck[0])) ** 2) / max(16.0, 0.25 * sh_span)
    head_raw = (yy <= neck_curve) & (np.abs(xx - float(neck[0])) <= sh_span * 0.95)

    # Compute distance fields for arms, torso core, and (if full) legs
    torso_top = sh_l * 0.5 + sh_r * 0.5
    torso_bot = hip_mid
    # Give torso core wider weight along chest/pelvis centerline
    d_torso = _segment_distance_sq(xx, yy, torso_top, torso_bot) * 0.52

    d_ual = _segment_distance_sq(xx, yy, sh_l, el_l)
    d_lal = _segment_distance_sq(xx, yy, el_l, ext_l)
    d_uar = _segment_distance_sq(xx, yy, sh_r, el_r)
    d_lar = _segment_distance_sq(xx, yy, el_r, ext_r)

    # Detect exact jacket/vest-to-sleeve color seam on left and right sides so neither torso nor sleeves steal each other!
    rgb_f = rgba[:, :, :3].astype(np.float32)
    seam_y0 = max(0, int(round(float(sh_l[1]) + r_shoulder * 0.35)))
    seam_y1 = min(h, int(round(float(hip_mid[1]) - r_hip * 0.25)))
    cx_int = int(np.clip(round(0.5 * float(sh_l[0] + sh_r[0])), 8, w - 9))
    chest_sample = rgba[seam_y0:max(seam_y0 + 8, seam_y1), max(0, cx_int - int(bw * 0.09)):min(w, cx_int + int(bw * 0.09))]
    chest_fg = chest_sample[:, :, 3] >= 180
    if int(chest_fg.sum()) >= 30:
        chest_med_col = np.median(chest_sample[:, :, :3][chest_fg], axis=0).astype(np.float32)
    else:
        chest_med_col = np.array([60.0, 105.0, 210.0], dtype=np.float32)

    # Scan outward from chest center per row to trace the exact vest/jacket-to-sleeve ink/color seam curve!
    seam_band_y0 = max(0, int(round(float(min(sh_l[1], sh_r[1])) - r_shoulder * 0.6)))
    seam_band_y1 = min(h, int(round(float(hip_mid[1]) + r_hip * 0.25)))
    raw_left_ys: list[int] = []
    raw_left_xs: list[float] = []
    raw_right_ys: list[int] = []
    raw_right_xs: list[float] = []

    for y_scan in range(seam_band_y0, max(seam_band_y0 + 1, seam_band_y1), 2):
        row_a = alpha[y_scan]
        # Reference local torso color just inside the chest on this row (handles multi-band vests with gray/orange/blue stripes!)
        ref_l_slice = rgb_f[y_scan, max(0, cx_int - int(bw * 0.12)):max(1, cx_int - int(bw * 0.04))]
        ref_r_slice = rgb_f[y_scan, min(w - 1, cx_int + int(bw * 0.04)):min(w, cx_int + int(bw * 0.12))]
        row_ref_l = np.median(ref_l_slice, axis=0) if len(ref_l_slice) >= 3 else chest_med_col
        row_ref_r = np.median(ref_r_slice, axis=0) if len(ref_r_slice) >= 3 else chest_med_col

        x_min_scan = max(2, int(round(float(sh_l[0]) - r_shoulder * 0.65)))
        x_start_l = max(x_min_scan + 3, int(round(cx_int - bw * 0.12)))
        for x_s in range(x_start_l, x_min_scan, -1):
            if row_a[x_s] < 120:
                raw_left_ys.append(y_scan)
                raw_left_xs.append(float(x_s + 1))
                break
            lum_here = 0.299 * rgb_f[y_scan, x_s, 0] + 0.587 * rgb_f[y_scan, x_s, 1] + 0.114 * rgb_f[y_scan, x_s, 2]
            lum_in = 0.299 * rgb_f[y_scan, min(w - 1, x_s + 4), 0] + 0.587 * rgb_f[y_scan, min(w - 1, x_s + 4), 1] + 0.114 * rgb_f[y_scan, min(w - 1, x_s + 4), 2]
            col_step = float(np.linalg.norm(rgb_f[y_scan, x_s] - rgb_f[y_scan, min(w - 1, x_s + 4)]))
            outer_diff = float(np.linalg.norm(rgb_f[y_scan, max(0, x_s - 4)] - row_ref_l))
            if (col_step > 42.0 or (lum_in - lum_here) > 38.0) and outer_diff > 45.0:
                raw_left_ys.append(y_scan)
                raw_left_xs.append(float(x_s))
                break

        x_max_scan = min(w - 3, int(round(float(sh_r[0]) + r_shoulder * 0.65)))
        x_start_r = min(x_max_scan - 3, int(round(cx_int + bw * 0.12)))
        for x_s in range(x_start_r, x_max_scan):
            if row_a[x_s] < 120:
                raw_right_ys.append(y_scan)
                raw_right_xs.append(float(x_s - 1))
                break
            lum_here = 0.299 * rgb_f[y_scan, x_s, 0] + 0.587 * rgb_f[y_scan, x_s, 1] + 0.114 * rgb_f[y_scan, x_s, 2]
            lum_in = 0.299 * rgb_f[y_scan, max(0, x_s - 4), 0] + 0.587 * rgb_f[y_scan, max(0, x_s - 4), 1] + 0.114 * rgb_f[y_scan, max(0, x_s - 4), 2]
            col_step = float(np.linalg.norm(rgb_f[y_scan, x_s] - rgb_f[y_scan, max(0, x_s - 4)]))
            outer_diff = float(np.linalg.norm(rgb_f[y_scan, min(w - 1, x_s + 4)] - row_ref_r))
            if (col_step > 42.0 or (lum_in - lum_here) > 38.0) and outer_diff > 45.0:
                raw_right_ys.append(y_scan)
                raw_right_xs.append(float(x_s))
                break

    fallback_l = float(sh_l[0])
    fallback_r = float(sh_r[0])
    med_left = float(np.median(raw_left_xs)) if len(raw_left_xs) >= 5 else fallback_l
    med_right = float(np.median(raw_right_xs)) if len(raw_right_xs) >= 5 else fallback_r
    med_left = float(np.clip(med_left, float(sh_l[0]) - r_shoulder * 0.25, cx_int - bw * 0.12))
    med_right = float(np.clip(med_right, cx_int + bw * 0.12, float(sh_r[0]) + r_shoulder * 0.25))

    # Build smooth linear-fit armhole lines (instead of wiggly row-by-row zigzags that follow chest badges!)
    all_ys = np.arange(h, dtype=np.float32)
    if len(raw_left_xs) >= 8:
        arr_ly = np.array(raw_left_ys, dtype=np.float32)
        arr_lx = np.clip(np.array(raw_left_xs, dtype=np.float32), med_left - r_shoulder * 0.25, med_left + r_shoulder * 0.25)
        # Keep only inliers near median to reject chest badges/stripes
        inliers_l = np.abs(arr_lx - med_left) <= max(10.0, r_shoulder * 0.22)
        if int(inliers_l.sum()) >= 5:
            slope_l, intercept_l = np.polyfit(arr_ly[inliers_l], arr_lx[inliers_l], 1)
            slope_l = float(np.clip(slope_l, -0.08, 0.08))
            line_l = slope_l * (all_ys - float(np.median(arr_ly[inliers_l]))) + float(np.median(arr_lx[inliers_l]))
            core_left_curve = np.clip(line_l, med_left - r_shoulder * 0.22, med_left + r_shoulder * 0.22).reshape(-1, 1).astype(np.float32)
        else:
            core_left_curve = np.full((h, 1), med_left, dtype=np.float32)
    else:
        core_left_curve = np.full((h, 1), med_left, dtype=np.float32)

    if len(raw_right_xs) >= 8:
        arr_ry = np.array(raw_right_ys, dtype=np.float32)
        arr_rx = np.clip(np.array(raw_right_xs, dtype=np.float32), med_right - r_shoulder * 0.25, med_right + r_shoulder * 0.25)
        inliers_r = np.abs(arr_rx - med_right) <= max(10.0, r_shoulder * 0.22)
        if int(inliers_r.sum()) >= 5:
            slope_r, intercept_r = np.polyfit(arr_ry[inliers_r], arr_rx[inliers_r], 1)
            slope_r = float(np.clip(slope_r, -0.08, 0.08))
            line_r = slope_r * (all_ys - float(np.median(arr_ry[inliers_r]))) + float(np.median(arr_rx[inliers_r]))
            core_right_curve = np.clip(line_r, med_right - r_shoulder * 0.22, med_right + r_shoulder * 0.22).reshape(-1, 1).astype(np.float32)
        else:
            core_right_curve = np.full((h, 1), med_right, dtype=np.float32)
    else:
        core_right_curve = np.full((h, 1), med_right, dtype=np.float32)

    sh_l_seam_x = float(core_left_curve[int(np.clip(round(float(sh_l[1])), 0, h - 1)), 0])
    sh_r_seam_x = float(core_right_curve[int(np.clip(round(float(sh_r[1])), 0, h - 1)), 0])
    sh_l = np.array([min(float(sh_l[0]), sh_l_seam_x), float(sh_l[1])], dtype=np.float32)
    sh_r = np.array([max(float(sh_r[0]), sh_r_seam_x), float(sh_r[1])], dtype=np.float32)

    chest_guard_l = xx > core_left_curve
    chest_guard_r = xx < core_right_curve
    arm_vertical_guard = yy > float(max(wr_l[1], wr_r[1], hip_mid[1])) + bh * 0.14
    inf = np.float32(1e12)
    d_ual = np.where(chest_guard_l | arm_vertical_guard | head_raw, inf, d_ual)
    d_lal = np.where(chest_guard_l | arm_vertical_guard | head_raw, inf, d_lal)
    d_uar = np.where(chest_guard_r | arm_vertical_guard | head_raw, inf, d_uar)
    d_lar = np.where(chest_guard_r | arm_vertical_guard | head_raw, inf, d_lar)
    # Prevent torso label (0) from claiming outer sleeve pixels outside [core_left_curve, core_right_curve]!
    d_torso = np.where((xx < core_left_curve) | (xx > core_right_curve), inf, d_torso)

    if level == 'full':
        hip_l, kn_l, an_l = joints['hip_l'], joints['kn_l'], joints['an_l']
        hip_r, kn_r, an_r = joints['hip_r'], joints['kn_r'], joints['an_r']
        foot_ext_l = an_l + np.array([0.0, max(18.0, bh * 0.06)], dtype=np.float32)
        foot_ext_r = an_r + np.array([0.0, max(18.0, bh * 0.06)], dtype=np.float32)

        leg_top_guard = yy < float(min(hip_l[1], hip_r[1])) - r_hip * 0.35
        mid_leg_x = 0.5 * float(hip_l[0] + hip_r[0])

        # Strictly separate left leg vs right leg along mid_leg_x so neither leg carries inner pixels of the other leg!
        d_ull = np.where(leg_top_guard | (xx > mid_leg_x + 2.0), inf, _segment_distance_sq(xx, yy, hip_l, kn_l))
        d_lll = np.where(leg_top_guard | (xx > mid_leg_x + 2.0), inf, _segment_distance_sq(xx, yy, kn_l, foot_ext_l))
        d_ulr = np.where(leg_top_guard | (xx < mid_leg_x - 2.0), inf, _segment_distance_sq(xx, yy, hip_r, kn_r))
        d_llr = np.where(leg_top_guard | (xx < mid_leg_x - 2.0), inf, _segment_distance_sq(xx, yy, kn_r, foot_ext_r))
        # Extend torso pelvis bridge down to hip_mid + r_hip*0.95 so crotch/pelvis never splits into a thin V!
        d_torso = np.where(yy > float(hip_mid[1]) + r_hip * 0.95, inf, d_torso)

        stack = np.stack([d_torso, d_ual, d_lal, d_uar, d_lar, d_ull, d_lll, d_ulr, d_llr], axis=0)
        labels = np.argmin(stack, axis=0)
    else:
        # 'upper': lower body stays merged with body
        d_body_lower = _segment_distance_sq(xx, yy, hip_mid, np.array([float(hip_mid[0]), float(by + bh)], dtype=np.float32)) * 0.45
        d_body = np.minimum(d_torso, d_body_lower)
        stack = np.stack([d_body, d_ual, d_lal, d_uar, d_lar], axis=0)
        labels = np.argmin(stack, axis=0)

    fg_u8 = fg.astype(np.uint8)
    cap_neck = _circle_mask((h, w), neck, r_neck) & fg_u8
    cap_sh_l = _circle_mask((h, w), sh_l, r_shoulder) & fg_u8
    cap_el_l = _circle_mask((h, w), el_l, r_elbow) & fg_u8
    cap_sh_r = _circle_mask((h, w), sh_r, r_shoulder) & fg_u8
    cap_el_r = _circle_mask((h, w), el_r, r_elbow) & fg_u8

    m_head = ((head_raw & fg).astype(np.uint8)) | cap_neck
    not_head = (~head_raw).astype(np.uint8) & fg_u8

    # Give upper arms a FULL circular ball-and-socket shoulder cap PLUS a 10px inner sleeve backing strip under the vest edge
    # so rotating an arm outward (-54deg in `point`/`wave` or -3.5deg in `talk`) NEVER exposes a gap between sleeve and vest!
    arm_underlap_l = (
        (yy >= float(sh_l[1]) - r_shoulder * 0.25)
        & (yy <= float(el_l[1]) + r_elbow * 0.45)
        & (xx >= core_left_curve - 4.0)
        & (xx <= core_left_curve + 16.0)
        & (~head_raw)
        & fg
    ).astype(np.uint8)
    arm_underlap_r = (
        (yy >= float(sh_r[1]) - r_shoulder * 0.25)
        & (yy <= float(el_r[1]) + r_elbow * 0.45)
        & (xx >= core_right_curve - 16.0)
        & (xx <= core_right_curve + 4.0)
        & (~head_raw)
        & fg
    ).astype(np.uint8)
    m_ual = (((labels == 1).astype(np.uint8) & not_head) | cap_sh_l | cap_el_l | arm_underlap_l)
    m_lal = (((labels == 2).astype(np.uint8) & not_head) | (cap_el_l & (xx <= core_left_curve + 8.0).astype(np.uint8)))
    m_uar = (((labels == 3).astype(np.uint8) & not_head) | cap_sh_r | cap_el_r | arm_underlap_r)
    m_lar = (((labels == 4).astype(np.uint8) & not_head) | (cap_el_r & (xx >= core_right_curve - 8.0).astype(np.uint8)))

    torso_key = 'torso' if level == 'full' else 'body'
    # Keep torso core solid across chest-to-hip behind arms, bounded cleanly at [core_left_curve, core_right_curve] plus circular shoulder sockets!
    torso_trapezoid = (
        (yy >= float(neck[1]))
        & (yy <= float(hip_mid[1]) + r_hip * 0.55)
        & (xx >= core_left_curve)
        & (xx <= core_right_curve)
        & (~head_raw)
        & fg
    ).astype(np.uint8)
    m_torso_core = ((labels == 0).astype(np.uint8) & not_head) | torso_trapezoid | cap_neck | (cap_sh_l & (xx >= core_left_curve - 3.0).astype(np.uint8)) | (cap_sh_r & (xx <= core_right_curve + 3.0).astype(np.uint8))

    if level == 'full':
        cap_hip_l = _circle_mask((h, w), joints['hip_l'], r_hip) & fg_u8 & (xx <= mid_leg_x + 3.0).astype(np.uint8)
        cap_kn_l = _circle_mask((h, w), joints['kn_l'], r_knee) & fg_u8 & (xx <= mid_leg_x + 3.0).astype(np.uint8)
        cap_hip_r = _circle_mask((h, w), joints['hip_r'], r_hip) & fg_u8 & (xx >= mid_leg_x - 3.0).astype(np.uint8)
        cap_kn_r = _circle_mask((h, w), joints['kn_r'], r_knee) & fg_u8 & (xx >= mid_leg_x - 3.0).astype(np.uint8)
        pelvis_bridge = _circle_mask((h, w), hip_mid + np.array([0.0, r_hip * 0.25], dtype=np.float32), r_hip * 1.15) & fg_u8
        m_torso_core = m_torso_core | cap_hip_l | cap_hip_r | pelvis_bridge
        m_ull = (((labels == 5).astype(np.uint8) & not_head) | cap_hip_l | cap_kn_l)
        m_lll = (((labels == 6).astype(np.uint8) & not_head) | cap_kn_l)
        m_ulr = (((labels == 7).astype(np.uint8) & not_head) | cap_hip_r | cap_kn_r)
        m_llr = (((labels == 8).astype(np.uint8) & not_head) | cap_kn_r)

        # Keep each lower leg intact unless a substantial, clean shoe region
        # exists below the detected ankle.  Accepted splits overlap at a round
        # ankle cap so foot pitch cannot expose transparent cracks.
        m_lll, m_foot_l = _split_articulated_foot(
            m_lll, joints['an_l'], bh, r_knee
        )
        m_llr, m_foot_r = _split_articulated_foot(
            m_llr, joints['an_r'], bh, r_knee
        )

    # Below the cuff a forearm should carry only the hand.  Trouser/hip pixels
    # next to a hanging hand are nearer the forearm segment than any leg bone,
    # but they would swing with the arm as a sliver; hand them back to the leg
    # (or body) on that side.
    hsv = cv2.cvtColor(np.ascontiguousarray(rgba[:, :, :3]), cv2.COLOR_RGB2HSV)
    skin_like = (
        ((hsv[:, :, 0] <= 22) | (hsv[:, :, 0] >= 170))
        & (hsv[:, :, 1] >= 40)
        & (hsv[:, :, 2] >= 110)
    )

    lum_f = 0.299 * rgb_f[:, :, 0] + 0.587 * rgb_f[:, :, 1] + 0.114 * rgb_f[:, :, 2]
    # Skin incl. shaded hand tones (warm hue, mid saturation) - still brighter than ink lines.
    hand_tone = (
        ((hsv[:, :, 0] <= 25) | (hsv[:, :, 0] >= 165))
        & (hsv[:, :, 1] >= 28)
        & (hsv[:, :, 2] >= 70)
        & (lum_f >= 75.0)
    )
    ink_like = lum_f < 85.0
    cross3 = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    ellipse3 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))

    def _stray_below_cuff(arm_mask: np.ndarray, wr_pt: np.ndarray, leg_ref_mask: np.ndarray) -> np.ndarray:
        """Return only disconnected trouser-coloured islands below the cuff.

        The whole hand (skin + shaded tones + its dark ink outline, up to 5px)
        is grown from a skin seed and always stays with the forearm; nothing
        that belongs to the hand component, or touches its outline, is removed.
        """
        none = np.zeros(arm_mask.shape, dtype=bool)
        region = (arm_mask > 0) & (yy > float(wr_pt[1]) + r_elbow * 0.15)
        skin = (skin_like & region).astype(np.uint8)
        if int(skin.sum()) < 50:
            return none
        skin = cv2.morphologyEx(skin, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
        n_cc, cc, stats, _ = cv2.connectedComponentsWithStats(skin, connectivity=8)
        if n_cc <= 1:
            return none
        best = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        seed = cc == best
        seed_ys, _ = np.where(seed)
        # The cuff hem overlaps the first hand rows; leave those with the arm.
        first_row = int(seed_ys.min()) + max(6, int(round(r_elbow * 0.4)))

        # 1. Grow across contiguous skin + shaded hand tones (stops at internal ink edges lum < 75).
        tone = ((hand_tone & region) | seed).astype(np.uint8)
        n2, cc2 = cv2.connectedComponents(tone, connectivity=8)
        keep_ids = np.unique(cc2[seed])
        keep_ids = keep_ids[keep_ids > 0]
        if len(keep_ids) == 0:
            return none
        hand_core = np.isin(cc2, keep_ids)
        hand_core = cv2.morphologyEx(hand_core.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)) > 0

        # 2. Preserve the surrounding dark ink outline (up to 5px border around the hand component).
        grown = hand_core.copy()
        frontier = hand_core.astype(np.uint8)
        ink_region = ink_like & ((arm_mask > 0) | (fg_u8 > 0))
        for _ in range(5):
            dil = cv2.dilate(frontier, cross3) > 0
            fresh = dil & ink_region & (~grown)
            if not fresh.any():
                break
            grown |= fresh
            frontier = fresh.astype(np.uint8)

        # 3. Only trouser-coloured pixels clearly outside the hand + outline go back to the leg/body.
        guard = cv2.dilate(grown.astype(np.uint8), ellipse3, iterations=2) > 0
        band = leg_ref_mask & (yy >= float(wr_pt[1]) - bh * 0.04) & (yy <= float(wr_pt[1]) + bh * 0.28)
        ref_pool = band if int(band.sum()) >= 40 else leg_ref_mask
        if int(ref_pool.sum()) < 40:
            return none
        leg_ref = np.median(rgb_f[ref_pool], axis=0)
        color_dist_to_leg = np.linalg.norm(rgb_f - leg_ref.reshape(1, 1, 3), axis=2)
        stray = region & (yy > float(first_row)) & (~guard) & (color_dist_to_leg < 26.0)
        return stray if int(stray.sum()) >= 20 else none

    if level == 'full':
        leg_ref_l = ((labels == 5) | (labels == 6)) & (fg_u8 > 0)
        leg_ref_r = ((labels == 7) | (labels == 8)) & (fg_u8 > 0)
    else:
        lower_body = (labels == 0) & (fg_u8 > 0) & (yy > float(hip_mid[1]))
        leg_ref_l = lower_body & (xx <= float(hip_mid[0]))
        leg_ref_r = lower_body & (xx > float(hip_mid[0]))
    stray_l = _stray_below_cuff(m_lal, wr_l, leg_ref_l)
    stray_r = _stray_below_cuff(m_lar, wr_r, leg_ref_r)
    m_lal = (m_lal & (~stray_l).astype(np.uint8)).astype(np.uint8)
    m_lar = (m_lar & (~stray_r).astype(np.uint8)).astype(np.uint8)
    if level == 'full':
        m_ull = m_ull | stray_l.astype(np.uint8)
        m_ulr = m_ulr | stray_r.astype(np.uint8)
    else:
        m_torso_core = m_torso_core | stray_l.astype(np.uint8) | stray_r.astype(np.uint8)

    # Solid flat garment fill (no blurry cv2.inpaint smear!) for any outer sleeve pixels overlapping torso shoulder sockets
    arms_union = ((labels == 1) | (labels == 2) | (labels == 3) | (labels == 4)).astype(np.uint8) & not_head
    inpaint_zone = (arms_union > 0) & (m_torso_core > 0)
    torso_rgb = rgba[:, :, :3].copy()
    pure_chest = (m_torso_core > 0) & (~inpaint_zone) & (yy >= float(neck[1]) + 8.0) & (yy <= float(hip_mid[1]))
    if int(inpaint_zone.sum()) > 0 and int(pure_chest.sum()) > 32:
        # Fill each row of shoulder-socket overlap with the median clean chest color of that row band!
        iz_ys, iz_xs = np.where(inpaint_zone)
        global_chest_med = np.median(rgba[:, :, :3][pure_chest], axis=0).astype(np.uint8)
        for y_val in np.unique(iz_ys):
            band = pure_chest[max(0, y_val - 6):min(h, y_val + 7), :]
            if int(band.sum()) >= 12:
                row_col = np.median(rgba[max(0, y_val - 6):min(h, y_val + 7), :, :3][band], axis=0).astype(np.uint8)
            else:
                row_col = global_chest_med
            row_xs = iz_xs[iz_ys == y_val]
            torso_rgb[y_val, row_xs] = row_col

    # Likewise fill inner shoulder cap + underlap of upper_arm row-by-row from adjacent outer sleeve pixels so color continuity is seamless!
    def _arm_rgb_clean(arm_core_label: int, arm_mask: np.ndarray, sh_pt: np.ndarray, el_pt: np.ndarray) -> np.ndarray:
        out_rgb = rgba[:, :, :3].copy()
        upper_band = (yy >= float(sh_pt[1]) - r_shoulder * 0.4) & (yy <= 0.55 * float(sh_pt[1] + el_pt[1]))
        pure_sleeve = (labels == arm_core_label) & (fg_u8 > 0) & upper_band
        if int(pure_sleeve.sum()) < 16:
            pure_sleeve = (labels == arm_core_label) & (fg_u8 > 0)
        inner_cap_steal = (arm_mask > 0) & (labels != arm_core_label) & (labels != arm_core_label + 1)
        if int(inner_cap_steal.sum()) >= 4 and int(pure_sleeve.sum()) >= 16:
            sleeve_med = np.median(rgba[:, :, :3][pure_sleeve], axis=0).astype(np.uint8)
            st_ys, st_xs = np.where(inner_cap_steal)
            for y_val in np.unique(st_ys):
                row_sl = (labels[max(0, y_val - 4):min(h, y_val + 5), :] == arm_core_label) & (fg_u8[max(0, y_val - 4):min(h, y_val + 5), :] > 0)
                if int(row_sl.sum()) >= 8:
                    r_col = np.median(rgba[max(0, y_val - 4):min(h, y_val + 5), :, :3][row_sl], axis=0).astype(np.uint8)
                else:
                    r_col = sleeve_med
                out_rgb[y_val, st_xs[st_ys == y_val]] = r_col
        return out_rgb

    ual_rgb = _arm_rgb_clean(1, m_ual, sh_l, el_l)
    uar_rgb = _arm_rgb_clean(3, m_uar, sh_r, el_r)
    lal_rgb = _arm_rgb_clean(2, m_lal, el_l, wr_l)
    lar_rgb = _arm_rgb_clean(4, m_lar, el_r, wr_r)

    part_specs: list[tuple[str, np.ndarray, np.ndarray, np.ndarray]] = [
        (torso_key, m_torso_core, hip_mid, torso_rgb),
        ('head', m_head, neck, rgba[:, :, :3]),
        ('upper_arm_l', m_ual, sh_l, ual_rgb),
        ('lower_arm_l', m_lal, el_l, lal_rgb),
        ('upper_arm_r', m_uar, sh_r, uar_rgb),
        ('lower_arm_r', m_lar, el_r, lar_rgb),
    ]
    if level == 'full':
        part_specs.extend([
            ('upper_leg_l', m_ull, joints['hip_l'], rgba[:, :, :3]),
            ('lower_leg_l', m_lll, joints['kn_l'], rgba[:, :, :3]),
            ('upper_leg_r', m_ulr, joints['kn_r'] * 0.0 + joints['hip_r'], rgba[:, :, :3]),
            ('lower_leg_r', m_llr, joints['kn_r'], rgba[:, :, :3]),
        ])
        if m_foot_l is not None:
            part_specs.append(('foot_l', m_foot_l, joints['an_l'], rgba[:, :, :3]))
        if m_foot_r is not None:
            part_specs.append(('foot_r', m_foot_r, joints['an_r'], rgba[:, :, :3]))

    parts_meta: dict[str, dict] = {}
    for name, mask_u8, pivot_pt, custom_rgb in part_specs:
        part_rgba = np.empty_like(rgba)
        part_rgba[:, :, :3] = custom_rgb
        part_rgba[:, :, 3] = _soft_alpha(mask_u8, alpha)
        out_path = out_dir / f'{name}.png'
        Image.fromarray(part_rgba, 'RGBA').save(out_path, optimize=True)
        parts_meta[name] = {
            'file': str(out_path),
            'pivot': [
                round(float(np.clip(pivot_pt[0] / w, 0.0, 1.0)), 5),
                round(float(np.clip(pivot_pt[1] / h, 0.0, 1.0)), 5),
            ],
        }

    return parts_meta


def _cache_valid(rig_json_path: Path) -> bool:
    if not rig_json_path.is_file():
        return False
    try:
        data = json.loads(rig_json_path.read_text(encoding='utf-8'))
        parts = data.get('parts')
        if not isinstance(parts, dict) or not parts:
            return False
        return all(isinstance(p, dict) and p.get('file') and Path(p['file']).is_file() for p in parts.values())
    except Exception:
        return False


def rig_character_master(master_path: Path, cache_root: Path = Path('media/rigs'), slot: str | None = None) -> Path:
    """Rig a single character master image and return the path to cached `rig.json`."""
    cache_hash = hashlib.sha256()
    cache_hash.update(master_path.read_bytes())
    cache_hash.update(b'\0rig-v4-view-slot')
    cache_hash.update((slot or '').encode('utf-8'))
    digest = cache_hash.hexdigest()[:16]
    out_dir = cache_root / f'char-{digest}'
    rig_json_path = out_dir / 'rig.json'
    if _cache_valid(rig_json_path):
        return rig_json_path

    raw = np.asarray(Image.open(master_path).convert('RGBA'))
    rgba = _ensure_rgba_matte(raw)
    h, w = rgba.shape[:2]
    fg_ys, fg_xs = np.where(rgba[:, :, 3] >= 48)
    if len(fg_xs) < 64:
        out_dir.mkdir(parents=True, exist_ok=True)
        body_path = out_dir / 'body.png'
        Image.fromarray(rgba, 'RGBA').save(body_path, optimize=True)
        fallback_rig = {
            'level': 'sprite',
            'width': w,
            'height': h,
            'anchor_x': 0.5,
            'foot_y': 0.95,
            'head_top_y': 0.08,
            'native_facing': 'right',
            'parts': {'body': {'file': str(body_path), 'pivot': [0.5, 0.95]}},
            'face': {
                'left_eye': [0.42, 0.16, 0.06, 0.03],
                'right_eye': [0.52, 0.16, 0.06, 0.03],
                'mouth': [0.46, 0.22, 0.06, 0.025],
                'skin_rgb': [242, 212, 194],
                'line_rgb': [36, 28, 34],
            },
            'view_angle': resolve_slot_view_angle(slot, 'front'),
        }
        rig_json_path.write_text(json.dumps(fallback_rig, ensure_ascii=False, indent=2), encoding='utf-8')
        return rig_json_path

    bx = int(fg_xs.min())
    by = int(fg_ys.min())
    bw = max(1, int(fg_xs.max() - bx + 1))
    bh = max(1, int(fg_ys.max() - by + 1))
    bbox = (bx, by, bw, bh)

    rtm = _try_rtmlib_keypoints(rgba)
    analysis = _analyze_from_rtmlib(rgba, bbox, rtm[0], rtm[1]) if rtm is not None else None
    if analysis is None:
        analysis = _analyze_silhouette(rgba, bbox)

    parts_meta = _cut_and_save_parts(rgba, bbox, analysis, out_dir)
    hip_mid = analysis['joints']['hip_mid']
    anchor_x = round(float(np.clip(hip_mid[0] / w, 0.05, 0.95)), 5)
    head_top_y = round(float(np.clip(by / h, 0.0, 0.85)), 5)
    foot_y = round(float(np.clip((by + bh) / h, head_top_y + 0.1, 1.0)), 5)

    rig_doc = {
        'level': analysis['level'],
        'width': w,
        'height': h,
        'anchor_x': anchor_x,
        'foot_y': foot_y,
        'head_top_y': head_top_y,
        'native_facing': analysis['native_facing'],
        'parts': parts_meta,
        'face': analysis['face'],
        'view_angle': resolve_slot_view_angle(slot, str(analysis.get('view_angle') or 'front')),
    }
    if analysis['level'] != 'sprite':
        wj = analysis['joints']
        rig_doc['wrists'] = {
            'l': [
                round(float(np.clip(wj['wr_l'][0] / w, 0.0, 1.0)), 5),
                round(float(np.clip(wj['wr_l'][1] / h, 0.0, 1.0)), 5),
            ],
            'r': [
                round(float(np.clip(wj['wr_r'][0] / w, 0.0, 1.0)), 5),
                round(float(np.clip(wj['wr_r'][1] / h, 0.0, 1.0)), 5),
            ],
            'radius': 0.024,
        }
    if analysis['level'] == 'full':
        joints = analysis['joints']
        rig_doc['ankles'] = {
            'l': [
                round(float(np.clip(joints['an_l'][0] / w, 0.0, 1.0)), 5),
                round(float(np.clip(joints['an_l'][1] / h, 0.0, 1.0)), 5),
            ],
            'r': [
                round(float(np.clip(joints['an_r'][0] / w, 0.0, 1.0)), 5),
                round(float(np.clip(joints['an_r'][1] / h, 0.0, 1.0)), 5),
            ],
        }
    rig_json_path.write_text(json.dumps(rig_doc, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"rig_characters: {master_path.name} -> {out_dir.name} level={analysis['level']} parts={len(parts_meta)}", flush=True)
    return rig_json_path


def process_manifest(manifest_path: Path, cache_dir: str | None = None) -> None:
    doc = json.loads(manifest_path.read_text(encoding='utf-8'))
    cache_root = Path(cache_dir or os.environ.get('SOUNDBOOK_RIG_CACHE', 'media/rigs'))

    master_to_rig: dict[tuple[Path, str], str] = {}
    id_to_rig: dict[str, str] = {}
    name_to_rig: dict[str, str] = {}
    key_to_rig: dict[str, str] = {}
    id_to_side: dict[str, str] = {}
    name_to_side: dict[str, str] = {}

    def _rig_file(file_str: str, slot: str | None = None) -> str | None:
        p = Path(file_str)
        if not p.is_file():
            return None
        resolved = p.resolve()
        cache_id = (resolved, slot or '')
        if cache_id not in master_to_rig:
            master_to_rig[cache_id] = str(rig_character_master(p, cache_root, slot=slot))
        return master_to_rig[cache_id]

    for entry in doc.get('character_rigs') or []:
        if not isinstance(entry, dict):
            continue
        master = entry.get('master_file') or entry.get('file') or entry.get('rig_file')
        if not master:
            continue
        rig_json = _rig_file(str(master), 'master')
        if not rig_json:
            continue
        entry['rig_json'] = rig_json
        side_master = entry.get('side_master_file') or entry.get('side_rig_file')
        if side_master:
            side_json = _rig_file(str(side_master), 'side')
            if side_json:
                entry['side_rig_json'] = side_json
                if entry.get('id'):
                    id_to_side[str(entry['id'])] = side_json
                if entry.get('name'):
                    name_to_side[str(entry['name'])] = side_json
        if entry.get('id'):
            id_to_rig[str(entry['id'])] = rig_json
        if entry.get('name'):
            name_to_rig[str(entry['name'])] = rig_json
        for k_field in ('master_key', 'rig_key', 'key'):
            if entry.get(k_field):
                key_to_rig[str(entry[k_field])] = rig_json

    for shot in doc.get('shots') or []:
        if not isinstance(shot, dict):
            continue
        sm = shot.get('story_motion')
        if not isinstance(sm, dict):
            continue
        for actor in sm.get('actors') or []:
            if not isinstance(actor, dict):
                continue
            rig_json = None
            if actor.get('rig_file'):
                rig_json = _rig_file(str(actor['rig_file']), 'master')
            if not rig_json and actor.get('rig_key') and str(actor['rig_key']) in key_to_rig:
                rig_json = key_to_rig[str(actor['rig_key'])]
            if not rig_json and actor.get('character_id') and str(actor['character_id']) in id_to_rig:
                rig_json = id_to_rig[str(actor['character_id'])]
            if not rig_json and actor.get('name'):
                actor_name = str(actor['name'])
                rig_json = name_to_rig.get(actor_name)
                if not rig_json:
                    for cname, crig in name_to_rig.items():
                        if cname and (cname in actor_name or actor_name in cname):
                            rig_json = crig
                            break
            if rig_json:
                actor['rig_json'] = rig_json
            side_json = None
            if actor.get('side_rig_file'):
                side_json = _rig_file(str(actor['side_rig_file']), 'side')
            if not side_json and actor.get('character_id'):
                side_json = id_to_side.get(str(actor['character_id']))
            if not side_json and actor.get('name'):
                side_json = name_to_side.get(str(actor['name']))
            if side_json:
                actor['side_rig_json'] = side_json

    manifest_path.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')
    print(f'rig_characters: rigged {len(master_to_rig)} unique character master(s)', flush=True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest')
    parser.add_argument('--cache-dir', default=None, help='rig cache root (default: $SOUNDBOOK_RIG_CACHE or media/rigs)')
    cli = parser.parse_args()
    process_manifest(Path(cli.manifest), cli.cache_dir)
