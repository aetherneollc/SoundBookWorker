#!/usr/bin/env python3
"""Cut a 2×3 expression board into aligned transparent cels.

The talk cel is not swapped in whole. Pixels that differ from the neutral cel
inside the face are copied into ``talk_delta``; Remotion toggles only that
layer, so the hair and hoodie stay still while the mouth opens.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from board_spec import CELL_KEYS


def _snap_gutter(profile: np.ndarray, expected: int, window: int) -> int:
    start = max(1, expected - window)
    end = min(len(profile) - 1, expected + window)
    return int(start + np.argmax(profile[start:end]))


def split_grid(rgb: np.ndarray) -> list[np.ndarray]:
    """Return six panels, cutting on the whitest seam near each expected gutter."""
    height, width = rgb.shape[:2]
    white = np.all(rgb > 210, axis=2)
    col = white.mean(axis=0)
    row = white.mean(axis=1)
    xs = [0,
          _snap_gutter(col, width // 3, width // 18),
          _snap_gutter(col, 2 * width // 3, width // 18),
          width]
    ys = [0, _snap_gutter(row, height // 2, height // 14), height]
    # Drop a bright outer frame so it is not left on the cel.
    if row[:8].mean() > 0.4:
        ys[0] = int(np.argmax(row[:12] < 0.5))
    if row[-8:].mean() > 0.4:
        ys[-1] = height - int(np.argmax(row[::-1][:12] < 0.5))
    print(f"grid cuts x={xs} y={ys}", flush=True)
    panels = []
    for y0, y1 in zip(ys, ys[1:]):
        for x0, x1 in zip(xs, xs[1:]):
            pad = 16
            panels.append(rgb[y0 + pad:max(y0 + pad + 1, y1 - pad), x0 + pad:max(x0 + pad + 1, x1 - pad)])
    if len(panels) != 6:
        raise RuntimeError(f"expected 6 panels, got {len(panels)}")
    return panels


def _matte(rgb: np.ndarray) -> np.ndarray:
    red = rgb[:, :, 0].astype(np.int16)
    green = rgb[:, :, 1].astype(np.int16)
    blue = rgb[:, :, 2].astype(np.int16)
    # Key the flat corner color. Kolors greens out too dark for a fixed threshold,
    # and the white shirt touches the bottom edge, so it must not be flooded.
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]], axis=0)
    key = np.median(border.astype(np.float32), axis=0)
    distance = np.linalg.norm(rgb.astype(np.float32) - key, axis=2)
    is_green = distance < 46
    candidate = np.where(is_green, np.uint8(255), np.uint8(0))
    height, width = candidate.shape
    mask = np.zeros((height + 2, width + 2), np.uint8)
    flags = 4 | cv2.FLOODFILL_MASK_ONLY | (255 << 8)
    seeds = [(x, 0) for x in range(width)] + [(x, height - 1) for x in range(width)]
    seeds += [(0, y) for y in range(height)] + [(width - 1, y) for y in range(height)]
    for x, y in seeds:
        if candidate[y, x] == 255 and mask[y + 1, x + 1] == 0:
            cv2.floodFill(candidate, mask, (int(x), int(y)), 0, flags=flags)
    background = (mask[1:-1, 1:-1] > 0) | is_green
    alpha = np.where(background, np.uint8(0), np.uint8(255))
    alpha = cv2.erode(alpha, np.ones((3, 3), np.uint8), iterations=1)
    alpha = cv2.GaussianBlur(alpha, (3, 3), 0)
    out = cv2.cvtColor(rgb, cv2.COLOR_RGB2RGBA)
    spill = (alpha > 0) & is_green
    # Pull leftover green fringe back toward the stronger of red or blue.
    out[:, :, 1] = np.where(spill, np.maximum(red, blue).astype(np.uint8), out[:, :, 1])
    out[:, :, 3] = alpha
    return out


def _bbox(alpha: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.where(alpha > 24)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def _shift(image: np.ndarray, dx: int, dy: int) -> np.ndarray:
    height, width = image.shape[:2]
    matrix = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(image, matrix, (width, height), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))


def _place(rgba: np.ndarray, canvas: tuple[int, int]) -> np.ndarray:
    box = _bbox(rgba[:, :, 3])
    placed = np.zeros((canvas[1], canvas[0], 4), np.uint8)
    if box is None:
        return placed
    x0, y0, x1, y1 = box
    crop = rgba[y0:y1 + 1, x0:x1 + 1]
    dest_x = (canvas[0] - crop.shape[1]) // 2
    dest_y = canvas[1] - crop.shape[0] - 6
    h, w = crop.shape[:2]
    x_end = min(canvas[0], dest_x + w)
    y_end = min(canvas[1], dest_y + h)
    src_x = max(0, -dest_x)
    src_y = max(0, -dest_y)
    placed[max(0, dest_y):y_end, max(0, dest_x):x_end] = crop[src_y:src_y + (y_end - max(0, dest_y)),
                                                              src_x:src_x + (x_end - max(0, dest_x))]
    return placed


def _best_shift(base: np.ndarray, other: np.ndarray, limit: int = 8) -> tuple[int, int]:
    """Register the other cel to the neutral face before cutting the mouth."""
    box = _bbox(base[:, :, 3])
    if box is None:
        return 0, 0
    x0, y0, x1, y1 = box
    face = base[y0:y0 + int((y1 - y0) * 0.62), x0:x1 + 1]
    best = (0, 0)
    best_score = 1e18
    step = 2
    for dy in range(-limit, limit + 1, step):
        for dx in range(-limit, limit + 1, step):
            shifted = _shift(other, dx, dy)
            sample = shifted[y0:y0 + face.shape[0], x0:x1 + 1]
            both = (face[:, :, 3] > 128) & (sample[:, :, 3] > 128)
            if int(both.sum()) < 40:
                continue
            score = np.abs(face[:, :, :3].astype(np.int16) - sample[:, :, :3].astype(np.int16))
            score = float(score[both].mean())
            if score < best_score:
                best_score = score
                best = (dx, dy)
    return best


def _mouth_delta(neutral: np.ndarray, talk: np.ndarray) -> np.ndarray:
    box = _bbox(neutral[:, :, 3])
    delta = np.zeros_like(neutral)
    if box is None:
        return delta
    x0, y0, x1, y1 = box
    width = x1 - x0 + 1
    height = y1 - y0 + 1
    # Face column only. Hair and shoulders differ a little and must not flap.
    # Same-seed Kolors cels still shift the cheeks. Keep only the hottest
    # blob in the mouth band, which is where an open mouth actually differs.
    rx0 = x0 + int(width * 0.40)
    rx1 = x0 + int(width * 0.60)
    ry0 = y0 + int(height * 0.46)
    ry1 = y0 + int(height * 0.64)
    diff = np.abs(talk[:, :, :3].astype(np.int16) - neutral[:, :, :3].astype(np.int16)).mean(axis=2)
    both = (talk[:, :, 3] > 140) & (neutral[:, :, 3] > 140)
    region = np.zeros(diff.shape, bool)
    region[ry0:ry1, rx0:rx1] = True
    score = np.where(both & region, diff, 0)
    cutoff = max(18, float(np.percentile(score[score > 0], 75)) if np.any(score > 0) else 18)
    changed = score >= cutoff
    count, labels, stats, _ = cv2.connectedComponentsWithStats(changed.astype(np.uint8), 8)
    if count <= 1:
        print("talk delta rejected: no mouth blob", flush=True)
        return delta
    main = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    changed = labels == main
    changed = cv2.dilate(changed.astype(np.uint8), np.ones((5, 5), np.uint8), iterations=1) > 0
    changed &= talk[:, :, 3] > 140
    area = int(changed.sum())
    face_area = max(1, (rx1 - rx0) * (ry1 - ry0))
    if area < 30 or area > face_area * 0.7:
        print(f"talk delta rejected area={area} face={face_area}", flush=True)
        return delta
    delta[changed] = talk[changed]
    print(f"talk delta accepted area={area}", flush=True)
    return delta


def slice_board(board: Path, out_dir: Path, character_id: str) -> dict:
    rgb = np.asarray(Image.open(board).convert("RGB"))
    panels = split_grid(rgb)
    matted = [_matte(panel) for panel in panels]
    boxes = [_bbox(panel[:, :, 3]) for panel in matted]
    if any(box is None for box in boxes):
        raise RuntimeError("a panel matted to nothing; the board background was not flat")
    max_w = max(box[2] - box[0] for box in boxes if box)
    max_h = max(box[3] - box[1] for box in boxes if box)
    canvas = (max_w + 16, max_h + 16)
    placed = {key: _place(panel, canvas) for key, panel in zip(CELL_KEYS, matted)}
    dx, dy = _best_shift(placed["neutral"], placed["talk"])
    print(f"talk registered by {(dx, dy)}", flush=True)
    placed["talk"] = _shift(placed["talk"], dx, dy)
    placed["talk_delta"] = _mouth_delta(placed["neutral"], placed["talk"])
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for key, image in placed.items():
        path = out_dir / f"{character_id}-{key}.png"
        Image.fromarray(image, "RGBA").save(path)
        files[key] = str(path)
        coverage = float((image[:, :, 3] > 24).mean())
        print(f"{key}: {image.shape[1]}x{image.shape[0]} coverage={coverage:.3f}", flush=True)
    contact = _contact(placed)
    contact_path = out_dir / f"{character_id}-contact.png"
    Image.fromarray(contact, "RGBA").save(contact_path)
    manifest = {"character_id": character_id, "cells": files, "canvas": list(canvas),
                "flap": bool((placed["talk_delta"][:, :, 3] > 24).any())}
    (out_dir / f"{character_id}.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _contact(placed: dict[str, np.ndarray]) -> np.ndarray:
    keys = ["neutral", "talk_delta", "smile", "angry", "sad", "shock"]
    sample = placed["neutral"]
    thumb_h = 280
    scale = thumb_h / sample.shape[0]
    thumb_w = int(sample.shape[1] * scale)
    tiles = []
    for key in keys:
        layer = placed[key]
        if key == "talk_delta":
            base = placed["neutral"].copy()
            alpha = layer[:, :, 3:4].astype(np.float32) / 255.0
            base[:, :, :3] = (base[:, :, :3] * (1 - alpha) + layer[:, :, :3] * alpha).astype(np.uint8)
            base[:, :, 3] = np.maximum(base[:, :, 3], layer[:, :, 3])
            layer = base
        rgb = np.full(layer.shape, (70, 74, 82, 255), np.uint8)
        alpha = layer[:, :, 3:4].astype(np.float32) / 255.0
        rgb[:, :, :3] = (rgb[:, :, :3] * (1 - alpha) + layer[:, :, :3] * alpha).astype(np.uint8)
        small = cv2.resize(rgb, (thumb_w, thumb_h), interpolation=cv2.INTER_AREA)
        tiles.append(small)
    return np.concatenate(tiles, axis=1)


if __name__ == "__main__":
    slice_board(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else "hero")
