#!/usr/bin/env python3
"""Phase-1 retro A/B cut: the lunar observation bay crisis, about 24 seconds.

Version A and Version B share one story, one clock, one TTS pass, and the same
director beats. Version B adds the industrial art profile and the foley bed.

    python tools/gen_retro_ab_demo.py
"""
from __future__ import annotations

import json
import math
import struct
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import gen_generality_manifests as gg
from renderer.scene_layout import SceneLayoutSystem
from renderer.synthesize import synthesize_edge_file

OUT = ROOT / 'media' / 'm6_3d' / 'retro_ab'
FPS = 24
VOICES = {
    'linlan': 'zh-CN-XiaoxiaoNeural',
    'chen': 'zh-CN-YunxiNeural',
}
ROVER_MODEL = 'media/assets3d/atuo/atuo_rigged.glb'
LINES = (
    ('linlan', '南侧阵列彻底掉了！备件呢？'),
    ('chen', '最后一块！风暴提前了四十分钟！'),
    ('linlan', '阵列锁定。我们撑过去了。'),
)


def _wav_seconds(path: Path) -> float:
    with wave.open(str(path), 'rb') as handle:
        rate = handle.getframerate() or 24000
        frames = handle.getnframes()
    return frames / max(rate, 1)


def _fallback_voice(path: Path, seconds: float = 1.8) -> None:
    """Offline stand-in with syllable pulses so lip-sync still has energy."""
    rate = 24000
    count = max(1, int(seconds * rate))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        frames = bytearray()
        for index in range(count):
            t = index / rate
            syllable = 0.5 + 0.5 * math.sin(t * 2.0 * math.pi * 3.2)
            burst = syllable if (index // int(rate * 0.09)) % 2 == 0 else 0.25
            sample = math.sin(2.0 * math.pi * 170.0 * t) * burst * 0.42
            frames += struct.pack('<h', int(max(-32767, min(32767, sample * 32767))))
        handle.writeframes(frames)


def _voice_line(audio_dir: Path, index: int, speaker: str, text: str) -> tuple[Path, float, str]:
    path = audio_dir / f'line_{index:02d}.wav'
    if path.is_file() and path.stat().st_size > 8000:
        return path, _wav_seconds(path), 'edge-tts-cached'
    voice = VOICES[speaker]
    try:
        synthesize_edge_file(text, voice, '+8%', path, speaker=speaker)
        return path, _wav_seconds(path), 'edge-tts'
    except Exception as exc:
        print(f'edge-tts unavailable for {speaker!r} ({exc}); writing a pulsed fallback', flush=True)
        _fallback_voice(path, 1.9)
        return path, _wav_seconds(path), 'fallback'


def _fit(planned: float, spoken: float, cap: float) -> float:
    need = spoken + 0.5 if spoken > 0.0 else planned
    return round(min(cap, max(planned, need)), 2)


def _path_meters(points: list[tuple[float, float]]) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:]))


def _clip_path(points: list[tuple[float, float]], max_meters: float) -> list[tuple[float, float]]:
    if len(points) < 2 or max_meters <= 0.2:
        return points[:1] or [(0.0, 0.0)]
    kept = [points[0]]
    remain = max_meters
    for start, end in zip(points, points[1:]):
        seg = math.hypot(end[0] - start[0], end[1] - start[1])
        if seg <= remain + 1e-6:
            kept.append(end)
            remain -= seg
            continue
        if remain > 0.25 and seg > 1e-6:
            scale = remain / seg
            kept.append((start[0] + (end[0] - start[0]) * scale, start[1] + (end[1] - start[1]) * scale))
        break
    return kept


def _round_pt(point: tuple[float, float]) -> list[float]:
    return [round(point[0], 3), round(point[1], 3)]


def _event(kind: str, start: int, end: int, intensity: float, target: str) -> dict:
    return {
        'type': kind,
        'from': int(start),
        'to': int(end),
        'intensity': intensity,
        'target': target,
    }


