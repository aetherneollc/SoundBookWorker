#!/usr/bin/env python3
"""Build a reusable full-frame sprite rig from an approved character cutout.

The annotation JSON is a one-time art-direction step. Daily video renders reuse
the resulting sprites; they do not re-segment or redraw the main character.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw


def build(source_path: Path, annotation_path: Path, output_dir: Path) -> dict:
    spec = json.loads(annotation_path.read_text(encoding='utf-8'))
    source = np.asarray(Image.open(source_path).convert('RGBA')).copy()
    source_h, source_w = source.shape[:2]
    canvas_w, canvas_h = (int(v) for v in spec['canvas'])
    scale = float(spec['scale'])
    offset_x, offset_y = (int(v) for v in spec['offset'])
    if not (0.2 <= scale <= 2 and 320 <= canvas_w <= 3840 and 320 <= canvas_h <= 2160):
        raise ValueError('invalid canvas or scale')
    if offset_x < 0 or offset_y < 0 or offset_x + source_w * scale > canvas_w or offset_y + source_h * scale > canvas_h:
        raise ValueError('character does not fit canvas')

    # Remove translucent remote segmentation noise and keep only the main figure.
    binary = (source[:, :, 3] >= 60).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    if count < 2:
        raise ValueError('no connected character foreground')
    main = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    source[:, :, 3] = np.where(labels == main, source[:, :, 3], 0)
    foreground_area = int((source[:, :, 3] >= 96).sum())
    if foreground_area < source_w * source_h * .08:
        raise ValueError('foreground extraction too small')

    masks: dict[str, np.ndarray] = {}
    for name in ('head', 'arm_left', 'arm_right'):
        points = spec.get('parts', {}).get(name)
        if not points:
            continue
        polygon = np.asarray(points, dtype=np.int32)
        if polygon.ndim != 2 or polygon.shape[1] != 2 or len(polygon) < 3:
            raise ValueError(f'invalid {name} polygon')
        mask = np.zeros((source_h, source_w), np.uint8)
        cv2.fillPoly(mask, [polygon], 255)
        masks[name] = cv2.GaussianBlur(mask, (3, 3), .6).astype(np.float32) / 255
        if int(((mask > 128) & (source[:, :, 3] >= 96)).sum()) < foreground_area * .015:
            raise ValueError(f'{name} contains too little foreground')
    if 'head' not in masks or not any(k.startswith('arm_') for k in masks):
        raise ValueError('head and at least one arm are required')

    used = np.maximum.reduce(list(masks.values()))
    masks['body'] = 1 - used
    output_dir.mkdir(parents=True, exist_ok=True)
    rig: dict[str, object] = {
        'subject_x': (offset_x + float(spec['subject'][0]) * scale) / canvas_w,
        'subject_y': (offset_y + float(spec['subject'][1]) * scale) / canvas_h,
        'character': str(spec['character']),
        'mouth_mode': 'led',
        'mouth_x': (offset_x + float(spec['mouth'][0]) * scale) / canvas_w,
        'mouth_y': (offset_y + float(spec['mouth'][1]) * scale) / canvas_h,
        'mouth_width': float(spec.get('mouth_width', .03)),
        'action': 'idle',
    }
    for name, point in spec.get('pivots', {}).items():
        if name not in masks:
            continue
        rig[f'{name}_pivot_x'] = (offset_x + float(point[0]) * scale) / canvas_w
        rig[f'{name}_pivot_y'] = (offset_y + float(point[1]) * scale) / canvas_h

    for name, mask in masks.items():
        pixels = source.copy()
        pixels[:, :, 3] = (pixels[:, :, 3].astype(np.float32) * mask).astype(np.uint8)
        image = Image.fromarray(pixels, 'RGBA')
        if name == 'body':
            for joint in spec.get('joint_disks', []):
                # Draw beneath the arm sprite so a restrained rotation does not expose the background.
                ink = Image.new('RGBA', image.size)
                draw = ImageDraw.Draw(ink)
                x, y, rx, ry = (float(v) for v in joint[:4])
                color = str(joint[4]) if len(joint) > 4 else '#101b29'
                draw.ellipse((x-rx, y-ry, x+rx, y+ry), fill=color)
                ink.alpha_composite(image)
                image = ink
        image = image.resize((round(source_w * scale), round(source_h * scale)), Image.Resampling.LANCZOS)
        frame = Image.new('RGBA', (canvas_w, canvas_h))
        frame.alpha_composite(image, (offset_x, offset_y))
        filename = f'{name}.png'
        frame.save(output_dir / filename, optimize=True)
        rig[f'{name}_file'] = str(output_dir / filename)

    reconstructed = Image.new('RGBA', (canvas_w, canvas_h))
    for name in ('body', 'arm_left', 'arm_right', 'head'):
        if name in masks:
            reconstructed.alpha_composite(Image.open(output_dir / f'{name}.png'))
    reconstructed.save(output_dir / 'rest-pose.png', optimize=True)
    (output_dir / 'rig.json').write_text(json.dumps(rig, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"rig built: {spec['character']}; foreground={foreground_area}; parts={','.join(masks)}")
    return rig


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('cutout', type=Path)
    parser.add_argument('annotation', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    build(args.cutout, args.annotation, args.output)
