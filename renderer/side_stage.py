"""Side-view staging for a walk, a short weapon, and a hit.

Design 1 composites the flash-doll capsules, then strokes only the outer
silhouette. Collar, belt, and boot sole come from the side plate. The head is
cropped from that same plate and the green fringe is keyed out. The weapon is
its own sprite, gripped on the far hand.

Later designs live in this module too, but each one draws new shapes. They do
not load an earlier design's part images.

Front, side, and back are the same figure: one set of head marks, the same
bone lengths, and the same costume colors. ``render_facing`` picks which of
those drawings to use.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from renderer.flash_doll import (  # noqa: E402
    CAST,
    _ipt,
    _joint,
    _load_rgba,
    _median_color,
    _over,
    _radii,
    _rgb,
    _scale_sprite,
    _solve,
    _trim,
    _view_meta,
    _warp_stamp,
    crop_head,
    make_limb,
    make_mitten,
    make_shoe_side,
    make_torso_side,
    measure_bones,
    rotate_cw,
)
from renderer.mux import ffmpeg_executable  # noqa: E402

FPS = 24
TARGET_H = 640.0
REVIEW = ROOT / "media" / "m6_interact" / "review"
ROVER_FILE = ROOT / "media" / "m4_e2e_episode" / "assets" / "at1_master.png"
FONT_PATH = Path(r"C:\Windows\Fonts\msyh.ttc")

# One short step is four beats. Two steps return to the same contact pose.
BEAT = 3
CYCLE_FRAMES = 8 * BEAT  # 24, plus the repeated contact makes 25 images

WALK_A = (
    ("contact", {
        "thigh_l": -34.0, "shin_l": 6.0,
        "thigh_r": 24.0, "shin_r": 8.0,
        "upper_arm_l": 28.0, "lower_arm_l": 10.0,
        "upper_arm_r": -12.0, "lower_arm_r": 6.0,
        "torso": 4.0,
    }),
    ("down", {
        "thigh_l": -18.0, "shin_l": 30.0,
        "thigh_r": 10.0, "shin_r": 64.0,
        "upper_arm_l": 12.0, "lower_arm_l": 8.0,
        "upper_arm_r": -5.0, "lower_arm_r": 4.0,
        "torso": 2.0,
    }),
    ("passing", {
        "thigh_l": 2.0, "shin_l": 8.0,
        "thigh_r": -6.0, "shin_r": 76.0,
        "upper_arm_l": -2.0, "lower_arm_l": 4.0,
        "upper_arm_r": 2.0, "lower_arm_r": 2.0,
        "torso": 0.0,
    }),
    ("up", {
        "thigh_l": 16.0, "shin_l": 10.0,
        "thigh_r": -28.0, "shin_r": 18.0,
        "upper_arm_l": -24.0, "lower_arm_l": 8.0,
        "upper_arm_r": 10.0, "lower_arm_r": 4.0,
        "torso": -2.0,
    }),
)


def _font(size: int) -> ImageFont.ImageFont:
    if FONT_PATH.is_file():
        return ImageFont.truetype(str(FONT_PATH), size)
    return ImageFont.load_default()


def _lerp(a: float, b: float, t: float) -> float:
    eased = 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, t)))
    return a + (b - a) * eased


def _lerp_spec(a: dict, b: dict, t: float) -> dict:
    keys = set(a) | set(b)
    out = {}
    for key in keys:
        if key == "hip":
            continue
        out[key] = _lerp(float(a.get(key, 0.0)), float(b.get(key, 0.0)), t)
    return out


def _mirror_step(spec: dict) -> dict:
    """Other foot forward. The empty arm keeps the wider swing."""
    return {
        "thigh_l": spec["thigh_r"], "shin_l": spec["shin_r"],
        "thigh_r": spec["thigh_l"], "shin_r": spec["shin_l"],
        "upper_arm_l": -spec["upper_arm_l"], "lower_arm_l": spec["lower_arm_l"],
        "upper_arm_r": -spec["upper_arm_r"], "lower_arm_r": spec["lower_arm_r"],
        "torso": -spec.get("torso", 0.0),
    }


def _plant_foot(spec: dict, side: str) -> dict:
    """Keep the planted sole level."""
    out = dict(spec)
    out[f"foot_{side}"] = -(float(out.get(f"thigh_{side}", 0.0)) + float(out.get(f"shin_{side}", 0.0)))
    return out


def _walk_feet(spec: dict, stance: str) -> dict:
    """Planted sole stays flat. The swing toe stays lifted instead of spearing the floor."""
    out = _plant_foot(spec, stance)
    other = "r" if stance == "l" else "l"
    out[f"foot_{other}"] = 22.0 - (float(out.get(f"thigh_{other}", 0.0)) + float(out.get(f"shin_{other}", 0.0)))
    return out


def _plant_both(spec: dict) -> dict:
    return _plant_foot(_plant_foot(spec, "l"), "r")


def walk_keys() -> list[tuple[int, dict, str]]:
    keys: list[tuple[int, dict, str]] = []
    for step, stance in ((0, "l"), (1, "r")):
        for beat, (name, spec) in enumerate(WALK_A):
            posed = spec if step == 0 else _mirror_step(spec)
            keys.append((step * 4 * BEAT + beat * BEAT, _walk_feet(posed, stance), stance))
    keys.append((CYCLE_FRAMES, dict(keys[0][1]), "l"))
    return keys


def walk_frame_spec(index: int) -> tuple[dict, str]:
    keys = walk_keys()
    index = max(0, min(CYCLE_FRAMES, index))
    for (t0, spec0, stance0), (t1, spec1, stance1) in zip(keys, keys[1:]):
        if index <= t1:
            span = max(1, t1 - t0)
            spec = _lerp_spec(spec0, spec1, (index - t0) / span)
            stance = stance0 if index < t1 else stance1
            return _walk_feet(spec, stance), stance
    return dict(keys[0][1]), "l"


IDLE = _plant_both({
    "thigh_l": 0.0, "shin_l": 0.0,
    "thigh_r": 0.0, "shin_r": 0.0,
    "upper_arm_l": 8.0, "lower_arm_l": 6.0,
    "upper_arm_r": 11.0, "lower_arm_r": 7.0,
    "torso": 0.0,
})

WINDUP = _plant_both({
    "thigh_l": 0.0, "shin_l": 0.0,
    "thigh_r": 0.0, "shin_r": 0.0,
    "upper_arm_l": -18.0, "lower_arm_l": 12.0,
    "upper_arm_r": 98.0, "lower_arm_r": 46.0,
    "torso": -12.0,
})

STRIKE = _plant_both({
    "thigh_l": 0.0, "shin_l": 0.0,
    "thigh_r": 0.0, "shin_r": 0.0,
    "upper_arm_l": 36.0, "lower_arm_l": 14.0,
    "upper_arm_r": -60.0, "lower_arm_r": -22.0,
    "torso": 8.0,
})

GUARD = _plant_both({
    "thigh_l": 0.0, "shin_l": 0.0,
    "thigh_r": 0.0, "shin_r": 0.0,
    "upper_arm_l": -16.0, "lower_arm_l": 22.0,
    "upper_arm_r": -50.0, "lower_arm_r": -44.0,
    "torso": -3.0,
})

HIT = _plant_both({
    "thigh_l": 0.0, "shin_l": 0.0,
    "thigh_r": 0.0, "shin_r": 0.0,
    "upper_arm_l": 24.0, "lower_arm_l": 18.0,
    "upper_arm_r": 16.0, "lower_arm_r": 22.0,
    "torso": -22.0,
})


def attack_frames() -> list[tuple[dict, str, str]]:
    """6 windup, 3 strike, 8 recover. Weapon is behind only while winding up."""
    frames = []
    for index in range(6):
        frames.append((_lerp_spec(IDLE, WINDUP, index / 5.0), "back", "windup"))
    for index in range(3):
        frames.append((_lerp_spec(WINDUP, STRIKE, (index + 1) / 3.0), "front", "strike"))
    for index in range(8):
        frames.append((_lerp_spec(STRIKE, IDLE, (index + 1) / 8.0), "front", "recover"))
    return frames


def hit_frames() -> list[dict]:
    frames = []
    for index in range(4):
        frames.append(_lerp_spec(IDLE, HIT, (index + 1) / 4.0))
    for index in range(4):
        frames.append(_lerp_spec(HIT, IDLE, (index + 1) / 4.0))
    return frames


def key_green_fringe(image: np.ndarray) -> np.ndarray:
    """Drop leftover screen green and pull the green spill off the cut edge."""
    out = np.array(image, copy=True)
    rgb = out[:, :, :3].astype(np.float32)
    red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    excess = green - np.maximum(red, blue)
    screen = (excess > 22.0) & (green > 70.0) & (green > red + 16.0) & (green > blue + 16.0)
    out[:, :, 3] = np.where(screen, 0, out[:, :, 3])
    alpha = out[:, :, 3]
    clear = (alpha < 20).astype(np.uint8)
    edge = (cv2.dilate(clear, np.ones((3, 3), np.uint8), iterations=1) > 0) & (alpha > 0)
    spill = edge & (excess > 6.0)
    cleaned = np.where(spill, np.maximum(red, blue), green)
    out[:, :, 1] = np.clip(cleaned, 0, 255).astype(np.uint8)
    return out


def _band_pixels(image: np.ndarray, y0: float, y1: float, half: float) -> np.ndarray:
    alpha = image[:, :, 3] > 40
    ys, xs = np.where(alpha)
    if len(xs) == 0:
        return np.zeros((0, 3), np.uint8)
    top, bottom = int(ys.min()), int(ys.max())
    height = max(1, bottom - top)
    center = float(np.median(xs))
    y_start = int(top + y0 * height)
    y_end = int(top + y1 * height)
    x_start = int(center - half * height)
    x_end = int(center + half * height)
    tile = image[y_start:y_end, x_start:x_end]
    if tile.size == 0:
        return np.zeros((0, 3), np.uint8)
    mask = tile[:, :, 3] > 40
    if not np.any(mask):
        return np.zeros((0, 3), np.uint8)
    return tile[:, :, :3][mask]


def _blue_median(pixels: np.ndarray, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    if len(pixels) < 12:
        return fallback
    red = pixels[:, 0].astype(np.int16)
    blue = pixels[:, 2].astype(np.int16)
    kept = pixels[(blue > red + 8) & (blue > 40) & (red < 140) & (pixels.max(axis=1) > 28)]
    if len(kept) < 12:
        kept = pixels[pixels.max(axis=1) > 28]
    return _median_color(kept, fallback)


def _accent(pixels: np.ndarray, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    if len(pixels) < 12:
        return fallback
    red = pixels[:, 0].astype(np.int16)
    green = pixels[:, 1].astype(np.int16)
    blue = pixels[:, 2].astype(np.int16)
    orange = pixels[(red > 150) & (red > green + 25) & (red > blue + 40) & (blue < 140) & (green > 40)]
    if len(orange) > 40:
        return _median_color(orange, fallback)
    return fallback


def sample_side_master(image: np.ndarray) -> dict[str, tuple[int, int, int]]:
    """Colors from the side plate only. The front plate is not read."""
    chest = _band_pixels(image, 0.22, 0.40, 0.16)
    thigh = _band_pixels(image, 0.56, 0.74, 0.14)
    boot_px = _band_pixels(image, 0.88, 0.995, 0.18)
    neck = _band_pixels(image, 0.15, 0.24, 0.10)
    waist = _band_pixels(image, 0.46, 0.54, 0.12)
    hands = _band_pixels(image, 0.36, 0.52, 0.22)
    jacket = _blue_median(chest, (48, 96, 120))
    pants = _blue_median(thigh, jacket)
    boot = _median_color(boot_px[boot_px.max(axis=1) > 24] if len(boot_px) else boot_px, (40, 90, 70))
    collar = _accent(neck, tuple(int(channel * 0.78) for channel in jacket))
    glove = _accent(hands, (232, 196, 150))
    if glove == (232, 196, 150):
        skin_px = hands
        if len(skin_px):
            red = skin_px[:, 0].astype(np.int16)
            green = skin_px[:, 1].astype(np.int16)
            blue = skin_px[:, 2].astype(np.int16)
            warm = skin_px[(red > 170) & (green > 120) & (blue > 70) & (red > blue)]
            glove = _median_color(warm, glove)
    dark = waist[waist.mean(axis=1) < 55] if len(waist) else waist
    belt = _median_color(dark, (28, 32, 40))
    ink_px = image[:, :, :3][(image[:, :, 3] > 200) & (image[:, :, :3].max(axis=2) < 42)]
    ink = _rgb(np.median(ink_px, axis=0)) if len(ink_px) > 20 else (16, 18, 22)
    sole = tuple(int(channel * 0.62) for channel in boot)
    return {
        "jacket": jacket,
        "pants": pants,
        "boot": boot,
        "sole": sole,
        "collar": _rgb(collar),
        "belt": belt,
        "glove": _rgb(glove),
        "ink": ink,
    }


def _darken(color, scale: float) -> tuple[int, int, int]:
    return tuple(int(max(0, min(255, channel * scale))) for channel in _rgb(color))


def _paint_where(sprite: np.ndarray, mask: np.ndarray, color) -> None:
    sprite[mask, 0] = color[0]
    sprite[mask, 1] = color[1]
    sprite[mask, 2] = color[2]
    sprite[mask, 3] = np.maximum(sprite[mask, 3], 255)


def _decorate_torso(sprite: np.ndarray, pivot, neck_local, colors: dict, height: float) -> None:
    opaque = sprite[:, :, 3] > 128
    neck_x = int(round(pivot[0] + neck_local[0]))
    neck_y = int(round(pivot[1] + neck_local[1]))
    band = max(4, int(round(0.05 * height)))
    rows = np.arange(sprite.shape[0])[:, None]
    collar = opaque & (rows >= neck_y) & (rows < neck_y + band)
    _paint_where(sprite, collar, colors["collar"])
    line = opaque & (rows >= neck_y + band - 2) & (rows < neck_y + band + 1)
    _paint_where(sprite, line, colors["ink"])
    belt_y = int(round(pivot[1] - 0.11 * height))
    belt_h = max(5, int(round(0.03 * height)))
    belt = opaque & (rows >= belt_y) & (rows < belt_y + belt_h)
    _paint_where(sprite, belt, colors["belt"])
    buckle = belt & (np.arange(sprite.shape[1])[None, :] > neck_x + 0.01 * height)
    buckle = buckle & (np.arange(sprite.shape[1])[None, :] < neck_x + 0.045 * height)
    _paint_where(sprite, buckle, _darken(colors["belt"], 1.45))


def _decorate_boot(sprite: np.ndarray, colors: dict) -> None:
    opaque = sprite[:, :, 3] > 128
    ys = np.where(opaque)[0]
    if len(ys) == 0:
        return
    top, bottom = int(ys.min()), int(ys.max())
    sole_top = bottom - max(3, int(round(0.30 * (bottom - top + 1))))
    rows = np.arange(sprite.shape[0])[:, None]
    _paint_where(sprite, opaque & (rows >= sole_top), colors["sole"])


def _recenter_pivot(image: np.ndarray, pivot) -> tuple[np.ndarray, tuple[float, float]]:
    ys, xs = np.where(image[:, :, 3] > 32)
    if len(xs) == 0:
        return image, pivot
    return image, (float(np.mean(xs)), float(np.mean(ys)))


def _part(name, parent, pivot, offset, image, kind, z, length) -> dict:
    return {
        "name": name,
        "parent": parent,
        "pivot": (float(pivot[0]), float(pivot[1])),
        "rest": 0.0,
        "offset": (float(offset[0]), float(offset[1])),
        "image": image,
        "kind": kind,
        "z": z,
        "length": float(length),
        "group": kind,
    }


def _side_bones(folder: Path) -> dict:
    bones = measure_bones(folder)
    height = float(bones["H"])
    bones["shoulder_half"] = max(float(bones["shoulder_half"]), 0.125 * height)
    bones["hip_half"] = max(float(bones["hip_half"]), 0.070 * height)
    bones["shoulder_forward"] = 0.035 * height
    bones["upper_arm"] = 0.175 * height
    bones["forearm"] = 0.150 * height
    bones["hip_to_shoulder"] = min(max(float(bones["hip_to_shoulder"]), 0.22 * height), 0.30 * height)
    scale = TARGET_H / height
    for key in (
        "H", "top", "feet_y", "center_x", "hip_to_neck", "hip_to_shoulder",
        "upper_arm", "forearm", "thigh", "shin", "shoulder_half", "hip_half", "shoulder_forward",
    ):
        bones[key] = float(bones[key]) * scale
    return bones


def _crop_side_head(folder: Path, scale: float) -> tuple[np.ndarray, tuple[float, float]]:
    meta = _view_meta(folder, "three_quarter")
    image = key_green_fringe(_load_rgba(folder / meta["file"]))
    neck = _joint(meta, "head")
    if neck is None:
        raise RuntimeError(f"{folder.name} side plate has no neck joint")
    mouth = (meta.get("anchors") or {}).get("mouth") or {}
    body_h = float(meta["body_height"])
    if mouth:
        chin_y = float(mouth["y"] + mouth["h"]) + 0.018 * body_h
    else:
        chin_y = float(neck[1]) - 0.035 * body_h
    cropped = crop_head(image, neck, chin_y, max(14.0, 0.024 * body_h))
    if cropped is None:
        raise RuntimeError(f"{folder.name} side head crop failed")
    part, pivot, _rect = cropped
    part = key_green_fringe(part)
    part, pivot = _scale_sprite(part, pivot, scale)
    return _trim(part, pivot, pad=1)


def make_weapon(height: float, ink) -> tuple[np.ndarray, tuple[float, float], tuple[float, float]]:
    """Short blade. Local +y runs from the grip toward the tip, same as a hanging limb."""
    length = 0.30 * height
    pommel = 0.035 * height
    guard = 0.028 * height
    half = 0.020 * height
    stroke = max(2.0, 0.004 * height)
    pad = stroke + 4.0
    width = int(math.ceil(guard * 2 + pad * 2))
    grip_y = pad + pommel
    img_h = int(math.ceil(grip_y + length + pad))
    pivot = (width / 2.0, grip_y)
    fill = np.zeros((img_h, width), np.uint8)
    edge = np.zeros((img_h, width), np.uint8)

    def paint(mask: np.ndarray, extra: float) -> None:
        cv2.circle(mask, _ipt(pivot), max(2, int(round(half * 0.7 + extra))), 255, -1, lineType=cv2.LINE_AA)
        _blade = np.array([
            [pivot[0] - half * 0.55 - extra, pivot[1] + half],
            [pivot[0] + half * 0.55 + extra, pivot[1] + half],
            [pivot[0] + half * 0.22 + extra, pivot[1] + length * 0.78],
            [pivot[0], pivot[1] + length],
            [pivot[0] - half * 0.22 - extra, pivot[1] + length * 0.78],
        ], np.float64)
        cv2.fillPoly(mask, [_blade.astype(np.int32)], 255, lineType=cv2.LINE_AA)
        cv2.line(
            mask,
            _ipt((pivot[0] - guard, pivot[1])),
            _ipt((pivot[0] + guard, pivot[1])),
            255, max(2, int(round(half * 0.55 + extra))), cv2.LINE_AA,
        )

    paint(edge, stroke)
    paint(fill, 0.0)
    blade = np.array((214, 218, 224), np.float32)
    ink_rgb = np.array(_rgb(ink), np.float32)
    grip_color = np.array((92, 64, 46), np.float32)
    fill_a = fill.astype(np.float32) / 255.0
    edge_a = edge.astype(np.float32) / 255.0
    alpha = np.maximum(fill_a, edge_a)
    rgb = np.zeros(fill.shape + (3,), np.float32)
    rgb[:] = ink_rgb
    rgb = rgb * (1.0 - fill_a[..., None]) + blade * fill_a[..., None]
    grip_band = (np.arange(img_h)[:, None] > pivot[1] - pommel) & (np.arange(img_h)[:, None] < pivot[1] + half * 0.8)
    rgb = np.where((grip_band & (fill > 0))[..., None], grip_color, rgb)
    sprite = np.zeros(fill.shape + (4,), np.uint8)
    sprite[:, :, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    sprite[:, :, 3] = np.clip(alpha * 255.0, 0, 255).astype(np.uint8)
    tip = (0.0, length)
    sprite, pivot = _trim(sprite, pivot, pad=1)
    return sprite, pivot, tip


def _save_part(folder: Path, name: str, image: np.ndarray) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.ascontiguousarray(image), "RGBA").save(folder / f"{name}.png")


def build_silhouette(name: str, part_dir: Path) -> dict:
    """Design 1. Capsules are filled, overlapped, then outlined once."""
    folder = CAST / name
    bones = _side_bones(folder)
    meta = _view_meta(folder, "three_quarter")
    master = _load_rgba(folder / meta["file"])
    colors = sample_side_master(master)
    rad = _radii(bones, "side")
    height = bones["H"]
    ink = colors["ink"]
    # Parts themselves carry no outline. The silhouette stroke is applied later.
    torso, torso_pivot, neck_local = make_torso_side(bones, rad, colors["jacket"], ink, 0.0)
    _decorate_torso(torso, torso_pivot, neck_local, colors, height)
    head, head_pivot = _crop_side_head(folder, TARGET_H / float(meta["body_height"]))
    head = key_green_fringe(head)
    weapon, weapon_pivot, weapon_tip = make_weapon(height, ink)
    parts = [
        _part("torso", "pelvis", torso_pivot, (0.0, 0.0), torso, "torso", 7, bones["hip_to_neck"]),
        _part("head", "torso", head_pivot, neck_local, head, "head", 14, 0.0),
    ]
    limb_draw = (
        ("upper_arm", bones["upper_arm"], rad["shoulder"], rad["elbow"], colors["jacket"]),
        ("lower_arm", bones["forearm"], rad["elbow"], rad["wrist"], colors["jacket"]),
        ("thigh", bones["thigh"], rad["hip"], rad["knee"], colors["pants"]),
        ("shin", bones["shin"], rad["knee"], rad["ankle"], colors["pants"]),
    )
    sprites = {}
    for limb_name, length, r0, r1, color in limb_draw:
        sprites[limb_name] = make_limb(length, r0, r1, color, ink, 0.0)
    shoulder = (bones["shoulder_forward"], -bones["hip_to_shoulder"])
    for side, z_arm, z_leg in (("r", 1, 4), ("l", 11, 8)):
        thumb = -1.0 if side == "l" else 1.0
        hand, hand_pivot = make_mitten(height, rad["wrist"], colors["glove"], ink, 0.0, thumb)
        hand, hand_pivot = _recenter_pivot(hand, hand_pivot)
        shoe, shoe_pivot = make_shoe_side(height, rad["ankle"], colors["boot"], ink, 0.0)
        _decorate_boot(shoe, colors)
        upper, upper_pivot = sprites["upper_arm"]
        lower, lower_pivot = sprites["lower_arm"]
        thigh, thigh_pivot = sprites["thigh"]
        shin, shin_pivot = sprites["shin"]
        parts.extend((
            _part(f"upper_arm_{side}", "torso", upper_pivot, shoulder, upper, "arm", z_arm, bones["upper_arm"]),
            _part(f"lower_arm_{side}", f"upper_arm_{side}", lower_pivot, (0.0, bones["upper_arm"]), lower, "arm", z_arm + 1, bones["forearm"]),
            _part(f"hand_{side}", f"lower_arm_{side}", hand_pivot, (0.0, bones["forearm"]), hand, "hand", z_arm + 2, 0.0),
            _part(f"thigh_{side}", "pelvis", thigh_pivot, (0.0, 0.0), thigh, "leg", z_leg, bones["thigh"]),
            _part(f"shin_{side}", f"thigh_{side}", shin_pivot, (0.0, bones["thigh"]), shin, "leg", z_leg + 1, bones["shin"]),
            _part(f"foot_{side}", f"shin_{side}", shoe_pivot, (0.0, bones["shin"]), shoe, "foot", z_leg + 2, 0.0),
        ))
    for part in parts:
        _save_part(part_dir, part["name"], part["image"])
    _save_part(part_dir, "weapon", weapon)
    return {
        "name": name,
        "bones": bones,
        "colors": colors,
        "parts": parts,
        "weapon": weapon,
        "weapon_pivot": weapon_pivot,
        "weapon_tip": weapon_tip,
        "height": height,
        "stroke": max(3, int(round(0.007 * height))),
        "design": "silhouette",
    }


def _sprite_points(sprite: np.ndarray, pivot, world, degrees: float) -> tuple[np.ndarray, np.ndarray]:
    ys, xs = np.where(sprite[:, :, 3] > 140)
    if len(xs) == 0:
        return np.zeros(0), np.zeros(0)
    step = max(1, len(xs) // 4000)
    dx = xs[::step].astype(np.float64) - float(pivot[0])
    dy = ys[::step].astype(np.float64) - float(pivot[1])
    theta = math.radians(degrees)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    rx = dx * cos_t - dy * sin_t + float(world[0])
    ry = dx * sin_t + dy * cos_t + float(world[1])
    return rx, ry


def _sole_point(sprite, pivot, world, degrees) -> tuple[float, float] | None:
    rx, ry = _sprite_points(sprite, pivot, world, degrees)
    if len(rx) == 0:
        return None
    bottom = float(ry.max())
    kept = rx[ry >= bottom - 2.0]
    return float(np.median(kept)), bottom


def _bounds_of(sprite, pivot, world, degrees) -> tuple[float, float, float, float] | None:
    rx, ry = _sprite_points(sprite, pivot, world, degrees)
    if len(rx) == 0:
        return None
    return float(rx.min()), float(ry.min()), float(rx.max()), float(ry.max())


def _depth(part: dict, pos: dict, hip_x: float, height: float) -> tuple[int, int]:
    if part["kind"] in ("torso", "head"):
        return (1 if part["kind"] == "torso" else 3), part["z"]
    side = part["name"][-1]
    end_name = f"hand_{side}" if part["kind"] in ("arm", "hand") else f"foot_{side}"
    end = pos.get(end_name, pos[part["name"]])
    tol = 0.02 * height
    if float(end[0]) > hip_x + tol:
        layer = 2
    elif float(end[0]) < hip_x - tol:
        layer = 0
    else:
        layer = 2 if side == "l" else 0
    return layer, part["z"]


def _stroke_outside(image: np.ndarray, width: int, ink) -> np.ndarray:
    mask = (image[:, :, 3] > 128).astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (width * 2 + 1, width * 2 + 1))
    fat = cv2.dilate(mask, kernel)
    ring = (fat > 0) & (mask == 0)
    out = np.zeros_like(image)
    out[ring, 0] = ink[0]
    out[ring, 1] = ink[1]
    out[ring, 2] = ink[2]
    out[ring, 3] = 255
    _over(out, image)
    return out


def _crop_frame(image: np.ndarray, points: dict, pad: int = 4) -> tuple[np.ndarray, dict]:
    ys, xs = np.where(image[:, :, 3] > 8)
    if len(xs) == 0:
        return image, points
    x0 = max(0, int(xs.min()) - pad)
    y0 = max(0, int(ys.min()) - pad)
    x1 = min(image.shape[1], int(xs.max()) + pad + 1)
    y1 = min(image.shape[0], int(ys.max()) + pad + 1)
    cropped = np.ascontiguousarray(image[y0:y1, x0:x1])

    def shift(point):
        if point is None:
            return None
        return (float(point[0]) - x0, float(point[1]) - y0)

    moved = {}
    for key, value in points.items():
        if key == "torso_box" and value is not None:
            moved[key] = (value[0] - x0, value[1] - y0, value[2] - x0, value[3] - y0)
        elif key == "soles":
            moved[key] = {name: shift(point) for name, point in value.items()}
        else:
            moved[key] = shift(value) if isinstance(value, tuple) else value
    return cropped, moved


def render_character(actor: dict, spec: dict, weapon_mode: str = "follow") -> dict:
    angles = {key: float(value) for key, value in spec.items() if key != "hip"}
    pos, ang = _solve(actor["parts"], angles, 0.0)
    height = actor["height"]
    hip_x = float(pos["pelvis"][0])
    hand = pos["hand_r"]
    tip_local = actor["weapon_tip"]
    tx, ty = rotate_cw(tip_local[0], tip_local[1], ang["hand_r"])
    tip = hand + np.array([tx, ty])
    if weapon_mode == "back":
        weapon_front = False
    elif weapon_mode == "front":
        weapon_front = True
    else:
        weapon_front = float(hand[0]) > hip_x + 0.02 * height

    drawn = [part for part in actor["parts"] if part["kind"] != "head"]
    drawn.sort(key=lambda part: _depth(part, pos, hip_x, height))
    head = next(part for part in actor["parts"] if part["name"] == "head")
    corners = []
    for part in list(drawn) + [head]:
        h, w = part["image"].shape[:2]
        px, py = part["pivot"]
        for x, y in ((0, 0), (w - 1, 0), (w - 1, h - 1), (0, h - 1)):
            rx, ry = rotate_cw(x - px, y - py, ang[part["name"]])
            corners.append((pos[part["name"]][0] + rx, pos[part["name"]][1] + ry))
    wh, ww = actor["weapon"].shape[:2]
    wpx, wpy = actor["weapon_pivot"]
    for x, y in ((0, 0), (ww - 1, 0), (ww - 1, wh - 1), (0, wh - 1)):
        rx, ry = rotate_cw(x - wpx, y - wpy, ang["hand_r"])
        corners.append((hand[0] + rx, hand[1] + ry))
    xs = [point[0] for point in corners]
    ys = [point[1] for point in corners]
    margin = actor["stroke"] + 12
    min_x, min_y = math.floor(min(xs)) - margin, math.floor(min(ys)) - margin
    max_x, max_y = math.ceil(max(xs)) + margin, math.ceil(max(ys)) + margin
    canvas = np.zeros((max_y - min_y + 1, max_x - min_x + 1, 4), np.uint8)
    origin = np.array([min_x, min_y], np.float64)
    body = np.zeros_like(canvas)

    def stamp(target, sprite, pivot, world, degrees):
        _warp_stamp(target, sprite, pivot, world - origin, degrees)

    if not weapon_front:
        stamp(canvas, actor["weapon"], actor["weapon_pivot"], hand, ang["hand_r"])
    for part in drawn:
        stamp(body, part["image"], part["pivot"], pos[part["name"]], ang[part["name"]])
    body = _stroke_outside(body, actor["stroke"], actor["colors"]["ink"])
    stamp(body, head["image"], head["pivot"], pos["head"], ang["head"])
    _over(canvas, body)
    if weapon_front:
        stamp(canvas, actor["weapon"], actor["weapon_pivot"], hand, ang["hand_r"])

    torso = next(part for part in actor["parts"] if part["name"] == "torso")
    soles = {}
    for side in ("l", "r"):
        foot = next(part for part in actor["parts"] if part["name"] == f"foot_{side}")
        soles[side] = _sole_point(foot["image"], foot["pivot"], pos[foot["name"]] - origin, ang[foot["name"]])
    info = {
        "pelvis": tuple(pos["pelvis"] - origin),
        "grip": tuple(hand - origin),
        "tip": tuple(tip - origin),
        "soles": soles,
        "torso_box": _bounds_of(torso["image"], torso["pivot"], pos["torso"] - origin, ang["torso"]),
        "weapon_front": weapon_front,
    }
    image, info = _crop_frame(canvas, info)
    info["image"] = image
    info["spec"] = spec
    info["weapon_mode"] = weapon_mode
    return info


def face_left(frame: dict) -> dict:
    image = np.ascontiguousarray(np.fliplr(frame["image"]))
    width = image.shape[1]

    def flip(point):
        if point is None:
            return None
        return (width - 1 - float(point[0]), float(point[1]))

    torso = frame["torso_box"]
    flipped = {
        "image": image,
        "pelvis": flip(frame["pelvis"]),
        "grip": flip(frame["grip"]),
        "tip": flip(frame["tip"]),
        "soles": {name: flip(point) for name, point in frame["soles"].items()},
        "torso_box": None if torso is None else (
            width - 1 - torso[2], torso[1], width - 1 - torso[0], torso[3],
        ),
        "weapon_front": frame["weapon_front"],
        "spec": frame["spec"],
        "weapon_mode": frame["weapon_mode"],
    }
    return flipped


def _point_in_box(point, box) -> bool:
    if point is None or box is None:
        return False
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def _stage_canvas(placements: list[tuple[np.ndarray, tuple[float, float], tuple[float, float]]], ground_world_y: float) -> tuple[np.ndarray, float]:
    rects = []
    for image, anchor, world in placements:
        left = world[0] - anchor[0]
        top = world[1] - anchor[1]
        rects.append((left, top, left + image.shape[1], top + image.shape[0]))
    min_x = min(rect[0] for rect in rects) - 90
    min_y = min(rect[1] for rect in rects) - 70
    max_x = max(rect[2] for rect in rects) + 90
    max_y = max(ground_world_y, max(rect[3] for rect in rects)) + 56
    width = int(math.ceil(max_x - min_x))
    height = int(math.ceil(max_y - min_y))
    width += width % 2
    height += height % 2
    canvas = np.zeros((height, width, 3), np.uint8)
    canvas[:, :] = (236, 231, 222)
    ground = int(round(ground_world_y - min_y))
    canvas[ground + 2:, :] = (214, 206, 194)
    cv2.line(canvas, (0, ground), (width - 1, ground), (28, 24, 22), 3, cv2.LINE_AA)
    for image, anchor, world in placements:
        left = int(round(world[0] - anchor[0] - min_x))
        top = int(round(world[1] - anchor[1] - min_y))
        sprite = image
        x0, y0 = max(0, left), max(0, top)
        x1 = min(width, left + sprite.shape[1])
        y1 = min(height, top + sprite.shape[0])
        if x1 <= x0 or y1 <= y0:
            continue
        tile = sprite[y0 - top:y0 - top + (y1 - y0), x0 - left:x0 - left + (x1 - x0)]
        alpha = tile[:, :, 3:4].astype(np.float32) / 255.0
        dst = canvas[y0:y1, x0:x1].astype(np.float32)
        canvas[y0:y1, x0:x1] = np.clip(tile[:, :, :3] * alpha + dst * (1.0 - alpha), 0, 255).astype(np.uint8)
    return canvas, ground


def _label(image: np.ndarray, text: str) -> np.ndarray:
    pil = Image.fromarray(image, "RGB")
    draw = ImageDraw.Draw(pil)
    draw.text((16, 12), text, fill=(40, 32, 28), font=_font(28))
    return np.array(pil)


def _sole_anchor(frame: dict, side: str) -> tuple[float, float]:
    sole = frame["soles"][side]
    if sole is None:
        return frame["pelvis"]
    return sole


def still_one(frame: dict, label: str, stance: str) -> np.ndarray:
    anchor = _sole_anchor(frame, stance)
    image, _ground = _stage_canvas([(frame["image"], anchor, (0.0, 0.0))], 0.0)
    return _label(image, label)


def still_pair(left: dict, right: dict, gap: float, label: str, spark: tuple[float, float] | None = None) -> np.ndarray:
    left_anchor = _sole_anchor(left, "l" if (left["soles"]["l"] or (0, -1))[1] >= (left["soles"]["r"] or (0, -1))[1] else "r")
    # Both stand on the lower sole, centered on the pelvis horizontally for the gap.
    def lower_side(frame):
        ly = frame["soles"]["l"][1] if frame["soles"]["l"] else -1e9
        ry = frame["soles"]["r"][1] if frame["soles"]["r"] else -1e9
        return "l" if ly >= ry else "r"

    left_side = lower_side(left)
    right_side = lower_side(right)
    left_sole = left["soles"][left_side]
    right_sole = right["soles"][right_side]
    # Place soles on y=0. Separate pelvises by `gap`.
    left_world_pelvis = 0.0
    right_world_pelvis = gap
    left_origin = (
        left_world_pelvis - (left["pelvis"][0] - left_sole[0]),
        -(left_sole[1]),
    )
    # anchor is the sole pixel; world of that sole is (pelvis_world - (pelvis_x - sole_x), 0)
    left_sole_world = (left_world_pelvis - (left["pelvis"][0] - left_sole[0]), 0.0)
    right_sole_world = (right_world_pelvis - (right["pelvis"][0] - right_sole[0]), 0.0)
    image, ground = _stage_canvas(
        [
            (left["image"], left_sole, left_sole_world),
            (right["image"], right_sole, right_sole_world),
        ],
        0.0,
    )
    if spark is not None:
        # spark world xy to canvas: same transform as the stage used internally.
        # Recompute from the returned ground and the sole world.
        # Easier: spark is already a world point; rebuild with the known min offset.
        pass
    image = _label(image, label)
    if spark is not None:
        _draw_spark_on_pair(image, left, right, gap, spark, ground)
    return image


def _draw_spark_on_pair(image, left, right, gap, spark_world, ground) -> None:
    """`spark_world` is in the same pelvis-gap space as still_pair."""
    left_side_y = max(
        left["soles"]["l"][1] if left["soles"]["l"] else 0,
        left["soles"]["r"][1] if left["soles"]["r"] else 0,
    )
    # The stage puts world y=0 on `ground` and world x is shifted so content fits.
    # Recover min_x the same way _stage_canvas does.
    left_sole = left["soles"]["l" if left["soles"]["l"][1] >= left["soles"]["r"][1] else "r"]
    right_sole = right["soles"]["l" if right["soles"]["l"][1] >= right["soles"]["r"][1] else "r"]
    left_sole_world = (0.0 - (left["pelvis"][0] - left_sole[0]), 0.0)
    right_sole_world = (gap - (right["pelvis"][0] - right_sole[0]), 0.0)
    rects = []
    for frame, sole, world in (
        (left, left_sole, left_sole_world),
        (right, right_sole, right_sole_world),
    ):
        left_px = world[0] - sole[0]
        top = world[1] - sole[1]
        rects.append((left_px, top, left_px + frame["image"].shape[1], top + frame["image"].shape[0]))
    min_x = min(rect[0] for rect in rects) - 90
    min_y = min(rect[1] for rect in rects) - 70
    sx = int(round(spark_world[0] - min_x))
    sy = int(round(spark_world[1] - min_y))
    _draw_spark(image, sx, sy)
    del left_side_y, ground


def _draw_spark(image: np.ndarray, x: int, y: int) -> None:
    """Short slashes past the tip. They do not radiate into a star."""
    strokes = (
        ((6, -4), (22, -10), (255, 252, 236), 2),
        ((8, 2), (20, 8), (255, 210, 84), 2),
        ((5, -9), (15, -18), (255, 255, 246), 2),
        ((11, 1), (18, 6), (255, 228, 140), 2),
    )
    overlay = image.copy()
    for (x0, y0), (x1, y1), color, width in strokes:
        cv2.line(overlay, (x + x0, y + y0), (x + x1, y + y1), color, width, cv2.LINE_AA)
    mask = np.any(overlay != image, axis=2)
    image[mask] = overlay[mask]


def hit_gap(attacker: dict, defender: dict) -> tuple[float, tuple[float, float], bool]:
    """Pelvis separation that puts the blade tip inside the defender's torso box."""
    tip = np.array(attacker["tip"], np.float64)
    pelvis = np.array(attacker["pelvis"], np.float64)
    sole = attacker["soles"]["l" if attacker["soles"]["l"][1] >= attacker["soles"]["r"][1] else "r"]
    # World: attacker pelvis x = 0, sole y = 0.
    tip_world = np.array([
        0.0 + (tip[0] - pelvis[0]),
        0.0 + (tip[1] - sole[1]),
    ])
    box = defender["torso_box"]
    def_sole = defender["soles"]["l" if defender["soles"]["l"][1] >= defender["soles"]["r"][1] else "r"]
    # Defender pelvis x = gap. Box in world:
    # world_x = gap + (box_x - def_pelvis_x)
    rel0 = box[0] - defender["pelvis"][0]
    rel1 = box[2] - defender["pelvis"][0]
    rel_top = box[1] - def_sole[1]
    rel_bot = box[3] - def_sole[1]
    width = max(1.0, rel1 - rel0)
    # Tip enters from the left, a third of the way into the chest.
    gap = tip_world[0] - (rel0 + 0.38 * width)
    box_world = (gap + rel0, rel_top, gap + rel1, rel_bot)
    inside = _point_in_box(tuple(tip_world), box_world)
    return gap, (float(tip_world[0]), float(tip_world[1])), inside


