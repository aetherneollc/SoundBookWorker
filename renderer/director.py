#!/usr/bin/env python3
"""Direct a Worker manifest into the frame-accurate ``stage.json`` (v1) played by ``godot/player``.

The Worker's ``story_motion`` says who is on stage, where they stand and what they do.
This module adds everything that depends on the measured voice timeline: the cuts,
each speaker's gesture, the listeners' reactions and the mouth cues.

``docs/STAGE2D_CONTRACT.md`` describes the output. :func:`build_stage` is pure: it
reads no files and copies no media, so every path it is given is written through as is.
"""
from __future__ import annotations

import logging
import math
import re
from typing import Any

LOG = logging.getLogger('soundbook.director')

STAGE_VERSION = 1
STAGE_SIZE = (1280, 720)  # godot/player/main.gd refuses any other canvas.

# Clip name -> seconds for one cycle. Mirrors godot/motions/*.json; the unit test compares them.
CLIP_SECONDS: dict[str, float] = {
    'idle': 3.2, 'talk_calm': 2.4, 'talk_emphatic': 2.4, 'talk_explain': 2.8, 'listen': 3.0,
    'nod': 1.0, 'point': 2.4, 'wave': 1.6, 'react': 1.2, 'walk_medium': 1.2, 'step_shift': 2.0,
}
# M2 clips are validated by renderer/test_motions.py.  The director only needs
# durations for clips it schedules for a fixed number of cycles.
M2_CLIPS = ('sit', 'stand', 'operate', 'carry', 'cross_arms', 'angry', 'sad', 'surprised')
CLIPS = (*CLIP_SECONDS, *M2_CLIPS)
FRAMINGS = ('wide', 'two_shot', 'medium', 'close')
CAMERA_MOVES = ('push_in', 'slow_pan_left', 'slow_pan_right', 'static')
VISEMES = ('X', 'A', 'B', 'C', 'D', 'E', 'F')
EMOTIONS = ('neutral', 'happy', 'serious', 'angry', 'sad', 'surprised')
DEPTHS = ('near', 'mid', 'far')
FACINGS = ('left', 'right')
PROP_KINDS = ('console_desk', 'telemetry_screen', 'workbench')
ROVER_ACTIONS = (
    'idle', 'drive', 'patrol', 'scan', 'inspect', 'work', 'assemble',
    'pick', 'carry', 'wave', 'alert', 'tilt', 'recover',
)
ROVER_ACTION_ALIASES = {
    'sample': 'inspect', 'reach': 'inspect', 'signal': 'scan',
    'fall': 'tilt', 'stand': 'recover',
}

# CARTOON_ACTIONS (SoundBookCF/src/cartoon_stage.ts) -> clip. ``walk`` and ``carry`` are
# resolved by displacement in map_action(); the values here apply to the largest moves.
LEGACY_ACTION_CLIPS: dict[str, str] = {
    'idle': 'idle', 'listen': 'listen', 'talk': 'talk_calm', 'react': 'react',
    'point': 'point', 'wave': 'wave',
    'work': 'operate', 'console': 'operate', 'carry': 'carry',
    'walk': 'walk_medium',
}

# The rig is front-facing only, so a long walk is shot waist-up with a scrolling background.
WALK_MEDIUM_MIN_SPAN = 0.14
STEP_SHIFT_MIN_SPAN = 0.03
WALK_SCROLL_SPEED = 38.0  # px/s. Positive scrolls the set left, i.e. the actor walks toward +x.
LEAN = 0.25

MIN_CUT_FRAMES_AT_24 = 28
LONG_TAKE_SECONDS = 4.5
ESTABLISH_SECONDS = (1.8, 2.0, 2.2)
REACTION_SECONDS = 1.5

_ROTATION = ('push_in', 'slow_pan_right', 'static', 'slow_pan_left')
# A one-cycle gesture opens the line, then the speaker settles into this clip.
_GESTURE_FOLLOW = {'point': 'talk_explain', 'wave': 'talk_calm', 'react': 'talk_emphatic'}
_STRONG_EMOTIONS = ('angry', 'surprised')
_RHUBARB_VISEMES = {'X': 'X', 'A': 'A', 'B': 'B', 'C': 'C', 'D': 'D', 'E': 'E', 'F': 'F',
                    'G': 'B', 'H': 'C'}  # Rhubarb's own fallbacks for its two extended shapes.

_NARRATOR_RE = re.compile(r'^(旁白|画外音|科普主持人|主持人|narrator|narration|host|voice ?over)$', re.I)
_SPEAKER_PREFIX_RE = re.compile(r'^[^：:，。,.!?！？\s]{1,16}[：:]\s*')
_EMOTION_HINTS = (
    ('angry', re.compile(r'angry|anger|furious|irritat|愤怒|生气|恼|暴躁|气愤|怒')),
    ('surprised', re.compile(r'surpris|shock|astonish|amaz|惊|诧异|错愕')),
    ('sad', re.compile(r'sad|sorrow|grie|upset|depress|悲|难过|伤心|沮丧|失落|愧疚|哽咽')),
    ('happy', re.compile(r'happy|joy|glad|excit|cheer|delight|高兴|开心|兴奋|愉快|欣慰|喜悦|激动|轻松|笑')),
    ('serious', re.compile(r'serious|stern|firm|grave|tense|worr|anxious|urgent|doubt|skeptic'
                           r'|严肃|认真|坚定|紧张|担忧|担心|焦急|质疑|凝重|警惕|急切')),
)
_TEXT_EMOTIONS = (
    ('surprised', re.compile(r'什么[？?！!]|怎么会|怎么可能|居然|竟然|天[哪啊]|不会吧|不可能', re.I)),
    ('angry', re.compile(r'可恶|住口|闭嘴|胡说|胡闹|凭什么|岂有此理|混蛋|够了[！!]', re.I)),
    ('sad', re.compile(r'对不起|抱歉|可惜|遗憾|难过|没能', re.I)),
    ('happy', re.compile(r'太好了|成功了|哈哈|真棒|太棒|恭喜|谢谢|做到了', re.I)),
    ('serious', re.compile(r'必须|立刻|立即|马上|危险|警告|注意|小心|不行|不能再|来不及', re.I)),
)
_WAVE_RE = re.compile(r'你好|您好|大家好|早上好|晚上好|早安|再见|拜拜|回头见|欢迎|嗨[，,！!]'
                      r'|\b(hello|hi|hey|bye|goodbye|welcome)\b', re.I)
_POINT_RE = re.compile(r'你看|请看|快看|先看|看这|看那|这里|那里|这边|那边|屏幕上|前方|就是它'
                       r'|\b(look at|over there|right here|see this)\b', re.I)
_EXPLAIN_RE = re.compile(r'因为|所以|首先|其次|然后|也就是说|意思是|比如|例如|如果|那么|说明|原理|数据|参数'
                         r'|步骤|第[一二三四五六七八九十]|\b(because|first|then|which means|for example|if)\b', re.I)


def _int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool) or value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return int(round(number)) if math.isfinite(number) else default


def _float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ''


