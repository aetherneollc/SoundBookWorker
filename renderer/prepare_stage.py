#!/usr/bin/env python3
"""Turn a Worker manifest into the Godot 2D ``stage.json``.

Builds a cached mesh rig for every character master, locks the timeline to the
voice tracks, and asks ``director.build_stage`` for the document the player
reads. A stage that breaks the v1 contract is refused instead of rendered.

    python renderer/prepare_stage.py manifest.json stage.json --rig-cache media/rigs
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

try:
    from calibrate_plate import calibrate_plate_file
    from director import build_stage, validate_stage
    from render import write_intro_chime
    from rig_mesh import build_mesh_rig
    from timeline_common import (
        build_voice_locked_timeline, frame, shot_duration_frames, shot_is_animated,
        speech_items_for_track, track_start_seconds,
    )
except ImportError:  # ``python -m unittest`` imports this file as part of the package.
    from renderer.calibrate_plate import calibrate_plate_file
    from renderer.director import build_stage, validate_stage
    from renderer.render import write_intro_chime
    from renderer.rig_mesh import build_mesh_rig
    from renderer.timeline_common import (
        build_voice_locked_timeline, frame, shot_duration_frames, shot_is_animated,
        speech_items_for_track, track_start_seconds,
    )


# Bump the salt when the mesh document changes shape, so old caches are not reused.
_MESH_SALT = b'\0godot2d-mesh-v1'
_IMAGE_KEYS = ('master_file', 'rig_file', 'file')


def _mesh_cache_valid(rig_json: Path) -> bool:
    if not rig_json.is_file():
        return False
    try:
        data = json.loads(rig_json.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return False
    if data.get('version') != 1 or not data.get('bones') or not data.get('groups'):
        return False
    texture = rig_json.parent / str(data.get('texture') or 'texture.png')
    return texture.is_file()


def _source_image(entry: dict) -> Path | None:
    for key in _IMAGE_KEYS:
        raw = entry.get(key)
        if not isinstance(raw, str) or not raw or raw.lower().endswith('.json'):
            continue
        path = Path(raw)
        if path.is_file():
            return path
    return None


def _existing_mesh(entry: dict) -> Path | None:
    for key in ('rig_json', 'rig_file', 'master_file', 'file'):
        raw = entry.get(key)
        if not isinstance(raw, str) or not raw.lower().endswith('.json'):
            continue
        path = Path(raw)
        if _mesh_cache_valid(path):
            return path
    return None


def cached_mesh_rig(image: Path, cache_root: Path, memo: dict[str, Path]) -> Path:
    """Return the mesh ``rig.json`` for ``image``, building it once per file hash."""
    resolved = str(image.resolve())
    if resolved in memo:
        return memo[resolved]
    digest = hashlib.sha256(image.read_bytes())
    digest.update(_MESH_SALT)
    out_dir = cache_root / f'mesh-{digest.hexdigest()[:16]}'
    rig_json = out_dir / 'rig.json'
    if _mesh_cache_valid(rig_json):
        print(f'prepare_stage: mesh cache hit {rig_json}', flush=True)
    else:
        print(f'prepare_stage: building mesh {image.name} -> {out_dir.name}', flush=True)
        rig_json = build_mesh_rig(image, out_dir)
    memo[resolved] = rig_json
    return rig_json


def _rig_path_for(entry: dict, cache_root: Path, memo: dict[str, Path]) -> Path | None:
    existing = _existing_mesh(entry)
    if existing is not None:
        return existing
    image = _source_image(entry)
    if image is None:
        return None
    return cached_mesh_rig(image, cache_root, memo)


def _publish_rig(rigs: dict[str, dict], entry: dict, rig_json: Path) -> None:
    ident = str(entry.get('id') or entry.get('character_id') or '')
    name = str(entry.get('name') or '')
    record = {
        'rig_json': rig_json.resolve().as_posix(),
        'id': ident,
        'name': name,
    }
    scale = entry.get('stature_scale')
    if isinstance(scale, (int, float)) and not isinstance(scale, bool):
        record['stature_scale'] = float(scale)
    for key in (ident, name):
        if not key:
            continue
        current = rigs.get(key)
        if current is None:
            rigs[key] = dict(record)
            continue
        if current.get('rig_json') == record['rig_json']:
            if 'stature_scale' not in current and 'stature_scale' in record:
                current['stature_scale'] = record['stature_scale']
            continue
        merged = dict(record)
        if 'stature_scale' not in merged and 'stature_scale' in current:
            merged['stature_scale'] = current['stature_scale']
        rigs[key] = merged


def ensure_mesh_rigs(doc: dict, cache_root: Path) -> dict[str, dict]:
    """Build or reuse a mesh ``rig.json`` for every master the stage can stand on."""
    cache_root.mkdir(parents=True, exist_ok=True)
    memo: dict[str, Path] = {}
    rigs: dict[str, dict] = {}
    for entry in doc.get('character_rigs') or []:
        if not isinstance(entry, dict):
            continue
        rig_json = _rig_path_for(entry, cache_root, memo)
        if rig_json is not None:
            _publish_rig(rigs, entry, rig_json)
        elif entry.get('master_file') or entry.get('rig_file'):
            label = entry.get('id') or entry.get('name') or 'character'
            print(f'prepare_stage: no readable master for {label}', flush=True)
    for shot in doc.get('shots') or []:
        if not isinstance(shot, dict):
            continue
        motion = shot.get('story_motion')
        if not isinstance(motion, dict):
            continue
        for actor in motion.get('actors') or []:
            if not isinstance(actor, dict):
                continue
            rig_json = _rig_path_for(actor, cache_root, memo)
            if rig_json is not None:
                _publish_rig(rigs, actor, rig_json)
    return rigs


def _absolutize(value: object) -> object:
    if not isinstance(value, str) or not value or value.startswith('res://'):
        return value
    path = Path(value)
    if path.is_file():
        return path.resolve().as_posix()
    return value.replace('\\', '/')


def _absolutize_plate(plate: dict) -> None:
    for key in ('plate_file', 'far_file', 'near_file', 'file', 'src', 'far', 'near', 'plate'):
        if key in plate:
            plate[key] = _absolutize(plate.get(key))


def _attach_plate_metrics(shot: dict, cache: dict[str, dict]) -> None:
    plates = []
    if isinstance(shot.get('plate'), dict):
        plates.append(shot['plate'])
    motion = shot.get('story_motion')
    if isinstance(motion, dict) and isinstance(motion.get('plate'), dict):
        plates.append(motion['plate'])
    for plate in plates:
        _absolutize_plate(plate)
        raw = plate.get('plate_file') or plate.get('file') or plate.get('src')
        if not isinstance(raw, str) or not raw:
            continue
        path = Path(raw)
        if not path.is_file():
            continue
        key = str(path.resolve())
        if key not in cache:
            try:
                cache[key] = calibrate_plate_file(path)
            except Exception as exc:
                print(
                    f'prepare_stage: plate metrics skipped for {path.name} ({type(exc).__name__})',
                    flush=True,
                )
                cache[key] = {}
        if cache[key] and 'plate_metrics' not in shot:
            shot['plate_metrics'] = cache[key]


def _prepare_timeline_shot(shot: dict, duration_frames: int, start: int, plate_cache: dict[str, dict]) -> dict:
    prepared = dict(shot)
    prepared['from'] = start
    prepared['duration'] = duration_frames
    prepared['frames'] = duration_frames
    if isinstance(prepared.get('plate'), dict):
        prepared['plate'] = dict(prepared['plate'])
    motion = prepared.get('story_motion')
    if isinstance(motion, dict):
        motion = dict(motion)
        if isinstance(motion.get('plate'), dict):
            motion['plate'] = dict(motion['plate'])
        prop = motion.get('set_prop')
        if isinstance(prop, dict):
            prop = dict(prop)
            raw = prop.get('prop_file') or prop.get('image')
            if isinstance(raw, str) and raw and Path(raw).is_file():
                prop['prop_src'] = Path(raw).resolve().as_posix()
            motion['set_prop'] = prop
        prepared['story_motion'] = motion
    _attach_plate_metrics(prepared, plate_cache)
    return prepared


def _audio_item(path: Path, start_ms: int, volume: float, loop: bool) -> dict:
    return {
        'file': path.resolve().as_posix(),
        'start_ms': max(0, int(start_ms)),
        'volume': max(0.0, min(4.0, float(volume))),
        'loop': bool(loop),
    }


def prepare(manifest_path: Path, output_path: Path, rig_cache: Path | None = None) -> dict:
    """Read ``manifest_path``, write a playable ``stage.json``, and return it."""
    doc = json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    if doc.get('version') != 1 or not isinstance(doc.get('shots'), list) or not doc['shots']:
        raise ValueError('Expected a v1 manifest with shots')
    width, height = int(doc.get('width', 1280)), int(doc.get('height', 720))
    fps = int(doc.get('fps', 24))
    if (width, height) != (1280, 720) or not 1 <= fps <= 60:
        raise ValueError('Godot 2D stage only accepts 1280x720 and a frame rate from 1 to 60')

    cache_root = Path(rig_cache) if rig_cache is not None else Path(
        os.environ.get('SOUNDBOOK_RIG_CACHE', 'media/rigs')
    )
    rigs_map = ensure_mesh_rigs(doc, cache_root)

    shots = [dict(shot) for shot in doc['shots'] if isinstance(shot, dict)]
    raw_tracks = doc.get('audio_tracks') if isinstance(doc.get('audio_tracks'), list) else []
    tracks = [dict(track) for track in raw_tracks if isinstance(track, dict)]
    if isinstance(doc.get('audio'), dict):
        tracks.append({**doc['audio'], 'start_ms': 0, 'volume': 1, '_full_length': True})

    intro_seconds, captions = build_voice_locked_timeline(doc, shots, tracks, fps)
    intro_frames = frame(intro_seconds, fps)
    plate_cache: dict[str, dict] = {}
    timeline = []
    cursor = intro_frames
    for shot in shots:
        duration_frames = shot_duration_frames(shot.get('duration', 4), fps)
        timeline.append(_prepare_timeline_shot(shot, duration_frames, cursor, plate_cache))
        cursor += duration_frames

    voice_locked = doc.get('timeline_mode') == 'voice_locked'
    animated = {int(shot.get('scene_number', 0)) for shot in timeline if shot_is_animated(shot)}
    language = str(doc.get('language') or 'zh')
    audio_tracks: list[dict] = []
    speech: list[dict] = []
    if intro_frames:
        chime = cache_root / 'intro-chime.wav'
        chime.parent.mkdir(parents=True, exist_ok=True)
        write_intro_chime(chime)
        audio_tracks.append(_audio_item(chime, 0, 0.5, False))
        intro_voice = Path(doc['intro_voice_file']) if doc.get('intro_voice_file') else None
        if intro_voice is not None and intro_voice.is_file():
            lead = round(frame(0.22, fps) * 1000 / fps)
            audio_tracks.append(_audio_item(intro_voice, lead, 1.0, False))
    for track in tracks:
        raw_file = track.get('file')
        if not raw_file:
            continue
        path = Path(str(raw_file))
        if not path.is_file():
            raise FileNotFoundError(path)
        start = track_start_seconds(track, voice_locked=voice_locked, intro_seconds=intro_seconds)
        audio_tracks.append(_audio_item(
            path, round(start * 1000), float(track.get('volume', 1)), bool(track.get('loop')),
        ))
        speech.extend(speech_items_for_track(
            track, start=start, fps=fps, language=language, animated_scenes=animated,
        ))

    subtitle = doc.get('episode_subtitle') or doc.get('intro_subtitle') or ''
    if not isinstance(subtitle, str):
        subtitle = ''
    intro_info = {
        'frames': intro_frames,
        'episode_number': int(doc.get('episode_number') or 1),
        'title': str(doc.get('episode_title') or ''),
        'subtitle': subtitle,
    }
    cta_card = doc.get('cta_card') if isinstance(doc.get('cta_card'), dict) else None
    cta_name = str(cta_card.get('name') or '').strip() if cta_card else ''
    cta_info = {
        'frames': frame(3.8, fps) if cta_name else 0,
        'title': cta_name,
        'subtitle': str((cta_card.get('tagline') or cta_card.get('subtitle') or '') if cta_card else ''),
    }
    stage = build_stage(doc, timeline, speech, captions, rigs_map, audio_tracks, intro_info, cta_info)
    problems = validate_stage(stage)
    if problems:
        detail = '\n'.join(f'- {problem}' for problem in problems)
        raise ValueError(f'stage.json failed the Godot 2D contract:\n{detail}')

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(stage, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(
        f"Prepared stage: {len(stage['shots'])} shots, {len(stage['audio'])} audio tracks, "
        f"{len(stage['captions'])} captions, {len(stage['rigs'])} rigs, {stage['total_frames']} frames",
        flush=True,
    )
    return stage


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description='Build a Godot 2D stage.json from a SoundBook manifest.')
    parser.add_argument('manifest', type=Path)
    parser.add_argument('stage', type=Path)
    parser.add_argument('--rig-cache', type=Path, default=None,
                        help='Directory for cached mesh rigs (default: media/rigs or SOUNDBOOK_RIG_CACHE)')
    args = parser.parse_args(argv)
    cache = args.rig_cache
    if cache is None:
        cache = Path(os.environ.get('SOUNDBOOK_RIG_CACHE', 'media/rigs'))
    prepare(args.manifest, args.stage, cache)


if __name__ == '__main__':
    main(sys.argv[1:])
