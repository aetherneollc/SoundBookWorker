#!/usr/bin/env python3
"""Render a mesh rig with FK, linear blend skinning, and affine triangles.

Usage:
    python renderer/preview_rig.py path/to/rig.json --out preview.png
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import cv2
import numpy as np
from PIL import Image


STANDARD_POSES: dict[str, dict[str, float]] = {
    "rest": {},
    "arm_raise_60": {
        "upper_arm_l": 60.0,
        "upper_arm_r": -60.0,
    },
    "elbow_bend_90": {
        "lower_arm_l": 90.0,
        "lower_arm_r": -90.0,
    },
    "knee_raise": {
        "upper_leg_l": -45.0,
        "lower_leg_l": 55.0,
    },
    "torso_lean": {
        "torso": 15.0,
        "head": -15.0,
    },
    "wave": {
        "upper_arm_r": -58.0,
        "lower_arm_r": -82.0,
        "hand_r": 28.0,
    },
}


def _rotation(angle: float) -> np.ndarray:
    cosine, sine = math.cos(angle), math.sin(angle)
    return np.array([[cosine, -sine], [sine, cosine]], dtype=np.float64)


def forward_kinematics(
    bones: Sequence[Mapping[str, Any]],
    pose_deg: Mapping[str, float] | None = None,
    root_offset: Sequence[float] = (0.0, 0.0),
) -> dict[str, dict[str, Any]]:
    """Resolve posed bone heads/tails and rest-to-pose skinning transforms."""
    pose_deg = pose_deg or {}
    source = {str(bone["name"]): bone for bone in bones}
    state: dict[str, dict[str, Any]] = {}
    pending = list(source)
    offset = np.asarray(root_offset, dtype=np.float64)
    while pending:
        progressed = False
        for name in list(pending):
            bone = source[name]
            parent_name = bone.get("parent")
            if parent_name is not None and str(parent_name) not in state:
                continue
            rest_head = np.asarray(bone["head"], dtype=np.float64)
            rest_tail = np.asarray(bone["tail"], dtype=np.float64)
            rest_angle = float(
                bone.get(
                    "rest_angle",
                    math.atan2(
                        float(rest_tail[1] - rest_head[1]),
                        float(rest_tail[0] - rest_head[0]),
                    ),
                )
            )
            delta = math.radians(float(pose_deg.get(name, 0.0)))
            if parent_name is None:
                posed_head = rest_head + offset
                posed_angle = rest_angle + delta
            else:
                parent = state[str(parent_name)]
                parent_source = source[str(parent_name)]
                parent_rest_head = np.asarray(
                    parent_source["head"], dtype=np.float64
                )
                parent_rest_angle = float(parent["rest_angle"])
                local_offset = _rotation(-parent_rest_angle) @ (
                    rest_head - parent_rest_head
                )
                posed_head = (
                    np.asarray(parent["head"], dtype=np.float64)
                    + _rotation(float(parent["angle"])) @ local_offset
                )
                posed_angle = (
                    float(parent["angle"])
                    + (rest_angle - parent_rest_angle)
                    + delta
                )
            skin_rotation = _rotation(posed_angle - rest_angle)
            posed_tail = posed_head + skin_rotation @ (rest_tail - rest_head)
            # x' = R x + t maps bind-space pixels to posed-space pixels.
            translation = posed_head - skin_rotation @ rest_head
            state[name] = {
                "head": posed_head,
                "tail": posed_tail,
                "angle": posed_angle,
                "rest_angle": rest_angle,
                "matrix": skin_rotation,
                "translation": translation,
            }
            pending.remove(name)
            progressed = True
        if not progressed:
            raise ValueError(
                "bone hierarchy contains a cycle or a missing parent: "
                + ", ".join(pending)
            )
    return state


def linear_blend_skin(
    vertices: np.ndarray,
    weights: Mapping[str, Sequence[float]],
    bone_state: Mapping[str, Mapping[str, Any]],
) -> np.ndarray:
    """Apply up to four exported influences per vertex."""
    vertices = np.asarray(vertices, dtype=np.float64)
    deformed = np.zeros_like(vertices)
    totals = np.zeros(len(vertices), dtype=np.float64)
    for bone_name, raw_values in weights.items():
        if bone_name not in bone_state:
            continue
        values = np.asarray(raw_values, dtype=np.float64)
        if len(values) != len(vertices):
            raise ValueError(
                f"weight array for {bone_name!r} has {len(values)} values, "
                f"expected {len(vertices)}"
            )
        state = bone_state[bone_name]
        transformed = (
            vertices @ np.asarray(state["matrix"], dtype=np.float64).T
            + np.asarray(state["translation"], dtype=np.float64)
        )
        deformed += transformed * values[:, None]
        totals += values
    missing = totals <= 1e-8
    if np.any(missing):
        deformed[missing] = vertices[missing]
        totals[missing] = 1.0
    deformed /= totals[:, None]
    return deformed.astype(np.float32)


def _checkerboard(width: int, height: int, square: int = 18) -> np.ndarray:
    yy, xx = np.mgrid[0:height, 0:width]
    cells = ((xx // square) + (yy // square)) & 1
    light = np.array([247, 247, 249], dtype=np.uint8)
    dark = np.array([232, 233, 237], dtype=np.uint8)
    return np.where(cells[:, :, None] == 0, light, dark).astype(np.uint8)


def _expanded_triangle(points: np.ndarray, amount: float = 0.42) -> np.ndarray:
    center = points.mean(axis=0)
    vectors = points - center
    lengths = np.linalg.norm(vectors, axis=1)
    scales = np.where(lengths > 1e-4, (lengths + amount) / lengths, 1.0)
    return center + vectors * scales[:, None]


def _alpha_composite(
    destination: np.ndarray,
    source_rgba: np.ndarray,
    mask: np.ndarray,
    x: int,
    y: int,
) -> None:
    height, width = source_rgba.shape[:2]
    target = destination[y : y + height, x : x + width]
    alpha = (
        source_rgba[:, :, 3].astype(np.float32) / 255.0
    ) * (mask.astype(np.float32) / 255.0)
    target_f = target.astype(np.float32)
    source_f = source_rgba[:, :, :3].astype(np.float32)
    target[:] = np.clip(
        source_f * alpha[:, :, None]
        + target_f * (1.0 - alpha[:, :, None]),
        0,
        255,
    ).astype(np.uint8)


def _render_triangle(
    canvas: np.ndarray,
    texture: np.ndarray,
    source_points: np.ndarray,
    destination_points: np.ndarray,
) -> None:
    """Affine-warp one textured triangle into ``canvas``."""
    if abs(
        float(
            (source_points[1, 0] - source_points[0, 0])
            * (source_points[2, 1] - source_points[0, 1])
            - (source_points[1, 1] - source_points[0, 1])
            * (source_points[2, 0] - source_points[0, 0])
        )
    ) < 0.05:
        return
    canvas_h, canvas_w = canvas.shape[:2]
    texture_h, texture_w = texture.shape[:2]
    sx0 = max(0, int(math.floor(float(source_points[:, 0].min()))) - 2)
    sy0 = max(0, int(math.floor(float(source_points[:, 1].min()))) - 2)
    sx1 = min(texture_w, int(math.ceil(float(source_points[:, 0].max()))) + 3)
    sy1 = min(texture_h, int(math.ceil(float(source_points[:, 1].max()))) + 3)
    if sx1 <= sx0 or sy1 <= sy0:
        return

    expanded = _expanded_triangle(destination_points)
    dx0 = max(0, int(math.floor(float(expanded[:, 0].min()))) - 2)
    dy0 = max(0, int(math.floor(float(expanded[:, 1].min()))) - 2)
    dx1 = min(canvas_w, int(math.ceil(float(expanded[:, 0].max()))) + 3)
    dy1 = min(canvas_h, int(math.ceil(float(expanded[:, 1].max()))) + 3)
    if dx1 <= dx0 or dy1 <= dy0:
        return

    source_local = source_points - np.array([sx0, sy0], dtype=np.float32)
    destination_local = destination_points - np.array(
        [dx0, dy0], dtype=np.float32
    )
    affine = cv2.getAffineTransform(
        source_local.astype(np.float32),
        destination_local.astype(np.float32),
    )
    crop = np.ascontiguousarray(texture[sy0:sy1, sx0:sx1])
    warped = cv2.warpAffine(
        crop,
        affine,
        (dx1 - dx0, dy1 - dy0),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0),
    )
    mask = np.zeros((dy1 - dy0, dx1 - dx0), dtype=np.uint8)
    expanded_local = expanded - np.array([dx0, dy0], dtype=np.float32)
    cv2.fillConvexPoly(
        mask,
        np.round(expanded_local * 16.0).astype(np.int32),
        255,
        lineType=cv2.LINE_AA,
        shift=4,
    )
    _alpha_composite(canvas, warped, mask, dx0, dy0)


def _draw_skeleton(
    canvas: np.ndarray, state: Mapping[str, Mapping[str, Any]]
) -> None:
    for name, bone in state.items():
        head = tuple(np.round(bone["head"]).astype(int))
        tail = tuple(np.round(bone["tail"]).astype(int))
        if name == "root":
            color = (235, 92, 75)
        elif "arm" in name or name.startswith("hand"):
            color = (222, 76, 157)
        elif "leg" in name or name.startswith("foot"):
            color = (70, 146, 224)
        else:
            color = (75, 181, 116)
        cv2.line(canvas, head, tail, color, 2, lineType=cv2.LINE_AA)
        cv2.circle(canvas, head, 4, (40, 43, 51), -1, lineType=cv2.LINE_AA)
        cv2.circle(canvas, head, 2, (252, 252, 252), -1, lineType=cv2.LINE_AA)


def render_pose(
    rig: Mapping[str, Any],
    texture: np.ndarray,
    pose_deg: Mapping[str, float] | None = None,
    root_offset: Sequence[float] = (0.0, 0.0),
    canvas_size: tuple[int, int] | None = None,
    skeleton: bool = False,
) -> np.ndarray:
    """Render one pose and return an RGB canvas."""
    texture_h, texture_w = texture.shape[:2]
    width, height = canvas_size or (texture_w, texture_h)
    canvas = _checkerboard(width, height)
    state = forward_kinematics(rig["bones"], pose_deg, root_offset)
    for group in sorted(rig["groups"], key=lambda item: int(item["z_index"])):
        vertices = np.asarray(group["vertices"], dtype=np.float32)
        uvs = np.asarray(group["uvs"], dtype=np.float32)
        if len(vertices) == 0:
            continue
        deformed = linear_blend_skin(vertices, group["weights"], state)
        for triangle in group["triangles"]:
            indices = np.asarray(triangle, dtype=np.int32)
            _render_triangle(
                canvas,
                texture,
                uvs[indices],
                deformed[indices],
            )
    if skeleton:
        _draw_skeleton(canvas, state)
    return canvas


def preview_rig(
    rig_path: Path, out_path: Path, skeleton: bool = False
) -> Path:
    """Render the six standard poses as a 3x2 contact sheet."""
    rig_path = Path(rig_path)
    out_path = Path(out_path)
    rig = json.loads(rig_path.read_text(encoding="utf-8"))
    texture_path = Path(str(rig["texture"]))
    if not texture_path.is_absolute():
        texture_path = rig_path.parent / texture_path
    texture = np.asarray(Image.open(texture_path).convert("RGBA"))
    texture_h, texture_w = texture.shape[:2]
    margin = max(28, int(round(max(texture_w, texture_h) * 0.16)))
    label_height = max(32, int(round(texture_h * 0.045)))
    cell_width = texture_w + 2 * margin
    cell_height = texture_h + 2 * margin + label_height
    sheet = np.full(
        (cell_height * 2, cell_width * 3, 3), 242, dtype=np.uint8
    )

    for index, (name, pose) in enumerate(STANDARD_POSES.items()):
        tile = render_pose(
            rig,
            texture,
            pose_deg=pose,
            root_offset=(margin, margin + label_height),
            canvas_size=(cell_width, cell_height),
            skeleton=skeleton,
        )
        font_scale = max(0.55, min(1.0, texture_h / 700.0))
        cv2.putText(
            tile,
            name,
            (18, label_height - 9),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (45, 48, 57),
            2,
            lineType=cv2.LINE_AA,
        )
        row, column = divmod(index, 3)
        sheet[
            row * cell_height : (row + 1) * cell_height,
            column * cell_width : (column + 1) * cell_width,
        ] = tile

    out_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(sheet, "RGB").save(out_path, optimize=True)
    print(
        f"preview_rig: {rig_path.name} -> {out_path} "
        f"poses={len(STANDARD_POSES)}",
        flush=True,
    )
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rig", type=Path, help="rig.json path")
    parser.add_argument("--out", type=Path, required=True, help="preview PNG")
    parser.add_argument(
        "--skeleton", action="store_true", help="overlay posed bones and joints"
    )
    arguments = parser.parse_args()
    preview_rig(arguments.rig, arguments.out, arguments.skeleton)


if __name__ == "__main__":
    main()