def _names_match(left: str, right: str) -> bool:
    left, right = left.strip(), right.strip()
    if not left or not right:
        return False
    if left == right:
        return True
    return min(len(left), len(right)) >= 2 and (left in right or right in left)


def map_action(action: Any, displacement: float = 0.0) -> str:
    """Map a legacy ``CARTOON_ACTIONS`` verb (or an already valid clip name) to one of CLIPS.

    ``displacement`` is ``to_x - from_x``. It decides how ``walk``/``carry`` are staged:
    a waist-up ``walk_medium``, an in-place ``step_shift``, or no movement at all.
    """
    name = str(action or '').strip().lower()
    if name in ('walk', 'walk_medium', 'step_shift'):
        span = abs(round(_float(displacement), 4))
        if span > WALK_MEDIUM_MIN_SPAN:
            return 'walk_medium'
        return 'step_shift' if span > STEP_SHIFT_MIN_SPAN else 'idle'
    if name in CLIPS:
        return name
    return LEGACY_ACTION_CLIPS.get(name, 'idle')


def map_viseme(value: Any) -> str:
    """Map a Rhubarb shape (A-H, X) or a 0..1 RMS level to one of VISEMES."""
    if isinstance(value, bool):
        return 'D' if value else 'X'
    if isinstance(value, (int, float)):
        level = _float(value)
        return 'X' if level < 0.13 else 'B' if level < 0.35 else 'C' if level < 0.68 else 'D'
    return _RHUBARB_VISEMES.get(str(value or '').strip().upper(), 'X')


def normalize_cues(cues: Any, fps: int, line_from: int = 0, line_frames: int | None = None) -> list[list]:
    """Return ``[[rel_frame, viseme], ...]``: ascending, starting at frame 0, no repeated shape.

    Accepted cue shapes: ``[rel_frame, value]``; ``{'start': seconds, 'value'}`` relative to
    the line (voice_cues.speech_cues); ``{'from': absolute_frame, 'value'}`` (prepare_remotion).
    """
    timed: list[tuple[int, str]] = []
    for cue in cues or []:
        if isinstance(cue, (list, tuple)) and len(cue) >= 2:
            at, value = _float(cue[0], math.nan), cue[1]
        elif isinstance(cue, dict):
            value = next((cue[key] for key in ('value', 'viseme', 'level', 'rms') if key in cue), None)
            if 'start' in cue:
                at = _float(cue['start'], math.nan) * fps
            elif 'frame' in cue:
                at = _float(cue['frame'], math.nan)
            else:
                at = _float(cue.get('from'), math.nan) - line_from
        else:
            continue
        if math.isnan(at) or (line_frames is not None and round(at) >= line_frames):
            continue
        timed.append((max(0, round(at)), map_viseme(value)))
    timed.sort(key=lambda item: item[0])  # Stable: on one frame the later cue wins below.
    result: list[list] = []
    for frame, viseme in timed:
        if result and result[-1][0] == frame:
            result[-1][1] = viseme
        else:
            result.append([frame, viseme])
    if not result or result[0][0] > 0:
        result.insert(0, [0, 'X'])
    return [cue for index, cue in enumerate(result) if index == 0 or cue[1] != result[index - 1][1]]


def normalize_emotion(hint: Any, text: str = '') -> str:
    """Reduce a free-text emotion (the script writes e.g. ``质疑``) or the line itself to EMOTIONS."""
    label = str(hint or '').strip().lower()
    if label in EMOTIONS and label != 'neutral':
        return label
    for emotion, pattern in _EMOTION_HINTS:
        if label and pattern.search(label):
            return emotion
    for emotion, pattern in _TEXT_EMOTIONS:
        if text and pattern.search(text):
            return emotion
    return 'neutral'


class _Director:
    """Frame constants for one episode, plus the rotations that keep moves and gestures varied."""

    def __init__(self, fps: int):
        self.fps = fps
        self.min_cut = max(1, round(MIN_CUT_FRAMES_AT_24 * fps / 24))
        self.long_take = self.frames(LONG_TAKE_SECONDS)
        self.establish = tuple(self.frames(seconds) for seconds in ESTABLISH_SECONDS)
        self.reaction = max(self.min_cut, self.frames(REACTION_SECONDS))
        self.last_move = ''
        self.rotor = -1
        self.variation = 0
        self.last_gesture: dict[str, str] = {}
        self.line_gestures: dict[tuple, str] = {}

    def frames(self, seconds: float) -> int:
        return max(1, round(seconds * self.fps))

    def clip_frames(self, clip: str) -> int:
        return self.frames(CLIP_SECONDS[clip])

    def next_move(self, allowed: tuple[str, ...], preferred: str | None = None) -> str:
        """Pick the next camera move; two neighbouring cuts never share one."""
        move = preferred if preferred in allowed and preferred != self.last_move else ''
        for step in range(1, len(_ROTATION) + 1):
            if move:
                break
            candidate = _ROTATION[(self.rotor + step) % len(_ROTATION)]
            if candidate in allowed and candidate != self.last_move:
                move = candidate
        move = move or allowed[0]
        self.rotor = _ROTATION.index(move)
        self.last_move = move
        return move


class _Cast:
    """Stable actor ids for the episode. Only characters with a rig can stand on stage."""

    def __init__(self, rigs_map: Any):
        self.rigs: dict[str, dict] = {}
        for key, entry in (rigs_map or {}).items() if isinstance(rigs_map, dict) else ():
            if isinstance(entry, str) and entry.strip():
                self.rigs[str(key)] = {'rig_json': entry.strip()}
            elif isinstance(entry, dict) and _text(entry.get('rig_json')):
                self.rigs[str(key)] = entry
        self.names: dict[str, str] = {}        # actor id -> display name
        self.used: dict[str, dict] = {}        # actor id -> stage.json rigs entry
        self._by_rig: dict[str, str] = {}      # rigs_map key -> actor id
        self._character_ids: dict[str, str] = {}
        self._missing: set[str] = set()

    def _find_rig(self, character_id: str, name: str) -> str | None:
        for key in (character_id, name):
            if key and key in self.rigs:
                return key
        for key, entry in self.rigs.items():
            if character_id and character_id in (_text(entry.get('id')), _text(entry.get('character_id'))):
                return key
        for key, entry in self.rigs.items():
            if name and (_names_match(name, _text(entry.get('name'))) or _names_match(name, key)):
                return key
        return None

    def enlist(self, name: str, character_id: str, stature: Any) -> str | None:
        """Return the actor id for a ``story_motion`` actor, or None when it has no rig."""
        rig_key = self._find_rig(character_id, name)
        if rig_key is None:
            label = name or character_id
            if label not in self._missing:
                self._missing.add(label)
                LOG.warning('director: no rig for %s; the character stays off stage', label)
            return None
        if rig_key in self._by_rig:
            return self._by_rig[rig_key]
        entry = self.rigs[rig_key]
        actor_id = ''
        for candidate in (entry.get('id'), rig_key, character_id, name):
            actor_id = re.sub(r'[^A-Za-z0-9_-]+', '_', str(candidate or '')).strip('_')
            if actor_id:
                break
        actor_id = actor_id or f'actor_{len(self.names) + 1}'
        while actor_id in self.names:
            actor_id += '_x'
        self._by_rig[rig_key] = actor_id
        self.names[actor_id] = name or _text(entry.get('name')) or actor_id
        if character_id:
            self._character_ids[character_id] = actor_id
        scale = _float(entry.get('stature_scale'), _float(stature, 1.0))
        self.used[actor_id] = {'rig_json': _text(entry.get('rig_json')).replace('\\', '/'),
                               'stature_scale': round(_clamp(scale, 0.5, 1.5), 3)}
        return actor_id

    def speaker_id(self, line: dict) -> str | None:
        """Resolve a voice line to an actor that appears somewhere in the episode."""
        explicit = line['actor_hint']
        if explicit in self.names:
            return explicit
        if explicit in self._character_ids:
            return self._character_ids[explicit]
        speaker = line['speaker']
        if not speaker or _NARRATOR_RE.match(speaker):
            return None
        for actor_id, name in self.names.items():
            if name == speaker:
                return actor_id
        for actor_id, name in self.names.items():
            if _names_match(name, speaker):
                return actor_id
        return None


