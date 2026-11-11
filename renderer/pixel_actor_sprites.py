#!/usr/bin/env python3
"""Turn free Kolors character sheets into reusable, tiny game sprites.

This runs on the Actions CPU. The raw sheet is never sent to Remotion: failed
segmentation drops the body file and lets the vector actor render instead.
"""
from __future__ import annotations
import io
import json
import os
import sys
from pathlib import Path

from PIL import Image


def make_sprite(path: Path, output: Path, session) -> bool:
    from rembg import remove
    image = Image.open(io.BytesIO(remove(path.read_bytes(), session=session))).convert('RGBA')
    alpha = image.getchannel('A')
    box = alpha.point(lambda value: 255 if value >= 96 else 0).getbbox()
    if not box or box[3] - box[1] < image.height * .65:
        return False
    image = image.crop(box)
    width, height = image.size
    if width > height * .85 or width < height * .15:
        return False
    # Generated sheets often contain background visible between the legs.
    # Remove only pale, border-colored pixels in that central lower gap.
    pixels = image.load()
    border = Image.open(path).convert('RGB')
    sample = border.getpixel((min(border.width - 1, 8), min(border.height - 1, int(border.height * .72))))
    for y in range(int(height * .55), int(height * .94)):
        for x in range(int(width * .43), int(width * .57)):
            r, g, b, a = pixels[x, y]
            distance = ((r-sample[0])**2 + (g-sample[1])**2 + (b-sample[2])**2)**.5
            if a and distance < 82 and min(r, g, b) > 105:
                pixels[x, y] = (r, g, b, 0)
    scale = min(30 / width, 60 / height)
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    image = image.resize(size, Image.Resampling.LANCZOS)
    canvas = Image.new('RGBA', (36, 64), (0, 0, 0, 0))
    canvas.alpha_composite(image, ((36 - size[0]) // 2, 64 - size[1]))
    if canvas.getchannel('A').getbbox() is None:
        return False
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output)
    return True


def process(manifest: Path) -> None:
    doc = json.loads(manifest.read_text())
    paths = {actor.get('body_file') for shot in doc.get('shots', []) if shot.get('pixel_story')
             for actor in (shot.get('cartoon') or {}).get('actors', []) if actor.get('body_file')}
    if not paths:
        return
    try:
        from rembg import new_session
        os.environ.setdefault('U2NET_HOME', 'character-model')
        session = new_session('isnet-anime')
    except Exception as exc:
        print(f'Pixel sprite segmentation unavailable: {type(exc).__name__}', flush=True)
        session = None
    results = {}
    for name in sorted(paths):
        output = Path('media') / f'pixel-actor-{Path(name).stem}.png'
        try:
            results[name] = str(output) if session and make_sprite(Path(name), output, session) else ''
        except Exception as exc:
            print(f'Pixel sprite rejected for {Path(name).name}: {type(exc).__name__}', flush=True)
            results[name] = ''
    for shot in doc.get('shots', []):
        if not shot.get('pixel_story'):
            continue
        for actor in (shot.get('cartoon') or {}).get('actors', []):
            if actor.get('body_file'):
                replacement = results.get(actor['body_file'], '')
                if replacement:
                    actor['body_file'] = replacement
                else:
                    actor.pop('body_file', None)
    manifest.write_text(json.dumps(doc, ensure_ascii=False))
    print(f'Pixel actor sprites ready: {sum(bool(value) for value in results.values())}/{len(results)}', flush=True)


if __name__ == '__main__':
    process(Path(sys.argv[1] if len(sys.argv) > 1 else 'manifest.json'))
