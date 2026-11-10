#!/usr/bin/env python3
"""Ask Kwai Kolors for one character, one seed, six expressions, and an empty room.

A single six-panel prompt grows extra heads. Image-to-image also redraws the
clothes and the face. The stable Kolors path is six text-to-image calls that
share a seed and differ only in the expression clause. Keys rotate through
SILICONFLOW_API_KEYS.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

STUDIO_SRC = Path(r"D:\wordk\动态漫\SoundBookCF\studio_app\src")
sys.path.insert(0, str(STUDIO_SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from board_spec import CELL_KEYS

EXPRESSIONS = {
    "neutral": "平静闭嘴",
    "talk": "嘴巴微微张开正在说话",
    "smile": "温和微笑，嘴巴闭上",
    "angry": "生气，眉头皱紧，嘴巴闭上",
    "sad": "难过，眼睛看向下方，嘴巴闭上",
    "shock": "惊讶，眼睛睁大，嘴巴张开",
}
BUST = (
    "明亮的日系少年漫画，平涂赛璐璐，干净线稿，大眼睛，健康肤色，柔和均匀的正面光，"
    "没有黑眼圈，没有深阴影。一个年轻男人的胸部以上正面半身，短黑发，棕色眼睛，"
    "灰色拉链连帽衫，白色T恤，{expr}，面向镜头，居中，"
    "纯亮绿色抠像背景，只有一个人，不要文字，不要水印"
)
BUST_NEGATIVE = (
    "text, watermark, logo, letters, extra person, extra head, glasses, "
    "multiple views, collage, grid, hands, full body, scenery, photorealistic, "
    "realistic skin, pores, dark circles, gaunt face, horror, scary, noir, uncanny"
)
ROOM = (
    "空的夜晚中式小房间，无人，没有人影，没有角色。"
    "木书桌和一盏暖色台灯，窗户有雨和远处城市灯光。"
    "二次元动画背景，新海诚配色，横构图，不要文字，不要水印"
)
ROOM_NEGATIVE = "person, people, face, silhouette, character, text, watermark, logo"
SEED = 21


def _ready(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 10_000


def _cell_file(out: Path, key: str) -> Path:
    """Prefer the same-seed text-to-image talk cel over an image-to-image redraw."""
    if key == "talk" and _ready(out / "talk-t2i.png"):
        return out / "talk-t2i.png"
    return out / f"{key}.png"


def _cover_corner(rgb: np.ndarray, *, green: bool) -> np.ndarray:
    """Paint out the provider's bottom-right mark."""
    rgb = np.array(rgb, copy=True)
    height, width = rgb.shape[:2]
    box_w, box_h = min(240, width // 4), min(72, height // 8)
    if green:
        rgb[-box_h:, -box_w:] = rgb[8, 8]
        return rgb
    patch = rgb[-(box_h * 2):-box_h, -box_w:]
    if patch.size == 0:
        return rgb
    rgb[-box_h:, -box_w:] = np.array(
        Image.fromarray(patch).resize((box_w, box_h), Image.Resampling.BILINEAR)
    )
    return rgb


def stitch(out: Path) -> tuple[Path, Path]:
    tiles = []
    for key in CELL_KEYS:
        path = _cell_file(out, key)
        if not _ready(path):
            raise FileNotFoundError(path)
        rgb = np.asarray(Image.open(path).convert("RGB"))
        tiles.append(Image.fromarray(_cover_corner(rgb, green=True)))
    width, height = tiles[0].size
    gutter = 8
    board = Image.new("RGB", (width * 3 + gutter * 2, height * 2 + gutter), "white")
    for index, tile in enumerate(tiles):
        board.paste(tile, ((index % 3) * (width + gutter), (index // 3) * (height + gutter)))
    board_path = out / "board.png"
    board.save(board_path)
    room_src = out / "room.png"
    room = np.asarray(Image.open(room_src).convert("RGB"))
    room_path = out / "room-clean.png"
    Image.fromarray(_cover_corner(room, green=False)).save(room_path)
    print(f"board={board_path}", flush=True)
    print(f"room={room_path}", flush=True)
    return board_path, room_path


def generate(out: Path, *, force: bool = False) -> None:
    from soundstorywriter.llm.openai_image_client import OpenAICompatImageClient

    client = OpenAICompatImageClient(backend="siliconflow", model="Kwai-Kolors/Kolors")
    if not client.available:
        print("No SILICONFLOW_API_KEY / SILICONFLOW_API_KEYS.", flush=True)
        raise SystemExit(2)
    print(f"Kolors key pool: {len(client.api_keys)}", flush=True)
    for key, expr in EXPRESSIONS.items():
        dest = out / f"{key}.png"
        if key == "talk" and _ready(out / "talk-t2i.png") and not force:
            continue
        if not force and _ready(dest):
            continue
        client.generate(
            BUST.format(expr=expr), dest,
            negative_prompt=BUST_NEGATIVE, size="1024x1024", force=True, seed=SEED,
        )
        print(f"cel {key}", flush=True)
    room = out / "room.png"
    if force or not _ready(room):
        client.generate(
            ROOM, room, negative_prompt=ROOM_NEGATIVE, size="1280x720", force=True, seed=11,
        )


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else Path("manju/demo/kolors")
    out.mkdir(parents=True, exist_ok=True)
    force = "--force" in sys.argv
    generate(out, force=force)
    stitch(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
