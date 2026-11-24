#!/usr/bin/env python3
"""Turn a godot3d manifest into the ``stage3d.json`` the godot3d player renders.

    python renderer/prepare_stage3d.py manifest.json stage3d.json
    python renderer/prepare_stage3d.py --manifest manifest.json --output stage3d.json
    python renderer/prepare_stage3d.py --demo-manifest media/m6_3d/manifest.json --output media/m6_3d/stage3d.json

The sample story is six shots in the lunar observation bay: Linlan walks in,
talks with Chen, takes the sensor from him, AT-1 rolls up to the window, she
looks out in first person, and closes on a nod. Shot lengths are locked to the
recorded lines with ``timeline_common.build_voice_locked_timeline``; each
shot can hold its first line back (``voice_lead``) so the blocking has time
to happen. Actors, the rover and the sensor run on one absolute frame clock,
so a cut never moves anybody. Cameras are per shot, in shot-local frames.

Characters use the manifest's ``cast.<id>.model`` GLB when the file exists
(fal.ai output); otherwise the Quaternius CC0 base body dressed in the crew
uniform. ``media/assets3d/licenses.json`` lists every CC0 file.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import struct
import sys
import wave
from pathlib import Path

try:
    from timeline_common import build_voice_locked_timeline, frame, shot_duration_frames
except ImportError:  # ``python -m unittest`` loads this module as part of the package.
    from renderer.timeline_common import build_voice_locked_timeline, frame, shot_duration_frames

ROOT = Path(__file__).resolve().parents[1]
CC0 = ROOT / 'media' / 'assets3d' / 'cc0'
KENNEY = CC0 / 'kenney_space_kit'
UAL = CC0 / 'quaternius_universal_animation_library'
UBC = CC0 / 'quaternius_universal_base_characters'
LOC2 = ROOT / 'public' / 'media' / 'loc2-plate.png'
DEMO_DIR = ROOT / 'media' / 'm6_3d'
INTERACT_AUDIO = ROOT / 'media' / 'm6_interact' / 'audio'

STAGE_VERSION = 1
SHOT_TYPES = ('wide_follow', 'two_shot', 'over_shoulder', 'rover_medium', 'first_person', 'closeup')

ROOM = {'min': [-7.0, 0.0, -4.5], 'max': [7.0, 3.4, 4.0]}
DOOR = {'x': 0.0, 'width': 1.6, 'height': 2.4, 'corridor_depth': 3.0}
WINDOW = {'x_min': -5.5, 'x_max': 5.5, 'y_min': 0.95, 'y_max': 3.0}
CAMERA_BOUNDS = {'min': [-6.7, 0.3, -4.15], 'max': [6.7, 3.1, 3.75], 'corridor_max_z': 6.8}

MARKS = {
    'door_start': (0.0, 4.65),
    'door_mid': (-0.3, 1.8),
    'chen': (-2.9, -3.05),
    'chen_console': (-3.5, -2.85),
    'linlan': (-2.34, -2.6),
    'console': (-3.25, -3.8),
    'rover_start': (6.35, -1.35),
    'rover_stop': (2.6, -3.3),
    'window_view': (-0.6, -10.0),
}
WALK_SPEED = 1.05
ROVER_SPEED = 1.15
ACTOR_RADIUS = 0.3
ROVER_RADIUS = 0.55
HANDOFF_CLIP_SECONDS = 2.0

# Unscaled Kenney footprints (width along local x, depth along local z), from
# godot3d/tools/probe_assets.gd.
KENNEY_FOOTPRINT = {
    'desk_computer.glb': (0.53, 0.22),
    'desk_computerScreen.glb': (0.53, 0.22),
    'desk_computerCorner.glb': (0.75, 0.75),
    'desk_chair.glb': (0.15, 0.15),
    'machine_wireless.glb': (0.75, 0.50),
    'machine_generatorLarge.glb': (1.00, 1.30),
    'machine_barrel.glb': (0.65, 0.52),
    'machine_barrelLarge.glb': (0.75, 0.75),
    'barrels.glb': (0.55, 0.50),
}
PROPS = [
    {'id': 'console_main', 'model': 'desk_computer.glb', 'pos': [-3.25, -3.8], 'yaw_deg': 0.0, 'scale': 2.6},
    {'id': 'console_corner', 'model': 'desk_computerCorner.glb', 'pos': [-5.5, -3.55], 'yaw_deg': 0.0, 'scale': 1.9},
    {'id': 'chair', 'model': 'desk_chair.glb', 'pos': [-4.3, -2.45], 'yaw_deg': 160.0, 'scale': 2.6},
    {'id': 'generator', 'model': 'machine_generatorLarge.glb', 'pos': [5.75, 2.75], 'yaw_deg': -90.0, 'scale': 1.4},
    {'id': 'wireless', 'model': 'machine_wireless.glb', 'pos': [6.2, 1.05], 'yaw_deg': -90.0, 'scale': 1.7},
    {'id': 'barrel_a', 'model': 'machine_barrel.glb', 'pos': [-6.05, 2.9], 'yaw_deg': 90.0, 'scale': 1.6},
    {'id': 'barrel_b', 'model': 'machine_barrelLarge.glb', 'pos': [-5.95, 1.45], 'yaw_deg': 0.0, 'scale': 1.4},
    {'id': 'barrels', 'model': 'barrels.glb', 'pos': [3.6, 3.25], 'yaw_deg': 0.0, 'scale': 1.8},
    {'id': 'crate_a', 'kind': 'crate', 'pos': [5.0, -0.35], 'size': [0.9, 0.9, 0.9], 'color': 'orange', 'yaw_deg': 6.0},
    {'id': 'crate_b', 'kind': 'crate', 'pos': [6.08, -0.15], 'size': [0.75, 0.6, 0.75], 'color': 'dark', 'yaw_deg': -10.0},
]
OUTSIDE_PROPS = [
    {'id': 'rocks_small', 'model': 'rocks_smallA.glb', 'pos': [3.0, -11.0], 'yaw_deg': 0.0, 'scale': 6.0},
    {'id': 'dish', 'model': 'satelliteDish_large.glb', 'pos': [-19.0, -30.0], 'yaw_deg': 35.0, 'scale': 7.0},
    {'id': 'crystals', 'model': 'rock_crystalsLargeA.glb', 'pos': [24.0, -20.0], 'yaw_deg': 10.0, 'scale': 6.0},
]
LIGHTING = {
    'ambient': '#a9b8ce',
    'ambient_energy': 0.7,
    'sun_energy': 0.72,
    'lamp_energy': 0.7,
    'fills': [
        {'id': 'ConsoleGlow', 'pos': [-3.0, 1.45, -3.25], 'range': 3.2, 'energy': 0.55, 'color': '#9fe8ff'},
        {'id': 'FaceFill', 'pos': [-1.2, 1.9, -1.2], 'range': 4.0, 'energy': 0.45, 'color': '#fff1df'},    ],
}
DEFAULT_PALETTE = {
    'sky_top': '#3f7fc6',
    'horizon': '#c4dcee',
    'snow': '#eef3f8',
    'rock': '#9fb0c2',
    'shadow': '#6f85a0',
    'planet_light': '#eef4fb',
    'planet_mid': '#7fb0d8',
    'planet_shadow': '#4f7fb5',
}
UNIFORM = {'jacket': '#2c5aa0', 'collar': '#f08a24', 'pants': '#1c2638', 'boots': '#15171c'}
DEFAULT_CAST = {
    'linlan': {
        'name': '林岚',
        'height_m': 1.65,
        'model': UBC / 'Superhero_Female_FullBody.gltf',
        'look': {
            **UNIFORM,
            'skin_texture': UBC / 'T_Superhero_Female_Light_BaseColor.png',
            'hair_model': UBC / 'Hair_BuzzedFemale.gltf',
            'hair_color': '#121216',
            'ponytail': True,
            'badge': False,
            'viewmodel_skin': '#e8c2a0',
        },
    },
    'chen': {
        'name': '陈工',
        'height_m': 1.76,
        'model': UBC / 'Superhero_Male_FullBody.gltf',
        'look': {
            **UNIFORM,
            'skin_texture': UBC / 'T_Superhero_Male_Ligh.png',
            'hair_model': UBC / 'Hair_SimpleParted.gltf',
            'hair_color': '#121216',
            'ponytail': False,
            'badge': True,
        },
    },
}
ANIMATION = {
    'library': UAL / 'UAL1_Standard.glb',
    'root_motion_library': UAL / 'UAL1_Standard_RM.glb',
    'sources': ['library', 'own'],
}
ROVER = {'id': 'at1', 'name': '阿拓', 'model': KENNEY / 'rover.glb', 'height_m': 1.0, 'yaw_offset_deg': 0.0}

DEMO_LINES = [
    (1, 1, '林岚', 'linlan', '陈工，三号站的信号又断了？'),
    (2, 1, '陈工', 'chen', '是传感器坏了，得换个新的。'),
    (2, 2, '林岚', 'linlan', '我去换。'),
    (3, 1, '陈工', 'chen', '拿好，这是最后一个备件。'),
    (4, 1, '阿拓', 'at1', '阿拓已就位，随时可以出发。'),
    (5, 1, '林岚', 'linlan', '风暴来之前，得把它装好。'),
    (6, 1, '林岚', 'linlan', '放心，交给我。'),
]
DEMO_SHOTS = [
    {'scene_number': 1, 'id': 's1_wide_follow', 'type': 'wide_follow', 'duration': 5.8, 'voice_lead': 2.7,
     'description': 'Wide 3rd-person follow: Linlan walks in from the bay door toward the console.'},
    {'scene_number': 2, 'id': 's2_two_shot', 'type': 'two_shot', 'duration': 5.5, 'voice_lead': 0.6,
     'description': 'Two-shot: Chen at the console, Linlan and Chen talk.'},
    {'scene_number': 3, 'id': 's3_over_shoulder', 'type': 'over_shoulder', 'duration': 4.4, 'voice_lead': 0.3,
     'description': 'Over the shoulder: Chen hands the sensor to Linlan.'},
    {'scene_number': 4, 'id': 's4_rover_medium', 'type': 'rover_medium', 'duration': 5.0, 'voice_lead': 2.6,
     'description': 'Medium: AT-1 drives in from the right and stops by the window.'},
    {'scene_number': 5, 'id': 's5_first_person', 'type': 'first_person', 'duration': 4.2, 'voice_lead': 0.7,
     'description': 'First person: Linlan looks out at the moonscape, sensor in hand.'},
    {'scene_number': 6, 'id': 's6_closeup', 'type': 'closeup', 'duration': 3.6, 'voice_lead': 0.45,
     'description': 'Chest-up close-up: Linlan nods and says the last line.'},
]


# ------------------------------------------------------------------ geometry


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _norm(v: tuple[float, float]) -> tuple[float, float]:
    length = math.hypot(*v) or 1.0
    return v[0] / length, v[1] / length


def _yaw_deg(direction: tuple[float, float]) -> float:
    """Godot yaw that turns a +Z-facing model toward ``direction`` (x, z)."""
    return math.degrees(math.atan2(direction[0], direction[1]))


def _round(values, digits: int = 3):
    return [round(float(v), digits) for v in values]


def prop_footprint(prop: dict) -> list[tuple[float, float]] | None:
    """World-space corners of a prop's floor footprint, or None if unknown."""
    if prop.get('kind') == 'crate':
        width, _, depth = prop.get('size', [0.9, 0.9, 0.9])
    else:
        base = KENNEY_FOOTPRINT.get(Path(str(prop.get('model', ''))).name)
        if base is None:
            return None
        scale = float(prop.get('scale', 1.0))
        width, depth = base[0] * scale, base[1] * scale
    cx, cz = float(prop['pos'][0]), float(prop['pos'][-1])
    yaw = math.radians(float(prop.get('yaw_deg', 0.0)))
    cos_y, sin_y = math.cos(yaw), math.sin(yaw)
    corners = []
    for sx, sz in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        lx, lz = sx * width / 2, sz * depth / 2
        corners.append((cx + lx * cos_y + lz * sin_y, cz - lx * sin_y + lz * cos_y))
    return corners


