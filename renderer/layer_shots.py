#!/usr/bin/env python3
"""Extract safe anime character layers for independent Remotion motion.

The original shot remains the fallback. A bad mask must never break a render.
Story shots (story_motion) may keep two subjects and are inpainted at full
resolution. Every other shot stays a single subject, inpainted at half resolution.
A story plate may also carry a rover overlay; that overlay is not a reason to
skip the cutout. Rover-only and stage plates stay untouched.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _skip_cutout(shot: dict) -> bool:
    """Leave a supplied rig alone. Skip rover, stage, and puppet plate shots."""
    layers = shot.get('character_layers')
    if isinstance(layers, dict) and layers.get('body_file'):
        return True
    if shot.get('plate'):
        return True
    story_motion = shot.get('story_motion')
    if isinstance(story_motion, dict):
        actors = story_motion.get('actors') or []
        if any(isinstance(a, dict) and (a.get('rig_key') or a.get('rig_file') or a.get('rig_json')) for a in actors):
            return True
        return False
    return bool(shot.get('rover') or shot.get('stage'))


def _component_box(stats, index: int) -> tuple[int, int, int, int, int]:
    import cv2
    area = int(stats[index, cv2.CC_STAT_AREA])
    x, y, box_width, box_height = (int(stats[index, field]) for field in
        (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP, cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT))
    return area, x, y, box_width, box_height


def _person_mask(area: int, box_width: int, box_height: int, width: int, height: int,
                 min_coverage: float = .075) -> tuple[bool, float]:
    coverage = area / (width * height)
    # A full-frame or tiny mask is usually scenery, a desk, or a broken cutout.
    plausible = (min_coverage <= coverage <= .42 and box_width <= width * .76 and box_height >= height * .39
                 and box_width / box_height <= .88)
    return plausible, coverage


def layer_shot(shot: dict, index: int, session, output_dir: Path) -> bool:
    import cv2
    import numpy as np
    from PIL import Image
    from rembg import remove

    # A supplied rig has deliberate pivots and sprites; never replace it with a whole-body mask.
    # Rover-only and stage plates are composited elsewhere. A story_motion plate
    # that also carries a rover still needs its character cutout.
    if _skip_cutout(shot):
        return False
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
    # New story plates may contain two separated figures. Explainer shots stay single-subject.
    story = isinstance(shot.get('story_motion'), dict)
    main = components[0]
    area, x, y, box_width, box_height = _component_box(stats, main)
    plausible, coverage = _person_mask(area, box_width, box_height, width, height)
    if not plausible:
        print(f'layer {index}: rejected mask area={coverage:.3f} box={box_width}x{box_height}', flush=True)
        return False
    subjects = [(main, x, y, box_width, box_height)]
    if len(components) > 1 and int(stats[components[1], cv2.CC_STAT_AREA]) > area * .25:
        if not story:
            print(f'layer {index}: multiple subjects; keeping original shot', flush=True)
            return False
        second_area, sx, sy, sw, sh = _component_box(stats, components[1])
        # A slightly smaller partner can still be a full figure. Scenery-sized blobs cannot.
        second_ok, second_coverage = _person_mask(second_area, sw, sh, width, height, min_coverage=.04)
        if not second_ok:
            print(f'layer {index}: second subject rejected area={second_coverage:.3f} box={sw}x{sh}', flush=True)
            return False
        if len(components) > 2 and int(stats[components[2], cv2.CC_STAT_AREA]) > area * .25:
            print(f'layer {index}: more than two subjects; keeping original shot', flush=True)
            return False
        subjects.append((components[1], sx, sy, sw, sh))

    def matte_for(component: int) -> np.ndarray:
        kept = (labels == component).astype(np.uint8)
        # The soft original matte supplies antialiased hair and clothing edges.
        kept = cv2.dilate(kept, np.ones((3, 3), np.uint8), iterations=1)
        return (alpha.astype(np.float32) * kept).astype(np.uint8)

    mattes = [matte_for(component) for component, *_rest in subjects]
    if int((mattes[0] >= 96).sum()) < width * height * .07:
        return False
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f'layer-{index:04d}'
    plate = np.asarray(rgb.convert('RGBA'))
    fill = np.zeros((height, width), np.uint8)
    for matte in mattes:
        fill = np.maximum(fill, (matte >= 32).astype(np.uint8))
    fill_mask = cv2.dilate(fill, np.ones((17, 17), np.uint8), iterations=1)
    img_bgr = cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2BGR)
    if story:
        # Full-frame inpaint keeps the fill sharp. Only story shots pay this cost.
        restored = cv2.inpaint(img_bgr, fill_mask, 7, cv2.INPAINT_TELEA)
    else:
        # Inpaint at half resolution: the moving subject reveals only a narrow strip.
        small = (max(1, width // 2), max(1, height // 2))
        restored_small = cv2.inpaint(cv2.resize(img_bgr, small, interpolation=cv2.INTER_AREA),
                                      cv2.resize(fill_mask, small, interpolation=cv2.INTER_NEAREST),
                                      7, cv2.INPAINT_TELEA)
        restored = cv2.resize(restored_small, (width, height), interpolation=cv2.INTER_CUBIC)
    # Preserve all untouched image pixels exactly, including the original style.
    background = np.where(fill_mask[:, :, None] > 0, restored, img_bgr)
    bg_path = output_dir / f'{stem}-background.jpg'
    cv2.imwrite(str(bg_path), background, [cv2.IMWRITE_JPEG_QUALITY, 94])
    layers: dict[str, object] = {'background_file': str(bg_path)}
    body_keys = [('body', 'body_file', 'subject_x', 'subject_y'), ('body2', 'body2_file', 'subject2_x', 'subject2_y')]
    for (_component, bx, by, bw, bh), matte, (name, file_key, x_key, y_key) in zip(subjects, mattes, body_keys):
        foreground = plate.copy()
        foreground[:, :, 3] = matte
        body_path = output_dir / f'{stem}-{name}.png'
        Image.fromarray(foreground, 'RGBA').save(body_path)
        layers[file_key] = str(body_path)
        layers[x_key] = (bx + bw / 2) / width
        layers[y_key] = (by + bh * .78) / height
    shot['character_layers'] = layers
    print(f'layer {index}: accepted area={coverage:.3f} subjects={len(subjects)}', flush=True)
    return True


def process(manifest_path: Path) -> None:
    doc = json.loads(manifest_path.read_text(encoding='utf-8'))
    shots = doc.get('shots', [])
    candidates = [shot for shot in shots if Path(str(shot.get('file', ''))).suffix.lower()
                  in {'.png', '.jpg', '.jpeg', '.webp'} and not shot.get('fact_card') and not shot.get('label')
                  and not _skip_cutout(shot)]
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
