#!/usr/bin/env python3
"""Extract safe anime character layers for independent Remotion motion.

The original shot remains the fallback. A bad mask must never break a render.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def layer_shot(shot: dict, index: int, session, output_dir: Path) -> bool:
    import cv2
    import numpy as np
    from PIL import Image
    from rembg import remove

    source = Path(str(shot.get('file', '')))
    if source.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp'} or not source.is_file():
        return False
    # Explanatory graphics often have no character. Keep them untouched.
    if shot.get('fact_card') or shot.get('label'):
        return False
    rgb = Image.open(source).convert('RGB')
    width, height = rgb.size
    cut = remove(rgb, session=session).convert('RGBA')
    alpha = np.asarray(cut.getchannel('A'), dtype=np.uint8)
    binary = (alpha >= 96).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    components = sorted(range(1, count), key=lambda i: int(stats[i, cv2.CC_STAT_AREA]), reverse=True)
    if not components:
        return False
    main = components[0]
    area = int(stats[main, cv2.CC_STAT_AREA])
    x, y, box_width, box_height = (int(stats[main, field]) for field in
        (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP, cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT))
    coverage = area / (width * height)
    # A full-frame or tiny mask is usually scenery, a desk, or a broken cutout.
    if not (.075 <= coverage <= .42 and box_width <= width * .76 and box_height >= height * .39
            and box_width / box_height <= .88):
        print(f'layer {index}: rejected mask area={coverage:.3f} box={box_width}x{box_height}', flush=True)
        return False
    if len(components) > 1 and stats[components[1], cv2.CC_STAT_AREA] > area * .25:
        print(f'layer {index}: multiple subjects; keeping original shot', flush=True)
        return False
    kept = (labels == main).astype(np.uint8)
    # The soft original matte supplies antialiased hair and clothing edges.
    kept = cv2.dilate(kept, np.ones((3, 3), np.uint8), iterations=1)
    matte = (alpha.astype(np.float32) * kept).astype(np.uint8)
    if int((matte >= 96).sum()) < width * height * .07:
        return False
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f'layer-{index:04d}'
    foreground = np.asarray(rgb.convert('RGBA')).copy()
    foreground[:, :, 3] = matte
    # Inpaint at half resolution: the moving subject reveals only a narrow strip.
    fill_mask = cv2.dilate((matte >= 32).astype(np.uint8), np.ones((17, 17), np.uint8), iterations=1)
    img_bgr = cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2BGR)
    small = (max(1, width // 2), max(1, height // 2))
    restored_small = cv2.inpaint(cv2.resize(img_bgr, small, interpolation=cv2.INTER_AREA),
                                  cv2.resize(fill_mask, small, interpolation=cv2.INTER_NEAREST),
                                  7, cv2.INPAINT_TELEA)
    restored = cv2.resize(restored_small, (width, height), interpolation=cv2.INTER_CUBIC)
    # Preserve all untouched image pixels exactly, including the original style.
    background = np.where(fill_mask[:, :, None] > 0, restored, img_bgr)
    bg_path = output_dir / f'{stem}-background.jpg'
    cv2.imwrite(str(bg_path), background, [cv2.IMWRITE_JPEG_QUALITY, 94])
    body_path = output_dir / f'{stem}-body.png'
    Image.fromarray(foreground, 'RGBA').save(body_path)
    layers: dict[str, object] = {'background_file': str(bg_path), 'body_file': str(body_path),
                                 'subject_x': (x + box_width / 2) / width,
                                 'subject_y': (y + box_height * .78) / height}
    shot['character_layers'] = layers
    print(f'layer {index}: accepted area={coverage:.3f}', flush=True)
    return True


def process(manifest_path: Path) -> None:
    doc = json.loads(manifest_path.read_text(encoding='utf-8'))
    shots = doc.get('shots', [])
    candidates = [shot for shot in shots if Path(str(shot.get('file', ''))).suffix.lower()
                  in {'.png', '.jpg', '.jpeg', '.webp'} and not shot.get('fact_card') and not shot.get('label')]
    if not candidates:
        return
    from rembg import new_session
    session = new_session('isnet-anime', providers=['CPUExecutionProvider'])
    output_dir = Path('media/character-layers')
    accepted = 0
    for i, shot in enumerate(shots):
        try:
            accepted += int(layer_shot(shot, i, session, output_dir))
        except Exception as error:
            print(f'layer {i}: skipped after {type(error).__name__}: {error}', flush=True)
    manifest_path.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')
    print(f'character layers: {accepted}/{len(shots)} shots', flush=True)


if __name__ == '__main__':
    process(Path(sys.argv[1]))
