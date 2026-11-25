#!/usr/bin/env python3
"""Phase-2 retro C/D cut: eight visual beats in the observation bay, ~22.5s.

Version C keeps the original characters and the Version-B room, and plays the
real 117-clip pack. Version D adds the PS2 characters and the modular Kenney
bay. Version B's movie is never written.

    python tools/gen_retro_cd_demo.py
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
from renderer.retro_art_profile import RetroArtProfile
from renderer.scene_layout import SceneLayoutSystem
from renderer.synthesize import synthesize_edge_file

OUT = ROOT / 'media' / 'm6_3d' / 'retro_ab'
FPS = 24
VOICES = {
    'linlan': 'zh-CN-XiaoxiaoNeural',
    'chen': 'zh-CN-YunxiNeural',
}
ROVER_MODEL = 'media/assets3d/atuo/atuo_rigged.glb'
PS2_MODELS = {
    'linlan': 'media/assets3d/characters/linlan_ps2.glb',
    'chen': 'media/assets3d/characters/chen_ps2.glb',
}
LINES = (
    ('chen', '林岚！备件到了！'),
    ('linlan', '阵列全断了，快给我！'),
    ('linlan', '信号锁定，我们撑过去了。'),
)
# Eight beats. shot_frames() rounding lands on exactly 540 frames / 22.5s.
DURATIONS = (2.2, 2.3, 2.7, 2.8, 3.2, 2.8, 3.2, 3.3)


def _wav_seconds(path: Path) -> float:
    with wave.open(str(path), 'rb') as handle:
        rate = handle.getframerate() or 24000
        frames = handle.getnframes()
    return frames / max(rate, 1)


def _fallback_voice(path: Path, seconds: float = 1.6) -> None:
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
            syllable = 0.5 + 0.5 * math.sin(t * 2.0 * math.pi * 3.4)
            burst = syllable if (index // int(rate * 0.08)) % 2 == 0 else 0.22
            sample = math.sin(2.0 * math.pi * 180.0 * t) * burst * 0.4
            frames += struct.pack('<h', int(max(-32767, min(32767, sample * 32767))))
        handle.writeframes(frames)


def _voice_line(audio_dir: Path, index: int, speaker: str, text: str) -> tuple[Path, float, str]:
    path = audio_dir / f'line_{index:02d}.wav'
    if path.is_file() and path.stat().st_size > 8000:
        return path, _wav_seconds(path), 'edge-tts-cached'
    voice = VOICES[speaker]
    try:
        synthesize_edge_file(text, voice, '+10%', path, speaker=speaker)
        return path, _wav_seconds(path), 'edge-tts'
    except Exception as exc:
        print(f'edge-tts unavailable for {speaker!r} ({exc}); writing a pulsed fallback', flush=True)
        _fallback_voice(path, 1.5)
        return path, _wav_seconds(path), 'fallback'


def _event(kind: str, start: int, end: int, intensity: float, target: str) -> dict:
    return {
        'type': kind,
        'from': int(start),
        'to': int(end),
        'intensity': intensity,
        'target': target,
    }


def _focus(pos, look, fov, fov_to, pos_to=None, look_to=None, subject: str = '') -> dict:
    doc = {
        'pos': [round(float(v), 3) for v in pos],
        'pos_to': [round(float(v), 3) for v in (pos_to or pos)],
        'look_at': [round(float(v), 3) for v in look],
        'look_to': [round(float(v), 3) for v in (look_to or look)],
        'fov': float(fov),
        'fov_to': float(fov_to),
    }
    if subject:
        doc['id'] = subject
    return doc


def _art(modular: bool) -> dict:
    return RetroArtProfile().resolve({
        'id': 'retro_industrial_scifi',
        'modular_scene_assets': modular,
    })


def _build(audio: list[tuple[Path, float]]) -> tuple[dict, dict]:
    layout = SceneLayoutSystem().solve('observation_bay', lighting_profile='day_bay', validate=True)
    line_chen, line_slam, line_lock = audio
    starts, total_frames = gg.frame_starts(DURATIONS)
    lin_x, lin_z = gg.anchor_xz(layout, 'workstation_operator')
    # Chen plants just inside the bay, within a short reach of Linlan.
    chen_stop = (-2.25, -2.05)
    lin_step = (round(lin_x, 3), round(lin_z, 3))
    sprint_from = starts[1] + 1
    sprint_to = starts[2] + 6
    handoff_from = starts[3] + 4
    handoff_to = starts[4] - 2
    transfer = handoff_from + max(12, (handoff_to - handoff_from) // 2)
    meet = [
        round((lin_step[0] + chen_stop[0]) * 0.5, 3),
        1.34,
        round((lin_step[1] + chen_stop[1]) * 0.5, 3),
    ]
    install_still = starts[4] + 26
    beats = [
        gg.beat(
            'b1_crisis', 1, DURATIONS[0],
            speaker='linlan', listener='chen', emotion='angry', listener_emotion='urgent',
            action='operate_console', reaction='firm_nod',
            camera_intent='impact_opening', importance=0.72, intensity=0.7,
            blocking='works the dead console as the bay alarms',
            environment_event=_event('storm_approaching', starts[0], starts[2], 0.9, 'window'),
            event_focus=_focus(
                (1.35, 1.68, -1.55), (-2.4, 1.35, -3.85), 50, 44,
                pos_to=(0.85, 1.6, -1.85), look_to=(-2.9, 1.22, -3.7),
                subject='storm_approaching',
            ),
            target='console', objective='The bay drops into a red fault',
            conflict='The south array is already dead',
            description='Console fault, flickering lights, klaxon, and storm lightning.',
        ),
        gg.beat(
            'b2_sprint', 1, DURATIONS[1],
            speaker='chen', listener='linlan', emotion='urgent', listener_emotion='angry',
            action='rush_enter', reaction='snap_look_back',
            camera_intent='entrance_exit', importance=0.8, intensity=0.84,
            blocking='sprints through the bulkhead and skids to a stop',
            environment_event=_event('door_open', starts[1], starts[2], 0.95, 'door'),
            event_focus=_focus(
                (1.85, 1.58, 1.15), (0.0, 1.42, 3.55), 42, 36,
                pos_to=(1.55, 1.52, 0.85), look_to=(0.05, 1.36, 3.35),
                subject='door_open',
            ),
            target='door', text=LINES[0][1], audio_file=line_chen[0].resolve().as_posix(),
            objective='Chen delivers the spare at a run',
            conflict='The door is still sliding open',
            description='Chen bursts in upright and plants in a skid halt.',
        ),
        gg.beat(
            'b3_slam', 1, DURATIONS[2],
            speaker='linlan', listener='chen', emotion='angry', listener_emotion='urgent',
            action='slam_table', reaction='startle_recoil',
            camera_intent='low_angle', importance=0.9, intensity=0.92,
            chain_action='snap_look_back', chain_at=0.52,
            blocking='slams the console then snaps toward Chen',
            environment_event=_event('table_impact', starts[2] + 8, starts[2] + 28, 0.95, 'console'),
            event_focus=_focus(
                (-1.15, 1.22, -2.55), (-3.16, 1.08, -3.62), 34, 30,
                pos_to=(-1.45, 1.18, -2.85), subject='table_impact',
            ),
            target='console', text=LINES[1][1], audio_file=line_slam[0].resolve().as_posix(),
            objective='Linlan demands the spare',
            conflict='The array is flatlined',
            description='Linlan slams the console and turns on Chen.',
        ),
        gg.beat(
            'b4_handoff', 1, DURATIONS[3],
            speaker='chen', listener='linlan', emotion='focused', listener_emotion='focused',
            action='pass_prop', reaction='receive_prop',
            camera_intent='over_the_shoulder', importance=0.82, intensity=0.74,
            blocking='offers the spare above the subtitle line',
            environment_event=_event('door_close', starts[3], starts[4], 0.8, 'door'),
            event_focus=_focus(
                (-0.35, 1.58, -0.15), (meet[0], 1.36, meet[2]), 36, 32,
                pos_to=(-0.7, 1.5, -0.45), subject='spare_sensor',
            ),
            target='spare_sensor',
            prop_event={'prop_id': 'spare_sensor', 'event': 'handoff', 'position': meet},
            objective='The spare changes hands',
            conflict='The bulkhead is sealing',
            description='Firm handoff, both hands clear of the subtitles.',
        ),
        gg.beat(
            'b5_install', 1, DURATIONS[4],
            speaker='linlan', listener='chen', emotion='focused', listener_emotion='tense',
            action='install_module', reaction='firm_nod',
            camera_intent='prop_mech_closeup', importance=0.92, intensity=0.9,
            hold=True, chain_action='press_button', chain_at=0.56,
            blocking='locks the spare into the console and hits the switch',
            environment_event=_event('module_install', starts[4] + 10, starts[5], 0.95, 'console'),
            event_focus=_focus(
                (-1.15, 1.28, -2.55), (-3.16, 1.05, -3.62), 32, 28,
                pos_to=(-1.4, 1.22, -2.9), subject='module_install',
            ),
            target='console', objective='Linlan seats the module',
            conflict='The carrier is still red',
            description='Physical install on the module slot, then a hard button press.',
        ),
        gg.beat(
            'b6_flinch', 1, DURATIONS[5],
            speaker='linlan', listener='chen', emotion='fear', listener_emotion='fear',
            action='flinch_dodge', reaction='startle_recoil',
            camera_intent='reaction', importance=0.88, intensity=0.9,
            blocking='both brace as the first boot sputters',
            environment_event=_event('storm_approaching', starts[5], starts[6], 0.95, 'window'),
            event_focus=_focus(
                (0.55, 1.48, -1.55), (-2.15, 1.4, -2.9), 46, 40,
                pos_to=(0.2, 1.42, -1.85), look_to=(-2.4, 1.45, -3.6),
                subject='storm_approaching',
            ),
            target='window', objective='The boot fails once',
            conflict='Lightning hits as the countdown flashes',
            description='Red sputter, thunder, and both characters flinch.',
        ),
        gg.beat(
            'b7_array', 1, DURATIONS[6],
            speaker='linlan', listener='chen', emotion='focused', listener_emotion='focused',
            action='look_up_array', reaction='look_up_array',
            camera_intent='environment_insert', importance=0.96, intensity=0.94,
            hold=True, blocking='the exterior dish rotates into lock',
            environment_event=_event('array_restore', starts[6], starts[7], 0.98, 'window'),
            target='window', objective='The dish locks green',
            conflict='The storm is still on the glass',
            description='Heroic view through the window onto the rotating dish.',
        ),
        gg.beat(
            'b8_release', 1, DURATIONS[7],
            speaker='linlan', listener='chen', emotion='relieved', listener_emotion='relieved',
            action='exhale_relax', reaction='firm_nod',
            camera_intent='climax_push', importance=0.9, intensity=0.7,
            hold=True, blocking='drops her shoulders as the carrier holds',
            environment_event=_event('robot_speaking', starts[7] + 8, total_frames - 6, 0.7, 'rover'),
            target='linlan', text=LINES[2][1], audio_file=line_lock[0].resolve().as_posix(),
            objective='They confirm the lock',
            conflict='The storm is still outside',
            description='Green carrier, Atuo rolls up, Linlan exhales.',
        ),
    ]
    for item in beats:
        item['scene_number'] = 1

    events = [
        _event('power_flicker', starts[0], starts[2], 0.9, 'lights'),
        _event('red_alert', starts[0] + 4, starts[2], 0.85, 'console'),
        _event('storm_approaching', starts[0], total_frames, 0.82, 'window'),
        _event('door_open', starts[1], starts[2] + 8, 0.95, 'door'),
        _event('table_impact', starts[2] + 8, starts[2] + 28, 0.95, 'console'),
        _event('door_close', starts[3], starts[4], 0.85, 'door'),
        _event('module_install', starts[4] + 12, starts[5] - 8, 0.95, 'console'),
        _event('device_operating', starts[4] + 40, starts[5], 0.7, 'console'),
        _event('sensor_error', starts[5], starts[6], 0.9, 'console'),
        _event('power_flicker', starts[5], starts[6], 0.8, 'lights'),
        _event('array_restore', starts[6], starts[7] + 10, 0.98, 'window'),
        _event('robot_speaking', starts[7] + 6, total_frames - 4, 0.85, 'rover'),
    ]
    cast = {
        'linlan': gg.actor(
            layout, 'linlan', gait='confident', height=1.65, start_anchor='workstation_operator',
            facing=[
                gg.face(0, 'console'),
                gg.face(starts[2] + 36, 'chen'),
                {'from': starts[4], 'yaw_deg': 180.0},
                {'from': starts[6], 'yaw_deg': 180.0},
                gg.face(starts[7], 'chen'),
            ],
            emotions=[
                gg.emotion(0, starts[4], 'angry', 0.9),
                gg.emotion(starts[4], starts[5], 'focused', 0.85),
                gg.emotion(starts[5], starts[6], 'fear', 0.8),
                gg.emotion(starts[6], starts[7], 'focused', 0.75),
                gg.emotion(starts[7], total_frames, 'relieved', 0.7),
            ],
        ),
        'chen': gg.actor(
            layout, 'chen', gait='sprint', height=1.78, start_anchor='entry_threshold',
            facing=[
                {'from': 0, 'yaw_deg': 180.0},
                gg.face(sprint_to, 'linlan'),
            ],
            emotions=[
                gg.emotion(0, starts[4], 'urgent', 0.9),
                gg.emotion(starts[4], starts[6], 'tense', 0.75),
                gg.emotion(starts[6], starts[7], 'fear', 0.7),
                gg.emotion(starts[7], total_frames, 'relieved', 0.65),
            ],
            moves=[{
                'from': sprint_from,
                'to': sprint_to,
                'points': [[-0.45, 0.9], [round(chen_stop[0], 3), round(chen_stop[1], 3)]],
                'auto_path': False,
                'ramp_s': 0.45,
                'gait': 'sprint',
            }],
        ),
    }
    for actor_id in ('linlan', 'chen'):
        cast[actor_id]['look'] = {'preserve_materials': True, 'uniform': False}
    cast['chen'].pop('start_anchor', None)
    cast['chen']['start_pos'] = [0.0, 4.45]
    cast['chen']['yaw_deg'] = 180.0

    props = {
        'spare_sensor': {
            'kind': 'sensor',
            'entity_role': 'spare_sensor',
            'model': gg.require_asset(gg.SENSOR_MODEL),
            'timeline': [
                {'from': 0, 'to': handoff_from, 'action': 'held', 'actor': 'chen', 'side': 'right'},
                {
                    'from': handoff_from, 'to': handoff_to, 'action': 'handoff',
                    'from_actor': 'chen', 'to_actor': 'linlan',
                    'transfer_frame': transfer, 'height': 1.34, 'ramp_s': 0.28,
                },
                {'from': handoff_to, 'to': total_frames, 'action': 'held', 'actor': 'linlan', 'side': 'right'},
            ],
            'owners': [
                {'from': 0, 'actor': 'chen'},
                {'from': handoff_from, 'actor': 'chen', 'handoff_window': [handoff_from, handoff_to], 'max_gap': 0.36},
                {'from': transfer, 'actor': 'linlan'},
            ],
        },
    }
    rover_start = gg.anchor_xz(layout, 'rover_bay_spot')
    rover_goal = (lin_x + 1.15, lin_z + 0.35)
    rover_path = [rover_start, rover_goal]
    rover_from = starts[7] + 4
    rover_to = min(total_frames - 8, rover_from + 36)
    rover = {
        'id': 'at1',
        'name': '阿拓',
        'model': gg.require_asset(ROVER_MODEL),
        'height_m': 0.95,
        'yaw_offset_deg': 0.0,
        'eye_offset': [0.0, 0.58, 0.05],
        'start': {'pos': [round(rover_start[0], 3), round(rover_start[1], 3)], 'yaw_deg': gg.yaw_of(layout, 'rover_bay_spot')},
        'visibility': [{'from': 0, 'visible': False}, {'from': max(0, rover_from - 2), 'visible': True}],
        'moves': [{
            'from': rover_from,
            'to': rover_to,
            'points': [[round(rover_goal[0], 3), round(rover_goal[1], 3)]],
            'ramp_s': 0.3,
        }],
        'facing': [{'from': rover_from, 'target': 'linlan'}],
    }
    shared = {
        'version': 1,
        'mode': 'generic',
        'renderer': 'godot3d',
        'language': 'zh',
        'title': '南侧阵列',
        'story': 'observation_bay visual-first spare crisis',
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
        'beat_durations': list(DURATIONS),
        'stills': {
            '01_crisis_door': min(total_frames - 1, starts[1] + 10),
            '02_sprint_halt': min(total_frames - 1, starts[1] + 21),
            '03_slam_conflict': min(total_frames - 1, starts[2] + 22),
            '04_install_console': min(total_frames - 1, starts[4] + 16),
            '05_array_dish': min(total_frames - 1, starts[6] + 64),
            '06_release_rover': min(total_frames - 1, starts[7] + 40),
        },
        'positions': {
            'linlan': [round(lin_x, 3), round(lin_z, 3)],
            'lin_step': list(lin_step),
            'chen_stop': [round(chen_stop[0], 3), round(chen_stop[1], 3)],
            'meet': meet,
        },
    }
    return shared, timing


def _sound_events(timing: dict) -> list[dict]:
    starts = timing['beat_starts']
    specs = [
        ('alarm_klaxon', starts[0] + 6, 0.5),
        ('power_down', starts[0] + 2, 0.55),
        ('console_error', starts[0] + 8, 0.48),
        ('storm_thunder', starts[0] + 14, 0.7),
        ('wind_gust', starts[0] + 4, 0.36),
        ('door_open_hiss', starts[1] + 1, 0.62),
        ('footstep_heavy', starts[1] + 8, 0.58),
        ('footstep_heavy', starts[1] + 16, 0.5),
        ('camera_whoosh_impact', starts[2] + 10, 0.46),
        ('table_slam', starts[2] + 12, 0.8),
        ('cloth_rustle', starts[3] + 10, 0.34),
        ('door_close_thud', starts[3] + 8, 0.68),
        ('module_install', starts[4] + 18, 0.66),
        ('metal_clang', starts[4] + 48, 0.55),
        ('console_beep', starts[4] + 50, 0.42),
        ('console_error', starts[5] + 6, 0.5),
        ('power_down', starts[5] + 4, 0.48),
        ('storm_thunder', starts[5] + 10, 0.74),
        ('mech_dish_servo', starts[6] + 8, 0.62),
        ('power_restore', starts[6] + 36, 0.5),
        ('console_beep', starts[7] + 10, 0.36),
        ('wind_gust', starts[7] + 4, 0.28),
    ]
    return [{'kind': kind, 'frame': int(frame), 'volume': volume} for kind, frame, volume in specs]


def _write(shared: dict, timing: dict) -> None:
    version_c = json.loads(json.dumps(shared))
    version_d = json.loads(json.dumps(shared))
    version_c['art_profile'] = _art(False)
    version_d['art_profile'] = _art(True)
    version_c['sound_events'] = _sound_events(timing)
    version_d['sound_events'] = _sound_events(timing)
    for actor_id, relative in PS2_MODELS.items():
        version_d['cast'][actor_id]['model'] = gg.require_asset(relative)
    for name, doc in (('version_c', version_c), ('version_d', version_d)):
        folder = OUT / name
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'manifest.json'
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f'wrote {path}', flush=True)
    (OUT / 'timing_cd.json').write_text(json.dumps(timing, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(
        f"timeline {timing['duration_s']:.2f}s ({timing['total_frames']} frames @ {FPS} fps)",
        flush=True,
    )


def main() -> None:
    audio_dir = OUT / 'cd_audio'
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