def _project(corners, axis):
    values = [c[0] * axis[0] + c[1] * axis[1] for c in corners]
    return min(values), max(values)


def footprints_overlap(a: list[tuple[float, float]], b: list[tuple[float, float]], margin: float = 0.0) -> bool:
    for poly in (a, b):
        for i in range(len(poly)):
            p, q = poly[i], poly[(i + 1) % len(poly)]
            axis = _norm((q[1] - p[1], p[0] - q[0]))
            a0, a1 = _project(a, axis)
            b0, b1 = _project(b, axis)
            if a1 + margin <= b0 or b1 + margin <= a0:
                return False
    return True


def point_footprint_distance(point: tuple[float, float], corners: list[tuple[float, float]]) -> float:
    inside = all(
        (corners[(i + 1) % 4][0] - corners[i][0]) * (point[1] - corners[i][1])
        - (corners[(i + 1) % 4][1] - corners[i][1]) * (point[0] - corners[i][0]) >= 0
        for i in range(4)
    ) or all(
        (corners[(i + 1) % 4][0] - corners[i][0]) * (point[1] - corners[i][1])
        - (corners[(i + 1) % 4][1] - corners[i][1]) * (point[0] - corners[i][0]) <= 0
        for i in range(4)
    )
    if inside:
        return 0.0
    best = math.inf
    for i in range(4):
        p, q = corners[i], corners[(i + 1) % 4]
        dx, dz = q[0] - p[0], q[1] - p[1]
        t = max(0.0, min(1.0, ((point[0] - p[0]) * dx + (point[1] - p[1]) * dz) / (dx * dx + dz * dz or 1.0)))
        best = min(best, math.hypot(point[0] - (p[0] + t * dx), point[1] - (p[1] + t * dz)))
    return best


