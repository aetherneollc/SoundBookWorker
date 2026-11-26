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
    from scene_layout import SceneLayoutSystem
    from cinematic_director import CinematicDirector
    from dramatic_director import DramaticDirector
    from performance_planner import PerformancePlanner
    from retro_art_profile import RetroArtProfile
    from semantic_animation_library import SemanticAnimationLibrary
    from sound_events import SoundEvent
except ImportError:  # ``python -m unittest`` loads this module as part of the package.
    from renderer.timeline_common import build_voice_locked_timeline, frame, shot_duration_frames
    from renderer.scene_layout import SceneLayoutSystem
    from renderer.cinematic_director import CinematicDirector
    from renderer.dramatic_director import DramaticDirector
    from renderer.performance_planner import PerformancePlanner
    from renderer.retro_art_profile import RetroArtProfile
    from renderer.semantic_animation_library import SemanticAnimationLibrary
    from renderer.sound_events import SoundEvent

ROOT = Path(__file__).resolve().parents[1]
CC0 = ROOT / 'media' / 'assets3d' / 'cc0'
KENNEY = CC0 / 'kenney_space_kit'
UAL = CC0 / 'quaternius_universal_animation_library'
UAL2 = CC0 / 'quaternius_universal_animation_library_2' / 'UAL2_Standard.glb'
DRAMATIC_PACK = ROOT / 'media' / 'assets3d' / 'animations' / 'ps2_dramatic_pack.glb'
UBC = CC0 / 'quaternius_universal_base_characters'
LOC2 = ROOT / 'public' / 'media' / 'loc2-plate.png'
TEXTURES = ROOT / 'media' / 'assets3d' / 'textures'
# Painted horizon of lunar_panorama*.png, as a fraction of the image height from the top.
PANORAMA_HORIZON_V = 0.852
DEMO_DIR = ROOT / 'media' / 'm6_3d'
INTERACT_AUDIO = ROOT / 'media' / 'm6_interact' / 'audio'