# ---------------------------------------------------------------------------
# Voice lines
# ---------------------------------------------------------------------------

def _line_span(track: dict, fps: int) -> tuple[int, int]:
    """A line's absolute frame span: ``from``/``to``, else ``start_ms`` plus ``duration_ms``."""
    start = _int(track.get('from'), -1)
    if start < 0 and track.get('start_ms') is not None:
        start = _int(_float(track['start_ms']) * fps / 1000, -1)
    end = _int(track.get('to'), -1)
    if end < 0 and track.get('duration_ms') is not None:
        end = start + _int(_float(track['duration_ms']) * fps / 1000)
    return start, end


def _normalize_lines(speech_tracks: Any, captions: list[dict], fps: int) -> list[dict]:
    """Turn the supported ``speech_tracks`` shapes into one record per spoken line."""
    lines: list[dict] = []
    flat: list[dict] = []
    for track in speech_tracks or []:
        if not isinstance(track, dict):
            continue
        if 'cues' not in track and 'value' in track:
            flat.append(track)  # One mouth cue per item, as prepare_remotion emits them.
            continue
        start, end = _line_span(track, fps)
        if start < 0 or end <= start:
            continue
        lines.append({'from': start, 'to': end, 'track': track,
                      'cues': normalize_cues(track.get('cues'), fps, start, end - start)})

    group: dict | None = None
    for cue in sorted(flat, key=lambda item: _int(item.get('from'))):
        start, end = _int(cue.get('from'), -1), _int(cue.get('to'), -1)
        if start < 0 or end <= start:
            continue
        key = (_text(cue.get('speaker')), cue.get('scene_number'))
        if group is None or group['key'] != key or start - group['to'] > 2:
            group = {'from': start, 'to': end, 'key': key, 'track': cue, 'raw': []}
            lines.append(group)
        group['to'] = max(group['to'], end)
        group['raw'].append({'from': start, 'value': cue.get('value')})

    for line in lines:
        track = line.pop('track')
        if 'raw' in line:
            line['cues'] = normalize_cues(line.pop('raw'), fps, line['from'], line['to'] - line['from'])
            line.pop('key')
        speaker = _text(track.get('speaker')) or _text(track.get('name'))
        text = next((_text(track.get(key)) for key in ('text', 'caption_text', 'tts_text')
                     if _text(track.get(key))), '')
        if not text:
            text = ''.join(_SPEAKER_PREFIX_RE.sub('', caption['text']) for caption in captions
                           if line['from'] <= caption['from'] < line['to'])
        closing = text.rstrip(' 　"”’」』)）')
        line.update({
            'speaker': speaker,
            'actor_hint': _text(track.get('actor')) or _text(track.get('character_id')),
            'text': text,
            'emotion': normalize_emotion(track.get('emotion'), text),
            'question': closing.endswith(('？', '?')),
            'exclaim': closing.endswith(('！', '!')),
        })
    lines.sort(key=lambda item: (item['from'], item['to']))
    return lines


# ---------------------------------------------------------------------------
# Shots, sets and blocking
# ---------------------------------------------------------------------------

def _camera_hint(text: Any) -> str | None:
    words = str(text or '').lower()
    if re.search(r'推|push|dolly in|zoom in', words):
        return 'push_in'
    if re.search(r'左摇|左移|向左|pan left', words):
        return 'slow_pan_left'
    if re.search(r'右摇|右移|向右|pan right', words):
        return 'slow_pan_right'
    if re.search(r'固定|静止|static|locked', words):
        return 'static'
    return None


def _stage_set(shot: dict, motion: dict) -> dict:
    plate = next((value for value in (shot.get('plate'), motion.get('plate')) if isinstance(value, dict)), {})

    def path(*keys: str) -> str:
        return next((_text(plate.get(key)).replace('\\', '/') for key in keys if _text(plate.get(key))), '')

    metrics = shot.get('plate_metrics') if isinstance(shot.get('plate_metrics'), dict) else {}
    ground = next((_float(value) for value in (plate.get('ground_y'), shot.get('ground_y'), metrics.get('mid_y'))
                   if isinstance(value, (int, float)) and not isinstance(value, bool)), 0.86)
    raw_prop = motion.get('set_prop') if isinstance(motion.get('set_prop'), dict) else {}
    kind = _text(raw_prop.get('kind'))
    depth = _text(raw_prop.get('depth')).lower()
    return {
        'plate': path('src', 'plate', 'plate_file', 'file') or _text(shot.get('src')).replace('\\', '/'),
        'far': path('far', 'far_file'),
        'near': path('near', 'near_file'),
        'ground_y': round(_clamp(ground, 0.5, 0.98), 4),
        'walk_scroll_speed': 0.0,
        # An empty kind and image means "no prop"; stage.gd draws nothing for it.
        'prop': {
            'kind': kind if kind in PROP_KINDS else '',
            'x': round(_clamp(_float(raw_prop.get('x'), 0.5), 0.0, 1.0), 4),
            'depth': depth if depth in DEPTHS else 'mid',
            'image': (_text(raw_prop.get('image')) or _text(raw_prop.get('prop_src'))).replace('\\', '/'),
        },
    }


def _normalize_rover(shot: dict, motion: dict) -> dict:
    """Normalize the manifest's rover shorthand into the stage v1 record."""
    motion_rover = motion.get('rover') if isinstance(motion.get('rover'), dict) else {}
    shot_rover = shot.get('rover') if isinstance(shot.get('rover'), dict) else {}
    if not shot_rover and not motion_rover and not _text(shot.get('rover_action')) and not _text(motion.get('rover_action')):
        return {}
    raw = {**motion_rover, **shot_rover}
    action = (
        _text(shot_rover.get('action'))
        or _text(shot.get('rover_action'))
        or _text(motion_rover.get('action'))
        or _text(motion.get('rover_action'))
        or 'idle'
    ).lower()
    action = ROVER_ACTION_ALIASES.get(action, action)
    if action not in ROVER_ACTIONS:
        action = 'idle'
    from_x = round(_clamp(_float(raw.get('from_x'), _float(raw.get('x'), 0.65)), 0.0, 1.0), 4)
    to_x = round(_clamp(_float(raw.get('to_x'), from_x), 0.0, 1.0), 4)
    facing = _text(raw.get('facing')).lower()
    if facing not in FACINGS:
        facing = 'left' if to_x < from_x else 'right'
    depth = _text(raw.get('depth')).lower()
    target_x_default = to_x + (0.14 if facing == 'right' else -0.14)
    return {
        'action': action,
        'from_x': from_x,
        'to_x': to_x,
        'facing': facing,
        'depth': depth if depth in DEPTHS else 'mid',
        'target_x': round(_clamp(_float(raw.get('target_x'), target_x_default), 0.0, 1.0), 4),
        'target_y': round(_clamp(_float(raw.get('target_y'), 0.55), 0.0, 1.0), 4),
        'active': bool(raw.get('active', True)),
    }