def sample_polyline(points: list[tuple[float, float]], step: float = 0.1) -> list[tuple[float, float]]:
    out = [points[0]]
    for a, b in zip(points, points[1:]):
        count = max(1, int(math.ceil(_dist(a, b) / step)))
        out.extend((a[0] + (b[0] - a[0]) * k / count, a[1] + (b[1] - a[1]) * k / count) for k in range(1, count + 1))
    return out


# --------------------------------------------------------------- LOC2 colour


def loc2_palette(path: Path = LOC2) -> dict:
    """Sky, ridge and planet colours sampled from the observation bay plate."""
    try:
        from PIL import Image
    except ImportError:
        return dict(DEFAULT_PALETTE)
    if not Path(path).is_file():
        return dict(DEFAULT_PALETTE)
    image = Image.open(path).convert('RGB').resize((1024, 576))

    def region(box):
        crop = image.crop(box)
        return list(crop.get_flattened_data() if hasattr(crop, 'get_flattened_data') else crop.getdata())

    def mean(pixels):
        n = max(1, len(pixels))
        return tuple(sum(p[i] for p in pixels) / n for i in range(3))

    def by_brightness(pixels):
        return sorted(pixels, key=lambda p: p[0] + p[1] + p[2])

    def hex_color(rgb):
        return '#%02x%02x%02x' % tuple(max(0, min(255, int(round(c)))) for c in rgb)

    sky = region((560, 168, 760, 186))
    horizon = region((300, 246, 760, 258))
    ridges = by_brightness(region((262, 266, 790, 340)))
    planet = by_brightness(region((300, 190, 520, 258)))
    k = max(1, len(ridges) // 7)
    j = max(1, len(planet) // 5)
    return {
        'sky_top': hex_color(mean(sky)),
        'horizon': hex_color(mean(horizon)),
        'snow': hex_color(mean(ridges[-k:])),
        'rock': hex_color(mean(ridges[len(ridges) // 2 - k // 2: len(ridges) // 2 + k // 2 + 1])),
        'shadow': hex_color(mean(ridges[:k])),
        'planet_light': hex_color(mean(planet[-j:])),
        'planet_mid': hex_color(mean(planet[len(planet) // 2 - j // 2: len(planet) // 2 + j // 2 + 1])),
        'planet_shadow': hex_color(mean(planet[:j])),
    }


# ------------------------------------------------------------------- audio


def _write_wav(path: Path, samples: list[float], rate: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = b''.join(struct.pack('<h', max(-32767, min(32767, int(s * 32767)))) for s in samples)
    with wave.open(str(path), 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(data)


def write_bay_hum(path: Path, seconds: float = 8.0, rate: int = 24000) -> Path:
    """A seamless life-support hum: whole cycles of 50/100/150 Hz plus a slow air hiss."""
    count = int(seconds * rate)
    samples = []
    state = 12345
    smooth = 0.0
    for n in range(count):
        t = n / rate
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        smooth += ((state / 0x7FFFFFFF) * 2 - 1 - smooth) * 0.02
        tone = 0.5 * math.sin(2 * math.pi * 50 * t) + 0.3 * math.sin(2 * math.pi * 100 * t) + 0.12 * math.sin(2 * math.pi * 150 * t)
        swell = 0.85 + 0.15 * math.sin(2 * math.pi * t / seconds)
        samples.append(0.18 * tone * swell + 0.25 * smooth)
    _write_wav(path, samples, rate)
    return path


def write_rover_whirr(path: Path, seconds: float, rate: int = 24000) -> Path:
    """Electric drive whine that spins up, holds, and winds down over ``seconds``."""
    count = max(1, int(seconds * rate))
    samples = []
    phase = 0.0
    for n in range(count):
        t = n / rate
        u = t / max(seconds, 0.01)
        envelope = min(1.0, u / 0.15) * min(1.0, (1.0 - u) / 0.2)
        freq = 140 + 120 * min(1.0, u / 0.25) - 60 * max(0.0, (u - 0.8) / 0.2)
        phase += 2 * math.pi * freq / rate
        tone = 0.6 * math.sin(phase) + 0.25 * math.sin(2 * phase) + 0.1 * math.sin(3.01 * phase)
        rumble = 0.15 * math.sin(2 * math.pi * 31 * t) * (0.6 + 0.4 * math.sin(2 * math.pi * 7 * t))
        samples.append(0.35 * envelope * (tone + rumble))
    _write_wav(path, samples, rate)
    return path


def write_silence(path: Path, seconds: float, rate: int = 24000) -> Path:
    _write_wav(path, [0.0] * max(1, int(seconds * rate)), rate)
    return path


# ------------------------------------------------------------ demo manifest


def demo_manifest_doc() -> dict:
    tracks = []
    for scene, dialogue, speaker, actor, text in DEMO_LINES:
        tracks.append({
            'scene_number': scene,
            'dialogue_number': dialogue,
            'speaker': speaker,
            'actor': actor,
            'tts_voice': speaker,
            'tts_text': text,
            'caption_text': text,
        })
    return {
        'version': 1,
        'renderer': 'godot3d',
        'story': 'lunar_bay_sensor_handoff',
        'title': '观测舱：最后一个传感器',
        'width': 1280,
        'height': 720,
        'fps': 24,
        'language': 'zh',
        'timeline_mode': 'voice_locked',
        'tts_engine': 'edge',
        'cast': {
            'linlan': {'name': '林岚', 'model': None, 'height_m': 1.65},
            'chen': {'name': '陈工', 'model': None, 'height_m': 1.76},
            'at1': {'name': '阿拓', 'model': None},
        },
        'shots': [dict(shot) for shot in DEMO_SHOTS],
        'audio_tracks': tracks,
    }


def write_demo_manifest(path: Path, synthesize_audio: bool = True) -> dict:
    """Write the sample manifest and give every line a real audio file.

    Lines are voiced with ``renderer/synthesize.py`` (engine ``edge``; MOSS is
    not installed locally). If that fails, the three clips in
    ``media/m6_interact/audio`` stand in for the first three lines and the rest
    become silent holds of the estimated length. ``audio_source`` says which.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = demo_manifest_doc()
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    error = '' if synthesize_audio else 'skipped (--no-tts)'
    if synthesize_audio:
        try:
            _synthesize_with_edge(path)
            doc = json.loads(path.read_text(encoding='utf-8'))
            for track in doc['audio_tracks']:
                if not Path(track['file']).is_absolute():
                    track['file'] = (ROOT / track['file']).resolve().as_posix()
            doc['audio_source'] = (
                'edge-tts neural voices via renderer/synthesize.py (tts_engine=edge); '
                'MOSS is not installed locally'
            )
        except Exception as exc:  # no network, or edge-tts missing
            error = str(exc)
    if error:
        doc = demo_manifest_doc()
        stand_ins = sorted(INTERACT_AUDIO.glob('line*.wav'))
        for index, track in enumerate(doc['audio_tracks']):
            if index < len(stand_ins):
                track['file'] = stand_ins[index].resolve().as_posix()
            else:
                seconds = 0.6 + 0.24 * len(track['caption_text'])
                track['file'] = write_silence(path.parent / 'audio' / f'hold-{index}.wav', seconds).resolve().as_posix()
        doc['audio_source'] = (
            'placeholder: media/m6_interact/audio line0-2 reused for the first three lines, '
            f'silent holds for the rest (edge-tts: {error[:160]})'
        )
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return doc


def _synthesize_with_edge(manifest: Path) -> None:
    """Run ``renderer/synthesize.py`` from the repo root, where it caches into media/tts."""
    try:
        from synthesize import synthesize
    except ImportError:
        from renderer.synthesize import synthesize
    previous = Path.cwd()
    os.chdir(ROOT)
    try:
        synthesize(manifest.resolve())
    finally:
        os.chdir(previous)


# ----------------------------------------------------------------- compile


def _resolve_file(value: object, manifest_dir: Path) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    for candidate in ([path] if path.is_absolute() else [manifest_dir / path, ROOT / path, path]):
        if candidate.is_file():
            return candidate.resolve()
    return None


def _cast_entry(actor_id: str, cast_doc: dict, manifest_dir: Path) -> dict:
    base = DEFAULT_CAST[actor_id]
    entry = cast_doc.get(actor_id) if isinstance(cast_doc.get(actor_id), dict) else {}
    generated = _resolve_file(entry.get('model'), manifest_dir)
    look = {k: (v.as_posix() if isinstance(v, Path) else v) for k, v in base['look'].items()}
    if generated is not None:
        look = {**{k: look[k] for k in ('jacket', 'collar')}, 'uniform': False, 'ponytail': False, 'badge': False}
    look.update(entry.get('look') or {})
    animation = {
        'library': Path(entry.get('animation_library') or ANIMATION['library']).as_posix(),
        'root_motion_library': Path(ANIMATION['root_motion_library']).as_posix(),
        'sources': list(entry.get('animation_sources') or ANIMATION['sources']),
    }
    # A rig's own walk (Meshy basic_animations) was authored on that mesh, so it beats a retarget.
    walk = entry.get('walk_clip') if generated is not None and isinstance(entry.get('walk_clip'), dict) else None
    walk_library = _resolve_file(walk.get('library'), manifest_dir) if walk else None
    if walk_library is not None:
        animation['clip_sources'] = {'walk': {'library': walk_library.as_posix(), 'clip': str(walk.get('clip', ''))}}
    return {
        'name': str(entry.get('name') or base['name']),
        'source': 'fal_generated' if generated is not None else 'cc0_fallback',
        'fallback_reason': '' if generated is not None else str(entry.get('fallback_reason') or 'no generated model in manifest'),
        'model': Path(generated or base['model']).as_posix(),
        'height_m': float(entry.get('height_m') or base['height_m']),
        'animation': animation,
        'look': look,
    }


def sensor_grip_point(model: Path) -> list[float]:
    """Middle of the handle: the lowest third of the mesh, where the hand closes."""
    try:
        import trimesh
    except ImportError:
        return [0.0, 0.0, 0.0]
    mesh = trimesh.load(str(model), force='mesh')
    vertices = mesh.vertices
    low, high = float(vertices[:, 1].min()), float(vertices[:, 1].max())
    band = vertices[vertices[:, 1] < low + (high - low) * 0.38]
    center = band.mean(axis=0)
    return [round(float(center[0]), 4), round(low + (high - low) * 0.2, 4), round(float(center[2]), 4)]


def _lock_shot(shot: dict, tracks: list[dict], fps: int) -> tuple[float, list[dict], list[dict]]:
    """Voice-lock one shot on its own clock. Returns (voiced seconds, captions, tracks)."""
    if not tracks:
        return 0.0, [], []
    local_shot = dict(shot)
    local_tracks = [dict(track) for track in tracks]
    _, captions = build_voice_locked_timeline({'timeline_mode': 'voice_locked'}, [local_shot], local_tracks, fps)
    return float(local_shot['duration']), captions, local_tracks


def prepare(manifest_path: Path, output_path: Path, check_files: bool = True) -> dict:
    """Read ``manifest_path``, write ``stage3d.json`` to ``output_path``, return it."""
    manifest_path = Path(manifest_path)
    output_path = Path(output_path)
    doc = json.loads(manifest_path.read_text(encoding='utf-8'))
    if doc.get('version') != 1 or not isinstance(doc.get('shots'), list):
        raise ValueError('Expected a v1 manifest with shots')
    fps = int(doc.get('fps', 24))
    if (int(doc.get('width', 1280)), int(doc.get('height', 720))) != (1280, 720) or not 1 <= fps <= 60:
        raise ValueError('godot3d only renders 1280x720 at 1-60 fps')
    manifest_dir = manifest_path.resolve().parent
    shots_in = [dict(shot) for shot in doc['shots'] if isinstance(shot, dict)]
    types = [str(shot.get('type', '')) for shot in shots_in]
    if types != list(SHOT_TYPES):
        raise ValueError(f'godot3d sample story needs the six shots {SHOT_TYPES}, got {types}')
    tracks = []
    for raw in doc.get('audio_tracks') or []:
        if not isinstance(raw, dict):
            continue
        track = dict(raw)
        resolved = _resolve_file(track.get('file'), manifest_dir)
        if resolved is None:
            raise FileNotFoundError(f"audio track has no readable file: {track.get('file')!r} ({track.get('caption_text', '')})")
        track['file'] = resolved.as_posix()
        tracks.append(track)
    voice_locked = doc.get('timeline_mode', 'voice_locked') == 'voice_locked'

    marks = {k: tuple(v) for k, v in MARKS.items()}
    door, mid, chen, linlan = marks['door_start'], marks['door_mid'], marks['chen'], marks['linlan']
    walk_start = frame(0.35, fps)
    walk_len = _dist(door, mid) + _dist(mid, linlan)
    arrival = walk_start + frame(walk_len / WALK_SPEED, fps)
    rover_len = _dist(marks['rover_start'], marks['rover_stop'])
    rover_travel = rover_len / ROVER_SPEED

    shots: list[dict] = []
    captions: list[dict] = []
    voice_items: list[dict] = []
    cursor = 0
    for shot in shots_in:
        scene = int(shot.get('scene_number', 0))
        shot_tracks = sorted(
            (t for t in tracks if int(t.get('scene_number', -1)) == scene and not t.get('loop')),
            key=lambda t: int(t.get('dialogue_number', 0)),
        )
        lead = max(0.0, float(shot.get('voice_lead', 0.3)))
        minimum = max(0.25, float(shot.get('duration', 4.0)))
        kind = shot['type']
        if kind == 'two_shot':
            lead = max(lead, (arrival - cursor) / fps + 0.35)
        elif kind == 'over_shoulder':
            minimum = max(minimum, lead + 0.5 + HANDOFF_CLIP_SECONDS + 0.6)
        elif kind == 'rover_medium':
            lead = max(lead, 0.3 + rover_travel - 0.35)
            minimum = max(minimum, 0.3 + rover_travel + 1.2)
        voiced, local_caps, local_tracks = _lock_shot(shot, shot_tracks, fps) if voice_locked else (0.0, [], [])
        duration = max(minimum, lead + voiced) if voiced else minimum
        frames = shot_duration_frames(duration, fps)
        lead_frames = frame(lead, fps)
        for cap in local_caps:
            captions.append({'from': cursor + lead_frames + cap['from'], 'to': cursor + lead_frames + cap['to'], 'text': cap['text']})
        lines = []
        for track in local_tracks:
            start = cursor + lead_frames + frame(float(track['start_ms']) / 1000, fps)
            end = start + frame(float(track['_duration']), fps)
            item = {
                'file': track['file'],
                'start_ms': round((cursor + lead_frames) * 1000 / fps + float(track['start_ms'])),
                'volume': float(track.get('volume', 1.0)),
                'loop': False,
                'actor': str(track.get('actor', '')),
                'speaker': str(track.get('speaker', '')),
                'from': start,
                'to': end,
                'text': str(track.get('caption_text', '')),
            }
            voice_items.append(item)
            lines.append(item)
        shots.append({
            'id': str(shot.get('id', f'shot{scene}')),
            'type': kind,
            'scene_number': scene,
            'description': str(shot.get('description', '')),
            'from': cursor,
            'frames': frames,
            'voice_lead_frames': lead_frames,
            'lines': lines,
        })
        cursor += frames
    total_frames = cursor
    by_type = {shot['type']: shot for shot in shots}

    # ---- blocking on the absolute clock
    s3, s4, s5, s6 = by_type['over_shoulder'], by_type['rover_medium'], by_type['first_person'], by_type['closeup']
    a = _norm((chen[0] - linlan[0], chen[1] - linlan[1]))
    right_of_linlan = (-a[1], a[0])
    room_side = right_of_linlan if right_of_linlan[1] > 0 else (a[1], -a[0])
    console_dir = _norm((marks['console'][0] - chen[0], marks['console'][1] - chen[1]))

    def talk_clips(actor: str) -> list[dict]:
        return [{'from': item['from'], 'to': item['to'] + frame(0.2, fps), 'clip': 'talk'}
                for item in voice_items if item['actor'] == actor]

    s3_line = s3['lines'][0] if s3['lines'] else {'from': s3['from'] + s3['voice_lead_frames'], 'to': s3['from'] + s3['voice_lead_frames']}
    # Both right hands reach for the point between them (IK in the player);
    # the sensor changes hands when the gap between the palms stops closing.
    handoff_chen = s3_line['from'] + frame(0.45, fps)
    handoff_linlan = handoff_chen + frame(0.25, fps)
    window = [handoff_chen + frame(0.55, fps), handoff_chen + frame(1.45, fps)]
    reach_height = 1.08
    s6_line = s6['lines'][0] if s6['lines'] else {'from': s6['from'] + s6['voice_lead_frames'], 'to': s6['from'] + s6['frames']}
    # Her last line is a nod with hands down: the close-up keeps her hands out of frame.
    linlan_clips = [c for c in talk_clips('linlan') if not (s6['from'] <= c['from'] < s6['from'] + s6['frames'])]
    chen_clips = talk_clips('chen')
    chen_reaches = [{'from': handoff_chen, 'to': handoff_chen + frame(HANDOFF_CLIP_SECONDS, fps),
                     'target': 'linlan', 'height': reach_height, 'ramp_s': 0.45}]
    linlan_reaches = [{'from': handoff_linlan, 'to': handoff_linlan + frame(HANDOFF_CLIP_SECONDS - 0.15, fps),
                       'target': 'chen', 'height': reach_height, 'ramp_s': 0.4}]

    cast_doc = doc.get('cast') if isinstance(doc.get('cast'), dict) else {}
    chen_step = s5['from'] + frame(0.6, fps)
    actors = {}
    linlan_cfg = _cast_entry('linlan', cast_doc, manifest_dir)
    linlan_cfg.update({
        'start': {'pos': _round(door), 'yaw_deg': 180.0},
        'moves': [{'from': walk_start, 'to': arrival, 'points': [_round(mid), _round(linlan)], 'ramp_s': 0.5}],
        'facing': [
            {'from': 0, 'target': 'chen'},
            {'from': s4['from'] + frame(0.5, fps), 'target': ROVER['id']},
            {'from': s5['from'] - frame(0.6, fps), 'point': _round(marks['window_view'])},
            {'from': s6['from'] - frame(0.4, fps), 'target': 'chen'},
        ],
        'clips': sorted(linlan_clips, key=lambda c: c['from']),
        'reaches': linlan_reaches,
        'nods': [{'from': max(s6['from'], s6_line['from'] - 2), 'to': s6_line['from'] + frame(1.2, fps), 'rate': 1.6}],
    })
    actors['linlan'] = linlan_cfg
    chen_cfg = _cast_entry('chen', cast_doc, manifest_dir)
    chen_cfg.update({
        'start': {'pos': _round(chen), 'yaw_deg': round(_yaw_deg(console_dir), 2)},
        # He steps back to the console while the camera is in Linlan's eyes, so
        # the closing close-up has room to frame her alone.
        'moves': [{'from': chen_step, 'to': chen_step + frame(_dist(chen, marks['chen_console']) / 0.9, fps),
                   'points': [_round(marks['chen_console'])], 'ramp_s': 0.3}],
        'facing': [
            {'from': 0, 'point': _round(marks['console'])},
            {'from': max(0, arrival - frame(1.6, fps)), 'target': 'linlan'},
            {'from': s4['from'] + frame(0.7, fps), 'target': ROVER['id']},
            {'from': s5['from'] + frame(0.5, fps), 'target': 'linlan'},
        ],
        'clips': sorted(chen_clips, key=lambda c: c['from']),
        'reaches': chen_reaches,
        'nods': [],
    })
    actors['chen'] = chen_cfg

    rover_go = s4['from'] + frame(0.3, fps)
    rover_arrive = rover_go + frame(rover_travel, fps)
    rover_dir = _norm((marks['rover_stop'][0] - marks['rover_start'][0], marks['rover_stop'][1] - marks['rover_start'][1]))
    rover_entry = cast_doc.get('at1') if isinstance(cast_doc.get('at1'), dict) else {}
    rover_model = _resolve_file(rover_entry.get('model'), manifest_dir)
    rover = {
        'id': ROVER['id'],
        'name': ROVER['name'],
        'source': 'fal_generated' if rover_model else 'kenney_fallback',
        'fallback_reason': '' if rover_model else str(rover_entry.get('fallback_reason') or 'no generated model in manifest'),
        'model': (rover_model or ROVER['model']).as_posix(),
        'matte': rover_model is not None,
        'height_m': float(rover_entry.get('height_m') or ROVER['height_m']),
        'yaw_offset_deg': float(rover_entry.get('yaw_offset_deg', ROVER['yaw_offset_deg'])),
        'eye_offset': [float(v) for v in rover_entry.get('eye_offset') or
                       [0.0, float(rover_entry.get('height_m') or ROVER['height_m']) * 0.62, 0.05]],
        'start': {'pos': _round(marks['rover_start']), 'yaw_deg': round(_yaw_deg(rover_dir), 2)},
        'visibility': [{'from': 0, 'visible': False}, {'from': s4['from'], 'visible': True}],
        'moves': [{'from': rover_go, 'to': rover_arrive, 'points': [_round(marks['rover_stop'])], 'ramp_s': 0.6}],
        'facing': [{'from': rover_arrive + frame(0.3, fps), 'target': 'linlan'}],
    }

    sensor_entry = cast_doc.get('sensor') if isinstance(cast_doc.get('sensor'), dict) else {}
    sensor_model = _resolve_file(sensor_entry.get('model'), manifest_dir)
    props = {'sensor': {
        'kind': 'sensor',
        'source': 'fal_generated' if sensor_model else 'procedural_fallback',
        'model': sensor_model.as_posix() if sensor_model else '',
        'grip_point': [float(v) for v in sensor_entry.get('grip_point') or (sensor_grip_point(sensor_model) if sensor_model else [0, 0, 0])],
        'grip_rotation_deg': [float(v) for v in sensor_entry.get('grip_rotation_deg') or [0.0, 0.0, 0.0]],
        'owners': [
            {'from': 0, 'actor': 'chen'},
            {'from': window[0], 'actor': 'linlan', 'handoff_window': window, 'max_gap': 0.36},
        ],
    }}

    # ---- cameras, in shot-local frames
    mid_pair = ((linlan[0] + chen[0]) / 2, (linlan[1] + chen[1]) / 2)
    to_console = _norm((marks['chen_console'][0] - linlan[0], marks['chen_console'][1] - linlan[1]))
    console_side = (-to_console[1], to_console[0])
    if console_side[1] < 0:
        console_side = (-console_side[0], -console_side[1])
    closeup_dir = _norm((to_console[0] * math.cos(math.radians(40)) + console_side[0] * math.sin(math.radians(40)),
                         to_console[1] * math.cos(math.radians(40)) + console_side[1] * math.sin(math.radians(40))))
    view_dir = _norm((marks['window_view'][0] - linlan[0], marks['window_view'][1] - linlan[1]))
    for shot in shots:
        n = shot['frames']
        kind = shot['type']
        if kind == 'wide_follow':
            cams = [{'from': 0, 'to': n, 'mode': 'follow', 'actor': 'linlan',
                     'offset': [0.3, 1.62, 1.7], 'offset_to': [1.15, 2.2, 3.6],
                     'look_offset': [0.0, 1.3, -2.2], 'look_offset_to': [-0.6, 1.05, -3.2],
                     'smooth': 0.2, 'fov': 56.0}]
        elif kind == 'two_shot':
            cams = [{'from': 0, 'to': n, 'mode': 'dolly',
                     'pos': _round((mid_pair[0] + room_side[0] * 3.2, 1.5, mid_pair[1] + room_side[1] * 3.2)),
                     'pos_to': _round((mid_pair[0] + room_side[0] * 2.8, 1.46, mid_pair[1] + room_side[1] * 2.8)),
                     'look_at': _round((mid_pair[0], 1.28, mid_pair[1])),
                     'look_to': _round((mid_pair[0], 1.3, mid_pair[1])),
                     'fov': 40.0, 'fov_to': 40.0}]
        elif kind == 'over_shoulder':
            base = (linlan[0] - a[0] * 1.45 + right_of_linlan[0] * 0.88, linlan[1] - a[1] * 1.45 + right_of_linlan[1] * 0.88)
            look = (chen[0] * 0.58 + linlan[0] * 0.42, chen[1] * 0.58 + linlan[1] * 0.42)
            cams = [{'from': 0, 'to': n, 'mode': 'dolly',
                     'pos': _round((base[0], 1.68, base[1])),
                     'pos_to': _round((base[0] + a[0] * 0.15, 1.66, base[1] + a[1] * 0.15)),
                     'look_at': _round((look[0], 1.16, look[1])), 'look_to': _round((look[0], 1.12, look[1])),
                     'fov': 34.0, 'fov_to': 33.0}]
        elif kind == 'rover_medium':
            stop = marks['rover_stop']
            cams = [{'from': 0, 'to': n, 'mode': 'dolly',
                     'pos': [1.05, 1.02, -0.75], 'pos_to': [1.15, 1.0, -0.95],
                     'look_at': _round((stop[0] + 1.7, 0.5, stop[1] + 0.85)), 'look_to': _round((stop[0] + 0.1, 0.5, stop[1])),
                     'fov': 42.0, 'fov_to': 40.0}]
        elif kind == 'first_person':
            far = (linlan[0] + view_dir[0] * 9.0, linlan[1] + view_dir[1] * 9.0)
            cams = [{'from': 0, 'to': n, 'mode': 'pov', 'actor': 'linlan', 'eye_height': 1.55,
                     'look_at': _round((far[0] - 0.8, 1.4, far[1])), 'look_to': _round((far[0] + 0.6, 1.5, far[1])),
                     'fov': 60.0, 'sway': 0.005}]
        else:
            cams = [{'from': 0, 'to': n, 'mode': 'dolly',
                     'pos': _round((linlan[0] + closeup_dir[0] * 1.18, 1.45, linlan[1] + closeup_dir[1] * 1.18)),
                     'pos_to': _round((linlan[0] + closeup_dir[0] * 1.06, 1.44, linlan[1] + closeup_dir[1] * 1.06)),
                     'look_at': _round((linlan[0], 1.36, linlan[1])), 'look_to': _round((linlan[0], 1.37, linlan[1])),
                     'fov': 35.0, 'fov_to': 35.0}]
        shot['cameras'] = cams
        shot['first_person'] = 'linlan' if kind == 'first_person' else ''

    # ---- audio bed
    audio_dir = output_path.resolve().parent / 'audio'
    hum = write_bay_hum(audio_dir / 'bay_hum.wav')
    whirr = write_rover_whirr(audio_dir / 'rover_whirr.wav', rover_travel + 0.4)
    audio = [{'file': item['file'], 'start_ms': item['start_ms'], 'volume': item['volume'], 'loop': False}
             for item in voice_items]
    audio.append({'file': hum.as_posix(), 'start_ms': 0, 'volume': 0.22, 'loop': True})
    audio.append({'file': whirr.as_posix(), 'start_ms': round(rover_go * 1000 / fps), 'volume': 0.5, 'loop': False})
    speech = [{'actor': item['actor'], 'speaker': item['speaker'], 'from': item['from'], 'to': item['to']} for item in voice_items]

    kenney = lambda name: (KENNEY / name).as_posix()  # noqa: E731
    stage = {
        'version': STAGE_VERSION,
        'renderer': 'godot3d',
        'width': 1280,
        'height': 720,
        'fps': fps,
        'total_frames': total_frames,
        'title': str(doc.get('title', '')),
        'set': {
            'kind': 'lunar_observation_bay',
            'room': ROOM,
            'door': DOOR,
            'window': WINDOW,
            'camera_bounds': CAMERA_BOUNDS,
            'backdrop': {'mode': 'procedural', 'ground_y': -2.4, 'seed': 7, 'palette': loc2_palette(),
                         'palette_source': LOC2.relative_to(ROOT).as_posix() if LOC2.is_file() else 'defaults',
                         'planet': {'pos': [-34.0, 26.0, -330.0], 'radius': 62.0}},
            'props': [{**p, 'model': kenney(p['model'])} if 'model' in p else dict(p) for p in PROPS],
            'outside_props': [{**p, 'model': kenney(p['model'])} for p in OUTSIDE_PROPS],
            'lighting': LIGHTING,
            'marks': {k: _round(v) for k, v in marks.items()},
        },
        'actors': actors,
        'rover': rover,
        'props': props,
        'viewmodel': {
            'hand': [0.2, -0.2, -0.5], 'arm_dir': [-0.38, 0.36, -0.85], 'palm_hint': [-0.55, 0.25, 0.8],
            'sensor_up': [0.08, 1.0, 0.5], 'sensor_offset': [-0.004, 0.02, 0.018],
            **(doc.get('viewmodel') if isinstance(doc.get('viewmodel'), dict) else {}),
        },
        'shots': shots,
        'captions': captions,
        'speech': speech,
        'audio': audio,
        'meta': {
            'manifest': manifest_path.resolve().as_posix(),
            'story': str(doc.get('story', '')),
            'audio_source': str(doc.get('audio_source', 'manifest audio_tracks')),
            'walk_speed_mps': WALK_SPEED,
            'linlan_arrival_frame': arrival,
            'handoff_window': window,
            'rover_move': [rover_go, rover_arrive],
        },
    }
    problems = validate_stage3d(stage, check_files=check_files)
    if problems:
        raise ValueError('stage3d.json failed validation:\n' + '\n'.join(f'- {p}' for p in problems))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(stage, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    cast_summary = ','.join(f"{k}:{v['source']}" for k, v in actors.items())
    print(
        f"Prepared stage3d: {len(shots)} shots, {total_frames} frames ({total_frames / fps:.2f}s), "
        f"{len(captions)} captions, {len(audio)} audio tracks, cast={cast_summary}, rover={rover['source']}",
        flush=True,
    )
    return stage


# --------------------------------------------------------------- validate


def _inside_bounds(p, bounds: dict) -> bool:
    lo, hi = bounds['min'], bounds['max']
    if lo[0] - 1e-6 <= p[0] <= hi[0] + 1e-6 and lo[1] - 1e-6 <= p[1] <= hi[1] + 1e-6 and lo[2] - 1e-6 <= p[2] <= hi[2] + 1e-6:
        return True
    half = DOOR['width'] / 2 - 0.22
    return abs(p[0] - DOOR['x']) <= half and p[2] <= bounds['corridor_max_z'] and p[1] <= DOOR['height'] - 0.2


def validate_stage3d(stage: dict, check_files: bool = True) -> list[str]:
    problems: list[str] = []
    fps = int(stage.get('fps', 0))
    total = int(stage.get('total_frames', 0))
    shots = stage.get('shots') or []
    if stage.get('renderer') != 'godot3d' or stage.get('version') != STAGE_VERSION:
        problems.append('renderer/version must be godot3d v1')
    if [s.get('type') for s in shots] != list(SHOT_TYPES):
        problems.append('shots must be the six sample shot types in order')
    cursor = 0
    for shot in shots:
        if shot['from'] != cursor or shot['frames'] <= 0:
            problems.append(f"shot {shot.get('id')} is not contiguous")
        cursor = shot['from'] + shot['frames']
        for cam in shot.get('cameras') or []:
            for key in ('pos', 'pos_to'):
                if key in cam and not _inside_bounds(cam[key], stage['set']['camera_bounds']):
                    problems.append(f"shot {shot['id']} camera {key} {cam[key]} is outside the room")
    if cursor != total:
        problems.append('total_frames does not match the shots')
    if fps and not 20.0 <= total / fps <= 40.0:
        problems.append(f'sample length {total / fps:.1f}s is outside 20-40s')
    for cap in stage.get('captions') or []:
        if not 0 <= cap['from'] < cap['to'] <= total:
            problems.append(f"caption {cap.get('text')!r} falls outside the timeline")

    footprints = [(p['id'], prop_footprint(p)) for p in stage['set']['props']]
    known = [(pid, fp) for pid, fp in footprints if fp is not None]
    lo, hi = stage['set']['room']['min'], stage['set']['room']['max']
    for i, (pid, fp) in enumerate(known):
        for x, z in fp:
            if not (lo[0] <= x <= hi[0] and lo[2] <= z <= hi[2]):
                problems.append(f'prop {pid} pokes through a wall')
                break
        for other_id, other in known[i + 1:]:
            if footprints_overlap(fp, other, margin=0.05):
                problems.append(f'props {pid} and {other_id} overlap')

    def check_path(label: str, spec: dict, radius: float) -> None:
        points = [tuple(spec['start']['pos'])]
        for move in spec.get('moves') or []:
            points.extend(tuple(p) for p in move['points'])
        for p in sample_polyline(points) if len(points) > 1 else points:
            for pid, fp in known:
                if point_footprint_distance(p, fp) < radius:
                    problems.append(f'{label} path runs into prop {pid} at {tuple(round(v, 2) for v in p)}')
                    return

    for actor_id, spec in (stage.get('actors') or {}).items():
        check_path(actor_id, spec, ACTOR_RADIUS + 0.05)
        for move in spec.get('moves') or []:
            pts = [tuple(spec['start']['pos'])] + [tuple(p) for p in move['points']]
            length = sum(_dist(a, b) for a, b in zip(pts, pts[1:]))
            speed = length / max((move['to'] - move['from']) / fps, 1e-6)
            if not 0.5 <= speed <= 1.5:
                problems.append(f'{actor_id} walks at {speed:.2f} m/s')
    if stage.get('rover'):
        check_path('rover', stage['rover'], ROVER_RADIUS)
    if check_files:
        files = [stage['rover']['model']] if stage.get('rover') else []
        for spec in (stage.get('actors') or {}).values():
            files += [spec['model'], spec['animation']['library']]
        files += [p['model'] for p in stage['set']['props'] if 'model' in p]
        files += [a['file'] for a in stage.get('audio') or []]
        for path in files:
            if not Path(path).is_file():
                problems.append(f'missing file {path}')
    return problems


# --------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description='Build a godot3d stage3d.json from a manifest.')
    parser.add_argument('paths', nargs='*', type=Path, help='manifest.json [stage3d.json]')
    parser.add_argument('--manifest', type=Path, default=None)
    parser.add_argument('--output', type=Path, default=None)
    parser.add_argument('--demo-manifest', type=Path, default=None,
                        help='write the six-shot sample manifest (and voice it) to this path first')
    parser.add_argument('--no-tts', action='store_true', help='with --demo-manifest: skip edge-tts, use stand-in audio')
    args = parser.parse_args(argv)
    manifest = args.manifest or (args.paths[0] if args.paths else None)
    output = args.output or (args.paths[1] if len(args.paths) > 1 else None)
    if args.demo_manifest is not None:
        doc = write_demo_manifest(args.demo_manifest, synthesize_audio=not args.no_tts)
        print(f"Demo manifest: {args.demo_manifest} ({doc.get('audio_source', 'no audio')})", flush=True)
        manifest = manifest or args.demo_manifest
        if output is None:
            output = Path(args.demo_manifest).with_name('stage3d.json')
    if manifest is None or output is None:
        parser.error('need a manifest and an output path (positional or --manifest/--output)')
    prepare(manifest, output)


if __name__ == '__main__':
    main(sys.argv[1:])