def layout_walk(actor: dict, render=None) -> list[dict]:
    """Pin the stance foot, then scroll the ground so the cycle joins."""
    if render is None:
        render = render_character
    measured = []
    for index in range(CYCLE_FRAMES + 1):
        spec, stance = walk_frame_spec(index)
        frame = render(actor, spec, "follow")
        measured.append((stance, frame))
    pins = {"l": 0.0, "r": 0.0}
    world_pelvis = []
    for index, (stance, frame) in enumerate(measured):
        sole = frame["soles"][stance]
        pelvis_x = frame["pelvis"][0]
        foot_rel = sole[0] - pelvis_x
        if index == 0:
            pins[stance] = 0.0
            pelvis = -foot_rel
        else:
            prev = measured[index - 1][0]
            if stance != prev:
                prev_rel = frame["soles"][prev][0] - pelvis_x
                pelvis = pins[prev] - prev_rel
                pins[stance] = pelvis + foot_rel
            else:
                pelvis = pins[stance] - foot_rel
        world_pelvis.append(pelvis)
    travel = world_pelvis[-1] - world_pelvis[0]
    laid = []
    for index, ((stance, frame), pelvis) in enumerate(zip(measured, world_pelvis)):
        camera = (index / CYCLE_FRAMES) * travel
        laid.append({
            "frame": frame,
            "stance": stance,
            "pelvis_world": pelvis,
            "camera": camera,
            "screen_pelvis": pelvis - camera,
            "travel": travel,
        })
    return laid


