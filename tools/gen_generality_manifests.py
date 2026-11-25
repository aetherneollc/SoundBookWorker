#!/usr/bin/env python3
"""Write three short generic episodes for corridor, med lab, and command deck.

Actors, walks, and the sensor rest pose use SceneLayoutSystem anchor names.
No camera coordinates are authored; CinematicDirector plans every shot from
``preferred_shot`` plus the solved blocking.

    python tools/gen_generality_manifests.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from renderer.scene_layout import SceneLayout, SceneLayoutSystem

FPS = 24
WIDTH = 1280
HEIGHT = 720
OUT_ROOT = ROOT / 'media' / 'm6_3d' / 'generality'

MODELS = {
    'linlan': 'media/assets3d/characters/linlan.glb',
    'chen': 'media/assets3d/characters/chen.glb',
    'director_zhou': 'media/assets3d/characters/director_zhou.glb',
    'dr_su': 'media/assets3d/characters/dr_su.glb',
}
SENSOR_MODEL = 'media/assets3d/sensor/sensor_norm.glb'
NAMES = {
    'linlan': '林岚',
    'chen': '陈工',
    'director_zhou': '周主任',
    'dr_su': '苏医生',
}


def shot_frames(seconds: float, fps: int = FPS) -> int:
    """Match ``renderer.timeline_common.shot_duration_frames``."""
    clamped = min(300.0, max(0.25, float(seconds)))
    return max(1, max(0, round(clamped * fps)))


def frame_starts(durations: tuple[float, ...]) -> tuple[list[int], int]:
    cursor = 0
    starts: list[int] = []
    for seconds in durations:
        starts.append(cursor)
        cursor += shot_frames(seconds)
    return starts, cursor


def require_asset(relative: str) -> str:
    path = ROOT / relative
    if not path.is_file():
        raise SystemExit(f'missing asset {relative}')
    return relative.replace('\\', '/')


def standing(layout: SceneLayout, anchor_id: str):
    anchor = layout.standing_anchors.get(anchor_id)
    if anchor is None:
        known = ', '.join(sorted(layout.standing_anchors))
        raise SystemExit(f'{layout.scene_type} has no standing anchor {anchor_id!r} (known: {known})')
    return anchor


def yaw_of(layout: SceneLayout, anchor_id: str) -> float:
    return round(float(standing(layout, anchor_id).facing_yaw_deg), 2)


def anchor_xz(layout: SceneLayout, anchor_id: str) -> tuple[float, float]:
    anchor = standing(layout, anchor_id)
    return float(anchor.position[0]), float(anchor.position[2])


def travel_frames(layout: SceneLayout, start_id: str, goal_id: str, speed_mps: float) -> int:
    """Frames to walk a solved clear path at ``speed_mps``."""
    start = anchor_xz(layout, start_id)
    goal = anchor_xz(layout, goal_id)
    path = layout.find_path(start, goal) or [start, goal]
    length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(path, path[1:]))
    return max(12, shot_frames(length / max(float(speed_mps), 0.2)))


def surface_rest(layout: SceneLayout, anchor_id: str) -> dict:
    """Rest pose copied from a solved surface anchor (not a camera)."""
    surf = layout.surface_anchors.get(anchor_id)
    if surf is None:
        known = ', '.join(sorted(layout.surface_anchors))
        raise SystemExit(f'{layout.scene_type} has no surface anchor {anchor_id!r} (known: {known})')
    return {
        'anchor': anchor_id,
        'pos': [round(float(surf.position[0]), 3), round(float(surf.position[2]), 3)],
        'surface_y': round(float(surf.surface_y), 4),
        'yaw_deg': 0.0,
    }


def interaction_point(layout: SceneLayout, src_id: str, dst_id: str, reach_m: float, height_m: float) -> list[float]:
    """Point ``reach_m`` from ``src`` toward ``dst``, for a prop-event focus."""
    ax, az = anchor_xz(layout, src_id)
    bx, bz = anchor_xz(layout, dst_id)
    dx, dz = bx - ax, bz - az
    length = math.hypot(dx, dz)
    if length < 1e-6:
        return [round(ax, 3), height_m, round(az, 3)]
    step = min(float(reach_m), length * 0.5) / length
    return [round(ax + dx * step, 3), height_m, round(az + dz * step, 3)]


def walk(start: int, end: int, anchor_id: str, gait: str, ramp_s: float = 0.4) -> dict:
    return {
        'from': int(start),
        'to': int(end),
        'to_anchor': anchor_id,
        'auto_path': True,
        'ramp_s': ramp_s,
        'gait': gait,
    }


def emotion(start: int, end: int, name: str, intensity: float) -> dict:
    return {'from': int(start), 'to': int(end), 'emotion': name, 'intensity': round(float(intensity), 2)}


def face(start: int, target: str) -> dict:
    return {'from': int(start), 'target': target}


def actor(
    layout: SceneLayout,
    actor_id: str,
    *,
    gait: str,
    height: float,
    start_anchor: str,
    facing: list[dict],
    emotions: list[dict],
    moves: list[dict] | None = None,
) -> dict:
    spec = {
        'name': NAMES[actor_id],
        'model': require_asset(MODELS[actor_id]),
        'height_m': height,
        'gait': gait,
        'start_anchor': start_anchor,
        'yaw_deg': yaw_of(layout, start_anchor),
        'facing': facing,
        'emotions': emotions,
    }
    if moves:
        spec['moves'] = moves
    return spec


def beat(beat_id: str, scene: int, duration: float, **fields: object) -> dict:
    doc: dict = {'id': beat_id, 'scene_number': scene, 'duration': duration}
    for key, value in fields.items():
        if value is not None:
            doc[key] = value
    return doc


def envelope(*, title: str, story: str, scene_type: str, lighting: str, cast: dict, beats: list[dict], props: dict | None = None) -> dict:
    doc = {
        'version': 1,
        'mode': 'generic',
        'renderer': 'godot3d',
        'language': 'zh',
        'title': title,
        'story': story,
        'width': WIDTH,
        'height': HEIGHT,
        'fps': FPS,
        'scene_type': scene_type,
        'lighting_profile': lighting,
        'cast': cast,
        'beats': beats,
        # prepare() requires shots before the generic dispatch; the compiler reads beats first.
        'shots': beats,
    }
    if props:
        doc['props'] = props
    seconds = sum(shot_frames(float(item['duration'])) for item in beats) / FPS
    if not 8.0 <= seconds <= 10.0:
        raise SystemExit(f'{title} is {seconds:.2f}s; generality clips must be 8-10s')
    return doc


def test_corridor(layout: SceneLayout) -> dict:
    """Zhou strides the dim corridor and confronts Linlan. ~9.0s."""
    durations = (4.0, 2.75, 2.25)
    starts, total = frame_starts(durations)
    zhou = 'director_zhou'
    lin = 'linlan'
    cast = {
        zhou: actor(
            layout, zhou, gait='heavy', height=1.80, start_anchor='entry_threshold',
            facing=[face(0, lin)],
            emotions=[emotion(0, total, 'angry', 0.9)],
            moves=[walk(0, starts[1] - 6, 'center_a', 'urgent', ramp_s=0.72)],
        ),
        lin: actor(
            layout, lin, gait='cautious', height=1.65, start_anchor='center_b',
            facing=[face(0, zhou)],
            emotions=[emotion(0, total, 'worried', 0.8)],
        ),
    }
    beats = [
        beat(
            'approach', 1, durations[0],
            speaker=zhou, listener=lin, emotion='angry', listener_emotion='worried',
            action='walk', importance=0.7, preferred_shot='tracking',
            description='Zhou strides the dim corridor toward Linlan.',
            text='林岚，站住。',
        ),
        beat(
            'confront', 2, durations[1],
            speaker=zhou, listener=lin, emotion='angry', listener_emotion='worried',
            action='talk', importance=0.85, preferred_shot='over_the_shoulder',
            description='Zhou confronts Linlan about the missed report.',
            text='信号断了这么久，你为什么不报？',
        ),
        beat(
            'reply', 3, durations[2],
            speaker=lin, listener=zhou, emotion='worried', listener_emotion='angry',
            action='talk', importance=0.8, preferred_shot='close_up',
            description='Linlan answers, worried.',
            text='我在确认来源，周主任。',
        ),
    ]
    return envelope(
        title='昏暗走廊对质',
        story='Zhou strides down a dim corridor and confronts Linlan.',
        scene_type='corridor',
        lighting='dim_corridor',
        cast=cast,
        beats=beats,
    )


def test_medlab(layout: SceneLayout) -> dict:
    """Dr. Su inspects a sensor in the med lab and hands it to Chen. ~9.0s."""
    durations = (3.0, 3.25, 2.75)
    starts, total = frame_starts(durations)
    doctor = 'dr_su'
    assistant = 'chen'
    inspect_end = starts[2]
    handoff_end = inspect_end + shot_frames(2.25)
    transfer = inspect_end + (handoff_end - inspect_end) // 2
    doc_x, doc_z = anchor_xz(layout, 'exam_doctor')
    handoff_spot = [round(doc_x - 0.92, 3), round(doc_z - 0.12, 3)]
    cast = {
        doctor: actor(
            layout, doctor, gait='feminine', height=1.62, start_anchor='exam_doctor',
            facing=[face(0, assistant)],
            emotions=[
                emotion(0, starts[1], 'happy', 0.8),
                emotion(starts[1], total, 'confident', 0.85),
            ],
        ),
        assistant: actor(
            layout, assistant, gait='casual', height=1.76, start_anchor='exam_assistant',
            facing=[face(0, doctor)],
            emotions=[emotion(0, total, 'neutral', 0.45)],
            moves=[{
                'from': starts[1] + 2,
                'to': starts[2] - 12,
                'points': [handoff_spot],
                'ramp_s': 0.30,
                'gait': 'casual',
            }],
        ),
    }
    beats = [
        beat(
            'reading', 1, durations[0],
            speaker=doctor, listener=assistant, emotion='happy', listener_emotion='neutral',
            action='talk', importance=0.65, preferred_shot='two_shot',
            description='Dr. Su and Chen in the bright med lab.',
            text='陈工，这块传感器的读数不对。',
        ),
        beat(
            'inspect', 2, durations[1],
            speaker=doctor, listener=assistant, emotion='confident', listener_emotion='neutral',
            action='inspect', importance=0.8, preferred_shot='medium',
            prop_event={
                'prop_id': 'spare_sensor',
                'event': 'inspect',
                'position': interaction_point(layout, 'exam_doctor', 'exam_assistant', 0.42, 1.14),
            },
            description='Medium shot on Dr. Su inspecting the sensor as Chen steps in.',
            text='外壳完好，问题在校准。',
        ),
        beat(
            'handoff', 3, durations[2],
            speaker=doctor, listener=assistant, emotion='confident', listener_emotion='neutral',
            action='handoff', importance=0.85, preferred_shot='two_shot',
            prop_event={
                'prop_id': 'spare_sensor',
                'event': 'handoff',
                'position': [round((doc_x + handoff_spot[0]) * 0.5, 3), 1.16, round((doc_z + handoff_spot[1]) * 0.5, 3)],
            },
            description='Two-shot showing Dr. Su handing the spare sensor cleanly to Chen above the subtitle band.',
            text='拿去，换上这块备件。',
        ),
    ]
    bed_rest = surface_rest(layout, 'exam_bed_main_top')
    props = {
        # BUG-002 #11: Explicitly distinct entity IDs & ownership for faulty vs spare sensor.
        'faulty_sensor': {
            'kind': 'sensor',
            'entity_role': 'faulty_sensor',
            'model': require_asset(SENSOR_MODEL),
            'surface': {
                **bed_rest,
                'pos': [round(bed_rest['pos'][0] - 0.28, 3), round(bed_rest['pos'][1] + 0.12, 3)],
                'yaw_deg': 28.0,
            },
            'timeline': [
                {'from': 0, 'to': total, 'action': 'surface'},
            ],
        },
        'spare_sensor': {
            'kind': 'sensor',
            'entity_role': 'spare_sensor',
            'model': require_asset(SENSOR_MODEL),
            'surface': bed_rest,
            'timeline': [
                {'from': 0, 'to': inspect_end, 'action': 'inspect', 'actor': doctor, 'side': 'right'},
                {
                    'from': inspect_end,
                    'to': handoff_end,
                    'action': 'handoff',
                    'from_actor': doctor,
                    'to_actor': assistant,
                    'transfer_frame': transfer,
                    'height': 1.18,
                    'ramp_s': 0.35,
                },
                {'from': handoff_end, 'to': total, 'action': 'held', 'actor': assistant, 'side': 'right'},
            ],
        },
    }
    return envelope(
        title='医疗舱传感器交接',
        story='Dr. Su inspects a sensor in the med lab and hands it to Chen.',
        scene_type='med_lab',
        lighting='med_lab',
        cast=cast,
        beats=beats,
        props=props,
    )


def test_command(layout: SceneLayout) -> dict:
    """Red-alert briefing around the command holo-table. ~9.5s."""
    durations = (3.5, 3.0, 3.0)
    starts, total = frame_starts(durations)
    commander = 'director_zhou'
    specialist = 'dr_su'
    officer = 'chen'
    arrival = min(10 + travel_frames(layout, 'entry_threshold', 'command_south', 0.95), starts[1])
    cast = {
        commander: actor(
            layout, commander, gait='confident', height=1.80, start_anchor='command_north',
            facing=[face(0, officer), face(starts[1], specialist), face(starts[2], officer)],
            emotions=[emotion(0, total, 'angry', 0.9)],
        ),
        specialist: actor(
            layout, specialist, gait='feminine', height=1.62, start_anchor='command_west',
            facing=[face(0, commander), face(starts[2], officer)],
            emotions=[
                emotion(0, starts[1], 'worried', 0.6),
                emotion(starts[1], total, 'confident', 0.85),
            ],
        ),
        officer: actor(
            layout, officer, gait='casual', height=1.76, start_anchor='entry_threshold',
            facing=[face(0, commander)],
            emotions=[
                emotion(0, starts[2], 'neutral', 0.4),
                emotion(starts[2], total, 'worried', 0.85),
            ],
            moves=[walk(10, arrival, 'command_south', 'casual', ramp_s=0.16)],
        ),
    }
    beats = [
        beat(
            'alert', 1, durations[0],
            speaker=commander, listener=specialist, emotion='angry', listener_emotion='worried',
            action='talk', importance=0.9, preferred_shot='establishing',
            description='Red-alert wide shot as Chen reaches the holo-table.',
            text='红色警报。全员到指挥台。',
        ),
        beat(
            'briefing', 2, durations[1],
            speaker=specialist, listener=commander, emotion='confident', listener_emotion='angry',
            action='talk', importance=0.8, preferred_shot='over_the_shoulder',
            description='Dr. Su gives the tactical read.',
            text='生命维持稳定，战术窗口还有三分钟。',
        ),
        beat(
            'concern', 3, durations[2],
            speaker=officer, listener=commander, emotion='worried', listener_emotion='angry',
            action='talk', importance=0.75, preferred_shot='close_up',
            description='Close-up on Chen under night alert.',
            text='外围通道还没清空，我担心有人没撤出来。',
        ),
    ]
    return envelope(
        title='红色警报战术简报',
        story='Red-alert tactical briefing around the command holo-table.',
        scene_type='command_deck',
        lighting='night_alert',
        cast=cast,
        beats=beats,
    )


def write_manifest(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    frames = sum(shot_frames(float(item['duration'])) for item in doc['beats'])
    shots = ', '.join(str(item.get('preferred_shot')) for item in doc['beats'])
    rel = path.relative_to(ROOT).as_posix()
    print(f'Wrote {rel} ({frames / FPS:.2f}s, {shots})', flush=True)


def main() -> int:
    for relative in (*MODELS.values(), SENSOR_MODEL):
        require_asset(relative)
    solver = SceneLayoutSystem()
    episodes = (
        (OUT_ROOT / 'test1_corridor' / 'manifest.json', test_corridor(solver.solve('corridor', lighting_profile='dim_corridor'))),
        (OUT_ROOT / 'test2_medlab' / 'manifest.json', test_medlab(solver.solve('med_lab', lighting_profile='med_lab'))),
        (OUT_ROOT / 'test3_command' / 'manifest.json', test_command(solver.solve('command_deck', lighting_profile='night_alert'))),
    )
    for path, doc in episodes:
        write_manifest(path, doc)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
