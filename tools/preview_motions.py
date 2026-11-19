#!/usr/bin/env python3
"""Render reusable motion clips against a mesh rig.

Usage:
    python tools/preview_motions.py path/to/rig.json --out media/motion_preview
"""
from __future__ import annotations

import argparse
import bisect
import copy
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import cv2
import numpy as np
from PIL import Image


REPOSITORY = Path(__file__).resolve().parents[1]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

from renderer.preview_rig import render_pose  # noqa: E402


MOTION_ORDER = (
    "idle",
    "talk_calm",
    "talk_emphatic",
    "talk_explain",
    "listen",
    "nod",
    "point",
    "wave",
    "react",
    "walk_medium",
    "step_shift",
)
CORE_GIFS = (
    "idle",
    "talk_emphatic",
    "point",
    "wave",
    "walk_medium",
    "step_shift",
)


def load_motion(path: Path) -> dict[str, Any]:
    """Load one motion document and perform basic preview-time checks."""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{path}: motion JSON must be an object")
    keyframes = document.get("keyframes")
    if not isinstance(keyframes, list) or not keyframes:
        raise ValueError(f"{path}: keyframes must be a non-empty list")
    duration = float(document.get("duration", 0.0))
    if duration <= 0.0:
        raise ValueError(f"{path}: duration must be positive")
    return document


def sample_motion(
    motion: Mapping[str, Any], time_seconds: float
) -> tuple[dict[str, float], tuple[float, float]]:
    """Linearly sample bone angles and ratio-based root translation."""
    keyframes = motion["keyframes"]
    duration = float(motion["duration"])
    time_seconds = float(np.clip(time_seconds, 0.0, duration))
    times = [float(frame["t"]) for frame in keyframes]
    right = bisect.bisect_right(times, time_seconds)
    if right == 0:
        left_frame = right_frame = keyframes[0]
    elif right >= len(keyframes):
        left_frame = right_frame = keyframes[-1]
    else:
        left_frame = keyframes[right - 1]
        right_frame = keyframes[right]

    left_time = float(left_frame["t"])
    right_time = float(right_frame["t"])
    if right_time <= left_time:
        amount = 0.0
    else:
        amount = (time_seconds - left_time) / (right_time - left_time)

    left_bones = left_frame["bones"]
    right_bones = right_frame["bones"]
    bone_names = set(left_bones) | set(right_bones)
    pose = {
        name: float(left_bones.get(name, 0.0))
        + (
            float(right_bones.get(name, 0.0))
            - float(left_bones.get(name, 0.0))
        )
        * amount
        for name in bone_names
    }
    left_root = left_frame["root_offset"]
    right_root = right_frame["root_offset"]
    root_offset = (
        float(left_root[0])
        + (float(right_root[0]) - float(left_root[0])) * amount,
        float(left_root[1])
        + (float(right_root[1]) - float(left_root[1])) * amount,
    )
    return pose, root_offset


def _scaled_assets(
    rig: Mapping[str, Any],
    texture: np.ndarray,
    target_body_height: int,
) -> tuple[dict[str, Any], np.ndarray]:
    """Scale the rig and texture together to keep previews reasonably sized."""
    body_height = float(rig["body_height"])
    if body_height <= 0.0:
        raise ValueError("rig body_height must be positive")
    scale = min(1.0, float(target_body_height) / body_height)
    scaled = copy.deepcopy(rig)

    for field in ("feet_y", "body_height", "center_x"):
        if field in scaled:
            scaled[field] = float(scaled[field]) * scale
    scaled["texture_size"] = [
        max(1, int(round(float(value) * scale)))
        for value in rig.get("texture_size", texture.shape[1::-1])
    ]
    for bone in scaled["bones"]:
        bone["head"] = [float(value) * scale for value in bone["head"]]
        bone["tail"] = [float(value) * scale for value in bone["tail"]]
        if "length" in bone:
            bone["length"] = float(bone["length"]) * scale
    for group in scaled["groups"]:
        group["vertices"] = [
            [float(point[0]) * scale, float(point[1]) * scale]
            for point in group["vertices"]
        ]
        group["uvs"] = [
            [float(point[0]) * scale, float(point[1]) * scale]
            for point in group["uvs"]
        ]

    if scale < 1.0:
        width = max(1, int(round(texture.shape[1] * scale)))
        height = max(1, int(round(texture.shape[0] * scale)))
        texture = cv2.resize(
            texture,
            (width, height),
            interpolation=cv2.INTER_AREA,
        )
    return scaled, np.ascontiguousarray(texture)


def _load_rig_assets(
    rig_path: Path, target_body_height: int
) -> tuple[dict[str, Any], np.ndarray]:
    rig = json.loads(rig_path.read_text(encoding="utf-8"))
    texture_path = Path(str(rig["texture"]))
    if not texture_path.is_absolute():
        texture_path = rig_path.parent / texture_path
    if not texture_path.is_file():
        raise FileNotFoundError(texture_path)
    texture = np.asarray(Image.open(texture_path).convert("RGBA"))
    return _scaled_assets(rig, texture, target_body_height)


def _cell_geometry(texture: np.ndarray) -> tuple[int, int, int, int]:
    texture_h, texture_w = texture.shape[:2]
    margin = max(24, int(round(max(texture_w, texture_h) * 0.075)))
    label_height = max(34, int(round(texture_h * 0.075)))
    return (
        texture_w + 2 * margin,
        texture_h + 2 * margin + label_height,
        margin,
        label_height,
    )