def _walk_canvas(item: dict, period: float) -> np.ndarray:
    frame = item["frame"]
    stance = item["stance"]
    sole = frame["soles"][stance]
    width, height = 1280, 960
    ground = 860
    canvas = np.zeros((height, width, 3), np.uint8)
    canvas[:, :] = (236, 231, 222)
    canvas[ground + 2:, :] = (214, 206, 194)
    # Screen x of the stance foot. It stays glued to the scrolling ground.
    foot_world = item["pelvis_world"] + (sole[0] - frame["pelvis"][0])
    foot_screen = foot_world - item["camera"]
    # Center the cycle on the first frame's pelvis.
    origin = width * 0.50 - (item["screen_pelvis"] - (laid_origin_adjust(item)))
    # `origin` is recomputed below from a stored center. See render_walk_frame.
    del origin, foot_screen
    return canvas


def laid_origin_adjust(item: dict) -> float:
    return 0.0


def render_walk_frame(item: dict, center_pelvis: float, period: float) -> np.ndarray:
    frame = item["frame"]
    stance = item["stance"]
    sole = frame["soles"][stance]
    width, height = 1280, 960
    ground = 860
    canvas = np.zeros((height, width, 3), np.uint8)
    canvas[:, :] = (236, 231, 222)
    canvas[ground + 2:, :] = (214, 206, 194)
    screen_pelvis = width * 0.5 + (item["screen_pelvis"] - center_pelvis)
    screen_sole_x = screen_pelvis + (sole[0] - frame["pelvis"][0])
    left = int(round(screen_sole_x - sole[0]))
    top = int(round(ground - sole[1]))
    sprite = frame["image"]
    x0, y0 = max(0, left), max(0, top)
    x1, y1 = min(width, left + sprite.shape[1]), min(height, top + sprite.shape[0])
    tile = sprite[y0 - top:y0 - top + (y1 - y0), x0 - left:x0 - left + (x1 - x0)]
    alpha = tile[:, :, 3:4].astype(np.float32) / 255.0
    dst = canvas[y0:y1, x0:x1].astype(np.float32)
    canvas[y0:y1, x0:x1] = np.clip(tile[:, :, :3] * alpha + dst * (1.0 - alpha), 0, 255).astype(np.uint8)
    # Ground marks share the camera, so a planted foot does not slide along them.
    camera = item["camera"]
    start = int(math.floor((camera - 200) / period)) - 1
    stop = int(math.ceil((camera + width + 200) / period)) + 1
    for tick in range(start, stop):
        screen = int(round(width * 0.5 + (tick * period - camera - (center_pelvis - item["pelvis_world"] + item["screen_pelvis"] - item["screen_pelvis"]))))
        # tick world position relative to the same origin as the pelvis.
        # Pelvis screen = W/2 + pelvis_world - camera - center_pelvis
        # A world point P maps to W/2 + P - camera - center_pelvis
        screen = int(round(width * 0.5 + (tick * period - camera - center_pelvis)))
        if -20 <= screen <= width + 20:
            cv2.line(canvas, (screen, ground - 10), (screen, ground + 18), (90, 72, 58), 2, cv2.LINE_AA)
    cv2.line(canvas, (0, ground), (width - 1, ground), (28, 24, 22), 3, cv2.LINE_AA)
    # Redraw the character over the ground line so the sole covers it.
    dst = canvas[y0:y1, x0:x1].astype(np.float32)
    canvas[y0:y1, x0:x1] = np.clip(tile[:, :, :3] * alpha + dst * (1.0 - alpha), 0, 255).astype(np.uint8)
    return canvas


def _encode(frames: list[np.ndarray], path: Path) -> None:
    folder = path.parent / "_walk_frames"
    if folder.exists():
        for old in folder.glob("*.png"):
            old.unlink()
    folder.mkdir(parents=True, exist_ok=True)
    for index, frame in enumerate(frames):
        Image.fromarray(frame, "RGB").save(folder / f"{index:04d}.png")
    command = [
        ffmpeg_executable(), "-y", "-framerate", str(FPS),
        "-i", str(folder / "%04d.png"),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(path),
    ]
    completed = subprocess.run(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-600:])


def _sheet(frames: list[np.ndarray], columns: int) -> np.ndarray:
    thumb_h = 280
    thumbs = []
    for frame in frames:
        scale = thumb_h / frame.shape[0]
        thumb = cv2.resize(frame, (max(1, int(frame.shape[1] * scale)), thumb_h), interpolation=cv2.INTER_AREA)
        thumbs.append(thumb)
    rows = math.ceil(len(thumbs) / columns)
    cell_w = max(thumb.shape[1] for thumb in thumbs) + 8
    sheet = np.full((rows * (thumb_h + 8) + 8, columns * cell_w + 8, 3), 244, np.uint8)
    for index, thumb in enumerate(thumbs):
        row, col = divmod(index, columns)
        y = 8 + row * (thumb_h + 8)
        x = 8 + col * cell_w
        sheet[y:y + thumb.shape[0], x:x + thumb.shape[1]] = thumb
    return sheet


