#!/usr/bin/env python3
"""Turn the existing manifest into a frame-accurate Remotion composition."""
from __future__ import annotations
import json, math, os, re, shutil, subprocess, sys
from pathlib import Path
try:
    from calibrate_plate import calibrate_plate_file
    from render import media_duration, subtitle_chunks, write_intro_chime
    from rig_characters import resolve_slot_view_angle, rig_character_master
    from voice_cues import speech_cues
    from timeline_common import (
        build_voice_locked_timeline, frame, parse_srt, shot_duration_frames,
        shot_is_animated, speech_items_for_track, track_start_seconds,
    )
except ImportError:  # ``python -m unittest`` imports this file as part of the package.
    from renderer.calibrate_plate import calibrate_plate_file
    from renderer.render import media_duration, subtitle_chunks, write_intro_chime
    from renderer.rig_characters import resolve_slot_view_angle, rig_character_master
    from renderer.voice_cues import speech_cues
    from renderer.timeline_common import (
        build_voice_locked_timeline, frame, parse_srt, shot_duration_frames,
        shot_is_animated, speech_items_for_track, track_start_seconds,
    )


def source(item: dict, public: Path) -> str:
    path = Path(item['file']).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    # Prefix rig subdirectory name so multiple char-<hash>/head.png or robot-<hash>/body.png never collide
    prefix = f'{path.parent.name}-' if path.parent.name.startswith(('char-', 'robot-')) else ''
    target = public / f'{prefix}{path.name}'
    if path != target.resolve():
        shutil.copy2(path, target)
    return f'media/{target.name}'


def _prepare_char_rig(rig_json_str: str, public: Path, cache: dict[str, dict], view_slot: str | None = None) -> dict | None:
    rig_path = Path(rig_json_str)
    if not rig_path.is_file():
        return None
    cache_key = f'{rig_path.resolve()}::{view_slot or ""}'
    if cache_key in cache:
        return cache[cache_key]
    raw = json.loads(rig_path.read_text(encoding='utf-8'))
    parts_out = {}
    for part_name, spec in (raw.get('parts') or {}).items():
        if not isinstance(spec, dict):
            continue
        pfile = spec.get('file') or spec.get('src')
        if not pfile or not Path(str(pfile)).is_file():
            continue
        parts_out[str(part_name)] = {
            'src': source({'file': str(pfile)}, public),
            'pivot': list(spec.get('pivot') or [0.5, 0.5]),
        }
    if not parts_out:
        return None
    prepared_rig = {
        'level': str(raw.get('level') or 'sprite'),
        'width': int(raw.get('width') or 768),
        'height': int(raw.get('height') or 1024),
        'anchor_x': float(raw.get('anchor_x', 0.5)),
        'foot_y': float(raw.get('foot_y', 0.96)),
        'head_top_y': float(raw.get('head_top_y', 0.04)),
        'native_facing': str(raw.get('native_facing') or 'right'),
        'parts': parts_out,
        **({'face': raw['face']} if isinstance(raw.get('face'), dict) else {}),
        **({'ankles': raw['ankles']} if isinstance(raw.get('ankles'), dict) else {}),
        **({'wrists': raw['wrists']} if isinstance(raw.get('wrists'), dict) else {}),
        'view_angle': resolve_slot_view_angle(
            view_slot, raw['view_angle'] if isinstance(raw.get('view_angle'), str) else 'front',
        ),
    }
    cache[cache_key] = prepared_rig
    return prepared_rig


def _ensure_side_rig_json(
    item: dict,
    side_file_key: str,
    cache_root: Path,
    cache: dict[str, str],
) -> str | None:
    existing = item.get('side_rig_json')
    if existing and Path(str(existing)).is_file():
        return str(existing)
    side_file = item.get(side_file_key)
    if not side_file or not Path(str(side_file)).is_file():
        return None
    resolved = str(Path(str(side_file)).resolve())
    if resolved not in cache:
        cache[resolved] = str(rig_character_master(Path(str(side_file)), cache_root=cache_root, slot='side'))
    return cache[resolved]


