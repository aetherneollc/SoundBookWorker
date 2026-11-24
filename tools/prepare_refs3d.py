#!/usr/bin/env python3
"""Square, pure-white reference plates for the single-image 3D jobs (TRELLIS).

    python tools/prepare_refs3d.py

Writes ``media/assets3d/refs_clean/``:

* ``linlan_apose.png`` / ``chen_apose.png``: the verified A-pose cutouts from
  ``media/m0_real_gate/*/texture.png``, alpha-composited onto white and centred.
* ``sensor_clean.png``: ``refs/sensor.png`` cut out with GrabCut so the pale
  blue spray on the left and the orange speck under the grip become white.
* ``atuo_clean.png``: ``refs/atuo.png`` copied as is.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'media' / 'assets3d' / 'refs_clean'
SIZE = 1024
MARGIN = 0.06


def place_on_white(rgba: Image.Image, fill: float = 1.0 - 2 * MARGIN) -> Image.Image:
    """Crop to the alpha box, fit inside ``fill`` of the square, centre on white."""
    box = rgba.getchannel('A').point(lambda a: 255 if a > 8 else 0).getbbox()
    if box is None:
        raise ValueError('image is fully transparent')
    cropped = rgba.crop(box)
    scale = min(SIZE * fill / cropped.width, SIZE * fill / cropped.height)
    size = (max(1, round(cropped.width * scale)), max(1, round(cropped.height * scale)))
    cropped = cropped.resize(size, Image.Resampling.LANCZOS)
    canvas = Image.new('RGBA', (SIZE, SIZE), (255, 255, 255, 255))
    canvas.alpha_composite(cropped, ((SIZE - size[0]) // 2, (SIZE - size[1]) // 2))
    return canvas.convert('RGB')


def sensor_cutout(path: Path) -> Image.Image:
    """Cut the device away from the haze on the left and the reflection under the grip.

    The device is white plastic with blown highlights, so white alone cannot
    mark the canvas: the coloured parts are joined into one silhouette, its
    holes are filled, and the pale cyan spray is removed from it.
    """
    rgb = np.array(Image.open(path).convert('RGB'))
    h, w = rgb.shape[:2]
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    r, b = rgb[:, :, 0].astype(np.int32), rgb[:, :, 2].astype(np.int32)
    haze = (r > 150) & (b > 205) & (b - r > 18) & (hsv[:, :, 1] < 90)
    white = rgb.min(axis=2) > 248
    seed = (~white & ~haze).astype(np.uint8) * 255
    seed[:, :int(0.2 * w)] = 0
    seed[:, int(0.78 * w):] = 0
    seed[int(0.847 * h):, :] = 0
    seed = cv2.morphologyEx(seed, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(seed, 8)
    keep = np.zeros_like(seed)
    for index in range(1, count):
        if stats[index, cv2.CC_STAT_AREA] >= 40:
            keep[labels == index] = 255
    keep = cv2.morphologyEx(keep, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (41, 41)))
    flood = keep.copy()
    cv2.floodFill(flood, np.zeros((h + 2, w + 2), np.uint8), (0, 0), 255)
    keep = keep | cv2.bitwise_not(flood)
    keep[haze & (keep > 0) & (cv2.erode(keep, np.ones((15, 15), np.uint8)) == 0)] = 0
    count, labels, stats, _ = cv2.connectedComponentsWithStats(keep, 8)
    if count > 1:
        largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        keep = np.where(labels == largest, 255, 0).astype(np.uint8)
    alpha = cv2.GaussianBlur(keep, (3, 3), 0)
    return Image.fromarray(np.dstack([rgb, alpha]), 'RGBA')


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    report = {}
    for name, source in (
        ('linlan_apose.png', ROOT / 'media' / 'm0_real_gate' / 'linlan_1011' / 'texture.png'),
        ('chen_apose.png', ROOT / 'media' / 'm0_real_gate' / 'chen_2003' / 'texture.png'),
    ):
        place_on_white(Image.open(source).convert('RGBA')).save(OUT / name)
        report[name] = {'source': source.relative_to(ROOT).as_posix(), 'method': 'alpha composite on white, centred'}
    sensor_src = ROOT / 'media' / 'assets3d' / 'refs' / 'sensor.png'
    place_on_white(sensor_cutout(sensor_src), fill=0.8).save(OUT / 'sensor_clean.png')
    report['sensor_clean.png'] = {'source': sensor_src.relative_to(ROOT).as_posix(), 'method': 'GrabCut, haze and reflection to white'}
    atuo_src = ROOT / 'media' / 'assets3d' / 'refs' / 'atuo.png'
    shutil.copyfile(atuo_src, OUT / 'atuo_clean.png')
    report['atuo_clean.png'] = {'source': atuo_src.relative_to(ROOT).as_posix(), 'method': 'copy'}
    (OUT / 'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for name in report:
        print(OUT.relative_to(ROOT).joinpath(name).as_posix())


if __name__ == '__main__':
    main()