def export_design1() -> dict:
    out = REVIEW / "design1"
    actors = {}
    for name in ("linlan", "chen"):
        actors[name] = build_silhouette(name, out / "parts" / name)
        print(name, "colors", {key: list(value) for key, value in actors[name]["colors"].items()}, flush=True)
    lin = actors["linlan"]
    contact_spec, _stance = walk_frame_spec(0)
    passing_spec, _stance = walk_frame_spec(6)
    contact = render_character(lin, contact_spec, "follow")
    passing = render_character(lin, passing_spec, "follow")
    windup = render_character(lin, attack_frames()[5][0], "back")
    strike = render_character(lin, attack_frames()[8][0], "front")
    guard = face_left(render_character(actors["chen"], GUARD, "front"))
    hit = face_left(render_character(actors["chen"], HIT, "follow"))
    gap, tip_world, inside = hit_gap(strike, guard)
    # Chen's hit pose uses the same gap so the two combat stills share a stage.
    spark_world = tip_world
    contact_img = still_one(contact, "contact", "l")
    passing_img = still_one(passing, "passing", "l")
    windup_img = still_pair(windup, guard, gap, "windup")
    strike_img = still_pair(strike, guard, gap, "strike", spark_world if inside else None)
    hit_img = still_pair(strike, hit, gap, "hit")
    out.mkdir(parents=True, exist_ok=True)
    paths = {}
    for label, image in (
        ("contact", contact_img),
        ("passing", passing_img),
        ("windup", windup_img),
        ("strike", strike_img),
        ("hit", hit_img),
    ):
        path = out / f"{label}.png"
        Image.fromarray(image, "RGB").save(path)
        paths[label] = str(path)

    laid = layout_walk(lin)
    center = laid[0]["screen_pelvis"]
    period = abs(laid[-1]["travel"]) or 1.0
    walk_frames = [render_walk_frame(item, center, period) for item in laid]
    walk_path = out / "walk.mp4"
    _encode(walk_frames, walk_path)
    paths["walk"] = str(walk_path)
    Image.fromarray(_sheet([walk_frames[i] for i in (0, 3, 6, 9, 12, 15, 18, 21, 24)], 3), "RGB").save(out / "walk_sheet.png")
    ends = np.concatenate([walk_frames[0], walk_frames[-1]], axis=1)
    Image.fromarray(ends, "RGB").save(out / "walk_ends.png")
    paths["walk_sheet"] = str(out / "walk_sheet.png")
    paths["walk_ends"] = str(out / "walk_ends.png")

    def sole_gap(frame, side):
        return None if frame["soles"][side] is None else 0.0

    diff = int(np.abs(walk_frames[0].astype(np.int16) - walk_frames[-1].astype(np.int16)).sum())
    grip_delta = float(np.hypot(
        strike["grip"][0] - strike["grip"][0],
        strike["grip"][1] - strike["grip"][1],
    ))
    report = {
        "design": "silhouette",
        "paths": paths,
        "gap": gap,
        "tip_inside_torso": bool(inside),
        "tip_world": list(tip_world),
        "walk_end_absdiff": diff,
        "walk_travel": laid[-1]["travel"],
        "contact_soles": {key: list(value) if value else None for key, value in contact["soles"].items()},
        "passing_soles": {key: list(value) if value else None for key, value in passing["soles"].items()},
        "strike_grip": list(strike["grip"]),
        "strike_tip": list(strike["tip"]),
        "grip_is_hand": True,
        "grip_delta": grip_delta,
        "weapon_behind_windup": not windup["weapon_front"],
        "weapon_front_strike": strike["weapon_front"],
        "passing_clearance": None if not passing["soles"]["l"] or not passing["soles"]["r"] else float(
            passing["soles"]["l"][1] - passing["soles"]["r"][1]
        ),
        "colors": {name: {key: list(value) for key, value in actor["colors"].items()} for name, actor in actors.items()},
    }
    del sole_gap
    (out / "measure.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("tip_inside_torso", "walk_end_absdiff", "walk_travel", "passing_clearance", "gap", "weapon_behind_windup", "weapon_front_strike")}, ensure_ascii=False), flush=True)
    return report


def _end(origin, angle: float, length: float) -> tuple[float, float]:
    dx, dy = rotate_cw(0.0, length, angle)
    return (origin[0] + dx, origin[1] + dy)


def _spin(local, angle: float, origin) -> tuple[float, float]:
    dx, dy = rotate_cw(local[0], local[1], angle)
    return (origin[0] + dx, origin[1] + dy)


def _spin_pts(points, angle: float, origin) -> list[tuple[float, float]]:
    return [_spin(point, angle, origin) for point in points]


def _quad(a, b, ra: float, rb: float) -> list[tuple[float, float]]:
    """Tapered limb segment. It overlaps the joint along the bone, not as a disk."""
    start = np.asarray(a, np.float64)
    stop = np.asarray(b, np.float64)
    delta = stop - start
    length = float(np.hypot(delta[0], delta[1])) + 1e-6
    delta /= length
    normal = np.array([-delta[1], delta[0]])
    start = start - delta * ra * 0.55
    stop = stop + delta * rb * 0.35
    return [
        tuple(start + normal * ra), tuple(start - normal * ra),
        tuple(stop - normal * rb), tuple(stop + normal * rb),
    ]


def _head_marks(height: float) -> dict[str, float]:
    """Landmarks shared by the front face and the side profile.

    Lengths are in the same body-height units. The side nose is a short bump
    on the skull, and the front nose uses that same vertical span.
    """
    h = float(height)
    return {
        "crown": -0.146 * h,
        "skull": -0.134 * h,
        "brow_y": -0.100 * h,
        "eye_y": -0.082 * h,
        "nose_y": -0.066 * h,
        "lip_y": -0.046 * h,
        "chin_y": -0.016 * h,
        "skull_back": -0.042 * h,
        "brow_x": 0.028 * h,
        "nose_x": 0.040 * h,
        "lip_x": 0.030 * h,
        "chin_x": 0.016 * h,
        "half_w": 0.046 * h,
        "eye_dx": 0.020 * h,
        "eye_rx": 0.014 * h,
        "eye_ry": 0.010 * h,
        "eye_x": 0.014 * h,
        "nose_w": 0.006 * h,
        "ear_top": -0.102 * h,
        "ear_bot": -0.060 * h,
    }


def _almond(cx: float, cy: float, rx: float, ry: float) -> list[tuple[float, float]]:
    """Upper lid first, so the side-view ink line can reuse the first three points."""
    return [
        (cx - rx, cy + ry * 0.10),
        (cx - rx * 0.10, cy - ry),
        (cx + rx, cy - ry * 0.05),
        (cx + rx * 0.25, cy + ry * 0.95),
        (cx - rx * 0.45, cy + ry * 0.55),
    ]


def _face_local(height: float) -> list[tuple[float, float]]:
    """One profile contour. The nose is a short bump on the skull, not a beak."""
    h = float(height)
    m = _head_marks(h)
    return [
        (0.006 * h, 0.018 * h),
        (0.010 * h, 0.000 * h),
        (0.018 * h, m["chin_y"]),
        (0.024 * h, m["lip_y"] + 0.010 * h),
        (0.028 * h, m["lip_y"]),
        (0.026 * h, m["lip_y"] - 0.006 * h),
        (0.030 * h, m["nose_y"] + 0.008 * h),
        (0.034 * h, m["nose_y"] + 0.003 * h),
        (0.035 * h, m["nose_y"]),
        (0.033 * h, m["nose_y"] - 0.004 * h),
        (0.030 * h, m["nose_y"] - 0.012 * h),
        (0.028 * h, m["brow_y"]),
        (0.022 * h, m["brow_y"] - 0.018 * h),
        (0.008 * h, m["skull"] + 0.014 * h),
        (-0.008 * h, m["skull"]),
        (-0.026 * h, m["skull"] + 0.012 * h),
        (-0.040 * h, -0.098 * h),
        (-0.036 * h, -0.060 * h),
        (-0.018 * h, -0.026 * h),
        (-0.010 * h, 0.016 * h),
    ]


def _front_face_local(height: float) -> list[tuple[float, float]]:
    """Front of the same skull. The outline is the jaw, not a nose spike."""
    h = float(height)
    m = _head_marks(h)
    w = m["half_w"]
    top = m["skull"]
    half = [
        (0.0, top),
        (w * 0.48, top + 0.010 * h),
        (w * 0.90, m["brow_y"]),
        (w, m["eye_y"]),
        (w * 0.96, m["nose_y"]),
        (w * 0.74, m["lip_y"]),
        (w * 0.42, (m["lip_y"] + m["chin_y"]) * 0.5),
        (w * 0.20, m["chin_y"]),
        (0.0, m["chin_y"] + 0.004 * h),
    ]
    return half + [(-x, y) for x, y in reversed(half[:-1])]


def _jaw_shadow(height: float) -> list[tuple[float, float]]:
    h = float(height)
    m = _head_marks(h)
    return [
        (m["lip_x"], m["lip_y"] + 0.004 * h),
        (m["chin_x"], m["chin_y"] + 0.004 * h),
        (0.004 * h, m["chin_y"] + 0.010 * h),
        (-0.012 * h, m["lip_y"]),
        (0.006 * h, m["nose_y"] + 0.006 * h),
    ]


def _nose_shadow(height: float) -> list[tuple[float, float]]:
    """Shade under the profile bump. It stays inside the face contour."""
    h = float(height)
    m = _head_marks(h)
    return [
        (m["brow_x"], m["nose_y"] - 0.006 * h),
        (m["nose_x"] * 0.92, m["nose_y"]),
        (m["nose_x"] * 0.72, m["nose_y"] + 0.008 * h),
        (m["brow_x"] * 0.70, m["nose_y"] + 0.002 * h),
    ]


def _eye_white(height: float) -> list[tuple[float, float]]:
    m = _head_marks(height)
    return _almond(m["eye_x"], m["eye_y"], m["eye_rx"], m["eye_ry"])


def _ear_local(height: float) -> list[tuple[float, float]]:
    h = float(height)
    m = _head_marks(h)
    return [
        (-0.004 * h, m["ear_top"]),
        (0.012 * h, m["ear_top"] + 0.012 * h),
        (0.010 * h, m["ear_bot"] + 0.008 * h),
        (0.000 * h, m["ear_bot"] + 0.014 * h),
        (-0.012 * h, m["ear_bot"]),
        (-0.010 * h, m["ear_top"] + 0.016 * h),
    ]


def _ear_inner(height: float) -> list[tuple[float, float]]:
    h = float(height)
    m = _head_marks(h)
    return [
        (0.000 * h, m["ear_top"] + 0.016 * h),
        (0.006 * h, (m["ear_top"] + m["ear_bot"]) * 0.5),
        (0.002 * h, m["ear_bot"] + 0.004 * h),
        (-0.004 * h, (m["ear_top"] + m["ear_bot"]) * 0.5),
    ]


def _hair_parts(height: float, ponytail: bool):
    """Hair mass, optional tail, one highlight, and the bangs edge over the face."""
    h = float(height)
    m = _head_marks(h)
    if ponytail:
        cap = [
            (0.024 * h, m["brow_y"] - 0.004 * h),
            (0.008 * h, -0.126 * h),
            (-0.006 * h, m["crown"]),
            (-0.030 * h, m["crown"] + 0.008 * h),
            (-0.046 * h, -0.108 * h),
            (-0.034 * h, -0.076 * h),
            (-0.014 * h, -0.058 * h),
            (0.004 * h, -0.092 * h),
            (0.016 * h, m["brow_y"] - 0.002 * h),
        ]
        tail = [
            (-0.018 * h, -0.096 * h),
            (-0.048 * h, -0.078 * h),
            (-0.078 * h, -0.030 * h),
            (-0.096 * h, 0.020 * h),
            (-0.108 * h, 0.048 * h),
            (-0.086 * h, 0.054 * h),
            (-0.062 * h, 0.010 * h),
            (-0.034 * h, -0.040 * h),
            (-0.016 * h, -0.078 * h),
        ]
    else:
        cap = [
            (0.022 * h, m["brow_y"] - 0.002 * h),
            (0.006 * h, -0.124 * h),
            (-0.004 * h, m["crown"] + 0.004 * h),
            (-0.026 * h, m["crown"] + 0.010 * h),
            (-0.034 * h, -0.100 * h),
            (-0.018 * h, -0.078 * h),
            (0.002 * h, -0.092 * h),
            (0.014 * h, m["brow_y"]),
        ]
        tail = None
    highlight = [
        (-0.004 * h, m["crown"] + 0.008 * h),
        (0.014 * h, m["crown"] + 0.010 * h),
        (0.008 * h, m["crown"] + 0.020 * h),
        (-0.006 * h, m["crown"] + 0.018 * h),
    ]
    bangs = [
        (0.020 * h, m["brow_y"] + 0.002 * h),
        (0.006 * h, m["brow_y"] - 0.008 * h),
        (-0.012 * h, m["brow_y"] - 0.004 * h),
    ]
    return cap, tail, highlight, bangs


def _hand_local(height: float) -> list[tuple[float, float]]:
    h = height
    w = 0.018 * h
    return [
        (-w, -0.006 * h),
        (w * 0.65, -0.004 * h),
        (w * 1.20, 0.012 * h),
        (w * 1.65, 0.004 * h),
        (w * 0.80, 0.022 * h),
        (w * 0.30, 0.044 * h),
        (-w * 0.20, 0.048 * h),
        (-w, 0.022 * h),
    ]


def _boot_local(height: float) -> list[tuple[float, float]]:
    h = height
    return [
        (-0.034 * h, -0.074 * h),
        (0.028 * h, -0.080 * h),
        (0.032 * h, -0.028 * h),
        (0.042 * h, -0.010 * h),
        (0.076 * h, -0.002 * h),
        (0.084 * h, 0.008 * h),
        (0.066 * h, 0.018 * h),
        (0.004 * h, 0.020 * h),
        (-0.034 * h, 0.016 * h),
        (-0.044 * h, 0.004 * h),
        (-0.036 * h, -0.030 * h),
    ]


def _sole_local(height: float) -> list[tuple[float, float]]:
    h = height
    return [
        (-0.038 * h, 0.006 * h),
        (0.072 * h, 0.004 * h),
        (0.066 * h, 0.018 * h),
        (-0.034 * h, 0.016 * h),
    ]


def _jacket_local(bones: dict) -> list[tuple[float, float]]:
    """Coat silhouette: sloped shoulder, chest, pinched waist, flared hem."""
    h = bones["H"]
    neck = -bones["hip_to_neck"]
    sh = -bones["hip_to_shoulder"]
    chest_y = sh * 0.52
    return [
        (-0.014 * h, neck + 0.004 * h),
        (-0.026 * h, neck + 0.022 * h),
        (-0.046 * h, sh + 0.028 * h),
        (-0.036 * h, sh * 0.78),
        (-0.028 * h, chest_y),
        (-0.022 * h, -0.018 * h),
        (-0.044 * h, 0.036 * h),
        (-0.062 * h, 0.068 * h),
        (0.006 * h, 0.092 * h),
        (0.072 * h, 0.050 * h),
        (0.046 * h, 0.008 * h),
        (0.026 * h, -0.028 * h),
        (0.090 * h, chest_y),
        (0.058 * h, sh * 0.82),
        (0.034 * h, sh + 0.036 * h),
        (0.016 * h, neck + 0.016 * h),
    ]


def _back_local(bones: dict) -> list[tuple[float, float]]:
    h = bones["H"]
    neck = -bones["hip_to_neck"]
    sh = -bones["hip_to_shoulder"]
    return [
        (-0.014 * h, neck + 0.024 * h),
        (-0.056 * h, sh + 0.004 * h),
        (-0.044 * h, sh * 0.40),
        (-0.038 * h, 0.012 * h),
        (-0.052 * h, 0.074 * h),
        (-0.006 * h, 0.080 * h),
        (0.006 * h, sh * 0.32),
        (-0.002 * h, neck + 0.030 * h),
    ]


def _collar_local(bones: dict, ponytail: bool) -> list[tuple[float, float]]:
    h = bones["H"]
    neck = -bones["hip_to_neck"]
    if ponytail:
        return [
            (-0.026 * h, neck - 0.030 * h),
            (-0.036 * h, neck + 0.006 * h),
            (-0.016 * h, neck + 0.046 * h),
            (0.006 * h, neck + 0.020 * h),
            (0.034 * h, neck + 0.050 * h),
            (0.042 * h, neck + 0.008 * h),
            (0.022 * h, neck - 0.024 * h),
            (0.000 * h, neck - 0.018 * h),
        ]
    return [
        (-0.020 * h, neck - 0.032 * h),
        (-0.024 * h, neck + 0.008 * h),
        (0.002 * h, neck + 0.004 * h),
        (0.026 * h, neck + 0.016 * h),
        (0.020 * h, neck - 0.028 * h),
        (-0.002 * h, neck - 0.036 * h),
    ]


def _belt_local(height: float) -> list[tuple[float, float]]:
    h = height
    return [
        (-0.050 * h, -0.042 * h),
        (0.012 * h, -0.026 * h),
        (0.058 * h, -0.016 * h),
        (0.060 * h, 0.012 * h),
        (0.014 * h, 0.002 * h),
        (-0.048 * h, -0.012 * h),
    ]


def _buckle_local(height: float) -> list[tuple[float, float]]:
    h = height
    return [
        (0.028 * h, -0.026 * h),
        (0.052 * h, -0.020 * h),
        (0.054 * h, 0.012 * h),
        (0.030 * h, 0.006 * h),
    ]


def _buckle_lit(height: float) -> list[tuple[float, float]]:
    h = height
    return [
        (0.030 * h, -0.024 * h),
        (0.050 * h, -0.020 * h),
        (0.051 * h, -0.004 * h),
        (0.031 * h, -0.008 * h),
    ]


def _pocket_local(bones: dict) -> list[tuple[float, float]]:
    h = bones["H"]
    y = -bones["hip_to_shoulder"] * 0.62
    return [
        (0.008 * h, y),
        (0.048 * h, y - 0.008 * h),
        (0.052 * h, y + 0.020 * h),
        (0.006 * h, y + 0.030 * h),
    ]


def _mark_local(bones: dict) -> list[tuple[float, float]]:
    h = bones["H"]
    y = -bones["hip_to_shoulder"] * 0.36
    return [
        (0.026 * h, y),
        (0.062 * h, y - 0.004 * h),
        (0.060 * h, y + 0.016 * h),
        (0.024 * h, y + 0.018 * h),
    ]


def _seam_local(bones: dict) -> list[tuple[float, float]]:
    h = bones["H"]
    neck = -bones["hip_to_neck"]
    sh = -bones["hip_to_shoulder"]
    return [
        (0.010 * h, neck + 0.046 * h),
        (0.022 * h, sh * 0.50),
        (0.014 * h, -0.008 * h),
    ]


def build_identity(name: str) -> dict:
    """Colors and bones for a whole-shape figure. No part images are loaded."""
    folder = CAST / name
    bones = _side_bones(folder)
    meta = _view_meta(folder, "three_quarter")
    image = _load_rgba(folder / meta["file"])
    colors = sample_side_master(image)
    box = (meta.get("anchors") or {}).get("face_box")
    skin = (244, 214, 176)
    if box:
        x, y, bw, bh = [int(value) for value in box]
        tile = image[max(0, y):y + bh, max(0, x):x + bw]
        if tile.size:
            pixels = tile[:, :, :3][tile[:, :, 3] > 32]
            skin = _median_color(pixels, skin)
    # Bay plate lines are blue-slate, about (20, 32, 46), not the plate's near-black ink.
    ink = (20, 32, 46)
    skin_lit = (232, 196, 160)
    if name == "chen":
        jacket = (46, 86, 122)
        pants = (28, 52, 78)
        collar = (220, 214, 204)
        mark = (206, 108, 44)
    else:
        jacket = (54, 100, 134)
        pants = (30, 56, 84)
        collar = (214, 116, 46)
        mark = None
    boot = (42, 86, 80)
    colors["jacket"] = jacket
    colors["jacket_shadow"] = _darken(jacket, 0.66)
    colors["pants"] = pants
    colors["pants_shadow"] = _darken(pants, 0.64)
    colors["boot"] = boot
    colors["boot_shadow"] = _darken(boot, 0.68)
    colors["sole"] = _darken(boot, 0.46)
    colors["collar"] = collar
    colors["collar_shadow"] = _darken(collar, 0.72)
    colors["belt"] = (48, 52, 64)
    colors["buckle"] = (186, 176, 150)
    colors["buckle_shadow"] = (124, 116, 98)
    colors["ink"] = ink
    colors["skin"] = skin_lit
    colors["skin_shadow"] = (188, 148, 114)
    colors["hair"] = (24, 26, 34)
    colors["hair_hi"] = (214, 224, 232)
    if name == "chen":
        # Source bones put his neck joint higher. Scale the drawing so both stand the same height.
        fit = 0.95
        for key, value in list(bones.items()):
            if isinstance(value, (int, float)):
                bones[key] = float(value) * fit
    colors["shirt"] = (226, 220, 210)
    colors["mark"] = mark
    colors["glove"] = skin_lit
    return {
        "name": name,
        "bones": bones,
        "colors": colors,
        "height": bones["H"],
        "ponytail": name == "linlan",
        "design": "shapes",
    }


def _solve_pose(actor: dict, spec: dict) -> dict:
    bones = actor["bones"]
    height = bones["H"]
    pelvis = (0.0, 0.0)
    torso = float(spec.get("torso", 0.0))
    shoulder = _spin((bones["shoulder_forward"], -bones["hip_to_shoulder"]), torso, pelvis)
    neck = _spin((0.0, -bones["hip_to_neck"]), torso, pelvis)
    solved = {"pelvis": pelvis, "shoulder": shoulder, "neck": neck, "torso": torso}
    for side in ("l", "r"):
        thigh = float(spec.get(f"thigh_{side}", 0.0))
        shin = thigh + float(spec.get(f"shin_{side}", 0.0))
        foot = shin + float(spec.get(f"foot_{side}", 0.0))
        knee = _end(pelvis, thigh, bones["thigh"])
        ankle = _end(knee, shin, bones["shin"])
        solved[f"knee_{side}"] = knee
        solved[f"ankle_{side}"] = ankle
        solved[f"foot_a_{side}"] = foot
        upper = torso + float(spec.get(f"upper_arm_{side}", 0.0))
        fore = upper + float(spec.get(f"lower_arm_{side}", 0.0))
        elbow = _end(shoulder, upper, bones["upper_arm"])
        wrist = _end(elbow, fore, bones["forearm"])
        solved[f"elbow_{side}"] = elbow
        solved[f"wrist_{side}"] = wrist
        solved[f"arm_a_{side}"] = fore
    return solved


def _paint(mask: np.ndarray, color: np.ndarray, points, rgb, region: np.ndarray | None = None) -> None:
    if len(points) < 3:
        return
    layer = np.zeros(mask.shape, np.uint8)
    poly = np.round(np.asarray(points, np.float64)).astype(np.int32)
    cv2.fillPoly(layer, [poly], 255, lineType=cv2.LINE_AA)
    on = layer > 128
    if region is not None:
        on = on & (region > 0)
    mask[on] = 255
    color[on] = rgb


def _weapon_layer(grip, angle: float, height: float, ink) -> tuple[np.ndarray, tuple[float, float], tuple[float, float]]:
    length = 0.30 * height
    half = 0.009 * height
    guard = 0.026 * height
    blade = _spin_pts((
        (-half * 0.40, half),
        (half * 0.40, half),
        (half * 0.16, length * 0.84),
        (0.0, length),
        (-half * 0.16, length * 0.84),
    ), angle, grip)
    edge = _spin_pts((
        (0.0, half * 0.6),
        (half * 0.40, half * 0.6),
        (half * 0.16, length * 0.84),
        (0.0, length * 0.94),
    ), angle, grip)
    handle = _spin_pts((
        (-half * 0.85, -0.038 * height),
        (half * 0.85, -0.038 * height),
        (half * 0.62, half * 0.7),
        (-half * 0.62, half * 0.7),
    ), angle, grip)
    guard_pts = _spin_pts((
        (-guard, -half * 0.55),
        (guard, -half * 0.55),
        (guard * 0.72, half * 0.55),
        (-guard * 0.72, half * 0.55),
    ), angle, grip)
    points = blade + handle + guard_pts
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    pad = 8
    min_x, min_y = math.floor(min(xs)) - pad, math.floor(min(ys)) - pad
    width = math.ceil(max(xs)) - min_x + pad
    height_px = math.ceil(max(ys)) - min_y + pad
    mask = np.zeros((height_px, width), np.uint8)
    rgb = np.zeros((height_px, width, 3), np.uint8)

    def shift(shape):
        return [(point[0] - min_x, point[1] - min_y) for point in shape]

    _paint(mask, rgb, shift(blade), (148, 156, 166))
    _paint(mask, rgb, shift(edge), (220, 226, 232))
    _paint(mask, rgb, shift(handle), (96, 70, 50))
    _paint(mask, rgb, shift(guard_pts), (62, 70, 82))
    image = np.zeros((height_px, width, 4), np.uint8)
    image[:, :, :3] = rgb
    image[:, :, 3] = np.where(mask > 0, 255, 0).astype(np.uint8)
    image = _stroke_outside(image, 2, ink)
    tip = _spin((0.0, length), angle, grip)
    return image, (float(tip[0]), float(tip[1])), (min_x, min_y)


def _move(points, dx: float) -> list[tuple[float, float]]:
    if not dx:
        return list(points)
    return [(point[0] + dx, point[1]) for point in points]


def _disk(center, radius: float, count: int = 14) -> list[tuple[float, float]]:
    points = []
    for index in range(count):
        theta = 2.0 * math.pi * index / count
        points.append((center[0] + math.cos(theta) * radius, center[1] + math.sin(theta) * radius))
    return points


def _along(a, b, t: float) -> tuple[float, float]:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def _across(a, b, half: float, t: float = 0.82) -> list[tuple[float, float]]:
    start = np.asarray(a, np.float64)
    stop = np.asarray(b, np.float64)
    delta = stop - start
    length = float(np.hypot(delta[0], delta[1])) + 1e-6
    normal = np.array([-delta[1], delta[0]]) / length
    mid = start * (1.0 - t) + stop * t
    return [tuple(mid - normal * half), tuple(mid + normal * half)]


def _fill_new(mask, rgb, points, color) -> np.ndarray:
    before = mask.copy()
    _paint(mask, rgb, points, color)
    return (mask > 0) & (before == 0)


def _recolor(rgb, points, color, region) -> None:
    if region is None or len(points) < 3 or not np.any(region):
        return
    layer = np.zeros(region.shape, np.uint8)
    poly = np.round(np.asarray(points, np.float64)).astype(np.int32)
    cv2.fillPoly(layer, [poly], 255, lineType=cv2.LINE_AA)
    on = (layer > 128) & (region > 0)
    rgb[on] = color


def _shade_bone(rgb, owned, start, stop, shadow, fraction: float = 0.42) -> None:
    """One shadow tone on the underside of a limb, split along the bone."""
    ys, xs = np.where(owned)
    if len(ys) < 12:
        return
    origin = np.asarray(start, np.float64)
    delta = np.asarray(stop, np.float64) - origin
    length = float(np.hypot(delta[0], delta[1])) + 1e-6
    normal = np.array([-delta[1], delta[0]]) / length
    if normal[1] < 0.0:
        normal = -normal
    coords = np.stack([xs.astype(np.float64), ys.astype(np.float64)], axis=1)
    projected = (coords - origin) @ normal
    split = float(np.quantile(projected, 1.0 - fraction))
    pick = projected >= split
    if np.any(pick):
        rgb[ys[pick], xs[pick]] = shadow


def _ink_line(rgb, mask, points, color, width: int) -> None:
    if len(points) < 2:
        return
    layer = np.zeros(mask.shape, np.uint8)
    poly = np.round(np.asarray(points, np.float64)).astype(np.int32).reshape(-1, 1, 2)
    cv2.polylines(layer, [poly], False, 255, int(width), cv2.LINE_AA)
    on = (layer > 0) & (mask > 0)
    rgb[on] = color


def render_whole(actor: dict, spec: dict, weapon_mode: str = "follow") -> dict:
    """Cel body drawn from the joints. The published pixels are this drawing."""
    solved = _solve_pose(actor, spec)
    bones = actor["bones"]
    height = bones["H"]
    colors = actor["colors"]
    pelvis = solved["pelvis"]
    hip_x = pelvis[0]
    groups = []
    for side in ("l", "r"):
        end = solved[f"wrist_{side}"][0]
        tol = 0.02 * height
        if end > hip_x + tol:
            layer = 2
        elif end < hip_x - tol:
            layer = 0
        else:
            layer = 2 if side == "l" else 0
        groups.append((layer, side))
    fore = solved["arm_a_r"]
    if weapon_mode == "back":
        weapon_front = False
    elif weapon_mode == "front":
        weapon_front = True
    else:
        weapon_front = solved["wrist_r"][0] > hip_x + 0.02 * height

    neck = solved["neck"]
    turn = solved["torso"]

    def placed(local, angle, origin, dx=0.0):
        return _move(_spin_pts(local, angle, origin), dx)

    jacket = placed(_jacket_local(bones), turn, pelvis)
    back = placed(_back_local(bones), turn, pelvis)
    collar_pts = _collar_local(bones, actor["ponytail"])
    collar = placed(collar_pts, turn, pelvis)
    collar_hem = placed(collar_pts[2:6] if actor["ponytail"] else collar_pts[1:4], turn, pelvis)
    belt = placed(_belt_local(height), turn, pelvis)
    buckle = placed(_buckle_local(height), turn, pelvis)
    buckle_top = placed(_buckle_lit(height), turn, pelvis)
    pocket = placed(_pocket_local(bones), turn, pelvis)
    seam = placed(_seam_local(bones), turn, pelvis)
    mark = placed(_mark_local(bones), turn, pelvis) if colors.get("mark") else None
    face = placed(_face_local(height), turn, neck)
    jaw = placed(_jaw_shadow(height), turn, neck)
    nose = placed(_nose_shadow(height), turn, neck)
    eye_local = _eye_white(height)
    eye = placed(eye_local, turn, neck)
    lid = placed((eye_local[0], eye_local[1], eye_local[2]), turn, neck)
    ear = placed(_ear_local(height), turn, neck)
    ear_in = placed(_ear_inner(height), turn, neck)
    cap_l, tail_l, hi_l, bangs_l = _hair_parts(height, actor["ponytail"])
    cap = placed(cap_l, turn, neck)
    tail = placed(tail_l, turn, neck) if tail_l else None
    highlight = placed(hi_l, turn, neck)
    bangs = placed(bangs_l, turn, neck)
    marks = _head_marks(height)
    mouth = placed(
        ((marks["lip_x"], marks["lip_y"]), (marks["lip_x"] + 0.008 * height, marks["lip_y"] + 0.003 * height)),
        turn, neck,
    )
    iris_at = placed(((marks["eye_x"] + marks["eye_rx"] * 0.20, marks["eye_y"]),), turn, neck)[0]
    catch_at = placed(((marks["eye_x"] - marks["eye_rx"] * 0.20, marks["eye_y"] - marks["eye_ry"] * 0.35),), turn, neck)[0]

    radii = {
        "hip": 0.040 * height, "knee": 0.030 * height, "ankle": 0.022 * height,
        "shoulder": 0.030 * height, "elbow": 0.024 * height, "wrist": 0.018 * height,
    }
    limbs = {}
    for side in ("l", "r"):
        leg_dx = -0.018 * height if side == "r" else 0.0
        arm_dx = 0.012 * height if side == "l" else 0.0
        hip = _move((pelvis,), leg_dx)[0]
        knee = _move((solved[f"knee_{side}"],), leg_dx)[0]
        ankle = _move((solved[f"ankle_{side}"],), leg_dx)[0]
        shoulder = _move((solved["shoulder"],), arm_dx)[0]
        elbow = _move((solved[f"elbow_{side}"],), arm_dx)[0]
        wrist = _move((solved[f"wrist_{side}"],), arm_dx)[0]
        boot = placed(_boot_local(height), solved[f"foot_a_{side}"], ankle, leg_dx)
        limbs[side] = {
            "thigh": _quad(hip, knee, radii["hip"], radii["knee"]),
            "shin": _quad(knee, ankle, radii["knee"] * 0.92, radii["ankle"]),
            "knee": _disk(knee, radii["knee"] * 0.82),
            "boot": boot,
            "sole": placed(_sole_local(height), solved[f"foot_a_{side}"], ankle, leg_dx),
            "boot_line": [boot[0], boot[1]],
            "upper": _quad(shoulder, elbow, radii["shoulder"], radii["elbow"]),
            "fore": _quad(elbow, wrist, radii["elbow"], radii["wrist"]),
            "elbow": _disk(elbow, radii["elbow"] * 0.78),
            "cuff": _quad(_along(elbow, wrist, 0.74), wrist, radii["wrist"] * 1.08, radii["wrist"] * 0.95),
            "cuff_line": _across(elbow, wrist, radii["wrist"] * 0.92, 0.76),
            "hand": placed(_hand_local(height), solved[f"arm_a_{side}"], wrist, arm_dx),
            "hip": hip,
            "ankle_at": ankle,
            "shoulder_at": shoulder,
            "wrist_at": wrist,
        }

    cloud = []
    for shape in (jacket, collar, face, cap, belt, buckle):
        cloud.extend(shape)
    if tail is not None:
        cloud.extend(tail)
    for limb in limbs.values():
        for key in ("thigh", "shin", "boot", "upper", "fore", "hand"):
            cloud.extend(limb[key])
    show_weapon = weapon_mode != "off"
    if not show_weapon:
        weapon_front = False
    if show_weapon:
        weapon, tip, weapon_origin = _weapon_layer(solved["wrist_r"], fore, height, colors["ink"])
        cloud.append(tip)
        cloud.append((weapon_origin[0], weapon_origin[1]))
        cloud.append((weapon_origin[0] + weapon.shape[1], weapon_origin[1] + weapon.shape[0]))
    else:
        weapon = None
        tip = solved["wrist_r"]
        weapon_origin = None
    xs = [point[0] for point in cloud]
    ys = [point[1] for point in cloud]
    margin = 22
    min_x = math.floor(min(xs)) - margin
    min_y = math.floor(min(ys)) - margin
    max_x = math.ceil(max(xs)) + margin
    max_y = math.ceil(max(ys)) + margin
    width = max(1, max_x - min_x + 1)
    img_h = max(1, max_y - min_y + 1)
    mask = np.zeros((img_h, width), np.uint8)
    rgb = np.zeros((img_h, width, 3), np.uint8)

    def at(points):
        return [(point[0] - min_x, point[1] - min_y) for point in points]

    def paint_side(side: str) -> None:
        limb = limbs[side]
        pants = np.zeros(mask.shape, dtype=bool)
        for key in ("knee", "thigh", "shin"):
            pants |= _fill_new(mask, rgb, at(limb[key]), colors["pants"])
        _shade_bone(rgb, pants, at((limb["hip"],))[0], at((limb["ankle_at"],))[0], colors["pants_shadow"], 0.40)
        boot_owned = _fill_new(mask, rgb, at(limb["boot"]), colors["boot"])
        _shade_bone(
            rgb, boot_owned,
            at((limb["boot"][9],))[0], at((limb["boot"][5],))[0],
            colors["boot_shadow"], 0.34,
        )
        _paint(mask, rgb, at(limb["sole"]), colors["sole"])
        arm = np.zeros(mask.shape, dtype=bool)
        for key in ("elbow", "upper", "fore"):
            arm |= _fill_new(mask, rgb, at(limb[key]), colors["jacket"])
        _shade_bone(
            rgb, arm,
            at((limb["shoulder_at"],))[0], at((limb["wrist_at"],))[0],
            colors["jacket_shadow"], 0.40,
        )
        _paint(mask, rgb, at(limb["cuff"]), colors["jacket_shadow"])
        hand = _fill_new(mask, rgb, at(limb["hand"]), colors["skin"])
        finger = limb["hand"][5]
        _shade_bone(rgb, hand, at((limb["wrist_at"],))[0], at((finger,))[0], colors["skin_shadow"], 0.36)

    for _layer, side in sorted(groups):
        if _layer == 0:
            paint_side(side)

    jacket_owned = _fill_new(mask, rgb, at(jacket), colors["jacket"])
    jacket_u8 = np.where(jacket_owned, np.uint8(255), np.uint8(0))
    _recolor(rgb, at(back), colors["jacket_shadow"], jacket_u8)
    _recolor(rgb, at(pocket), colors["jacket_shadow"], jacket_u8)
    if mark is not None:
        _paint(mask, rgb, at(mark), colors["mark"], jacket_u8)
    _paint(mask, rgb, at(belt), colors["belt"], jacket_u8)
    _paint(mask, rgb, at(buckle), colors["buckle_shadow"], jacket_u8)
    _paint(mask, rgb, at(buckle_top), colors["buckle"], jacket_u8)

    for _layer, side in sorted(groups):
        if _layer == 2:
            paint_side(side)

    face_owned = _fill_new(mask, rgb, at(face), colors["skin"])
    face_u8 = np.where(face_owned, np.uint8(255), np.uint8(0))
    _recolor(rgb, at(jaw), colors["skin_shadow"], face_u8)
    _recolor(rgb, at(nose), colors["skin_shadow"], face_u8)
    _paint(mask, rgb, at(ear), colors["skin"])
    _recolor(rgb, at(ear_in), colors["skin_shadow"], mask)
    _paint(mask, rgb, at(eye), (250, 248, 244))
    iris_xy = (int(round(iris_at[0] - min_x)), int(round(iris_at[1] - min_y)))
    catch_xy = (int(round(catch_at[0] - min_x)), int(round(catch_at[1] - min_y)))
    iris_r = max(2, int(round(0.0062 * height)))
    cv2.circle(rgb, iris_xy, iris_r, colors["ink"], -1, lineType=cv2.LINE_AA)
    cv2.circle(rgb, catch_xy, max(1, iris_r // 2), (255, 255, 255), -1, lineType=cv2.LINE_AA)

    _paint(mask, rgb, at(collar), colors["collar"])
    collar_layer = np.zeros(mask.shape, np.uint8)
    collar_poly = np.round(np.asarray(at(collar), np.float64)).astype(np.int32)
    cv2.fillPoly(collar_layer, [collar_poly], 255, lineType=cv2.LINE_AA)
    collar_ys, collar_xs = np.where((collar_layer > 128) & (mask > 0))
    if len(collar_ys) > 12:
        split = float(np.quantile(collar_ys, 0.52))
        dark = collar_ys >= split
        rgb[collar_ys[dark], collar_xs[dark]] = colors["collar_shadow"]

    before_hair = mask.copy()
    _paint(mask, rgb, at(cap), colors["hair"])
    if tail is not None:
        _paint(mask, rgb, at(tail), colors["hair"])
    hair_u8 = np.where((mask > 0) & (before_hair == 0), np.uint8(255), np.uint8(0))
    _recolor(rgb, at(highlight), colors["hair_hi"], hair_u8)

    line_w = 2
    _ink_line(rgb, mask, at(bangs), colors["ink"], line_w)
    _ink_line(rgb, mask, at(collar_hem), colors["ink"], line_w)
    _ink_line(rgb, mask, at(belt[:3]), colors["ink"], line_w)
    _ink_line(rgb, mask, at(list(belt[3:]) + [belt[0]]), colors["ink"], line_w)
    _ink_line(rgb, mask, at(seam), colors["ink"], line_w)
    _ink_line(rgb, mask, at(pocket[1:]), colors["ink"], line_w)
    _ink_line(rgb, mask, at(lid), colors["ink"], line_w)
    _ink_line(rgb, mask, at(mouth), colors["skin_shadow"], line_w)
    for limb in limbs.values():
        _ink_line(rgb, mask, at(limb["boot_line"]), colors["ink"], line_w)
        _ink_line(rgb, mask, at(limb["cuff_line"]), colors["ink"], line_w)

    body = np.zeros((img_h, width, 4), np.uint8)
    body[:, :, :3] = rgb
    body[:, :, 3] = np.where(mask > 0, 255, 0).astype(np.uint8)
    body = _stroke_outside(body, 3, colors["ink"])

    canvas = np.zeros_like(body)
    if show_weapon and weapon is not None and weapon_origin is not None:
        weapon_left = int(round(weapon_origin[0] - min_x))
        weapon_top = int(round(weapon_origin[1] - min_y))
        if not weapon_front:
            _paste_rgba(canvas, weapon, weapon_left, weapon_top)
    else:
        weapon_left = weapon_top = 0
    _paste_rgba(canvas, body, 0, 0)
    if show_weapon and weapon is not None and weapon_origin is not None and weapon_front:
        _paste_rgba(canvas, weapon, weapon_left, weapon_top)

    def locate(point):
        return (float(point[0] - min_x), float(point[1] - min_y))

    soles = {}
    for side, limb in limbs.items():
        foot = limb["boot"]
        bottom = max(point[1] for point in foot)
        kept = [point[0] for point in foot if point[1] >= bottom - 2.0]
        soles[side] = (float(np.mean(kept)) - min_x, bottom - min_y)
    torso_box_pts = at(jacket)
    info = {
        "pelvis": locate(pelvis),
        "grip": locate(solved["wrist_r"]),
        "tip": locate(tip),
        "soles": soles,
        "torso_box": (
            min(point[0] for point in torso_box_pts),
            min(point[1] for point in torso_box_pts),
            max(point[0] for point in torso_box_pts),
            max(point[1] for point in torso_box_pts),
        ),
        "weapon_front": weapon_front,
        "spec": spec,
        "weapon_mode": weapon_mode,
    }
    image, info = _crop_frame(canvas, info)
    info["image"] = image
    return info


def _paste_rgba(dst: np.ndarray, src: np.ndarray, left: int, top: int) -> None:
    h, w = src.shape[:2]
    x0, y0 = max(0, left), max(0, top)
    x1, y1 = min(dst.shape[1], left + w), min(dst.shape[0], top + h)
    if x1 <= x0 or y1 <= y0:
        return
    tile = src[y0 - top:y0 - top + (y1 - y0), x0 - left:x0 - left + (x1 - x0)]
    _over(dst[y0:y1, x0:x1], tile)


def export_review(design: str, actors: dict, render, out: Path) -> dict:
    lin = actors["linlan"]
    contact_spec, _stance = walk_frame_spec(0)
    passing_spec, _stance = walk_frame_spec(6)
    contact = render(lin, contact_spec, "follow")
    passing = render(lin, passing_spec, "follow")
    windup = render(lin, attack_frames()[5][0], "back")
    strike = render(lin, attack_frames()[8][0], "front")
    guard = face_left(render(actors["chen"], GUARD, "front"))
    hit = face_left(render(actors["chen"], HIT, "follow"))
    gap, tip_world, inside = hit_gap(strike, guard)
    contact_img = still_one(contact, "contact", "l")
    passing_img = still_one(passing, "passing", "l")
    windup_img = still_pair(windup, guard, gap, "windup")
    strike_img = still_pair(strike, guard, gap, "strike", tip_world if inside else None)
    hit_img = still_pair(strike, hit, gap, "hit")
    out.mkdir(parents=True, exist_ok=True)
    paths = {}
    for label, image in (
        ("contact", contact_img),
        ("passing", passing_img),
        ("windup", windup_img),
        ("strike", strike_img),
        ("hit", hit_img),
    ):
        path = out / f"{label}.png"
        Image.fromarray(image, "RGB").save(path)
        paths[label] = str(path)
    laid = layout_walk(lin, render)
    center = laid[0]["screen_pelvis"]
    period = abs(laid[-1]["travel"]) or 1.0
    walk_frames = [render_walk_frame(item, center, period) for item in laid]
    walk_path = out / "walk.mp4"
    _encode(walk_frames, walk_path)
    paths["walk"] = str(walk_path)
    Image.fromarray(_sheet([walk_frames[index] for index in (0, 3, 6, 9, 12, 15, 18, 21, 24)], 3), "RGB").save(out / "walk_sheet.png")
    Image.fromarray(np.concatenate([walk_frames[0], walk_frames[-1]], axis=1), "RGB").save(out / "walk_ends.png")
    paths["walk_sheet"] = str(out / "walk_sheet.png")
    paths["walk_ends"] = str(out / "walk_ends.png")
    diff = int(np.abs(walk_frames[0].astype(np.int16) - walk_frames[-1].astype(np.int16)).sum())
    report = {
        "design": design,
        "paths": paths,
        "gap": float(gap),
        "tip_inside_torso": bool(inside),
        "tip_world": [float(tip_world[0]), float(tip_world[1])],
        "walk_end_absdiff": diff,
        "walk_travel": float(laid[-1]["travel"]),
        "weapon_behind_windup": not bool(windup["weapon_front"]),
        "weapon_front_strike": bool(strike["weapon_front"]),
        "passing_clearance": None if not passing["soles"]["l"] or not passing["soles"]["r"] else float(
            passing["soles"]["l"][1] - passing["soles"]["r"][1]
        ),
        "grip": [float(strike["grip"][0]), float(strike["grip"][1])],
        "tip": [float(strike["tip"][0]), float(strike["tip"][1])],
    }
    (out / "measure.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "design", "tip_inside_torso", "walk_end_absdiff", "walk_travel",
        "passing_clearance", "weapon_behind_windup", "weapon_front_strike",
    )}, ensure_ascii=False), flush=True)
    return report