def _shot_cast(motion: dict, cast: _Cast) -> list[dict]:
    """Read ``story_motion.actors`` into blocking records, dropping characters without a rig."""
    actors: list[dict] = []
    for raw in motion.get('actors') or []:
        if not isinstance(raw, dict):
            continue
        name, character_id = _text(raw.get('name')), _text(raw.get('character_id'))
        if not name and not character_id:
            continue
        actor_id = cast.enlist(name, character_id, raw.get('stature_scale'))
        if actor_id is None or any(actor['id'] == actor_id for actor in actors):
            continue
        from_x = round(_clamp(_float(raw.get('from_x'), _float(raw.get('x'), 0.5)), 0.0, 1.0), 4)
        to_x = round(_clamp(_float(raw.get('to_x'), from_x), 0.0, 1.0), 4)
        action = _text(raw.get('action')).lower() or 'idle'
        move_clip = map_action(action, to_x - from_x) if action in ('walk', 'walk_medium', 'step_shift') else ''
        if move_clip not in ('walk_medium', 'step_shift'):
            move_clip, to_x = '', from_x
        depth, facing = _text(raw.get('depth')).lower(), _text(raw.get('facing')).lower()
        actors.append({
            'id': actor_id, 'name': cast.names[actor_id], 'from_x': from_x, 'to_x': to_x,
            'action': action, 'move_clip': move_clip,
            'depth': depth if depth in DEPTHS else 'mid',
            'facing': facing if facing in FACINGS else '',
            'emotion': normalize_emotion(raw.get('emotion')),
        })
    return actors


def _block(actors: list[dict], frames: int, director: _Director, settled: bool = False) -> list[dict]:
    """Place the cast for one stage shot. ``settled`` means every move already happened."""
    placed: list[dict] = []
    for actor in actors:
        span = None
        if settled or not actor['move_clip']:
            start = end = actor['to_x'] if settled else actor['from_x']
        else:
            start, end = actor['from_x'], actor['to_x']
            if actor['move_clip'] == 'walk_medium':
                span = (0, frames)
            else:
                length = min(frames, director.clip_frames('step_shift'))
                lead = min(director.frames(0.33), frames - length)
                span = (lead, lead + length)
        placed.append({**actor, 'x': start, 'x_end': end, 'move_span': span,
                       'base_clip': 'listen' if actor['action'] == 'listen' else 'idle'})
    for actor in placed:
        others = [other['x_end'] for other in placed if other is not actor]
        offset = sum(others) / len(others) - actor['x_end'] if others else 0.0
        # Front-facing rigs show who they talk to by leaning toward the rest of the group.
        actor['lean'] = LEAN if offset > STEP_SHIFT_MIN_SPAN else -LEAN if offset < -STEP_SHIFT_MIN_SPAN else 0.0
        toward = 'right' if offset > 0 else 'left' if offset < 0 else ''
        if actor['move_span']:
            actor['facing'] = 'right' if actor['x_end'] >= actor['x'] else 'left'
        elif settled and actor['move_clip'] and toward:
            actor['facing'] = toward  # After walking in, turn to the group, not along the path.
        else:
            actor['facing'] = actor['facing'] or toward or 'right'
    return placed


def _build_segments(timeline_shots: Any, first_frame: int, cast: _Cast, director: _Director) -> list[dict]:
    """One stage shot per timeline shot, except that a long walk becomes a stage shot of its own.

    stage.gd scrolls the set for a whole shot, so the waist-up walk cannot share a shot
    with the dialogue that follows it.
    """
    segments: list[dict] = []
    cursor = first_frame
    previous_scene: Any = None
    counters: dict[int, int] = {}
    used_ids: set[str] = set()

    def unique(shot_id: str) -> str:
        while shot_id in used_ids:
            shot_id += '_x'
        used_ids.add(shot_id)
        return shot_id

    for shot in timeline_shots or []:
        if not isinstance(shot, dict):
            continue
        frames = _int(shot.get('frames', shot.get('duration')), 0)
        if frames <= 0:
            continue
        start = _int(shot.get('from'), cursor)
        cursor = start + frames
        scene = _int(shot.get('scene_number'), 0)
        counters[scene] = counters.get(scene, 0) + 1
        shot_id = unique(f"s{scene}_sh{_int(shot.get('shot_number'), 0) or counters[scene]}")
        motion = shot.get('story_motion') if isinstance(shot.get('story_motion'), dict) else {}
        actors = _shot_cast(motion, cast)
        framing = _text(motion.get('framing')).lower()
        common = {
            'scene_number': scene,
            'framing': framing if framing in ('wide', 'medium', 'close') else ('wide' if len(actors) >= 2 else 'medium'),
            'camera_hint': _camera_hint(motion.get('camera') or shot.get('camera')),
            'rover': _normalize_rover(shot, motion),
        }
        scene_first, previous_scene = scene != previous_scene, scene
        walker = max((actor for actor in actors if actor['move_clip'] == 'walk_medium'),
                     key=lambda actor: abs(actor['to_x'] - actor['from_x']), default=None)
        if walker is None:
            segments.append({**common, 'id': shot_id, 'from': start, 'frames': frames, 'scene_first': scene_first,
                             'walk_actor': '', 'set': _stage_set(shot, motion),
                             'actors': _block(actors, frames, director)})
            continue
        # Whole walk cycles, so the last step lands as the shot cuts.
        cycles = 2 if abs(walker['to_x'] - walker['from_x']) <= 0.24 else 3
        walk_frames = cycles * director.clip_frames('walk_medium')
        if frames - walk_frames < max(director.min_cut, director.frames(1.5)):
            walk_frames = frames
        walk_set = _stage_set(shot, motion)
        walk_set['walk_scroll_speed'] = math.copysign(WALK_SCROLL_SPEED, walker['to_x'] - walker['from_x'])
        segments.append({**common, 'id': shot_id, 'from': start, 'frames': walk_frames, 'scene_first': scene_first,
                         'walk_actor': walker['id'], 'set': walk_set,
                         'actors': _block(actors, walk_frames, director)})
        if walk_frames < frames:
            rest = frames - walk_frames
            segments.append({**common, 'id': unique(f'{shot_id}b'), 'from': start + walk_frames, 'frames': rest,
                             'scene_first': False, 'walk_actor': '', 'camera_hint': None,
                             'set': _stage_set(shot, motion),
                             'actors': _block(actors, rest, director, settled=True)})
    return segments