def _build(audio: list[tuple[Path, float]]) -> tuple[dict, dict]:
    layout = SceneLayoutSystem().solve('observation_bay', lighting_profile='day_bay', validate=True)
    line_a, line_b, line_c = audio
    entry = (0.0, 2.15)
    partner = gg.anchor_xz(layout, 'workstation_partner')
    approach = layout.find_path(entry, partner) or [entry, partner]
    approach_m = _path_meters(approach)
    rush_s = round(max(2.2, approach_m / 2.65 + 0.35), 2)
    durations = [
        rush_s,
        0.75,
        _fit(3.2, line_a[1], 4.8),
        _fit(2.8, line_b[1], 4.8),
        3.0,
        3.4,
        3.2,
        _fit(3.0, line_c[1], 4.8),
    ]
    total = sum(gg.shot_frames(item) for item in durations) / FPS
    if total > 26.0:
        scale = 26.0 / total
        for index in (0, 4, 5, 6):
            durations[index] = round(max(1.6, durations[index] * scale), 2)
    total = sum(gg.shot_frames(item) for item in durations) / FPS
    if total < 22.0:
        durations[7] = round(durations[7] + (22.0 - total), 2)
    starts, total_frames = gg.frame_starts(tuple(durations))
    enter_from = 4
    enter_to = max(enter_from + 18, starts[1] - 4)
    h0 = starts[4]
    h1 = starts[5]
    transfer = h0 + max(16, (h1 - h0) // 2)
    lin_x, lin_z = gg.anchor_xz(layout, 'workstation_operator')
    chen_x, chen_z = partner
    meet = [
        round((lin_x + chen_x) * 0.5, 3),
        1.22,
        round((lin_z + chen_z) * 0.5, 3),
    ]

    beats = [
        gg.beat(
            'b1_rush', 1, durations[0],
            speaker='chen', listener='linlan', emotion='urgent', listener_emotion='angry',
            action='rush_enter', reaction='snap_look_back',
            camera_intent='action_track', importance=0.72, intensity=0.78,
            blocking='bursts through the bulkhead with the spare sensor',
            environment_event=_event('door_open', starts[0], starts[1], 0.9, 'door'),
            target='door', objective='Chen rushes the spare into the bay',
            conflict='The door and the storm are both closing in',
            description='Overhead lights flicker as Chen bursts in with the spare sensor.',
        ),
        gg.beat(
            'b1_halt', 1, durations[1],
            speaker='chen', listener='linlan', emotion='urgent', listener_emotion='angry',
            action='abrupt_halt', reaction='snap_look_back',
            camera_intent='impact_opening', importance=0.7, intensity=0.74,
            blocking='plants his feet inside the doorway',
            environment_event=_event('power_flicker', starts[0], starts[2], 0.85, 'lights'),
            target='linlan', objective='Chen stops short of the console',
            conflict='The array is already dead',
            description='Chen halts as the storm flashes beyond the window.',
        ),
        gg.beat(
            'b2_slam', 1, durations[2],
            speaker='linlan', listener='chen', emotion='angry', listener_emotion='urgent',
            action='slam_table', reaction='startle_recoil',
            camera_intent='low_angle', importance=0.86, intensity=0.9,
            blocking='slams the dead console then turns on Chen',
            environment_event=_event('table_impact', starts[2], starts[2] + max(14, gg.shot_frames(durations[2]) // 3), 0.95, 'console'),
            target='console', text=LINES[0][1], audio_file=line_a[0].resolve().as_posix(),
            objective='Linlan demands the spare',
            conflict='The south array is flatlined',
            description='Linlan slams the console and snaps around at Chen.',
        ),
        gg.beat(
            'b3_accuse', 1, durations[3],
            speaker='chen', listener='linlan', emotion='urgent', listener_emotion='angry',
            action='confront_step', reaction='startle_recoil',
            camera_intent='over_the_shoulder', importance=0.84, intensity=0.86,
            blocking='steps in and holds the spare out',
            environment_event=_event('sensor_error', starts[2], starts[5], 0.8, 'array'),
            target='linlan', text=LINES[1][1], audio_file=line_b[0].resolve().as_posix(),
            objective='Chen shows the last spare',
            conflict='The storm is forty minutes early',
            description='Chen confronts Linlan with the last sensor module.',
        ),
        gg.beat(
            'b4_handoff', 1, durations[4],
            speaker='chen', listener='linlan', emotion='focused', listener_emotion='focused',
            action='pass_prop', reaction='receive_prop',
            camera_intent='over_the_shoulder', importance=0.8, intensity=0.72,
            blocking='hands the spare across above the subtitle line',
            environment_event=_event('door_close', h0, starts[5], 0.8, 'door'),
            target='spare_sensor',
            prop_event={'prop_id': 'spare_sensor', 'event': 'handoff', 'position': meet},
            objective='The spare changes hands',
            conflict='The bulkhead is sealing',
            description='Chen passes the spare sensor to Linlan as the door seals.',
        ),
        gg.beat(
            'b5_install', 1, durations[5],
            speaker='linlan', listener='chen', emotion='focused', listener_emotion='tense',
            action='install_module', reaction='firm_nod',
            camera_intent='low_angle', importance=0.9, intensity=0.88,
            hold=True, blocking='seats the module in the main console',
            environment_event=_event('module_install', starts[5], starts[6], 0.95, 'console'),
            target='console',
            objective='Linlan locks the spare into the console',
            conflict='The carrier is still a red flatline',
            description='Linlan installs the module and the console locks green.',
        ),
        gg.beat(
            'b6_array', 1, durations[6],
            speaker='linlan', listener='chen', emotion='focused', listener_emotion='focused',
            action='check_storm_window', reaction='firm_nod',
            camera_intent='environment_insert', importance=0.92, intensity=0.9,
            hold=True, blocking='turns to the panoramic window as the array locks',
            environment_event=_event('array_restore', starts[6], starts[7], 0.95, 'window'),
            target='window',
            objective='The exterior array rotates into lock',
            conflict='The storm is still on the glass',
            description='The dish elevates through the window and Atuo rolls in.',
        ),
        gg.beat(
            'b7_release', 1, durations[7],
            speaker='linlan', listener='chen', emotion='relieved', listener_emotion='relieved',
            action='exhale_relax', reaction='firm_nod',
            camera_intent='climax_push', importance=0.96, intensity=0.84,
            hold=True, blocking='both watch the storm as the uplink holds',
            environment_event=_event('storm_approaching', starts[0], total_frames, 0.75, 'window'),
            target='window', text=LINES[2][1], audio_file=line_c[0].resolve().as_posix(),
            objective='They confirm the array is locked',
            conflict='The storm is still outside',
            description='Push in on Linlan and Chen as the green array holds.',
        ),
    ]
    for item in beats:
        item['scene_number'] = 1

    events = [
        _event('door_open', starts[0], starts[1], 0.9, 'door'),
        _event('power_flicker', starts[0], starts[2], 0.9, 'lights'),
        _event('storm_approaching', starts[0], total_frames, 0.8, 'window'),
        _event('table_impact', starts[2], starts[2] + max(16, gg.shot_frames(durations[2]) // 3), 0.95, 'console'),
        _event('sensor_error', starts[2], starts[5], 0.85, 'array'),
        _event('door_close', h0, starts[5], 0.85, 'door'),
        _event('module_install', starts[5], starts[6], 0.95, 'console'),
        _event('device_operating', starts[5] + 8, starts[6], 0.7, 'console'),
        _event('array_restore', starts[6], starts[7], 0.95, 'window'),
        _event('robot_speaking', starts[6] + 6, starts[7] - 4, 0.9, 'rover'),
    ]
    cast = {
        'linlan': gg.actor(
            layout, 'linlan', gait='confident', height=1.65, start_anchor='workstation_operator',
            facing=[
                gg.face(0, 'chen'),
                {'from': starts[6], 'yaw_deg': gg.yaw_of(layout, 'window_observer')},
            ],
            emotions=[
                gg.emotion(0, starts[2], 'angry', 0.8),
                gg.emotion(starts[2], starts[5], 'angry', 0.95),
                gg.emotion(starts[5], starts[7], 'focused', 0.8),
                gg.emotion(starts[7], total_frames, 'relieved', 0.7),
            ],
        ),
        'chen': gg.actor(
            layout, 'chen', gait='sprint', height=1.78, start_anchor='entry_threshold',
            facing=[gg.face(0, 'linlan'), {'from': starts[6], 'yaw_deg': gg.yaw_of(layout, 'window_observer')}],
            emotions=[
                gg.emotion(0, starts[4], 'urgent', 0.9),
                gg.emotion(starts[4], starts[7], 'focused', 0.7),
                gg.emotion(starts[7], total_frames, 'relieved', 0.65),
            ],
            moves=[{
                'from': enter_from,
                'to': enter_to,
                'points': [_round_pt(point) for point in approach[1:]],
                'auto_path': False,
                'ramp_s': 0.28,
                'gait': 'sprint',
            }],
        ),
    }
    cast['chen'].pop('start_anchor', None)
    cast['chen']['start_pos'] = _round_pt(entry)
    cast['chen']['yaw_deg'] = 180.0

    props = {
        'spare_sensor': {
            'kind': 'sensor',
            'entity_role': 'spare_sensor',
            'model': gg.require_asset(gg.SENSOR_MODEL),
            'timeline': [
                {'from': 0, 'to': h0, 'action': 'held', 'actor': 'chen', 'side': 'right'},
                {
                    'from': h0, 'to': h1, 'action': 'handoff',
                    'from_actor': 'chen', 'to_actor': 'linlan',
                    'transfer_frame': transfer, 'height': 1.22, 'ramp_s': 0.32,
                },
                {'from': h1, 'to': total_frames, 'action': 'held', 'actor': 'linlan', 'side': 'right'},
            ],
            'owners': [
                {'from': 0, 'actor': 'chen'},
                {'from': h0, 'actor': 'chen', 'handoff_window': [h0, h1], 'max_gap': 0.36},
                {'from': transfer, 'actor': 'linlan'},
            ],
        },
    }
    rover_start = gg.anchor_xz(layout, 'rover_bay_spot')
    rover_goal = (lin_x + 1.35, lin_z + 0.15)
    rover_path = _clip_path(layout.find_path(rover_start, rover_goal) or [rover_start, rover_goal], 3.3)
    rover_from = starts[6] + 4
    rover_to = max(rover_from + 18, starts[7] - 8)
    rover = {
        'id': 'at1',
        'name': '阿拓',
        'model': gg.require_asset(ROVER_MODEL),
        'height_m': 0.95,
        'yaw_offset_deg': 0.0,
        'eye_offset': [0.0, 0.58, 0.05],
        'start': {'pos': _round_pt(rover_start), 'yaw_deg': gg.yaw_of(layout, 'rover_bay_spot')},
        'visibility': [{'from': 0, 'visible': False}, {'from': max(0, rover_from - 2), 'visible': True}],
        'moves': [{
            'from': rover_from,
            'to': rover_to,
            'points': [_round_pt(point) for point in rover_path[1:]] or [_round_pt(rover_goal)],
            'ramp_s': 0.35,
        }],
        'facing': [{'from': rover_from, 'target': 'linlan'}],
    }
    shared = {
        'version': 1,
        'mode': 'generic',
        'renderer': 'godot3d',
        'language': 'zh',
        'title': '南侧阵列',
        'story': 'observation_bay spare-sensor crisis',
        'width': 1280,
        'height': 720,
        'fps': FPS,
        'scene_type': 'observation_bay',
        'lighting_profile': 'day_bay',
        'door_start': 'closed',
        'dramatic_director': True,
        'performance_beats': True,
        'enable_cutaways': True,
        'cast': cast,
        'beats': beats,
        'shots': beats,
        'events': events,
        'props': props,
        'rover': rover,
    }
    timing = {
        'fps': FPS,
        'total_frames': total_frames,
        'duration_s': round(total_frames / FPS, 3),
        'beat_starts': starts,
        'beat_durations': durations,
        'stills': {
            'door': min(total_frames - 1, starts[0] + 16),
            'slam': min(total_frames - 1, starts[2] + max(10, gg.shot_frames(durations[2]) // 3)),
            'handoff': min(total_frames - 1, transfer),
            'install': min(total_frames - 1, starts[5] + int(gg.shot_frames(durations[5]) * 0.62)),
            'array': min(total_frames - 1, starts[6] + int(gg.shot_frames(durations[6]) * 0.78)),
        },
        'sound_frames': {
            'door_open_hiss': starts[0] + 2,
            'power_down': starts[0] + 1,
            'storm_thunder': starts[0] + 8,
            'wind_gust': starts[0] + 4,
            'footstep_heavy': starts[0] + 10,
            'table_slam': starts[2] + 8,
            'camera_whoosh_impact': starts[2] + 8,
            'door_close_thud': h0 + 4,
            'module_install': starts[5] + 10,
            'mech_dish_servo': starts[6] + 6,
            'storm_thunder_2': starts[7] + 6,
        },
    }
    return shared, timing


def _sound_events(timing: dict) -> list[dict]:
    frames = timing['sound_frames']
    specs = [
        ('door_open_hiss', frames['door_open_hiss'], 0.72),
        ('power_down', frames['power_down'], 0.55),
        ('storm_thunder', frames['storm_thunder'], 0.62),
        ('wind_gust', frames['wind_gust'], 0.4),
        ('footstep_heavy', frames['footstep_heavy'], 0.62),
        ('camera_whoosh_impact', frames['camera_whoosh_impact'], 0.48),
        ('door_close_thud', frames['door_close_thud'], 0.7),
        ('mech_dish_servo', frames['mech_dish_servo'], 0.58),
        ('storm_thunder', frames['storm_thunder_2'], 0.5),
    ]
    return [{'kind': kind, 'frame': int(frame), 'volume': volume} for kind, frame, volume in specs]


def _write(shared: dict, timing: dict) -> None:
    version_a = json.loads(json.dumps(shared))
    version_b = json.loads(json.dumps(shared))
    version_b['art_profile'] = 'retro_industrial_scifi'
    version_b['sound_events'] = _sound_events(timing)
    for name, doc in (('version_a', version_a), ('version_b', version_b)):
        folder = OUT / name
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'manifest.json'
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f'wrote {path}', flush=True)
    (OUT / 'timing.json').write_text(json.dumps(timing, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(
        f"timeline {timing['duration_s']:.2f}s ({timing['total_frames']} frames @ {FPS} fps)",
        flush=True,
    )


def main() -> None:
    audio_dir = OUT / 'shared_audio'
    audio_dir.mkdir(parents=True, exist_ok=True)
    voiced = []
    engines = []
    for index, (speaker, text) in enumerate(LINES, 1):
        path, seconds, engine = _voice_line(audio_dir, index, speaker, text)
        voiced.append((path, seconds))
        engines.append(engine)
        print(f'voice {index} {speaker} {seconds:.2f}s via {engine}', flush=True)
    shared, timing = _build(voiced)
    timing['tts'] = engines
    _write(shared, timing)


if __name__ == '__main__':
    main()