STAGE_VERSION = 1
SHOT_TYPES = ('wide_follow', 'two_shot', 'over_shoulder', 'rover_medium', 'first_person', 'closeup')
_LOCO_BEAT_ACTIONS = frozenset({
    'walk', 'move', 'enter', 'exit', 'run', 'jog', 'sprint',
    'rush_enter', 'door_enter', 'door_exit', 'sprint_burst', 'jog_approach',
    'jog_urgent', 'walk_urgent', 'pace_tense',
})

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
# Two clean silhouettes just outside the glass give the painted cyclorama
# foreground parallax: the comms dish on the left and an exterior catwalk.
OUTSIDE_PROPS = [
    {'id': 'dish', 'model': 'satelliteDish_large.glb', 'pos': [-8.8, -12.5], 'yaw_deg': 30.0, 'scale': 4.2},
    {'id': 'catwalk', 'kind': 'catwalk', 'pos': [1.5, -12.0], 'length': 24.0, 'depth': 1.3, 'deck_y': -0.15},
]
# Cool moonlight through the window as key/rim, warm-neutral ceiling strips,
# and soft fills on the faces and the console.
LIGHTING = {
    'ambient': '#9fb0c8',
    'ambient_energy': 0.58,
    'sun_energy': 0.95,
    'sun_color': '#d2e0ff',
    'sun_dir': [-0.38, -0.5, 0.78],
    'lamp_energy': 0.68,
    'lamp_color': '#ffeedd',
    'fills': [
        {'id': 'ConsoleGlow', 'pos': [-3.1, 1.5, -3.5], 'range': 3.0, 'energy': 0.5, 'color': '#8fe6ff'},
        {'id': 'FaceFill', 'pos': [-1.2, 1.9, -1.2], 'range': 4.0, 'energy': 0.4, 'color': '#fff0dc'},
        {'id': 'WindowSpill', 'pos': [0.0, 2.2, -3.9], 'range': 6.5, 'energy': 0.35, 'color': '#b9d0ff'},
    ],
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


REVIEW_STILLS = {
    'wide_follow': 'shot1_wide', 'two_shot': 'shot2_twoshot', 'over_shoulder': 'shot3_handoff',
    'rover_medium': 'shot4_rover', 'first_person': 'shot5_fps', 'closeup': 'shot6_closeup',
}


def review_frames(shots: list[dict], handoff_window: list[int], linlan_walk: dict) -> dict:
    """One still per shot, timed so it shows the shot's point: mid-stride in
    the follow, mid-line in the dialogue shots (the close-up on the middle of
    Linlan's last line, so face, mouth shape and caption are checked
    together), the middle of the handoff, plus four frames of one stride."""
    out: dict = {}
    for shot in shots:
        start, end = shot['from'], shot['from'] + shot['frames'] - 1
        kind = shot['type']
        lines = shot.get('lines') or []
        if kind == 'wide_follow':
            walk_end = min(end, linlan_walk['to'])
            frame_index = (max(start, linlan_walk['from']) + walk_end) // 2
        elif kind == 'over_shoulder':
            frame_index = (handoff_window[0] + handoff_window[1]) // 2
        elif lines:
            line = lines[-1] if kind == 'closeup' else lines[0]
            frame_index = (line['from'] + line['to']) // 2
        else:
            frame_index = (start + end) // 2
        out[REVIEW_STILLS[kind]] = int(min(max(frame_index, start), end))
    walk_mid = out['shot1_wide']
    out['walk_strip'] = [walk_mid - 6, walk_mid - 2, walk_mid + 2, walk_mid + 6]
    return out


def backdrop_config(textures: Path = TEXTURES) -> dict:
    """The view out of the window: the painted lunar panorama on a curved
    cyclorama when it exists (the cleaned copy first), else the procedural
    moonscape coloured from the LOC2 plate."""
    for name in ('lunar_panorama_clean.png', 'lunar_panorama.png'):
        image = textures / name
        if image.is_file():
            return {'mode': 'cyclorama', 'image': image.as_posix(), 'radius': 100.0, 'arc_deg': 140.0,
                    'center': [0.0, 0.0], 'horizon_v': PANORAMA_HORIZON_V, 'horizon_y': 1.45,
                    'ground_y': -2.4, 'near_ground': '#34486b'}
    return {'mode': 'procedural', 'ground_y': -2.4, 'seed': 7, 'palette': loc2_palette(),
            'palette_source': LOC2.relative_to(ROOT).as_posix() if LOC2.is_file() else 'defaults',
            'planet': {'pos': [-34.0, 26.0, -330.0], 'radius': 62.0}}


def surface_config(textures: Path = TEXTURES) -> dict:
    """Painted interior surfaces that exist on disk: deck tiles every 2.5 m,
    bulkhead panels every 3 m, and the console's telemetry screen."""
    surfaces: dict = {}
    if (textures / 'bay_floor.png').is_file():
        surfaces['floor'] = {'texture': (textures / 'bay_floor.png').as_posix(), 'tile_m': 2.5,
                             'roughness': 0.65, 'metallic': 0.1, 'specular': 0.5, 'tint': '#e6eaf0'}
    if (textures / 'bay_wall.png').is_file():
        surfaces['wall'] = {'texture': (textures / 'bay_wall.png').as_posix(), 'tile_m': 3.0,
                            'roughness': 0.78, 'specular': 0.35, 'tint': '#ffffff', 'low_tint': '#b8c0cc'}
    if (textures / 'console_screen.png').is_file():
        surfaces['console_screen'] = {'texture': (textures / 'console_screen.png').as_posix(),
                                      'pos': [-3.5, 1.36, -4.0], 'size': [0.94, 0.53], 'tilt_deg': 8.0,
                                      'emission': 0.65, 'stand_bottom_y': 0.97}
    return surfaces


# ------------------------------------------------------------------- audio


def _write_wav(path: Path, samples: list[float], rate: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = b''.join(struct.pack('<h', max(-32767, min(32767, int(s * 32767)))) for s in samples)
    with wave.open(str(path), 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(data)


def speech_energy(path: str | Path, frames: int, fps: int) -> list[float] | None:
    """Per-frame loudness of a voice line in [0, 1] (RMS, normalised to the line's 95th percentile).

    Drives mouth shapes in the player. Returns None for anything that is not
    a readable 16-bit PCM wav, so the player falls back to a timed cadence.
    """
    if frames <= 0:
        return []
    try:
        with wave.open(str(path), 'rb') as handle:
            if handle.getsampwidth() != 2:
                return None
            channels = handle.getnchannels()
            rate = handle.getframerate()
            raw = handle.readframes(handle.getnframes())
    except (OSError, EOFError, wave.Error):
        return None
    count = len(raw) // 2
    samples = struct.unpack(f'<{count}h', raw[:count * 2])
    if channels > 1:
        samples = samples[::channels]
    hop = rate / fps
    rms: list[float] = []
    for k in range(frames):
        a, b = int(k * hop), int((k + 1) * hop)
        window = samples[a:b]
        rms.append(math.sqrt(sum(s * s for s in window) / len(window)) if window else 0.0)
    ranked = sorted(rms)
    peak = ranked[min(len(ranked) - 1, int(len(ranked) * 0.95))]
    if peak <= 1e-6:
        return [0.0] * frames
    return [round(min(1.0, v / peak), 3) for v in rms]


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


def _load_registry_assets() -> list[dict]:
    reg_path = ROOT / 'media' / 'assets3d' / 'registry.json'
    if not reg_path.is_file():
        return []
    try:
        data = json.loads(reg_path.read_text(encoding='utf-8'))
        assets = data.get('assets')
        return assets if isinstance(assets, list) else []
    except Exception:
        return []


def _lookup_asset_capabilities(actor_id: str, model_path: Path) -> tuple[str | None, dict]:
    assets = _load_registry_assets()
    model_name = model_path.name
    matched = None
    for item in assets:
        if isinstance(item, dict) and Path(str(item.get('path') or '')).name == model_name:
            matched = item
            break
    if matched is None:
        for item in assets:
            if isinstance(item, dict) and item.get('asset_id') == actor_id:
                if Path(str(item.get('path') or '')).name == model_name:
                    matched = item
                    break
    if not isinstance(matched, dict):
        return None, {}
    tier = matched.get('tier')
    caps = dict(matched.get('capabilities') or {})
    if tier:
        caps.setdefault('tier', tier)
    return str(tier) if tier else None, caps


def _cast_entry(actor_id: str, cast_doc: dict, manifest_dir: Path) -> dict:
    base = DEFAULT_CAST.get(actor_id) or {
        'name': actor_id,
        'height_m': 1.70,
        'model': UBC / 'Superhero_Male_FullBody.gltf',
        'look': {**UNIFORM, 'ponytail': False, 'badge': False},
    }
    entry = cast_doc.get(actor_id) if isinstance(cast_doc.get(actor_id), dict) else {}
    generated = _resolve_file(entry.get('model'), manifest_dir)
    model_path = Path(generated or base['model'])
    tier, caps = _lookup_asset_capabilities(actor_id, model_path)
    look = {k: (v.as_posix() if isinstance(v, Path) else v) for k, v in base['look'].items()}
    if generated is not None:
        look = {**{k: look[k] for k in ('jacket', 'collar') if k in look}, 'uniform': False, 'ponytail': False, 'badge': False}
        if tier == 'TIER_A' or caps.get('blendshapes'):
            look['preserve_materials'] = True
    look.update(entry.get('look') or {})
    animation = {
        'library': Path(entry.get('animation_library') or ANIMATION['library']).as_posix(),
        'root_motion_library': Path(ANIMATION['root_motion_library']).as_posix(),
        'sources': list(entry.get('animation_sources') or ANIMATION['sources']),
    }
    extra_libraries = _extra_animation_libraries(entry)
    if extra_libraries:
        animation['extra_libraries'] = extra_libraries
    # A rig's own walk (Meshy basic_animations) was authored on that mesh, so it beats a retarget.
    walk = entry.get('walk_clip') if generated is not None and isinstance(entry.get('walk_clip'), dict) else None
    walk_library = _resolve_file(walk.get('library'), manifest_dir) if walk else None
    if walk_library is not None:
        animation['clip_sources'] = {'walk': {'library': walk_library.as_posix(), 'clip': str(walk.get('clip', ''))}}
    default_gait = 'confident' if actor_id == 'linlan' else ('heavy' if actor_id == 'chen' else 'casual')
    out = {
        'name': str(entry.get('name') or base['name']),
        'source': 'fal_generated' if generated is not None else 'cc0_fallback',
        'fallback_reason': '' if generated is not None else str(entry.get('fallback_reason') or 'no generated model in manifest'),
        'model': model_path.as_posix(),
        'height_m': float(entry.get('height_m') or base['height_m']),
        'gait': str(entry.get('gait') or default_gait),
        'animation': animation,
        'look': look,
    }
    if tier:
        out['tier'] = tier
    if caps:
        out['capabilities'] = caps
    return out


def _extra_animation_libraries(entry: dict | None = None) -> list[str]:
    """Real clip packs Godot registers by exact name. Missing files are skipped."""
    paths: list[str] = []
    if isinstance(entry, dict):
        raw = entry.get('extra_libraries') or entry.get('animation_extra_libraries') or []
        if isinstance(raw, str):
            raw = [raw]
        if isinstance(raw, dict):
            raw = list(raw.values())
        for item in raw:
            if isinstance(item, dict):
                item = item.get('path') or item.get('library') or ''
            text = str(item or '').strip()
            if text:
                paths.append(Path(text).as_posix())
    if not paths:
        for candidate in (DRAMATIC_PACK, UAL2):
            if candidate.is_file():
                paths.append(candidate.as_posix())
    seen: set[str] = set()
    unique: list[str] = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


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
    if doc.get('mode') == 'generic' or doc.get('scene_type') is not None:
        return prepare_generic(doc, manifest_path, output_path, check_files=check_files)
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
            'pov_required': bool(shot.get('pov_required', False)),
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
        'moves': [{'from': walk_start, 'to': arrival, 'points': [_round(mid), _round(linlan)], 'ramp_s': 0.5, 'gait': linlan_cfg.get('gait', 'confident')}],
        'facing': [
            {'from': 0, 'target': 'chen'},
            {'from': s4['from'] + frame(0.5, fps), 'target': ROVER['id']},
            {'from': s5['from'] - frame(0.6, fps), 'point': _round(marks['window_view'])},
            {'from': s6['from'] - frame(0.4, fps), 'target': 'chen'},
        ],
        'emotions': [
            {'from': 0, 'to': s2_end if False else s3['from'], 'emotion': 'worried', 'intensity': 0.65},
            {'from': s3['from'], 'to': s5['from'], 'emotion': 'neutral', 'intensity': 0.8},
            {'from': s5['from'], 'to': s6['from'], 'emotion': 'worried', 'intensity': 0.55},
            {'from': s6['from'], 'to': total_frames, 'emotion': 'confident', 'intensity': 0.95},
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
        'moves': [{'from': chen_step, 'to': chen_step + frame(_dist(chen, marks['chen_console']) / 0.72, fps),
                   'points': [_round(marks['chen_console'])], 'ramp_s': 0.14, 'gait': chen_cfg.get('gait', 'heavy')}],
        'facing': [
            {'from': 0, 'point': _round(marks['console'])},
            {'from': max(0, arrival - frame(1.6, fps)), 'target': 'linlan'},
            {'from': s4['from'] + frame(0.7, fps), 'target': ROVER['id']},
            {'from': s5['from'], 'point': _round(marks['chen_console'])},
            {'from': chen_step + frame(_dist(chen, marks['chen_console']) / 0.72, fps) + 2, 'target': 'linlan'},
        ],
        'emotions': [
            {'from': 0, 'to': s4['from'], 'emotion': 'worried', 'intensity': 0.72},
            {'from': s4['from'], 'to': total_frames, 'emotion': 'neutral', 'intensity': 0.75},
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
            # Inside-bay front-3/4 tracking dolly: never blocked by the corridor
            # doorframe, frames Linlan's confident stride from door toward console.
            mid = n // 2
            cams = [
                {'from': 0, 'to': mid, 'mode': 'dolly',
                 'pos': [0.85, 1.72, 2.35], 'pos_to': [1.35, 1.72, 1.65],
                 'look_at': [0.0, 1.24, 4.70], 'look_to': [-0.50, 1.24, 1.40],
                 'fov': 52.0, 'fov_to': 50.0},
                {'from': mid, 'to': n, 'mode': 'dolly',
                 'pos': [1.35, 1.72, 1.65], 'pos_to': [0.35, 1.66, 0.75],
                 'look_at': [-0.50, 1.24, 1.40], 'look_to': [-2.15, 1.24, -2.35],
                 'fov': 50.0, 'fov_to': 46.0},
            ]
            shot['primary_subject'] = 'linlan'
            shot['secondary_subject'] = 'chen'
        elif kind == 'two_shot':
            cams = [{'from': 0, 'to': n, 'mode': 'dolly',
                     'pos': _round((mid_pair[0] + room_side[0] * 3.2, 1.5, mid_pair[1] + room_side[1] * 3.2)),
                     'pos_to': _round((mid_pair[0] + room_side[0] * 2.8, 1.46, mid_pair[1] + room_side[1] * 2.8)),
                     'look_at': _round((mid_pair[0], 1.28, mid_pair[1])),
                     'look_to': _round((mid_pair[0], 1.3, mid_pair[1])),
                     'fov': 40.0, 'fov_to': 40.0}]
            shot['primary_subject'] = 'chen'
            shot['secondary_subject'] = 'linlan'
        elif kind == 'over_shoulder':
            base = (linlan[0] - a[0] * 1.45 + right_of_linlan[0] * 0.88, linlan[1] - a[1] * 1.45 + right_of_linlan[1] * 0.88)
            look = (chen[0] * 0.58 + linlan[0] * 0.42, chen[1] * 0.58 + linlan[1] * 0.42)
            cams = [{'from': 0, 'to': n, 'mode': 'dolly',
                     'pos': _round((base[0], 1.68, base[1])),
                     'pos_to': _round((base[0] + a[0] * 0.15, 1.66, base[1] + a[1] * 0.15)),
                     'look_at': _round((look[0], 1.16, look[1])), 'look_to': _round((look[0], 1.12, look[1])),
                     'fov': 34.0, 'fov_to': 33.0}]
            shot['primary_subject'] = 'chen'
            shot['secondary_subject'] = 'linlan'
        elif kind == 'rover_medium':
            stop = marks['rover_stop']
            cams = [{'from': 0, 'to': n, 'mode': 'dolly',
                     'pos': [1.05, 1.02, -0.75], 'pos_to': [1.15, 1.0, -0.95],
                     'look_at': _round((stop[0] + 1.7, 0.5, stop[1] + 0.85)), 'look_to': _round((stop[0] + 0.1, 0.5, stop[1])),
                     'fov': 42.0, 'fov_to': 40.0}]
            shot['primary_subject'] = ROVER['id']
            shot['secondary_subject'] = 'linlan'
        elif kind == 'first_person':
            far = (linlan[0] + view_dir[0] * 9.0, linlan[1] + view_dir[1] * 9.0)
            if bool(shot.get('pov_required', False)):
                cams = [{'from': 0, 'to': n, 'mode': 'pov', 'actor': 'linlan', 'eye_height': 1.55,
                         'look_at': _round((far[0] - 0.8, 1.75, far[1])), 'look_to': _round((far[0] + 0.6, 2.85, far[1])),
                         'fov': 60.0, 'sway': 0.005}]
                shot['first_person'] = 'linlan'
            else:
                # BUG-004: Default away from gratuitous FPS game camera; frame a cinematic
                # over-the-shoulder / prop close-up showing Linlan's real 3D body, real hand,
                # and the held spare sensor looking out toward the lunar window & storm front.
                cam_p0 = (linlan[0] + 0.72, 1.50, linlan[1] + 1.18)
                cam_p1 = (linlan[0] + 0.58, 1.48, linlan[1] + 0.96)
                look_0 = (linlan[0] - 0.25, 1.38, far[1])
                look_1 = (linlan[0] + 0.10, 1.72, far[1])
                cams = [{'from': 0, 'to': n, 'mode': 'dolly',
                         'pos': _round(cam_p0), 'pos_to': _round(cam_p1),
                         'look_at': _round(look_0), 'look_to': _round(look_1),
                         'fov': 46.0, 'fov_to': 44.0}]
                shot['first_person'] = ''
            shot['primary_subject'] = 'linlan'
            shot['secondary_subject'] = 'sensor'
        else:
            cams = [{'from': 0, 'to': n, 'mode': 'dolly',
                     'pos': _round((linlan[0] + closeup_dir[0] * 1.18, 1.48, linlan[1] + closeup_dir[1] * 1.18)),
                     'pos_to': _round((linlan[0] + closeup_dir[0] * 1.06, 1.47, linlan[1] + closeup_dir[1] * 1.06)),
                     'look_at': _round((linlan[0], 1.42, linlan[1])), 'look_to': _round((linlan[0], 1.43, linlan[1])),
                     'fov': 35.0, 'fov_to': 35.0,
                     'dof_enabled': True, 'dof_focus_distance': 1.12}]
            shot['primary_subject'] = 'linlan'
            shot['secondary_subject'] = 'chen'
            shot['first_person'] = ''
        shot['cameras'] = cams
        if 'first_person' not in shot:
            shot['first_person'] = ''

    # ---- audio bed
    audio_dir = output_path.resolve().parent / 'audio'
    hum = write_bay_hum(audio_dir / 'bay_hum.wav')
    whirr = write_rover_whirr(audio_dir / 'rover_whirr.wav', rover_travel + 0.4)
    audio = [{'file': item['file'], 'start_ms': item['start_ms'], 'volume': item['volume'], 'loop': False}
             for item in voice_items]
    audio.append({'file': hum.as_posix(), 'start_ms': 0, 'volume': 0.22, 'loop': True})
    audio.append({'file': whirr.as_posix(), 'start_ms': round(rover_go * 1000 / fps), 'volume': 0.5, 'loop': False})
    speech = []
    for item in voice_items:
        entry = {'actor': item['actor'], 'speaker': item['speaker'], 'from': item['from'], 'to': item['to']}
        energy = speech_energy(item['file'], item['to'] - item['from'], fps)
        if energy is not None:
            entry['energy'] = energy
        speech.append(entry)

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
            'scene_type': 'observation_bay',
            'lighting_profile': str(doc.get('lighting_profile') or 'day_bay'),
            'kind': 'lunar_observation_bay',
            'room': ROOM,
            'door': DOOR,
            'window': WINDOW,
            'camera_bounds': CAMERA_BOUNDS,
            'backdrop': backdrop_config(),
            'surfaces': surface_config(),
            'props': [{**p, 'model': kenney(p['model'])} if 'model' in p else dict(p) for p in PROPS],
            'outside_props': [{**p, 'model': kenney(p['model'])} if 'model' in p else dict(p) for p in OUTSIDE_PROPS],
            'lighting': LIGHTING,
            'marks': {k: _round(v) for k, v in marks.items()},
        },
        'actors': actors,
        'rover': rover,
        'props': props,
        'viewmodel': {
            'hand': [0.2, -0.165, -0.5], 'arm_dir': [-0.38, 0.36, -0.85], 'palm_hint': [-0.55, 0.25, 0.8],
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
            'review_frames': review_frames(shots, window, linlan_walk=actors['linlan']['moves'][0]),
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


# ------------------------------------------------------- generic multi-scene


_ONE_SHOT_CLIPS = {
    'handoff_offer', 'handoff_receive', 'insert_module', 'button_press',
    'slam_table', 'abrupt_halt', 'turn_around', 'exhale_relief',
    'pick_up', 'pickup', 'interact', 'panel_open',
}


def _copy_clip(entry: dict) -> dict:
    """Keep timeline clip fields, including an explicit one-shot flag."""
    out = dict(entry)
    if 'one_shot' in entry:
        out['one_shot'] = bool(entry['one_shot'])
    elif str(entry.get('clip') or '').strip().lower() in _ONE_SHOT_CLIPS:
        out['one_shot'] = True
    return out


def _normalize_interactive_prop(pspec: dict) -> dict:
    """Pass handoff / install timelines through without dropping legacy prop keys."""
    copied = dict(pspec)
    timeline = []
    source = copied.get('timeline', copied.get('actions', []))
    if isinstance(source, list):
        timeline = [dict(item) if isinstance(item, dict) else item for item in source]

    def _has(action_name: str) -> bool:
        return any(
            isinstance(item, dict) and str(item.get('action') or item.get('state') or '').lower() == action_name
            for item in timeline
        )

    handoff = copied.get('handoff')
    if isinstance(handoff, dict):
        copied['handoff'] = dict(handoff)
        if not _has('handoff'):
            row = dict(handoff)
            row['action'] = 'handoff'
            if 'from' not in row:
                row['from'] = int(row.get('start_frame', 0))
            if 'to' not in row:
                end_frame = int(row.get('end_frame', row.get('release_frame', row['from'])))
                row['to'] = end_frame + 1
            timeline.append(row)
    install = copied.get('install_into_socket')
    if isinstance(install, dict):
        copied['install_into_socket'] = dict(install)
        if not _has('install_into_socket'):
            row = dict(install)
            row['action'] = 'install_into_socket'
            if 'from' not in row:
                row['from'] = int(row.get('start_frame', 0))
            if 'to' not in row:
                end_frame = int(row.get('withdraw_end_frame', row.get('online_frame', row.get('lock_frame', row['from']))))
                row['to'] = end_frame + 1
            timeline.append(row)
    if timeline and (
        isinstance(copied.get('timeline'), list)
        or isinstance(copied.get('actions'), list)
        or isinstance(handoff, dict)
        or isinstance(install, dict)
    ):
        copied['timeline'] = timeline
    return copied


def _passthrough_events(raw_events) -> list[dict]:
    """Normalize manifest events into the stage3d event contract."""
    out: list[dict] = []
    if not isinstance(raw_events, list):
        return out
    for item in raw_events:
        if isinstance(item, str):
            item = {'type': item}
        if not isinstance(item, dict):
            continue
        kind = str(item.get('type') or item.get('event') or '').strip()
        if not kind or kind == 'none':
            continue
        start = int(item.get('from', 0))
        end = int(item.get('to', start if item.get('to') is not None else start + 1))
        if end <= start:
            end = start + 1
        out.append({
            'type': kind,
            'from': start,
            'to': end,
            'intensity': float(item.get('intensity', 0.6)),
            'target': str(item.get('target') or ''),
        })
    return out


def _dedupe_events(events: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out: list[dict] = []
    for event in events:
        key = (event.get('type'), int(event.get('from', 0)), str(event.get('target') or ''))
        if key in seen:
            continue
        seen.add(key)
        out.append(event)
    return out


def _align_performance_source(source: list, derived: list[dict]) -> list[dict]:
    rows = [dict(item) for item in source if isinstance(item, dict)]
    if len(rows) != len(derived):
        return rows
    for row, span in zip(rows, derived):
        if row.get('from') is None and row.get('from_frame') is None:
            row['from'] = span['from']
            row['to'] = span['to']
        row.setdefault('actor', span.get('actor', ''))
        row.setdefault('partner', span.get('partner', ''))
        row.setdefault('duration', span.get('duration', 3.0))
        row.setdefault('emotion', span.get('emotion', 'neutral'))
        row.setdefault('action', span.get('action', 'talk'))
    return rows


def _apply_retro_layer(
    stage: dict,
    doc: dict,
    planned,
    planner,
    audio_dir: Path,
    fps: int,
) -> None:
    """Attach art, events, dramatic actions, and foley without touching unrelated manifests."""
    if 'art_profile' in doc:
        stage['art_profile'] = RetroArtProfile().resolve(doc['art_profile'])
    want_events = 'events' in doc or planned is not None
    if want_events:
        merged = _passthrough_events(doc.get('events'))
        if planned is not None and planner is not None:
            merged.extend(planner.environment_events(planned))
        stage['events'] = _dedupe_events(merged)
    if planned is not None and planner is not None:
        actions = planner.compile_dramatic_actions(planned, fps)
        for actor_id, entries in actions.items():
            if actor_id in stage['actors']:
                stage['actors'][actor_id]['dramatic_actions'] = entries
    sound_spec = doc.get('sound_events', None)
    want_audio = 'sound_events' in doc and sound_spec not in (None, False)
    if want_audio:
        synth = SoundEvent(audio_dir)
        extra: list[dict] = []
        authored = sound_spec if isinstance(sound_spec, list) else []
        for index, spec in enumerate(authored):
            if not isinstance(spec, dict):
                continue
            kind = str(spec.get('kind') or spec.get('type') or '').strip()
            if not kind:
                continue
            frame_at = int(spec.get('frame', spec.get('from', 0)))
            extra.append(synth.timeline_entry(
                kind,
                frame=frame_at,
                fps=fps,
                volume=spec.get('volume'),
                loop=bool(spec.get('loop', False)),
                seed=int(spec.get('seed', 50 + index + frame_at)),
                duration_s=spec.get('duration_s'),
            ))
        if planned is not None:
            extra.extend(synth.entries_for_performance(planned, fps))
        stage['audio'].extend(extra)
    _append_chained_actions(stage, doc, planner)
    _stamp_authored_playback(stage, doc, fps)


def _append_chained_actions(stage: dict, doc: dict, planner) -> None:
    """Split a beat so a second real clip plays in the latter portion.

    ``chain_action`` is optional. Manifests that omit it are unchanged.
    """
    if planner is None:
        return
    beats = [b for b in (doc.get('beats') or doc.get('shots') or []) if isinstance(b, dict)]
    shots = [s for s in (stage.get('shots') or []) if isinstance(s, dict)]
    if len(beats) != len(shots):
        return
    library = getattr(planner, 'library', None)
    by_id = getattr(library, 'by_id', {}) if library is not None else {}
    for beat, shot in zip(beats, shots):
        chain = str(beat.get('chain_action') or '').strip()
        if not chain or chain not in by_id:
            continue
        actor = str(beat.get('speaker') or beat.get('actor') or '')
        actors = stage.get('actors') or {}
        if actor not in actors:
            continue
        entries = actors[actor].setdefault('dramatic_actions', [])
        start = int(shot.get('from') or 0)
        end = start + max(1, int(shot.get('frames') or 1))
        portion = min(0.8, max(0.2, float(beat.get('chain_at', 0.55))))
        split = start + int(round((end - start) * portion))
        split = max(start + 6, min(end - 6, split))
        primary = str(beat.get('action') or '')
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if str(entry.get('action_id') or '') != primary:
                continue
            entry_from = int(entry.get('from', -1))
            entry_to = int(entry.get('to', -1))
            if entry_from >= start - 1 and entry_to > split and entry_from < end:
                entry['to'] = split
        entries.append(planner._stage_action(
            chain,
            split,
            end,
            float(beat.get('intensity') or 0.8),
            str(beat.get('target') or ''),
        ))
        entries.sort(key=lambda item: (int(item.get('from', 0)), str(item.get('action_id') or '')))


def _stamp_authored_playback(stage: dict, doc: dict, fps: int) -> None:
    """Copy manifest raw_playback and exact clip names onto dramatic actions.

    Manifests that omit the fields are unchanged. The player may not honor
    raw_playback yet; the names are still written for that flag.
    """
    beats = [b for b in (doc.get('beats') or doc.get('shots') or []) if isinstance(b, dict)]
    if not beats or not any(b.get('raw_playback') or b.get('dedicated_clip') for b in beats):
        return
    windows: list[tuple[int, int, dict]] = []
    cursor = 0
    for beat in beats:
        frames = int(beat.get('frames') or shot_duration_frames(float(beat.get('duration') or 3.0), fps))
        windows.append((cursor, cursor + frames, beat))
        cursor += frames
    actors = stage.get('actors') or {}
    if not isinstance(actors, dict):
        return
    for actor in actors.values():
        if not isinstance(actor, dict):
            continue
        for entry in actor.get('dramatic_actions') or []:
            if not isinstance(entry, dict):
                continue
            start = int(entry.get('from', 0))
            beat = next((item for lo, hi, item in windows if lo <= start < hi), None)
            if beat is None:
                continue
            if not beat.get('raw_playback') and not beat.get('dedicated_clip'):
                continue
            chain_name = str(beat.get('chain_action') or '')
            action_id = str(entry.get('action_id') or '')
            if chain_name and action_id == chain_name:
                clip = str(beat.get('chain_dedicated_clip') or '')
                if 'chain_one_shot' in beat:
                    entry['one_shot'] = bool(beat.get('chain_one_shot'))
            else:
                clip = str(beat.get('dedicated_clip') or '')
                if 'one_shot' in beat:
                    entry['one_shot'] = bool(beat.get('one_shot'))
            if beat.get('raw_playback'):
                entry['raw_playback'] = True
            if clip:
                entry['dedicated_clip'] = clip
                entry['base_clip'] = clip


def prepare_generic(doc: dict, manifest_path: Path, output_path: Path, check_files: bool = True) -> dict:
    """Reusable multi-scene compiler using SceneLayoutSystem + CinematicDirector.

    Zero per-episode hardcoding: compiles any supported ``scene_type``
    (observation_bay, corridor, med_lab, command_deck), any ``lighting_profile``,
    any cast/gait/emotion/prop timeline, and any beats into a validated ``stage3d.json``.
    """
    fps = int(doc.get('fps', 24))
    manifest_dir = manifest_path.resolve().parent
    scene_type = str(doc.get('scene_type') or 'observation_bay')
    lighting_profile = str(doc.get('lighting_profile') or '') or None
    solver = SceneLayoutSystem(kenney_dir=KENNEY, textures_dir=TEXTURES)
    extra_props = doc.get('extra_props') if isinstance(doc.get('extra_props'), list) else None
    layout = solver.solve(
        scene_type,
        lighting_profile=lighting_profile,
        extra_props=extra_props,
        validate=True,
    )
    set_doc = layout.to_stage3d_set(resolve_asset_paths=True)
    door_start = str(doc.get('door_start') or '').strip().lower()
    if door_start in ('open', 'closed'):
        set_doc['door_start'] = door_start
    anchors = {aid: (a.position[0], a.position[2]) for aid, a in layout.standing_anchors.items()}

    def _resolve_xz(raw) -> tuple[float, float]:
        if isinstance(raw, str) and raw in anchors:
            return anchors[raw]
        if isinstance(raw, (list, tuple)) and len(raw) >= 2:
            return (float(raw[0]), float(raw[-1]))
        return next(iter(anchors.values())) if anchors else (0.0, 0.0)

    # ---- actors
    cast_doc = doc.get('cast') if isinstance(doc.get('cast'), dict) else {}
    actors: dict[str, dict] = {}
    actor_tiers: dict[str, dict] = {}
    actor_heights: dict[str, float] = {}
    for actor_id, raw_spec in cast_doc.items():
        if not isinstance(raw_spec, dict) or raw_spec.get('kind') in ('rover', 'prop'):
            continue
        cfg = _cast_entry(actor_id, cast_doc, manifest_dir)
        start_pos = _resolve_xz(raw_spec.get('start_anchor') or raw_spec.get('start_pos'))
        start_yaw = float(raw_spec.get('yaw_deg', 0.0))
        moves = []
        cur_xz = start_pos
        for mv in raw_spec.get('moves') or []:
            if not isinstance(mv, dict):
                continue
            pts = []
            for p in mv.get('points') or ([] if 'to_anchor' not in mv else [mv['to_anchor']]):
                dest = _resolve_xz(p)
                if mv.get('auto_path', False):
                    path_pts = layout.find_path(cur_xz, dest)
                    if path_pts and len(path_pts) > 1:
                        pts.extend([_round(q) for q in path_pts[1:]])
                        cur_xz = dest
                        continue
                pts.append(_round(dest))
                cur_xz = dest
            if pts:
                moves.append({
                    'from': int(mv.get('from', 0)),
                    'to': int(mv.get('to', 48)),
                    'points': pts,
                    'ramp_s': float(mv.get('ramp_s', 0.4)),
                    'gait': str(mv.get('gait') or cfg.get('gait', 'casual')),
                })
        cfg.update({
            'start': {'pos': _round(start_pos), 'yaw_deg': start_yaw},
            'moves': moves,
            'facing': list(raw_spec.get('facing') or []),
            'emotions': list(raw_spec.get('emotions') or []),
            'clips': [_copy_clip(c) for c in (raw_spec.get('clips') or []) if isinstance(c, dict)],
            'reaches': list(raw_spec.get('reaches') or []),
            'nods': list(raw_spec.get('nods') or []),
        })
        if 'raw_playback' in raw_spec:
            cfg['raw_playback'] = bool(raw_spec.get('raw_playback'))
        authored_actions = raw_spec.get('dramatic_actions')
        if isinstance(authored_actions, list):
            cfg['dramatic_actions'] = [dict(item) for item in authored_actions if isinstance(item, dict)]
        actors[actor_id] = cfg
        actor_heights[actor_id] = float(cfg['height_m'])
        actor_tiers[actor_id] = {
            'tier': cfg.get('tier', 'TIER_A'),
            'blendshapes': bool((cfg.get('capabilities') or {}).get('blendshapes', True)),
        }

    # ---- audio & captions & speech
    audio_dir = output_path.resolve().parent / 'audio'
    hum = write_bay_hum(audio_dir / 'ambient_hum.wav')
    captions: list[dict] = []
    speech: list[dict] = []
    audio: list[dict] = [{'file': hum.as_posix(), 'start_ms': 0, 'volume': 0.22, 'loop': True}]

    beats_in = [dict(b) for b in (doc.get('beats') or doc.get('shots') or []) if isinstance(b, dict)]
    if not beats_in:
        raise ValueError('Generic manifest requires non-empty beats or shots')

    # Helper to compute actor XZ position at a given frame for director planning.
    def _actor_pos_at(aid: str, f_idx: int) -> tuple[float, float, float]:
        spec = actors[aid]
        px, pz = float(spec['start']['pos'][0]), float(spec['start']['pos'][1])
        for mv in spec.get('moves') or []:
            f0, f1 = int(mv['from']), max(int(mv['to']), int(mv['from']) + 1)
            if f_idx < f0:
                break
            pts = [(px, pz)] + [(float(p[0]), float(p[1])) for p in mv['points']]
            if f_idx >= f1:
                px, pz = pts[-1]
                continue
            u = (f_idx - f0) / (f1 - f0)
            seg_len = sum(_dist(a, b) for a, b in zip(pts, pts[1:]))
            along = u * seg_len
            for a_p, b_p in zip(pts, pts[1:]):
                d = _dist(a_p, b_p)
                if along <= d or d < 1e-6:
                    k = max(0.0, min(1.0, along / max(d, 1e-6)))
                    px = a_p[0] + (b_p[0] - a_p[0]) * k
                    pz = a_p[1] + (b_p[1] - a_p[1]) * k
                    break
                along -= d
            break
        return (round(px, 3), 0.0, round(pz, 3))

    def _actor_facing_at(aid: str, f_idx: int, pos_map: dict) -> list[float]:
        spec = actors[aid]
        p_self = pos_map[aid]
        best = None
        for entry in spec.get('facing') or []:
            if int(entry.get('from', 0)) <= f_idx:
                best = entry
        if isinstance(best, dict):
            tgt = best.get('target')
            if isinstance(tgt, str) and tgt in pos_map and tgt != aid:
                p_o = pos_map[tgt]
                dx, dz = p_o[0] - p_self[0], p_o[2] - p_self[2]
                nx, nz = _norm((dx, dz))
                return [round(nx, 3), round(nz, 3)]
            if 'yaw_deg' in best:
                r = math.radians(float(best['yaw_deg']))
                return [round(math.sin(r), 3), round(math.cos(r), 3)]
        r = math.radians(float(spec['start'].get('yaw_deg', 0.0)))
        return [round(math.sin(r), 3), round(math.cos(r), 3)]

    use_dramatic = bool(doc.get('dramatic_director'))
    want_performance = use_dramatic or 'performance_beats' in doc
    director = DramaticDirector(fps=fps) if use_dramatic else CinematicDirector(fps=fps)
    director_beats: list[dict] = []
    perf_rows: list[dict] = []
    cursor = 0
    for idx, b in enumerate(beats_in, 1):
        dur = max(1.0, float(b.get('duration', 3.5)))
        b_frames = int(b.get('frames') or shot_duration_frames(dur, fps))
        mid_f = cursor + b_frames // 2
        end_f = cursor + b_frames - 1
        pos_start = {aid: list(_actor_pos_at(aid, cursor)) for aid in actors}
        pos_mid = {aid: list(_actor_pos_at(aid, mid_f)) for aid in actors}
        pos_end = {aid: list(_actor_pos_at(aid, end_f)) for aid in actors}
        facings = {aid: _actor_facing_at(aid, mid_f, pos_mid) for aid in actors}

        speaker = str(b.get('speaker') or '')
        listener = str(b.get('listener') or '')
        text = str(b.get('text') or b.get('caption_text') or b.get('caption') or '')
        speak = b.get('speak', True)
        if isinstance(speak, str):
            speak = speak.strip().lower() not in {'0', 'false', 'no', 'off'}
        else:
            speak = bool(speak)
        if text and not speak:
            captions.append({'from': cursor, 'to': cursor + b_frames, 'text': text})
        elif text:
            lead_f = min(frame(float(b.get('voice_lead', 0.25)), fps), max(0, b_frames - 12))
            line_from = cursor + lead_f
            line_to = max(line_from + 12, cursor + b_frames - 4)
            captions.append({'from': line_from, 'to': line_to, 'text': text})
            line_wav = _resolve_file(b.get('audio_file'), manifest_dir)
            if line_wav is None:
                line_wav = write_silence(audio_dir / f'beat_{idx}.wav', (line_to - line_from) / fps)
            audio.append({
                'file': line_wav.as_posix(),
                'start_ms': round(line_from * 1000 / fps),
                'volume': 1.0,
                'loop': False,
            })
            sp_entry: dict = {
                'actor': speaker,
                'speaker': actors[speaker]['name'] if speaker in actors else speaker,
                'from': line_from,
                'to': line_to,
            }
            if b.get('emotion'):
                sp_entry['emotion'] = str(b['emotion'])
            if b.get('listener_emotion'):
                sp_entry['listener_emotion'] = str(b['listener_emotion'])
            energy = speech_energy(line_wav, line_to - line_from, fps)
            if energy is not None and max(energy, default=0.0) > 0.05:
                sp_entry['energy'] = energy
            speech.append(sp_entry)

        actor_trajs = {}
        for aid in actors:
            pts_u = []
            for k in range(6):
                u = k / 5.0
                f_sample = cursor + int(round((b_frames - 1) * u))
                px, py, pz = _actor_pos_at(aid, f_sample)
                pts_u.append([round(u, 3), [round(px, 3), round(py, 3), round(pz, 3)]])
            actor_trajs[aid] = pts_u

        director_beats.append({
            'beat_id': str(b.get('id') or b.get('beat_id') or f'b{idx}'),
            'scene_number': int(b.get('scene_number', idx)),
            'duration': b_frames / fps,
            'frames': b_frames,
            'speaker': speaker,
            'listener': listener,
            'emotion': str(b.get('emotion') or 'neutral'),
            'action': str(b.get('action') or 'talk'),
            'narrative_importance': float(b.get('importance', 0.65)),
            'preferred_shot': b.get('preferred_shot') or b.get('shot_type'),
            'pov_required': bool(b.get('pov_required', False)),
            'prop_event': b.get('prop_event'),
            'prop_position': b.get('prop_position'),
            'actor_positions': pos_start if str(b.get('action') or '').strip().lower() in _LOCO_BEAT_ACTIONS else pos_mid,
            'actor_end_positions': pos_end,
            'actor_trajectories': actor_trajs,
            'actor_facings': facings,
            'actor_heights': actor_heights,
            'description': str(b.get('description') or ''),
            'event_focus': b.get('event_focus') or None,
            'environment_event': b.get('environment_event') or '',
            'hold': bool(b.get('hold') or False),
        })
        talkish = str(b.get('action') or 'talk').strip().lower() in ('talk', 'hold', 'listen', 'speak', 'dialogue', '')
        perf_rows.append({
            'beat_id': str(b.get('id') or b.get('beat_id') or f'b{idx}'),
            'actor': speaker,
            'partner': listener,
            'from': cursor,
            'to': cursor + b_frames,
            'duration': b_frames / fps,
            'action': str(b.get('action') or 'talk'),
            'reaction': b.get('reaction') or '',
            'emotion': str(b.get('emotion') or 'neutral'),
            'intensity': float(b.get('intensity', b.get('importance', 0.6)) or 0.6),
            'objective': str(b.get('objective') or b.get('description') or ''),
            'conflict': str(b.get('conflict') or ''),
            'target': str(b.get('target') or listener or ''),
            'blocking': str(b.get('blocking') or ('face_to_face' if talkish else '')),
            'environment_event': b.get('environment_event') or '',
            'camera_intent': str(b.get('camera_intent') or ''),
            'sound_event': str(b.get('sound_event') or ''),
            'text': text,
            'hold': bool(b.get('hold') or False),
        })
        cursor += b_frames

    planned = None
    planner = None
    if want_performance:
        library = SemanticAnimationLibrary()
        planner = PerformancePlanner(library)
        source_rows = doc['performance_beats'] if isinstance(doc.get('performance_beats'), list) else perf_rows
        planned = planner.plan({
            'scene_type': scene_type,
            'fps': fps,
            'beats': _align_performance_source(source_rows, perf_rows),
        })
        if use_dramatic and len(planned) == len(director_beats):
            for director_beat, performance_beat in zip(director_beats, planned):
                director_beat['action'] = performance_beat.action
                director_beat['emotion'] = performance_beat.emotion
                director_beat['camera_intent'] = performance_beat.camera_intent
                director_beat['environment_event'] = performance_beat.environment_event
                director_beat['intensity'] = performance_beat.intensity
                director_beat['narrative_importance'] = max(
                    float(director_beat.get('narrative_importance') or 0.0),
                    performance_beat.intensity,
                )
                if performance_beat.hold:
                    director_beat['hold'] = True

    shots = director.plan_sequence(
        director_beats,
        layout=layout,
        actor_tiers=actor_tiers,
        enable_cutaways=bool(doc.get('enable_cutaways', True)),
        reset=True,
    )
    locked = doc.get('locked_camera') if isinstance(doc.get('locked_camera'), dict) else None
    if locked and isinstance(locked.get('pos'), (list, tuple)) and isinstance(locked.get('look_at'), (list, tuple)):
        pos = [round(float(v), 3) for v in list(locked['pos'])[:3]]
        look = [round(float(v), 3) for v in list(locked['look_at'])[:3]]
        fov = float(locked.get('fov') or 40.0)
        for shot in shots:
            n = int(shot['frames'])
            shot['cameras'] = [{
                'from': 0,
                'to': n,
                'mode': 'static',
                'pos': pos,
                'pos_to': list(pos),
                'look_at': look,
                'look_to': list(look),
                'fov': fov,
                'fov_to': fov,
                'dof_enabled': False,
            }]
            shot['first_person'] = ''
    total_frames = sum(int(s['frames']) for s in shots)

    # Interactive props
    props_out: dict = {}
    for pid, pspec in (doc.get('props') or {}).items():
        if not isinstance(pspec, dict):
            continue
        p_copy = _normalize_interactive_prop(pspec)
        resolved_m = _resolve_file(p_copy.get('model'), manifest_dir)
        if resolved_m is not None:
            p_copy['model'] = resolved_m.as_posix()
            if 'grip_point' not in p_copy:
                p_copy['grip_point'] = sensor_grip_point(resolved_m)
        props_out[pid] = p_copy

    review = {}
    if isinstance(doc.get('review_frames'), dict):
        for key, value in doc['review_frames'].items():
            try:
                frame_index = int(value)
            except (TypeError, ValueError):
                continue
            if frame_index >= 0:
                review[str(key)] = frame_index
    else:
        for idx, sh in enumerate(shots, 1):
            review[f"shot{idx}_{sh['type']}"] = int(sh['from'] + sh['frames'] // 2)

    stage = {
        'version': STAGE_VERSION,
        'renderer': 'godot3d',
        'mode': 'generic',
        'width': 1280,
        'height': 720,
        'fps': fps,
        'total_frames': total_frames,
        'title': str(doc.get('title') or f'{scene_type}_{layout.lighting_profile}'),
        'set': set_doc,
        'actors': actors,
        'props': props_out,
        'shots': shots,
        'captions': captions,
        'speech': speech,
        'audio': audio,
        'meta': {
            'manifest': manifest_path.resolve().as_posix(),
            'story': str(doc.get('story') or scene_type),
            'scene_type': scene_type,
            'lighting_profile': layout.lighting_profile,
            'review_frames': review,
        },
    }
    if isinstance(doc.get('interaction_audit'), dict):
        stage['interaction_audit'] = dict(doc['interaction_audit'])

    if isinstance(doc.get('rover'), dict):
        r_copy = dict(doc['rover'])
        rm = _resolve_file(r_copy.get('model'), manifest_dir)
        if rm is not None:
            r_copy['model'] = rm.as_posix()
        stage['rover'] = r_copy

    if any(key in doc for key in ('art_profile', 'dramatic_director', 'performance_beats', 'events', 'sound_events')):
        _apply_retro_layer(stage, doc, planned, planner, audio_dir, fps)

    problems = validate_stage3d(stage, check_files=check_files)
    if problems:
        raise ValueError('Generic stage3d.json failed validation:\n' + '\n'.join(f'- {p}' for p in problems))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(stage, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(
        f"Prepared generic stage3d ({scene_type}/{layout.lighting_profile}): {len(shots)} shots, "
        f"{total_frames} frames ({total_frames / fps:.2f}s), cast={','.join(actors.keys())}",
        flush=True,
    )
    return stage


# --------------------------------------------------------------- validate


def _inside_bounds(p, bounds: dict, door: dict | None = None) -> bool:
    lo, hi = bounds['min'], bounds['max']
    if lo[0] - 1e-6 <= p[0] <= hi[0] + 1e-6 and lo[1] - 1e-6 <= p[1] <= hi[1] + 1e-6 and lo[2] - 1e-6 <= p[2] <= hi[2] + 1e-6:
        return True
    d = door if isinstance(door, dict) else DOOR
    half = float(d.get('width', 1.6)) / 2 - 0.22
    return abs(p[0] - float(d.get('x', 0.0))) <= half and p[2] <= float(bounds.get('corridor_max_z', hi[2])) and p[1] <= float(d.get('height', 2.4)) - 0.2


def validate_stage3d(stage: dict, check_files: bool = True) -> list[str]:
    problems: list[str] = []
    fps = int(stage.get('fps', 0))
    total = int(stage.get('total_frames', 0))
    shots = stage.get('shots') or []
    is_generic = stage.get('mode') == 'generic'
    if stage.get('renderer') != 'godot3d' or stage.get('version') != STAGE_VERSION:
        problems.append('renderer/version must be godot3d v1')
    if not is_generic and [s.get('type') for s in shots] != list(SHOT_TYPES):
        problems.append('shots must be the six sample shot types in order')
    if is_generic and not shots:
        problems.append('generic stage must contain at least one shot')
    cursor = 0
    door = stage.get('set', {}).get('door')
    for shot in shots:
        if shot['from'] != cursor or shot['frames'] <= 0:
            problems.append(f"shot {shot.get('id')} is not contiguous")
        cursor = shot['from'] + shot['frames']
        for cam in shot.get('cameras') or []:
            for key in ('pos', 'pos_to'):
                if key in cam and not _inside_bounds(cam[key], stage['set']['camera_bounds'], door):
                    problems.append(f"shot {shot['id']} camera {key} {cam[key]} is outside the room")
    if cursor != total:
        problems.append('total_frames does not match the shots')
    if fps and not is_generic and not 20.0 <= total / fps <= 40.0:
        problems.append(f'sample length {total / fps:.1f}s is outside 20-40s')
    for cap in stage.get('captions') or []:
        if not 0 <= cap['from'] < cap['to'] <= total:
            problems.append(f"caption {cap.get('text')!r} falls outside the timeline")

    footprints = [(p['id'], prop_footprint(p)) for p in stage['set']['props']]
    known = [(pid, fp) for pid, fp in footprints if fp is not None]
    lo, hi = stage['set']['room']['min'], stage['set']['room']['max']
    if not is_generic:
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

    if not is_generic:
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
        files = [stage['rover']['model']] if stage.get('rover') and stage['rover'].get('model') else []
        for spec in (stage.get('actors') or {}).values():
            files += [spec['model'], spec['animation']['library']]
        files += [p['model'] for p in stage['set']['props'] if p.get('model')]
        files += [a['file'] for a in stage.get('audio') or [] if a.get('file')]
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