def _stage_lines(segment: dict, lines: list[dict]) -> list[dict]:
    """Lines spoken by an actor standing in this stage shot, clipped to it, in shot frames."""
    start, frames = segment['from'], segment['frames']
    on_stage = [actor['id'] for actor in segment['actors']]
    local: list[dict] = []
    for line in lines:
        if line['actor'] not in on_stage or line['to'] <= start or line['from'] >= start + frames:
            continue
        local.append({**line, 'a': max(0, line['from'] - start), 'b': min(frames, line['to'] - start)})
    for current, following in zip(local, local[1:]):
        current['b'] = min(current['b'], following['a'])
    local = [line for line in local if line['b'] - line['a'] >= 2]

    positions = {actor['id']: actor['x_end'] for actor in segment['actors']}
    for index, line in enumerate(local):
        speaker = line['actor']
        # Talk to whoever spoke last, else to whoever answers, else to the nearest person.
        others = ([item['actor'] for item in reversed(local[:index])] + [item['actor'] for item in local[index + 1:]]
                  + sorted(on_stage, key=lambda actor_id: abs(positions[actor_id] - positions[speaker])))
        line['target'] = next((actor_id for actor_id in others if actor_id != speaker), '')
    run_end = 0
    for index in range(len(local) - 1, -1, -1):
        if index == len(local) - 1 or local[index + 1]['actor'] != local[index]['actor']:
            run_end = local[index]['b']
        local[index]['run_end'] = run_end
    return local


# ---------------------------------------------------------------------------
# Cuts
# ---------------------------------------------------------------------------

def _line_framing(line: dict, base: str, director: _Director) -> str:
    punchy = line['exclaim'] and line['to'] - line['from'] <= director.frames(1.5)
    return 'close' if base == 'close' or line['emotion'] in _STRONG_EMOTIONS or punchy else 'medium'


def _desired_cuts(segment: dict, lines: list[dict], director: _Director) -> list[dict]:
    frames, actors, base = segment['frames'], segment['actors'], segment['framing']
    count = len(actors)
    group = 'wide' if count != 2 or (segment['scene_first'] and base == 'wide') else 'two_shot'
    primary = lines[0]['actor'] if lines else actors[0]['id']
    if count >= 2 and segment['scene_first'] and frames < director.long_take:
        return [{'at': 0, 'framing': group, 'focus': '', 'until': frames}]  # Too short to open and cut in.

    desired: list[dict] = []
    floor = 0
    low, middle, high = director.establish
    if frames >= director.long_take and (base == 'wide' or (count >= 2 and segment['scene_first'])):
        first = lines[0]['a'] if lines else -1
        floor = first if low <= first <= high else middle
        desired.append({'at': 0, 'framing': group, 'focus': '', 'until': floor})
    opening = len(desired)
    settle, quiet = director.frames(0.5), director.frames(3.0)
    for index, line in enumerate(lines):
        at = max(line['a'], floor)
        if line['b'] - at >= director.min_cut // 2:
            desired.append({'at': at, 'framing': _line_framing(line, base, director),
                            'focus': line['actor'], 'until': line['run_end']})
        resume = lines[index + 1]['a'] if index + 1 < len(lines) else frames
        if count >= 2 and resume - line['b'] >= quiet and line['b'] + settle >= floor:
            # Nobody on stage talks for a while: step back instead of staring at one face.
            desired.append({'at': line['b'] + settle, 'framing': group, 'focus': '', 'until': resume})
    if len(desired) == opening:  # Nobody on stage speaks from here on.
        if opening:
            desired.append({'at': floor, 'framing': 'close' if base == 'close' else 'medium',
                            'focus': primary, 'until': frames})
        elif base == 'wide':
            desired.append({'at': 0, 'framing': group, 'focus': '', 'until': frames})
        else:
            desired.append({'at': 0, 'framing': base, 'focus': primary, 'until': frames})
    return desired


def _enforce_min_cut(desired: list[dict], frames: int, director: _Director) -> list[dict]:
    """Accept each wanted cut only if both it and its predecessor last at least ``min_cut``."""
    accepted: list[dict] = []
    for want in desired:
        if not accepted:
            accepted.append({**want, 'at': 0})
            continue
        current = accepted[-1]
        same_focus = want['focus'] == current['focus']
        if same_focus and (want['framing'] == current['framing'] or current['framing'] == 'close'):
            current['until'] = max(current['until'], want['until'])  # Never pull back mid-run.
            continue
        at = max(want['at'], current['at'] + director.min_cut)
        if at > want['at'] and want['until'] - at < director.min_cut // 2:
            continue  # By the time the cut is allowed, this speaker has all but finished.
        if frames - at < director.min_cut:
            continue
        accepted.append({**want, 'at': at})
    return accepted


def _partner(focus: str, start: int, end: int, segment: dict, lines: list[dict]) -> str:
    for line in lines:
        if line['actor'] == focus and line['a'] < end and line['b'] > start and line['target']:
            return line['target']
    positions = {actor['id']: actor['x_end'] for actor in segment['actors']}
    others = sorted((actor_id for actor_id in positions if actor_id != focus),
                    key=lambda actor_id: abs(positions[actor_id] - positions.get(focus, 0.5)))
    return others[0] if others else ''


def _break_long_take(start: int, end: int, framing: str, focus: str, partner: str,
                     director: _Director, reactions: list[tuple]) -> list[tuple]:
    """Split a take longer than 4.5 s: cut away to the listener, or change the shot size."""
    pieces: list[tuple] = []
    low = director.min_cut
    while end - start > director.long_take:
        length = end - start
        cutaway = bool(partner) and director.variation % 2 == 0 and length >= director.reaction + 2 * low
        director.variation += 1
        if cutaway:
            at = start + min(director.long_take, int(_clamp(round(length * 0.42), low, length - director.reaction - low)))
            pieces += [(start, framing, focus), (at, 'medium', partner)]
            reactions.append((partner, at, at + director.reaction, focus))
            start = at + director.reaction
        else:
            at = start + min(director.long_take, int(_clamp(round(length * 0.5), low, length - low)))
            pieces.append((start, framing, focus))
            framing = 'medium' if framing == 'close' else 'close'
            start = at
    pieces.append((start, framing, focus))
    return pieces


