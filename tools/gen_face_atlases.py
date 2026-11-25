#!/usr/bin/env python3
"""Paint 1024x1024 2x2 anime face atlases for Linlan and Chen.

Skin tone is sampled from the canonical plates. Brows, eyes, nose, and mouth
are redrawn in each character's ink weight and palette, then placed at the
same cell coordinates in every quadrant. The margin outside those features is
flat skin, so a face UV projection blends into the head without hair or a
black border.

Cells (col, row), each 512x512:
  neutral (0, 0)   blink (1, 0)
  talk_mid (0, 1)  talk_open (1, 1)
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / 'media' / 'assets3d' / 'textures'
CELL = 512
ATLAS = CELL * 2
SCALE = 4

# Cell-local anchors. Origin is the top-left of the cell; y grows downward.
FEATURES = {
    'left_eye': (0.34, 0.44),
    'right_eye': (0.66, 0.44),
    'nose': (0.50, 0.57),
    'mouth': (0.50, 0.69),
}
EXPRESSIONS = (
    ('neutral', 0, 0),
    ('blink', 1, 0),
    ('talk_mid', 0, 1),
    ('talk_open', 1, 1),
)

MOUTH_INTERIOR = (72, 32, 36)
MOUTH_INTERIOR_DEEP = (48, 22, 26)
TONGUE = (196, 124, 132)
TONGUE_DEEP = (168, 96, 108)
TEETH = (250, 246, 238)
SCLERA = (255, 253, 250)

CHARACTERS = {
    'linlan': {
        'source': ROOT / 'media' / 'm0_real_gate' / 'linlan_1011' / 'texture.png',
        'cheek_box': (960, 400, 40, 28),
        'ink': (40, 24, 26),
        'iris_kind': 'radial',
        'iris_inner': (168, 98, 74),
        'iris_outer': (68, 34, 32),
        'pupil': (32, 14, 16),
        'nose_rgb': (108, 68, 56),
        'eye_w': 98,
        'eye_h': 64,
        'outer_lift': 3.2,
        'round_eye': False,
        'iris_d': 50,
        'pupil_d': 20,
        'pupil_shift': 1.5,
        'upper_lid': 4.5,
        'lower_lid': 2.15,
        'lid_cover': 2.4,
        'wing': 3.0,
        'brow_len': 96,
        'brow_thick': 5.2,
        'brow_arch': 7.2,
        'brow_drop': 0.8,
        'brow_gap': 48,
        'heavy_brow': False,
        'nose_w': 2.35,
        'smile_w': 74,
        'smile_drop': 8.0,
        'smile_stroke': 3.15,
        'mouth_mid': (62, 24),
        'mouth_open': (76, 42),
        'outline': 3.15,
    },
    'chen': {
        'source': ROOT / 'media' / 'm0_real_gate' / 'chen_2003' / 'texture.png',
        'cheek_box': (1000, 480, 30, 20),
        'ink': (16, 12, 14),
        'iris_kind': 'vertical',
        'iris_top': (10, 12, 10),
        'iris_bottom': (220, 198, 46),
        'pupil': (6, 6, 8),
        'nose_rgb': (176, 96, 56),
        'eye_w': 104,
        'eye_h': 86,
        'outer_lift': 2.2,
        'round_eye': True,
        'iris_d': 72,
        'pupil_d': 54,
        'pupil_shift': -11.0,
        'upper_lid': 7.4,
        'lower_lid': 2.85,
        'lid_cover': 4.2,
        'wing': 2.2,
        'brow_len': 108,
        'brow_thick': 10.2,
        'brow_arch': 8.2,
        'brow_drop': 3.2,
        'brow_gap': 64,
        'heavy_brow': True,
        'nose_w': 3.05,
        'smile_w': 48,
        'smile_drop': 4.6,
        'smile_stroke': 3.3,
        'mouth_mid': (52, 22),
        'mouth_open': (66, 38),
        'outline': 3.25,
    },
}


def sample_skin(path: Path, box: tuple[int, int, int, int]) -> tuple[int, int, int]:
    """Median color of a flat cheek patch on the canonical plate."""
    image = np.asarray(Image.open(path).convert('RGB'))
    x, y, w, h = box
    crop = image[y:y + h, x:x + w]
    if crop.size == 0:
        raise ValueError('cheek sample is empty: %s' % path)
    flat = crop.reshape(-1, 3).astype(np.float64)
    std = flat.std(axis=0)
    if float(std.max()) > 12.0:
        raise ValueError('cheek sample is not flat skin (std %s) in %s' % (std.round(2), path))
    median = np.median(flat, axis=0)
    return tuple(int(round(channel)) for channel in median)


def _hex(rgb: tuple[int, int, int]) -> str:
    return '#%02X%02X%02X' % rgb


def _quad(p0, p1, p2, count: int) -> list[tuple[float, float]]:
    points = []
    for index in range(count + 1):
        t = index / count
        u = 1.0 - t
        points.append((
            u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
            u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1],
        ))
    return points


def _px(points, anchor, scale: int) -> list[tuple[float, float]]:
    ax, ay = anchor
    return [(ax + x * scale, ay + y * scale) for x, y in points]


def _downscale(sprite: Image.Image) -> Image.Image:
    """Premultiplied downsample so ink anti-aliases onto transparency, not black."""
    arr = np.asarray(sprite).astype(np.float32)
    alpha = arr[:, :, 3:4] / 255.0
    premul = Image.fromarray(np.clip(np.round(arr[:, :, :3] * alpha), 0, 255).astype(np.uint8), 'RGB')
    mask = Image.fromarray(np.clip(np.round(arr[:, :, 3]), 0, 255).astype(np.uint8), 'L')
    size = (max(1, sprite.width // SCALE), max(1, sprite.height // SCALE))
    premul = premul.resize(size, Image.Resampling.LANCZOS)
    mask = mask.resize(size, Image.Resampling.LANCZOS)
    rgb = np.asarray(premul).astype(np.float32)
    al = np.asarray(mask).astype(np.float32) / 255.0
    straight = np.zeros_like(rgb)
    visible = al > (2.0 / 255.0)
    straight[visible] = rgb[visible] / np.maximum(al[visible, None], 1e-4)
    out = np.zeros((size[1], size[0], 4), dtype=np.uint8)
    out[:, :, :3] = np.clip(np.round(straight), 0, 255).astype(np.uint8)
    out[:, :, 3] = np.where(visible, np.clip(np.round(al * 255.0), 0, 255), 0).astype(np.uint8)
    return Image.fromarray(out, 'RGBA')


def _canvas(width: float, height: float) -> tuple[Image.Image, tuple[float, float]]:
    pixel_w = int(round(width * SCALE))
    pixel_h = int(round(height * SCALE))
    image = Image.new('RGBA', (pixel_w, pixel_h), (0, 0, 0, 0))
    return image, (pixel_w / 2.0, pixel_h / 2.0)


def _stamp_stroke(image: Image.Image, points, widths, anchor, color) -> None:
    """Round variable-width stroke. Circle stamps avoid ribbon end-blobs."""
    if len(points) < 2:
        return
    if isinstance(widths, (int, float)):
        widths = [float(widths)] * len(points)
    px = _px(points, anchor, SCALE)
    ww = [float(width) * SCALE for width in widths]
    draw = ImageDraw.Draw(image)
    fill = color if len(color) == 4 else color + (255,)
    samples = [(px[0][0], px[0][1], ww[0])]
    for (x1, y1), w1, (x0, y0), w0 in zip(px[1:], ww[1:], px, ww):
        dist = math.hypot(x1 - x0, y1 - y0)
        steps = max(1, int(dist / 0.55))
        for step in range(1, steps + 1):
            t = step / steps
            samples.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, w0 + (w1 - w0) * t))
    for x, y, width in samples:
        radius = max(0.75, width / 2.0)
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill)


def _fill_polygon(image: Image.Image, points, color) -> None:
    if len(points) < 3:
        return
    ImageDraw.Draw(image).polygon(points, fill=color)


def _disc(diameter: float, shader) -> Image.Image:
    d = max(2, int(round(diameter)))
    yy, xx = np.mgrid[0:d, 0:d]
    center = (d - 1) / 2.0
    dx = (xx - center) / (d / 2.0)
    dy = (yy - center) / (d / 2.0)
    radius = np.sqrt(dx * dx + dy * dy)
    rgb = shader(dx, dy, radius)
    out = np.zeros((d, d, 4), dtype=np.uint8)
    inside = radius <= 1.0
    out[inside, :3] = np.clip(np.round(rgb[inside]), 0, 255).astype(np.uint8)
    out[inside, 3] = 255
    return Image.fromarray(out, 'RGBA')


def _paste(base: Image.Image, sprite: Image.Image, center) -> None:
    x = int(round(center[0] - sprite.width / 2.0))
    y = int(round(center[1] - sprite.height / 2.0))
    base.alpha_composite(sprite, (x, y))


def _clip(layer: Image.Image, mask: Image.Image) -> Image.Image:
    arr = np.array(layer)
    m = np.asarray(mask).astype(np.float32) / 255.0
    arr[:, :, 3] = np.clip(np.round(arr[:, :, 3].astype(np.float32) * m), 0, 255).astype(np.uint8)
    return Image.fromarray(arr, 'RGBA')


def _eye_curves(style: dict, closed: bool) -> tuple[list, list]:
    """Left-eye curves in 1x pixels. t=0 is the outer (image-left) corner."""
    count = 36
    width = style['eye_w']
    lift = style['outer_lift']
    upper = []
    lower = []
    for index in range(count + 1):
        t = index / count
        x = -width / 2.0 + width * t
        s = math.sin(math.pi * t)
        raised = lift * (1.0 - t)
        if closed:
            upper.append((x, -2.5 * s - raised * 0.65))
            lower.append((x, 1.05 * s - raised * 0.22 + 1.85))
        elif style['round_eye']:
            upper.append((x, -style['eye_h'] * 0.47 * s - raised * 0.45 + style['lid_cover']))
            lower.append((x, style['eye_h'] * 0.40 * s - raised * 0.12))
        else:
            upper.append((x, -style['eye_h'] * 0.48 * s - raised + style['lid_cover']))
            lower.append((x, style['eye_h'] * 0.34 * (s ** 0.92) - raised * 0.22))
    if not closed and style['wing'] > 0:
        ox, oy = upper[0]
        upper.insert(0, (ox - style['wing'], oy - style['wing'] * 0.28))
    return upper, lower


def _mirror(points):
    return [(-x, y) for x, y in points]


def _lid_widths(count: int, thick: float, closed: bool) -> list[float]:
    widths = []
    for index in range(count):
        t = index / max(1, count - 1)
        if closed:
            body = 0.72 + 0.28 * math.sin(math.pi * t)
        else:
            body = 0.55 + 0.55 * (math.sin(math.pi * (1.0 - t) * 0.92) ** 0.65)
        widths.append(thick * body)
    return widths


def _make_eye(style: dict, side: str, closed: bool) -> Image.Image:
    pad = 28
    image, anchor = _canvas(style['eye_w'] + pad, style['eye_h'] + pad)
    upper, lower = _eye_curves(style, closed)
    if side == 'right':
        upper, lower = _mirror(upper), _mirror(lower)
    upper_px = _px(upper, anchor, SCALE)
    lower_px = _px(lower, anchor, SCALE)
    ink = style['ink'] + (255,)
    draw = ImageDraw.Draw(image)
    if not closed:
        sclera = upper_px + lower_px[::-1]
        _fill_polygon(image, sclera, SCLERA + (255,))
        mask = Image.new('L', image.size, 0)
        ImageDraw.Draw(mask).polygon(sclera, fill=255)
        iris = _iris_sprite(style)
        iris_layer = Image.new('RGBA', image.size, (0, 0, 0, 0))
        shift = style['pupil_shift']
        iris_center = (anchor[0], anchor[1] + (2.0 + shift * 0.15) * SCALE)
        _paste(iris_layer, iris, iris_center)
        pupil = _disc(style['pupil_d'] * SCALE, lambda _dx, _dy, _r: np.broadcast_to(
            np.array(style['pupil'], dtype=np.float32), (*_dx.shape, 3),
        ))
        _paste(iris_layer, pupil, (anchor[0], anchor[1] + shift * SCALE))
        _draw_highlights(iris_layer, (anchor[0], anchor[1] + shift * SCALE), style)
        image.alpha_composite(_clip(iris_layer, mask))
    upper_w = _lid_widths(len(upper), style['upper_lid'] * (1.12 if closed else 1.0), closed)
    lower_w = _lid_widths(len(lower), style['lower_lid'] * (1.05 if closed else 1.0), closed)
    _stamp_stroke(image, upper, upper_w, anchor, style['ink'])
    _stamp_stroke(image, lower, lower_w, anchor, style['ink'])
    if closed:
        _draw_lashes(draw, upper_px, side, style['ink'], style['upper_lid'])
    return _downscale(image)


def _iris_sprite(style: dict) -> Image.Image:
    diameter = style['iris_d'] * SCALE

    def shader(dx, dy, radius):
        if style['iris_kind'] == 'vertical':
            # Yellow-green only in the lower crescent; the sides stay near-black.
            bottom_weight = np.clip((dy - 0.05) / 0.95, 0.0, 1.0)
            bottom_weight = bottom_weight ** 0.75
            side = np.clip(np.abs(dx) * 1.25, 0.0, 1.0)
            bottom_weight = bottom_weight * (1.0 - 0.72 * side)
            weight = bottom_weight[..., None]
            top = np.array(style['iris_top'], dtype=np.float32)
            bottom = np.array(style['iris_bottom'], dtype=np.float32)
            color = top * (1.0 - weight) + bottom * weight
        else:
            t = np.clip(radius, 0.0, 1.0)[..., None]
            inner = np.array(style['iris_inner'], dtype=np.float32)
            outer = np.array(style['iris_outer'], dtype=np.float32)
            color = inner * (1.0 - t) + outer * t
            shade = np.clip(-dy, 0.0, 1.0)[..., None]
            color = color * (1.0 - 0.28 * shade)
        ring = np.clip((radius - 0.78) / 0.22, 0.0, 1.0)[..., None]
        return color * (1.0 - 0.38 * ring)

    return _disc(diameter, shader)


def _draw_highlights(layer: Image.Image, center, style: dict) -> None:
    draw = ImageDraw.Draw(layer)
    cx, cy = center
    if style['iris_kind'] == 'vertical':
        specs = ((-0.16, -0.18, 13.0, 10.0), (0.12, 0.02, 4.2, 4.2))
    else:
        specs = ((-0.16, -0.16, 8.4, 6.4), (0.14, 0.12, 3.2, 3.2))
    iris = style['iris_d']
    for ox, oy, w, h in specs:
        x = cx + ox * iris * SCALE
        y = cy + oy * iris * SCALE
        hw, hh = w * SCALE / 2.0, h * SCALE / 2.0
        draw.ellipse((x - hw, y - hh, x + hw, y + hh), fill=(255, 255, 255, 245))


def _draw_lashes(draw: ImageDraw.ImageDraw, upper_px, side: str, ink, thick: float) -> None:
    samples = (0.06, 0.18, 0.32, 0.48, 0.64)
    lengths = (8.2, 10.0, 8.6, 6.4, 4.4)
    last = len(upper_px) - 1
    for t, length in zip(samples, lengths):
        index = int(round(t * last))
        x, y = upper_px[index]
        prev_i = max(0, index - 1)
        next_i = min(last, index + 1)
        dx = upper_px[next_i][0] - upper_px[prev_i][0]
        dy = upper_px[next_i][1] - upper_px[prev_i][1]
        nx, ny = -dy, dx
        if ny > 0:
            nx, ny = -nx, -ny
        outward = -1.0 if side == 'left' else 1.0
        nx += outward * abs(dx) * 0.55
        ny -= abs(dx) * 0.12
        norm = math.hypot(nx, ny) or 1.0
        nx, ny = nx / norm, ny / norm
        span = length * SCALE * (1.15 if thick > 6 else 1.0)
        width = max(2, int(round((2.3 if thick > 6 else 1.8) * SCALE)))
        draw.line((x, y, x + nx * span, y + ny * span), fill=ink + (255,), width=width)


def _make_brow(style: dict, side: str) -> Image.Image:
    image, anchor = _canvas(style['brow_len'] + 18, style['brow_arch'] + style['brow_thick'] + 16)
    count = 28
    length = style['brow_len']
    points = []
    for index in range(count + 1):
        t = index / count
        x = -length / 2.0 + length * t
        y = -style['brow_arch'] * (math.sin(math.pi * t) ** 1.05) + style['brow_drop'] * (1.0 - t)
        points.append((x, y))
    widths = _brow_widths(count, style['brow_thick'], style['heavy_brow'])
    if side == 'left':
        points = _mirror(points)
    _stamp_stroke(image, points, widths, anchor, style['ink'])
    return _downscale(image)


def _brow_widths(count: int, thick: float, heavy: bool) -> list[float]:
    widths = []
    for index in range(count + 1):
        t = index / count
        if heavy:
            if t < 0.76:
                widths.append(thick * (0.84 + 0.16 * math.sin(math.pi * min(t / 0.4, 1.0))))
            else:
                fade = (t - 0.76) / 0.24
                widths.append(thick * max(0.1, (1.0 - fade) ** 1.25))
        else:
            widths.append(thick * (0.7 + 0.3 * math.sin(math.pi * t) ** 0.5))
    return widths


def _make_nose(style: dict) -> Image.Image:
    image, anchor = _canvas(28, 40)
    points = []
    for index in range(18):
        t = index / 17
        points.append((2.2 * t * t, -13.0 + t * 22.0))
    tip = points[-1]
    for index in range(8):
        t = (index + 1) / 8
        points.append((tip[0] + t * 7.2, tip[1] + math.sin(t * math.pi) * 2.4))
    widths = [style['nose_w'] * (0.85 + 0.25 * math.sin(math.pi * i / max(1, len(points) - 1))) for i in range(len(points))]
    _stamp_stroke(image, points, widths, anchor, style['nose_rgb'])
    return _downscale(image)


def _smile_points(width: float, drop: float) -> list[tuple[float, float]]:
    return _quad((-width / 2.0, -drop / 2.0), (0.0, drop * 1.5), (width / 2.0, -drop / 2.0), 28)


def _make_smile(style: dict) -> Image.Image:
    pad = 10
    image, anchor = _canvas(style['smile_w'] + pad, style['smile_drop'] + style['smile_stroke'] + pad)
    points = _smile_points(style['smile_w'], style['smile_drop'])
    widths = []
    for index in range(len(points)):
        t = index / (len(points) - 1)
        widths.append(style['smile_stroke'] * (0.62 + 0.5 * math.sin(math.pi * t)))
    _stamp_stroke(image, points, widths, anchor, style['ink'])
    return _downscale(image)


def _oval(width: float, height: float, count: int = 72) -> list[tuple[float, float]]:
    points = []
    for index in range(count):
        angle = 2.0 * math.pi * index / count
        ca, sa = math.cos(angle), math.sin(angle)
        points.append((
            math.copysign(abs(ca) ** 0.78, ca) * width / 2.0,
            math.copysign(abs(sa) ** 0.92, sa) * height / 2.0,
        ))
    return points


def _inset(points, amount: float) -> list[tuple[float, float]]:
    result = []
    for x, y in points:
        length = math.hypot(x, y) or 1.0
        result.append((x - x / length * amount, y - y / length * amount))
    return result


def _make_open_mouth(style: dict, kind: str) -> Image.Image:
    width, height = style['mouth_mid'] if kind == 'talk_mid' else style['mouth_open']
    image, anchor = _canvas(width + 16, height + 16)
    outer = _oval(width, height)
    inner = _inset(outer, style['outline'])
    _fill_polygon(image, _px(outer, anchor, SCALE), style['ink'] + (255,))
    _fill_polygon(image, _px(inner, anchor, SCALE), MOUTH_INTERIOR + (255,))
    # Upper shadow keeps the opening from looking like a flat red hole.
    shadow = _oval(width * 0.62, height * 0.34)
    shadow = [(x, y - height * 0.16) for x, y in shadow]
    shadow_layer = Image.new('RGBA', image.size, (0, 0, 0, 0))
    _fill_polygon(shadow_layer, _px(shadow, anchor, SCALE), MOUTH_INTERIOR_DEEP + (255,))
    inner_mask = Image.new('L', image.size, 0)
    ImageDraw.Draw(inner_mask).polygon(_px(inner, anchor, SCALE), fill=255)
    image.alpha_composite(_clip(shadow_layer, inner_mask))
    tongue_w = width * (0.58 if kind == 'talk_mid' else 0.70)
    tongue_h = height * (0.34 if kind == 'talk_mid' else 0.48)
    tongue_y = height * (0.20 if kind == 'talk_mid' else 0.16)
    tongue = _disc_ellipse(tongue_w, tongue_h, TONGUE, TONGUE_DEEP)
    tongue_layer = Image.new('RGBA', image.size, (0, 0, 0, 0))
    _paste(tongue_layer, tongue, (anchor[0], anchor[1] + tongue_y * SCALE))
    image.alpha_composite(_clip(tongue_layer, inner_mask))
    if kind == 'talk_open':
        _draw_teeth(image, anchor, width, height, inner_mask)
    return _downscale(image)


def _disc_ellipse(width: float, height: float, top_rgb, bottom_rgb) -> Image.Image:
    pixel_w = max(2, int(round(width * SCALE)))
    pixel_h = max(2, int(round(height * SCALE)))
    yy, xx = np.mgrid[0:pixel_h, 0:pixel_w]
    dx = (xx - (pixel_w - 1) / 2.0) / (pixel_w / 2.0)
    dy = (yy - (pixel_h - 1) / 2.0) / (pixel_h / 2.0)
    inside = (dx * dx + dy * dy) <= 1.0
    t = np.clip((dy + 1.0) / 2.0, 0.0, 1.0)[..., None]
    color = np.array(top_rgb, dtype=np.float32) * (1.0 - t) + np.array(bottom_rgb, dtype=np.float32) * t
    out = np.zeros((pixel_h, pixel_w, 4), dtype=np.uint8)
    out[inside, :3] = np.clip(np.round(color[inside]), 0, 255).astype(np.uint8)
    out[inside, 3] = 255
    return Image.fromarray(out, 'RGBA')


def _draw_teeth(image: Image.Image, anchor, width: float, height: float, mask: Image.Image) -> None:
    tooth_w = width * 0.46
    tooth_h = max(5.0, height * 0.16)
    layer = Image.new('RGBA', image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    cx, cy = anchor
    x0 = cx - tooth_w * SCALE / 2.0
    x1 = cx + tooth_w * SCALE / 2.0
    y0 = cy - height * 0.30 * SCALE
    y1 = y0 + tooth_h * SCALE
    radius = tooth_h * SCALE * 0.45
    draw.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=TEETH + (255,))
    gap = max(1.0, 1.3 * SCALE)
    draw.rectangle((cx - gap / 2.0, y0 + 1, cx + gap / 2.0, y1 - 1), fill=MOUTH_INTERIOR_DEEP + (255,))
    image.alpha_composite(_clip(layer, mask))


def _feature_sprites(style: dict) -> dict:
    return {
        'brow_left': _make_brow(style, 'left'),
        'brow_right': _make_brow(style, 'right'),
        'eye_left_open': _make_eye(style, 'left', False),
        'eye_right_open': _make_eye(style, 'right', False),
        'eye_left_shut': _make_eye(style, 'left', True),
        'eye_right_shut': _make_eye(style, 'right', True),
        'nose': _make_nose(style),
        'smile': _make_smile(style),
        'talk_mid': _make_open_mouth(style, 'talk_mid'),
        'talk_open': _make_open_mouth(style, 'talk_open'),
    }


def _anchor(name: str) -> tuple[float, float]:
    u, v = FEATURES[name]
    return u * CELL, v * CELL


def render_cell(style: dict, sprites: dict, expression: str, skin: tuple[int, int, int]) -> Image.Image:
    cell = Image.new('RGBA', (CELL, CELL), skin + (255,))
    left = _anchor('left_eye')
    right = _anchor('right_eye')
    outward = 3.0
    _paste(cell, sprites['brow_left'], (left[0] - outward, left[1] - style['brow_gap']))
    _paste(cell, sprites['brow_right'], (right[0] + outward, right[1] - style['brow_gap']))
    shut = expression == 'blink'
    _paste(cell, sprites['eye_left_shut' if shut else 'eye_left_open'], left)
    _paste(cell, sprites['eye_right_shut' if shut else 'eye_right_open'], right)
    _paste(cell, sprites['nose'], _anchor('nose'))
    mouth_key = 'smile' if expression in ('neutral', 'blink') else expression
    _paste(cell, sprites[mouth_key], _anchor('mouth'))
    return cell.convert('RGB')


def _diff_boxes(style: dict, kind: str) -> list[tuple[int, int, int, int]]:
    """Rectangles allowed to change between neutral and this expression."""
    pad = 6
    if kind == 'blink':
        boxes = []
        for name in ('left_eye', 'right_eye'):
            cx, cy = _anchor(name)
            half_w = style['eye_w'] / 2.0 + style['wing'] + pad
            half_h = style['eye_h'] / 2.0 + 14
            boxes.append((int(cx - half_w), int(cy - half_h), int(cx + half_w) + 1, int(cy + half_h) + 1))
        return boxes
    width, height = style['mouth_open']
    cx, cy = _anchor('mouth')
    half_w = width / 2.0 + pad + 4
    half_h = height / 2.0 + pad + 4
    return [(int(cx - half_w), int(cy - half_h), int(cx + half_w) + 1, int(cy + half_h) + 1)]


def _assert_alignment(cells: dict[str, Image.Image], style: dict, name: str) -> None:
    neutral = np.asarray(cells['neutral'])
    skin = np.array(style['skin'], dtype=np.uint8)
    border = np.concatenate([
        neutral[:36, :, :].reshape(-1, 3),
        neutral[-36:, :, :].reshape(-1, 3),
        neutral[:, :36, :].reshape(-1, 3),
        neutral[:, -36:, :].reshape(-1, 3),
    ])
    if not np.equal(border, skin).all():
        raise RuntimeError('%s atlas margin is not flat skin' % name)
    for expression in ('blink', 'talk_mid', 'talk_open'):
        other = np.asarray(cells[expression])
        differ = np.any(neutral != other, axis=2)
        allowed = np.zeros(differ.shape, dtype=bool)
        for x0, y0, x1, y1 in _diff_boxes(style, 'blink' if expression == 'blink' else 'mouth'):
            allowed[max(0, y0):y1, max(0, x0):x1] = True
        stray = int(np.logical_and(differ, ~allowed).sum())
        if stray:
            raise RuntimeError('%s %s moves %d pixels outside its feature box' % (name, expression, stray))
        if expression == 'blink':
            # Mouth, nose, and brows stay put when the eyes close.
            mouth = _diff_boxes(style, 'mouth')[0]
            if np.any(neutral[mouth[1]:mouth[3], mouth[0]:mouth[2]] != other[mouth[1]:mouth[3], mouth[0]:mouth[2]]):
                raise RuntimeError('%s blink changed the mouth' % name)
        else:
            for box in _diff_boxes(style, 'blink'):
                if np.any(neutral[box[1]:box[3], box[0]:box[2]] != other[box[1]:box[3], box[0]:box[2]]):
                    raise RuntimeError('%s %s changed an eye' % (name, expression))


def _assert_no_hair(cell: Image.Image, skin: tuple[int, int, int]) -> None:
    arr = np.asarray(cell).astype(np.int16)
    dark = np.all(arr < 18, axis=2)
    # Ink is dark, but it must stay in the feature cluster, never the outer margin.
    dark[:48, :] = False
    dark[-48:, :] = False
    dark[:, :48] = False
    dark[:, -48:] = False
    if int(dark.sum()) > 8000:
        raise RuntimeError('too much near-black paint for a skin-filled face cell')
    margin = arr[:48, :48]
    if not np.equal(margin, np.array(skin, dtype=np.int16)).all():
        raise RuntimeError('corner skin drifted from %s' % (skin,))


def build_character(name: str) -> tuple[Image.Image, dict]:
    spec = dict(CHARACTERS[name])
    source = spec['source']
    if not source.is_file():
        raise FileNotFoundError('canonical plate missing: %s' % source)
    skin = sample_skin(source, spec['cheek_box'])
    spec['skin'] = skin
    sprites = _feature_sprites(spec)
    cells = {expression: render_cell(spec, sprites, expression, skin) for expression, _col, _row in EXPRESSIONS}
    _assert_alignment(cells, spec, name)
    for cell in cells.values():
        _assert_no_hair(cell, skin)
    atlas = Image.new('RGB', (ATLAS, ATLAS), skin)
    for expression, col, row in EXPRESSIONS:
        atlas.paste(cells[expression], (col * CELL, row * CELL))
    meta = {
        'atlas': 'media/assets3d/textures/%s_face_atlas.png' % name,
        'source': source.relative_to(ROOT).as_posix(),
        'grid': [2, 2],
        'cell_px': CELL,
        'atlas_px': ATLAS,
        'y_axis': 'down',
        'cells': {expression: [col, row] for expression, col, row in EXPRESSIONS},
        'left_eye': [FEATURES['left_eye'][0], FEATURES['left_eye'][1]],
        'right_eye': [FEATURES['right_eye'][0], FEATURES['right_eye'][1]],
        'nose': [FEATURES['nose'][0], FEATURES['nose'][1]],
        'mouth': [FEATURES['mouth'][0], FEATURES['mouth'][1]],
        'skin_rgb': {'hex': _hex(skin), 'rgb': list(skin)},
    }
    return atlas, meta


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    document = {}
    for name in ('linlan', 'chen'):
        atlas, meta = build_character(name)
        path = OUT_DIR / ('%s_face_atlas.png' % name)
        atlas.save(path, 'PNG')
        document[name] = meta
        print('%s %s skin %s' % (name, path.relative_to(ROOT).as_posix(), meta['skin_rgb']['hex']))
    meta_path = OUT_DIR / 'face_atlas_meta.json'
    meta_path.write_text(json.dumps(document, indent=2) + '\n', encoding='utf-8')
    print(meta_path.relative_to(ROOT).as_posix())


if __name__ == '__main__':
    main()
