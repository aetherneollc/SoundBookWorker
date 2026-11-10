#!/usr/bin/env python3
"""Split one room plate into a far wall and a near desk.

The person is drawn between those two layers, so the desktop, lamp, books
and chair hide his waist. A single flat plate cannot do that: the sprite
always covers the furniture.
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


def metal_edge(rgb: np.ndarray) -> int:
    """Top of the desk, not the brightest line inside the console."""
    height, width = rgb.shape[:2]
    gray = rgb.mean(axis=2)
    left, right = int(width * 0.12), int(width * 0.88)
    start, end = max(1, int(height * 0.55)), int(height * 0.82)
    strengths = [
        float(np.abs(gray[y, left:right] - gray[y - 1, left:right]).sum())
        for y in range(start, max(start, end))
    ]
    if not strengths:
        return int(height * 0.72)
    cutoff = max(strengths) * 0.45
    for offset, edge in enumerate(strengths):
        if edge >= cutoff:
            return start + offset
    return int(height * 0.72)


def split_room(room: Path, out_dir: Path, sill_override: int | None = None) -> tuple[Path, Path]:
    rgb = np.asarray(Image.open(room).convert("RGB"))
    height, width = rgb.shape[:2]
    red = rgb[:, :, 0].astype(np.int16)
    green = rgb[:, :, 1].astype(np.int16)
    blue = rgb[:, :, 2].astype(np.int16)
    rows = np.arange(height)[:, None]
    cols = np.arange(width)[None, :]

    # The window frame is brown too. Filling the wood bounding box paints that
    # glass over the person, so the bust reads as a floating head. The near
    # layer starts at the desktop sill and stays below it.
    brown = (red > 80) & (red > green + 8) & (green > blue) & (red < 230)
    band = brown[:, int(width * 0.18):int(width * 0.82)].mean(axis=1)
    sill = int(height * 0.78)
    found = False
    metal = False
    for y in range(int(height * 0.68), height - 30):
        if band[y] > 0.28 and float(band[y:y + 20].mean()) > 0.35:
            sill = y
            found = True
            break
    # A gray lunar bench has no wood. Use the strongest horizontal edge in
    # the lower half, and never pull that edge up over the window.
    if not found and float(band.mean()) < 0.05:
        sill = metal_edge(rgb)
        metal = True
        print(f"metal sill={sill}", flush=True)
    if sill_override is not None:
        sill = int(sill_override)
        metal = True
        print(f"sill override={sill}", flush=True)
    wood = (brown & (rows >= sill)).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(wood, 8)
    mask = np.zeros((height, width), np.uint8)
    desk = max(range(1, count), key=lambda i: int(stats[i, cv2.CC_STAT_AREA]), default=0)
    if not metal and desk:
        left = int(stats[desk, cv2.CC_STAT_LEFT])
        desk_w = int(stats[desk, cv2.CC_STAT_WIDTH])
        if desk_w >= width * 0.55:
            # Fill from the sill downward only. The window frame is brown too, and
            # its bounding box would paint that glass over the person.
            mask[sill:height, left:left + desk_w] = 255
        else:
            desk = 0
    if metal or not desk:
        sill = metal_edge(rgb) if not metal else sill
        metal = True
        mask[sill:height, :] = 255
        print(f"metal sill={sill}", flush=True)
    print(f"sill={sill}", flush=True)

    # The rainy-room lamp and chair sit above a wooden desk. A metal cabin
    # already has a full-width slab, and those color tests would paint the
    # orange window frame into the foreground.
    if not metal:
        shade = ((red > 190) & (green > 80) & (blue < 150) & (red > blue + 50)
                 & (rows > 250) & (rows < 520) & (cols > 250) & (cols < 620))
        shade_rows, shade_cols = np.where(shade)
        if len(shade_rows):
            mask[shade] = 255
            neck = ((rows > int(shade_rows.max()) - 8) & (rows < 640)
                    & (cols > int(shade_cols.min()) - 16) & (cols < int(shade_cols.max()) + 36)
                    & (red < 110) & (green < 95) & (blue < 80))
            mask[neck] = 255
        chair = ((red > 150) & (green > 140) & (blue > 120) & (rows > 520) & (cols > 980))
        mask[chair] = 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)
    # Drop scraps that float over the person. Keep the desk and the lamp.
    count, labels, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), 8)
    kept = np.zeros_like(mask)
    for index in range(1, count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        bottom = int(stats[index, cv2.CC_STAT_TOP] + stats[index, cv2.CC_STAT_HEIGHT])
        if area > 3000 or bottom > height - 30:
            kept[labels == index] = 255
    mask = kept

    fill = cv2.dilate(mask, np.ones((9, 9), np.uint8), iterations=1)
    restored = cv2.inpaint(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), fill, 5, cv2.INPAINT_TELEA)
    background = cv2.cvtColor(restored, cv2.COLOR_BGR2RGB)
    foreground = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGRA)
    foreground[:, :, 3] = mask

    out_dir.mkdir(parents=True, exist_ok=True)
    far = out_dir / "room-far.png"
    near = out_dir / "desk-near.png"
    Image.fromarray(background, "RGB").save(far)
    Image.fromarray(cv2.cvtColor(foreground, cv2.COLOR_BGRA2RGBA), "RGBA").save(near)
    print(f"far={far}", flush=True)
    print(f"near={near} coverage={float((mask > 0).mean()):.3f}", flush=True)
    return far, near


if __name__ == "__main__":
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("manju/demo/kolors/room-clean.png")
    override = int(sys.argv[3]) if len(sys.argv) > 3 else None
    split_room(source, Path(sys.argv[2]) if len(sys.argv) > 2 else Path("manju/demo/depth"), override)
