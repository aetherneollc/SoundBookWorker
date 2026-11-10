#!/usr/bin/env python3
"""Build the 阿拓 library, pass the bench stills, then render episode 1.

The studio API must already be running at http://127.0.0.1:8787.
Keys are read from the gitignored studio env and are never printed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

WORKER = Path(__file__).resolve().parents[1]
CF = Path(r"D:\wordk\动态漫\SoundBookCF")
ENV_FILE = CF / "studio_app" / ".env"
DEV_VARS = CF / ".dev.vars"
OUT = WORKER / "manju" / "atuo"
PUBLIC = WORKER / "public" / "media" / "atuo"
STATE_PATH = OUT / "state.json"
API = "http://127.0.0.1:8787"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PASSWORD = "atuo-local-gate"
ENGINEER = "周宁"
ATUO = "阿拓 AT-1"
LOCATION = "舱内作业台"

EXPRESSIONS = ("neutral", "talk", "smile", "angry", "sad", "shock")
ATUO_LOOK = (
    "二十多岁的东亚男性月球舱内技术员，短而整齐的黑发，健康的浅暖肤色，明亮的棕色眼睛，没有黑眼圈。"
    "浅灰蓝色舱内工作夹克，里面是暖白色圆领衫。胸部以上正面半身，肩膀清楚。"
    "明亮平涂的动画人物，不是金属机器人外壳，不是写实照片。"
)
ENGINEER_LOOK = (
    "三十岁左右的东亚女性地面任务工程师，黑色低马尾，健康肤色，明亮的眼睛，没有黑眼圈。"
    "暖橙色地面任务外套，里面是白色衬衫。胸部以上正面半身。"
    "和浅灰蓝夹克的男性技术员不是同一个人。明亮平涂，不是写实照片。"
)
CABIN = (
    "空的月球舱内作业台，没有人，没有人影，没有脸。横构图，明亮平涂动画背景。"
    "画面下半是横贯画面的金属作业台，台面上有一盏暖色工作灯和几件小型设备，台面有一条清楚的水平边缘。"
    "上半是浅色舱壁和一扇观察窗，窗玻璃在远处，窗外是月球表面。窗必须留在人会站的位置后面。"
)


def write_dev_vars() -> None:
    keys = ""
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("SILICONFLOW_API_KEYS="):
            keys = line.split("=", 1)[1].strip().strip('"').strip("'")
    if len(keys) < 20:
        raise SystemExit("SILICONFLOW_API_KEYS is missing from the studio env")
    DEV_VARS.write_text(
        "\n".join([
            f"CONTROL_PASSWORD={PASSWORD}",
            "SSW_SESSION_SECRET=atuo-session-secret-32-bytes-min",
            "CALLBACK_TOKEN=atuo-callback",
            "GITHUB_TOKEN=atuo-local",
            f"SILICONFLOW_API_KEYS={keys}",
            "",
        ]),
        encoding="utf-8",
    )
    print(f"local worker env ready, key count {keys.count(',') + keys.count(';') + 1}", flush=True)


class Client:
    def __init__(self) -> None:
        self.token = ""

    def request(self, method: str, path: str, body: dict | None = None, timeout: int = 180, raw: bytes | None = None, content_type: str = ""):
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        data = None
        if raw is not None:
            data = raw
            headers["Content-Type"] = content_type
        elif body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(API + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read()
                content = response.headers.get("Content-Type", "")
                if "json" in content or path.endswith("/library") or "/generate" in path or path.endswith("/script") or path.endswith("/outline"):
                    return json.loads(payload.decode("utf-8"))
                return payload
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            try:
                message = json.loads(detail).get("error") or detail
            except json.JSONDecodeError:
                message = detail
            raise RuntimeError(f"{method} {path} -> {error.code}: {message[:400]}") from None

    def login(self) -> None:
        result = self.request("POST", "/api/auth/login", {"password": PASSWORD}, timeout=30)
        self.token = result["token"]


def load_state() -> dict:
    if STATE_PATH.is_file():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_story(client: Client, state: dict) -> None:
    stories = client.request("GET", "/api/stories", timeout=30)["stories"]
    serials = [story for story in stories if (story.get("outline") or {}).get("_scientific_serial") is True]
    chosen = next((story for story in serials if "阿拓" in str(story.get("title") or "")), serials[0] if serials else None)
    if chosen is None:
        created = client.request("POST", "/api/stories", {
            "universe_id": "default",
            "title": "月球上的阿拓",
            "premise": "近未来月球居住舱里，舱内技术员阿拓 AT-1 要在有限电力和月尘条件下保住一条机械臂的密封。地面工程师周宁通过延迟链路核对每一步。不能用月壤直接造芯片，也不能忽略真空、辐射和热循环。",
            "style": "明亮平涂的日系动画，月球舱硬科幻，柔和正面光，不要恐怖片和黑色电影。",
            "planned_episodes": 12,
        }, timeout=30)
        client.request("PATCH", f"/api/stories/{created['id']}", {"outline": {
            "_scientific_serial": True, "_format": "story", "_language": "zh",
            "series_summary": "阿拓 AT-1 在月球舱内逐项修复居住舱设备，周宁在地面核对代价和证据。",
            "world_rules": "真空、月尘、辐射、热循环、能源和通信延迟都要计入。新能力必须有材料、工具、能源和测试。",
        }}, timeout=30)
        chosen = client.request("GET", f"/api/stories/{created['id']}", timeout=30)
        print("created scientific serial", flush=True)
    else:
        print(f"using story {chosen.get('title')}", flush=True)
    state["story_id"] = chosen["id"]
    cast = client.request("GET", f"/api/stories/{chosen['id']}/characters", timeout=30)["characters"]
    by_name = {person["name"]: person for person in cast}
    if ATUO not in by_name:
        by_name[ATUO] = client.request("POST", f"/api/stories/{chosen['id']}/characters", {
            "name": ATUO, "role": "月球舱内技术员", "appearance": ATUO_LOOK, "voice": "longcheng",
        }, timeout=30)
    if ENGINEER not in by_name:
        by_name[ENGINEER] = client.request("POST", f"/api/stories/{chosen['id']}/characters", {
            "name": ENGINEER, "role": "地面任务工程师", "appearance": ENGINEER_LOOK, "voice": "longxiaochun",
        }, timeout=30)
    state["atuo_id"] = by_name[ATUO]["id"]
    state["engineer_id"] = by_name[ENGINEER]["id"]
    state["engineer_name"] = ENGINEER
    save_state(state)
    print(f"cast locked: {ATUO} / {ENGINEER}", flush=True)


def fill_library(client: Client, state: dict) -> None:
    story = state["story_id"]
    for owner, label in ((state["atuo_id"], ATUO), (state["engineer_id"], ENGINEER)):
        for slot in EXPRESSIONS:
            library = client.request("GET", f"/api/stories/{story}/library", timeout=30)
            person = next(item for item in library["characters"] if item["id"] == owner)
            current = (person.get("slots") or {}).get(slot) or {}
            if current.get("status") == "ready":
                print(f"skip {label} {slot}", flush=True)
                continue
            print(f"generate {label} {slot}", flush=True)
            result = client.request("POST", f"/api/stories/{story}/library/characters/{owner}/generate", {"slot": slot}, timeout=200)
            print(f"  generated {result.get('generated')} skipped {result.get('skipped')} {result.get('error') or ''}", flush=True)
        library = client.request("GET", f"/api/stories/{story}/library", timeout=30)
        person = next(item for item in library["characters"] if item["id"] == owner)
        delta = (person.get("slots") or {}).get("talk_delta") or {}
        if delta.get("status") != "ready":
            print(f"mouth delta {label}", flush=True)
            client.request("POST", f"/api/stories/{story}/library/characters/{owner}/generate", {"slot": "talk_delta"}, timeout=120)
    library = client.request("GET", f"/api/stories/{story}/library", timeout=30)
    places = library.get("locations") or []
    place = next((item for item in places if item.get("name") == LOCATION), places[0] if places else None)
    plate_ready = place and (place.get("slots") or {}).get("plate", {}).get("status") == "ready"
    if not plate_ready:
        print("generate cabin plate", flush=True)
        place = client.request("POST", f"/api/stories/{story}/library/locations", {
            "name": LOCATION, "prompt": CABIN, "id": place["id"] if place else None,
        }, timeout=220)
        state["location_id"] = place["id"]
    else:
        state["location_id"] = place["id"]
        near = (place.get("slots") or {}).get("near") or {}
        if near.get("status") != "ready":
            print("split cabin layers", flush=True)
            client.request("POST", f"/api/stories/{story}/library/locations", {
                "id": place["id"], "name": LOCATION, "slot": "near",
            }, timeout=120)
    save_state(state)


def download_library(client: Client, state: dict) -> dict[str, Path]:
    library = client.request("GET", f"/api/stories/{state['story_id']}/library", timeout=30)
    OUT.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}
    owners = {
        state["atuo_id"]: "atuo",
        state["engineer_id"]: "engineer",
        state["location_id"]: "cabin",
    }
    groups = [("characters", EXPRESSIONS + ("talk_delta",)), ("locations", ("plate", "far", "near"))]
    for group, slots in groups:
        for item in library[group]:
            prefix = owners.get(item["id"])
            if not prefix:
                continue
            for slot in slots:
                cell = (item.get("slots") or {}).get(slot) or {}
                if cell.get("status") != "ready":
                    raise RuntimeError(f"missing {prefix} {slot}")
                target = OUT / f"{prefix}-{slot}.png"
                if not target.is_file() or target.stat().st_size < 1000:
                    payload = client.request("GET", f"/api/stories/{state['story_id']}/library/assets/{cell['id']}/content", timeout=60)
                    target.write_bytes(payload)
                files[f"{prefix}-{slot}"] = target
    return files


def upload_slot(client: Client, state: dict, kind: str, owner: str, slot: str, path: Path) -> None:
    client.request(
        "POST", f"/api/stories/{state['story_id']}/library/{kind}/{owner}/slots/{slot}",
        timeout=60, raw=path.read_bytes(), content_type="image/png",
    )


def repair_layers(client: Client, state: dict, files: dict[str, Path], sill: int | None) -> dict[str, Path]:
    sys.path.insert(0, str(WORKER / "manju"))
    from split_depth import split_room

    split_dir = OUT / "depth"
    split_room(files["cabin-plate"], split_dir, sill)
    far = split_dir / "room-far.png"
    near = split_dir / "desk-near.png"
    upload_slot(client, state, "location", state["location_id"], "far", far)
    upload_slot(client, state, "location", state["location_id"], "near", near)
    files["cabin-far"] = OUT / "cabin-far.png"
    files["cabin-near"] = OUT / "cabin-near.png"
    shutil.copyfile(far, files["cabin-far"])
    shutil.copyfile(near, files["cabin-near"])
    return files


def stage_props(files: dict[str, Path], beats: list[dict], title: str = "") -> Path:
    PUBLIC.mkdir(parents=True, exist_ok=True)
    copied = {}
    for key, path in files.items():
        target = PUBLIC / path.name
        if path.resolve() != target.resolve():
            shutil.copyfile(path, target)
        copied[key] = f"media/atuo/{target.name}"
    shots = []
    frame = 0
    for index, beat in enumerate(beats):
        who = beat.get("who", "atuo")
        duration = int(round(float(beat.get("duration", 1)) * 24))
        cells = {}
        for slot in EXPRESSIONS + ("talk_delta",):
            name = f"{who}-{slot}"
            if name in copied:
                cells[slot] = copied[name]
        shots.append({
            "src": copied["cabin-far"],
            "from": frame,
            "duration": duration,
            "index": index,
            "stage": {
                "background": copied["cabin-far"],
                "foreground": copied["cabin-near"],
                "camera": beat.get("camera", "hold"),
                "enter": "cut",
                "speaker": beat.get("speaker", ""),
                "line": beat.get("line", ""),
                "actors": [{
                    "cells": cells,
                    "expression": beat.get("expression", "neutral"),
                    "talking": bool(beat.get("talking")),
                    "x": beat["x"], "y": beat["y"], "scale": beat["scale"],
                }],
            },
        })
        frame += duration
    props = {
        "width": 1280, "height": 720, "fps": 24, "durationInFrames": max(frame, 1),
        "introFrames": 0, "shots": shots, "audio": [], "captions": [],
        "episodeNumber": 1, "title": title, "language": "zh",
        "cta": None, "ctaFrom": frame, "ctaFrames": 0,
    }
    target = OUT / "remotion-props.json"
    target.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")
    return target


def render_still(props: Path, frame: int, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "npx.cmd", "remotion", "still", "remotion/index.tsx", "SoundBook", str(dest),
        f"--frame={frame}", f"--props={props}", f"--browser-executable={CHROME}",
    ]
    subprocess.run(command, cwd=WORKER, check=True)


def actor_box(x: float, y: float, scale: float, width=1280, height=720):
    box_h = height * 0.64 * scale
    box_w = box_h * 0.86
    left = width * x - box_w / 2
    top = height * y - box_h
    return left, top, left + box_w, top + box_h


def evaluate_still(still_path: Path, near_path: Path, far_path: Path, box) -> tuple[bool, str]:
    still = np.asarray(Image.open(still_path).convert("RGB"))
    near = np.asarray(Image.open(near_path).convert("RGBA"))
    far = np.asarray(Image.open(far_path).convert("RGB"))
    height, width = still.shape[:2]
    if near.shape[0] != height or near.shape[1] != width:
        near = np.asarray(Image.fromarray(near).resize((width, height), Image.Resampling.BILINEAR))
    if far.shape[0] != height or far.shape[1] != width:
        far = np.asarray(Image.fromarray(far).resize((width, height), Image.Resampling.BILINEAR))
    x0, y0, x1, y1 = [int(round(value)) for value in box]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(width, x1), min(height, y1)
    cx0 = x0 + (x1 - x0) // 3
    cx1 = x0 + 2 * (x1 - x0) // 3
    if cx1 <= cx0 or y1 <= y0:
        return False, "actor box is empty"
    diff = np.abs(still[:, cx0:cx1].astype(np.int16) - far[:, cx0:cx1].astype(np.int16)).mean(axis=2)
    alpha = near[:, cx0:cx1, 3]
    character = (diff.mean(axis=1) > 34) & (alpha.mean(axis=1) < 48)
    furniture = alpha.mean(axis=1) > 140
    upper_alpha = float(near[: int(height * 0.42), cx0:cx1, 3].mean()) / 255
    if upper_alpha > 0.22:
        return False, f"window or hull is painted in front of the person ({upper_alpha:.2f})"
    runs: list[tuple[int, int]] = []
    start = None
    for row, on in enumerate(character):
        if on and start is None:
            start = row
        elif not on and start is not None:
            runs.append((start, row - 1))
            start = None
    if start is not None:
        runs.append((start, height - 1))
    if not runs:
        return False, "no neck, shoulders or chest were visible"
    run_start, run_end = max(runs, key=lambda item: item[1] - item[0])
    run_h = run_end - run_start + 1
    if run_h < int(height * 0.32):
        return False, f"visible body is only {run_h}px, neck shoulders and chest must clear the bench"
    furniture_rows = np.where(furniture)[0]
    below = furniture_rows[furniture_rows >= run_end - 8]
    if len(below) == 0:
        return False, "the bench is not in front of the body"
    gap = int(below[0] - run_end)
    if gap > 28:
        return False, f"a {gap}px gap of window or hull sits between the torso and the bench"
    return True, f"body {run_h}px ending at row {run_end}, bench overlaps after {gap}px, upper foreground {upper_alpha:.2f}"


def gate_beats(placement: dict) -> list[dict]:
    return [
        {"who": "atuo", "expression": "neutral", "talking": False, "camera": "hold", "duration": 1,
         "speaker": "", "line": "", **placement["wide"]},
        {"who": "atuo", "expression": "neutral", "talking": True, "camera": "push", "duration": 1,
         "speaker": ATUO, "line": "密封圈的读数掉下去了。", **placement["talking"]},
        {"who": "atuo", "expression": "smile", "talking": False, "camera": "hold", "duration": 1,
         "speaker": ATUO, "line": "这一圈先保住了。", **placement["expression"]},
    ]


# The bust fills the cell. y is the bottom of that cell, and the bench sill
# sits near 0.62 of the frame, so the waist has to cross the sill instead of
# sitting on the floor.
PLACEMENTS = [
    {"wide": {"x": 0.50, "y": 0.66, "scale": 1.22}, "talking": {"x": 0.46, "y": 0.68, "scale": 1.30}, "expression": {"x": 0.54, "y": 0.68, "scale": 1.26}},
    {"wide": {"x": 0.50, "y": 0.62, "scale": 1.32}, "talking": {"x": 0.46, "y": 0.64, "scale": 1.40}, "expression": {"x": 0.54, "y": 0.64, "scale": 1.36}},
    {"wide": {"x": 0.50, "y": 0.70, "scale": 1.12}, "talking": {"x": 0.48, "y": 0.72, "scale": 1.20}, "expression": {"x": 0.52, "y": 0.72, "scale": 1.16}},
]


def run_gate(client: Client, state: dict, files: dict[str, Path]) -> None:
    plate = np.asarray(Image.open(files["cabin-plate"]).convert("RGB"))
    sill_tries: list[int | None] = [None, int(plate.shape[0] * 0.70), int(plate.shape[0] * 0.62), int(plate.shape[0] * 0.76)]
    stills = OUT / "stills"
    for sill in sill_tries:
        files = repair_layers(client, state, files, sill)
        for placement in PLACEMENTS:
            props = stage_props(files, gate_beats(placement))
            names = ("wide", "talking", "expression")
            frames = (8, 32, 56)
            notes = []
            passed = True
            for name, frame, beat in zip(names, frames, gate_beats(placement)):
                dest = stills / f"{name}.png"
                render_still(props, frame, dest)
                box = actor_box(beat["x"], beat["y"], beat["scale"])
                ok, note = evaluate_still(dest, files["cabin-near"], files["cabin-far"], box)
                notes.append(f"{name}: {note}")
                print(f"still {name}: {'PASS' if ok else 'FAIL'} {note}", flush=True)
                passed = passed and ok
            if passed:
                state["gate"] = "pass"
                state["placement"] = placement
                state["stills"] = {name: str(stills / f"{name}.png") for name in names}
                state["sill"] = sill
                save_state(state)
                print("GATE PASS", flush=True)
                return
            print("retry placement/mask: " + " | ".join(notes), flush=True)
    state["gate"] = "fail"
    save_state(state)
    raise SystemExit("visual gate failed")


def ensure_episode(client: Client, state: dict) -> dict:
    story = state["story_id"]
    detail = client.request("GET", f"/api/stories/{story}", timeout=30)
    episode = next((item for item in detail.get("episodes") or [] if item.get("episode_number") == 1), None)
    if episode is None:
        episode = client.request("POST", f"/api/stories/{story}/episodes", {
            "episode_number": 1,
            "title": "密封圈",
            "concept": "阿拓 AT-1 要在本班次保住机械臂腕部的一道密封。加压测试失败一次，周宁核对一个可以复测的小步骤。",
        }, timeout=30)
    state["episode_id"] = episode["id"]
    save_state(state)
    note = "只使用已登记的全名：阿拓 AT-1 和周宁。禁止发明其他人名。6场，全片4到6分钟。一个工程目标，一次失败或代价，一个可以复测的小进展。"
    outline = (episode.get("outline") or {})
    if not outline.get("scenes"):
        print("writing outline", flush=True)
        outline = client.request("POST", f"/api/episodes/{episode['id']}/generate/outline", {"note": note}, timeout=240)["outline"]
    scenes = ((episode.get("script") or {}).get("scenes") or [])
    while len(scenes) < 6:
        print(f"writing scene {len(scenes) + 1}", flush=True)
        result = client.request("POST", f"/api/episodes/{episode['id']}/generate/script", {"incremental": True, "note": note}, timeout=240)
        scenes = (result.get("script") or {}).get("scenes") or scenes
        if result.get("partial") is not True and len(scenes) < 6 and not result.get("error"):
            break
    episode = client.request("GET", f"/api/episodes/{episode['id']}", timeout=30)
    script = episode.get("script") or {}
    scenes = script.get("scenes") or []
    if len(scenes) != 6:
        raise RuntimeError(f"episode 1 has {len(scenes)} scenes")
    allowed = {ATUO, ENGINEER, "旁白"}
    for scene in scenes:
        for line in scene.get("dialogues") or []:
            speaker = str(line.get("character") or "")
            if speaker not in allowed:
                raise RuntimeError(f"unexpected speaker {speaker}")
    total = 0.0
    for scene in scenes:
        for shot in scene.get("shots") or []:
            total += float(shot.get("duration_seconds") or 0)
    if total < 240 or total > 360:
        scale = 300 / max(total, 1)
        for scene in scenes:
            for shot in scene.get("shots") or []:
                shot["duration_seconds"] = round(max(4, float(shot.get("duration_seconds") or 6) * scale), 2)
            scene["duration_seconds"] = round(sum(float(shot.get("duration_seconds") or 0) for shot in scene.get("shots") or []), 2)
        client.request("PATCH", f"/api/episodes/{episode['id']}", {"script": script}, timeout=30)
        episode = client.request("GET", f"/api/episodes/{episode['id']}", timeout=30)
        print("scaled episode timing into 4-6 minutes", flush=True)
    library = client.request("GET", f"/api/stories/{story}/library", timeout=30)
    placement = state.get("placement") or PLACEMENTS[0]
    for scene in episode["script"]["scenes"]:
        for shot in scene.get("shots") or []:
            mark = shot.get("library") or {}
            framing = str(mark.get("framing") or "wide")
            mark["placement"] = placement.get(framing) or placement["wide"]
            mark["location"] = LOCATION
            shot["library"] = mark
    client.request("PATCH", f"/api/episodes/{episode['id']}", {"script": episode["script"]}, timeout=30)
    for scene in episode["script"]["scenes"]:
        number = int(scene.get("scene_number") or 0)
        for shot in scene.get("shots") or []:
            shot_number = int(shot.get("shot_number") or 0)
            print(f"bind scene {number} shot {shot_number}", flush=True)
            bound = client.request("POST", f"/api/episodes/{episode['id']}/generate/image", {
                "scene_number": number, "shot_number": shot_number,
            }, timeout=60)
            if bound.get("provider") != "story_library":
                raise RuntimeError(f"shot invented a new face: {bound}")
    print("episode shots reference the library", flush=True)
    return episode


def episode_beats(episode: dict, placement: dict) -> list[dict]:
    beats = []
    for scene in episode["script"]["scenes"]:
        lines = scene.get("dialogues") or []
        speaker = next((str(line.get("character")) for line in lines if line.get("character") in (ATUO, ENGINEER)), ATUO)
        line = next((str(item.get("text") or "") for item in lines if item.get("character") == speaker), "")
        who = "engineer" if speaker == ENGINEER else "atuo"
        for shot in scene.get("shots") or []:
            mark = shot.get("library") or {}
            framing = str(mark.get("framing") or "wide")
            place = placement.get(framing) or placement["wide"]
            beats.append({
                "who": who,
                "expression": str(mark.get("expression") or "neutral"),
                "talking": bool(mark.get("talking")),
                "camera": "push" if framing == "talking" else "hold",
                "duration": float(shot.get("duration_seconds") or 8),
                "speaker": speaker if speaker != "旁白" else "",
                "line": line[:42],
                "framing": framing,
                **place,
            })
    return beats


def render_episode(state: dict, files: dict[str, Path], episode: dict) -> None:
    beats = episode_beats(episode, state["placement"])
    props = stage_props(files, beats, title=str(episode.get("title") or "密封圈"))
    props_doc = json.loads(props.read_text(encoding="utf-8"))
    frames = {"wide": None, "talking": None, "expression": None}
    cursor = 0
    for beat, shot in zip(beats, props_doc["shots"]):
        framing = beat.get("framing") or "wide"
        if frames.get(framing) is None:
            frames[framing] = shot["from"] + min(8, shot["duration"] // 2)
        cursor += shot["duration"]
    for name, frame in frames.items():
        if frame is None:
            continue
        dest = OUT / "episode-stills" / f"{name}.png"
        render_still(props, int(frame), dest)
        sample = next(beat for beat in beats if (beat.get("framing") or "wide") == name)
        ok, note = evaluate_still(dest, files["cabin-near"], files["cabin-far"], actor_box(sample["x"], sample["y"], sample["scale"]))
        print(f"episode {name}: {'PASS' if ok else 'FAIL'} {note}", flush=True)
        if not ok:
            raise SystemExit(f"episode frame failed: {name}")
    output = OUT / "episode-1.mp4"
    command = [
        "npx.cmd", "remotion", "render", "remotion/index.tsx", "SoundBook", str(output),
        f"--props={props}", f"--browser-executable={CHROME}", "--codec=h264",
    ]
    print("rendering episode", flush=True)
    subprocess.run(command, cwd=WORKER, check=True)
    state["episode_output"] = str(output)
    state["episode_stills"] = {name: str(OUT / "episode-stills" / f"{name}.png") for name, frame in frames.items() if frame is not None}
    save_state(state)
    print(f"episode output {output}", flush=True)


def main() -> int:
    phase = sys.argv[1] if len(sys.argv) > 1 else "all"
    write_dev_vars()
    if phase == "env":
        return 0
    client = Client()
    for _ in range(60):
        try:
            urllib.request.urlopen(API + "/api/health", timeout=3).read()
            break
        except Exception:
            time.sleep(1)
    else:
        raise SystemExit("studio API is not running")
    client.login()
    state = load_state()
    ensure_story(client, state)
    if phase in ("all", "assets", "gate", "episode"):
        fill_library(client, state)
        files = download_library(client, state)
    else:
        files = download_library(client, state)
    if phase == "assets":
        return 0
    if state.get("gate") != "pass" and phase in ("all", "gate", "episode"):
        run_gate(client, state, files)
    if phase == "gate":
        return 0
    if state.get("gate") != "pass":
        raise SystemExit("gate has not passed")
    files = download_library(client, state)
    episode = ensure_episode(client, state)
    render_episode(state, files, episode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
