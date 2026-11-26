#!/usr/bin/env python3
"""Phase 4 motion compare: the same body, overlays on versus the clip alone.

One planted character, one fixed eye-height camera, no dialogue. Raw shots
keep the actor on the stage mark and play the clip in place. The baker already
strips horizontal root travel, and this episode does not add it back.

    python tools/gen_motion_compare.py
"""
from __future__ import annotations

import argparse
import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from renderer.prepare_stage3d import _inside_bounds, prepare
from renderer.scene_layout import SceneLayoutSystem
from renderer.timeline_common import shot_duration_frames
from tools.render_episode3d import resolve_godot

OUT = ROOT / 'media' / 'm6_3d' / 'retro_ab' / 'motion_compare'
FPS = 24
SHOT_SECONDS = 2.0
CHARACTER = 'media/assets3d/characters/chen_ps2_v2.glb'
UAL1 = ROOT / 'media/assets3d/cc0/quaternius_universal_animation_library/UAL1_Standard.glb'
UAL2 = ROOT / 'media/assets3d/cc0/quaternius_universal_animation_library_2/UAL2_Standard.glb'
PS2 = ROOT / 'media/assets3d/animations/ps2_dramatic_pack.glb'
MOCAP = ROOT / 'media/assets3d/animations/mocap_core10_pack.glb'

# Mirrors Character3D._raw_clip_loops. Cycles play through; everything else holds.
_ONE_SHOT_WORDS = (
    'halt', 'turn', 'reach', 'handoff', 'confront', 'pickup', 'pick_up',
    'offer', 'slam', 'jump_land', 'jump_start', 'land', 'hit_', 'death',
    'recoil', 'flinch',
)


def _glb_document(path: Path) -> dict:
    data = path.read_bytes()
    offset = 12
    while offset + 8 <= len(data):
        chunk_len, chunk_type = struct.unpack_from('<II', data, offset)
        chunk = data[offset + 8:offset + 8 + chunk_len]
        offset += 8 + chunk_len
        if chunk_type == 0x4E4F534A:
            return json.loads(chunk.decode('utf-8').rstrip('\x00').strip())
    raise RuntimeError(f'{path} has no glTF JSON chunk')


def _clip_durations(path: Path) -> dict[str, float]:
    document = _glb_document(path)
    durations: dict[str, float] = {}
    for anim in document.get('animations') or []:
        name = str(anim.get('name') or '')
        if not name:
            continue
        duration = 0.0
        for sampler in anim.get('samplers') or []:
            accessor = document['accessors'][int(sampler['input'])]
            if accessor.get('max'):
                duration = max(duration, float(accessor['max'][0]))
        durations[name] = duration
    return durations


def _loops(clip: str) -> bool:
    key = clip.lower()
    if any(word in key for word in _ONE_SHOT_WORDS):
        return False
    return any(word in key for word in ('idle', 'walk', 'run', 'jog', 'sprint', 'loop'))


def _sample_seconds(loops: bool, duration: float) -> float:
    if loops:
        if duration <= 0.05:
            return 0.4
        return min(duration * 0.45, 1.25)
    if duration <= 0.05:
        return 0.5
    return min(max(0.4, duration * 0.72), 1.7)