def export_design2() -> dict:
    out = REVIEW / "design2"
    actors = {name: build_identity(name) for name in ("linlan", "chen")}
    for name, actor in actors.items():
        print(name, {key: list(value) if isinstance(value, tuple) else value for key, value in actor["colors"].items()}, flush=True)
    return export_review("shapes", actors, render_whole, out)


def _ellipse_pts(cx: float, cy: float, rx: float, ry: float, count: int = 16) -> list[tuple[float, float]]:
    points = []
    for index in range(count):
        theta = 2.0 * math.pi * index / count
        points.append((cx + math.cos(theta) * rx, cy + math.sin(theta) * ry))
    return points


def _crop_rgba(image: np.ndarray) -> np.ndarray:
    ys, xs = np.where(image[:, :, 3] > 16)
    if len(xs) == 0:
        return image
    return np.ascontiguousarray(image[int(ys.min()): int(ys.max()) + 1, int(xs.min()): int(xs.max()) + 1])


def render_planar(actor: dict, spec: dict, weapon_mode: str = "follow", view: str = "front") -> dict:
    """Front or back of the same body ``render_whole`` draws from the side.

    Bone lengths, costume colors, and ``_head_marks`` are shared. A back view
    has hair, jacket, belt, and boots, and no face.
    """
    if view not in ("front", "back"):
        raise ValueError(view)
    bones = actor["bones"]
    height = float(bones["H"])
    colors = actor["colors"]
    marks = _head_marks(height)
    ink = colors["ink"]
    neck_y = -float(bones["hip_to_neck"])
    shoulder_y = -float(bones["hip_to_shoulder"])
    hem_y = 0.074 * height
    belt_y = -0.014 * height
    ankle_y = float(bones["thigh"]) + float(bones["shin"])
    sole_y = ankle_y + 0.020 * height
    shoulder_x = float(bones["shoulder_half"])
    hip_x = max(float(bones["hip_half"]) * 0.85, 0.040 * height)
    # Character-right is screen-left when the figure faces the camera.
    right = -1.0 if view == "front" else 1.0
    back = view == "back"
    ponytail = bool(actor["ponytail"])
    waist_x = shoulder_x * 0.62
    hem_x = shoulder_x * 0.80
    neck_half = 0.020 * height

    def sx(character_x: float) -> float:
        return right * character_x

    def on_neck(points) -> list[tuple[float, float]]:
        return [(point[0], point[1] + neck_y) for point in points]

    cloud: list[tuple[float, float]] = []
    ops: list[tuple] = []

    def fill(points, color) -> None:
        points = list(points)
        ops.append(("fill", points, color))
        cloud.extend(points)

    def limb(points, color, shadow, start, stop) -> None:
        points = list(points)
        ops.append(("limb", points, color, shadow, start, stop))
        cloud.extend(points)

    def ink_pts(points, color, width: int = 2) -> None:
        ops.append(("ink", list(points), color, width))

    def dot(center, radius: int, color) -> None:
        ops.append(("dot", center, int(radius), color))
        cloud.append((center[0] - radius, center[1] - radius))
        cloud.append((center[0] + radius, center[1] + radius))

    r_hip, r_knee, r_ankle = 0.040 * height, 0.030 * height, 0.022 * height
    r_shoulder, r_elbow, r_wrist = 0.030 * height, 0.024 * height, 0.018 * height
    soles: dict[str, tuple[float, float]] = {}
    wrists: dict[str, tuple[float, float]] = {}
    for character_sign, side_name in ((1.0, "r"), (-1.0, "l")):
        hip = (sx(character_sign * hip_x), 0.0)
        knee = (sx(character_sign * hip_x * 0.98), float(bones["thigh"]))
        ankle = (sx(character_sign * hip_x * 0.94), ankle_y)
        fill(_disk(knee, r_knee * 0.9), colors["pants"])
        limb(_quad(hip, knee, r_hip, r_knee), colors["pants"], colors["pants_shadow"], hip, knee)
        limb(_quad(knee, ankle, r_knee * 0.92, r_ankle), colors["pants"], colors["pants_shadow"], knee, ankle)
        boot_top = ankle_y - 0.076 * height
        half = 0.024 * height
        sole_half = 0.032 * height * (0.82 if back else 1.0)
        ax = ankle[0]
        if back:
            boot = [
                (ax - half * 0.9, boot_top),
                (ax + half * 0.9, boot_top),
                (ax + half * 0.85, sole_y - 0.010 * height),
                (ax + sole_half, sole_y),
                (ax - sole_half * 0.86, sole_y),
                (ax - half * 0.7, sole_y - 0.008 * height),
            ]
        else:
            boot = [
                (ax - half, boot_top),
                (ax + half, boot_top),
                (ax + half, ankle_y),
                (ax + sole_half, sole_y),
                (ax - sole_half, sole_y),
                (ax - half, ankle_y),
            ]
        limb(boot, colors["boot"], colors["boot_shadow"], (ax, boot_top), (ax, sole_y))
        fill([
            (ax - sole_half, sole_y - 0.008 * height),
            (ax + sole_half, sole_y - 0.008 * height),
            (ax + sole_half * 0.92, sole_y),
            (ax - sole_half * 0.92, sole_y),
        ], colors["sole"])
        ink_pts([(ax - half * 0.4, boot_top + 0.01 * height), (ax, sole_y - 0.006 * height)], ink)
        soles[side_name] = (ax, sole_y)

    if ponytail and not back:
        # Same tail as the side and back: it leaves the nape and hangs down the
        # back. Painted before the coat, so the shoulders cover the chest.
        # It stays narrow through the face, flares only beside the neck, and
        # tucks back inside the shoulder line.
        nape_y = neck_y + marks["ear_bot"]
        tip_y = nape_y + 0.17 * height
        flank = min(0.062 * height, shoulder_x * 0.52)
        fill([
            (-0.024 * height, neck_y + marks["chin_y"] - 0.006 * height),
            (-flank, neck_y + 0.004 * height),
            (-0.020 * height, neck_y + 0.046 * height),
            (-0.012 * height, tip_y),
            (0.012 * height, tip_y),
            (0.020 * height, neck_y + 0.046 * height),
            (flank, neck_y + 0.004 * height),
            (0.024 * height, neck_y + marks["chin_y"] - 0.006 * height),
        ], colors["hair"])

    jacket = [
        (-neck_half, neck_y + 0.018 * height),
        (-shoulder_x, shoulder_y + 0.010 * height),
        (-waist_x, shoulder_y * 0.42),
        (-hem_x, hem_y),
        (hem_x, hem_y),
        (waist_x, shoulder_y * 0.42),
        (shoulder_x, shoulder_y + 0.010 * height),
        (neck_half, neck_y + 0.018 * height),
    ]
    fill(jacket, colors["jacket"])
    if back:
        fill([
            (-hem_x * 0.26, shoulder_y + 0.05 * height),
            (hem_x * 0.26, shoulder_y + 0.05 * height),
            (hem_x * 0.20, hem_y - 0.012 * height),
            (-hem_x * 0.20, hem_y - 0.012 * height),
        ], colors["jacket_shadow"])
    belt_half = waist_x * 0.94
    fill([
        (-belt_half, belt_y),
        (belt_half, belt_y),
        (belt_half, belt_y + 0.020 * height),
        (-belt_half, belt_y + 0.020 * height),
    ], colors["belt"])
    if not back:
        fill([
            (-0.014 * height, belt_y - 0.004 * height),
            (0.014 * height, belt_y - 0.004 * height),
            (0.014 * height, belt_y + 0.024 * height),
            (-0.014 * height, belt_y + 0.024 * height),
        ], colors["buckle_shadow"])
        fill([
            (-0.012 * height, belt_y - 0.002 * height),
            (0.012 * height, belt_y - 0.002 * height),
            (0.012 * height, belt_y + 0.008 * height),
            (-0.012 * height, belt_y + 0.008 * height),
        ], colors["buckle"])
        if colors.get("mark"):
            mark_x = sx(-0.30 * shoulder_x)
            mark_y = shoulder_y * 0.52
            mw, mh = 0.026 * height, 0.014 * height
            mark = [
                (mark_x - mw, mark_y - mh),
                (mark_x + mw, mark_y - mh),
                (mark_x + mw, mark_y + mh),
                (mark_x - mw, mark_y + mh),
            ]
            fill(mark, colors["mark"])
            ink_pts(mark + [mark[0]], ink, 2)
    ink_pts([(0.0, neck_y + 0.03 * height), (0.0, belt_y)], ink, 2)
    ink_pts([
        (-belt_half, belt_y),
        (belt_half, belt_y),
    ], ink, 2)
    if back:
        ink_pts([
            (-shoulder_x * 0.72, shoulder_y + 0.04 * height),
            (shoulder_x * 0.72, shoulder_y + 0.04 * height),
        ], ink, 2)

    # Sleeves after the jacket so each arm overlaps the shoulder instead of floating beside it.
    for character_sign, side_name in ((1.0, "r"), (-1.0, "l")):
        shoulder = (sx(character_sign * shoulder_x * 0.78), shoulder_y + 0.018 * height)
        elbow = (sx(character_sign * shoulder_x * 0.86), shoulder_y + float(bones["upper_arm"]))
        wrist = (sx(character_sign * shoulder_x * 0.80), elbow[1] + float(bones["forearm"]))
        wrists[side_name] = wrist
        fill(_disk(shoulder, r_shoulder), colors["jacket"])
        fill(_disk(elbow, r_elbow * 0.85), colors["jacket"])
        limb(_quad(shoulder, elbow, r_shoulder, r_elbow), colors["jacket"], colors["jacket_shadow"], shoulder, elbow)
        limb(_quad(elbow, wrist, r_elbow, r_wrist), colors["jacket"], colors["jacket_shadow"], elbow, wrist)
        fill(_quad(_along(elbow, wrist, 0.78), wrist, r_wrist * 1.15, r_wrist), colors["jacket_shadow"])
        thumb = right * character_sign
        hand = [(wrist[0] + thumb * point[0], wrist[1] + point[1]) for point in _hand_local(height)]
        limb(hand, colors["skin"], colors["skin_shadow"], wrist, hand[5])

    fill([
        (-0.026 * height, neck_y + marks["chin_y"] + 0.008 * height),
        (0.026 * height, neck_y + marks["chin_y"] + 0.008 * height),
        (0.024 * height, neck_y + 0.030 * height),
        (-0.024 * height, neck_y + 0.030 * height),
    ], colors["skin"])

    if back:
        band_top = neck_y - 0.012 * height
        fill([
            (-0.036 * height, band_top),
            (0.036 * height, band_top),
            (0.042 * height, neck_y + 0.026 * height),
            (-0.042 * height, neck_y + 0.026 * height),
        ], colors["collar"])
        fill([
            (-0.036 * height, neck_y + 0.008 * height),
            (0.036 * height, neck_y + 0.008 * height),
            (0.040 * height, neck_y + 0.026 * height),
            (-0.040 * height, neck_y + 0.026 * height),
        ], colors["collar_shadow"])
    elif ponytail:
        fill([
            (-0.030 * height, neck_y - 0.014 * height),
            (-0.052 * height, neck_y + 0.042 * height),
            (-0.016 * height, neck_y + 0.026 * height),
            (0.0, neck_y + 0.050 * height),
            (0.016 * height, neck_y + 0.026 * height),
            (0.052 * height, neck_y + 0.042 * height),
            (0.030 * height, neck_y - 0.014 * height),
        ], colors["collar"])
        fill([
            (-0.046 * height, neck_y + 0.030 * height),
            (0.0, neck_y + 0.050 * height),
            (0.046 * height, neck_y + 0.030 * height),
            (0.016 * height, neck_y + 0.024 * height),
            (-0.016 * height, neck_y + 0.024 * height),
        ], colors["collar_shadow"])
    else:
        fill([
            (-0.028 * height, neck_y - 0.016 * height),
            (-0.034 * height, neck_y + 0.022 * height),
            (0.034 * height, neck_y + 0.022 * height),
            (0.028 * height, neck_y - 0.016 * height),
        ], colors["collar"])
        fill([
            (-0.032 * height, neck_y + 0.006 * height),
            (0.032 * height, neck_y + 0.006 * height),
            (0.034 * height, neck_y + 0.022 * height),
            (-0.034 * height, neck_y + 0.022 * height),
        ], colors["collar_shadow"])

    ear_rx, ear_ry = 0.008 * height, 0.014 * height
    ear_y = neck_y + (marks["ear_top"] + marks["ear_bot"]) * 0.5
    for character_sign in (1.0, -1.0):
        ear_x = sx(character_sign * marks["half_w"] * 0.92)
        fill(_ellipse_pts(ear_x, ear_y, ear_rx, ear_ry, 12), colors["skin"])
        fill(_ellipse_pts(ear_x, ear_y + 0.003 * height, ear_rx * 0.42, ear_ry * 0.42, 8), colors["skin_shadow"])

    if not back:
        fill(on_neck(_front_face_local(height)), colors["skin"])

    hair_w = marks["half_w"] * (1.16 if ponytail else 0.98)
    if back:
        top = neck_y + marks["crown"]
        bot = neck_y + (0.006 * height if ponytail else -0.028 * height)
        fill(_ellipse_pts(0.0, (top + bot) * 0.5, hair_w, max(1.0, (bot - top) * 0.5), 20), colors["hair"])
        fill([
            (-0.012 * height, top + 0.012 * height),
            (0.016 * height, top + 0.016 * height),
            (0.006 * height, top + 0.034 * height),
            (-0.010 * height, top + 0.030 * height),
        ], colors["hair_hi"])
        if ponytail:
            tw = 0.020 * height
            y0 = neck_y + marks["ear_bot"]
            y1 = y0 + 0.17 * height
            fill([
                (-tw * 1.15, y0 - 0.02 * height),
                (tw * 1.15, y0 - 0.02 * height),
                (tw * 0.70, y1),
                (-tw * 0.70, y1),
            ], colors["hair"])
            fill([
                (-tw, y0),
                (tw, y0),
                (tw, y0 + 0.014 * height),
                (-tw, y0 + 0.014 * height),
            ], ink)
        else:
            ink_pts([
                (-hair_w, neck_y + marks["ear_bot"] - 0.008 * height),
                (0.0, neck_y + marks["ear_bot"] + 0.014 * height),
                (hair_w, neck_y + marks["ear_bot"] - 0.008 * height),
            ], ink, 2)
    else:
        brow = marks["brow_y"]
        cap = [
            (-hair_w, brow),
            (-hair_w * 0.70, marks["crown"] + 0.012 * height),
            (0.0, marks["crown"]),
            (hair_w * 0.70, marks["crown"] + 0.012 * height),
            (hair_w, brow),
            (hair_w * 0.42, brow + 0.003 * height),
            (0.0, brow + 0.006 * height),
            (-hair_w * 0.42, brow + 0.003 * height),
        ]
        fill(on_neck(cap), colors["hair"])
        fill(on_neck([
            (-0.010 * height, marks["crown"] + 0.012 * height),
            (0.018 * height, marks["crown"] + 0.014 * height),
            (0.008 * height, marks["crown"] + 0.028 * height),
            (-0.012 * height, marks["crown"] + 0.026 * height),
        ]), colors["hair_hi"])
        # Bridge and a round tip. The silhouette of the head does not include the nose.
        tip_y = marks["nose_y"]
        bridge_top = marks["eye_y"] + marks["eye_ry"] + 0.004 * height
        fill(on_neck([
            (-0.0035 * height, bridge_top),
            (0.0035 * height, bridge_top),
            (0.004 * height, tip_y),
            (-0.004 * height, tip_y),
        ]), colors["skin_shadow"])
        fill(on_neck(_ellipse_pts(0.0, tip_y + 0.005 * height, 0.009 * height, 0.006 * height, 14)), colors["skin_shadow"])
        nostril = _darken(colors["skin_shadow"], 0.62)
        for sign in (-1.0, 1.0):
            dot((sign * 0.005 * height, neck_y + tip_y + 0.009 * height), max(1, int(round(0.0028 * height))), nostril)
        for sign in (-1.0, 1.0):
            eye = on_neck(_almond(sign * marks["eye_dx"], marks["eye_y"], marks["eye_rx"], marks["eye_ry"]))
            fill(eye, (250, 248, 244))
            ink_pts(eye[:3], ink, 2)
            brow_y = marks["eye_y"] - marks["eye_ry"] * 1.85
            ink_pts(on_neck([
                (sign * marks["eye_dx"] - marks["eye_rx"], brow_y),
                (sign * marks["eye_dx"] + marks["eye_rx"] * 0.8, brow_y + 0.002 * height),
            ]), ink, 2)
            iris = (
                sign * marks["eye_dx"] + marks["eye_rx"] * 0.15,
                neck_y + marks["eye_y"],
            )
            dot(iris, max(2, int(round(0.0055 * height))), ink)
            dot((iris[0] - sign * marks["eye_rx"] * 0.35, iris[1] - marks["eye_ry"] * 0.35), max(1, int(round(0.0024 * height))), (255, 255, 255))
        ink_pts(on_neck([
            (-0.011 * height, marks["lip_y"]),
            (0.011 * height, marks["lip_y"] + 0.002 * height),
        ]), colors["skin_shadow"], 2)

    show_weapon = weapon_mode != "off"
    weapon = None
    weapon_origin = None
    wrist_r = wrists["r"]
    if show_weapon:
        weapon, tip, weapon_origin = _weapon_layer(wrist_r, 0.0, height, ink)
        cloud.append(tip)
        cloud.append(weapon_origin)
        cloud.append((weapon_origin[0] + weapon.shape[1], weapon_origin[1] + weapon.shape[0]))
    else:
        tip = wrist_r

    margin = 16
    min_x = math.floor(min(point[0] for point in cloud)) - margin
    min_y = math.floor(min(point[1] for point in cloud)) - margin
    max_x = math.ceil(max(point[0] for point in cloud)) + margin
    max_y = math.ceil(max(point[1] for point in cloud)) + margin
    width = max(1, max_x - min_x + 1)
    img_h = max(1, max_y - min_y + 1)
    mask = np.zeros((img_h, width), np.uint8)
    rgb = np.zeros((img_h, width, 3), np.uint8)

    def at(points):
        return [(point[0] - min_x, point[1] - min_y) for point in points]

    for op in ops:
        kind = op[0]
        if kind == "fill":
            _paint(mask, rgb, at(op[1]), op[2])
        elif kind == "limb":
            owned = _fill_new(mask, rgb, at(op[1]), op[2])
            _shade_bone(rgb, owned, at([op[4]])[0], at([op[5]])[0], op[3])
        elif kind == "ink":
            _ink_line(rgb, mask, at(op[1]), op[2], op[3])
        else:
            center = at([op[1]])[0]
            radius = op[2]
            cv2.circle(mask, (int(round(center[0])), int(round(center[1]))), radius, 255, -1, lineType=cv2.LINE_AA)
            cv2.circle(rgb, (int(round(center[0])), int(round(center[1]))), radius, op[3], -1, lineType=cv2.LINE_AA)

    body = np.zeros((img_h, width, 4), np.uint8)
    body[:, :, :3] = rgb
    body[:, :, 3] = np.where(mask > 0, 255, 0).astype(np.uint8)
    body = _stroke_outside(body, 3, ink)
    canvas = np.zeros_like(body)
    if show_weapon and weapon is not None and weapon_origin is not None:
        weapon_left = int(round(weapon_origin[0] - min_x))
        weapon_top = int(round(weapon_origin[1] - min_y))
        _paste_rgba(canvas, weapon, weapon_left, weapon_top)
    _paste_rgba(canvas, body, 0, 0)

    jacket_at = at(jacket)
    info = {
        "pelvis": (0.0 - min_x, 0.0 - min_y),
        "grip": (wrist_r[0] - min_x, wrist_r[1] - min_y),
        "tip": (float(tip[0]) - min_x, float(tip[1]) - min_y),
        "soles": {name: (point[0] - min_x, point[1] - min_y) for name, point in soles.items()},
        "torso_box": (
            min(point[0] for point in jacket_at),
            min(point[1] for point in jacket_at),
            max(point[0] for point in jacket_at),
            max(point[1] for point in jacket_at),
        ),
        "weapon_front": bool(show_weapon),
        "spec": spec,
        "weapon_mode": weapon_mode,
        "view": view,
    }
    image, info = _crop_frame(canvas, info)
    info["image"] = image
    info["view"] = view
    return info


