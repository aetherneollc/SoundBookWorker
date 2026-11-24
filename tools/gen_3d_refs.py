#!/usr/bin/env python3
"""White-background 3D reference plates for Linlan, Chen, the sensor, and Atuo.

SiliconFlow Kolors (Kwai-Kolors/Kolors) only. This script does not call fal.ai.
Keys are read from SILICONFLOW_API_KEY / SILICONFLOW_API_KEYS in
D:\\wordk\\动态漫\\SoundBookCF\\studio_app\\.env and are never printed.

A file that is already present is left alone unless --force. One character
uses one seed for every view. --attempt 0..3 picks the next shared seed.

    python tools/gen_3d_refs.py
    python tools/gen_3d_refs.py --only linlan --attempt 1 --force
    python tools/gen_3d_refs.py --dry-run
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
# Same path as ROVER in media/m6_free/build_episode.py.
ROVER_SRC = ROOT / "media" / "m4_e2e_episode" / "assets" / "at1_master.png"
OUT = ROOT / "media" / "assets3d" / "refs"
RAW = OUT / "raw"
MANIFEST = OUT / "manifest.json"
MODEL = "Kwai-Kolors/Kolors"
CANVAS = 1024
ROVER_PAD = 88

SEED_TRIES = {
    "linlan": (61002, 41087, 41153, 41219),
    "chen": (82011, 82087, 82153, 82219),
    "sensor": (94021, 93087, 93153, 93219),
}

STYLE = (
    "国产2D番剧风格扁平日系动画，干净均匀的深色描边，平涂赛璐璐上色，"
    "明亮柔和色彩，正常头身比约七头身，线条干净，没有照片感。"
)
POSE = (
    "两条手臂和身体之间能看见白色背景，肘在腰的外面，手在大腿外侧，大约成四十度，不要贴着身体，不要把手插进口袋，也不要平举。"
    "恰好两只手，两条腿，两只鞋。手里什么都没有。"
)
FRAME = (
    "全身，从头顶到鞋底都在画面里，头顶上方留少量空白，鞋底靠近画面底部但不被切断。"
    "只有一个人。背景是均匀的纯白色，像白纸，没有地面，没有阴影。"
    "不要文字，不要水印，不要分格，不要多视图。"
)
VIEWS = {
    "front": "朝向：正面面对镜头，左右对称，两只眼睛一样大。姿势：双脚分开站直。",
    "back": (
        "背影。纯白色背景，不要房间，不要地板，不要墙壁。"
        "镜头在人物身后。只能看见后脑勺和后背，没有眼睛，没有鼻子，没有嘴，没有侧脸。"
        "后背没有橙色方标。夹克扣上，双手离开身体。双脚分开站直。全身都在画面里。"
    ),
    "left": (
        "只画一个人的严格侧面，脸朝画面左边，只能看见一只眼睛。不要正面，不要背面，不要两个人。"
        "纯白色背景，不要房间。全身，从头顶到靴底。双手离开身体，不要插兜。"
    ),
}
LINLAN = (
    "一个年轻东亚女性全身立绘，脑后一条又长又黑的马尾，马尾从后脑垂到腰，只有一条马尾。"
    "上身是扣上的蓝色短夹克，领圈是两指宽的橙色，橙色只在这圈领子上，胸口是蓝色布。"
    "下身是深灰色长裤，不是背带裤，不是短裤。棕色短靴，双手不戴手套。"
)
CHEN = (
    "一个成年东亚男性全身立绘，短黑发，正常成人比例，不是小孩。"
    "上身是扣上的蓝色短夹克，左胸只有一小块橙色方标，胸口其他地方和袖子都是蓝色，后背没有方标。"
    "下身是深灰色长裤，不是背带裤，不是短裤。黑色短靴，双手不戴手套。"
)
SENSOR = (
    "纯白色背景正中，一件小巧的手持科学仪器，四分之三角度，四周留白，物体完整不被切断。"
    "外壳以白色为主，只有边角是鲜橙色。下方是完整的短握把。正面一块没有文字的青色发光屏幕。顶部一根细短的天线。"
    "它不是手枪，没有扳机。没有手，没有人，没有地面，没有阴影，没有文字，没有水印。"
)
BASE_NEGATIVE = (
    "photorealistic, photo, 3d, cgi, portrait, bust, close-up, cropped, chibi, child, "
    "sword, weapon, gun, holding, gloves, shorts, overalls, dungarees, bare legs, white shirt, orange shirt, "
    "extra arm, extra leg, extra finger, hands in pockets, three-quarter view, "
    "shadow, ground, text, watermark, logo, two people, collage, multiple views"
)
VIEW_NEGATIVE = {
    "front": ", profile, back view, side view, looking away",
    "back": ", face, eyes, nose, mouth, front view, side view",
    "left": ", front view, back view, both eyes, looking at camera",
    "product": ", hand, fingers, person, human, gun, rifle, phone, weapon",
}

JOBS = [
    {"id": "linlan_front", "group": "linlan", "view": "front", "kind": "character"},
    {"id": "linlan_back", "group": "linlan", "view": "back", "kind": "character"},
    {"id": "linlan_left", "group": "linlan", "view": "left", "kind": "character"},
    {"id": "chen_front", "group": "chen", "view": "front", "kind": "character"},
    {"id": "chen_back", "group": "chen", "view": "back", "kind": "character"},
    {"id": "chen_left", "group": "chen", "view": "left", "kind": "character"},
    {"id": "sensor", "group": "sensor", "view": "product", "kind": "sensor"},
    {"id": "atuo", "group": "atuo", "view": "side", "kind": "rover"},
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


def _ready(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 10_000


def _cover_corner(
    rgb: np.ndarray,
    *,
    green: bool,
    box_w: int | None = None,
    box_h: int | None = None,
) -> np.ndarray:
    """Paint out the provider's bottom-right mark.

    Same method as manju/kolors_board.py. A flat backdrop is filled with the
    corner colour. Anything else stretches the patch above the mark. The box
    is larger than that helper's 240 by 72 because these plates carry a pale
    ``AI生成`` chip about 250 by 100 pixels.
    """
    rgb = np.array(rgb, copy=True)
    height, width = rgb.shape[:2]
    if box_w is None:
        box_w = min(340, width // 2)
    if box_h is None:
        box_h = min(150, height // 6)
    box_w = max(1, min(int(box_w), width))
    box_h = max(1, min(int(box_h), height // 2))
    if green:
        rgb[-box_h:, -box_w:] = rgb[min(8, height - 1), min(8, width - 1)]
        return rgb
    patch = rgb[max(0, height - box_h * 2): height - box_h, width - box_w:]
    if patch.size == 0:
        return rgb
    rgb[-box_h:, -box_w:] = np.array(
        Image.fromarray(patch).resize((box_w, box_h), Image.Resampling.BILINEAR)
    )
    return rgb


def cover_mark(rgb: np.ndarray) -> np.ndarray:
    """Paint out the bottom-right ``AI生成`` chip the way ``_cover_corner`` does.

    On a white plate the fill is solid white, which is the flat-backdrop branch.
    Boots or sleeves inside the box are copied back.
    """
    height, width = rgb.shape[:2]
    box_w = min(340, width // 2)
    box_h = min(150, height // 6)
    corner = rgb[-box_h:, -box_w:]
    channels = corner.astype(np.int16)
    chroma = channels.max(axis=2) - channels.min(axis=2)
    subject = (chroma >= 22) | (corner.max(axis=2) <= 90)
    top = rgb[:12, :12]
    pale_mark = (chroma <= 16) & (corner.max(axis=2) >= 150)
    if float(np.median(top)) >= 248 and float(subject.mean()) < 0.12:
        painted = np.array(rgb, copy=True)
        painted[-box_h:, -box_w:][~subject] = 255
        return painted
    if float(subject.mean()) >= 0.12:
        painted = np.array(rgb, copy=True)
        painted[-box_h:, -box_w:][pale_mark] = 255
        return painted
    above = rgb[max(0, height - box_h * 2): height - box_h, width - box_w:]
    ref = rgb[min(8, height - 1), min(8, width - 1)].astype(np.int16)
    if above.size == 0:
        flat = True
    else:
        median = np.median(above.reshape(-1, 3), axis=0).astype(np.int16)
        flat = int(np.max(np.abs(median - ref))) <= 18
    painted = _cover_corner(rgb, green=flat, box_w=box_w, box_h=box_h)
    if float(subject.mean()) <= 0.03:
        return painted
    restored = painted.copy()
    restored[-box_h:, -box_w:][subject] = corner[subject]
    return restored


def whiten_backdrop(rgb: np.ndarray, *, min_channel: int, max_chroma: int = 18) -> np.ndarray:
    """Flood a light neutral backdrop, from the borders, to pure white."""
    import cv2

    out = np.array(rgb, copy=True)
    channels = out.astype(np.int16)
    chroma = channels.max(axis=2) - channels.min(axis=2)
    candidate = (out.min(axis=2) >= min_channel) & (chroma <= max_chroma)
    count, labels = cv2.connectedComponents(candidate.astype(np.uint8), connectivity=4)
    if count <= 1:
        return out
    edge = np.unique(np.concatenate([
        labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1],
    ]))
    edge = edge[edge != 0]
    if edge.size == 0:
        return out
    out[np.isin(labels, edge)] = 255
    return out


def center_subject(rgb: np.ndarray, *, fill: float = 0.86) -> np.ndarray:
    """Center a full-body figure on a fresh white canvas when it is fully in frame."""
    import cv2

    mask = rgb.min(axis=2) < 248
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    if count <= 1:
        return rgb
    areas = stats[1:, cv2.CC_STAT_AREA]
    main = int(areas.max())
    keep = [
        index for index in range(1, count)
        if int(stats[index, cv2.CC_STAT_AREA]) >= max(40, int(main * 0.015))
    ]
    chosen = np.isin(labels, keep)
    ys, xs = np.where(chosen)
    if len(xs) == 0:
        return rgb
    top, bottom = int(ys.min()), int(ys.max())
    left, right = int(xs.min()), int(xs.max())
    height, width = rgb.shape[:2]
    if top <= 1 or bottom >= height - 2:
        return rgb
    pad = 2
    top = max(0, top - pad)
    bottom = min(height - 1, bottom + pad)
    left = max(0, left - pad)
    right = min(width - 1, right + pad)
    crop = rgb[top:bottom + 1, left:right + 1]
    crop_h, crop_w = crop.shape[:2]
    scale = 1.0
    if crop_h < height * (fill - 0.03):
        scale = min(1.35, (height * fill) / crop_h)
    new_w = max(1, int(round(crop_w * scale)))
    new_h = max(1, int(round(crop_h * scale)))
    limit = min((width - 8) / new_w, (height - 8) / new_h, 1.0)
    if limit < 1.0:
        new_w = max(1, int(round(new_w * limit)))
        new_h = max(1, int(round(new_h * limit)))
    if (new_w, new_h) != (crop_w, crop_h):
        crop = np.array(Image.fromarray(crop, "RGB").resize((new_w, new_h), Image.Resampling.LANCZOS))
    canvas = np.full((height, width, 3), 255, dtype=np.uint8)
    x = (width - crop.shape[1]) // 2
    y = (height - crop.shape[0]) // 2
    canvas[y:y + crop.shape[0], x:x + crop.shape[1]] = crop
    return canvas


def load_rgb(path: Path) -> np.ndarray:
    image = Image.open(path)
    if image.mode == "RGBA":
        flat = Image.new("RGB", image.size, (255, 255, 255))
        flat.paste(image, mask=image.getchannel("A"))
        return np.asarray(flat)
    return np.asarray(image.convert("RGB"))


def flatten_light_field(rgb: np.ndarray) -> np.ndarray:
    """Turn a light seamless backdrop into pure white without eating the subject.

    Flooding stops at a hard edge, so a white instrument shell stays put.
    """
    import cv2

    out = np.array(rgb, copy=True)
    border = np.concatenate([out[0], out[-1], out[:, 0], out[:, -1]], axis=0)
    median = np.median(border, axis=0)
    if float(median.min()) < 200 or float(median.max() - median.min()) > 40:
        return out
    gray = out.astype(np.float32).mean(axis=2)
    magnitude = cv2.magnitude(
        cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3),
        cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3),
    )
    channels = out.astype(np.int16)
    chroma = channels.max(axis=2) - channels.min(axis=2)
    distance = np.abs(channels - median.astype(np.int16)).max(axis=2)
    # Neutral pixels only, so skin and the orange collar are not eaten.
    candidate = (distance <= 72) & (magnitude <= 26) & (chroma <= 16)
    count, labels = cv2.connectedComponents(candidate.astype(np.uint8), connectivity=4)
    if count <= 1:
        return out
    edge = np.unique(np.concatenate([
        labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1],
    ]))
    edge = edge[edge != 0]
    if edge.size == 0:
        return out
    out[np.isin(labels, edge)] = 255
    return out


def clear_pale_backdrop(rgb: np.ndarray) -> np.ndarray:
    """Drop pale studio smears, a blue ground shadow, and a light provider mark.

    Skin (redder than blue) and saturated clothes stay. The ground pass only
    looks at the bottom of the frame, so a blue jacket is left alone.
    """
    out = np.array(rgb, copy=True)
    channels = out.astype(np.int16)
    chroma = channels.max(axis=2) - channels.min(axis=2)
    red = channels[:, :, 0]
    blue = channels[:, :, 2]
    skin = red > blue + 12
    pale = (chroma <= 46) & (channels.min(axis=2) >= 168) & (~skin)
    out[pale] = 255
    height = out.shape[0]
    ground = np.zeros(chroma.shape, dtype=bool)
    ground[int(height * 0.84):] = True
    shadow = ground & (~skin) & (blue > red) & (chroma <= 70) & (channels.min(axis=2) >= 130)
    out[shadow] = 255
    return out


def finish_plate(rgb: np.ndarray, *, kind: str) -> np.ndarray:
    cleaned = clear_pale_backdrop(rgb)
    cleaned = flatten_light_field(cleaned)
    cleaned = whiten_backdrop(cleaned, min_channel=232 if kind == "sensor" else 214)
    cleaned = cover_mark(cleaned)
    if kind == "character":
        cleaned = center_subject(cleaned)
        cleaned = cover_mark(cleaned)
    return cleaned


def prompt_for(job: dict) -> tuple[str, str, str]:
    view = job["view"]
    if job["kind"] == "sensor":
        text = SENSOR
        negative = BASE_NEGATIVE + VIEW_NEGATIVE["product"]
        size = "1024x1024"
    else:
        identity = LINLAN if job["group"] == "linlan" else CHEN
        text = VIEWS[view] + POSE + STYLE + identity + FRAME
        negative = BASE_NEGATIVE + VIEW_NEGATIVE[view]
        size = "768x1024"
    if len(text) > 2000 or len(negative) > 500:
        raise SystemExit(f"{job['id']} prompt {len(text)} / negative {len(negative)} is too long")
    return text, negative, size


def seed_for(group: str, attempt: int, override: int | None) -> int:
    if override is not None:
        return int(override)
    tries = SEED_TRIES[group]
    if attempt < 0 or attempt >= len(tries):
        raise SystemExit(f"--attempt 必须是 0 到 {len(tries) - 1}")
    return int(tries[attempt])


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
        if result is not None and _ready(dest):
            return
        _DEAD.add(key)
        print("key skipped", flush=True)
    raise SystemExit(last)


def exterior_backdrop(rgb: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    import cv2

    backdrop = (rgb.max(axis=2) <= 12) | (alpha <= 16)
    count, labels = cv2.connectedComponents(backdrop.astype(np.uint8), connectivity=4)
    if count <= 1:
        return backdrop
    edge = np.unique(np.concatenate([
        labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1],
    ]))
    edge = edge[edge != 0]
    if edge.size == 0:
        return np.zeros(backdrop.shape, dtype=bool)
    return np.isin(labels, edge)


def prepare_rover(src: Path, dest: Path) -> dict:
    """Center the existing AT-1 plate on a white 1024 canvas."""
    import cv2

    if not src.is_file():
        raise SystemExit(f"找不到阿拓原图 {src}")
    image = Image.open(src).convert("RGBA")
    arr = np.asarray(image)
    rgb = arr[:, :, :3]
    alpha = arr[:, :, 3]
    color = (rgb.max(axis=2) > 12) & (alpha > 16)
    ring = cv2.dilate(color.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1).astype(bool)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ring.astype(np.uint8), 8)
    if count > 1:
        areas = stats[1:, cv2.CC_STAT_AREA]
        main = int(areas.max())
        keep = [
            index for index in range(1, count)
            if int(stats[index, cv2.CC_STAT_AREA]) >= max(80, int(main * 0.02))
        ]
        ring = np.isin(labels, keep)
    ys, xs = np.where(ring)
    if len(xs) == 0:
        raise SystemExit("阿拓原图没有可用像素")
    top, bottom = int(ys.min()), int(ys.max())
    left, right = int(xs.min()), int(xs.max())
    touches = top <= 1 or left <= 1 or bottom >= arr.shape[0] - 2 or right >= arr.shape[1] - 2
    crop = rgb[top:bottom + 1, left:right + 1]
    mask = ring[top:bottom + 1, left:right + 1]
    crop_h, crop_w = mask.shape
    avail = CANVAS - ROVER_PAD * 2
    scale = min(avail / crop_w, avail / crop_h)
    new_w = max(1, int(round(crop_w * scale)))
    new_h = max(1, int(round(crop_h * scale)))
    resized = np.asarray(Image.fromarray(crop, "RGB").resize((new_w, new_h), Image.Resampling.LANCZOS))
    resized_mask = np.asarray(
        Image.fromarray((mask.astype(np.uint8) * 255), "L").resize((new_w, new_h), Image.Resampling.LANCZOS)
    )
    plate = Image.new("RGB", (CANVAS, CANVAS), (255, 255, 255))
    sprite = Image.fromarray(resized, "RGB")
    plate.paste(sprite, ((CANVAS - new_w) // 2, (CANVAS - new_h) // 2), Image.fromarray(resized_mask, "L"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    plate.save(dest, "PNG")
    white_frac = float(np.count_nonzero((crop.max(axis=2) >= 245) & mask)) / max(1, int(mask.sum()))
    notes = [
        "side elevation copied from the existing AT-1 master, centered on white",
    ]
    if touches:
        notes.append("source content touches the master edge, so the drawing is cropped")
    if scale > 1.15:
        notes.append(f"upscaled {scale:.2f}x")
    if crop_w / max(crop_h, 1) > 1.45:
        notes.append(
            "single side view: roof, far side, and front width are not shown, so TRELLIS has to invent them"
        )
    if white_frac > 0.2:
        notes.append(
            f"{white_frac:.0%} of the rover is near-white and relies on the orange band, wheels, and dark rim"
        )
    kept_dark = int(np.count_nonzero((crop.max(axis=2) <= 12) & mask))
    return {
        "file": dest.name,
        "seed": None,
        "source": str(src),
        "source_size": [image.width, image.height],
        "content_size": [crop_w, crop_h],
        "canvas": [CANVAS, CANVAS],
        "scale": round(float(scale), 3),
        "touches_source_edge": bool(touches),
        "near_white_fraction": round(white_frac, 3),
        "kept_dark_pixels": kept_dark,
        "note": "; ".join(notes),
    }


def selected_jobs(only: list[str]) -> list[dict]:
    if not only:
        return JOBS
    wanted = set(only)
    chosen = [job for job in JOBS if job["id"] in wanted or job["group"] in wanted]
    if not chosen:
        known = ", ".join(job["id"] for job in JOBS)
        raise SystemExit(f"未知目标。可选 {known}，或 linlan / chen / sensor / atuo")
    return chosen


def load_manifest() -> dict:
    if not MANIFEST.is_file():
        return {}
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def save_manifest(data: dict) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def generate(jobs: list[dict], *, force: bool, attempt: int, seed_override: int | None, dry_run: bool) -> int:
    manifest = load_manifest()
    pool_announced = False
    for job in jobs:
        dest = OUT / f"{job['id']}.png"
        if job["kind"] == "rover":
            if _ready(dest) and not force:
                print(f"{job['id']} cached", flush=True)
                continue
            if dry_run:
                print(f"would prepare {dest.name} from {ROVER_SRC}", flush=True)
                continue
            info = prepare_rover(ROVER_SRC, dest)
            manifest[job["id"]] = info
            save_manifest(manifest)
            print(
                f"{job['id']} source {info['source_size'][0]}x{info['source_size'][1]} "
                f"content {info['content_size'][0]}x{info['content_size'][1]} "
                f"scale {info['scale']} -> {dest}",
                flush=True,
            )
            print(f"note {info['note']}", flush=True)
            continue

        seed = seed_for(job["group"], attempt, seed_override)
        text, negative, size = prompt_for(job)
        if dry_run:
            print(
                f"would paint {job['id']} seed {seed} size {size} "
                f"prompt {len(text)} negative {len(negative)}",
                flush=True,
            )
            continue
        if _ready(dest) and not force:
            print(f"{job['id']} cached", flush=True)
            continue
        if not pool_announced:
            load_keys()
            sys.path.insert(0, str(STUDIO_SRC))
            from soundstorywriter.llm.openai_image_client import OpenAICompatImageClient

            probe = OpenAICompatImageClient(backend="siliconflow", model=MODEL)
            if not probe.available:
                raise SystemExit("没有可用的硅基流动密钥")
            print(f"Kolors key pool: {len(probe.api_keys)}", flush=True)
            pool_announced = True
        raw_path = RAW / f"{job['id']}.png"
        print(f"paint {job['id']} seed {seed}", flush=True)
        paint_kolors(text, negative, seed, size, raw_path)
        finished = finish_plate(load_rgb(raw_path), kind=job["kind"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(finished, "RGB").save(dest, "PNG")
        manifest[job["id"]] = {
            "file": dest.name,
            "seed": seed,
            "attempt": attempt if seed_override is None else None,
            "size": [int(finished.shape[1]), int(finished.shape[0])],
            "model": MODEL,
        }
        save_manifest(manifest)
        print(f"saved {dest}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    _utf8_stdio()
    parser = argparse.ArgumentParser(description="Generate white-background 3D reference plates with Kolors.")
    parser.add_argument("--force", action="store_true", help="regenerate files that already exist")
    parser.add_argument("--only", nargs="+", default=[], help="job id or group: linlan, chen, sensor, atuo")
    parser.add_argument("--attempt", type=int, default=0, help="shared seed attempt 0..3")
    parser.add_argument("--seed", type=int, default=None, help="override the seed for every Kolors image in this run")
    parser.add_argument("--dry-run", action="store_true", help="print the plan without calling Kolors")
    args = parser.parse_args(argv)
    jobs = selected_jobs(args.only)
    return generate(
        jobs,
        force=args.force,
        attempt=args.attempt,
        seed_override=args.seed,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