def _prepare_robot_rig(rig_json_str: str, public: Path, cache: dict[str, dict]) -> dict | None:
    rig_path = Path(rig_json_str)
    if not rig_path.is_file():
        return None
    cache_key = str(rig_path.resolve())
    if cache_key in cache:
        return cache[cache_key]
    raw = json.loads(rig_path.read_text(encoding='utf-8'))
    body_file = raw.get('body_file') or (raw.get('body') if isinstance(raw.get('body'), str) else None)
    if isinstance(raw.get('body'), dict):
        body_file = raw['body'].get('file') or raw['body'].get('src')
    if not body_file or not Path(str(body_file)).is_file():
        return None
    wheels_out = []
    for w_item in raw.get('wheels') or []:
        if not isinstance(w_item, dict):
            continue
        wf = w_item.get('file') or w_item.get('src')
        if not wf or not Path(str(wf)).is_file():
            continue
        wheel_out = {
            'src': source({'file': str(wf)}, public),
            'cx': float(w_item.get('cx', 0.5)),
            'cy': float(w_item.get('cy', 0.85)),
            'r': float(w_item.get('r', 0.08)),
        }
        for metric in ('rx', 'ry', 'tilt_deg'):
            if isinstance(w_item.get(metric), (int, float)):
                wheel_out[metric] = float(w_item[metric])
        tire_file = w_item.get('tire_file')
        if tire_file and Path(str(tire_file)).is_file():
            wheel_out['tire_src'] = source({'file': str(tire_file)}, public)
        wheels_out.append(wheel_out)
    prepared_robot: dict = {
        'level': str(raw.get('level') or ('wheeled' if len(wheels_out) >= 2 else 'sprite')),
        'width': int(raw.get('width') or 1024),
        'height': int(raw.get('height') or 1024),
        'anchor_x': float(raw.get('anchor_x', 0.5)),
        'foot_y': float(raw.get('foot_y', 0.92)),
        'native_facing': str(raw.get('native_facing') or 'right'),
        'body': source({'file': str(body_file)}, public),
        'body_pivot': list(raw.get('body_pivot') or [0.5, 0.72]),
        'wheels': wheels_out,
    }
    if isinstance(raw.get('content_top'), (int, float)):
        prepared_robot['content_top'] = float(raw['content_top'])
    if isinstance(raw.get('signal_point'), list) and len(raw['signal_point']) >= 2:
        prepared_robot['signal_point'] = [float(raw['signal_point'][0]), float(raw['signal_point'][1])]
    for opt_name in ('mast', 'arm'):
        opt = raw.get(opt_name)
        if isinstance(opt, dict):
            of = opt.get('file') or opt.get('src')
            if of and Path(str(of)).is_file():
                prepared_robot[opt_name] = {
                    'src': source({'file': str(of)}, public),
                    'pivot': list(opt.get('pivot') or [0.5, 0.5]),
                }
    cache[cache_key] = prepared_robot
    return prepared_robot