def _plan_cuts(segment: dict, lines: list[dict], director: _Director) -> tuple[list[dict], list[tuple]]:
    """Return cuts that tile ``[0, frames)`` and the listener cutaways ``(actor, from, to, speaker)``."""
    frames = segment['frames']
    reactions: list[tuple] = []
    if segment['walk_actor']:
        plan = [(0, 'medium', segment['walk_actor'])]
    elif not segment['actors']:
        plan = [(0, 'wide', '')]
    else:
        accepted = _enforce_min_cut(_desired_cuts(segment, lines, director), frames, director)
        plan = []
        for index, cut in enumerate(accepted):
            end = accepted[index + 1]['at'] if index + 1 < len(accepted) else frames
            if cut['framing'] in ('medium', 'close') and cut['focus']:
                partner = _partner(cut['focus'], cut['at'], end, segment, lines)
                plan += _break_long_take(cut['at'], end, cut['framing'], cut['focus'], partner, director, reactions)
            else:
                plan.append((cut['at'], cut['framing'], cut['focus']))
    cuts: list[dict] = []
    for index, (start, framing, focus) in enumerate(plan):
        if framing == 'close':
            allowed, preferred = ('push_in', 'static'), None  # A pan on a face reads as drift.
        elif segment['walk_actor']:
            allowed, preferred = ('static', 'push_in'), 'static'  # The scrolling set is the move.
        else:
            allowed, preferred = CAMERA_MOVES, segment['camera_hint'] if index == 0 else None
        cuts.append({'from': start, 'to': plan[index + 1][0] if index + 1 < len(plan) else frames,
                     'framing': framing, 'focus_actor': focus,
                     'camera_move': director.next_move(allowed, preferred)})
    return cuts, reactions


# ---------------------------------------------------------------------------
# Beats
# ---------------------------------------------------------------------------

def _pick_gesture(line: dict, action: str, director: _Director) -> str:
    """Choose the speaker's clip from the line and the shot's action, avoiding a repeat."""
    key = (line['from'], line['to'], line['actor'])
    if key in director.line_gestures:  # The line continues from the previous stage shot.
        return director.line_gestures[key]
    text, emotion = line['text'], line['emotion']
    frames = line['to'] - line['from']
    strong = emotion in _STRONG_EMOTIONS
    if action == 'wave' or _WAVE_RE.search(text):
        clip = 'wave'
    elif action == 'react' or (emotion == 'surprised' and frames <= director.frames(2.0)):
        clip = 'react'
    elif action in ('work', 'console', 'operate'):
        clip = 'operate'
    elif action in ('point', 'console') or _POINT_RE.search(text):
        clip = 'point'
    elif strong or line['exclaim']:
        clip = 'talk_emphatic'
    elif line['question'] or frames >= director.frames(3.5) or _EXPLAIN_RE.search(text):
        clip = 'talk_explain'
    else:
        clip = 'talk_calm'
    if clip == director.last_gesture.get(line['actor']):
        if clip in _GESTURE_FOLLOW:
            clip = 'talk_emphatic' if strong or line['exclaim'] else 'talk_explain'
        elif clip == 'talk_calm':
            clip = 'talk_explain'
        elif clip == 'talk_explain':
            clip = 'talk_calm'
        elif not strong:  # talk_emphatic twice is right only while the anger or shock lasts.
            clip = 'talk_explain'
    director.last_gesture[line['actor']] = director.line_gestures[key] = clip
    return clip


def _layer_at(layer: list[tuple], frame: float) -> dict | None:
    return next((data for start, end, data in reversed(layer) if start <= frame < end), None)


def _actor_beats(actor: dict, layers: dict[str, list[tuple]], frames: int) -> list[dict]:
    """Flatten one actor's layers into beats. Priority: move, speak, nod, listen, action."""
    move = actor['move_span']
    points = {0, frames, *(move or ())}
    for layer in layers.values():
        for start, end, _ in layer:
            points.update((start, end))
    marks = sorted(point for point in points if 0 <= point <= frames)
    spans: list[list] = []
    for start, end in zip(marks, marks[1:]):
        middle = (start + end) / 2
        speak, nod, listen, action = (_layer_at(layers[name], middle) for name in ('speak', 'nod', 'listen', 'action'))
        if move and move[0] <= middle < move[1]:
            # One unbroken beat: the player restarts a clip at every beat boundary.
            key = (actor['move_clip'], actor['emotion'], False, '')
        elif speak:
            key = (speak['clip'], speak['emotion'], True, speak['look_at'])
        elif nod or listen:
            key = ('nod' if nod else 'listen', actor['emotion'], False, (nod or listen)['look_at'])
        elif action:
            key = (action['clip'], actor['emotion'], False, '')
        elif move and middle >= move[1]:
            # Without a beat the player puts the actor back at actors[].x.
            key = (actor['base_clip'], actor['emotion'], False, '')
        else:
            continue
        if spans and spans[-1][1] == start and spans[-1][2] == key:
            spans[-1][1] = end
        else:
            spans.append([start, end, key])

    index = 0
    while index < len(spans):  # Fold slivers into a neighbour instead of flashing a pose.
        start, end, _ = spans[index]
        if end - start >= 3 or len(spans) == 1:
            index += 1
        elif index and spans[index - 1][1] == start:
            spans[index - 1][1] = end
            del spans[index]
        elif index + 1 < len(spans) and spans[index + 1][0] == end:
            spans[index + 1][0] = start
            del spans[index]
        else:
            del spans[index]
    merged: list[list] = []
    for span in spans:  # Folding can leave two equal neighbours; one beat keeps the clip running.
        if merged and merged[-1][1] == span[0] and merged[-1][2] == span[2]:
            merged[-1][1] = span[1]
        else:
            merged.append(span)

    def position(frame: int) -> float:
        if not move or frame <= move[0]:
            return actor['x']
        if frame >= move[1]:
            return actor['x_end']
        return round(actor['x'] + (actor['x_end'] - actor['x']) * (frame - move[0]) / (move[1] - move[0]), 4)

    return [{'from': start, 'to': end, 'actor': actor['id'], 'clip': clip, 'emotion': emotion,
             'speaking': speaking, 'look_at': look_at, 'move_x': [position(start), position(end)]}
            for start, end, (clip, emotion, speaking, look_at) in merged]


