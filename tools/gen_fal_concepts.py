#!/usr/bin/env python3
"""Front, left, and back concept plates for the fal character meshes.

SiliconFlow Kolors (the configured IMAGE_MODEL, Kwai-Kolors/Kolors). This
script does not call fal.ai. Keys are loaded the same way as
tools/gen_bay_textures.py and are never printed.

    python tools/gen_fal_concepts.py
    python tools/gen_fal_concepts.py --only linlan --attempt 1 --force
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import gen_3d_refs as plates  # noqa: E402

OUT = ROOT / "media" / "assets3d" / "characters" / "concepts"
RAW = OUT / "raw"

SEED_TRIES = {
    "linlan": (51027, 51103, 53011, 54017),
    "chen": (62041, 62113, 63011, 64017),
}

FRAME = (
    "全身，从头顶到靴底都在画面里，人物高度约占画面八成，头顶上方留白，靴底靠近底部但不被切断。"
    "绝对不要半身像，不要只画到腰，不要只画头和肩膀。只有一个人。"
    "背景是均匀的纯白色，像白纸，没有地面，没有窗帘，没有墙壁，没有摄影棚，没有阴影。"
    "不要文字，不要水印，不要分格，不要多视图。"
)
POSE = (
    "两条手臂和身体之间能看见白色背景，肘在腰的外面，手在大腿外侧，大约成四十度，不要贴着身体，不要把手插进口袋，也不要平举。"
    "恰好两只手，两条腿，两只鞋。手里什么都没有。五指松开，不是拳头。"
)
STYLE = (
    "角色设定图，干净均匀的深色描边，全身都画完，纯白背景，正常头身比约七头身，线条干净，没有照片感。"
    "上色接近2004年游戏里的半写实模型，眼睛和真人一样大，低饱和的工业色。"
    "不是照片，不是棚拍，不是Q版大头，不是超级英雄，不是橙色塑料皮肤。"
)
VIEWS = {
    "front": "朝向：正面面对镜头，左右对称，两只眼睛一样大。不要侧面，不要背面。",
    "back": (
        "背面全身立绘。人物背对镜头，穿着宽松帆布工装夹克和工装长裤，短靴。"
        "林岚的夹克是灰橄榄色，裤子深灰。陈的夹克是深蓝灰色，裤子卡其灰。按人物使用对应颜色。"
        "夹克盖住整个后背和腰，不要露腰，不要背心，不要短裤。"
        "只能看见后脑勺的短发、后背的夹克、长裤和靴底。没有眼睛，没有脸。纯白色背景。只有一个人。双手在身体两侧。"
    ),
    "left": (
        "严格正侧面全身立绘，鼻子朝画面左边，只能看见一只眼睛。只有一个人，不要第二个人，不要正面。"
        "穿着宽松帆布工装夹克和工装长裤，短靴。林岚的夹克是灰橄榄色、裤子深灰、靴是棕色。陈的夹克是深蓝灰色、裤子卡其灰、靴是黑色。"
        "夹克盖住腰，不要露腰。手垂在大腿旁边，手指露出来，不要插兜。纯白色背景。从头顶到靴底。"
    ),
}
CLOTHES = (
    "上衣和下衣是分开的两件：宽松帆布工装夹克盖在裤腰外面，能看见夹克的平直下摆，不是连体工装，不是背带裤。"
    "夹克有衣领、袖口和口袋，布料不贴着胸口，不描出腹肌。笔直的工装长裤，裤管不贴腿。真实的短靴，能看见靴筒和鞋带。"
)
LINLAN = (
    "一个三十二岁左右的东亚女性全身立绘，普通身材，肩膀不宽，不是沙漏型，没有夸张的胸和臀。"
    "深棕色短发只到下巴，侧分，有几缕分开的层次，不是马尾，不是长发。"
    "灰橄榄色帆布夹克，深灰色长裤，棕色短靴。"
) + CLOTHES
CHEN = (
    "一个三十八岁左右的东亚男性全身立绘，现场工程师，看起来疲倦，普通头身比，头不要大，不是健美运动员。"
    "肩膀不比夹克更宽，没有胸肌，没有腹肌。"
    "黑色短发，两侧贴头，顶部短而平整，不是飞机头。"
    "褪色深蓝灰色帆布夹克，卡其灰色长裤，黑色工作靴。"
) + CLOTHES
BASE_NEGATIVE = (
    "photo, photograph, photorealistic, dslr, studio, curtain, bokeh, portrait, bust, close-up, "
    "cropped, waist-up, cartoon, chibi, toy, anime eyes, superhero, bodybuilder, muscular, abs, "
    "hourglass, tight suit, bodysuit, jumpsuit, coveralls, hands in pockets, fists, ponytail, "
    "quiff, long hair, orange skin, extra arm, extra leg, two people, text, watermark, shadow, ground"
)
VIEW_NEGATIVE = {
    "front": ", profile, back view, side view",
    "back": ", face, eyes, nose, mouth, front view, side view",
    "left": ", front view, back view, both eyes, three-quarter view",
}

JOBS = [
    {"id": "linlan_front", "group": "linlan", "view": "front"},
    {"id": "linlan_left", "group": "linlan", "view": "left"},
    {"id": "linlan_back", "group": "linlan", "view": "back"},
    {"id": "chen_front", "group": "chen", "view": "front"},
    {"id": "chen_left", "group": "chen", "view": "left"},
    {"id": "chen_back", "group": "chen", "view": "back"},
]


def prompt_for(job: dict) -> tuple[str, str, str]:
    identity = LINLAN if job["group"] == "linlan" else CHEN
    text = VIEWS[job["view"]] + POSE + STYLE + identity + FRAME
    negative = BASE_NEGATIVE + VIEW_NEGATIVE[job["view"]]
    if len(text) > 2000 or len(negative) > 500:
        raise SystemExit(f"{job['id']} prompt {len(text)} / negative {len(negative)} is too long")
    return text, negative, "1024x1536"


def finish_character(rgb: np.ndarray) -> np.ndarray:
    """Use the same white-plate cleanup as the existing 3D reference path."""
    return plates.finish_plate(rgb, kind="character")


def selected(only: list[str]) -> list[dict]:
    if not only:
        return JOBS
    wanted = set(only)
    chosen = [job for job in JOBS if job["id"] in wanted or job["group"] in wanted]
    if not chosen:
        known = ", ".join(job["id"] for job in JOBS)
        raise SystemExit(f"未知目标。可选 {known}，或 linlan / chen")
    return chosen


def generate(jobs: list[dict], *, force: bool, attempt: int, dry_run: bool) -> int:
    plates._utf8_stdio()
    announced = False
    for job in jobs:
        dest = OUT / f"{job['id']}.png"
        text, negative, size = prompt_for(job)
        tries = SEED_TRIES[job["group"]]
        if attempt < 0 or attempt >= len(tries):
            raise SystemExit(f"--attempt 必须是 0 到 {len(tries) - 1}")
        seed = int(tries[attempt])
        if dry_run:
            print(
                f"would paint {job['id']} seed {seed} size {size} "
                f"prompt {len(text)} negative {len(negative)}",
                flush=True,
            )
            continue
        if dest.is_file() and dest.stat().st_size > 10_000 and not force:
            print(f"{job['id']} cached", flush=True)
            continue
        if not announced:
            plates.load_keys()
            sys.path.insert(0, str(plates.STUDIO_SRC))
            from soundstorywriter.llm.openai_image_client import OpenAICompatImageClient

            probe = OpenAICompatImageClient(backend="siliconflow", model=plates.MODEL)
            if not probe.available:
                raise SystemExit("没有可用的硅基流动密钥")
            print(f"Kolors key pool: {len(probe.api_keys)} model {plates.MODEL}", flush=True)
            announced = True
        raw_path = RAW / f"{job['id']}.png"
        print(f"paint {job['id']} seed {seed}", flush=True)
        plates.paint_kolors(text, negative, seed, size, raw_path)
        finished = finish_character(plates.load_rgb(raw_path))
        dest.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(finished, "RGB").save(dest, "PNG")
        white = float(np.mean(np.all(finished >= 250, axis=2)))
        print(f"saved {dest} white_frac {white:.2f} size {finished.shape[1]}x{finished.shape[0]}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate fal character turnaround plates with Kolors.")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--only", nargs="+", default=[])
    parser.add_argument("--attempt", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    return generate(selected(args.only), force=args.force, attempt=args.attempt, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