def _planned_shots(catalog: dict[str, dict[str, float]]) -> tuple[list[dict], list[dict]]:
    """Real clip names only. Missing names are skipped, not invented."""
    wanted = [
        ('CTRL', 'UAL1', 'Idle_Loop', False, 'control pair, overlays on'),
        ('CTRL', 'UAL1', 'Idle_Loop', True, 'control pair, raw playback'),
        ('A', 'ps2_dramatic_pack', 'Sprint_Urgent_Upright', True, 'procedural sprint'),
        ('A', 'ps2_dramatic_pack', 'Abrupt_Halt_Skid', True, 'procedural halt'),
        ('A', 'ps2_dramatic_pack', 'Pace_Anxious_Turn', True, 'procedural turn'),
        ('A', 'ps2_dramatic_pack', 'Handoff_Offer_Firm', True, 'procedural handoff'),
        ('B', 'UAL1', 'Idle_Loop', True, 'UAL idle'),
        ('B', 'UAL1', 'Walk_Loop', True, 'UAL walk'),
        ('B', 'UAL1', 'Jog_Fwd_Loop', True, 'UAL run; the library has no clip named Run'),
        ('B', 'UAL1', 'Jump_Land', True, 'UAL stop; the library has no Stop or Turn clip'),
        ('B', 'UAL1', 'PickUp_Table', True, 'UAL pickup'),
        ('C', 'mocap_core10', 'Mocap_Idle_Sway_Loop', True, 'mocap idle'),
        ('C', 'mocap_core10', 'Mocap_Sprint_Carry', True, 'mocap sprint'),
        ('C', 'mocap_core10', 'Mocap_Abrupt_Halt', True, 'mocap halt'),
        ('C', 'mocap_core10', 'Mocap_Turn_Around', True, 'mocap turn'),
        ('C', 'mocap_core10', 'Mocap_Handoff_Offer', True, 'mocap handoff'),
        ('C', 'mocap_core10', 'Mocap_Angry_Confront', True, 'mocap confront'),
    ]
    skipped = [{
        'row': 'A',
        'wanted': 'idle',
        'reason': 'ps2_dramatic_pack.glb has no idle clip',
    }]
    shots: list[dict] = []
    frames_each = shot_duration_frames(SHOT_SECONDS, FPS)
    cursor = 0
    for row, source, clip, raw, note in wanted:
        names = catalog.get(source) or {}
        if clip not in names:
            skipped.append({'row': row, 'wanted': clip, 'reason': f'{source} has no clip {clip}'})
            continue
        duration = float(names[clip])
        loops = _loops(clip)
        sample_s = _sample_seconds(loops, duration)
        sample_frame = cursor + max(1, min(frames_each - 2, int(round(sample_s * FPS))))
        mode = 'overlay ON' if row == 'CTRL' and not raw else 'raw'
        label = f'{row} {mode} | {source} {clip}'
        shots.append({
            'row': row,
            'source': source,
            'clip': clip,
            'raw_playback': raw,
            'loops': loops,
            'duration_s': round(duration, 4),
            'note': note,
            'from': cursor,
            'frames': frames_each,
            'sample_frame': sample_frame,
            'caption': label,
            'slug': f'{len(shots) + 1:02d}_{row.lower()}_{"overlay" if not raw else "raw"}_{clip}',
        })
        cursor += frames_each
    return shots, skipped


def _camera(layout) -> tuple[list[float], list[float], float]:
    """Full-body, eye-height, static. The actor faces +Z, so the camera stands in front."""
    actor_z = 0.35
    pos = [0.0, 1.62, round(actor_z + 3.45, 3)]
    look = [0.0, 0.90, actor_z]
    fov = 36.0
    door = layout.doors[0].to_dict() if layout.doors else None
    if not _inside_bounds(pos, layout.camera_bounds, door):
        raise SystemExit(f'locked camera {pos} is outside the room')
    return pos, look, fov


def _manifest(shots: list[dict], skipped: list[dict], pos: list[float], look: list[float], fov: float) -> dict:
    clips = []
    actions = []
    review = {}
    beats = []
    for shot in shots:
        end = shot['from'] + shot['frames']
        clips.append({
            'from': shot['from'],
            'to': end,
            'clip': shot['clip'],
            'speed': 1.0,
        })
        if shot['raw_playback']:
            actions.append({
                'from': shot['from'],
                'to': end - 1,
                'action_id': f"raw_{shot['slug']}",
                'base_clip': shot['clip'],
                'dedicated_clip': shot['clip'],
                'intensity': 1.0,
                'one_shot': not shot['loops'],
                'raw_playback': True,
            })
        review[shot['slug']] = shot['sample_frame']
        beats.append({
            'id': shot['slug'],
            'scene_number': len(beats) + 1,
            'duration': SHOT_SECONDS,
            'frames': shot['frames'],
            'speaker': 'chen',
            'action': 'hold',
            'emotion': 'neutral',
            'importance': 0.35,
            'preferred_shot': 'wide',
            'speak': False,
            'caption': shot['caption'],
            'description': shot['note'],
        })
    return {
        'version': 1,
        'mode': 'generic',
        'renderer': 'godot3d',
        'title': 'Motion compare',
        'story': 'Same character and camera. Overlay path, then raw clips.',
        'width': 1280,
        'height': 720,
        'fps': FPS,
        'scene_type': 'observation_bay',
        'lighting_profile': 'day_bay',
        'enable_cutaways': False,
        'locked_camera': {'pos': pos, 'look_at': look, 'fov': fov},
        'review_frames': review,
        'cast': {
            'chen': {
                'name': 'Chen',
                'model': CHARACTER,
                'height_m': 1.76,
                'gait': 'casual',
                'start_pos': [0.0, 0.35],
                'yaw_deg': 0.0,
                'raw_playback': False,
                'look': {
                    'preserve_materials': True,
                    'uniform': False,
                    'ponytail': False,
                    'badge': False,
                },
                'animation_library': UAL1.as_posix(),
                'extra_libraries': [
                    UAL1.as_posix(),
                    UAL2.as_posix(),
                    MOCAP.as_posix(),
                    PS2.as_posix(),
                ],
                'clips': clips,
                'dramatic_actions': actions,
                'moves': [],
                'facing': [],
                'reaches': [],
                'nods': [],
            },
        },
        'beats': beats,
        'shots': beats,
        'meta_plan': {
            'root_policy': 'planted_in_place',
            'root_policy_note': (
                'The actor stays on the stage mark. Clip root travel is not applied. '
                'The baker already removes horizontal hip travel and leaves at most 8 cm of sway.'
            ),
            'skipped': skipped,
            'shots': shots,
        },
    }


