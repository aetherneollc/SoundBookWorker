#!/usr/bin/env python3
"""Version E: the eight-beat bay crisis with the causal install chain.

Same 22.5s beat clock as Version D. Version D's movie is not written.
Layered v2 characters, Core-10 handoff/install/relief clips, and the
socket state machine. No power flicker and no lightsaber dish beam.

    python tools/gen_retro_e_demo.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from renderer.prepare_stage3d import prepare
from renderer.retro_art_profile import RetroArtProfile
from renderer.scene_layout import SceneLayoutSystem
from tools import gen_generality_manifests as gg
from tools.gen_interaction_proof import (
    _copy_stills,
    _patch_review_frames,
    _print_metrics,
    _render,
)
from tools.render_episode3d import load_json, run_command

OUT = ROOT / 'media' / 'm6_3d' / 'retro_ab' / 'version_e'
D_VIDEO = ROOT / 'media' / 'm6_3d' / 'retro_ab' / 'version_d' / 'version_d.mp4'
COMPARE = ROOT / 'media' / 'm6_3d' / 'retro_ab' / 'stills_de'
FPS = 24
DURATIONS = (2.2, 2.3, 2.7, 2.8, 3.2, 2.8, 3.2, 3.3)
MODELS = {
    'linlan': 'media/assets3d/characters/linlan_ps2_v2.glb',
    'chen': 'media/assets3d/characters/chen_ps2_v2.glb',
}
ROVER_MODEL = 'media/assets3d/atuo/atuo_rigged.glb'
LIN_SLOT = (-3.02, -3.34)
CHEN_STOP = (-2.15, -2.35)
VOICE_DIR = ROOT / 'media' / 'm6_3d' / 'retro_ab' / 'cd_audio'
LINES = (
    ('chen', '林岚！备件到了！'),
    ('linlan', '阵列全断了，快给我！'),
    ('linlan', '信号锁定，我们撑过去了。'),
)


def _art() -> dict:
    return RetroArtProfile().resolve({
        'id': 'retro_industrial_scifi',
        'modular_scene_assets': True,
    })


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


def _clip(start: int, end: int, name: str, *, one_shot: bool) -> dict:
    return {'from': int(start), 'to': int(end), 'clip': name, 'one_shot': bool(one_shot), 'speed': 1.0}


def _voice(index: int) -> str:
    path = VOICE_DIR / f'line_{index:02d}.wav'
    if not path.is_file():
        raise SystemExit(f'missing voice {path}')
    return path.resolve().as_posix()


def _build() -> tuple[dict, dict]:
    layout = SceneLayoutSystem().solve('observation_bay', lighting_profile='day_bay', validate=True)
    starts, total = gg.frame_starts(DURATIONS)
    lin_x, lin_z = gg.anchor_xz(layout, 'workstation_operator')
    sprint_from = starts[1] + 1
    sprint_to = starts[2] + 6
    handoff_start = starts[3] + 31
    contact = handoff_start + 16
    release = contact + 10
    step_to = starts[4] + 14
    install_start = starts[4] + 20
    align = install_start + 12
    insert = align + 14
    lock = insert + 16
    online = starts[6] + 8
    if online < lock + 4:
        raise SystemExit('online_frame must stay at least 4 frames after lock')
    meet = [
        round((CHEN_STOP[0] + lin_x) * 0.5, 3),
        1.28,
        round((CHEN_STOP[1] + lin_z) * 0.5, 3),
    ]
    stills = {
        '01_crisis': min(total - 1, starts[0] + 28),
        '02_sprint': min(total - 1, starts[1] + 18),
        '03_slam': min(total - 1, starts[2] + 22),
        '04_handoff': min(total - 1, contact),
        '05_install_lock': min(total - 1, min(lock + 8, starts[5] - 4)),
        '06_array_online': min(total - 1, starts[6] + 64),
        '07_relief_rover': min(total - 1, starts[7] + 40),
    }
    beats = [
        gg.beat(
            'b1_crisis', 1, DURATIONS[0],
            speaker='linlan', listener='chen', emotion='angry', listener_emotion='urgent',
            action='operate_console', reaction='firm_nod',
            camera_intent='environment_insert', importance=0.72, intensity=0.62,
            blocking='Linlan works the dead console while the storm sits on the glass',
            environment_event=_event('storm_approaching', starts[0], starts[2], 0.85, 'window'),
            event_focus=_focus(
                (1.15, 1.66, -1.35), (-1.6, 1.42, -3.9), 54, 48,
                pos_to=(0.7, 1.62, -1.6), look_to=(-2.2, 1.32, -3.7),
                subject='storm_approaching',
            ),
            target='console', objective='The bay is down and the storm is on the glass',
            conflict='The south array is already dead',
            description='Cool bay, dead console, storm beyond the viewport.',
        ),
        gg.beat(
            'b2_sprint', 1, DURATIONS[1],
            speaker='chen', listener='linlan', emotion='urgent', listener_emotion='angry',
            action='rush_enter', reaction='snap_look_back',
            camera_intent='action_follow', importance=0.8, intensity=0.78,
            blocking='Chen comes through the bulkhead already in frame and plants',
            environment_event=_event('door_open', starts[1], starts[1] + 36, 0.9, 'door'),
            event_focus=_focus(
                (2.35, 1.64, 1.85), (0.15, 1.42, 3.55), 46, 42,
                pos_to=(1.15, 1.58, -0.35), look_to=(-1.4, 1.38, -1.2),
                subject='door_open',
            ),
            target='door', text=LINES[0][1], audio_file=_voice(1),
            objective='Chen brings the spare in through the door',
            conflict='The bay is still dark',
            description='He is in the doorway as it opens, then skids to Linlan.',
        ),
        gg.beat(
            'b3_slam', 1, DURATIONS[2],
            speaker='linlan', listener='chen', emotion='angry', listener_emotion='urgent',
            action='argue_push', reaction='startle_recoil',
            camera_intent='over_the_shoulder', importance=0.88, intensity=0.84,
            blocking='Linlan hits the console and turns toward the spare',
            event_focus=_focus(
                (-0.55, 1.74, -1.55), (-2.55, 1.48, -2.85), 52, 46,
                pos_to=(-0.85, 1.68, -1.85),
                subject='linlan',
            ),
            target='console', text=LINES[1][1], audio_file=_voice(2),
            objective='She demands the spare',
            conflict='The array is still flat',
            description='Linlan slams the console and faces Chen.',
        ),
        gg.beat(
            'b4_handoff', 1, DURATIONS[3],
            speaker='chen', listener='linlan', emotion='focused', listener_emotion='focused',
            action='handoff_offer', reaction='handoff_receive',
            camera_intent='prop_mech_closeup', importance=0.9, intensity=0.7,
            hold=True,
            blocking='profile handoff, Chen facing the camera, both hands on the spare',
            event_focus=_focus(
                (-1.15, 1.62, -3.18), (meet[0], 1.36, meet[2]), 48, 44,
                pos_to=(-1.35, 1.58, -3.05),
                subject='spare_sensor',
            ),
            target='spare_sensor', objective='Ownership crosses at the palms',
            conflict='The slot is still empty',
            description='Close handoff. The camera stays off Chen\'s back.',
        ),
        gg.beat(
            'b5_install', 1, DURATIONS[4],
            speaker='linlan', emotion='focused',
            action='insert_module', reaction='firm_nod',
            camera_intent='prop_mech_closeup', importance=0.96, intensity=0.86,
            hold=True,
            blocking='she steps to the recess, slides the spare in, and the latch closes',
            event_focus=_focus(
                (-1.45, 1.52, -2.95), (-3.08, 1.22, -3.62), 48, 44,
                pos_to=(-1.7, 1.48, -3.1),
                subject='module_install',
            ),
            target='console', objective='The module seats and locks',
            conflict='The carrier is still amber',
            description='Install close-up. The spare leaves her hand and stays in the slot.',
        ),
        gg.beat(
            'b6_flinch', 1, DURATIONS[5],
            speaker='linlan', listener='chen', emotion='fear', listener_emotion='fear',
            action='flinch_dodge', reaction='startle_recoil',
            camera_intent='reaction', importance=0.84, intensity=0.72,
            blocking='both brace while the boot sputters and the storm flashes',
            event_focus=_focus(
                (0.8, 1.66, -1.4), (-1.8, 1.42, -3.8), 52, 46,
                pos_to=(0.3, 1.6, -1.7), look_to=(-2.3, 1.35, -3.6),
                subject='storm_approaching',
            ),
            target='window', objective='The first boot fails',
            conflict='The slot is locked but still not green',
            description='They flinch. The console stays amber.',
        ),
        gg.beat(
            'b7_array', 1, DURATIONS[6],
            speaker='linlan', listener='chen', emotion='focused', listener_emotion='focused',
            action='look_up_array', reaction='look_up_array',
            camera_intent='environment_insert', importance=0.94, intensity=0.8,
            hold=True,
            blocking='the dish and the slot go green only after the lock',
            environment_event=_event('array_restore', online, starts[7] + 12, 0.95, 'window'),
            event_focus=_focus(
                (-4.55, 1.58, -1.7), (-2.0, 1.32, -3.5), 56, 42,
                pos_to=(-2.15, 1.52, -2.65), look_to=(-3.02, 1.28, -3.58),
                subject='array_restore',
            ),
            target='window', objective='The array locks green after the module',
            conflict='The storm is still on the glass',
            description='Green slot, green dish, both of them still in the bay.',
        ),
        gg.beat(
            'b8_release', 1, DURATIONS[7],
            speaker='linlan', listener='chen', emotion='relieved', listener_emotion='relieved',
            action='exhale_relief', reaction='firm_nod',
            camera_intent='environment_insert', importance=0.9, intensity=0.45,
            hold=True,
            blocking='Linlan exhales and Atuo rolls into the same frame',
            event_focus=_focus(
                (-4.35, 1.56, -1.85), (-1.7, 1.28, -3.45), 54, 50,
                pos_to=(-4.05, 1.52, -2.05),
                subject='linlan',
            ),
            target='linlan', text=LINES[2][1], audio_file=_voice(3),
            objective='They confirm the lock',
            conflict='The storm is still outside',
            description='Relief, green carrier, Atuo in frame.',
        ),
    ]
    cast = {
        'linlan': gg.actor(
            layout, 'linlan', gait='confident', height=1.65, start_anchor='workstation_operator',
            facing=[
                gg.face(0, 'console'),
                gg.face(starts[3], 'chen'),
                {'from': starts[4] - 4, 'yaw_deg': 180.0},
            ],
            emotions=[
                gg.emotion(0, starts[4], 'angry', 0.85),
                gg.emotion(starts[4], starts[6], 'focused', 0.8),
                gg.emotion(starts[5], starts[6], 'fear', 0.75),
                gg.emotion(starts[6], starts[7], 'focused', 0.7),
                gg.emotion(starts[7], total, 'relieved', 0.7),
            ],
            moves=[{
                'from': starts[4] + 2,
                'to': step_to,
                'points': [[LIN_SLOT[0], LIN_SLOT[1]]],
                'auto_path': False,
                'ramp_s': 0.2,
                'gait': 'confident',
            }],
        ),
        'chen': gg.actor(
            layout, 'chen', gait='sprint', height=1.78, start_anchor='entry_threshold',
            facing=[
                {'from': 0, 'yaw_deg': 180.0},
                gg.face(sprint_to, 'linlan'),
            ],
            emotions=[
                gg.emotion(0, starts[4], 'urgent', 0.85),
                gg.emotion(starts[4], starts[6], 'tense', 0.7),
                gg.emotion(starts[5], starts[6], 'fear', 0.7),
                gg.emotion(starts[6], total, 'relieved', 0.6),
            ],
            moves=[{
                'from': sprint_from,
                'to': sprint_to,
                'points': [[round(CHEN_STOP[0], 3), round(CHEN_STOP[1], 3)]],
                'auto_path': False,
                'ramp_s': 0.4,
                'gait': 'sprint',
            }],
        ),
    }
    for actor_id, relative in MODELS.items():
        cast[actor_id]['model'] = gg.require_asset(relative)
        cast[actor_id]['look'] = {'preserve_materials': True, 'uniform': False}
    cast['chen'].pop('start_anchor', None)
    cast['chen']['start_pos'] = [0.0, 4.15]
    cast['chen']['yaw_deg'] = 180.0
    cast['chen']['clips'] = [
        _clip(starts[3], starts[4], 'Mocap_Handoff_Offer', one_shot=True),
    ]
    cast['linlan']['clips'] = [
        _clip(starts[3], starts[4], 'Mocap_Handoff_Receive', one_shot=True),
        _clip(starts[4], starts[5], 'PickUp_Table', one_shot=True),
        _clip(starts[7], total, 'Mocap_Exhale_Relief', one_shot=True),
    ]
    props = {
        'spare_sensor': {
            'kind': 'sensor',
            'entity_role': 'spare_sensor',
            'model': gg.require_asset(gg.SENSOR_MODEL),
            'handoff': {
                'from_actor': 'chen',
                'to_actor': 'linlan',
                'giver_side': 'right',
                'receiver_side': 'right',
                'start_frame': handoff_start,
                'contact_frame': contact,
                'release_frame': release,
                'end_frame': starts[4] - 1,
            },
            'install_into_socket': {
                'actor': 'linlan',
                'side': 'right',
                'start_frame': install_start,
                'align_frame': align,
                'insert_frame': insert,
                'lock_frame': lock,
                'online_frame': online,
                'withdraw_end_frame': online + 16,
            },
            'timeline': [
                {'from': 0, 'to': handoff_start, 'action': 'held', 'actor': 'chen', 'side': 'right'},
                {'from': starts[4], 'to': install_start, 'action': 'held', 'actor': 'linlan', 'side': 'right'},
            ],
        },
    }
    rover_from = starts[7] + 6
    rover_to = min(total - 8, rover_from + 40)
    rover = {
        'id': 'at1',
        'name': '阿拓',
        'model': gg.require_asset(ROVER_MODEL),
        'height_m': 0.95,
        'yaw_offset_deg': 0.0,
        'eye_offset': [0.0, 0.58, 0.05],
        'start': {'pos': [2.6, -1.4], 'yaw_deg': -130.0},
        'visibility': [{'from': 0, 'visible': False}, {'from': max(0, rover_from - 2), 'visible': True}],
        'moves': [{
            'from': rover_from,
            'to': rover_to,
            'points': [[0.2, -2.7]],
            'ramp_s': 0.3,
        }],
        'facing': [{'from': rover_from, 'target': 'linlan'}],
    }
    manifest = {
        'version': 1,
        'mode': 'generic',
        'renderer': 'godot3d',
        'language': 'zh',
        'title': '南侧阵列',
        'story': 'version E causal spare crisis',
        'width': 1280,
        'height': 720,
        'fps': FPS,
        'scene_type': 'observation_bay',
        'lighting_profile': 'day_bay',
        'door_start': 'closed',
        'dramatic_director': True,
        'performance_beats': True,
        'enable_cutaways': False,
        'art_profile': _art(),
        'cast': cast,
        'beats': beats,
        'shots': beats,
        'events': [
            _event('storm_approaching', starts[0], starts[2], 0.85, 'window'),
            _event('door_open', starts[1], starts[1] + 36, 0.9, 'door'),
            _event('door_close', starts[3], starts[4], 0.7, 'door'),
            _event('array_restore', online, starts[7] + 12, 0.95, 'window'),
        ],
        'sound_events': [
            {'kind': 'alarm_klaxon', 'frame': starts[0] + 6, 'volume': 0.45},
            {'kind': 'storm_thunder', 'frame': starts[0] + 16, 'volume': 0.55},
            {'kind': 'door_open_hiss', 'frame': starts[1] + 2, 'volume': 0.5},
            {'kind': 'footstep_heavy', 'frame': starts[1] + 10, 'volume': 0.4},
            {'kind': 'table_slam', 'frame': starts[2] + 14, 'volume': 0.7},
            {'kind': 'cloth_rustle', 'frame': contact - 4, 'volume': 0.3},
            {'kind': 'module_install', 'frame': lock, 'volume': 0.6},
            {'kind': 'console_error', 'frame': starts[5] + 8, 'volume': 0.45},
            {'kind': 'storm_thunder', 'frame': starts[5] + 12, 'volume': 0.55},
            {'kind': 'console_beep', 'frame': online, 'volume': 0.42},
            {'kind': 'mech_dish_servo', 'frame': online + 8, 'volume': 0.4},
        ],
        'props': props,
        'rover': rover,
    }
    timing = {
        'fps': FPS,
        'total_frames': total,
        'duration_s': round(total / FPS, 3),
        'beat_starts': starts,
        'beat_durations': list(DURATIONS),
        'contact_frame': contact,
        'lock_frame': lock,
        'online_frame': online,
        'stills': stills,
        'positions': {
            'linlan': [round(lin_x, 3), round(lin_z, 3)],
            'linlan_slot': list(LIN_SLOT),
            'chen_stop': list(CHEN_STOP),
            'meet': meet,
        },
    }
    return manifest, timing


def _ffmpeg() -> str:
    from renderer.mux import ffmpeg_executable
    return ffmpeg_executable()


def _grab(video: Path, frame: int, dest: Path) -> None:
    if not video.is_file():
        print(f'skip compare, missing {video.name}', flush=True)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    seconds = max(0.0, (int(frame) + 0.5) / FPS)
    subprocess.run(
        [
            _ffmpeg(), '-y', '-ss', f'{seconds:.3f}', '-i', str(video),
            '-frames:v', '1', str(dest),
        ],
        check=False,
        capture_output=True,
    )


def _compare(stills: dict) -> None:
    COMPARE.mkdir(parents=True, exist_ok=True)
    e_video = OUT / 'version_e.mp4'
    for name, frame in stills.items():
        e_still = OUT / 'stills' / f'{name}.png'
        if e_still.is_file():
            shutil.copyfile(e_still, COMPARE / f'e_{name}.png')
        _grab(D_VIDEO, int(frame), COMPARE / f'd_{name}.png')
        _grab(e_video, int(frame), COMPARE / f'e_video_{name}.png')
    print(f'comparison stills {COMPARE}', flush=True)


def main() -> int:
    manifest, timing = _build()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'review').mkdir(parents=True, exist_ok=True)
    manifest_path = OUT / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (OUT / 'timing.json').write_text(json.dumps(timing, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(
        f"version E {timing['duration_s']:.2f}s ({timing['total_frames']} frames) "
        f"lock={timing['lock_frame']} online={timing['online_frame']}",
        flush=True,
    )
    stage_path = OUT / 'stage3d.json'
    prepare(manifest_path, stage_path)
    _patch_review_frames(stage_path, timing['stills'])
    video = OUT / 'version_e.mp4'
    _render(stage_path, OUT, video)
    _copy_stills(OUT, timing['stills'])
    audit_path = OUT / 'interaction_audit.json'
    audit = load_json(audit_path) or {}
    (OUT / 'ownership_log.json').write_text(
        json.dumps({
            'ownership_log': audit.get('ownership_log') or [],
            'device_timeline': audit.get('device_timeline') or [],
        }, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    artistic_path = OUT / 'artistic_qc_report.json'
    run_command([
        sys.executable,
        str(ROOT / 'tools' / 'artistic_qc.py'),
        '--stage', str(stage_path),
        '--telemetry', str(OUT / 'qc_telemetry.json'),
        '--video', str(video),
        '--stills', str(OUT / 'stills'),
        '--interaction-audit', str(audit_path),
        '--out', str(artistic_path),
    ], capture=False)
    artistic = load_json(artistic_path) or {}
    print(f"artistic QC: {artistic.get('status', 'MISSING')}", flush=True)
    for check in artistic.get('checks') or []:
        if isinstance(check, dict):
            print(f"  {check.get('id', 'check')}: {check.get('status', 'MISSING')}", flush=True)
    _print_metrics(audit)
    _compare(timing['stills'])
    print(f'Video: {video}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