def prepare(manifest: Path, output: Path) -> None:
    doc = json.loads(manifest.read_text(encoding='utf-8'))
    if doc.get('version') != 1 or not doc.get('shots'):
        raise ValueError('Expected a v1 manifest with shots')
    width, height, fps = (int(doc.get(k, default)) for k, default in [('width', 1280), ('height', 720), ('fps', 24)])
    if not (320 <= width <= 3840 and 320 <= height <= 2160 and 1 <= fps <= 60):
        raise ValueError('Invalid resolution or frame rate')
    public = Path('public/media'); public.mkdir(parents=True, exist_ok=True)
    for old in public.iterdir():
        if old.is_file(): old.unlink()

    char_rig_cache: dict[str, dict] = {}
    side_rig_json_cache: dict[str, str] = {}
    robot_rig_cache: dict[str, dict] = {}
    plate_metrics_cache: dict[str, dict] = {}
    rig_cache_root = Path(os.environ.get('SOUNDBOOK_RIG_CACHE', 'media/rigs'))
    char_by_id: dict[str, dict] = {}
    char_by_name: dict[str, dict] = {}
    side_by_id: dict[str, dict] = {}
    side_by_name: dict[str, dict] = {}
    side_by_key: dict[str, dict] = {}
    for entry in doc.get('character_rigs') or []:
        if not isinstance(entry, dict):
            continue
        crig = (
            _prepare_char_rig(str(entry['rig_json']), public, char_rig_cache, 'master')
            if entry.get('rig_json') else None
        )
        if crig:
            if entry.get('id'):
                char_by_id[str(entry['id'])] = crig
            if entry.get('name'):
                char_by_name[str(entry['name'])] = crig
        side_json = _ensure_side_rig_json(
            entry, 'side_master_file', rig_cache_root, side_rig_json_cache,
        )
        side_rig = (
            _prepare_char_rig(side_json, public, char_rig_cache, 'side')
            if side_json else None
        )
        if side_rig:
            if entry.get('id'):
                side_by_id[str(entry['id'])] = side_rig
            if entry.get('name'):
                side_by_name[str(entry['name'])] = side_rig
            for key_name in ('side_master_key', 'side_rig_key'):
                if entry.get(key_name):
                    side_by_key[str(entry[key_name])] = side_rig

    global_robot_json = doc.get('robot_rig_json') or (
        doc['robot_rig'].get('rig_json') if isinstance(doc.get('robot_rig'), dict) else None
    )
    global_robot_rig = _prepare_robot_rig(str(global_robot_json), public, robot_rig_cache) if global_robot_json else None

    intro_voice = Path(doc['intro_voice_file']) if doc.get('intro_voice_file') else None
    shots = [dict(shot) for shot in doc['shots']]
    tracks = [dict(track) for track in doc.get('audio_tracks', [])]
    if doc.get('audio'):
        tracks.append({**doc['audio'], 'start_ms': 0, 'volume': 1, '_full_length': True})
    intro_seconds, captions = build_voice_locked_timeline(doc, shots, tracks, fps)
    intro_frames = frame(intro_seconds, fps)
    timeline = []; cursor_frame = intro_frames
    for i, shot in enumerate(shots):
        duration_frames = shot_duration_frames(shot.get('duration', 4), fps)
        src = source(shot, public) if shot.get('file') and Path(str(shot['file'])).is_file() else ''
        prepared = {**shot, 'src': src, 'from': cursor_frame,
                    'duration': duration_frames, 'index': i}

        raw_plate = shot.get('plate') if isinstance(shot.get('plate'), dict) else (
            shot['story_motion'].get('plate')
            if isinstance(shot.get('story_motion'), dict) and isinstance(shot['story_motion'].get('plate'), dict)
            else None
        )
        if raw_plate:
            plate_file = raw_plate.get('plate_file') or raw_plate.get('file')
            if plate_file:
                plate_key = str(Path(str(plate_file)).resolve())
                if plate_key not in plate_metrics_cache:
                    plate_metrics_cache[plate_key] = calibrate_plate_file(Path(str(plate_file)))
                # Story shots read screens / cabinet_top_y / cabinets off this dict.
                # Shots with no plate never get plate_metrics. Stage and explainer shots ignore it.
                metrics = plate_metrics_cache[plate_key]
                if not isinstance(metrics.get('screens'), list):
                    metrics['screens'] = []
                if not isinstance(metrics.get('cabinets'), list):
                    metrics['cabinets'] = []
                if 'cabinet_top_y' not in metrics:
                    metrics['cabinet_top_y'] = None
                prepared['plate_metrics'] = metrics
            if plate_file and Path(str(plate_file)).is_file():
                prepared_plate: dict[str, str] = {'src': source({'file': str(plate_file)}, public)}
                if raw_plate.get('far_file') and Path(str(raw_plate['far_file'])).is_file():
                    prepared_plate['far'] = source({'file': str(raw_plate['far_file'])}, public)
                if raw_plate.get('near_file') and Path(str(raw_plate['near_file'])).is_file():
                    prepared_plate['near'] = source({'file': str(raw_plate['near_file'])}, public)
                prepared['plate'] = prepared_plate
                if not prepared['src']:
                    prepared['src'] = prepared_plate['src']

        shot_robot_json = (
            shot.get('robot_rig_json')
            or (shot['rover'].get('rig_json') if isinstance(shot.get('rover'), dict) else None)
            or (shot['story_motion'].get('robot_rig_json') if isinstance(shot.get('story_motion'), dict) else None)
        )
        shot_robot_rig = (
            _prepare_robot_rig(str(shot_robot_json), public, robot_rig_cache)
            if shot_robot_json else global_robot_rig
        )
        if shot_robot_rig and global_robot_rig is None:
            global_robot_rig = shot_robot_rig

        if isinstance(shot.get('rover'), dict):
            rv_clean = {k: v for k, v in shot['rover'].items()
                        if not k.endswith('_file') and not k.endswith('_key') and k != 'rig_json'}
            if shot_robot_rig:
                rv_clean['rig'] = shot_robot_rig
            prepared['rover'] = rv_clean

        if isinstance(shot.get('story_motion'), dict):
            sm_clean = {k: v for k, v in shot['story_motion'].items()
                        if not k.endswith('_file') and not k.endswith('_key') and k not in ('robot_rig_json', 'plate')}
            raw_set_prop = shot['story_motion'].get('set_prop')
            if isinstance(raw_set_prop, dict):
                set_prop = {k: v for k, v in raw_set_prop.items()
                            if not k.endswith('_file') and not k.endswith('_key')}
                prop_file = raw_set_prop.get('prop_file')
                if prop_file and Path(str(prop_file)).is_file():
                    set_prop['prop_src'] = source({'file': str(prop_file)}, public)
                sm_clean['set_prop'] = set_prop
            prepared_actors = []
            for actor in shot['story_motion'].get('actors') or []:
                if not isinstance(actor, dict):
                    continue
                ac = {k: v for k, v in actor.items()
                      if not k.endswith('_file') and not k.endswith('_key')
                      and k not in ('rig_json', 'side_rig_json')}
                actor_rig = None
                if actor.get('rig_json'):
                    actor_rig = _prepare_char_rig(str(actor['rig_json']), public, char_rig_cache, 'master')
                if not actor_rig and actor.get('character_id'):
                    actor_rig = char_by_id.get(str(actor['character_id']))
                if not actor_rig and actor.get('name'):
                    aname = str(actor['name'])
                    actor_rig = char_by_name.get(aname)
                    if not actor_rig:
                        for cname, crig in char_by_name.items():
                            if cname and (cname in aname or aname in cname):
                                actor_rig = crig
                                break
                if actor_rig:
                    ac['rig'] = actor_rig
                side_rig = None
                side_json = _ensure_side_rig_json(
                    actor, 'side_rig_file', rig_cache_root, side_rig_json_cache,
                )
                if side_json:
                    side_rig = _prepare_char_rig(side_json, public, char_rig_cache, 'side')
                if not side_rig and actor.get('side_rig_key'):
                    side_rig = side_by_key.get(str(actor['side_rig_key']))
                if not side_rig and actor.get('character_id'):
                    side_rig = side_by_id.get(str(actor['character_id']))
                if not side_rig and actor.get('name'):
                    aname = str(actor['name'])
                    side_rig = side_by_name.get(aname)
                    if not side_rig:
                        for cname, candidate in side_by_name.items():
                            if cname and (cname in aname or aname in cname):
                                side_rig = candidate
                                break
                if side_rig:
                    ac['side_rig'] = side_rig
                prepared_actors.append(ac)
            sm_clean['actors'] = prepared_actors
            if shot_robot_rig:
                sm_clean['robot_rig'] = shot_robot_rig
            prepared['story_motion'] = sm_clean

        prepared.pop('robot_rig_json', None)
        prepared.pop('robot_rig_file', None)
        prepared.pop('robot_arm_file', None)

        stage = shot.get('stage') if isinstance(shot.get('stage'), dict) else None
        if stage and stage.get('background_file') and Path(stage['background_file']).is_file():
            actors = []
            for actor in stage.get('cast') or []:
                cells = {}
                for key, file in (actor.get('cells') or {}).items():
                    if file and Path(str(file)).is_file():
                        cells[str(key)] = source({'file': str(file)}, public)
                if not cells:
                    continue
                actors.append({
                    'cells': cells,
                    'expression': str(actor.get('expression') or 'neutral'),
                    'talking': bool(actor.get('talking')),
                    'x': float(actor.get('x', 0.5)),
                    'y': float(actor.get('y', 0.94)),
                    'scale': float(actor.get('scale', 1)),
                })
            prepared_stage = {
                'background': source({'file': stage['background_file']}, public),
                'camera': str(stage.get('camera') or ''),
                'enter': str(stage.get('enter') or 'cut'),
                'speaker': str(stage.get('speaker') or ''),
                'line': str(stage.get('line') or ''),
                'actors': actors,
            }
            near = stage.get('foreground_file')
            if near and Path(str(near)).is_file():
                prepared_stage['foreground'] = source({'file': str(near)}, public)
            prepared['stage'] = prepared_stage
            prepared.pop('file', None)
        if isinstance(shot.get('character_layers'), dict):
            layers = dict(shot['character_layers'])
            if all(layers.get(k) and Path(layers[k]).is_file() for k in ('background_file', 'body_file')):
                for file_key, src_key in [('background_file', 'background'), ('body_file', 'body'), ('body2_file', 'body2'),
                                          ('head_file', 'head'), ('arm_left_file', 'arm_left'), ('arm_right_file', 'arm_right')]:
                    if layers.get(file_key) and Path(layers[file_key]).is_file():
                        layers[src_key] = source({'file': layers[file_key]}, public)
                if isinstance(layers.get('mouth_files'), dict):
                    layers['mouth'] = {shape: source({'file': path}, public)
                                       for shape, path in layers['mouth_files'].items()
                                       if isinstance(path, str) and Path(path).is_file() and shape in 'ABCDEFGHX'}
                prepared['layers'] = {k: v for k, v in layers.items()
                                      if not k.endswith('_file') and k not in ('mouth_files', 'mouth_keys')
                                      and not k.endswith('_key')}
        timeline.append(prepared)
        cursor_frame += duration_frames
    if doc.get('motion_policy') == 'majority':
        moving = [shot for shot in timeline if shot.get('cartoon') or shot.get('story_motion') or shot.get('rover')
                  or shot.get('stage') or shot.get('layers')]
        if len(moving) * 2 < len(timeline):
            raise ValueError(f'Motion quality gate: only {len(moving)}/{len(timeline)} shots have an articulated subject')
    audio = []
    if intro_frames:
        chime = Path('media/intro-chime.wav'); chime.parent.mkdir(exist_ok=True)
        write_intro_chime(chime)
        audio.append({'src': source({'file': str(chime)}, public), 'from': 0, 'volume': .5, 'loop': False})
        if intro_voice:
            audio.append({'src': source({'file': str(intro_voice)}, public), 'from': frame(.22, fps), 'volume': 1, 'loop': False})
    animated_scenes = {int(shot.get('scene_number', 0)) for shot in timeline if shot_is_animated(shot)}
    speech = []
    voice_locked = doc.get('timeline_mode') == 'voice_locked'
    for track in tracks:
        if not track.get('file'):
            continue
        start = track_start_seconds(track, voice_locked=voice_locked, intro_seconds=intro_seconds)
        audio.append({'src': source(track, public), 'from': frame(start, fps),
                      'volume': max(0, min(4, float(track.get('volume', 1)))), 'loop': bool(track.get('loop'))})
        speech.extend(speech_items_for_track(
            track, start=start, fps=fps, language=str(doc.get('language') or 'zh'),
            animated_scenes=animated_scenes,
        ))
    cta = doc.get('cta_card') if isinstance(doc.get('cta_card'), dict) else None
    cta_frames = frame(3.8, fps) if cta and str(cta.get('name', '')).strip() else 0
    if cta_frames and str(cta.get('url', '')).startswith('https://'):
        import qrcode
        qr = Path('media/cta-qr.png'); qr.parent.mkdir(exist_ok=True)
        qrcode.make(str(cta['url']), border=2).save(qr)
        cta = {**cta, 'qr': source({'file': str(qr)}, public)}
    props = {'width': width, 'height': height, 'fps': fps, 'durationInFrames': cursor_frame+cta_frames,
             'introFrames': intro_frames, 'shots': timeline, 'audio': audio, 'captions': captions, 'speech': speech,
             'episodeNumber': int(doc.get('episode_number') or 1), 'title': str(doc.get('episode_title') or ''),
             'language': str(doc.get('language') or 'zh'), 'cta': cta if cta_frames else None,
             'ctaFrom': cursor_frame, 'ctaFrames': cta_frames,
             **({'robotRig': global_robot_rig} if global_robot_rig else {})}
    output.write_text(json.dumps(props, ensure_ascii=False), encoding='utf-8')
    print(f"Prepared {len(timeline)} shots, {len(audio)} audio tracks, {len(captions)} captions, {props['durationInFrames']} frames")


if __name__ == '__main__':
    prepare(Path(sys.argv[1]), Path(sys.argv[2]))
