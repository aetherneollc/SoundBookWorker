#!/usr/bin/env python3
"""Lunar-bay textures for the observation-deck cyclorama and interior.

SiliconFlow Kolors (Kwai-Kolors/Kolors) only. This script does not call fal.ai.
Keys are read from SILICONFLOW_API_KEY / SILICONFLOW_API_KEYS in
D:\\wordk\\动态漫\\SoundBookCF\\studio_app\\.env and are never printed.

A finished texture that already has the right pixel size is left alone unless
--force. --attempt 0..2 picks the next seed. --reprocess rebuilds a finished
PNG from media/assets3d/textures/raw without calling Kolors again.

    python tools/gen_bay_textures.py
    python tools/gen_bay_textures.py --only bay_floor --attempt 1 --force
    python tools/gen_bay_textures.py --reprocess --only lunar_panorama
    python tools/gen_bay_textures.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
STUDIO_SRC = Path(r"D:\wordk\动态漫\SoundBookCF\studio_app\src")
ENV_FILE = Path(r"D:\wordk\动态漫\SoundBookCF\studio_app\.env")
OUT = ROOT / "media" / "assets3d" / "textures"
RAW = OUT / "raw"
MANIFEST = OUT / "manifest.json"
MODEL = "Kwai-Kolors/Kolors"

# Horizon lands at 60% of the height, so the lunar surface fills the lower 40%.
HORIZON_FRACTION = 0.60
PANORAMA_SIZE = (1536, 768)
FLOOR_SIZE = (1024, 1024)
WALL_SIZE = (1024, 1024)
CONSOLE_SIZE = (1024, 576)  # 16:9
FLOOR_MEAN = np.array([154.0, 166.0, 177.0], dtype=np.float32)  # between #a6b2bd and #8d99a5
WALL_MEAN = np.array([34.0, 59.0, 88.0], dtype=np.float32)  # between #264362 and #1d334d

LUNAR_PROMPT = (
    "一张干净的二次元科幻月球远景，平涂，清晰，没有窗。"
    "天空是深暮蓝色，没有云，没有星星，上面留出大片天空。"
    "天空里只有一颗很大的地球，能看见蓝色海洋和白色云团，是凸月，边缘有大气光。"
    "不要第二颗星球，不要行星环，不要卫星。"
    "地平线放在画面偏下的位置。地平线下面是远处的明亮银灰色环形山平原，没有近处悬崖。"
    "远处一条低矮的锯齿山脉。地平线上只有一座极小的细天线塔剪影，像一根针。"
    "不要人，不要车，不要文字，不要水印。塔和地球不要放在右下角。"
)
LUNAR_NEGATIVE = (
    "window, frame, glass, indoor, people, astronaut, rover, cliff, canyon, foreground rock, "
    "cloudy sky, cumulus, cloudscape, overcast, stars, starfield, second planet, ringed planet, "
    "saturn, extra moon, text, letters, watermark, logo, photorealistic, blurry, city, green sky"
)
FLOOR_PROMPT = (
    "正方形游戏材质贴图，可无缝平铺的金属地板，不是房间照片。"
    "严格正交俯视，相机在正上方九十度垂直向下，完全没有透视，没有消失点，没有地平线，没有近大远小。"
    "所有接缝都是绝对水平或绝对垂直的直线，平行于画面四边。"
    "浅冷灰蓝色阳极氧化金属甲板，模块化矩形地砖铺满整张图，细几何接缝，接缝处有很小的凹陷螺钉，只有很淡的划痕。"
    "光照完全均匀，没有方向光，没有阴影带。四边都可以继续拼接。不要墙壁，不要人物，不要文字，不要水印。"
)
FLOOR_NEGATIVE = (
    "perspective, vanishing point, horizon, foreshortening, isometric, oblique, angled camera, tilt, "
    "three-quarter view, room, wall, ceiling, window, people, person, astronaut, foot, shoe, rover, "
    "text, letters, numbers, watermark, logo, signature, border, frame, vignette, shadow, "
    "photorealistic, blur"
)
WALL_PROMPT = (
    "正方形游戏材质贴图，左右可无缝拼接的舱壁，不是房间照片。"
    "严格正面正交，相机正对墙面，完全没有透视，没有消失点。"
    "所有板缝要么完全水平，要么完全垂直，平行于画面四边。"
    "深石板蓝航空金属舱壁，偏暗的蓝灰色，模块化矩形镶板铺满整张图，缝线干净，四角有很小的铆钉，少量细而暗的结构肋。"
    "光照完全均匀，没有地板，没有天花板分界，没有窗户，没有门。不要人物，不要文字，不要水印。"
)
WALL_NEGATIVE = (
    "perspective, vanishing point, foreshortening, isometric, oblique, angled camera, tilt, "
    "floor, ceiling, window, door, people, person, astronaut, text, letters, numbers, watermark, "
    "logo, signature, border, frame, vignette, shadow, photorealistic, blur, sky"
)
CONSOLE_PROMPT = (
    "抽象信息图表，不是风景画，不是照片，不是设备，没有画框。"
    "整张图铺满深海军蓝底色，四边都是这个深色。"
    "正中间一条发光的青色折线波形。左上方一个圆，圆里面只有细网格。右侧五根高低不同的青色竖条。"
    "右上角一个很小的橙色实心圆点。不要山，不要海，不要星球，不要天空，不要建筑。"
    "不要任何字母、数字、汉字或乱码。不要水印。圆点不要放在右下角。"
)
CONSOLE_NEGATIVE = (
    "landscape, mountain, ocean, sea, beach, planet, moon, earth, sky, stars, scenery, terrain, valley, "
    "water, monitor, bezel, computer, desk, room, wall, frame, border, window, text, letters, numbers, "
    "words, kanji, hanzi, caption, typography, glyph, logo, watermark, photorealistic, person, blur"
)

JOBS = [
    {
        "id": "lunar_panorama",
        "seeds": (73289, 73157, 73011),
        "request_size": "1536x1024",
        "output_size": PANORAMA_SIZE,
        "kind": "panorama",
        "prompt": LUNAR_PROMPT,
        "negative": LUNAR_NEGATIVE,
    },
    {
        "id": "bay_floor",
        "seeds": (74021, 74163, 74297),
        "request_size": "1024x1024",
        "output_size": FLOOR_SIZE,
        "kind": "floor",
        "prompt": FLOOR_PROMPT,
        "negative": FLOOR_NEGATIVE,
    },
    {
        "id": "bay_wall",
        "seeds": (75031, 75187, 75319),
        "request_size": "1024x1024",
        "output_size": WALL_SIZE,
        "kind": "wall",
        "prompt": WALL_PROMPT,
        "negative": WALL_NEGATIVE,
    },
    {
        "id": "console_screen",
        "seeds": (76337, 76193, 76041),
        "request_size": "1024x768",
        "output_size": CONSOLE_SIZE,
        "kind": "console",
        "prompt": CONSOLE_PROMPT,
        "negative": CONSOLE_NEGATIVE,
    },
]

_DEAD: set[str] = set()


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _redact(text: str) -> str:
    cleaned = re.sub(r"sk-[A-Za-z0-9_\-]{4,}", "sk-redacted", text)
    cleaned = re.sub(r"Bearer\s+\S+", "Bearer redacted", cleaned)
    return cleaned[:240]


def load_keys() -> None:
    """Load the SiliconFlow pool from studio_app/.env. Never print the values."""
    os.environ.setdefault("IMAGE_STRICT", "true")
    os.environ["IMAGE_MAX_RETRIES"] = "0"
    if os.environ.get("SILICONFLOW_API_KEYS") or os.environ.get("SILICONFLOW_API_KEY"):
        return
    if not ENV_FILE.is_file():
        raise SystemExit("找不到硅基流动密钥文件")
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        name = name.strip().removeprefix("export ").strip()
        if name not in ("SILICONFLOW_API_KEY", "SILICONFLOW_API_KEYS"):
            continue
        os.environ[name] = value.strip().strip('"').strip("'")


def _ready(path: Path, size: tuple[int, int]) -> bool:
    if not path.is_file() or path.stat().st_size <= 10_000:
        return False
    with Image.open(path) as image:
        return image.size == size


def _smoothstep(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, 0.0, 1.0)
    return clipped * clipped * (3.0 - 2.0 * clipped)


def _band_mask(length: int, blend: int) -> np.ndarray:
    """1 at both edges, falling to 0 `blend` pixels inward."""
    if blend <= 0 or length < 2:
        return np.zeros(length, dtype=np.float32)
    blend = min(int(blend), length // 2)
    dist = np.minimum(np.arange(length), length - 1 - np.arange(length)).astype(np.float32)
    return _smoothstep((blend - dist) / float(blend))


def make_seamless(rgb: np.ndarray, *, blend_x: int, blend_y: int) -> np.ndarray:
    """Cross-fade each edge with the opposite half so a tiled copy has no hard seam.

    Edge pixels become the image center, which already continues into the next
    center pixel. Interior pixels stay the original texture.
    """
    src = rgb.astype(np.float32)
    height, width = src.shape[:2]
    mask_x = _band_mask(width, blend_x).reshape(1, width, 1)
    mask_y = _band_mask(height, blend_y).reshape(height, 1, 1)
    roll_x = np.roll(src, width // 2, axis=1)
    roll_y = np.roll(src, height // 2, axis=0)
    roll_xy = np.roll(roll_x, height // 2, axis=0)
    mixed = (
        (1.0 - mask_x) * (1.0 - mask_y) * src
        + mask_x * (1.0 - mask_y) * roll_x
        + (1.0 - mask_x) * mask_y * roll_y
        + mask_x * mask_y * roll_xy
    )
    return np.clip(np.round(mixed), 0, 255).astype(np.uint8)


def _shift_mean(donor: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """Keep the donor's detail, but match the brightness of the neighboring strip."""
    shifted = donor.astype(np.float32)
    shifted += reference.astype(np.float32).mean(axis=(0, 1)) - shifted.mean(axis=(0, 1))
    return np.clip(shifted, 0, 255)