def _plan_beats(segment: dict, lines: list[dict], reactions: list[tuple], director: _Director) -> list[dict]:
    frames, actors = segment['frames'], segment['actors']
    by_id = {actor['id']: actor for actor in actors}
    layers = {actor['id']: {'speak': [], 'nod': [], 'listen': [], 'action': []} for actor in actors}
    nod_frames = director.clip_frames('nod')
    spoke: set[str] = set()

    for index, line in enumerate(lines):
        speaker = line['actor']
        following = lines[index + 1] if index + 1 < len(lines) else None
        clip = _pick_gesture(line, '' if speaker in spoke else by_id[speaker]['action'], director)
        spoke.add(speaker)
        end = line['b']
        if following and following['actor'] == speaker and following['a'] - end <= director.frames(0.5):
            end = following['a']  # Keep gesturing through a breath instead of dropping to idle.
        state = {'emotion': line['emotion'], 'look_at': line['target']}
        head = director.clip_frames(clip) if clip in _GESTURE_FOLLOW else 0
        if head and end - line['a'] - head >= director.frames(0.75):
            layers[speaker]['speak'] += [(line['a'], line['a'] + head, {**state, 'clip': clip}),
                                         (line['a'] + head, end, {**state, 'clip': _GESTURE_FOLLOW[clip]})]
        else:
            layers[speaker]['speak'].append((line['a'], end, {**state, 'clip': clip}))
        # Listeners hold through a short pause so they do not flicker between lines.
        hold = (following['a'] if following and following['a'] - line['b'] <= director.frames(1.0)
                else min(frames, line['b'] + director.frames(0.5)))
        for actor in actors:
            if actor['id'] != speaker:
                layers[actor['id']]['listen'].append((line['a'], hold, {'look_at': speaker}))

    def add_nod(actor_id: str, start: int, speaker: str) -> None:
        end = start + nod_frames
        spacing = director.frames(2.0)
        if start < 0 or end > frames:
            return
        if any(start < busy_end and end > busy_start for busy_start, busy_end, _ in layers[actor_id]['speak']):
            return
        if any(start < other_end + spacing and end > other_start - spacing
               for other_start, other_end, _ in layers[actor_id]['nod']):
            return
        layers[actor_id]['nod'].append((start, end, {'look_at': speaker}))

    for actor_id, start, end, speaker in reactions:  # Give the cutaway something to show.
        if any(busy_start < end and busy_end > start for busy_start, busy_end, _ in layers[speaker]['speak']):
            add_nod(actor_id, start + (end - start - nod_frames) // 2, speaker)
    for index, line in enumerate(lines):
        if not line['target'] or line['question']:
            continue
        pause = (lines[index + 1]['a'] if index + 1 < len(lines) else frames) - line['b']
        if line['b'] - line['a'] >= director.frames(2.5):
            add_nod(line['target'], line['b'] - nod_frames - director.frames(0.15), line['actor'])
        elif pause >= director.frames(0.6):
            add_nod(line['target'], line['b'], line['actor'])

    if not segment['walk_actor']:
        lead = director.frames(0.25)
        for actor in actors:
            clip = map_action(actor['action'])
            if actor['id'] in spoke:
                continue
            if clip in _GESTURE_FOLLOW:
                layers[actor['id']]['action'].append((lead, min(frames, lead + director.clip_frames(clip)), {'clip': clip}))
            elif clip in M2_CLIPS:
                layers[actor['id']]['action'].append((0, frames, {'clip': clip}))

    beats = [beat for actor in actors for beat in _actor_beats(actor, layers[actor['id']], frames)]
    order = {actor['id']: index for index, actor in enumerate(actors)}
    beats.sort(key=lambda beat: (beat['from'], order[beat['actor']]))
    return beats


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def _clean_captions(captions: Any) -> list[dict]:
    cleaned = []
    for caption in captions or []:
        if not isinstance(caption, dict) or not _text(caption.get('text')):
            continue
        start, end = _int(caption.get('from'), -1), _int(caption.get('to'), -1)
        if start >= 0 and end > start:
            cleaned.append({'from': start, 'to': end, 'text': caption['text'].strip()})
    cleaned.sort(key=lambda caption: caption['from'])
    return cleaned


def _clean_audio(audio_tracks: Any, fps: int) -> list[dict]:
    cleaned = []
    for track in audio_tracks or []:
        if not isinstance(track, dict):
            continue
        file = (_text(track.get('file')) or _text(track.get('src'))).replace('\\', '/')
        if not file:
            continue
        if track.get('start_ms') is not None:
            start_ms = _int(track['start_ms'])
        else:
            start_ms = _int(_float(track.get('from')) * 1000 / fps)  # prepare_remotion style frames.
        cleaned.append({'file': file, 'start_ms': max(0, start_ms),
                        'volume': round(_clamp(_float(track.get('volume'), 1.0), 0.0, 4.0), 3),
                        'loop': bool(track.get('loop'))})
    return cleaned


def build_stage(
    manifest: dict,
    timeline_shots: list[dict],
    speech_tracks: list[dict],
    captions: list[dict],
    rigs_map: dict,
    audio_tracks: list[dict],
    intro_info: dict | None = None,
    cta_info: dict | None = None,
) -> dict:
    """Expand the manifest and the measured timeline into a ``stage.json`` v1 document.

    manifest: the Worker manifest; ``width``, ``height``, ``fps``, ``episode_number`` and
        ``episode_title`` are read.
    timeline_shots: manifest shots in play order, each with an absolute ``from`` and a
        length (``frames`` or ``duration``) in frames, plus ``scene_number``, ``story_motion``
        and optionally ``plate`` / ``plate_metrics``.
    speech_tracks: one item per voice line ``{speaker, from, to, cues[, text, emotion]}``
        in absolute frames, or prepare_remotion's flat cue items ``{speaker, from, to, value}``.
    captions: ``{from, to, text}`` in absolute frames.
    rigs_map: character id or name -> ``{rig_json[, stature_scale, name, id]}`` (or a path).
    audio_tracks: ``{file, start_ms, volume, loop}`` (``src`` and a frame ``from`` also work).
    intro_info / cta_info: ``{frames, title, subtitle[, episode_number]}``; the CTA also
        takes ``from``.
    """
    manifest = manifest if isinstance(manifest, dict) else {}
    fps = int(_clamp(_int(manifest.get('fps'), 24), 1, 240))
    director = _Director(fps)
    cast = _Cast(rigs_map)

    intro_info = intro_info if isinstance(intro_info, dict) else {}
    intro = {
        'frames': max(0, _int(intro_info.get('frames'), 0)),
        'episode_number': _int(intro_info.get('episode_number'), _int(manifest.get('episode_number'), 1)),
        'title': str(intro_info.get('title') or manifest.get('episode_title') or ''),
        'subtitle': str(intro_info.get('subtitle') or ''),
    }

    clean_captions = _clean_captions(captions)
    segments = _build_segments(timeline_shots, intro['frames'], cast, director)
    lines = _normalize_lines(speech_tracks, clean_captions, fps)
    for line in lines:  # Needs the whole cast, so it runs after every shot is read.
        line['actor'] = cast.speaker_id(line)

    shots = []
    for segment in segments:
        stage_lines = _stage_lines(segment, lines)
        cuts, reactions = _plan_cuts(segment, stage_lines, director)
        stage_shot = {
            'id': segment['id'],
            'scene_number': segment['scene_number'],
            'from': segment['from'],
            'frames': segment['frames'],
            'set': segment['set'],
            'actors': [{'id': actor['id'], 'name': actor['name'], 'x': actor['x'], 'depth': actor['depth'],
                        'facing': actor['facing'], 'lean': actor['lean'], 'base_clip': actor['base_clip'],
                        'emotion': actor['emotion']} for actor in segment['actors']],
            'beats': _plan_beats(segment, stage_lines, reactions, director),
            'cuts': cuts,
        }
        if segment['rover']:
            stage_shot['rover'] = segment['rover']
        shots.append(stage_shot)

    content_end = max([intro['frames']] + [shot['from'] + shot['frames'] for shot in shots])
    cta_info = cta_info if isinstance(cta_info, dict) else {}
    cta_frames = max(0, _int(cta_info.get('frames'), 0))
    cta = {
        'from': max(0, _int(cta_info.get('from'), content_end)),
        'frames': cta_frames,
        'title': str(cta_info.get('title') or cta_info.get('name') or ''),
        'subtitle': str(cta_info.get('subtitle') or ''),
    }
    robot_rig_path = ''
    robot_rig_value = manifest.get('robot_rig')
    if isinstance(robot_rig_value, dict):
        robot_rig_path = _text(robot_rig_value.get('rig_json'))
    robot_rig_path = robot_rig_path or _text(manifest.get('robot_rig_json'))
    if not robot_rig_path:
        for timeline_shot in timeline_shots:
            if not isinstance(timeline_shot, dict):
                continue
            motion = timeline_shot.get('story_motion') if isinstance(timeline_shot.get('story_motion'), dict) else {}
            rover = timeline_shot.get('rover') if isinstance(timeline_shot.get('rover'), dict) else {}
            robot_rig_path = (
                _text(timeline_shot.get('robot_rig_json'))
                or _text(rover.get('rig_json'))
                or _text(motion.get('robot_rig_json'))
            )
            if robot_rig_path:
                break
    stage = {
        'version': STAGE_VERSION,
        'width': _int(manifest.get('width'), STAGE_SIZE[0]),
        'height': _int(manifest.get('height'), STAGE_SIZE[1]),
        'fps': fps,
        'total_frames': max(content_end, cta['from'] + cta_frames),
        'intro': intro,
        'cta': cta,
        'rigs': dict(cast.used),
        'shots': shots,
        # A narrator or a character who never stands on stage has no mouth to drive.
        'speech': [{'actor': line['actor'], 'from': line['from'], 'to': line['to'], 'cues': line['cues']}
                   for line in lines if line['actor']],
        'captions': clean_captions,
        'audio': _clean_audio(audio_tracks, fps),
    }
    if robot_rig_path:
        stage['robot_rig'] = {'rig_json': robot_rig_path.replace('\\', '/')}
    return stage


def validate_stage(stage: dict) -> list[str]:
    """Return every way ``stage`` breaks the v1 contract; an empty list means it is playable."""
    problems: list[str] = []
    if stage.get('version') != STAGE_VERSION:
        problems.append('version must be 1')
    if (stage.get('width'), stage.get('height')) != STAGE_SIZE:
        problems.append('the Godot player only accepts a 1280x720 stage')
    fps, total = _int(stage.get('fps'), 0), _int(stage.get('total_frames'), 0)
    if fps <= 0 or total <= 0:
        problems.append('fps and total_frames must be positive')
        return problems
    min_cut = max(1, round(MIN_CUT_FRAMES_AT_24 * fps / 24))
    rigs = stage.get('rigs') or {}
    robot_rig = stage.get('robot_rig')
    robot_rig_valid = (
        isinstance(robot_rig, dict)
        and bool(_text(robot_rig.get('rig_json')))
    )
    if robot_rig is not None and not robot_rig_valid:
        problems.append('robot_rig must contain a non-empty rig_json')
    cursor = 0
    for shot in stage.get('shots') or []:
        label = f"shot {shot.get('id')}"
        start, frames = _int(shot.get('from'), -1), _int(shot.get('frames'), 0)
        if start < cursor or frames <= 0 or start + frames > total:
            problems.append(f'{label}: overlaps the previous shot or leaves the timeline')
        cursor = start + frames
        actor_ids = [actor.get('id') for actor in shot.get('actors') or []]
        rover = shot.get('rover')
        if rover is not None:
            if not isinstance(rover, dict):
                problems.append(f'{label}: rover must be an object')
            else:
                if not robot_rig_valid:
                    problems.append(f'{label}: rover requires top-level robot_rig')
                if rover.get('action') not in ROVER_ACTIONS:
                    problems.append(f"{label}: unknown rover action {rover.get('action')}")
                if rover.get('facing') not in FACINGS or rover.get('depth') not in DEPTHS:
                    problems.append(f'{label}: rover has a bad facing or depth')
                for key in ('from_x', 'to_x', 'target_x', 'target_y'):
                    if not 0.0 <= _float(rover.get(key), -1.0) <= 1.0:
                        problems.append(f'{label}: rover {key} must be between 0 and 1')
                if not isinstance(rover.get('active'), bool):
                    problems.append(f'{label}: rover active must be boolean')
        for actor in shot.get('actors') or []:
            if actor.get('id') not in rigs:
                problems.append(f"{label}: actor {actor.get('id')} has no rig")
            if actor.get('base_clip') not in CLIPS:
                problems.append(f"{label}: unknown base_clip {actor.get('base_clip')}")
            if actor.get('depth') not in DEPTHS or not -1.0 <= _float(actor.get('lean'), 9.0) <= 1.0:
                problems.append(f"{label}: actor {actor.get('id')} has a bad depth or lean")
        expected = 0
        cuts = shot.get('cuts') or []
        for cut in cuts:
            if cut.get('from') != expected or _int(cut.get('to'), 0) <= expected:
                problems.append(f'{label}: cuts must tile the shot without gaps')
            if len(cuts) > 1 and _int(cut.get('to'), 0) - _int(cut.get('from'), 0) < min_cut:
                problems.append(f"{label}: cut at {cut.get('from')} is shorter than {min_cut} frames")
            if cut.get('framing') not in FRAMINGS or cut.get('camera_move') not in CAMERA_MOVES:
                problems.append(f"{label}: cut at {cut.get('from')} uses an unknown framing or camera_move")
            if cut.get('focus_actor') and cut['focus_actor'] not in actor_ids:
                problems.append(f"{label}: cut focuses on absent actor {cut['focus_actor']}")
            expected = _int(cut.get('to'), 0)
        if expected != frames:
            problems.append(f'{label}: cuts end at {expected}, the shot at {frames}')
        busy: dict[str, int] = {}
        for beat in sorted(shot.get('beats') or [], key=lambda item: _int(item.get('from'))):
            actor_id, begin, end = beat.get('actor'), _int(beat.get('from'), -1), _int(beat.get('to'), -1)
            if actor_id not in actor_ids or beat.get('clip') not in CLIPS:
                problems.append(f'{label}: beat at {begin} names an absent actor or unknown clip')
            if begin < busy.get(actor_id, 0) or end <= begin or end > frames:
                problems.append(f'{label}: beat at {begin} for {actor_id} overlaps or leaves the shot')
            busy[actor_id] = end
            look_at = beat.get('look_at')
            if look_at and (look_at not in actor_ids or look_at == actor_id):
                problems.append(f'{label}: beat at {begin} looks at {look_at}')
            move = beat.get('move_x')
            if not (isinstance(move, list) and len(move) == 2 and all(0.0 <= _float(x, -1.0) <= 1.0 for x in move)):
                problems.append(f'{label}: beat at {begin} needs move_x [from, to]')
    for entry in stage.get('speech') or []:
        length = _int(entry.get('to'), 0) - _int(entry.get('from'), 0)
        frames_seen = [cue[0] for cue in entry.get('cues') or []]
        if entry.get('actor') not in rigs or length <= 0:
            problems.append(f"speech at {entry.get('from')}: unknown actor or empty span")
        if (not frames_seen or frames_seen[0] != 0 or frames_seen != sorted(set(frames_seen))
                or frames_seen[-1] >= length or any(cue[1] not in VISEMES for cue in entry['cues'])):
            problems.append(f"speech at {entry.get('from')}: cues must ascend from 0 with known visemes")
    for caption in stage.get('captions') or []:
        if _int(caption.get('to'), 0) <= _int(caption.get('from'), 0):
            problems.append(f"caption at {caption.get('from')}: empty span")
    return problems
