#!/usr/bin/env python3
"""30-second corridor action reel. No dialogue.

Writes a normal episode manifest. Render it with the existing episode path:

    python tools/render_episode3d.py --manifest media/m6_3d/retro_ab/action_reel/manifest.json --out-dir media/m6_3d/retro_ab/action_reel --video-name action_reel.mp4

Clip names are taken from the on-disk GLB libraries. There is no Run_Fast and
no vault or hurdle clip. The sprint asks for the UAL run ``Sprint_Loop``.
The obstacle beat plays ``Jump_Start`` then ``Jump_Land``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import gen_generality_manifests as gg

OUT = ROOT / 'media' / 'm6_3d' / 'retro_ab' / 'action_reel'
FPS = 24
# 4 + 4 + 5 + 6 + 6 + 5 = 30.0s, 720 frames.
DURATIONS = (4.0, 4.0, 5.0, 6.0, 6.0, 5.0)
CHEN_MODEL = 'media/assets3d/characters/chen_ps2_v2.glb'
ROVER_MODEL = 'media/assets3d/atuo/atuo_rigged.glb'
UAL1 = 'media/assets3d/cc0/quaternius_universal_animation_library/UAL1_Standard.glb'
UAL2 = 'media/assets3d/cc0/quaternius_universal_animation_library_2/UAL2_Standard.glb'
DRAMATIC = 'media/assets3d/animations/ps2_dramatic_pack.glb'

# Floor XZ. +Z is the south door (the far blast). -Z is the north door.
CHEN_START = (0.0, 6.2)
BEAM_STOP = (0.0, -2.15)
FIGHT = (-0.15, -3.85)
DOOR_POS = (0.0, -6.45)
MACHINE_POINT = (0.82, -4.55)


def _focus(pos, look, fov, fov_to=None, pos_to=None, look_to=None, subject: str = '') -> dict:
    doc = {
        'pos': [round(float(v), 3) for v in pos],
        'pos_to': [round(float(v), 3) for v in (pos_to or pos)],
        'look_at': [round(float(v), 3) for v in look],
        'look_to': [round(float(v), 3) for v in (look_to or look)],
        'fov': float(fov),
        'fov_to': float(fov_to if fov_to is not None else fov),
    }
    if subject:
        doc['id'] = subject
    return doc


def _event(kind: str, start: int, end: int, intensity: float, target: str) -> dict:
    return {
        'type': kind,
        'from': int(start),
        'to': int(end),
        'intensity': intensity,
        'target': target,
    }


def _performance(
    action: str,
    clip: str,
    *,
    one_shot: bool,
    chain: str = '',
    chain_clip: str = '',
    chain_one_shot: bool = True,
    chain_at: float | None = None,
) -> dict:
    doc = {
        'action': action,
        'dedicated_clip': clip,
        'raw_playback': True,
        'one_shot': bool(one_shot),
    }
    if chain:
        doc['chain_action'] = chain
        doc['chain_dedicated_clip'] = chain_clip
        doc['chain_one_shot'] = bool(chain_one_shot)
        doc['chain_at'] = float(chain_at if chain_at is not None else 0.45)
    return doc


def _abs(relative: str) -> str:
    gg.require_asset(relative)
    return (ROOT / relative).resolve().as_posix()


def _build() -> tuple[dict, dict]:
    starts, total = gg.frame_starts(DURATIONS)
    if total != 720:
        raise SystemExit(f'expected 720 frames, got {total}')
    # Sprint_Loop travels about 8.25 m/s in place. Match the path so the feet
    # are not asked to skate. The rest of the beat is the halt.
    sprint_to = starts[1] + 26
    step_from = starts[2] + 104
    door_to = starts[4] + 10
    perf = [
        _performance(
            'flinch_dodge', 'Flinch_Explosion_Flash', one_shot=True,
            chain='idle_natural', chain_clip='Mocap_Idle_Sway_Loop',
            chain_one_shot=False, chain_at=0.28,
        ),
        _performance(
            'rush_enter', 'Sprint_Loop', one_shot=False,
            chain='abrupt_halt', chain_clip='Mocap_Abrupt_Halt',
            chain_one_shot=True, chain_at=0.30,
        ),
        _performance(
            'jump_up', 'Jump_Start', one_shot=True,
            chain='land_heavy', chain_clip='Jump_Land',
            chain_one_shot=True, chain_at=0.34,
        ),
        _performance(
            'slam_table', 'Punch_Cross', one_shot=True,
            chain='stagger_back', chain_clip='Hit_Knockback',
            chain_one_shot=True, chain_at=0.34,
        ),
        _performance(
            'rush_enter', 'Sprint_Loop', one_shot=False,
            chain='flinch_dodge', chain_clip='Flinch_Explosion_Flash',
            chain_one_shot=True, chain_at=0.10,
        ),
        _performance('exhale_relief', 'Mocap_Exhale_Relief', one_shot=True),
    ]
    beats = [
        gg.beat(
            'b1_blackout', 1, DURATIONS[0],
            speaker='chen', emotion='fear',
            camera_intent='impact_opening', importance=0.8, intensity=0.84,
            hold=True,
            blocking='braces as the hall lights die and a blast flares at the south end',
            environment_event=_event('table_impact', 12, 86, 0.82, 'south_door'),
            event_focus=_focus(
                (1.35, 1.62, 0.4), (0.05, 1.2, 6.35), 50, 46,
                pos_to=(1.05, 1.55, 0.85), look_to=(0.0, 1.15, 6.7),
                subject='south_blast',
            ),
            target='south_door',
            objective='The corridor loses power',
            conflict='A blast lights the far end',
            description='Overhead lights die. The red emergency strip stays on. An orange flare hits the south end.',
            **perf[0],
        ),
        gg.beat(
            'b2_sprint', 1, DURATIONS[1],
            speaker='chen', emotion='urgent',
            camera_intent='action_track', importance=0.86, intensity=0.9,
            hold=True,
            blocking='sprints north down the lane toward the camera',
            environment_event=_event('power_flicker', starts[1], starts[2], 1.0, 'lights'),
            event_focus=_focus(
                (1.5, 1.52, -3.35), (0.0, 1.15, 4.4), 56, 50,
                pos_to=(1.35, 1.46, -3.5), look_to=(0.0, 1.1, 1.2),
                subject='chen',
            ),
            target='chen',
            objective='Chen sprints the lane',
            conflict='The hall is dark and the beam is ahead',
            description='Side track of a real run clip. He closes on the fallen beam.',
            **perf[1],
        ),
        gg.beat(
            'b3_beam', 1, DURATIONS[2],
            speaker='chen', emotion='urgent',
            camera_intent='low_angle', importance=0.84, intensity=0.8,
            hold=True,
            blocking='jump-starts over the fallen beam, lands, then steps past it',
            environment_event=_event('none', starts[2], starts[3], 0.0, 'fallen_beam'),
            event_focus=_focus(
                (-1.55, 1.18, -4.2), (0.05, 0.95, -2.25), 42, 38,
                pos_to=(-1.4, 1.12, -4.3), look_to=(0.02, 1.0, -2.5),
                subject='fallen_beam',
            ),
            target='fallen_beam',
            objective='He gets past the beam',
            conflict='The beam blocks the lane and there is no vault clip',
            description='Jump_Start then Jump_Land. No hurdle clip exists in the libraries.',
            **perf[2],
        ),
        gg.beat(
            'b4_machine', 1, DURATIONS[3],
            speaker='chen', emotion='angry',
            camera_intent='low_angle', importance=0.92, intensity=0.94,
            hold=True,
            blocking='punches the red machine then reels from the hit',
            environment_event=_event('table_impact', starts[3] + 8, starts[3] + 28, 0.9, 'hostile_machine'),
            event_focus=_focus(
                (-1.5, 1.28, -4.75), (0.25, 1.15, -4.15), 40, 36,
                pos_to=(-1.35, 1.22, -4.85), look_to=(0.35, 1.12, -4.3),
                subject='hostile_machine',
            ),
            target='hostile_machine',
            objective='He hits the machine',
            conflict='The machine blocks the corner',
            description='Punch_Cross, then Hit_Knockback. A short local spark, not a white frame.',
            **perf[3],
        ),
        gg.beat(
            'b5_door', 1, DURATIONS[4],
            speaker='chen', emotion='urgent',
            camera_intent='low_angle', importance=0.9, intensity=0.88,
            hold=True,
            blocking='bursts through the north door as it slams and a blast flares outside',
            environment_event=_event('door_close', door_to, total - 4, 0.95, 'north_door'),
            event_focus=_focus(
                (1.2, 1.42, -4.85), (0.0, 1.28, -7.4), 50, 46,
                pos_to=(1.0, 1.36, -5.1), look_to=(0.0, 1.22, -7.5),
                subject='north_door',
            ),
            target='north_door',
            objective='The north door slams',
            conflict='A second blast is outside the closing door',
            description='He runs into the north doorway. The door slides shut. The blast stays outside.',
            **perf[4],
        ),
        gg.beat(
            'b6_breath', 1, DURATIONS[5],
            speaker='chen', emotion='relieved',
            camera_intent='reaction', importance=0.7, intensity=0.4,
            hold=True,
            blocking='turns back and breathes as the radio chirps and a red light comes up',
            environment_event=_event('robot_speaking', starts[5] + 8, total - 8, 0.7, 'rover'),
            event_focus=_focus(
                (1.15, 1.48, -5.0), (0.0, 1.32, -7.15), 48, 44,
                pos_to=(0.95, 1.42, -5.15), look_to=(0.05, 1.28, -7.45),
                subject='chen',
            ),
            target='chen',
            objective='He catches a breath',
            conflict='A new red light marks another threat',
            description='Mocap_Exhale_Relief, a radio chirp, and a red light beyond the shut door. No dialogue.',
            **perf[5],
        ),
    ]
    cast = {
        'chen': {
            'name': '陈工',
            'model': _abs(CHEN_MODEL),
            'height_m': 1.78,
            'gait': 'sprint',
            'start_pos': [CHEN_START[0], CHEN_START[1]],
            'yaw_deg': 180.0,
            'look': {'preserve_materials': True, 'uniform': False},
            'extra_libraries': [
                _abs(UAL1),
                _abs(UAL2),
                _abs(DRAMATIC),
            ],
            'facing': [
                {'from': 0, 'yaw_deg': 180.0},
                {'from': starts[3], 'point': [MACHINE_POINT[0], MACHINE_POINT[1]]},
                {'from': starts[4], 'yaw_deg': 180.0},
                {'from': starts[4] + 48, 'yaw_deg': 0.0},
            ],
            'emotions': [
                gg.emotion(0, starts[2], 'fear', 0.8),
                gg.emotion(starts[2], starts[4], 'urgent', 0.85),
                gg.emotion(starts[3], starts[4], 'angry', 0.75),
                gg.emotion(starts[5], total, 'relieved', 0.55),
            ],
            'moves': [
                {
                    'from': starts[1] + 2,
                    'to': sprint_to,
                    'points': [[BEAM_STOP[0], BEAM_STOP[1]]],
                    'auto_path': False,
                    'ramp_s': 0.16,
                    'gait': 'sprint',
                },
                {
                    'from': step_from,
                    'to': starts[3] - 2,
                    'points': [[FIGHT[0], FIGHT[1]]],
                    'auto_path': False,
                    'ramp_s': 0.2,
                    'gait': 'casual',
                },
                {
                    'from': starts[4] + 2,
                    'to': door_to,
                    'points': [[DOOR_POS[0], DOOR_POS[1]]],
                    'auto_path': False,
                    'ramp_s': 0.12,
                    'gait': 'sprint',
                },
            ],
        },
    }
    rover_from = starts[5] + 4
    manifest = {
        'version': 1,
        'mode': 'generic',
        'renderer': 'godot3d',
        'language': 'zh',
        'title': '走廊突围',
        'story': 'corridor 30s action reel',
        'width': 1280,
        'height': 720,
        'fps': FPS,
        'scene_type': 'corridor',
        'lighting_profile': 'dim_corridor',
        'door_start': 'open',
        'dramatic_director': True,
        'performance_beats': True,
        'enable_cutaways': False,
        'extra_props': [
            {
                'id': 'fallen_beam',
                'kind': 'crate',
                'pos': [0.02, -2.75],
                'yaw_deg': 82.0,
                'size': [1.55, 0.32, 0.42],
                'color': 'steel',
                'zone': 'center_stage',
            },
            {
                'id': 'hostile_machine',
                'kind': 'model',
                'model': 'machine_generatorLarge.glb',
                'pos': [MACHINE_POINT[0], MACHINE_POINT[1]],
                'yaw_deg': -12.0,
                'scale': 1.2,
                'zone': 'center_stage',
                'tint': '#ff3a22',
                'emission_boost': 0.9,
            },
        ],
        'cast': cast,
        'beats': beats,
        'shots': beats,
        'events': [
            _event('power_flicker', 8, total - 1, 1.0, 'lights'),
            _event('table_impact', 12, 86, 0.82, 'south_door'),
            _event('table_impact', starts[3] + 8, starts[3] + 28, 0.9, 'hostile_machine'),
            _event('table_impact', 478, 552, 0.78, 'north_door'),
            _event('door_close', door_to, total - 1, 0.95, 'north_door'),
            _event('robot_speaking', rover_from, total - 8, 0.75, 'rover'),
        ],
        'sound_events': [
            {'kind': 'power_down', 'frame': 6, 'volume': 0.62},
            {'kind': 'metal_clang', 'frame': 18, 'volume': 0.7},
            {'kind': 'camera_whoosh_impact', 'frame': 20, 'volume': 0.42},
            {'kind': 'footstep_heavy', 'frame': starts[1] + 8, 'volume': 0.55},
            {'kind': 'footstep_heavy', 'frame': starts[1] + 20, 'volume': 0.5},
            {'kind': 'footstep_heavy', 'frame': starts[1] + 34, 'volume': 0.48},
            {'kind': 'footstep_heavy', 'frame': starts[1] + 48, 'volume': 0.46},
            {'kind': 'cloth_rustle', 'frame': starts[2] + 6, 'volume': 0.28},
            {'kind': 'footstep_heavy', 'frame': starts[2] + 18, 'volume': 0.4},
            {'kind': 'metal_clang', 'frame': starts[3] + 14, 'volume': 0.72},
            {'kind': 'camera_whoosh_impact', 'frame': starts[3] + 16, 'volume': 0.4},
            {'kind': 'footstep_heavy', 'frame': starts[4] + 6, 'volume': 0.5},
            {'kind': 'door_close_thud', 'frame': door_to + 8, 'volume': 0.78},
            {'kind': 'metal_clang', 'frame': 492, 'volume': 0.58},
            {'kind': 'console_beep', 'frame': starts[5] + 14, 'volume': 0.46},
            {'kind': 'console_beep', 'frame': starts[5] + 36, 'volume': 0.38},
        ],
        'rover': {
            'id': 'at1',
            'name': '阿拓',
            'model': _abs(ROVER_MODEL),
            'height_m': 0.72,
            'yaw_offset_deg': 0.0,
            'eye_offset': [0.0, 0.42, 0.04],
            'start': {'pos': [1.15, -6.55], 'yaw_deg': 180.0},
            'visibility': [
                {'from': 0, 'visible': False},
                {'from': rover_from, 'visible': True},
            ],
        },
    }
    timing = {
        'fps': FPS,
        'total_frames': total,
        'duration_s': round(total / FPS, 3),
        'beat_starts': starts,
        'beat_durations': list(DURATIONS),
        'clips': {
            '0-4_blackout': {'clip': 'Flinch_Explosion_Flash', 'then': 'Mocap_Idle_Sway_Loop'},
            '4-8_sprint': {'clip': 'Sprint_Loop', 'note': 'UAL run. Run_Fast is not in the libraries.'},
            '8-13_beam': {'clip': 'Jump_Start', 'then': 'Jump_Land', 'note': 'No vault or hurdle clip exists.'},
            '13-19_machine': {'clip': 'Punch_Cross', 'then': 'Hit_Knockback'},
            '19-25_door': {'clip': 'Sprint_Loop'},
            '25-30_breath': {'clip': 'Mocap_Exhale_Relief'},
        },
        'stills': {
            '01_blackout': 48,
            '02_sprint': 110,
            '03_beam': 218,
            '04_punch': 348,
            '05_door': 482,
            '06_breath': 660,
        },
    }
    return manifest, timing


def main() -> None:
    manifest, timing = _build()
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / 'manifest.json'
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (OUT / 'timing.json').write_text(json.dumps(timing, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'wrote {path}', flush=True)
    print(f"timeline {timing['duration_s']:.2f}s ({timing['total_frames']} frames @ {FPS} fps)", flush=True)


if __name__ == '__main__':
    main()
