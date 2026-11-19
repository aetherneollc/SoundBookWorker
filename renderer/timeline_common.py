#!/usr/bin/env python3
"""Frame clock, subtitles, and voice-locked timing shared by both renderers.

``prepare_remotion`` and ``prepare_stage`` have to agree on when a line starts,
how long a shot stays up, and which mouth shape is open. The arithmetic lives
here so the Remotion props and the Godot ``stage.json`` describe the same cut.
"""
from __future__ import annotations

import re
from pathlib import Path

try:
    from render import media_duration, subtitle_chunks
    from voice_cues import speech_cues
except ImportError:  # ``python -m unittest`` loads this module as part of the package.
    from renderer.render import media_duration, subtitle_chunks
    from renderer.voice_cues import speech_cues


def frame(seconds: float, fps: int) -> int:
    return max(0, round(seconds * fps))


def shot_duration_frames(seconds: float, fps: int) -> int:
    """The per-shot frame count ``prepare_remotion`` has always written into props."""
    return max(1, frame(min(300, max(.25, float(seconds))), fps))


def parse_srt(path: Path, offset: float, fps: int) -> list[dict]:
    def secs(value: str) -> float:
        h, m, s, ms = map(int, re.split('[:,]', value))
        return 3600 * h + 60 * m + s + ms / 1000
    cues = []
    for block in re.split(r'\n\s*\n', path.read_text(encoding='utf-8-sig')):
        match = re.search(r'(\d\d:\d\d:\d\d,\d{3})\s*-->\s*(\d\d:\d\d:\d\d,\d{3})', block)
        if match:
            content = block[match.end():].strip()
            if content:
                cues.append({'from': frame(offset + secs(match[1]), fps),
                             'to': frame(offset + secs(match[2]), fps), 'text': content})
    return cues


def intro_lead_seconds(doc: dict) -> float:
    """Silent (or voiced) hold before the first story shot. Same rule as Remotion."""
    intro_voice = Path(doc['intro_voice_file']) if doc.get('intro_voice_file') else None
    if intro_voice and doc.get('episode_title'):
        return max(2.8, media_duration(intro_voice) + .65)
    if doc.get('episode_title'):
        return 2.8
    return 0


def build_voice_locked_timeline(
    doc: dict,
    shots: list[dict],
    tracks: list[dict],
    fps: int,
) -> tuple[float, list[dict]]:
    """Lock shot durations and voice ``start_ms`` to the recorded lines.

    Mutates ``shots`` (their ``duration``, in seconds) and the non-loop voice
    ``tracks`` (``start_ms`` and a private ``_duration``). Returns
    ``(intro_seconds, captions)``. When the manifest is not voice-locked, shot
    durations stay as authored and captions come from the SRT, if one exists.
    """
    intro_seconds = intro_lead_seconds(doc)
    captions: list[dict] = []
    if doc.get('timeline_mode') == 'voice_locked':
        scene_order = list(dict.fromkeys(int(shot.get('scene_number', 0)) for shot in shots))
        voices = sorted(
            (track for track in tracks if track.get('scene_number') is not None and not track.get('loop')),
            key=lambda track: (int(track.get('scene_number', 0)), int(track.get('dialogue_number', 0))),
        )
        for track in voices:
            track['_duration'] = media_duration(Path(track['file']))
        cursor = intro_seconds
        for scene in scene_order:
            scene_voices = [track for track in voices if int(track['scene_number']) == scene]
            scene_shots = [shot for shot in shots if int(shot.get('scene_number', 0)) == scene]
            if not scene_voices:
                continue
            local = 0.0
            for track in scene_voices:
                duration = track['_duration']
                track['start_ms'] = round((cursor + local) * 1000)
                chunks = subtitle_chunks(str(track.get('caption_text', '')), str(track.get('speaker', '')))
                total = sum(max(1, len(chunk.replace(' ', ''))) for chunk in chunks)
                offset = 0.0
                for chunk in chunks:
                    length = duration * max(1, len(chunk.replace(' ', ''))) / total
                    captions.append({
                        'from': frame(cursor + local + offset, fps),
                        'to': frame(cursor + local + offset + length, fps),
                        'text': chunk,
                    })
                    offset += length
                local += duration + .28
            scene_duration = max(.25, local - .28 + .65)
            if len(scene_voices) == len(scene_shots) and len(scene_shots) > 1:
                for index, (shot, track) in enumerate(zip(scene_shots, scene_voices)):
                    shot['duration'] = track['_duration'] + (.65 if index == len(scene_shots) - 1 else .28)
            else:
                planned = sum(max(.25, float(shot.get('duration', 4))) for shot in scene_shots) or 1
                for shot in scene_shots:
                    shot['duration'] = max(.25, scene_duration * max(.25, float(shot.get('duration', 4))) / planned)
            cursor += scene_duration
    elif doc.get('subtitle'):
        captions = parse_srt(Path(doc['subtitle']['file']), intro_seconds, fps)
    return intro_seconds, captions


def track_start_seconds(track: dict, *, voice_locked: bool, intro_seconds: float) -> float:
    """Absolute start, in seconds, of one manifest audio track."""
    start = (intro_seconds if track.get('loop') else 0) if voice_locked else (
        0 if track.get('_full_length') else intro_seconds
    )
    if not track.get('loop') and track.get('start_ms') is not None:
        start = float(track['start_ms']) / 1000 + (
            0 if voice_locked or track.get('_full_length') else intro_seconds
        )
    return start


def shot_is_animated(shot: dict) -> bool:
    """True when the shot has a mouth that should follow the voice track."""
    if shot.get('cartoon') or shot.get('story_motion') or shot.get('rover') or shot.get('stage'):
        return True
    layers = shot.get('layers')
    return bool(
        isinstance(layers, dict)
        and (layers.get('mouth') or layers.get('mouth_mode') == 'led')
    )


def speech_items_for_track(
    track: dict,
    *,
    start: float,
    fps: int,
    language: str,
    animated_scenes: set[int],
) -> list[dict]:
    """Flat per-viseme items, the shape ``prepare_remotion`` puts on ``speech``.

    Rhubarb wins when ``RHUBARB_BIN`` (or a ``rhubarb`` on ``PATH``) can read the
    line. Otherwise the cues are the RMS energy fallback from ``voice_cues``.
    """
    if not track.get('file') or track.get('loop'):
        return []
    if track.get('scene_number') is None or not track.get('speaker'):
        return []
    if int(track['scene_number']) not in animated_scenes:
        return []
    items = []
    for cue in speech_cues(Path(track['file']), str(track.get('caption_text', '')), language):
        items.append({
            'from': frame(start + cue['start'], fps),
            'to': frame(start + cue['end'], fps),
            'value': cue['value'],
            'speaker': str(track['speaker']),
            'scene_number': int(track['scene_number']),
        })
    return items
