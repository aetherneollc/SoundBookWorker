"""Flash-style paper doll: circle-capped limbs, painted head, gloves, and boots.

Parts rotate about the circle at each joint. A child cap is centered on the same
point as the parent socket and extends at least one third of the joint radius
back into that socket, so a bend does not open a gap.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
CAST = ROOT / "media" / "m6_cast"

# Fallback joint heights as a fraction of the opaque body, top to sole.
PROP = {
    "neck": 0.16,
    "shoulder": 0.20,
    "elbow": 0.36,
    "wrist": 0.48,
    "hip": 0.52,
    "knee": 0.73,
    "ankle": 0.92,
}

# Clockwise degrees from a limb that hangs straight down. Parent rotation carries children.
POSES = (
    ("contact", {
        "upper_leg_l": -28, "lower_leg_l": 8,
        "upper_leg_r": 22, "lower_leg_r": 18,
        "upper_arm_l": 20, "lower_arm_l": 15,
        "upper_arm_r": -25, "lower_arm_r": 10,
        "torso": 4,
        "hip": 0.0,
    }),
    ("down", {
        "upper_leg_l": -8, "lower_leg_l": 28,
        "upper_leg_r": 8, "lower_leg_r": 55,
        "hip": 0.04,
    }),
    ("passing", {
        "upper_leg_l": 4, "lower_leg_l": 6,
        "upper_leg_r": -6, "lower_leg_r": 70,
        "hip": -0.02,
    }),
    ("up", {
        "upper_leg_l": 18, "lower_leg_l": 12,
        "upper_leg_r": -20, "lower_leg_r": 25,
        "hip": -0.03,
    }),
)

PART_FROM_POSE = {
    "upper_leg_l": "thigh_l",
    "lower_leg_l": "shin_l",
    "upper_leg_r": "thigh_r",
    "lower_leg_r": "shin_r",
    "upper_arm_l": "upper_arm_l",
    "lower_arm_l": "lower_arm_l",
    "upper_arm_r": "upper_arm_r",
    "lower_arm_r": "lower_arm_r",
    "torso": "torso",
}

# How far the child cap reaches back into the parent socket, as a fraction of radius.
JOINT_OVERLAP = 1.0 / 3.0


def rotate_cw(x: float, y: float, degrees: float) -> tuple[float, float]:
    """Clockwise rotation in image space, where y grows downward."""
    theta = math.radians(degrees)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    return (x * cos_t - y * sin_t, x * sin_t + y * cos_t)


def _load_rgba(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGBA"))


def _ipt(point) -> tuple[int, int]:
    return int(round(float(point[0]))), int(round(float(point[1])))


def _rgb(color) -> tuple[int, int, int]:
    return tuple(int(np.clip(int(c), 0, 255)) for c in color[:3])


def _dist(a, b) -> float:
    return float(np.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1])))


def _opaque_bounds(image: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.where(image[:, :, 3] > 32)
    if len(xs) == 0:
        h, w = image.shape[:2]
        return 0, 0, w, h
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def _view_meta(folder: Path, view: str) -> dict:
    doc = json.loads((folder / "cutout.json").read_text(encoding="utf-8"))
    record = doc["views"][view]
    parts = {part["name"]: part for part in record.get("parts", [])}
    return {
        "file": record["file"],
        "body_height": float(record["body_height"]),
        "feet_y": float(record["feet_y"]),
        "center_x": float(record["center_x"]),
        "anchors": record.get("face_anchors") or {},
        "parts": parts,
        "facing": doc.get("native_facing", "right"),
    }


def _joint(meta: dict, name: str) -> np.ndarray | None:
    part = meta["parts"].get(name)
    if not part:
        return None
    return np.asarray(part["joint"], dtype=np.float64)


def measure_bones(folder: Path) -> dict:
    """Bone lengths from the stored pose joints, else the proportion table."""
    meta = _view_meta(folder, "front")
    image = _load_rgba(folder / meta["file"])
    x0, y0, x1, y1 = _opaque_bounds(image)
    height = max(1.0, float(meta["body_height"]))
    top = float(meta["feet_y"]) - height
    pelvis = _joint(meta, "pelvis")
    neck = _joint(meta, "head")
    sh_l, sh_r = _joint(meta, "arm_l"), _joint(meta, "arm_r")
    el_l, el_r = _joint(meta, "fore_l"), _joint(meta, "fore_r")
    hip_l, hip_r = _joint(meta, "thigh_l"), _joint(meta, "thigh_r")
    kn_l, kn_r = _joint(meta, "shin_l"), _joint(meta, "shin_r")
    have = all(point is not None for point in (pelvis, neck, sh_l, sh_r, el_l, el_r, hip_l, hip_r, kn_l, kn_r))
    if not have:
        center_x = 0.5 * (x0 + x1)
        return {
            "H": height,
            "top": top,
            "feet_y": float(meta["feet_y"]),
            "center_x": center_x,
            "hip_to_neck": (PROP["hip"] - PROP["neck"]) * height,
            "hip_to_shoulder": (PROP["hip"] - PROP["shoulder"]) * height,
            "upper_arm": (PROP["elbow"] - PROP["shoulder"]) * height,
            "forearm": (PROP["wrist"] - PROP["elbow"]) * height,
            "thigh": (PROP["knee"] - PROP["hip"]) * height,
            "shin": (PROP["ankle"] - PROP["knee"]) * height,
            "shoulder_half": 0.16 * height,
            "hip_half": 0.09 * height,
            "source": "proportions",
        }
    upper = 0.5 * (_dist(sh_l, el_l) + _dist(sh_r, el_r))
    thigh = 0.5 * (_dist(hip_l, kn_l) + _dist(hip_r, kn_r))
    upper = float(np.clip(upper, 0.12 * height, 0.20 * height))
    thigh = float(np.clip(thigh, 0.16 * height, 0.28 * height))
    forearm = upper * ((PROP["wrist"] - PROP["elbow"]) / (PROP["elbow"] - PROP["shoulder"]))
    shin = thigh * ((PROP["ankle"] - PROP["knee"]) / (PROP["knee"] - PROP["hip"]))
    shoulder_y = 0.5 * (float(sh_l[1]) + float(sh_r[1]))
    return {
        "H": height,
        "top": top,
        "feet_y": float(meta["feet_y"]),
        "center_x": float(pelvis[0]),
        "hip_to_neck": abs(float(pelvis[1]) - float(neck[1])),
        "hip_to_shoulder": abs(float(pelvis[1]) - shoulder_y),
        "upper_arm": upper,
        "forearm": forearm,
        "thigh": thigh,
        "shin": shin,
        "shoulder_half": 0.5 * abs(float(sh_l[0]) - float(sh_r[0])),
        "hip_half": 0.5 * abs(float(hip_l[0]) - float(hip_r[0])),
        "source": "cutout-joints",
        "neck_xy": neck,
        "sh_l": sh_l,
        "sh_r": sh_r,
        "el_l": el_l,
        "el_r": el_r,
    }


def _median_color(pixels: np.ndarray, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    if pixels is None or len(pixels) < 12:
        return fallback
    lum = pixels.astype(np.float32).mean(axis=1)
    kept = pixels[(lum > 28) & (lum < 250)]
    if len(kept) < 12:
        kept = pixels
    med = np.median(kept, axis=0)
    return _rgb(med)


def _segment_pixels(image: np.ndarray, start, end, radius: float) -> np.ndarray:
    h, w = image.shape[:2]
    x0 = max(0, int(min(start[0], end[0]) - radius))
    x1 = min(w, int(max(start[0], end[0]) + radius) + 1)
    y0 = max(0, int(min(start[1], end[1]) - radius))
    y1 = min(h, int(max(start[1], end[1]) + radius) + 1)
    if x1 <= x0 or y1 <= y0:
        return np.zeros((0, 3), np.uint8)
    tile = image[y0:y1, x0:x1]
    alpha = tile[:, :, 3] > 32
    if not np.any(alpha):
        return np.zeros((0, 3), np.uint8)
    yy, xx = np.mgrid[y0:y1, x0:x1]
    pts = np.stack([xx[alpha], yy[alpha]], axis=1).astype(np.float64)
    axis = np.asarray(end, np.float64) - np.asarray(start, np.float64)
    length2 = float(np.dot(axis, axis))
    if length2 < 1.0:
        dist = np.linalg.norm(pts - np.asarray(start, np.float64), axis=1)
    else:
        t = np.clip(((pts - np.asarray(start, np.float64)) @ axis) / length2, 0.0, 1.0)
        closest = np.asarray(start, np.float64) + t[:, None] * axis
        dist = np.linalg.norm(pts - closest, axis=1)
    keep = dist <= radius
    return tile[:, :, :3][alpha][keep]


def sample_colors(image: np.ndarray, bones: dict, anchors: dict) -> dict[str, tuple[int, int, int]]:
    height = bones["H"]
    skin_box = anchors.get("face_box") or anchors.get("head_box")
    skin_px = np.zeros((0, 3), np.uint8)
    if skin_box:
        x, y, bw, bh = [int(v) for v in skin_box]
        h, w = image.shape[:2]
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(w, x + bw), min(h, y + bh)
        tile = image[y0:y1, x0:x1]
        mask = tile[:, :, 3] > 32
        if np.any(mask):
            skin_px = tile[:, :, :3][mask]
    skin = _median_color(skin_px, tuple(anchors.get("skin_rgb") or (244, 214, 180)))
    ink_px = image[:, :, :3][(image[:, :, 3] > 200) & (image[:, :, :3].max(axis=2) < 38)]
    if len(ink_px) < 20:
        ink = tuple(anchors.get("ink_rgb") or (18, 14, 16))
    else:
        ink = _rgb(np.median(ink_px, axis=0))
    sh_l, sh_r = bones.get("sh_l"), bones.get("sh_r")
    el_l, el_r = bones.get("el_l"), bones.get("el_r")
    jacket_px = []
    if sh_l is not None:
        jacket_px.append(_segment_pixels(image, sh_l, el_l, 0.04 * height))
        jacket_px.append(_segment_pixels(image, sh_r, el_r, 0.04 * height))
    jacket = _median_color(np.concatenate(jacket_px) if jacket_px else None, (48, 78, 112))
    hip_y = bones["feet_y"] - (1.0 - PROP["hip"]) * height
    knee_y = bones["feet_y"] - (1.0 - PROP["knee"]) * height
    pants_band = image[int(hip_y):int(knee_y), :, :3]
    pants_alpha = image[int(hip_y):int(knee_y), :, 3] > 32
    pants = _median_color(pants_band[pants_alpha] if np.any(pants_alpha) else None, jacket)
    boot_y = int(bones["feet_y"] - 0.09 * height)
    boot_band = image[boot_y:int(bones["feet_y"]) + 1, :, :3]
    boot_alpha = image[boot_y:int(bones["feet_y"]) + 1, :, 3] > 32
    boot = _median_color(boot_band[boot_alpha] if np.any(boot_alpha) else None, (42, 46, 54))
    return {"jacket": jacket, "pants": pants, "boot": boot, "glove": skin, "skin": skin, "ink": ink}


def _paint_capsule(mask: np.ndarray, p0, p1, r0: float, r1: float) -> None:
    p0 = np.asarray(p0, np.float64)
    p1 = np.asarray(p1, np.float64)
    delta = p1 - p0
    length = float(np.hypot(delta[0], delta[1]))
    if length < 1.0:
        cv2.circle(mask, _ipt(p0), max(1, int(round(max(r0, r1)))), 255, -1, lineType=cv2.LINE_AA)
        return
    direction = delta / length
    # The cap is a full disk at the pivot. Extending the body by one third of the
    # radius pulls the child farther onto the parent socket than a butt joint would.
    back = p0 - direction * (r0 * JOINT_OVERLAP)
    normal = np.array([-direction[1], direction[0]])
    poly = np.round(np.stack([
        back + normal * r0,
        back - normal * r0,
        p1 - normal * r1,
        p1 + normal * r1,
    ])).astype(np.int32)
    cv2.fillConvexPoly(mask, poly, 255)
    cv2.circle(mask, _ipt(p0), max(1, int(round(r0))), 255, -1, lineType=cv2.LINE_AA)
    cv2.circle(mask, _ipt(p1), max(1, int(round(r1))), 255, -1, lineType=cv2.LINE_AA)


def _sprite_from_masks(fill: np.ndarray, ink: np.ndarray, color, ink_color) -> np.ndarray:
    fill_a = fill.astype(np.float32) / 255.0
    ink_a = ink.astype(np.float32) / 255.0
    alpha = np.maximum(fill_a, ink_a)
    rgb = np.zeros(fill.shape + (3,), np.float32)
    ink_rgb = np.array(_rgb(ink_color), np.float32)
    fill_rgb = np.array(_rgb(color), np.float32)
    rgb[:] = ink_rgb
    rgb = rgb * (1.0 - fill_a[..., None]) + fill_rgb * fill_a[..., None]
    out = np.zeros(fill.shape + (4,), np.uint8)
    out[:, :, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    out[:, :, 3] = np.clip(alpha * 255.0, 0, 255).astype(np.uint8)
    return out


def make_limb(length: float, r0: float, r1: float, color, ink, stroke: float) -> tuple[np.ndarray, tuple[float, float]]:
    """Capsule pointing down. Pivot is the proximal circle center."""
    pad = stroke + 3.0
    width = int(math.ceil(2 * (max(r0, r1) + pad)))
    pivot_y = pad + r0
    height = int(math.ceil(pivot_y + length + r1 + pad))
    pivot_x = width / 2.0
    fill = np.zeros((height, width), np.uint8)
    edge = np.zeros((height, width), np.uint8)
    p0 = (pivot_x, pivot_y)
    p1 = (pivot_x, pivot_y + length)
    _paint_capsule(edge, p0, p1, r0 + stroke, r1 + stroke)
    _paint_capsule(fill, p0, p1, r0, r1)
    return _sprite_from_masks(fill, edge, color, ink), (pivot_x, pivot_y)


def _new_canvas(w: int, h: int) -> tuple[np.ndarray, np.ndarray]:
    return np.zeros((h, w), np.uint8), np.zeros((h, w), np.uint8)


def _fill_poly(mask: np.ndarray, points: np.ndarray) -> None:
    cv2.fillPoly(mask, [np.round(points).astype(np.int32)], 255, lineType=cv2.LINE_AA)


def make_torso_side(bones: dict, rad: dict, color, ink, stroke: float) -> tuple[np.ndarray, tuple[float, float], tuple[float, float]]:
    """Side jacket: back, chest, waist and hip. The shoulder pivot sits inside it."""
    height = bones["H"]
    hip_to_neck = bones["hip_to_neck"]
    hip_to_sh = bones["hip_to_shoulder"]
    forward = bones["shoulder_forward"]
    chest = 0.150 * height
    waist = 0.108 * height
    hip_w = 0.128 * height
    neck_w = rad["neck"] * 1.7
    pad = stroke + 6.0
    half = chest * 0.62 + pad
    width = int(math.ceil(2 * half))
    pivot_x = width / 2.0
    pivot_y = pad + hip_to_neck
    img_h = int(math.ceil(pivot_y + 0.08 * height + pad))
    fill, edge = _new_canvas(width, img_h)

    def paint(mask: np.ndarray, extra: float) -> None:
        def at(x: float, y: float) -> tuple[float, float]:
            return (pivot_x + x, pivot_y + y)

        neck_y = -hip_to_neck
        shoulder_y = -hip_to_sh
        waist_y = -hip_to_sh * 0.46
        hem_y = 0.05 * height
        _fill_poly(mask, np.array([
            at(-neck_w * 0.42 - extra, neck_y),
            at(-chest * 0.40 - extra, shoulder_y),
            at(-waist * 0.46 - extra, waist_y),
            at(-hip_w * 0.46 - extra, hem_y),
            at(hip_w * 0.48 + extra, hem_y),
            at(waist * 0.40 + extra, waist_y),
            at(chest * 0.56 + extra, shoulder_y - 0.012 * height),
            at(neck_w * 0.48 + extra, neck_y),
        ], np.float64))
        shoulder = at(forward, shoulder_y)
        cv2.circle(
            mask, _ipt(shoulder),
            max(2, int(round(rad["shoulder"] * 0.9 + extra))),
            255, -1, lineType=cv2.LINE_AA,
        )

    paint(edge, stroke)
    paint(fill, 0.0)
    sprite = _sprite_from_masks(fill, edge, color, ink)
    return sprite, (pivot_x, pivot_y), (forward * 0.2, -hip_to_neck)


def make_torso_front(bones: dict, rad: dict, color, ink, stroke: float) -> tuple[np.ndarray, tuple[float, float], tuple[float, float]]:
    """Front jacket: wide shoulders, a narrower waist, hips. Arms hang outside it."""
    height = bones["H"]
    shoulder_half = bones["shoulder_half"]
    hip_half = bones["hip_half"]
    neck_r = rad["neck"]
    hip_to_neck = bones["hip_to_neck"]
    hip_to_sh = bones["hip_to_shoulder"]
    pad = stroke + 6.0
    half = shoulder_half + pad
    width = int(math.ceil(2 * half))
    pivot_x = width / 2.0
    pivot_y = pad + hip_to_neck
    img_h = int(math.ceil(pivot_y + 0.08 * height + pad))
    fill, edge = _new_canvas(width, img_h)
    waist_half = shoulder_half * 0.72
    hip_w = max(hip_half, shoulder_half * 0.80)

    def paint(mask: np.ndarray, extra: float) -> None:
        # The arm pivot is exactly at shoulder_y. The collar stops at the neck joint,
        # where the painted head sits. Dropping the shoulder further makes a tent.
        shoulder_y = pivot_y - hip_to_sh
        neck_y = pivot_y - hip_to_neck
        waist_y = pivot_y - hip_to_sh * 0.45
        hem_y = pivot_y + 0.06 * height
        _fill_poly(mask, np.array([
            [pivot_x - neck_r * 1.15 - extra, neck_y],
            [pivot_x + neck_r * 1.15 + extra, neck_y],
            [pivot_x + shoulder_half + extra, shoulder_y],
            [pivot_x + waist_half + extra, waist_y],
            [pivot_x + hip_w + extra, hem_y],
            [pivot_x - hip_w - extra, hem_y],
            [pivot_x - waist_half - extra, waist_y],
            [pivot_x - shoulder_half - extra, shoulder_y],
        ], np.float64))

    paint(edge, stroke)
    paint(fill, 0.0)
    sprite = _sprite_from_masks(fill, edge, color, ink)
    # A collar opening, so the chest reads as a jacket instead of a cushion.
    seam = _rgb(tuple(int(channel * 0.55) for channel in _rgb(ink)))
    cv2.line(
        sprite,
        _ipt((pivot_x, pivot_y - hip_to_neck * 0.92)),
        _ipt((pivot_x, pivot_y - hip_to_sh * 0.15)),
        (*seam, 255),
        max(2, int(round(stroke * 0.7))),
        cv2.LINE_AA,
    )
    return sprite, (pivot_x, pivot_y), (0.0, -hip_to_neck)


def make_mitten(body_h: float, wrist_r: float, color, ink, stroke: float, thumb: float) -> tuple[np.ndarray, tuple[float, float]]:
    """A glove: cuff, palm and a thumb. Pivot is the wrist."""
    palm_w = max(wrist_r * 2.15, 0.040 * body_h)
    palm_h = max(wrist_r * 2.6, 0.055 * body_h)
    pad = stroke + 4.0
    width = int(math.ceil(palm_w + wrist_r * 2.2 + pad * 2))
    pivot_x = width * (0.42 if thumb > 0 else 0.58)
    pivot_y = pad + wrist_r
    height = int(math.ceil(pivot_y + palm_h + pad))
    fill, edge = _new_canvas(width, height)

    def paint(mask: np.ndarray, extra: float) -> None:
        cv2.circle(mask, _ipt((pivot_x, pivot_y)), max(2, int(round(wrist_r + extra))), 255, -1, lineType=cv2.LINE_AA)
        palm = np.array([
            [pivot_x - palm_w * 0.42 - extra, pivot_y],
            [pivot_x + palm_w * 0.42 + extra, pivot_y],
            [pivot_x + palm_w * 0.36 + extra, pivot_y + palm_h],
            [pivot_x - palm_w * 0.36 - extra, pivot_y + palm_h],
        ], np.float64)
        _fill_poly(mask, palm)
        cv2.circle(
            mask,
            _ipt((pivot_x, pivot_y + palm_h * 0.92)),
            max(2, int(round(palm_w * 0.34 + extra))),
            255, -1, lineType=cv2.LINE_AA,
        )
        thumb_x = pivot_x + thumb * palm_w * 0.62
        cv2.ellipse(
            mask,
            _ipt((thumb_x, pivot_y + palm_h * 0.28)),
            (max(2, int(round(palm_w * 0.22 + extra))), max(2, int(round(palm_h * 0.28 + extra)))),
            0, 0, 360, 255, -1, lineType=cv2.LINE_AA,
        )

    paint(edge, stroke)
    paint(fill, 0.0)
    return _sprite_from_masks(fill, edge, color, ink), (pivot_x, pivot_y)


def make_shoe_side(body_h: float, ankle_r: float, color, ink, stroke: float) -> tuple[np.ndarray, tuple[float, float]]:
    """Profile shoe: shaft, heel, flat sole, toe pointing forward (+x)."""
    shaft = 0.045 * body_h
    toe = 0.070 * body_h
    heel = 0.028 * body_h
    sole = 0.020 * body_h
    pad = stroke + 4.0
    width = int(math.ceil(heel + toe + ankle_r + pad * 2))
    pivot_x = pad + heel + ankle_r
    pivot_y = pad + ankle_r
    height = int(math.ceil(pivot_y + shaft + sole + pad))
    fill, edge = _new_canvas(width, height)

    def paint(mask: np.ndarray, extra: float) -> None:
        cv2.circle(mask, _ipt((pivot_x, pivot_y)), max(2, int(round(ankle_r + extra))), 255, -1, lineType=cv2.LINE_AA)
        _fill_poly(mask, np.array([
            [pivot_x - ankle_r - extra, pivot_y],
            [pivot_x + ankle_r + extra, pivot_y],
            [pivot_x + ankle_r * 0.8 + extra, pivot_y + shaft],
            [pivot_x - ankle_r * 0.9 - extra, pivot_y + shaft],
        ], np.float64))
        sole_y = pivot_y + shaft
        _fill_poly(mask, np.array([
            [pivot_x - heel - extra, sole_y],
            [pivot_x + toe * 0.72 + extra, sole_y],
            [pivot_x + toe + extra, sole_y + sole * 0.55],
            [pivot_x + toe * 0.55 + extra, sole_y + sole],
            [pivot_x - heel * 0.65 - extra, sole_y + sole],
            [pivot_x - heel - extra, sole_y + sole * 0.35],
        ], np.float64))

    paint(edge, stroke)
    paint(fill, 0.0)
    return _sprite_from_masks(fill, edge, color, ink), (pivot_x, pivot_y)


def make_shoe_front(body_h: float, ankle_r: float, color, ink, stroke: float) -> tuple[np.ndarray, tuple[float, float]]:
    """Shoe seen from the front: a shaft and a sole wider than the ankle."""
    shaft = 0.040 * body_h
    sole_w = max(ankle_r * 2.6, 0.055 * body_h)
    sole_h = 0.022 * body_h
    pad = stroke + 4.0
    width = int(math.ceil(sole_w + pad * 2))
    pivot_x = width / 2.0
    pivot_y = pad + ankle_r
    height = int(math.ceil(pivot_y + shaft + sole_h + pad))
    fill, edge = _new_canvas(width, height)

    def paint(mask: np.ndarray, extra: float) -> None:
        cv2.circle(mask, _ipt((pivot_x, pivot_y)), max(2, int(round(ankle_r + extra))), 255, -1, lineType=cv2.LINE_AA)
        _fill_poly(mask, np.array([
            [pivot_x - ankle_r - extra, pivot_y],
            [pivot_x + ankle_r + extra, pivot_y],
            [pivot_x + sole_w * 0.46 + extra, pivot_y + shaft + sole_h],
            [pivot_x - sole_w * 0.46 - extra, pivot_y + shaft + sole_h],
        ], np.float64))

    paint(edge, stroke)
    paint(fill, 0.0)
    return _sprite_from_masks(fill, edge, color, ink), (pivot_x, pivot_y)


def _trim(image: np.ndarray, pivot: tuple[float, float], pad: int = 2) -> tuple[np.ndarray, tuple[float, float]]:
    ys, xs = np.where(image[:, :, 3] > 8)
    if len(xs) == 0:
        return image, pivot
    x0 = max(0, int(xs.min()) - pad)
    y0 = max(0, int(ys.min()) - pad)
    x1 = min(image.shape[1], int(xs.max()) + pad + 1)
    y1 = min(image.shape[0], int(ys.max()) + pad + 1)
    cropped = image[y0:y1, x0:x1].copy()
    return cropped, (pivot[0] - x0, pivot[1] - y0)


def _scale_sprite(image: np.ndarray, pivot: tuple[float, float], scale: float) -> tuple[np.ndarray, tuple[float, float]]:
    if abs(scale - 1.0) < 1e-3:
        return image, pivot
    height, width = image.shape[:2]
    new_w = max(1, int(round(width * scale)))
    new_h = max(1, int(round(height * scale)))
    interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    scaled = cv2.resize(image, (new_w, new_h), interpolation=interp)
    return scaled, (pivot[0] * scale, pivot[1] * scale)


def _mask_to_crop(image: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, tuple[int, int, int, int]] | None:
    ys, xs = np.where(mask)
    if len(xs) < 40:
        return None
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    part = image[y0:y1 + 1, x0:x1 + 1].copy()
    part[:, :, 3] = np.where(mask[y0:y1 + 1, x0:x1 + 1], part[:, :, 3], 0)
    return part, (x0, y0, x1 - x0 + 1, y1 - y0 + 1)


def crop_head(image: np.ndarray, neck_xy, chin_y: float, radius: float) -> tuple[np.ndarray, tuple[float, float], tuple[int, int, int, int]] | None:
    """Head above the chin, plus a round neck stump at the joint."""
    alpha = image[:, :, 3] > 32
    neck_x, neck_y = float(neck_xy[0]), float(neck_xy[1])
    cut = int(round(min(chin_y, neck_y)))
    keep = np.zeros(alpha.shape, bool)
    keep[: max(0, cut), :] = alpha[: max(0, cut), :]
    yy, xx = np.ogrid[: image.shape[0], : image.shape[1]]
    dist = np.hypot(xx - neck_x, yy - neck_y)
    keep |= alpha & (dist <= radius)
    # Hair beside the face can dip below the chin. Keep it if it touches the head
    # and stays outside the neck, above the shoulders.
    band = alpha.copy()
    band[:cut, :] = False
    band[int(min(image.shape[0] - 1, neck_y + radius * 1.2)):, :] = False
    band[:, int(max(0, neck_x - radius * 1.3)): int(min(image.shape[1], neck_x + radius * 1.3))] = False
    labels_n, labels = cv2.connectedComponents(band.astype(np.uint8), 8)
    for label in range(1, labels_n):
        ys, xs = np.where(labels == label)
        if len(xs) < 40:
            continue
        y_top = int(ys.min())
        above = keep[max(0, y_top - 3): cut, max(0, int(xs.min()) - 4): int(xs.max()) + 5]
        if y_top <= cut + 2 and np.any(above):
            # Only hair. Shoulder pads and the badge are much lighter than the ponytail.
            if float(image[ys, xs, :3].mean()) < 70:
                keep[labels == label] = True
    keep &= ~((yy > neck_y) & (dist > radius))
    cropped = _mask_to_crop(image, keep)
    if cropped is None:
        return None
    part, rect = cropped
    pivot = (neck_x - rect[0], neck_y - rect[1])
    return part, pivot, rect


def _boot_masks(image: np.ndarray, bones: dict, pants_rgb) -> list[tuple[np.ndarray, float]]:
    """One vertical boot per foot. A wide floor smear is split or dropped."""
    alpha = image[:, :, 3] > 36
    height, width = alpha.shape
    body_h = float(bones["H"])
    feet = int(min(height - 1, round(bones["feet_y"])))
    y0 = max(0, int(feet - 0.16 * body_h))
    band = np.zeros(alpha.shape, np.uint8)
    band[y0: feet + 1] = alpha[y0: feet + 1].astype(np.uint8)
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(band, 8)
    pieces: list[tuple[int, int]] = []
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        comp_w = int(stats[label, cv2.CC_STAT_WIDTH])
        comp_h = int(stats[label, cv2.CC_STAT_HEIGHT])
        if area < 0.0012 * body_h * body_h or comp_h < 0.025 * body_h:
            continue
        left = int(stats[label, cv2.CC_STAT_LEFT])
        if comp_w > 0.22 * body_h:
            columns = (labels[y0: feet + 1, left: left + comp_w] == label).sum(axis=0)
            gap = np.where(columns < 2)[0]
            if len(gap) == 0:
                continue
            mid = int(np.median(gap))
            pieces.append((left, left + mid))
            pieces.append((left + mid, left + comp_w))
        else:
            pieces.append((left, left + comp_w))
    masks = []
    for x_start, x_end in pieces:
        x0 = max(0, x_start - 12)
        x1 = min(width, x_end + 13)
        if x1 - x0 < 8:
            continue
        # The boot upper is often the same blue as the pants, so a color stop
        # keeps only the sole. Take a fixed shaft above the sole instead.
        boot_top = int(feet - 0.118 * body_h)
        mask = np.zeros(alpha.shape, bool)
        mask[boot_top: feet + 1, x0:x1] = alpha[boot_top: feet + 1, x0:x1]
        keep_n, keep_labels = cv2.connectedComponents(mask.astype(np.uint8), 8)
        best, best_area = None, 0
        for keep in range(1, keep_n):
            area = int((keep_labels == keep).sum())
            if area > best_area:
                best, best_area = keep, area
        if best is None or best_area < 80:
            continue
        mask = keep_labels == best
        ys, xs = np.where(mask)
        if len(xs) == 0 or (ys.max() - ys.min()) < (xs.max() - xs.min()) * 0.5:
            continue
        ankle_y = float(ys.min() + max(4.0, 0.012 * body_h))
        masks.append((mask, ankle_y))
    masks.sort(key=lambda item: float(np.where(item[0])[1].mean()) if np.any(item[0]) else 0.0)
    return masks[:2]


def rad_pad(body_h: float) -> float:
    return max(4.0, 0.012 * body_h)


def _disk_colors(image: np.ndarray, point, radius: float) -> np.ndarray | None:
    h, w = image.shape[:2]
    x, y = float(point[0]), float(point[1])
    x0, x1 = max(0, int(x - radius)), min(w, int(x + radius) + 1)
    y0, y1 = max(0, int(y - radius)), min(h, int(y + radius) + 1)
    if x1 <= x0 or y1 <= y0:
        return None
    tile = image[y0:y1, x0:x1]
    mask = tile[:, :, 3] > 36
    if int(mask.sum()) < 6:
        return None
    return tile[:, :, :3][mask].astype(np.float32)


def _hand_mask(image: np.ndarray, shoulder, elbow, forearm: float, jacket_rgb, skin_rgb) -> np.ndarray | None:
    """The painted hand is the last stretch of the arm, past the sleeve."""
    if shoulder is None or elbow is None:
        return None
    direction = np.asarray(elbow, np.float64) - np.asarray(shoulder, np.float64)
    upper = float(np.hypot(direction[0], direction[1]))
    if upper < 8.0:
        return None
    direction = direction / upper
    body_h = float(image.shape[0])
    radius = max(12.0, upper * 0.16)
    jacket = np.asarray(jacket_rgb, np.float32)
    skin = np.asarray(skin_rgb, np.float32)
    samples = []
    for t in np.linspace(0.0, upper * 1.35, 56):
        point = np.asarray(elbow, np.float64) + direction * t
        colors = _disk_colors(image, point, radius)
        if colors is None:
            continue
        med = np.median(colors, axis=0)
        lum = float(med.mean())
        spread = float(med.max() - med.min())
        white = lum > 168 and spread < 70
        skinish = float(np.linalg.norm(med - skin)) < 58 and lum > 90
        not_sleeve = float(np.linalg.norm(med - jacket)) > 42 or white or skinish
        samples.append((float(t), not_sleeve, point))
    if not samples:
        return None
    tip = samples[-1][0]
    hand_len = min(max(forearm * 0.72, 0.07 * body_h), 0.13 * body_h)
    glove_ts = [t for t, glove, _point in samples if glove and t > tip - hand_len * 1.35]
    if glove_ts:
        wrist_t = max(tip - hand_len, min(glove_ts) - upper * 0.06)
    else:
        wrist_t = tip - hand_len * 0.85
    wrist_t = max(0.0, wrist_t)
    alpha = image[:, :, 3] > 36
    h, w = alpha.shape
    elbow_v = np.asarray(elbow, np.float64)
    # Tight tube from just before the wrist to the fingertips.
    x_min = int(max(0, min(elbow_v[0], elbow_v[0] + direction[0] * tip) - radius * 2))
    x_max = int(min(w, max(elbow_v[0], elbow_v[0] + direction[0] * tip) + radius * 2))
    y_min = int(max(0, min(elbow_v[1], elbow_v[1] + direction[1] * tip) - radius * 2))
    y_max = int(min(h, max(elbow_v[1], elbow_v[1] + direction[1] * tip) + radius * 2))
    yy, xx = np.mgrid[y_min:y_max, x_min:x_max]
    pts = np.stack([xx.ravel(), yy.ravel()], axis=1).astype(np.float64)
    along = (pts - elbow_v) @ direction
    nearest = elbow_v + direction * along[:, None]
    tube = np.linalg.norm(pts - nearest, axis=1) <= radius
    span = (along >= wrist_t) & (along <= tip + radius * 0.4)
    box = alpha[y_min:y_max, x_min:x_max].ravel()
    chosen = tube & span & box
    if int(chosen.sum()) < 25:
        return None
    mask = np.zeros(alpha.shape, np.uint8)
    flat = mask[y_min:y_max, x_min:x_max].ravel()
    flat[chosen] = 255
    mask[y_min:y_max, x_min:x_max] = flat.reshape(y_max - y_min, x_max - x_min)
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)
    mask = (mask > 0) & alpha
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    tip_pt = elbow_v + direction * tip
    best, best_score = None, 1e9
    for label in range(1, count):
        if stats[label, cv2.CC_STAT_AREA] < 25:
            continue
        center = centroids[label]
        score = float(np.hypot(center[0] - tip_pt[0], center[1] - tip_pt[1]))
        if score < best_score:
            best, best_score = label, score
    if best is None:
        return None
    return labels == best


def _extremity_sprite(crop: np.ndarray, crop_pivot: tuple[float, float], radius: float, color, ink, stroke: float) -> tuple[np.ndarray, tuple[float, float]]:
    """Painted crop hung on a circle cap. The pivot is the circle center."""
    pad = stroke + 3.0
    cap_r = radius + stroke
    left = max(cap_r + pad, crop_pivot[0] + pad)
    right = max(cap_r + pad, crop.shape[1] - crop_pivot[0] + pad)
    up = max(cap_r + pad, crop_pivot[1] + pad)
    down = max(cap_r + pad, crop.shape[0] - crop_pivot[1] + pad)
    width = int(math.ceil(left + right))
    height = int(math.ceil(up + down))
    pivot = (left, up)
    canvas = np.zeros((height, width, 4), np.uint8)
    fill = np.zeros((height, width), np.uint8)
    edge = np.zeros((height, width), np.uint8)
    cv2.circle(edge, _ipt(pivot), max(1, int(round(radius + stroke))), 255, -1, lineType=cv2.LINE_AA)
    cv2.circle(fill, _ipt(pivot), max(1, int(round(radius))), 255, -1, lineType=cv2.LINE_AA)
    cap = _sprite_from_masks(fill, edge, color, ink)
    _over(canvas, cap)
    x = int(round(pivot[0] - crop_pivot[0]))
    y = int(round(pivot[1] - crop_pivot[1]))
    _paste(canvas, crop, x, y)
    return _trim(canvas, pivot, pad=1)


def _paste(dst: np.ndarray, src: np.ndarray, x: int, y: int) -> None:
    h, w = src.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(dst.shape[1], x + w), min(dst.shape[0], y + h)
    if x1 <= x0 or y1 <= y0:
        return
    sx0, sy0 = x0 - x, y0 - y
    src_tile = src[sy0:sy0 + (y1 - y0), sx0:sx0 + (x1 - x0)]
    tile = dst[y0:y1, x0:x1]
    _over_into(tile, src_tile)


def _over_into(dst: np.ndarray, src: np.ndarray) -> None:
    src_a = src[:, :, 3:4].astype(np.float32) / 255.0
    dst_a = dst[:, :, 3:4].astype(np.float32) / 255.0
    out_a = src_a + dst_a * (1.0 - src_a)
    src_rgb = src[:, :, :3].astype(np.float32)
    dst_rgb = dst[:, :, :3].astype(np.float32)
    out_rgb = src_rgb * src_a + dst_rgb * dst_a * (1.0 - src_a)
    safe = np.maximum(out_a, 1e-4)
    dst[:, :, :3] = np.clip(out_rgb / safe, 0, 255).astype(np.uint8)
    dst[:, :, 3] = np.clip(out_a[..., 0] * 255.0, 0, 255).astype(np.uint8)


def _over(dst: np.ndarray, src: np.ndarray) -> None:
    _over_into(dst, src)


def _find_hands(image: np.ndarray, meta: dict, colors: dict) -> dict[str, np.ndarray]:
    """Gloves or bare hands: color blobs below the chin, one on each side."""
    rgb = image[:, :, :3].astype(np.int16)
    alpha = image[:, :, 3] > 40
    body_h = float(meta["body_height"])
    neck = _joint(meta, "head")
    neck_y = float(neck[1]) if neck is not None else 0.2 * body_h
    feet = float(meta["feet_y"])
    center = float(meta["center_x"])
    skin = np.asarray(colors["skin"], np.int16)
    dist_skin = np.linalg.norm(rgb - skin, axis=2)
    lum = rgb.astype(np.int16).mean(axis=2)
    spread = rgb.max(axis=2) - rgb.min(axis=2)
    white = (lum > 170) & (spread < 60)
    orange = (rgb[:, :, 0] > 150) & (rgb[:, :, 1] > 50) & (rgb[:, :, 1] < 190) & (rgb[:, :, 2] < 110)
    orange &= (rgb[:, :, 0] - rgb[:, :, 2]) > 70
    colored = alpha & (white | orange | (dist_skin < 46))
    zone = (np.arange(image.shape[0])[:, None] > neck_y + 0.02 * body_h) & (
        np.arange(image.shape[0])[:, None] < feet - 0.20 * body_h
    )
    colored &= zone
    found: dict[str, np.ndarray] = {}
    for side in ("l", "r"):
        shoulder = _joint(meta, f"arm_{side}")
        elbow = _joint(meta, f"fore_{side}")
        if shoulder is None or elbow is None:
            continue
        direction = np.asarray(elbow, np.float64) - np.asarray(shoulder, np.float64)
        reach = float(np.hypot(direction[0], direction[1]))
        if reach < 8:
            continue
        guess = np.asarray(elbow, np.float64) + direction / reach * (0.62 * reach)
        mask = colored.copy()
        xs_all = np.arange(image.shape[1])
        if side == "l":
            mask[:, xs_all >= center] = False
        else:
            mask[:, xs_all < center] = False
        count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
        best, best_score = None, 1e9
        for label in range(1, count):
            area = int(stats[label, cv2.CC_STAT_AREA])
            if area < 120 or area > 0.16 * body_h * body_h:
                continue
            center_xy = centroids[label]
            score = float(np.hypot(center_xy[0] - guess[0], center_xy[1] - guess[1]))
            if score < best_score:
                best, best_score = label, score
        if best is None or best_score > 0.28 * body_h:
            continue
        component = labels == best
        ys, _xs = np.where(component)
        # A bare arm is one long skin shape. Keep its lower end, the hand.
        hand_top = max(int(ys.min()), int(ys.max() - 0.105 * body_h))
        component = component & (np.arange(image.shape[0])[:, None] >= hand_top - 0.012 * body_h)
        if int(component.sum()) < 40:
            continue
        found[side] = component
    return found


def _attach_crops(image: np.ndarray, bones: dict, colors: dict, meta: dict) -> dict:
    """Head, gloves, and boots cropped from one painted view."""
    height = bones["H"]
    scale = bones["H"] / max(1.0, float(meta["body_height"]))
    neck = _joint(meta, "head")
    if neck is None:
        neck = np.array([bones["center_x"], bones["top"] + PROP["neck"] * height], np.float64)
    mouth = (meta.get("anchors") or {}).get("mouth") or {}
    if mouth:
        chin_y = float(mouth["y"] + mouth["h"]) + 0.018 * float(meta["body_height"])
    else:
        chin_y = float(neck[1]) - 0.035 * float(meta["body_height"])
    head = crop_head(image, neck, chin_y, max(14.0, 0.024 * float(meta["body_height"])))
    crops: dict = {"head": None, "hands": {}, "feet": {}}
    if head is not None:
        part, pivot, rect = head
        part, pivot = _scale_sprite(part, pivot, scale)
        crops["head"] = {"image": part, "pivot": pivot, "rect": list(rect), "file": meta["file"]}
    sh = {"l": _joint(meta, "arm_l"), "r": _joint(meta, "arm_r")}
    el = {"l": _joint(meta, "fore_l"), "r": _joint(meta, "fore_r")}
    for side in ("l", "r"):
        if sh[side] is None or el[side] is None:
            continue
        local_fore = _dist(sh[side], el[side]) * 0.9
        mask = _hand_mask(image, sh[side], el[side], local_fore, colors["jacket"], colors["skin"])
        if mask is None:
            mask = _find_hands(image, meta, colors).get(side)
        if mask is None:
            continue
        packed = _mask_to_crop(image, mask)
        if packed is None:
            continue
        part, rect = packed
        direction = np.asarray(el[side], np.float64) - np.asarray(sh[side], np.float64)
        direction = direction / (float(np.hypot(direction[0], direction[1])) + 1e-6)
        ys, xs = np.where(part[:, :, 3] > 32)
        coords = np.stack([xs + rect[0], ys + rect[1]], axis=1).astype(np.float64)
        along = (coords - np.asarray(el[side], np.float64)) @ direction
        order = np.argsort(along)
        near = order[: max(8, len(order) // 6)]
        pivot = (float(np.median(xs[near])), float(np.median(ys[near])))
        part, pivot = _scale_sprite(part, pivot, scale)
        opaque = part[part[:, :, 3] > 32][:, :3]
        crops["hands"][side] = {
            "image": part,
            "pivot": pivot,
            "rect": list(rect),
            "file": meta["file"],
            "color": _median_color(opaque, colors["skin"]),
        }
    pants = colors["pants"]
    for mask, ankle_y in _boot_masks(image, {
        "H": meta["body_height"],
        "feet_y": meta["feet_y"],
    }, pants):
        packed = _mask_to_crop(image, mask)
        if packed is None:
            continue
        part, rect = packed
        cx = part.shape[1] / 2.0
        pivot = (cx, float(ankle_y - rect[1]))
        pivot = (pivot[0], min(max(4.0, pivot[1]), part.shape[0] * 0.35))
        side = "l" if (rect[0] + cx) < bones["center_x"] * (meta["body_height"] / bones["H"]) else "r"
        # center_x is in front-image space. Compare in this image's own center.
        side = "l" if (rect[0] + cx) <= meta["center_x"] else "r"
        part, pivot = _scale_sprite(part, pivot, scale)
        opaque = part[part[:, :, 3] > 32][:, :3]
        crops["feet"][side] = {
            "image": part,
            "pivot": pivot,
            "rect": list(rect),
            "file": meta["file"],
            "color": _median_color(opaque, colors["boot"]),
        }
    return crops


def _prefer(*items):
    for item in items:
        if item is not None:
            return item
    return None


def _radii(bones: dict, view: str) -> dict[str, float]:
    """Limb thickness, not a ball joint. The pivot stays inside the sleeve."""
    height = bones["H"]
    if view == "side":
        return {
            "neck": 0.030 * height,
            "shoulder": 0.024 * height,
            "elbow": 0.019 * height,
            "wrist": 0.015 * height,
            "hip": 0.032 * height,
            "knee": 0.024 * height,
            "ankle": 0.018 * height,
        }
    hip_r = min(0.030 * height, max(0.020 * height, bones["hip_half"] * 0.55))
    return {
        "neck": 0.028 * height,
        "shoulder": 0.022 * height,
        "elbow": 0.018 * height,
        "wrist": 0.014 * height,
        "hip": hip_r,
        "knee": hip_r * 0.78,
        "ankle": hip_r * 0.62,
    }


def _paint_neck_skin(sprite: np.ndarray, pivot, neck_local, radius: float, skin, ink, stroke: float) -> None:
    center = (float(pivot[0]) + float(neck_local[0]), float(pivot[1]) + float(neck_local[1]))
    fill = np.zeros(sprite.shape[:2], np.uint8)
    edge = np.zeros(sprite.shape[:2], np.uint8)
    cv2.circle(edge, _ipt(center), max(1, int(round(radius + stroke * 0.35))), 255, -1, lineType=cv2.LINE_AA)
    cv2.circle(fill, _ipt(center), max(1, int(round(radius))), 255, -1, lineType=cv2.LINE_AA)
    _over(sprite, _sprite_from_masks(fill, edge, skin, ink))


def _part_record(name: str, parent: str, pivot, rest: float, file: str, z: int, offset, group: str, image, length: float) -> dict:
    return {
        "name": name,
        "parent": parent,
        "pivot": [round(float(pivot[0]), 2), round(float(pivot[1]), 2)],
        "rest": round(float(rest), 2),
        "file": file,
        "z": int(z),
        "offset": (float(offset[0]), float(offset[1])),
        "group": group,
        "image": image,
        "length": float(length),
    }


def _build_view(view: str, bones: dict, colors: dict, crops: dict, out_dir: Path) -> list[dict]:
    rad = _radii(bones, view)
    stroke = max(3.0, 0.0055 * bones["H"])
    ink = colors["ink"]
    parts: list[dict] = []
    parts.append(_part_record("pelvis", "", (0.0, 0.0), 0.0, "", 0, (0.0, 0.0), "pelvis", None, 0.0))
    if view == "side":
        torso, torso_pivot, neck_local = make_torso_side(bones, rad, colors["jacket"], ink, stroke)
        shoulder_offset = (bones["shoulder_forward"], -bones["hip_to_shoulder"])
        hip_l = (0.0, 0.0)
        hip_r = (0.0, 0.0)
        z = {
            "upper_arm_r": 1, "lower_arm_r": 2, "hand_r": 3,
            "thigh_r": 4, "shin_r": 5, "foot_r": 6,
            "torso": 7,
            "thigh_l": 8, "shin_l": 9, "foot_l": 10,
            "upper_arm_l": 11, "lower_arm_l": 12, "hand_l": 13,
            "head": 14,
        }
    else:
        torso, torso_pivot, neck_local = make_torso_front(bones, rad, colors["jacket"], ink, stroke)
        shoulder_offset = None
        hip_l = (-bones["hip_half"], 0.0)
        hip_r = (bones["hip_half"], 0.0)
        z = {
            "thigh_l": 1, "shin_l": 2, "foot_l": 3,
            "thigh_r": 4, "shin_r": 5, "foot_r": 6,
            "torso": 7,
            "upper_arm_l": 8, "lower_arm_l": 9, "hand_l": 10,
            "upper_arm_r": 11, "lower_arm_r": 12, "hand_r": 13,
            "head": 14,
        }
    _paint_neck_skin(torso, torso_pivot, neck_local, rad["neck"] * 0.82, colors["skin"], ink, stroke)
    file_name = f"{view}_torso.png"
    Image.fromarray(torso, "RGBA").save(out_dir / file_name)
    parts.append(_part_record("torso", "pelvis", torso_pivot, 0.0, file_name, z["torso"], (0.0, 0.0), "torso", torso, bones["hip_to_neck"]))

    head = crops.get("head")
    if head is not None:
        head_img, head_pivot = head["image"], head["pivot"]
        file_name = f"{view}_head.png"
        Image.fromarray(head_img, "RGBA").save(out_dir / file_name)
        parts.append(_part_record("head", "torso", head_pivot, 0.0, file_name, z["head"], neck_local, "head", head_img, 0.0))

    limb_specs = (
        ("upper_arm", "torso", bones["upper_arm"], rad["shoulder"], rad["elbow"], colors["jacket"], "arm"),
        ("lower_arm", "upper_arm", bones["forearm"], rad["elbow"], rad["wrist"], colors["jacket"], "arm"),
        ("thigh", "pelvis", bones["thigh"], rad["hip"], rad["knee"], colors["pants"], "leg"),
        ("shin", "thigh", bones["shin"], rad["knee"], rad["ankle"], colors["pants"], "leg"),
    )
    sprites = {}
    for name, _parent, length, r0, r1, color, _group in limb_specs:
        sprites[name] = make_limb(length, r0, r1, color, ink, stroke)

    for side, sign in (("l", -1.0), ("r", 1.0)):
        if view == "side":
            arm_offset = shoulder_offset
        else:
            arm_offset = (sign * (bones["shoulder_half"] - rad["shoulder"] * 0.45), -bones["hip_to_shoulder"])
        hip_offset = hip_l if side == "l" else hip_r
        chain = {
            "upper_arm": ("torso", arm_offset, bones["upper_arm"]),
            "lower_arm": (f"upper_arm_{side}", (0.0, bones["upper_arm"]), bones["forearm"]),
            "thigh": ("pelvis", hip_offset, bones["thigh"]),
            "shin": (f"thigh_{side}", (0.0, bones["thigh"]), bones["shin"]),
        }
        for bone, (parent, offset, length) in chain.items():
            image, pivot = sprites[bone]
            part_name = f"{bone}_{side}"
            file_name = f"{view}_{part_name}.png"
            Image.fromarray(image, "RGBA").save(out_dir / file_name)
            group = ("arm_" if bone in ("upper_arm", "lower_arm") else "leg_") + side
            parts.append(_part_record(part_name, parent, pivot, 0.0, file_name, z[part_name], offset, group, image, length))

        # Drawn glove and shoe. A cropped scrap from the painting does not read as a hand or a foot.
        thumb = -1.0 if side == "l" else 1.0
        sprite, pivot = make_mitten(bones["H"], rad["wrist"], colors["glove"], ink, stroke, thumb)
        file_name = f"{view}_hand_{side}.png"
        Image.fromarray(sprite, "RGBA").save(out_dir / file_name)
        parts.append(_part_record(
            f"hand_{side}", f"lower_arm_{side}", pivot, 0.0, file_name, z[f"hand_{side}"],
            (0.0, bones["forearm"]), f"arm_{side}", sprite, 0.0,
        ))
        if view == "side":
            sprite, pivot = make_shoe_side(bones["H"], rad["ankle"], colors["boot"], ink, stroke)
        else:
            sprite, pivot = make_shoe_front(bones["H"], rad["ankle"], colors["boot"], ink, stroke)
        file_name = f"{view}_foot_{side}.png"
        Image.fromarray(sprite, "RGBA").save(out_dir / file_name)
        parts.append(_part_record(
            f"foot_{side}", f"shin_{side}", pivot, 0.0, file_name, z[f"foot_{side}"],
            (0.0, bones["shin"]), f"leg_{side}", sprite, 0.0,
        ))
    return parts


def _public_part(part: dict) -> dict:
    return {
        "name": part["name"],
        "parent": part["parent"],
        "pivot": part["pivot"],
        "rest": part["rest"],
        "file": part["file"],
        "z": part["z"],
    }


def build_character(folder: Path) -> dict:
    bones = measure_bones(folder)
    # Shoulders wider than the head, hips under them. A measured pose that is
    # narrower than this collapses the front view into one tube.
    height = bones["H"]
    bones["shoulder_half"] = max(float(bones["shoulder_half"]), 0.125 * height)
    bones["hip_half"] = max(float(bones["hip_half"]), 0.070 * height)
    bones["shoulder_forward"] = 0.035 * height
    # Sleeve plus glove reaches the middle of the thigh. The measured arm was
    # too short, so the hands stopped at the belt and the figure stopped reading as a person.
    bones["upper_arm"] = 0.175 * height
    bones["forearm"] = 0.150 * height
    bones["hip_to_shoulder"] = min(max(float(bones["hip_to_shoulder"]), 0.22 * height), 0.30 * height)
    front_meta = _view_meta(folder, "front")
    front = _load_rgba(folder / front_meta["file"])
    colors = sample_colors(front, bones, front_meta["anchors"])
    front_crops = _attach_crops(front, bones, colors, front_meta)
    side_crops = front_crops
    tq_path = folder / "three_quarter.png"
    if tq_path.exists():
        tq_meta = _view_meta(folder, "three_quarter")
        tq = _load_rgba(tq_path)
        tq_colors = sample_colors(tq, bones, tq_meta["anchors"])
        # Profile head, gloves, and boots read a step better than a flat front crop.
        side_crops = _attach_crops(tq, bones, colors, tq_meta)
        if side_crops["head"] is None:
            side_crops["head"] = front_crops["head"]
        for side in ("l", "r"):
            if not side_crops["hands"].get(side):
                side_crops["hands"][side] = front_crops["hands"].get(side)
            # Front-view boots are the standing shoes. Use them for both dolls.
            if front_crops["feet"].get(side):
                side_crops["feet"][side] = front_crops["feet"][side]
            elif not side_crops["feet"].get(side):
                side_crops["feet"][side] = None
        colors = dict(colors)
        hand_samples = [item for item in side_crops["hands"].values() if item]
        foot_samples = [item for item in side_crops["feet"].values() if item]
        if hand_samples:
            colors["glove"] = hand_samples[0]["color"]
        if foot_samples:
            colors["boot"] = foot_samples[0]["color"]
        # Keep the jacket/pants/skin sampled from the front plate; three-quarter
        # only replaces the painted extremities. tq_colors stays available if a
        # front sample came back empty.
        if colors["jacket"] == (48, 78, 112):
            colors["jacket"] = tq_colors["jacket"]
    out_dir = folder / "flash_doll"
    out_dir.mkdir(parents=True, exist_ok=True)
    views = {}
    for view, crops in (("side", side_crops), ("front", front_crops)):
        views[view] = _build_view(view, bones, colors, crops, out_dir)
    payload = {
        "name": folder.name,
        "body_height": round(bones["H"], 2),
        "facing": front_meta["facing"],
        "bone_source": bones["source"],
        "colors": {key: list(_rgb(value)) for key, value in colors.items()},
        "bones": {
            key: round(float(bones[key]), 2)
            for key in (
                "hip_to_neck", "hip_to_shoulder", "upper_arm", "forearm",
                "thigh", "shin", "shoulder_half", "hip_half", "shoulder_forward",
            )
        },
        "crops": {
            "side": _crop_dump(side_crops),
            "front": _crop_dump(front_crops),
        },
        "views": {
            view: {"parts": [_public_part(part) for part in parts]}
            for view, parts in views.items()
        },
    }
    (out_dir / "doll.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"name": folder.name, "bones": bones, "colors": colors, "views": views, "dir": out_dir}


def _crop_dump(crops: dict) -> dict:
    dumped = {}
    head = crops.get("head")
    if head:
        dumped["head"] = {"file": head["file"], "rect": head["rect"]}
    for kind in ("hands", "feet"):
        dumped[kind] = {}
        for side, item in crops.get(kind, {}).items():
            if item:
                dumped[kind][side] = {"file": item["file"], "rect": item["rect"]}
    return dumped


def _solve(parts: list[dict], angles: dict[str, float], hip_y: float) -> tuple[dict, dict]:
    by_name = {part["name"]: part for part in parts}
    pos = {"pelvis": np.array([0.0, hip_y], np.float64)}
    ang = {"pelvis": 0.0}
    pending = [part for part in parts if part["name"] != "pelvis"]
    guard = 0
    while pending and guard < 30:
        guard += 1
        next_pending = []
        for part in pending:
            parent = part["parent"] or "pelvis"
            if parent not in pos:
                next_pending.append(part)
                continue
            ox, oy = part["offset"]
            rx, ry = rotate_cw(ox, oy, ang[parent])
            pos[part["name"]] = pos[parent] + np.array([rx, ry])
            ang[part["name"]] = ang[parent] + float(part["rest"]) + float(angles.get(part["name"], 0.0))
        if len(next_pending) == len(pending):
            break
        pending = next_pending
    return pos, ang


def _end_x(group: str, pos: dict) -> float:
    if group.startswith("leg_"):
        return float(pos[f"foot_{group[-1]}"][0]) if f"foot_{group[-1]}" in pos else float(pos.get(f"thigh_{group[-1]}", [0, 0])[0])
    if group.startswith("arm_"):
        return float(pos[f"hand_{group[-1]}"][0]) if f"hand_{group[-1]}" in pos else float(pos.get(f"upper_arm_{group[-1]}", [0, 0])[0])
    return 0.0


def _draw_order(parts: list[dict], pos: dict, height: float) -> list[dict]:
    hip_x = float(pos["pelvis"][0])
    tol = 0.02 * height

    def key(part: dict) -> tuple[int, int]:
        group = part["group"]
        if group == "head":
            return (3, part["z"])
        if group in ("torso", "pelvis"):
            return (1, part["z"])
        end = _end_x(group, pos)
        near = group.endswith("_l")
        if end > hip_x + tol:
            layer = 2
        elif end < hip_x - tol:
            layer = 0
        else:
            layer = 2 if near else 0
        return (layer, part["z"])

    return sorted((part for part in parts if part["image"] is not None), key=key)


def _warp_stamp(canvas: np.ndarray, sprite: np.ndarray, pivot, world, degrees: float) -> None:
    theta = math.radians(degrees)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    px, py = float(pivot[0]), float(pivot[1])
    wx, wy = float(world[0]), float(world[1])
    matrix = np.array([
        [cos_t, sin_t, px - cos_t * wx - sin_t * wy],
        [-sin_t, cos_t, py + sin_t * wx - cos_t * wy],
    ], np.float32)
    height, width = canvas.shape[:2]
    flat = sprite.astype(np.float32)
    alpha = flat[:, :, 3:4] / 255.0
    flat[:, :, :3] *= alpha
    # The matrix already maps destination pixels back to the sprite.
    warped = cv2.warpAffine(
        flat, matrix, (width, height),
        flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0),
    )
    wa = warped[:, :, 3:4] / 255.0
    rgb = np.divide(warped[:, :, :3], np.maximum(wa, 1e-4), where=wa > 1e-4, out=np.zeros_like(warped[:, :, :3]))
    src = np.zeros_like(canvas)
    src[:, :, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    src[:, :, 3] = np.clip(wa[..., 0] * 255.0, 0, 255).astype(np.uint8)
    _over(canvas, src)


def _sprite_max_y(sprite: np.ndarray, pivot, world, degrees: float) -> float | None:
    ys, xs = np.where(sprite[:, :, 3] > 140)
    if len(xs) == 0:
        return None
    step = max(1, len(xs) // 2500)
    dx = xs[::step].astype(np.float64) - float(pivot[0])
    dy = ys[::step].astype(np.float64) - float(pivot[1])
    rx, ry = rotate_cw(dx, dy, degrees) if False else _rotate_many(dx, dy, degrees)
    return float(np.max(ry + float(world[1])))


def _rotate_many(x: np.ndarray, y: np.ndarray, degrees: float) -> tuple[np.ndarray, np.ndarray]:
    theta = math.radians(degrees)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    return (x * cos_t - y * sin_t, x * sin_t + y * cos_t)


def _pose_angles(spec: dict) -> dict[str, float]:
    angles = {}
    for key, value in spec.items():
        if key == "hip":
            continue
        angles[PART_FROM_POSE[key]] = float(value)
    return angles


def sole_clearance(parts: list[dict], spec: dict, height: float) -> tuple[float, float, float]:
    """Return stance sole, swing sole, and how far the swing sole sits above the stance sole."""
    pos, ang = _solve(parts, _pose_angles(spec), 0.0)
    soles = {}
    for name in ("foot_l", "foot_r"):
        part = next(item for item in parts if item["name"] == name)
        soles[name] = _sprite_max_y(part["image"], part["pivot"], pos[name], ang[name])
    stance = soles["foot_l"]
    swing = soles["foot_r"]
    return stance, swing, stance - swing


def render_pose(parts: list[dict], spec: dict, height: float) -> tuple[np.ndarray, dict]:
    hip_y = float(spec.get("hip", 0.0)) * height
    angles = _pose_angles(spec)
    pos, ang = _solve(parts, angles, hip_y)
    drawn = _draw_order(parts, pos, height)
    corners = []
    for part in drawn:
        h, w = part["image"].shape[:2]
        px, py = part["pivot"]
        for x, y in ((0, 0), (w - 1, 0), (w - 1, h - 1), (0, h - 1)):
            rx, ry = rotate_cw(x - px, y - py, ang[part["name"]])
            corners.append((pos[part["name"]][0] + rx, pos[part["name"]][1] + ry))
    xs = [point[0] for point in corners]
    ys = [point[1] for point in corners]
    margin = int(0.04 * height) + 8
    min_x, min_y = math.floor(min(xs)) - margin, math.floor(min(ys)) - margin
    max_x, max_y = math.ceil(max(xs)) + margin, math.ceil(max(ys)) + margin
    canvas = np.zeros((max_y - min_y + 1, max_x - min_x + 1, 4), np.uint8)
    origin = np.array([min_x, min_y], np.float64)
    for part in drawn:
        _warp_stamp(canvas, part["image"], part["pivot"], pos[part["name"]] - origin, ang[part["name"]])
    joints = {}
    for name in ("thigh_l", "thigh_r", "shin_l", "shin_r", "upper_arm_l", "upper_arm_r", "lower_arm_l", "lower_arm_r"):
        if name in pos:
            joints[name] = pos[name] - origin
    soles = {}
    for name in ("foot_l", "foot_r"):
        part = next(item for item in parts if item["name"] == name)
        soles[name] = _sprite_max_y(part["image"], part["pivot"], pos[name] - origin, ang[name])
    info = {
        "joints": joints,
        "soles": soles,
        "pelvis": pos["pelvis"] - origin,
        "angles": ang,
    }
    return canvas, info


def _joint_fill(image: np.ndarray, center, radius: float) -> float:
    if center is None or radius < 2:
        return 1.0
    h, w = image.shape[:2]
    cx, cy = int(round(float(center[0]))), int(round(float(center[1])))
    r = max(2, int(round(radius * 0.55)))
    x0, x1 = max(0, cx - r), min(w, cx + r + 1)
    y0, y1 = max(0, cy - r), min(h, cy + r + 1)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    yy, xx = np.ogrid[y0:y1, x0:x1]
    disk = (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r
    alpha = image[y0:y1, x0:x1, 3]
    if not np.any(disk):
        return 0.0
    return float((alpha[disk] > 128).mean())


def _font(size: int) -> ImageFont.ImageFont:
    for path in (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\segoeui.ttf"):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _lift_swing_foot(parts: list[dict], spec: dict, height: float, minimum: float) -> dict:
    """Bend the swinging knee until that sole clears the stance sole."""
    tuned = dict(spec)
    for _ in range(14):
        _stance, _swing, clearance = sole_clearance(parts, tuned, height)
        if clearance >= minimum:
            return tuned
        tuned["lower_leg_r"] = float(tuned.get("lower_leg_r", 0.0)) + 4.0
        if tuned["lower_leg_r"] > 88.0:
            break
    return tuned


def compose_sheet(dolls: list[dict], out_path: Path) -> dict:
    """Four side-view poses on one ground line. Stance sole is the left foot."""
    panels = []
    report = {}
    for doll in dolls:
        parts = doll["views"]["side"]
        height = doll["bones"]["H"]
        rad = _radii(doll["bones"], "side")
        row = []
        report[doll["name"]] = {}
        for name, spec in POSES:
            tuned = dict(spec)
            if name in ("down", "passing", "up"):
                tuned = _lift_swing_foot(parts, tuned, height, 0.05 * height)
            image, info = render_pose(parts, tuned, height)
            stance = info["soles"]["foot_l"]
            swing = info["soles"]["foot_r"]
            fills = {
                "knee_l": _joint_fill(image, info["joints"].get("shin_l"), rad["knee"]),
                "knee_r": _joint_fill(image, info["joints"].get("shin_r"), rad["knee"]),
                "elbow_l": _joint_fill(image, info["joints"].get("lower_arm_l"), rad["elbow"]),
                "elbow_r": _joint_fill(image, info["joints"].get("lower_arm_r"), rad["elbow"]),
            }
            report[doll["name"]][name] = {
                "clearance_px": None if stance is None or swing is None else round(stance - swing, 1),
                "joint_fill": {key: round(value, 3) for key, value in fills.items()},
                "lower_leg_r": tuned.get("lower_leg_r"),
                "hip": tuned.get("hip", 0.0),
            }
            row.append((name, image, stance, info["pelvis"][0]))
        panels.append((doll["name"], row, height))

    target_h = 760
    label_h = 54
    gap = 28
    rows_img = []
    ground_rows = []
    scales = []
    for _name, row, height in panels:
        scale = target_h / height
        scales.append(scale)
        placed = []
        max_w = 0
        for name, image, sole, pelvis_x in row:
            new_w = max(1, int(round(image.shape[1] * scale)))
            new_h = max(1, int(round(image.shape[0] * scale)))
            scaled = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)
            sole_s = None if sole is None else sole * scale
            pelvis_s = pelvis_x * scale
            placed.append((name, scaled, sole_s, pelvis_s))
            max_w = max(max_w, new_w)
        panel_w = max_w + 36
        row_h = label_h + target_h + 36
        sheet = Image.new("RGB", (panel_w * 4 + gap * 3, row_h), (244, 239, 230))
        draw = ImageDraw.Draw(sheet)
        font = _font(32)
        ground_y = label_h + target_h
        # Just under the sole pixel. Characters are pasted afterwards so the shoe covers the line it stands on.
        draw.line([(0, ground_y + 2), (sheet.width, ground_y + 2)], fill=(28, 24, 20), width=3)
        for index, (name, image, sole, pelvis_x) in enumerate(placed):
            pil = Image.fromarray(image, "RGBA")
            paste_x = int(index * (panel_w + gap) + panel_w / 2 - pelvis_x)
            paste_y = int(ground_y - (sole if sole is not None else image.shape[0]))
            sheet.paste(pil, (paste_x, paste_y), pil)
            label = name
            box = draw.textbbox((0, 0), label, font=font)
            text_w = box[2] - box[0]
            text_x = int(index * (panel_w + gap) + panel_w / 2 - text_w / 2)
            draw.text((text_x, 12), label, fill=(28, 24, 20), font=font)
        rows_img.append(sheet)
        ground_rows.append(ground_y)

    width = max(image.width for image in rows_img)
    height_sum = sum(image.height for image in rows_img) + 8 * (len(rows_img) + 1)
    final = Image.new("RGB", (width, height_sum), (244, 239, 230))
    draw = ImageDraw.Draw(final)
    font = _font(28)
    y = 8
    for (name, _row, _height), image in zip(panels, rows_img):
        final.paste(image, (0, y))
        draw.text((16, y + 8), name, fill=(90, 78, 64), font=font)
        y += image.height + 8
    out_path.parent.mkdir(parents=True, exist_ok=True)
    final.save(out_path)
    report["sheet"] = str(out_path)
    return report


def main() -> None:
    dolls = []
    for person in ("linlan", "chen"):
        doll = build_character(CAST / person)
        bones = doll["bones"]
        print(
            person,
            bones["source"],
            f"H={bones['H']:.0f}",
            f"arm={bones['upper_arm']:.0f}+{bones['forearm']:.0f}",
            f"leg={bones['thigh']:.0f}+{bones['shin']:.0f}",
            f"torso={bones['hip_to_neck']:.0f}",
        )
        dolls.append(doll)
    report = compose_sheet(dolls, CAST / "walk_poses.png")
    for person, poses in report.items():
        if person == "sheet":
            continue
        for pose, info in poses.items():
            print(person, pose, "clearance", info["clearance_px"], "kneeR", info["lower_leg_r"], "fill", info["joint_fill"])
    print(report["sheet"])


if __name__ == "__main__":
    main()
