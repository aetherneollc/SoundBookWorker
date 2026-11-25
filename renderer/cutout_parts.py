"""Split a standing cutout into rigid parts hung on joints.

This is a 2D-game paper doll: each piece is its own sprite and rotates about
one joint. It is not a skinned mesh, so a bend cannot stretch the painting.
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

# Drawn back to front. Legs stay on the pelvis, so a chest bend does not lift the feet.
PART_SPEC = (
    ("shin_l", "thigh_l", "kn_l", 1),
    ("shin_r", "thigh_r", "kn_r", 1),
    ("thigh_l", "pelvis", "hip_l", 2),
    ("thigh_r", "pelvis", "hip_r", 2),
    ("torso", "pelvis", "waist", 3),
    ("arm_l", "torso", "sh_l", 4),
    ("arm_r", "torso", "sh_r", 4),
    ("fore_l", "arm_l", "el_l", 5),
    ("fore_r", "arm_r", "el_r", 5),
    ("head", "torso", "neck", 6),
)


def _segment_distance(points: np.ndarray, start: np.ndarray, end: np.ndarray) -> np.ndarray:
    axis = end - start
    length2 = float(np.dot(axis, axis))
    if length2 < 1.0:
        return np.linalg.norm(points - start, axis=1)
    t = np.clip(((points - start) @ axis) / length2, 0.0, 1.0)
    closest = start + t[:, None] * axis
    return np.linalg.norm(points - closest, axis=1)


def _joints_from_pose(rgba: np.ndarray) -> dict[str, np.ndarray] | None:
    from renderer.rig_characters import _try_rtmlib_keypoints

    found = _try_rtmlib_keypoints(rgba)
    if found is None:
        return None
    kps, scores = found
    if float(np.min(scores[5:17])) < 0.20:
        return None
    if float(kps[5, 0]) <= float(kps[6, 0]):
        index = {
            "sh_l": 5, "el_l": 7, "wr_l": 9, "hip_l": 11, "kn_l": 13, "an_l": 15,
            "sh_r": 6, "el_r": 8, "wr_r": 10, "hip_r": 12, "kn_r": 14, "an_r": 16,
        }
    else:
        index = {
            "sh_l": 6, "el_l": 8, "wr_l": 10, "hip_l": 12, "kn_l": 14, "an_l": 16,
            "sh_r": 5, "el_r": 7, "wr_r": 9, "hip_r": 11, "kn_r": 13, "an_r": 15,
        }
    joints = {name: np.asarray(kps[i], dtype=np.float32) for name, i in index.items()}
    hip = 0.5 * (joints["hip_l"] + joints["hip_r"])
    shoulder = 0.5 * (joints["sh_l"] + joints["sh_r"])
    joints["hip_mid"] = hip
    joints["neck"] = np.array([shoulder[0], shoulder[1] - max(10.0, (hip[1] - shoulder[1]) * 0.18)], dtype=np.float32)
    joints["waist"] = 0.42 * hip + 0.58 * shoulder
    # Hands and boots continue past the detected wrist and ankle.
    for side in ("l", "r"):
        forearm = joints[f"wr_{side}"] - joints[f"el_{side}"]
        joints[f"hand_{side}"] = joints[f"wr_{side}"] + forearm * 0.55
        shin = joints[f"an_{side}"] - joints[f"kn_{side}"]
        joints[f"foot_{side}"] = joints[f"an_{side}"] + shin * 0.42
    return joints


def _label_parts(rgba: np.ndarray, joints: dict[str, np.ndarray]) -> np.ndarray:
    height, width = rgba.shape[:2]
    opaque = rgba[:, :, 3] > 32
    ys, xs = np.where(opaque)
    if len(xs) == 0:
        return np.zeros((height, width), np.int16)
    points = np.stack([xs, ys], axis=1).astype(np.float32)
    body_h = float(ys.max() - ys.min())
    limb_r = max(18.0, body_h * 0.045)
    names = [spec[0] for spec in PART_SPEC]
    index_of = {name: i + 1 for i, name in enumerate(names)}

    torso = np.zeros((height, width), np.uint8)
    hip = joints["hip_mid"]
    shoulder = 0.5 * (joints["sh_l"] + joints["sh_r"])
    pad = limb_r * 0.85
    quad = np.array([
        joints["sh_l"] + np.array([-pad, -pad * 0.3]),
        joints["sh_r"] + np.array([pad, -pad * 0.3]),
        joints["hip_r"] + np.array([pad * 0.55, pad * 0.2]),
        joints["hip_l"] + np.array([-pad * 0.55, pad * 0.2]),
    ], dtype=np.int32)
    cv2.fillConvexPoly(torso, quad, 1)
    spine = _segment_distance(points, joints["neck"], hip)

    segments = {
        "head": (joints["neck"], joints["neck"] + np.array([0.0, -body_h * 0.22])),
        "arm_l": (joints["sh_l"], joints["el_l"]),
        "arm_r": (joints["sh_r"], joints["el_r"]),
        "fore_l": (joints["el_l"], joints["hand_l"]),
        "fore_r": (joints["el_r"], joints["hand_r"]),
        "thigh_l": (joints["hip_l"], joints["kn_l"]),
        "thigh_r": (joints["hip_r"], joints["kn_r"]),
        "shin_l": (joints["kn_l"], joints["foot_l"]),
        "shin_r": (joints["kn_r"], joints["foot_r"]),
    }
    dist = {name: _segment_distance(points, start, end) for name, (start, end) in segments.items()}
    label_at = np.zeros(len(points), np.int16)
    best = np.full(len(points), 1e9, dtype=np.float32)
    neck_y = float(joints["neck"][1])
    hip_y = float(hip[1])
    py = points[:, 1]
    in_torso = (
        (torso[ys, xs] > 0)
        & (py >= neck_y)
        & (py <= hip_y + limb_r * 0.4)
    )

    def take(name: str, eligible: np.ndarray) -> None:
        reach = dist[name]
        better = eligible & (reach < best)
        label_at[better] = index_of[name]
        best[better] = reach[better]

    take("head", (py <= neck_y + limb_r * 0.35) & (dist["head"] < limb_r * 2.4))
    for name in ("thigh_l", "thigh_r", "shin_l", "shin_r"):
        take(name, (py >= hip_y - limb_r) & (dist[name] < limb_r * 1.15))
    for name in ("arm_l", "arm_r", "fore_l", "fore_r"):
        # A sleeve outside the jacket wins. A hand inside a pocket stays on the torso.
        outside = ~in_torso | (dist[name] <= spine * 0.72)
        take(name, (dist[name] < limb_r * 1.05) & outside)
    unset = label_at == 0
    torso_fill = unset & (in_torso | ((spine < limb_r * 2.2) & (py >= neck_y) & (py <= hip_y)))
    label_at[torso_fill] = index_of["torso"]

    labels = np.zeros((height, width), np.int16)
    labels[ys, xs] = label_at
    # Anything still loose goes to the nearest piece so the rest pose has no holes.
    # Joints stay on the parent. overlap_masks copies a ring onto the child.
    loose = opaque & (labels == 0)
    if np.any(loose):
        ly, lx = np.where(loose)
        loose_pts = np.stack([lx, ly], axis=1).astype(np.float32)
        loose_dist = np.stack([
            _segment_distance(loose_pts, start, end) for start, end in segments.values()
        ], axis=1)
        # Torso spine is a candidate too.
        loose_spine = _segment_distance(loose_pts, joints["neck"], hip)
        nearest = np.argmin(loose_dist, axis=1)
        names_only = list(segments)
        for i, name_i in enumerate(nearest):
            if loose_spine[i] < loose_dist[i, name_i]:
                labels[ly[i], lx[i]] = index_of["torso"]
            else:
                labels[ly[i], lx[i]] = index_of[names_only[name_i]]
    return labels


def overlap_masks(labels: np.ndarray) -> dict[str, np.ndarray]:
    """Each part, plus a ring of its parent so the joint stays covered when it bends."""
    names = [spec[0] for spec in PART_SPEC]
    index_of = {name: i + 1 for i, name in enumerate(names)}
    parent_of = {name: parent for name, parent, _joint, _z in PART_SPEC}
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31))
    masks = {name: labels == index_of[name] for name in names}
    for name, parent in parent_of.items():
        if parent not in masks or int(masks[name].sum()) < 30:
            continue
        ring = cv2.dilate(masks[name].astype(np.uint8), kernel) > 0
        masks[name] = masks[name] | (ring & masks[parent])
    return masks


def slice_view(rgba: np.ndarray) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]] | None:
    image = np.ascontiguousarray(rgba)
    if image.shape[2] == 3:
        image = np.dstack([image, np.full(image.shape[:2], 255, np.uint8)])
    joints = _joints_from_pose(image)
    if joints is None:
        return None
    labels = _label_parts(image, joints)
    return joints, overlap_masks(labels)


def _crop(image: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, tuple[int, int]] | None:
    ys, xs = np.where(mask)
    if len(xs) < 40:
        return None
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    part = image[y0 : y1 + 1, x0 : x1 + 1].copy()
    part[:, :, 3] = np.where(mask[y0 : y1 + 1, x0 : x1 + 1], part[:, :, 3], 0)
    return part, (x0, y0)


def write_parts(image: np.ndarray, out_dir: Path, view_name: str) -> list[dict] | None:
    sliced = slice_view(image)
    if sliced is None:
        return None
    joints, masks = sliced
    out_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for name, parent, joint_name, z_index in PART_SPEC:
        cropped = _crop(image, masks[name])
        if cropped is None:
            continue
        part, origin = cropped
        file_name = f"{view_name}_{name}.png"
        Image.fromarray(part, "RGBA").save(out_dir / file_name)
        joint = joints[joint_name]
        records.append({
            "name": name,
            "parent": parent,
            "joint": [round(float(joint[0]), 2), round(float(joint[1]), 2)],
            "file": f"parts/{file_name}",
            "origin": [int(origin[0]), int(origin[1])],
            "z": z_index,
            "swing": name in ("arm_l", "arm_r"),
        })
    pelvis = joints["hip_mid"]
    records.append({
        "name": "pelvis",
        "parent": "",
        "joint": [round(float(pelvis[0]), 2), round(float(pelvis[1]), 2)],
        "file": "",
        "origin": [0, 0],
        "z": 0,
    })
    return records


def _rotate(offset: np.ndarray, clockwise_deg: float) -> np.ndarray:
    theta = np.deg2rad(clockwise_deg)
    cos_t, sin_t = float(np.cos(theta)), float(np.sin(theta))
    x, y = float(offset[0]), float(offset[1])
    return np.array([x * cos_t + y * sin_t, -x * sin_t + y * cos_t], dtype=np.float32)


def preview_pose(image: np.ndarray, degrees: dict[str, float]) -> np.ndarray | None:
    """Bend the doll with parent joints carrying their children. Angles are clockwise degrees."""
    sliced = slice_view(image)
    if sliced is None:
        return None
    joints, masks = sliced
    joint_name_of = {name: joint for name, _parent, joint, _z in PART_SPEC}
    world_at = {"pelvis": np.asarray(joints["hip_mid"], dtype=np.float32)}
    world_deg = {"pelvis": 0.0}
    remaining = list(PART_SPEC)
    ordered: list[tuple] = []
    while remaining:
        ready = [spec for spec in remaining if (spec[1] or "pelvis") in world_at or (spec[1] or "pelvis") in {item[0] for item in ordered}]
        if not ready:
            break
        for spec in ready:
            remaining.remove(spec)
            ordered.append(spec)
            name, parent, joint_name, _z = spec
            parent_name = parent if parent else "pelvis"
            # Filled below once the parent world pose exists. Mark the name so children can queue.
            world_at[name] = world_at["pelvis"]
    for name, parent, joint_name, _z in ordered:
        parent_name = parent if parent else "pelvis"
        rest_parent = joints["hip_mid"] if parent_name == "pelvis" else joints[joint_name_of[parent_name]]
        offset = np.asarray(joints[joint_name], dtype=np.float32) - np.asarray(rest_parent, dtype=np.float32)
        world_at[name] = world_at[parent_name] + _rotate(offset, world_deg[parent_name])
        world_deg[name] = world_deg[parent_name] + float(degrees.get(name, 0.0))

    canvas = np.zeros_like(image)
    height, width = image.shape[:2]
    for name, _parent, joint_name, _z in sorted(PART_SPEC, key=lambda spec: spec[3]):
        mask = masks[name]
        if int(mask.sum()) < 40:
            continue
        part = image.copy()
        part[:, :, 3] = np.where(mask, part[:, :, 3], 0)
        angle = float(world_deg[name])
        if abs(angle) > 0.05:
            rest = np.asarray(joints[joint_name], dtype=np.float32)
            world = world_at[name]
            theta = np.deg2rad(angle)
            cos_t, sin_t = float(np.cos(theta)), float(np.sin(theta))
            # Destination pixel p comes from the rest pixel that rotates onto it.
            # p = world + R(q - rest)  =>  q = rest + Rinv(p - world)
            matrix = np.array([
                [cos_t, -sin_t, float(rest[0] - (cos_t * world[0] - sin_t * world[1]))],
                [sin_t, cos_t, float(rest[1] - (sin_t * world[0] + cos_t * world[1]))],
            ], dtype=np.float32)
            part = cv2.warpAffine(
                part, matrix, (width, height),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=(0, 0, 0, 0),
            )
        src = part[:, :, 3:4].astype(np.float32) / 255.0
        canvas[:, :, :3] = (part[:, :, :3] * src + canvas[:, :, :3] * (1.0 - src)).astype(np.uint8)
        canvas[:, :, 3] = np.maximum(canvas[:, :, 3], part[:, :, 3])
    return canvas


def label_overlay(image: np.ndarray) -> np.ndarray | None:
    sliced = slice_view(image)
    if sliced is None:
        return None
    _joints, masks = sliced
    colours = {
        "head": (255, 80, 80, 255),
        "torso": (80, 140, 255, 255),
        "arm_l": (255, 180, 40, 255),
        "arm_r": (255, 120, 40, 255),
        "fore_l": (255, 230, 80, 255),
        "fore_r": (255, 200, 80, 255),
        "thigh_l": (80, 200, 120, 255),
        "thigh_r": (40, 160, 90, 255),
        "shin_l": (80, 220, 220, 255),
        "shin_r": (40, 180, 200, 255),
    }
    out = image.copy()
    tint = np.zeros_like(image)
    for name, colour in colours.items():
        tint[masks[name]] = colour
    src = np.where(tint[:, :, 3] > 0, 0.45, 0.0).astype(np.float32)[..., None]
    out[:, :, :3] = (image[:, :, :3] * (1.0 - src) + tint[:, :, :3] * src).astype(np.uint8)
    return out
