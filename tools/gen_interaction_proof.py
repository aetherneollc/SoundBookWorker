#!/usr/bin/env python3
"""10-second silent causal interaction proof.

Chen holds the spare, hands it to Linlan in a profile close-up, and she locks
it into the recessed console slot. The dish and the module LEDs go green only
after that lock. No dialogue.

    python tools/gen_interaction_proof.py
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from renderer.prepare_stage3d import prepare
from renderer.retro_art_profile import RetroArtProfile
from tools import gen_generality_manifests as gg
from tools.render_episode3d import (
    godot_command,
    load_json,
    print_summary,
    resolve_godot,
    run_command,
    stage_env,
)

OUT = ROOT / 'media' / 'm6_3d' / 'retro_ab' / 'interaction_proof'
FPS = 24
# 1.5 + 2.0 + 1.5 + 3.3 + 1.7 = 10.0s. The install close-up stays up
# long enough to watch the hand leave the locked module.
DURATIONS = (1.5, 2.0, 1.5, 3.3, 1.7)
MODELS = {
    'linlan': 'media/assets3d/characters/linlan_ps2_v2.glb',
    'chen': 'media/assets3d/characters/chen_ps2_v2.glb',
}
ROVER_MODEL = 'media/assets3d/atuo/atuo_rigged.glb'
SENSOR_MODEL = gg.SENSOR_MODEL

# Floor positions (x, z). They face each other mostly along X, so a camera on
# the door side (+Z) sees profiles and never Chen's back (+X).
CHEN = (-1.50, -2.10)
LIN_MEET = (-2.50, -2.40)
LIN_SLOT = (-3.02, -3.34)


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


def _build() -> tuple[dict, dict]:
    starts, total = gg.frame_starts(DURATIONS)
    hold_end, handoff_end, walk_end, install_end = starts[1], starts[2], starts[3], starts[4]
    # Handoff occupies the whole second beat. Contact is the mute-watch midpoint.
    # Reach starts almost immediately so the spare is up in his hand during
    # the opening, while contact stays at the mute-watch midpoint.
    handoff_start = 8
    contact = hold_end + (handoff_end - hold_end) // 2
    release = contact + 12
    walk_stop = walk_end - 8
    install_start = walk_end + 20
    align = 148
    insert = 158
    lock = 168
    online = install_end + 6
    if online < lock + 4:
        raise SystemExit('online_frame must stay at least 4 frames after lock')
    meet = [
        round((CHEN[0] + LIN_MEET[0]) * 0.5, 3),
        1.28,
        round((CHEN[1] + LIN_MEET[1]) * 0.5, 3),
    ]
    stills = {
        '01_chen_holds': min(total - 1, hold_end // 2),
        '02_transfer_contact': min(total - 1, contact),
        '03_linlan_holds': min(total - 1, handoff_end + max(4, (walk_stop - handoff_end) // 2)),
        '04_align_socket': min(total - 1, align),
        '05_insert_lock': min(total - 1, install_end - 3),
        '06_device_online': min(total - 1, online + 28),
    }

    def person(actor_id: str, height: float, gait: str, start, yaw: float, facing, emotions, moves=None, clips=None) -> dict:
        spec = {
            'name': '林岚' if actor_id == 'linlan' else '陈工',
            'model': gg.require_asset(MODELS[actor_id]),
            'height_m': height,
            'gait': gait,
            'start_pos': [round(start[0], 3), round(start[1], 3)],
            'yaw_deg': round(float(yaw), 2),
            'facing': facing,
            'emotions': emotions,
            'look': {'preserve_materials': True, 'uniform': False},
        }
        if moves:
            spec['moves'] = moves
        if clips:
            spec['clips'] = clips
        return spec

    cast = {
        'chen': person(
            'chen', 1.78, 'heavy', CHEN, -100.0,
            [gg.face(0, 'linlan')],
            [
                gg.emotion(0, install_end, 'focused', 0.7),
                gg.emotion(install_end, total, 'relieved', 0.55),
            ],
            clips=[
                _clip(0, handoff_end, 'Mocap_Handoff_Offer', one_shot=True),
            ],
        ),
        'linlan': person(
            'linlan', 1.65, 'confident', LIN_MEET, 76.0,
            [
                gg.face(0, 'chen'),
                {'from': walk_end - 6, 'yaw_deg': 180.0},
            ],
            [
                gg.emotion(0, walk_end, 'focused', 0.75),
                gg.emotion(walk_end, online, 'focused', 0.85),
                gg.emotion(online, total, 'relieved', 0.7),
            ],
            moves=[{
                'from': handoff_end + 2,
                'to': walk_stop,
                'points': [[round(LIN_SLOT[0], 3), round(LIN_SLOT[1], 3)]],
                'auto_path': False,
                'ramp_s': 0.28,
                'gait': 'confident',
            }],
            clips=[
                _clip(hold_end, handoff_end, 'Mocap_Handoff_Receive', one_shot=True),
                _clip(walk_end, install_end, 'PickUp_Table', one_shot=True),
                _clip(install_end, total, 'Mocap_Exhale_Relief', one_shot=True),
            ],
        ),
    }
    beats = [
        gg.beat(
            'p1_hold', 1, DURATIONS[0],
            speaker='chen', listener='linlan', emotion='focused', listener_emotion='focused',
            action='handoff_offer', reaction='firm_nod',
            camera_intent='over_the_shoulder', importance=0.62, intensity=0.45,
            blocking='Chen presents the spare in his right hand, facing Linlan',
            event_focus=_focus(
                (-1.55, 1.78, -3.28), (-1.65, 1.28, -2.12), 58, 54,
                subject='spare_sensor',
            ),
            target='spare_sensor', objective='Chen shows the spare is in his hand',
            conflict='The console slot is still empty',
            description='Chen holds the spare and faces Linlan in front of the console.',
        ),
        gg.beat(
            'p2_handoff', 1, DURATIONS[1],
            speaker='chen', listener='linlan', emotion='focused', listener_emotion='focused',
            action='handoff_offer', reaction='handoff_receive',
            camera_intent='prop_mech_closeup', importance=0.9, intensity=0.66,
            hold=True,
            blocking='both right hands meet the module from a profile angle',
            event_focus=_focus(
                (-1.85, 1.64, -0.25), (-1.95, 1.40, -2.22), 42, 38,
                pos_to=(-1.90, 1.60, -0.55),
                subject='spare_sensor',
            ),
            target='spare_sensor', objective='Ownership crosses at the palms',
            conflict='The slot is still dark',
            description='Profile handoff. The camera stays on the door side so Chen is not seen from behind.',
        ),
        gg.beat(
            'p3_carry', 1, DURATIONS[2],
            speaker='linlan', listener='chen', emotion='focused', listener_emotion='focused',
            action='operate_console', reaction='firm_nod',
            camera_intent='over_the_shoulder', importance=0.74, intensity=0.6,
            blocking='Linlan turns and walks the spare to the recessed slot',
            event_focus=_focus(
                (-1.40, 1.64, -3.30), (-2.62, 1.32, -2.88), 58, 52,
                pos_to=(-1.60, 1.58, -3.18),
                subject='linlan',
            ),
            target='console', objective='Linlan carries the spare alone',
            conflict='The rails are still empty',
            description='Linlan holds the module and steps up to the console slot.',
        ),
        gg.beat(
            'p4_install', 1, DURATIONS[3],
            speaker='linlan', emotion='focused',
            action='insert_module', reaction='firm_nod',
            camera_intent='prop_mech_closeup', importance=0.96, intensity=0.88,
            hold=True,
            blocking='she aligns the spare with the rails and locks it in',
            event_focus=_focus(
                (-1.45, 1.52, -2.95), (-3.08, 1.22, -3.62), 48, 44,
                pos_to=(-1.70, 1.48, -3.10),
                subject='module_install',
            ),
            target='console', objective='The module seats and locks',
            conflict='The carrier is still offline',
            description='Install close-up. The module leaves her hand and stays in the slot.',
        ),
        gg.beat(
            'p5_online', 1, DURATIONS[4],
            speaker='linlan', listener='chen', emotion='relieved', listener_emotion='relieved',
            action='exhale_relief', reaction='firm_nod',
            camera_intent='environment_insert', importance=0.84, intensity=0.4,
            hold=True,
            blocking='Linlan exhales as the slot and the dish go green',
            environment_event=_event('array_restore', online, total, 0.9, 'window'),
            event_focus=_focus(
                (-4.65, 1.58, -1.75), (-2.1, 1.32, -3.45), 56, 52,
                pos_to=(-4.35, 1.54, -2.05), look_to=(-1.6, 1.28, -3.70),
                subject='array_restore',
            ),
            target='window', objective='The console comes online after the lock',
            conflict='The storm is still on the glass',
            description='LEDs and the dish beacon turn green. Linlan relaxes. Atuo rolls in.',
        ),
    ]
    props = {
        'spare_sensor': {
            'kind': 'sensor',
            'entity_role': 'spare_sensor',
            'model': gg.require_asset(SENSOR_MODEL),
            'handoff': {
                'from_actor': 'chen',
                'to_actor': 'linlan',
                'giver_side': 'right',
                'receiver_side': 'right',
                'start_frame': handoff_start,
                'contact_frame': contact,
                'release_frame': release,
                'end_frame': handoff_end - 1,
            },
            'install_into_socket': {
                'actor': 'linlan',
                'side': 'right',
                'start_frame': install_start,
                'align_frame': align,
                'insert_frame': insert,
                'lock_frame': lock,
                'online_frame': online,
                'withdraw_end_frame': online + 10,
            },
            'timeline': [
                {'from': 0, 'to': handoff_start, 'action': 'held', 'actor': 'chen', 'side': 'right'},
                {'from': handoff_end, 'to': install_start, 'action': 'held', 'actor': 'linlan', 'side': 'right'},
            ],
        },
    }
    rover_from = online + 4
    rover_to = min(total - 6, rover_from + 36)
    rover = {
        'id': 'at1',
        'name': '阿拓',
        'model': gg.require_asset(ROVER_MODEL),
        'height_m': 0.95,
        'yaw_offset_deg': 0.0,
        'eye_offset': [0.0, 0.58, 0.05],
        'start': {'pos': [2.4, -1.7], 'yaw_deg': -120.0},
        'visibility': [{'from': 0, 'visible': False}, {'from': max(0, rover_from - 2), 'visible': True}],
        'moves': [{
            'from': rover_from,
            'to': rover_to,
            'points': [[0.15, -2.85]],
            'ramp_s': 0.25,
        }],
        'facing': [{'from': rover_from, 'target': 'linlan'}],
    }
    manifest = {
        'version': 1,
        'mode': 'generic',
        'renderer': 'godot3d',
        'language': 'zh',
        'title': '交互证明',
        'story': 'silent causal handoff and socket lock',
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
            _event('array_restore', online, total, 0.9, 'window'),
        ],
        'sound_events': [
            {'kind': 'cloth_rustle', 'frame': contact - 4, 'volume': 0.28},
            {'kind': 'module_install', 'frame': lock, 'volume': 0.55},
            {'kind': 'console_beep', 'frame': online, 'volume': 0.4},
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
        'release_frame': release,
        'align_frame': align,
        'insert_frame': insert,
        'lock_frame': lock,
        'online_frame': online,
        'stills': stills,
        'positions': {
            'chen': list(CHEN),
            'linlan_meet': list(LIN_MEET),
            'linlan_slot': list(LIN_SLOT),
            'meet': meet,
        },
    }
    return manifest, timing


def _patch_review_frames(stage_path: Path, stills: dict) -> None:
    stage = json.loads(stage_path.read_text(encoding='utf-8'))
    meta = stage.setdefault('meta', {})
    review = dict(meta.get('review_frames') or {})
    review.update({name: int(frame) for name, frame in stills.items()})
    meta['review_frames'] = review
    stage_path.write_text(json.dumps(stage, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _render(stage_path: Path, out_dir: Path, video: Path) -> dict | None:
    stage = json.loads(stage_path.read_text(encoding='utf-8'))
    fps = int(stage.get('fps') or 0)
    total_frames = int(stage.get('total_frames') or 0)
    godot = resolve_godot()
    movie = out_dir / 'silent.avi'
    review = (stage.get('meta') or {}).get('review_frames') if isinstance(stage.get('meta'), dict) else {}
    sample_frames = []
    for value in (review or {}).values():
        if isinstance(value, int) and value >= 0:
            sample_frames.append(value)
    sample_frames = sorted(set(sample_frames))
    started = time.perf_counter()
    rendered = run_command(
        godot_command(godot, fps, total_frames, movie),
        capture=False,
        env=stage_env(out_dir, sample_frames),
    )
    wall_s = time.perf_counter() - started
    if rendered.returncode != 0:
        raise SystemExit(f'Godot exited {rendered.returncode}')
    if not movie.is_file():
        raise SystemExit(f'Godot did not write {movie}')
    muxed = run_command([
        sys.executable,
        str(ROOT / 'renderer' / 'mux.py'),
        str(movie),
        str(stage_path),
        str(video),
    ])
    if muxed.returncode != 0:
        raise SystemExit(f'mux failed with exit {muxed.returncode}; kept {movie.name}')
    try:
        movie.unlink()
    except OSError as exc:
        print(f'warning: could not remove {movie.name}: {exc}', file=sys.stderr, flush=True)
    report_path = out_dir / 'qc_report.json'
    run_command([
        sys.executable,
        str(ROOT / 'tools' / 'qc_pipeline.py'),
        '--stage', str(stage_path),
        '--telemetry', str(out_dir / 'qc_telemetry.json'),
        '--video', str(video),
        '--stills', str(out_dir / 'review'),
        '--registry', str(ROOT / 'media' / 'assets3d' / 'registry.json'),
        '--out', str(report_path),
    ], capture=True)
    print_summary(stage, wall_s, load_json(out_dir / 'stage-result.json'), load_json(report_path), video)
    return load_json(report_path)


def _copy_stills(out_dir: Path, stills: dict) -> None:
    review = out_dir / 'review'
    dest = out_dir / 'stills'
    dest.mkdir(parents=True, exist_ok=True)
    for name, frame in stills.items():
        source = review / f'frame_{int(frame):06d}.png'
        target = dest / f'{name}.png'
        if not source.is_file():
            print(f'missing still {source.name}', file=sys.stderr, flush=True)
            continue
        shutil.copyfile(source, target)
        print(f'still {target.name} <= frame {frame}', flush=True)


def _first_state(rows: list, state: str) -> int | None:
    for row in rows:
        if isinstance(row, dict) and str(row.get('state') or '') == state:
            return int(row.get('frame') or 0)
    return None


def _print_metrics(audit: dict) -> None:
    timeline = audit.get('device_timeline') if isinstance(audit.get('device_timeline'), list) else []
    ownership = audit.get('ownership_log') if isinstance(audit.get('ownership_log'), list) else []
    lock_frame = _first_state(timeline, 'locked')
    if lock_frame is None:
        lock_frame = _first_state(ownership, 'locked')
    online_frame = _first_state(timeline, 'device_online')
    if online_frame is None:
        online_frame = _first_state(ownership, 'device_online')
    print('interaction metrics', flush=True)
    for key in (
        'handoff_contact_error_m',
        'socket_insert_error_m',
        'causal_chain_valid',
        'prop_occlusion_frames',
        'head_cropped_frames',
        'empty_doorway_dwell_frames',
        'one_shot_loop_violations',
    ):
        print(f'  {key}: {audit.get(key)}', flush=True)
    print(f'  lock_frame: {lock_frame}', flush=True)
    print(f'  device_online_frame: {online_frame}', flush=True)
    if lock_frame is not None and online_frame is not None:
        print(f'  device_online_minus_lock_frames: {online_frame - lock_frame}', flush=True)
    violations = audit.get('invariant_violations') or []
    errors = audit.get('chain_errors') or []
    if violations or errors:
        print(f'  invariant_violations: {json.dumps(violations, ensure_ascii=False)}', flush=True)
        print(f'  chain_errors: {json.dumps(errors, ensure_ascii=False)}', flush=True)


def main() -> int:
    manifest, timing = _build()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'review').mkdir(parents=True, exist_ok=True)
    manifest_path = OUT / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (OUT / 'timing.json').write_text(json.dumps(timing, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(
        f"proof timeline {timing['duration_s']:.2f}s ({timing['total_frames']} frames) "
        f"lock={timing['lock_frame']} online={timing['online_frame']}",
        flush=True,
    )
    stage_path = OUT / 'stage3d.json'
    prepare(manifest_path, stage_path)
    _patch_review_frames(stage_path, timing['stills'])
    video = OUT / 'interaction_proof.mp4'
    _render(stage_path, OUT, video)
    _copy_stills(OUT, timing['stills'])
    audit_path = OUT / 'interaction_audit.json'
    audit = load_json(audit_path) or {}
    ownership = {
        'ownership_log': audit.get('ownership_log') or [],
        'device_timeline': audit.get('device_timeline') or [],
    }
    (OUT / 'ownership_log.json').write_text(
        json.dumps(ownership, ensure_ascii=False, indent=2) + '\n',
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
    print(f'Video: {video}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
