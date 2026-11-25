"""Clean full-body plates and a sprite-only cutout document. No mesh, no bones."""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from renderer.rig_mesh import (
    _drop_ground_stain,
    _keep_main_island,
    _punch_enclosed_chroma,
    _remove_boundary_background,
    _suppress_green_spill,
)


def _kill_floor(rgba: np.ndarray) -> np.ndarray:
    """Delete the painted floor under the soles, including dull brown stains."""
    out = np.ascontiguousarray(rgba.copy())
    alpha = out[:, :, 3]
    ys, xs = np.where(alpha > 32)
    if len(ys) == 0:
        return out
    top, bottom = int(ys.min()), int(ys.max())
    height = max(1, bottom - top)
    band = slice(top + int(height * 0.90), bottom + 1)
    hsv = cv2.cvtColor(out[:, :, :3], cv2.COLOR_RGB2HSV)
    hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    floor_color = (
        (alpha > 16)
        & (hue >= 8)
        & (hue <= 55)
        & (sat >= 10)
        & (val >= 20)
    )
    floor_color[: band.start] = False
    # Only the stain beside the shoes: a sole has the boot continuing above it.
    clear_above = np.ones(alpha.shape, dtype=bool)
    span = max(12, int(height * 0.035))
    lifted = np.pad(alpha, ((span, 0), (0, 0)))[:-span]
    clear_above = lifted < 24
    out[:, :, 3] = np.where(floor_color & clear_above, 0, alpha).astype(np.uint8)
    return out


def _despill(rgba: np.ndarray) -> np.ndarray:
    out = _suppress_green_spill(rgba)
    alpha = out[:, :, 3]
    transparent = (alpha < 16).astype(np.uint8)
    edge = (cv2.dilate(transparent, np.ones((5, 5), np.uint8)) > 0) & (alpha > 0)
    rgb = out[:, :, :3].astype(np.int16)
    greenish = edge & (rgb[:, :, 1] > rgb[:, :, 0] + 8) & (rgb[:, :, 1] > rgb[:, :, 2] + 8)
    rgb[:, :, 1] = np.where(greenish, np.maximum(rgb[:, :, 0], rgb[:, :, 2]), rgb[:, :, 1])
    out[:, :, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    # Eat a one-pixel halo so leftover screen does not outline the body.
    kernel = np.ones((3, 3), np.uint8)
    core = cv2.erode((out[:, :, 3] > 40).astype(np.uint8), kernel, iterations=1)
    fringe = (out[:, :, 3] > 0) & (core == 0)
    out[:, :, 3] = np.where(fringe, 0, out[:, :, 3]).astype(np.uint8)
    return out


def clean_plate(source: Path) -> tuple[np.ndarray, dict]:
    raw = np.asarray(Image.open(source).convert("RGBA"))
    image = _remove_boundary_background(raw)
    image = _punch_enclosed_chroma(image)
    image = _drop_ground_stain(image)
    image = _kill_floor(image)
    image = _keep_main_island(image)
    image = _despill(image)
    image = _keep_main_island(image)
    alpha = image[:, :, 3]
    ys, xs = np.where(alpha > 32)
    if len(ys) == 0:
        raise RuntimeError(f"empty cutout: {source}")
    top, bottom = int(ys.min()), int(ys.max())
    left, right = int(xs.min()), int(xs.max())
    pad = 8
    crop = image[
        max(0, top - pad) : min(image.shape[0], bottom + pad + 1),
        max(0, left - pad) : min(image.shape[1], right + pad + 1),
    ]
    local_alpha = crop[:, :, 3]
    lys, lxs = np.where(local_alpha > 32)
    feet_y = int(lys.max())
    # Horizontal centre of the shoes, so a turned figure still stands on its feet.
    shoe = lys > int(lys.max() * 0.92)
    center_x = float(np.median(lxs[shoe])) if shoe.any() else float(np.median(lxs))
    return crop, {
        "center_x": round(center_x, 2),
        "feet_y": feet_y,
        "body_height": int(lys.max() - lys.min()),
        "width": int(crop.shape[1]),
        "height": int(crop.shape[0]),
    }


def _mirror_anchors(anchors: dict, width: int) -> dict:
    mirrored = json.loads(json.dumps(anchors))

    def flip_box(box: dict) -> dict:
        return {
            "x": round(width - (float(box["x"]) + float(box["w"])), 2),
            "y": box["y"],
            "w": box["w"],
            "h": box["h"],
        }

    for key in ("head_box", "face_box", "mouth"):
        if isinstance(mirrored.get(key), dict):
            mirrored[key] = flip_box(mirrored[key])
    for left_key, right_key in (("eye_left", "eye_right"), ("brow_left", "brow_right")):
        left, right = mirrored.get(left_key), mirrored.get(right_key)
        if isinstance(left, dict) and isinstance(right, dict):
            mirrored[left_key] = flip_box(right)
            mirrored[right_key] = flip_box(left)
    return mirrored


def _face_anchors(image: np.ndarray) -> dict | None:
    """Mouth and eyes for the views where a face is visible. Backs stay silent."""
    try:
        from renderer.face_anchors import detect_face_anchors
    except ImportError:
        return None
    try:
        anchors = detect_face_anchors(image)
    except (ValueError, RuntimeError):
        return None
    if float(anchors.get("confidence", 0.0)) < 0.45:
        return None
    return anchors


def write_view(source: Path, dest_png: Path) -> dict:
    image, meta = clean_plate(source)
    dest_png.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(image, "RGBA").save(dest_png)
    meta["file"] = dest_png.name
    return meta


def build_cutout(views: dict[str, Path], out_dir: Path, native_facing: str = "right") -> Path:
    """views keys: front, three_quarter, back. Left-facing copies are baked."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stored: dict[str, dict] = {}
    for name, source in views.items():
        stored[name] = write_view(source, out_dir / f"{name}.png")
        image = np.asarray(Image.open(out_dir / f"{name}.png"))
        if name in ("front", "three_quarter"):
            anchors = _face_anchors(image)
            if anchors is not None:
                stored[name]["face_anchors"] = anchors
        if name == "front":
            continue
        flipped = np.ascontiguousarray(image[:, ::-1])
        left_name = f"{name}_left"
        Image.fromarray(flipped, "RGBA").save(out_dir / f"{left_name}.png")
        width = flipped.shape[1]
        meta = dict(stored[name])
        meta["file"] = f"{left_name}.png"
        meta["center_x"] = round(width - 1 - float(stored[name]["center_x"]), 2)
        if isinstance(stored[name].get("face_anchors"), dict):
            meta["face_anchors"] = _mirror_anchors(stored[name]["face_anchors"], width)
        stored[left_name] = meta
    document = {
        "version": 1,
        "presentation": "cutout",
        "native_facing": native_facing,
        "views": stored,
    }
    # Feet metrics of the front view keep the old rig fields the player already reads.
    front = stored["front"]
    document.update({
        "texture": front["file"],
        "center_x": front["center_x"],
        "feet_y": front["feet_y"],
        "body_height": front["body_height"],
    })
    path = out_dir / "cutout.json"
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
