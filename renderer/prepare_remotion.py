#!/usr/bin/env python3
"""Turn the existing manifest into a frame-accurate Remotion composition."""
from __future__ import annotations
import json, math, re, shutil, subprocess, sys
from pathlib import Path
from render import media_duration, subtitle_chunks, write_intro_chime
from voice_cues import speech_cues


def frame(seconds: float, fps: int) -> int:
    return max(0, round(seconds * fps))


def source(item: dict, public: Path) -> str:
    path = Path(item['file']).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    target = public / path.name
    if path != target.resolve():
        shutil.copy2(path, target)
    return f'media/{target.name}'


def parse_srt(path: Path, offset: float, fps: int) -> list[dict]:
    def secs(value: str) -> float:
        h, m, s, ms = map(int, re.split('[:,]', value))
        return 3600*h + 60*m + s + ms/1000
    cues = []
    for block in re.split(r'\n\s*\n', path.read_text(encoding='utf-8-sig')):
        match = re.search(r'(\d\d:\d\d:\d\d,\d{3})\s*-->\s*(\d\d:\d\d:\d\d,\d{3})', block)
        if match:
            content = block[match.end():].strip()
            if content:
                cues.append({'from': frame(offset+secs(match[1]), fps),
                             'to': frame(offset+secs(match[2]), fps), 'text': content})
    return cues


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
    intro_voice = Path(doc['intro_voice_file']) if doc.get('intro_voice_file') else None
    intro_seconds = max(2.8, media_duration(intro_voice)+.65) if intro_voice and doc.get('episode_title') else (2.8 if doc.get('episode_title') else 0)
    intro_frames = frame(intro_seconds, fps)
    shots = [dict(shot) for shot in doc['shots']]
    tracks = [dict(track) for track in doc.get('audio_tracks', [])]
    if doc.get('audio'):
        tracks.append({**doc['audio'], 'start_ms': 0, 'volume': 1, '_full_length': True})
    captions = []
    if doc.get('timeline_mode') == 'voice_locked':
        scene_order = list(dict.fromkeys(int(shot.get('scene_number', 0)) for shot in shots))
        voices = sorted((track for track in tracks if track.get('scene_number') is not None and not track.get('loop')),
                        key=lambda track: (int(track.get('scene_number', 0)), int(track.get('dialogue_number', 0))))
        for track in voices:
            track['_duration'] = media_duration(Path(track['file']))
        cursor = intro_seconds
        for scene in scene_order:
            scene_voices = [t for t in voices if int(t['scene_number']) == scene]
            scene_shots = [s for s in shots if int(s.get('scene_number', 0)) == scene]
            if not scene_voices:
                continue
            local = 0.0
            for track in scene_voices:
                duration = track['_duration']
                track['start_ms'] = round((cursor+local)*1000)
                chunks = subtitle_chunks(str(track.get('caption_text', '')), str(track.get('speaker', '')))
                total = sum(max(1, len(chunk.replace(' ', ''))) for chunk in chunks)
                offset = 0.0
                for chunk in chunks:
                    length = duration * max(1, len(chunk.replace(' ', ''))) / total
                    captions.append({'from': frame(cursor+local+offset, fps), 'to': frame(cursor+local+offset+length, fps), 'text': chunk})
                    offset += length
                local += duration + .28
            scene_duration = max(.25, local-.28+.65)
            if len(scene_voices) == len(scene_shots) and len(scene_shots) > 1:
                for i, (shot, track) in enumerate(zip(scene_shots, scene_voices)):
                    shot['duration'] = track['_duration'] + (.65 if i == len(scene_shots)-1 else .28)
            else:
                planned = sum(max(.25, float(s.get('duration', 4))) for s in scene_shots) or 1
                for shot in scene_shots:
                    shot['duration'] = max(.25, scene_duration * max(.25, float(shot.get('duration', 4))) / planned)
            cursor += scene_duration
    elif doc.get('subtitle'):
        captions = parse_srt(Path(doc['subtitle']['file']), intro_seconds, fps)
    timeline = []; cursor_frame = intro_frames
    for i, shot in enumerate(shots):
        duration_frames = max(1, frame(min(300, max(.25, float(shot.get('duration', 4)))), fps))
        src = source(shot, public) if shot.get('file') else ''
        prepared = {**shot, 'src': src, 'from': cursor_frame,
                    'duration': duration_frames, 'index': i}
        cartoon = shot.get('cartoon') if isinstance(shot.get('cartoon'), dict) else None
        if cartoon:
            cast = []
            for actor in cartoon.get('actors') or []:
                actor = dict(actor)
                for part in ('portrait', 'mouth'):
                    file = actor.get(f'{part}_file')
                    if file and Path(file).is_file():
                        actor[part] = source({'file': file}, public)
                cast.append({k: v for k, v in actor.items() if not k.endswith('_file') and not k.endswith('_key')})
            prepared['cartoon'] = {**cartoon, 'actors': cast}
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
                for file_key, src_key in [('background_file', 'background'), ('body_file', 'body'), ('head_file', 'head'),
                                          ('arm_left_file', 'arm_left'), ('arm_right_file', 'arm_right')]:
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
        moving = [shot for shot in timeline if shot.get('cartoon') or shot.get('rover') or shot.get('stage') or shot.get('layers')]
        if len(moving) * 2 < len(timeline):
            raise ValueError(f'Motion quality gate: only {len(moving)}/{len(timeline)} shots have an articulated subject')
    minimum_seconds = float(doc.get('min_duration_seconds') or 0)
    if minimum_seconds and cursor_frame / fps < minimum_seconds:
        raise ValueError(f'Duration quality gate: measured {cursor_frame / fps:.1f}s, minimum {minimum_seconds:.1f}s; rewrite the script instead of stretching shots')
    audio = []
    if intro_frames:
        chime = Path('media/intro-chime.wav'); chime.parent.mkdir(exist_ok=True)
        write_intro_chime(chime)
        audio.append({'src': source({'file': str(chime)}, public), 'from': 0, 'volume': .5, 'loop': False})
        if intro_voice:
            audio.append({'src': source({'file': str(intro_voice)}, public), 'from': frame(.22, fps), 'volume': 1, 'loop': False})
    animated_scenes = {int(shot.get('scene_number', 0)) for shot in timeline
                       if shot.get('cartoon') or shot.get('rover') or shot.get('stage') or (isinstance(shot.get('layers'), dict) and
                       (shot['layers'].get('mouth') or shot['layers'].get('mouth_mode') == 'led'))}
    speech = []
    for track in tracks:
        if not track.get('file'):
            continue
        start = (intro_seconds if track.get('loop') else 0) if doc.get('timeline_mode') == 'voice_locked' else (0 if track.get('_full_length') else intro_seconds)
        if not track.get('loop') and track.get('start_ms') is not None:
            start = float(track['start_ms']) / 1000 + (0 if doc.get('timeline_mode') == 'voice_locked' or track.get('_full_length') else intro_seconds)
        audio.append({'src': source(track, public), 'from': frame(start, fps),
                      'volume': max(0, min(4, float(track.get('volume', 1)))), 'loop': bool(track.get('loop'))})
        if not track.get('loop') and track.get('scene_number') is not None and track.get('speaker') and int(track['scene_number']) in animated_scenes:
            for cue in speech_cues(Path(track['file']), str(track.get('caption_text', '')), str(doc.get('language') or 'zh')):
                speech.append({'from': frame(start + cue['start'], fps),
                               'to': frame(start + cue['end'], fps),
                               'value': cue['value'], 'speaker': str(track['speaker']),
                               'scene_number': int(track['scene_number'])})
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
             'ctaFrom': cursor_frame, 'ctaFrames': cta_frames}
    output.write_text(json.dumps(props, ensure_ascii=False), encoding='utf-8')
    print(f"Prepared {len(timeline)} shots, {len(audio)} audio tracks, {len(captions)} captions, {props['durationInFrames']} frames")


if __name__ == '__main__':
    try:
        prepare(Path(sys.argv[1]), Path(sys.argv[2]))
    except Exception as error:
        Path('qa-error.txt').write_text(str(error)[:900], encoding='utf-8')
        raise