def view_for_facing(facing: str) -> str:
    """Map a stage direction to front, side, or back.

    Camera-facing directions use the front drawing. Away uses the back.
    Left and right use the side drawing; the caller mirrors left.
    """
    key = (facing or "right").strip().lower()
    if key in {"front", "camera", "toward", "forward"}:
        return "front"
    if key in {"back", "away", "backward"}:
        return "back"
    return "side"


def render_facing(actor: dict, spec: dict, weapon_mode: str = "follow", facing: str = "right") -> dict:
    """Draw the view selected by ``facing`` and mirror the side when facing left."""
    view = view_for_facing(facing)
    if view == "front":
        frame = render_planar(actor, spec, weapon_mode, "front")
    elif view == "back":
        frame = render_planar(actor, spec, weapon_mode, "back")
    else:
        frame = render_whole(actor, spec, weapon_mode)
        if (facing or "").strip().lower() in {"left", "l"}:
            frame = face_left(frame)
    frame["view"] = view
    frame["facing"] = facing
    return frame


_ROVER_SIDE: np.ndarray | None = None


def _load_rover_side() -> np.ndarray:
    global _ROVER_SIDE
    if _ROVER_SIDE is None:
        _ROVER_SIDE = _crop_rgba(np.array(Image.open(ROVER_FILE).convert("RGBA")))
    return _ROVER_SIDE