def _ffprobe_seconds(video: Path) -> float | None:
    probe = shutil.which('ffprobe')
    if probe is None:
        return None
    completed = subprocess.run(
        [
            probe, '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', str(video),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    try:
        return round(float(completed.stdout.strip()), 3)
    except ValueError:
        return None


def _copy_stills(shots: list[dict]) -> list[str]:
    review = OUT / 'review'
    stills = OUT / 'stills'
    stills.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for shot in shots:
        source = review / f"frame_{shot['sample_frame']:06d}.png"
        target = stills / f"{shot['slug']}.png"
        if not source.is_file():
            raise SystemExit(f'missing review still {source}')
        shutil.copyfile(source, target)
        written.append(target.relative_to(ROOT).as_posix())
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Render the overlay-versus-raw motion compare.')
    parser.add_argument('--prepare-only', action='store_true', help='write the manifest and stop before Godot')
    args = parser.parse_args(argv)
    resolve_godot()
    if not (ROOT / CHARACTER).is_file():
        raise SystemExit(f'missing character {CHARACTER}')
    catalog = {
        'UAL1': _clip_durations(UAL1),
        'UAL2': _clip_durations(UAL2),
        'ps2_dramatic_pack': _clip_durations(PS2),
        'mocap_core10': _clip_durations(MOCAP),
    }
    shots, skipped = _planned_shots(catalog)
    if len(shots) < 8:
        raise SystemExit(f'only {len(shots)} shots resolved; clip names did not match the libraries')
    layout = SceneLayoutSystem().solve('observation_bay', lighting_profile='day_bay', validate=True)
    pos, look, fov = _camera(layout)
    manifest_doc = _manifest(shots, skipped, pos, look, fov)
    OUT.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT / 'manifest.json'
    plan = manifest_doc.pop('meta_plan')
    manifest_path.write_text(json.dumps(manifest_doc, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    stage = prepare(manifest_path, OUT / 'stage3d_preview.json')
    cameras = [json.dumps(shot.get('cameras'), sort_keys=True) for shot in stage['shots']]
    if len(set(cameras)) != 1:
        raise SystemExit('prepare did not keep one camera for every shot')
    if len(stage['shots']) != len(shots):
        raise SystemExit(f"prepare made {len(stage['shots'])} shots, expected {len(shots)}")
    if stage.get('speech'):
        raise SystemExit('compare episode must not schedule dialogue')
    plan['total_frames'] = int(stage['total_frames'])
    plan['seconds'] = round(int(stage['total_frames']) / FPS, 3)
    plan['camera'] = {'pos': pos, 'look_at': look, 'fov': fov, 'mode': 'static'}
    (OUT / 'shot_index.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(
        f"Manifest: {len(shots)} shots, {plan['seconds']}s, camera {pos} -> {look}",
        flush=True,
    )
    for item in skipped:
        print(f"skipped {item['row']} {item['wanted']}: {item['reason']}", flush=True)
    if args.prepare_only:
        return 0

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / 'tools' / 'render_episode3d.py'),
            '--manifest', str(manifest_path),
            '--out-dir', str(OUT),
            '--video-name', 'motion_compare.mp4',
        ],
        cwd=ROOT,
        check=False,
    )
    video = OUT / 'motion_compare.mp4'
    if not video.is_file():
        raise SystemExit(f'render failed with exit {completed.returncode} and did not write {video}')
    if completed.returncode != 0:
        print(f'render pipeline exited {completed.returncode}; keeping {video.name}', flush=True)
    stills = _copy_stills(shots)
    duration = _ffprobe_seconds(video)
    plan['video'] = video.relative_to(ROOT).as_posix()
    plan['video_seconds'] = duration
    plan['stills'] = stills
    (OUT / 'shot_index.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Video: {video}', flush=True)
    if duration is not None:
        print(f'Duration: {duration}s', flush=True)
    for path in stills:
        print(f'Still: {path}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
