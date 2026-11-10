#!/usr/bin/env python3
"""Build a three-beat stage manifest and Remotion props for the Lin Mo demo.

Shot 1 plants the bust in the empty room. Shot 2 toggles only the mouth
delta. Shot 3 cuts to the smile cel. No video model is involved.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from PIL import Image

WORKER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKER / "renderer"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from prepare_remotion import prepare
from slice_board import slice_board

DEFAULT_BOARD = WORKER / "manju" / "demo" / "source" / "board.jpg"
DEFAULT_ROOM = WORKER / "manju" / "demo" / "source" / "room.jpg"


def _stage_preview(room: Path, cells_dir: Path, name: str, *, flap: bool) -> None:
    """One still of the close-up, so the cutout can be checked before Remotion."""
    frame = Image.open(room).convert("RGBA").resize((1280, 720))
    base = Image.open(cells_dir / "linmo-neutral.png").convert("RGBA")
    height, width = 720, 1280
    scale = 0.86
    box_h = int(height * 0.64 * scale)
    box_w = int(box_h * 0.86)
    cel = base.resize((box_w, box_h), Image.Resampling.LANCZOS)
    if flap:
        delta = Image.open(cells_dir / "linmo-talk_delta.png").convert("RGBA").resize((box_w, box_h), Image.Resampling.LANCZOS)
        cel = Image.alpha_composite(cel, delta)
    x, y = 0.38, 0.97
    left = int(width * x - box_w / 2)
    top = int(height * y - box_h)
    frame.alpha_composite(cel, (left, top))
    frame.convert("RGB").save(cells_dir.parent / name)


def build(board: Path, room: Path, foreground: Path | None = None) -> Path:
    demo = WORKER / "manju" / "demo"
    cells = slice_board(board, demo / "cells", "linmo")
    beats = [
        {"duration": 2.6, "camera": "hold", "enter": "slide", "expression": "neutral",
         "talking": False, "scale": 0.78, "x": 0.50, "y": 0.86, "speaker": "", "line": ""},
        {"duration": 5.0, "camera": "push", "enter": "cut", "expression": "neutral",
         "talking": True, "scale": 1.05, "x": 0.52, "y": 0.88, "speaker": "林默",
         "line": "电梯又停了，我爬楼梯上来的。"},
        {"duration": 3.6, "camera": "hold", "enter": "cut", "expression": "smile",
         "talking": False, "scale": 1.05, "x": 0.52, "y": 0.88, "speaker": "林默",
         "line": "灯还亮着，说明你也没走。"},
    ]
    shots = []
    for beat in beats:
        cell_files = {key: cells["cells"][key] for key in
                      ("neutral", "talk", "smile", "angry", "sad", "shock", "talk_delta")
                      if key in cells["cells"]}
        shots.append({
            "duration": beat["duration"],
            "stage": {
                "background_file": str(room),
                "foreground_file": str(foreground) if foreground else "",
                "camera": beat["camera"],
                "enter": beat["enter"],
                "speaker": beat["speaker"],
                "line": beat["line"],
                "cast": [{
                    "id": "linmo",
                    "cells": cell_files,
                    "expression": beat["expression"],
                    "talking": beat["talking"] and cells.get("flap", False),
                    "x": beat["x"],
                    "y": beat["y"],
                    "scale": beat["scale"],
                }],
            },
        })
    manifest = {
        "version": 1,
        "width": 1280,
        "height": 720,
        "fps": 24,
        "language": "zh",
        "episode_number": 1,
        "episode_title": "",
        "shots": shots,
    }
    manifest_path = demo / "manifest.json"
    props_path = demo / "remotion-props.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _stage_preview(room, demo / "cells", "preview-closed.jpg", flap=False)
    _stage_preview(room, demo / "cells", "preview-open.jpg", flap=True)
    os.chdir(WORKER)
    prepare(manifest_path, props_path)
    return props_path


if __name__ == "__main__":
    board = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_BOARD
    room = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_ROOM
    foreground = Path(sys.argv[3]) if len(sys.argv) > 3 else None
    print(build(board, room, foreground))