def _paint_round(canvas: np.ndarray, x: float, y: float, w: float, h: float, radius: float, color, ink, stroke: int = 3) -> None:
    mask = np.zeros(canvas.shape[:2], np.uint8)
    radius_i = int(max(1, min(radius, w / 2.0, h / 2.0)))
    x0, y0, x1, y1 = int(round(x)), int(round(y)), int(round(x + w)), int(round(y + h))
    cv2.rectangle(mask, (x0 + radius_i, y0), (x1 - radius_i, y1), 255, -1)
    cv2.rectangle(mask, (x0, y0 + radius_i), (x1, y1 - radius_i), 255, -1)
    for cx, cy in (
        (x0 + radius_i, y0 + radius_i),
        (x1 - radius_i, y0 + radius_i),
        (x0 + radius_i, y1 - radius_i),
        (x1 - radius_i, y1 - radius_i),
    ):
        cv2.circle(mask, (cx, cy), radius_i, 255, -1, lineType=cv2.LINE_AA)
    canvas[mask > 0] = (*color, 255)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(canvas, contours, -1, (*ink, 255), stroke, cv2.LINE_AA)


def _draw_rover(view: str) -> np.ndarray:
    """Front or back of the side sprite: the capsule's end, same height, wheels on the ground.

    The side view is long. Turned, the body is the rounded cross-section of that
    capsule, with the same white upper hull, orange band, and gold wheels.
    """
    side = _load_rover_side()
    height = int(side.shape[0])
    wheel_r = max(8, int(round(0.115 * height)))
    width = int(round(0.70 * height))
    canvas = np.zeros((height, width, 4), np.uint8)
    ink = (20, 32, 46)
    white = (246, 248, 236)
    orange = (255, 148, 0)
    chassis = (44, 60, 74)
    tire = (32, 46, 56)
    gold = (220, 168, 72)
    gold_dk = (150, 104, 40)
    window = (12, 36, 52)
    glass = (186, 214, 216)
    sensor = (186, 206, 208)
    cy = (height - 1) - wheel_r
    left_x = int(round(width * 0.28))
    right_x = int(round(width * 0.72))
    body_w = int(round(0.50 * height))
    body_x = (width - body_w) // 2
    body_top = int(round(0.16 * height))
    body_bot = int(round(0.74 * height))
    # Radius half the width makes the sides of the pod into the capsule's round end.
    _paint_round(canvas, body_x, body_top, body_w, body_bot - body_top, body_w / 2, white, ink, 3)
    split = int(round(0.50 * height))
    orange_bot = int(round(0.66 * height))
    band = np.zeros(canvas.shape[:2], np.uint8)
    cv2.rectangle(band, (0, split), (width, orange_bot), 255, -1)
    canvas[(band > 0) & (canvas[:, :, 3] > 0)] = (*orange, 255)
    low = np.zeros(canvas.shape[:2], np.uint8)
    cv2.rectangle(low, (0, orange_bot), (width, body_bot), 255, -1)
    canvas[(low > 0) & (canvas[:, :, 3] > 0)] = (*chassis, 255)
    cv2.line(canvas, (body_x + 6, split), (body_x + body_w - 6, split), (*ink, 255), 2, cv2.LINE_AA)
    # Small dish, the same mast the side sprite carries, not a second box.
    dish_w = int(round(0.28 * height))
    dish_h = int(round(0.055 * height))
    _paint_round(canvas, (width - dish_w) // 2, int(round(0.055 * height)), dish_w, dish_h, dish_h / 2, (214, 226, 228), ink, 2)
    mast_w = max(4, int(round(0.04 * height)))
    cv2.rectangle(
        canvas,
        ((width - mast_w) // 2, int(round(0.10 * height))),
        ((width + mast_w) // 2, body_top + 4),
        (*sensor, 255),
        -1,
        lineType=cv2.LINE_AA,
    )
    if view == "front":
        win_w = int(round(body_w * 0.62))
        win_h = int(round(0.16 * height))
        win_x = body_x + (body_w - win_w) // 2
        win_y = int(round(0.26 * height))
        _paint_round(canvas, win_x, win_y, win_w, win_h, min(16, win_w / 3), window, ink, 2)
        cv2.line(canvas, (win_x + 6, win_y + 6), (win_x + win_w // 2, win_y + win_h - 8), (*glass, 255), 3, cv2.LINE_AA)
    else:
        panel_w = int(round(body_w * 0.40))
        panel_h = int(round(0.09 * height))
        panel_x = body_x + (body_w - panel_w) // 2
        panel_y = int(round(0.30 * height))
        _paint_round(canvas, panel_x, panel_y, panel_w, panel_h, 8, (232, 236, 228), ink, 2)
        for index, light in enumerate(((220, 64, 48), (240, 176, 48))):
            cx = width // 2 + (index * 2 - 1) * int(body_w * 0.16)
            cy_light = panel_y + panel_h + int(0.035 * height)
            cv2.circle(canvas, (cx, cy_light), max(4, int(0.016 * height)), (*light, 255), -1, lineType=cv2.LINE_AA)
            cv2.circle(canvas, (cx, cy_light), max(4, int(0.016 * height)), (*ink, 255), 2, lineType=cv2.LINE_AA)

    def wheel(cx: int) -> None:
        cv2.circle(canvas, (cx, cy), wheel_r, (*tire, 255), -1, lineType=cv2.LINE_AA)
        cv2.circle(canvas, (cx, cy), int(wheel_r * 0.62), (*gold, 255), -1, lineType=cv2.LINE_AA)
        for spoke in range(5):
            theta = math.radians(-90 + spoke * 72)
            x2 = int(round(cx + math.cos(theta) * wheel_r * 0.58))
            y2 = int(round(cy + math.sin(theta) * wheel_r * 0.58))
            cv2.line(canvas, (cx, cy), (x2, y2), (*gold_dk, 255), 2, cv2.LINE_AA)
        cv2.circle(canvas, (cx, cy), max(2, int(wheel_r * 0.16)), (*ink, 255), -1, lineType=cv2.LINE_AA)
        cv2.circle(canvas, (cx, cy), wheel_r, (*ink, 255), 3, lineType=cv2.LINE_AA)

    wheel(left_x)
    wheel(right_x)
    return canvas


def rover_for_facing(facing: str = "right") -> np.ndarray:
    """AT-1 in the view ``facing`` selects. The published sprite is the side."""
    view = view_for_facing(facing)
    if view == "side":
        image = _load_rover_side()
        if (facing or "").strip().lower() in {"left", "l"}:
            image = np.ascontiguousarray(np.fliplr(image))
        return image
    return _draw_rover(view)


def _scale_rgba(image: np.ndarray, height: int) -> np.ndarray:
    src_h, src_w = image.shape[:2]
    if src_h == height:
        return image
    width = max(1, int(round(src_w * (height / src_h))))
    interp = cv2.INTER_AREA if height < src_h else cv2.INTER_CUBIC
    return cv2.resize(image, (width, height), interpolation=interp)


def _scale_frame(frame: dict, scale: float) -> dict:
    image = frame["image"]
    src_h, src_w = image.shape[:2]
    width = max(1, int(round(src_w * scale)))
    height = max(1, int(round(src_h * scale)))
    interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
    scaled = cv2.resize(image, (width, height), interpolation=interp)

    def point(value):
        if value is None:
            return None
        return (float(value[0]) * scale, float(value[1]) * scale)

    out = dict(frame)
    out["image"] = scaled
    out["pelvis"] = point(frame["pelvis"])
    out["grip"] = point(frame.get("grip"))
    out["tip"] = point(frame.get("tip"))
    out["soles"] = {name: point(value) for name, value in frame["soles"].items()}
    box = frame.get("torso_box")
    if box is not None:
        out["torso_box"] = (box[0] * scale, box[1] * scale, box[2] * scale, box[3] * scale)
    return out


def _sole_span(frame: dict) -> float:
    ys = np.where(frame["image"][:, :, 3] > 20)[0]
    sole = max(frame["soles"]["l"][1], frame["soles"]["r"][1])
    if len(ys) == 0:
        return 1.0
    return float(sole - ys.min())


def _blit_rgb(canvas: np.ndarray, image: np.ndarray, left: int, top: int) -> None:
    src_h, src_w = image.shape[:2]
    x0, y0 = max(0, left), max(0, top)
    x1, y1 = min(canvas.shape[1], left + src_w), min(canvas.shape[0], top + src_h)
    if x1 <= x0 or y1 <= y0:
        return
    tile = image[y0 - top: y0 - top + (y1 - y0), x0 - left: x0 - left + (x1 - x0)]
    alpha = tile[:, :, 3:4].astype(np.float32) / 255.0
    dst = canvas[y0:y1, x0:x1].astype(np.float32)
    canvas[y0:y1, x0:x1] = np.clip(tile[:, :, :3] * alpha + dst * (1.0 - alpha), 0, 255).astype(np.uint8)


def export_turnaround() -> Path:
    """Three views of 林岚, 陈工, and 阿拓 on one ground line per row."""
    columns = (("front", "正面"), ("side", "侧面"), ("back", "背面"))
    rows = []
    for name, label in (("linlan", "林岚"), ("chen", "陈工")):
        actor = build_identity(name)
        views = {
            "front": render_facing(actor, IDLE, "off", "front"),
            "side": render_facing(actor, IDLE, "off", "right"),
            "back": render_facing(actor, IDLE, "off", "back"),
        }
        target = _sole_span(views["side"])
        for key in ("front", "back"):
            span = _sole_span(views[key])
            if span > 1.0 and abs(span - target) > 6.0:
                views[key] = _scale_frame(views[key], target / span)
        rows.append((label, views, "figure"))
        print(label, {key: round(_sole_span(frame), 1) for key, frame in views.items()}, flush=True)

    # The episode scales people by BODY_PX/TARGET_H and the rover to 118px.
    rover_h = max(1, int(round(118.0 * TARGET_H / 440.0)))
    rover = {
        "front": _scale_rgba(rover_for_facing("front"), rover_h),
        "side": _scale_rgba(rover_for_facing("right"), rover_h),
        "back": _scale_rgba(rover_for_facing("back"), rover_h),
    }
    rows.append(("阿拓", rover, "rover"))

    pad_x, pad_y = 36, 28
    label_w, header_h = 88, 54
    col_w = []
    for key, _title in columns:
        widest = 1
        for _label, payload, kind in rows:
            image = payload[key]["image"] if kind == "figure" else payload[key]
            widest = max(widest, image.shape[1])
        col_w.append(widest + pad_x)
    row_specs = []
    for _label, payload, kind in rows:
        above = 1.0
        below = 1.0
        for key, _title in columns:
            if kind == "figure":
                frame = payload[key]
                image = frame["image"]
                sole = max(frame["soles"]["l"][1], frame["soles"]["r"][1])
            else:
                image = payload[key]
                sole = image.shape[0] - 1
            above = max(above, sole)
            below = max(below, image.shape[0] - sole)
        row_specs.append((int(math.ceil(above)), int(math.ceil(below))))

    width = label_w + sum(col_w) + pad_x
    height = header_h + sum(above + below + pad_y for above, below in row_specs) + pad_y
    sheet = np.full((height, width, 3), (236, 231, 222), np.uint8)
    pil = Image.fromarray(sheet, "RGB")
    draw = ImageDraw.Draw(pil)
    font = _font(28)
    x = label_w
    for (_key, title), cell_w in zip(columns, col_w):
        draw.text((x + 12, 12), title, fill=(40, 32, 28), font=font)
        x += cell_w
    y = header_h
    for (label, payload, kind), (above, below) in zip(rows, row_specs):
        ground = y + above
        draw.text((16, ground - 80), label, fill=(40, 32, 28), font=font)
        x = label_w
        floor0 = max(0, ground + 2)
        floor1 = min(height, ground + 34)
        if floor1 > floor0:
            band = np.array(pil)
            band[floor0:floor1, label_w:width - 8] = (214, 206, 194)
            pil = Image.fromarray(band, "RGB")
            draw = ImageDraw.Draw(pil)
        draw.line([(label_w, ground), (width - 12, ground)], fill=(28, 24, 22), width=3)
        raster = np.array(pil)
        for (key, _title), cell_w in zip(columns, col_w):
            if kind == "figure":
                frame = payload[key]
                image = frame["image"]
                sole = max(frame["soles"]["l"][1], frame["soles"]["r"][1])
            else:
                image = payload[key]
                sole = image.shape[0] - 1
            left = int(round(x + (cell_w - image.shape[1]) / 2))
            top = int(round(ground - sole))
            _blit_rgb(raster, image, left, top)
            x += cell_w
        pil = Image.fromarray(raster, "RGB")
        draw = ImageDraw.Draw(pil)
        y = ground + below + pad_y
    out = REVIEW / "turnaround.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    pil.save(out)
    print(out, flush=True)
    return out


def main() -> None:
    export_design2()


if __name__ == "__main__":
    main()