def _best_donor(rgb: np.ndarray, y0: int, x0: int, box_h: int, box_w: int) -> np.ndarray:
    """Copy a same-row patch from the left so a sky cannot be pasted onto the ground."""
    band = 10
    best_err: float | None = None
    best: np.ndarray | None = None
    left_ref = rgb[y0:y0 + box_h, max(0, x0 - band):x0]
    top_ref = rgb[max(0, y0 - band):y0, x0:x0 + box_w] if y0 >= band else None
    step = 4
    for dx in range(box_w, max(box_w, x0 - box_w) + 1, step):
        donor = rgb[y0:y0 + box_h, x0 - dx:x0 - dx + box_w]
        if donor.shape[0] != box_h or donor.shape[1] != box_w or left_ref.shape[1] != band:
            continue
        err = float(np.mean(np.abs(donor[:, :band].astype(np.float32) - left_ref.astype(np.float32))))
        if top_ref is not None and top_ref.shape[0] == band:
            err += float(np.mean(np.abs(donor[:band].astype(np.float32) - top_ref.astype(np.float32))))
        if best_err is None or err < best_err:
            best_err = err
            best = donor
    if best is None:
        src = rgb[y0:y0 + box_h, max(0, x0 - box_w):x0]
        if src.shape[1] != box_w:
            src = np.asarray(Image.fromarray(src).resize((box_w, box_h), Image.Resampling.BILINEAR))
        return src
    return best