def _render_sample(
    rig: Mapping[str, Any],
    texture: np.ndarray,
    motion: Mapping[str, Any],
    time_seconds: float,
    label: str,
) -> np.ndarray:
    cell_width, cell_height, margin, label_height = _cell_geometry(texture)
    pose, ratio_offset = sample_motion(motion, time_seconds)
    body_height = float(rig["body_height"])
    pixel_offset = (
        margin + ratio_offset[0] * body_height,
        margin + label_height + ratio_offset[1] * body_height,
    )
    tile = render_pose(
        rig,
        texture,
        pose_deg=pose,
        root_offset=pixel_offset,
        canvas_size=(cell_width, cell_height),
    )
    cv2.rectangle(
        tile,
        (0, 0),
        (cell_width, label_height),
        (249, 249, 251),
        thickness=-1,
    )
    font_scale = max(0.48, min(0.72, texture.shape[0] / 700.0))
    cv2.putText(
        tile,
        label,
        (12, label_height - 10),
        cv2.FONT_HERSHEY_SIMPLEX,
        font_scale,
        (40, 43, 51),
        1,
        lineType=cv2.LINE_AA,
    )
    return tile


def _strip_sample_times(motion: Mapping[str, Any]) -> np.ndarray:
    duration = float(motion["duration"])
    if bool(motion["loop"]):
        return np.linspace(0.0, duration, 6, dtype=np.float64)[:-1]
    return np.linspace(0.0, duration, 5, dtype=np.float64)


def render_strip(
    rig: Mapping[str, Any],
    texture: np.ndarray,
    motions: Sequence[Mapping[str, Any]],
    output_path: Path,
) -> Path:
    """Render five representative samples for each motion into one sheet."""
    cell_width, cell_height, _, _ = _cell_geometry(texture)
    sheet = np.full(
        (cell_height * len(motions), cell_width * 5, 3),
        244,
        dtype=np.uint8,
    )
    for row, motion in enumerate(motions):
        for column, time_seconds in enumerate(_strip_sample_times(motion)):
            tile = _render_sample(
                rig,
                texture,
                motion,
                float(time_seconds),
                f"{motion['name']}  t={time_seconds:.2f}s",
            )
            y0 = row * cell_height
            x0 = column * cell_width
            sheet[y0 : y0 + cell_height, x0 : x0 + cell_width] = tile
        if row:
            cv2.line(
                sheet,
                (0, row * cell_height),
                (sheet.shape[1] - 1, row * cell_height),
                (188, 190, 198),
                2,
            )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(sheet, "RGB").save(output_path, optimize=True)
    return output_path


def render_gif(
    rig: Mapping[str, Any],
    texture: np.ndarray,
    motion: Mapping[str, Any],
    output_path: Path,
    preview_fps: float,
) -> Path:
    """Render one clip as a continuously looping animated GIF."""
    duration = float(motion["duration"])
    source_fps = float(motion["fps"])
    frame_count = max(8, int(round(duration * min(preview_fps, source_fps))))
    times = np.arange(frame_count, dtype=np.float64) * duration / frame_count
    frames = [
        Image.fromarray(
            _render_sample(
                rig,
                texture,
                motion,
                float(time_seconds),
                f"{motion['name']}  t={time_seconds:.2f}s",
            ),
            "RGB",
        )
        for time_seconds in times
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame_duration_ms = max(20, int(round(duration * 1000.0 / frame_count)))
    frames[0].save(
        output_path,
        save_all=True,
        append_images=frames[1:],
        duration=frame_duration_ms,
        loop=0,
        disposal=2,
        optimize=False,
    )
    return output_path


def preview_motions(
    rig_path: Path,
    motion_dir: Path,
    out_dir: Path,
    target_body_height: int = 420,
    gif_fps: float = 12.0,
) -> list[Path]:
    """Generate the aggregate strip and six requested looping GIFs."""
    rig_path = Path(rig_path)
    motion_dir = Path(motion_dir)
    out_dir = Path(out_dir)
    if target_body_height < 160:
        raise ValueError("target_body_height must be at least 160 pixels")
    if gif_fps <= 0.0:
        raise ValueError("gif_fps must be positive")

    order = {name: index for index, name in enumerate(MOTION_ORDER)}
    motion_paths = sorted(
        motion_dir.glob("*.json"),
        key=lambda path: (order.get(path.stem, len(order)), path.stem),
    )
    if not motion_paths:
        raise FileNotFoundError(f"no motion JSON files found in {motion_dir}")
    motions = [load_motion(path) for path in motion_paths]
    by_name = {str(motion["name"]): motion for motion in motions}
    rig, texture = _load_rig_assets(rig_path, target_body_height)

    outputs = [
        render_strip(
            rig,
            texture,
            motions,
            out_dir / "all_motions_strip.png",
        )
    ]
    for name in CORE_GIFS:
        if name not in by_name:
            raise ValueError(f"core motion is missing: {name}")
        outputs.append(
            render_gif(
                rig,
                texture,
                by_name[name],
                out_dir / f"{name}.gif",
                gif_fps,
            )
        )
    for path in outputs:
        print(f"preview_motions: wrote {path}", flush=True)
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rig", type=Path, help="path to rig.json")
    parser.add_argument(
        "--motions",
        type=Path,
        default=REPOSITORY / "godot" / "motions",
        help="motion JSON directory",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPOSITORY / "media" / "motion_preview",
        help="preview output directory",
    )
    parser.add_argument(
        "--body-height",
        type=int,
        default=420,
        help="preview character height in pixels",
    )
    parser.add_argument(
        "--gif-fps",
        type=float,
        default=12.0,
        help="GIF preview frame rate",
    )
    arguments = parser.parse_args()
    preview_motions(
        arguments.rig,
        arguments.motions,
        arguments.out,
        arguments.body_height,
        arguments.gif_fps,
    )


if __name__ == "__main__":
    main()