def cover_watermark(rgb: np.ndarray) -> np.ndarray:
    """Paint out the bottom-right ``AI生成`` chip.

    The badge is a small fixed-size mark, so the patch stays about 240 by 110
    pixels instead of a slice of the frame. The replacement is taken from the
    same rows to the left, shifted to the local brightness, and feathered.
    """
    image = np.ascontiguousarray(rgb)
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("expected an RGB image")
    height, width = image.shape[:2]
    scale = float(np.clip(max(width, height) / 1024.0, 0.9, 1.2))
    box_w = int(np.clip(round(240 * scale), 160, min(300, max(160, width // 4))))
    box_h = int(np.clip(round(108 * scale), 72, min(140, max(72, height // 6))))
    feather = int(np.clip(round(32 * scale), 16, 48))
    y0, x0 = height - box_h, width - box_w
    donor = _best_donor(image, y0, x0, box_h, box_w)
    neighbor = image[y0:y0 + box_h, max(0, x0 - 28):x0]
    if neighbor.size:
        donor = _shift_mean(donor, neighbor)
    else:
        donor = donor.astype(np.float32)
    fade_y = _smoothstep(np.arange(box_h, dtype=np.float32) / float(feather))
    fade_x = _smoothstep(np.arange(box_w, dtype=np.float32) / float(feather))
    alpha = (fade_y[:, None] * fade_x[None, :])[:, :, None]
    out = image.copy()
    base = out[y0:, x0:].astype(np.float32)
    out[y0:, x0:] = np.clip(base * (1.0 - alpha) + donor * alpha, 0, 255).astype(np.uint8)
    return out


def _nudge_mean(rgb: np.ndarray, target: np.ndarray, strength: float = 0.42) -> np.ndarray:
    arr = rgb.astype(np.float32)
    delta = (target - arr.mean(axis=(0, 1))) * float(strength)
    return np.clip(arr + delta, 0, 255).astype(np.uint8)


def horizon_row(rgb: np.ndarray) -> int:
    """Row where the blue sky gives way to the grey surface, from the top."""
    image = rgb.astype(np.float32)
    red, green, blue = image[:, :, 0], image[:, :, 1], image[:, :, 2]
    margin = max(8, image.shape[1] // 10)
    score = (blue - np.maximum(red, green))[:, margin:-margin].mean(axis=1)
    kernel = 31
    smooth = np.convolve(score, np.ones(kernel, dtype=np.float32) / kernel, mode="same")
    top_n = max(8, len(smooth) // 6)
    top = float(np.median(smooth[:top_n]))
    bot = float(np.median(smooth[-top_n:]))
    if top - bot < 6.0:
        return int(round(len(smooth) * HORIZON_FRACTION))
    level = bot + 0.55 * (top - bot)
    y0 = int(len(smooth) * 0.12)
    y1 = int(len(smooth) * 0.90)
    below = np.where(smooth[y0:y1] < level)[0]
    if below.size == 0:
        return int(round(len(smooth) * HORIZON_FRACTION))
    return int(y0 + int(below[0]))


def frame_panorama(rgb: np.ndarray, horizon_frac: float = HORIZON_FRACTION) -> tuple[np.ndarray, dict]:
    """Fit a 1536×1024 plate, then crop to 1536×768 with the horizon low-middle."""
    image = Image.fromarray(np.ascontiguousarray(rgb), "RGB")
    if image.size != (1536, 1024):
        image = image.resize((1536, 1024), Image.Resampling.LANCZOS)
    plate = np.asarray(image)
    y_horizon = horizon_row(plate)
    out_h = PANORAMA_SIZE[1]
    top = int(round(y_horizon - horizon_frac * out_h))
    top = max(0, min(top, plate.shape[0] - out_h))
    cropped = plate[top:top + out_h]
    achieved = (y_horizon - top) / float(out_h)
    info = {
        "horizon_row": int(y_horizon),
        "crop_top": int(top),
        "horizon_fraction": round(float(achieved), 3),
    }
    return cropped, info


def frame_console(rgb: np.ndarray) -> np.ndarray:
    """Scale to 1024 wide and crop to 16:9 (1024×576)."""
    image = Image.fromarray(np.ascontiguousarray(rgb), "RGB")
    width, height = image.size
    scaled_h = max(1, int(round(height * (CONSOLE_SIZE[0] / float(width)))))
    image = image.resize((CONSOLE_SIZE[0], scaled_h), Image.Resampling.LANCZOS)
    if scaled_h == CONSOLE_SIZE[1]:
        return np.asarray(image)
    if scaled_h > CONSOLE_SIZE[1]:
        top = (scaled_h - CONSOLE_SIZE[1]) // 2
        return np.asarray(image)[top:top + CONSOLE_SIZE[1]]
    return np.asarray(image.resize(CONSOLE_SIZE, Image.Resampling.LANCZOS))


def axis_deviation(rgb: np.ndarray) -> float:
    """Median degrees by which strong edges miss horizontal or vertical.

    Near 0 means the panel lines are orthographic. A high score means the
    plate is tilted or in perspective.
    """
    gray = rgb.astype(np.float32).mean(axis=2)
    gx = np.zeros_like(gray)
    gy = np.zeros_like(gray)
    gx[:, 1:-1] = gray[:, 2:] - gray[:, :-2]
    gy[1:-1, :] = gray[2:, :] - gray[:-2, :]
    mag = np.hypot(gx, gy)
    strong = mag >= max(float(np.percentile(mag, 92)), 1.0)
    if int(strong.sum()) < 50:
        return 0.0
    angle = np.degrees(np.arctan2(gy[strong], gx[strong]))
    edge = (angle + 90.0) % 180.0
    dev = np.minimum(edge % 90.0, 90.0 - (edge % 90.0))
    return float(np.median(dev))


def seam_jump(rgb: np.ndarray) -> tuple[float, float]:
    """Mean edge-to-edge jump, and the mean jump between ordinary neighbors."""
    arr = rgb.astype(np.float32)
    horizontal = float(np.mean(np.abs(arr[:, 0] - arr[:, -1])))
    vertical = float(np.mean(np.abs(arr[0, :] - arr[-1, :])))
    natural = float(np.mean(np.abs(arr[:, 1:] - arr[:, :-1])))
    return max(horizontal, vertical), natural


def finish_texture(
    rgb: np.ndarray,
    kind: str,
    *,
    horizon_frac: float = HORIZON_FRACTION,
) -> tuple[np.ndarray, dict]:
    cleaned = cover_watermark(rgb)
    info: dict = {"kind": kind}
    if kind == "panorama":
        framed, info = frame_panorama(cleaned, horizon_frac)
        info["kind"] = kind
        return framed, info
    if kind == "console":
        return frame_console(cleaned), info
    side = FLOOR_SIZE[0]
    image = Image.fromarray(cleaned, "RGB")
    if image.size != (side, side):
        image = image.resize((side, side), Image.Resampling.LANCZOS)
    squared = np.asarray(image)
    if kind == "floor":
        squared = _nudge_mean(squared, FLOOR_MEAN)
        squared = make_seamless(squared, blend_x=80, blend_y=80)
    elif kind == "wall":
        squared = _nudge_mean(squared, WALL_MEAN)
        # Horizontal tile is required. A short vertical blend also clears the
        # dark trim Kolors paints on the top and bottom edges.
        squared = make_seamless(squared, blend_x=88, blend_y=64)
    else:
        raise ValueError(kind)
    info["axis_deviation_deg"] = round(axis_deviation(squared), 2)
    jump, natural = seam_jump(squared)
    info["seam_jump"] = round(jump, 3)
    info["neighbor_jump"] = round(natural, 3)
    return squared, info


def sanity_check() -> None:
    """Fail before any network call if framing, tiling, or watermark cover breaks."""
    for job in JOBS:
        if len(job["prompt"]) > 2000 or len(job["negative"]) > 500:
            raise SystemExit(
                f"{job['id']} prompt {len(job['prompt'])} / negative {len(job['negative'])} is too long"
            )
    vista = np.zeros((1024, 1536, 3), dtype=np.uint8)
    vista[:600] = (42, 86, 140)
    vista[600:] = (186, 188, 190)
    yy, xx = np.ogrid[:1024, :1536]
    vista[(xx - 380) ** 2 + (yy - 200) ** 2 < 110 ** 2] = (236, 242, 250)
    vista[-90:, -200:] = (245, 245, 245)
    framed, info = finish_texture(vista, "panorama")
    if framed.shape != (768, 1536, 3):
        raise SystemExit(f"panorama shape {framed.shape}")
    if not 0.55 <= info["horizon_fraction"] <= 0.66:
        raise SystemExit(f"horizon fraction {info['horizon_fraction']}")
    if int(framed[-8:, -8:].mean()) > 210:
        raise SystemExit("panorama watermark corner stayed near white")

    grid = np.zeros((1024, 1024, 3), dtype=np.uint8)
    grid[:] = (150, 162, 172)
    grid[:, ::96] = (110, 120, 130)
    grid[::96, :] = (110, 120, 130)
    grid[-110:, -220:] = (250, 250, 250)
    floor, floor_info = finish_texture(grid, "floor")
    if floor.shape != (1024, 1024, 3):
        raise SystemExit(f"floor shape {floor.shape}")
    if floor_info["seam_jump"] > floor_info["neighbor_jump"] + 8:
        raise SystemExit(f"floor seam {floor_info['seam_jump']} vs {floor_info['neighbor_jump']}")
    roll_x = np.roll(grid, grid.shape[1] // 2, axis=1)
    tiled = make_seamless(grid, blend_x=24, blend_y=0)
    if not np.array_equal(tiled[:, 0], roll_x[:, 0]):
        raise SystemExit("horizontal tile edge is not the wrapped center")
    if not np.array_equal(tiled[:, -1], roll_x[:, -1]):
        raise SystemExit("horizontal tile edge is not the wrapped center")
    wall, _wall_info = finish_texture(grid, "wall")
    if wall.shape != (1024, 1024, 3):
        raise SystemExit(f"wall shape {wall.shape}")
    screen = np.zeros((720, 1280, 3), dtype=np.uint8)
    screen[:] = (8, 20, 36)
    screen[-80:, -180:] = (230, 230, 230)
    console, _console_info = finish_texture(screen, "console")
    if console.shape != (576, 1024, 3):
        raise SystemExit(f"console shape {console.shape}")
    if int(console[-6:, -6:].max()) > 80:
        raise SystemExit("console watermark corner stayed bright")


def seed_for(job: dict, attempt: int, override: int | None) -> int:
    if override is not None:
        return int(override)
    seeds = job["seeds"]
    if attempt < 0 or attempt >= len(seeds):
        raise SystemExit(f"--attempt 必须是 0 到 {len(seeds) - 1}")
    return int(seeds[attempt])


def paint_kolors(prompt: str, negative: str, seed: int, size: str, dest: Path) -> None:
    from soundstorywriter.llm.image_client import ImageGenerationError
    from soundstorywriter.llm.openai_image_client import OpenAICompatImageClient

    probe = OpenAICompatImageClient(backend="siliconflow", model=MODEL)
    if not probe.available:
        raise SystemExit("没有可用的硅基流动密钥")
    keys = [key for key in probe.api_keys if key not in _DEAD]
    if not keys:
        raise SystemExit("没有可用的硅基流动密钥")
    last = "出图失败"
    dest.parent.mkdir(parents=True, exist_ok=True)
    for key in keys:
        client = OpenAICompatImageClient(backend="siliconflow", model=MODEL, api_key=key)
        try:
            result = client.generate(
                prompt, dest,
                negative_prompt=negative, size=size, force=True, seed=seed, timeout_s=180.0,
            )
        except ImageGenerationError as exc:
            last = _redact(str(exc))
            _DEAD.add(key)
            print("key skipped", flush=True)
            continue
        if result is not None and dest.is_file() and dest.stat().st_size > 10_000:
            return
        _DEAD.add(key)
        print("key skipped", flush=True)
    raise SystemExit(last)


def selected_jobs(only: list[str]) -> list[dict]:
    if not only:
        return JOBS
    wanted = set(only)
    chosen = [job for job in JOBS if job["id"] in wanted]
    if len(chosen) != len(wanted):
        known = ", ".join(job["id"] for job in JOBS)
        raise SystemExit(f"未知目标。可选 {known}")
    return chosen


def load_manifest() -> dict:
    if not MANIFEST.is_file():
        return {}
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def save_manifest(data: dict) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def generate(
    jobs: list[dict],
    *,
    force: bool,
    attempt: int,
    seed_override: int | None,
    dry_run: bool,
    reprocess: bool,
    horizon_frac: float,
) -> int:
    sanity_check()
    manifest = load_manifest()
    pool_announced = False
    for job in jobs:
        dest = OUT / f"{job['id']}.png"
        seed = seed_for(job, attempt, seed_override)
        if dry_run:
            print(
                f"would paint {job['id']} seed {seed} request {job['request_size']} "
                f"output {job['output_size'][0]}x{job['output_size'][1]} "
                f"prompt {len(job['prompt'])} negative {len(job['negative'])}",
                flush=True,
            )
            continue
        if _ready(dest, job["output_size"]) and not force and not reprocess:
            print(f"{job['id']} cached", flush=True)
            continue
        raw_path = RAW / f"{job['id']}.png"
        if reprocess and raw_path.is_file() and raw_path.stat().st_size > 10_000:
            previous = manifest.get(job["id"], {}).get("seed")
            if isinstance(previous, int) and seed_override is None:
                seed = int(previous)
            print(f"reprocess {job['id']}", flush=True)
        else:
            if reprocess and not force:
                raise SystemExit(f"没有可重处理的原图 {raw_path.name}")
            if not pool_announced:
                load_keys()
                sys.path.insert(0, str(STUDIO_SRC))
                from soundstorywriter.llm.openai_image_client import OpenAICompatImageClient

                probe = OpenAICompatImageClient(backend="siliconflow", model=MODEL)
                if not probe.available:
                    raise SystemExit("没有可用的硅基流动密钥")
                print(f"Kolors key pool: {len(probe.api_keys)}", flush=True)
                pool_announced = True
            print(f"paint {job['id']} seed {seed}", flush=True)
            paint_kolors(job["prompt"], job["negative"], seed, job["request_size"], raw_path)
        raw = np.asarray(Image.open(raw_path).convert("RGB"))
        finished, info = finish_texture(raw, job["kind"], horizon_frac=horizon_frac)
        if (finished.shape[1], finished.shape[0]) != job["output_size"]:
            raise SystemExit(f"{job['id']} came out {finished.shape[1]}x{finished.shape[0]}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(finished, "RGB").save(dest, "PNG")
        manifest[job["id"]] = {
            "file": dest.name,
            "seed": seed,
            "attempt": attempt if seed_override is None else None,
            "size": [int(finished.shape[1]), int(finished.shape[0])],
            "source_size": [int(raw.shape[1]), int(raw.shape[0])],
            "model": MODEL,
            **info,
        }
        save_manifest(manifest)
        extra = ""
        if "horizon_fraction" in info:
            extra = f" horizon {info['horizon_fraction']}"
        if "axis_deviation_deg" in info:
            extra = f" axis {info['axis_deviation_deg']}deg seam {info['seam_jump']}"
        print(
            f"saved {dest.name} {finished.shape[1]}x{finished.shape[0]} seed {seed}{extra}",
            flush=True,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    _utf8_stdio()
    parser = argparse.ArgumentParser(description="Generate lunar bay textures with Kolors.")
    parser.add_argument("--force", action="store_true", help="regenerate files that already exist")
    parser.add_argument(
        "--only", nargs="+", default=[],
        help="lunar_panorama, bay_floor, bay_wall, console_screen",
    )
    parser.add_argument("--attempt", type=int, default=0, help="seed attempt 0..2")
    parser.add_argument("--seed", type=int, default=None, help="override the Kolors seed")
    parser.add_argument("--dry-run", action="store_true", help="check the plan without calling Kolors")
    parser.add_argument(
        "--reprocess", action="store_true",
        help="rebuild finished PNGs from textures/raw without calling Kolors",
    )
    parser.add_argument(
        "--horizon", type=float, default=HORIZON_FRACTION,
        help="fraction of the panorama height where the horizon should sit",
    )
    args = parser.parse_args(argv)
    if not 0.2 <= args.horizon <= 0.8:
        raise SystemExit("--horizon 必须在 0.2 到 0.8 之间")
    return generate(
        selected_jobs(args.only),
        force=args.force,
        attempt=args.attempt,
        seed_override=args.seed,
        dry_run=args.dry_run,
        reprocess=args.reprocess,
        horizon_frac=args.horizon,
    )


if __name__ == "__main__":
    raise SystemExit(main())
