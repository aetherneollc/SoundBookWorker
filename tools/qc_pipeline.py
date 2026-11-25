#!/usr/bin/env python3
"""Deterministic and still-frame QC for a rendered 3D episode.

Reads ``stage3d.json`` plus per-frame ``qc_telemetry.json`` from the Godot
player. Optional MP4 (ffprobe) and sampled PNG stills add duration, stream,
and exposure checks.

Telemetry frames (a list, or a dict keyed by frame number) look like::

    {
      "frame": 12,
      "actors": {
        "linlan": {
          "left_foot": [0.1, 0.0, 0.2],
          "right_foot": [-0.1, 0.2, 0.2],
          "pelvis": [0.0, 0.95, 0.2],
          "clips": ["walk"],
          "clip_status": {"walk": "ok"},
          "hands": {"left": [-0.3, 1.0, 0.2], "right": [0.3, 1.0, 0.2]}
        }
      },
      "props": {
        "sensor": {"owner": "linlan", "position": [0.32, 1.0, 0.22], "hand_gap_m": 0.03}
      },
      "rover": {"wheel_min_y": 0.0, "position": [1.0, 0.2, 1.0]},
      "camera": {
        "position": [0.0, 1.6, 2.0],
        "look_at": [0.0, 1.4, 0.0],
        "wall_distance_m": 1.2,
        "subject": {"id": "linlan", "ndc_x": 0.1, "ndc_y": 0.0, "occluded": false}
      }
    }

Each check is ``PASS``, ``WARN``, or ``FAIL`` and names the exact frames to
replan. Exit status is 0 for PASS, 2 for WARN, and 1 for FAIL.

CLI::

    python tools/qc_pipeline.py --stage stage3d.json --telemetry qc_telemetry.json \\
        --video episode.mp4 --stills review_dir --out qc_report.json
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, UnidentifiedImageError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.asset_registry import missing_facial_morphs

FOOT_FLOAT_M = 0.035
FOOT_SINK_M = -0.04
WHEEL_FLOAT_M = 0.03
WHEEL_SINK_M = -0.03
PLANTED_Y_M = 0.03
SLIDE_M_PER_FRAME = 0.045
SLIDE_SUSTAINED = 2
HAND_GAP_M = 0.12
HANDOFF_GAP_M = 0.36
PROP_TELEPORT_M = 0.35
CAMERA_CLEARANCE_M = 0.15
NDC_LIMIT = 0.95
AUDIO_TOLERANCE_S = 0.15
VIDEO_SIZE = (1280, 720)
VIDEO_FPS = 24.0
BLACK_MEAN_LUMA = 12.0
BLOWN_PERCENT = 25.0
FLAT_STD_LUMA = 5.0
OVEREXPOSED_LUMA = 250.0
_EPS = 1e-6

HINT_GROUND = 'snap_to_ground'
HINT_WALK = 'adjust_walk_speed'
HINT_GRIP = 'reseat_prop_in_hand'
HINT_HANDOFF = 'smooth_prop_handoff'
HINT_OWNER = 'fix_prop_ownership'
HINT_CAMERA = 'shift_camera_inward'
HINT_WIDEN = 'widen_shot'
HINT_AUDIO = 'retimeline_audio'
HINT_EXPOSURE = 'adjust_exposure'
HINT_CLIP = 'restore_required_clip'
HINT_CLOSEUP = 'upgrade_character_for_closeup'

_STILL_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp'}
_CLOSEUP_TYPES = {'closeup', 'ecu', 'facial', 'face'}


def run_qc(stage: dict, telemetry: dict | list, video_path: str | Path | None = None,
           review_dir: str | Path | None = None, *, probe: dict | None = None,
           registry: dict | str | Path | None = None) -> dict:
    """Run every QC check and return a structured report. Does not write a file."""
    stage = stage if isinstance(stage, dict) else {}
    rows = _frame_rows(telemetry)
    registry_doc = _load_registry(registry)
    checks = [
        _check_grounding(stage, rows),
        _check_foot_slide(rows),
        _check_props(stage, rows),
        _check_camera(stage, rows),
        _check_framing(rows),
        _check_audio(stage, video_path, probe),
        _check_exposure(review_dir),
        _check_animation(stage, rows, registry_doc),
    ]
    status, suggestions = _overall(checks)
    fps = float(stage.get('fps') or 0) or None
    total = stage.get('total_frames')
    duration = None
    if fps and isinstance(total, (int, float)) and fps > 0:
        duration = float(total) / fps
    return {
        'version': 1,
        'status': status,
        'inputs': {
            'video': None if video_path is None else str(video_path),
            'stills': None if review_dir is None else str(review_dir),
        },
        'timeline': {
            'fps': fps,
            'total_frames': int(total) if isinstance(total, (int, float)) else None,
            'duration_s': None if duration is None else float(round(duration, 6)),
        },
        'checks': checks,
        'replan_suggestions': suggestions,
        'summary': {
            'pass': sum(1 for check in checks if check['status'] == 'PASS'),
            'warn': sum(1 for check in checks if check['status'] == 'WARN'),
            'fail': sum(1 for check in checks if check['status'] == 'FAIL'),
        },
    }


def probe_media(path: str | Path) -> dict:
    """Read duration, size, frame rate, and stream kinds with ffprobe (or ffmpeg fallback)."""
    try:
        completed = subprocess.run(
            ['ffprobe', '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', str(path)],
            check=False, capture_output=True, text=True, encoding='utf-8',
        )
        if completed.returncode == 0:
            return parse_probe(json.loads(completed.stdout or '{}'))
    except FileNotFoundError:
        pass
    return _probe_with_ffmpeg(path)


def _probe_with_ffmpeg(path: str | Path) -> dict:
    """Fallback media prober using ffmpeg (e.g. imageio_ffmpeg on Windows without ffprobe)."""
    import shutil
    ffmpeg_bin = shutil.which('ffmpeg')
    if not ffmpeg_bin:
        try:
            import imageio_ffmpeg
            ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            ffmpeg_bin = None
    if not ffmpeg_bin:
        raise RuntimeError('neither ffprobe nor ffmpeg is installed')
    completed = subprocess.run(
        [ffmpeg_bin, '-hide_banner', '-i', str(path)],
        check=False, capture_output=True, text=True, encoding='utf-8', errors='replace',
    )
    text = (completed.stderr or '') + '\n' + (completed.stdout or '')
    duration = None
    m_dur = re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)', text)
    if m_dur:
        duration = int(m_dur.group(1)) * 3600.0 + int(m_dur.group(2)) * 60.0 + float(m_dur.group(3))
    video_line = next((line for line in text.splitlines() if 'Video:' in line), None)
    audio_line = next((line for line in text.splitlines() if 'Audio:' in line), None)
    width = height = fps = None
    if video_line:
        m_dim = re.search(r'(\d{2,5})x(\d{2,5})', video_line)
        if m_dim:
            width, height = int(m_dim.group(1)), int(m_dim.group(2))
        m_fps = re.search(r'(\d+(?:\.\d+)?)\s*fps', video_line)
        if m_fps:
            fps = float(m_fps.group(1))
    if duration is None and video_line is None:
        raise RuntimeError(text.strip()[:240] or 'ffmpeg probe failed')
    return {
        'duration': duration,
        'width': width,
        'height': height,
        'fps': fps,
        'has_video': video_line is not None,
        'has_audio': audio_line is not None,
    }


def parse_probe(payload: dict) -> dict:
    streams = payload.get('streams') if isinstance(payload.get('streams'), list) else []
    video = next((stream for stream in streams if isinstance(stream, dict) and stream.get('codec_type') == 'video'), None)
    audio = next((stream for stream in streams if isinstance(stream, dict) and stream.get('codec_type') == 'audio'), None)
    fmt = payload.get('format') if isinstance(payload.get('format'), dict) else {}
    duration = _float_or_none(fmt.get('duration'))
    if duration is None and isinstance(video, dict):
        duration = _float_or_none(video.get('duration'))
    fps = None
    if isinstance(video, dict):
        for key in ('avg_frame_rate', 'r_frame_rate'):
            fps = _parse_rate(video.get(key))
            if fps:
                break
    return {
        'duration': duration,
        'width': None if not isinstance(video, dict) else video.get('width'),
        'height': None if not isinstance(video, dict) else video.get('height'),
        'fps': fps,
        'has_video': video is not None,
        'has_audio': audio is not None,
    }


def _check_grounding(stage: dict, rows: list[dict]) -> dict:
    issues = []
    actors = _actors(stage)
    saw_feet = {actor_id: False for actor_id in actors}
    saw_wheel = False
    rover_expected = isinstance(stage.get('rover'), dict)
    for row in rows:
        frame = int(row['frame'])
        for actor_id, actor in sorted(_row_actors(row).items()):
            heights = [y for y in (_foot_y(actor, 'left'), _foot_y(actor, 'right')) if y is not None]
            if not heights:
                continue
            if actor_id in saw_feet:
                saw_feet[actor_id] = True
            stance = min(heights)
            clips = _clip_names(actor.get('clips'))
            airborne = any(c in {'jog', 'sprint', 'run', 'jump'} for c in clips)
            float_limit = 0.24 if airborne else FOOT_FLOAT_M
            if stance > float_limit + _EPS:
                issues.append(_issue('FAIL', frame, actor_id, 'stance foot floating at %.3f m' % stance))
            elif stance < FOOT_SINK_M - _EPS:
                issues.append(_issue('FAIL', frame, actor_id, 'stance foot sunk to %.3f m' % stance))
        wheel = _wheel_y(row.get('rover'))
        if wheel is not None:
            saw_wheel = True
            if wheel > WHEEL_FLOAT_M + _EPS:
                issues.append(_issue('FAIL', frame, 'rover', 'rover wheel floating at %.3f m' % wheel))
            elif wheel < WHEEL_SINK_M - _EPS:
                issues.append(_issue('FAIL', frame, 'rover', 'rover wheel sunk to %.3f m' % wheel))
    for actor_id, seen in sorted(saw_feet.items()):
        if not seen:
            issues.append(_issue('WARN', None, actor_id, 'actor %s has no foot telemetry' % actor_id))
    if rover_expected and not saw_wheel:
        issues.append(_issue('WARN', None, 'rover', 'rover has no wheel telemetry'))
    return _result('grounding', 'Grounding / Floating', issues, [HINT_GROUND])


def _check_foot_slide(rows: list[dict]) -> dict:
    history: dict[tuple[str, str], tuple[int, float, float]] = {}
    slides: dict[tuple[str, str], list[int]] = {}
    for row in rows:
        frame = int(row['frame'])
        for actor_id, actor in sorted(_row_actors(row).items()):
            moving = _is_locomotion(_clip_names(actor.get('clips')))
            for side in ('left', 'right'):
                key = (actor_id, side)
                if not moving:
                    history.pop(key, None)
                    continue
                height = _foot_y(actor, side)
                xz = _foot_xz(actor, side)
                if height is None or xz is None or height > PLANTED_Y_M + _EPS:
                    history.pop(key, None)
                    continue
                previous = history.get(key)
                history[key] = (frame, xz[0], xz[1])
                if previous is None:
                    continue
                delta_frames = frame - previous[0]
                if delta_frames <= 0:
                    continue
                distance = math.hypot(xz[0] - previous[1], xz[1] - previous[2]) / delta_frames
                if distance > SLIDE_M_PER_FRAME + _EPS:
                    slides.setdefault(key, []).append(frame)
    issues = []
    for (actor_id, side), frames in sorted(slides.items()):
        for run in _consecutive_runs(frames):
            sustained = len(run) >= SLIDE_SUSTAINED
            severity = 'FAIL' if sustained else 'WARN'
            label = 'sustained' if sustained else 'isolated'
            for frame in run:
                issues.append(_issue(severity, frame, actor_id, '%s %s-foot slide during walk/run' % (label, side)))
    return _result('foot_slide', 'Foot sliding', issues, [HINT_WALK])


def _check_props(stage: dict, rows: list[dict]) -> dict:
    specs = _held_props(stage)
    issues = []
    previous: dict[str, tuple[int, tuple[float, float, float] | None, bool]] = {}
    prop_ids = sorted(set(specs) | {prop_id for row in rows for prop_id in _row_props(row)})
    gap_fail = teleport_fail = owner_fail = False
    for row in rows:
        frame = int(row['frame'])
        actors = _row_actors(row)
        props = _row_props(row)
        for prop_id in prop_ids:
            record = props.get(prop_id)
            if not isinstance(record, dict):
                continue
            spec = specs.get(prop_id) or {}
            windows = _handoff_windows(spec, stage)
            in_window, window_gap = _window_at(frame, windows)
            expected = _expected_owner(spec, frame)
            parties = _owner_parties(spec)
            owners = _frame_owners(record)
            if len(owners) >= 2:
                owner_fail = True
                issues.append(_issue('FAIL', frame, prop_id, 'duplicate owner: %s' % ', '.join(owners)))
            elif not owners and expected:
                owner_fail = True
                issues.append(_issue('FAIL', frame, prop_id, 'missing owner (expected %s)' % expected))
            elif len(owners) == 1 and expected:
                allowed = parties if in_window and parties else {expected}
                if owners[0] not in allowed:
                    owner_fail = True
                    issues.append(_issue('FAIL', frame, prop_id, 'unexpected owner %s (expected %s)' % (owners[0], expected)))
            owner = owners[0] if len(owners) == 1 else None
            position = _vec3(record.get('position') or record.get('pos'))
            if owner:
                gap = _hand_gap(record, actors.get(owner), position)
                limit = max(HAND_GAP_M, window_gap) if in_window else HAND_GAP_M
                if gap is not None and gap > limit + _EPS:
                    gap_fail = True
                    issues.append(_issue('FAIL', frame, prop_id, 'prop hand gap %.3f m exceeds %.3f m' % (gap, limit)))
            prior = previous.get(prop_id)
            if prior is not None and position is not None and prior[1] is not None:
                distance = _distance(position, prior[1])
                if distance > PROP_TELEPORT_M + _EPS and not in_window and not prior[2]:
                    teleport_fail = True
                    issues.append(_issue('FAIL', frame, prop_id, 'prop teleported %.3f m' % distance))
            previous[prop_id] = (frame, position, in_window)
    hints = []
    if gap_fail:
        hints.append(HINT_GRIP)
    if teleport_fail:
        hints.append(HINT_HANDOFF)
    if owner_fail:
        hints.append(HINT_OWNER)
    return _result('prop_contact', 'Prop hand contact', issues, hints)


def _check_camera(stage: dict, rows: list[dict]) -> dict:
    room = _room_box(stage)
    corridor = _corridor_box(stage, room)
    issues = []
    saw = False
    for row in rows:
        camera = row.get('camera') if isinstance(row.get('camera'), dict) else None
        if camera is None:
            continue
        saw = True
        frame = int(row['frame'])
        position = _vec3(camera.get('position') if camera.get('position') is not None else camera.get('pos'))
        reported = _float_or_none(camera.get('wall_distance_m', camera.get('wall_distance')))
        outside = False
        computed = None
        if position is not None and room is not None:
            in_room = _inside(position, room)
            in_corridor = corridor is not None and _inside(position, corridor)
            outside = not in_room and not in_corridor
            if in_room:
                computed = _clearance(position, room)
            elif in_corridor and corridor is not None:
                computed = _clearance(position, corridor)
            else:
                computed = 0.0
        clearance = reported if reported is not None else computed
        if outside:
            issues.append(_issue('FAIL', frame, 'camera', 'camera is outside room bounds'))
        elif clearance is not None and clearance < CAMERA_CLEARANCE_M - _EPS:
            issues.append(_issue('FAIL', frame, 'camera', 'camera clearance %.3f m is under %.2f m' % (clearance, CAMERA_CLEARANCE_M)))
    if not saw:
        issues.append(_issue('WARN', None, 'camera', 'camera telemetry is missing'))
    elif room is None and any(isinstance(row.get('camera'), dict) and _vec3((row.get('camera') or {}).get('position')) is not None for row in rows):
        issues.append(_issue('WARN', None, 'camera', 'stage is missing room bounds'))
    return _result('camera_bounds', 'Camera wall clipping', issues, [HINT_CAMERA])


def _check_framing(rows: list[dict]) -> dict:
    issues = []
    saw = False
    for row in rows:
        camera = row.get('camera') if isinstance(row.get('camera'), dict) else None
        if camera is None:
            continue
        subject = camera.get('subject') if isinstance(camera.get('subject'), dict) else None
        if subject is None:
            continue
        saw = True
        frame = int(row['frame'])
        subject_id = str(subject.get('id') or 'subject')
        ndc_x, ndc_y = _ndc(subject)
        if ndc_x is None or ndc_y is None:
            issues.append(_issue('WARN', frame, subject_id, 'subject projection is missing'))
            continue
        if abs(ndc_x) > NDC_LIMIT + _EPS or abs(ndc_y) > NDC_LIMIT + _EPS:
            issues.append(_issue('FAIL', frame, subject_id, 'subject out of frame ndc=(%.3f, %.3f)' % (ndc_x, ndc_y)))
        if _truthy(subject.get('occluded')):
            issues.append(_issue('FAIL', frame, subject_id, 'subject is occluded'))
    if not saw:
        issues.append(_issue('WARN', None, 'subject', 'subject framing telemetry is missing'))
    return _result('subject_framing', 'Subject framing', issues, [HINT_WIDEN])


def _check_audio(stage: dict, video_path, probe: dict | None) -> dict:
    issues = []
    total = stage.get('total_frames')
    fps = float(stage.get('fps') or 0)
    expected = float(total) / fps if isinstance(total, (int, float)) and fps > 0 else None
    captions = stage.get('captions') if isinstance(stage.get('captions'), list) else []
    total_int = int(total) if isinstance(total, (int, float)) else None
    for index, caption in enumerate(captions, 1):
        if not isinstance(caption, dict):
            issues.append(_issue('FAIL', None, 'caption', 'caption %s is invalid' % index))
            continue
        start, end = caption.get('from'), caption.get('to')
        frame = int(start) if isinstance(start, (int, float)) and float(start).is_integer() else None
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            issues.append(_issue('FAIL', frame, 'caption', 'caption %s has no frame range' % index))
        elif total_int is None or start < 0 or end > total_int or end < start:
            issues.append(_issue('FAIL', frame, 'caption', 'caption %s falls outside [0, %s]' % (index, total_int)))
    details = {'expected_duration_s': None if expected is None else float(round(expected, 6)), 'video': 'not provided'}
    if video_path is not None or probe is not None:
        measured = probe
        if measured is None:
            path = Path(video_path)
            if not path.is_file():
                issues.append(_issue('FAIL', None, 'video', 'video file is missing'))
                measured = None
            else:
                try:
                    measured = probe_media(path)
                except (OSError, RuntimeError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
                    issues.append(_issue('FAIL', None, 'video', 'ffprobe failed: %s' % exc))
                    measured = None
        if isinstance(measured, dict):
            details['video'] = {
                'duration_s': measured.get('duration'),
                'width': measured.get('width'),
                'height': measured.get('height'),
                'fps': measured.get('fps'),
                'has_audio': bool(measured.get('has_audio')),
                'has_video': bool(measured.get('has_video')),
            }
            if not measured.get('has_video'):
                issues.append(_issue('FAIL', None, 'video', 'video stream is missing'))
            else:
                width, height = measured.get('width'), measured.get('height')
                if int(width or 0) != VIDEO_SIZE[0] or int(height or 0) != VIDEO_SIZE[1]:
                    issues.append(_issue('FAIL', None, 'video', 'video is %sx%s, expected %sx%s' % (width, height, VIDEO_SIZE[0], VIDEO_SIZE[1])))
                fps_value = measured.get('fps')
                if fps_value is None or abs(float(fps_value) - VIDEO_FPS) > 0.02:
                    issues.append(_issue('FAIL', None, 'video', 'video fps %s is not 24' % fps_value))
            if not measured.get('has_audio'):
                issues.append(_issue('FAIL', None, 'video', 'audio stream is missing'))
            duration = measured.get('duration')
            if expected is None or duration is None or abs(float(duration) - expected) > AUDIO_TOLERANCE_S + _EPS:
                issues.append(_issue(
                    'FAIL', None, 'video',
                    'duration %s s does not match timeline %s s' % (duration, None if expected is None else round(expected, 6)),
                ))
    result = _result('audio_sync', 'Audio and caption sync', issues, [HINT_AUDIO])
    result['details'] = details
    return result


def _check_exposure(review_dir) -> dict:
    issues = []
    if review_dir is None:
        return _result('exposure', 'Exposure and visual sanity', [_issue('WARN', None, 'stills', 'no stills directory')], [HINT_EXPOSURE])
    directory = Path(review_dir)
    if not directory.is_dir():
        return _result('exposure', 'Exposure and visual sanity', [_issue('WARN', None, 'stills', 'no stills directory')], [HINT_EXPOSURE])
    files = sorted(path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in _STILL_SUFFIXES)
    if not files:
        return _result('exposure', 'Exposure and visual sanity', [_issue('WARN', None, 'stills', 'no stills sampled')], [HINT_EXPOSURE])
    for index, path in enumerate(files):
        frame = _frame_from_name(path.name)
        if frame is None:
            frame = index
        try:
            stats = _luma_stats(path)
        except (OSError, UnidentifiedImageError, ValueError) as exc:
            issues.append(_issue('FAIL', frame, path.name, 'unreadable still: %s' % exc))
            continue
        if stats['mean'] < BLACK_MEAN_LUMA:
            issues.append(_issue('FAIL', frame, path.name, 'black frame mean_luma=%.2f' % stats['mean']))
        if stats['pct_over'] > BLOWN_PERCENT + _EPS:
            issues.append(_issue('FAIL', frame, path.name, 'blown-out frame pct_overexposed=%.2f' % stats['pct_over']))
        if stats['std'] < FLAT_STD_LUMA:
            issues.append(_issue('FAIL', frame, path.name, 'flat frame std_luma=%.2f' % stats['std']))
    return _result('exposure', 'Exposure and visual sanity', issues, [HINT_EXPOSURE])


def _check_animation(stage: dict, rows: list[dict], registry: dict | None) -> dict:
    issues = []
    clip_fail = False
    closeup_fail = False
    by_frame = {int(row['frame']): row for row in rows}
    for actor_id, actor in sorted(_actors(stage).items()):
        if not isinstance(actor, dict):
            continue
        animation = actor.get('animation') if isinstance(actor.get('animation'), dict) else {}
        fallbacks = animation.get('fallbacks') if isinstance(animation.get('fallbacks'), dict) else {}
        for clip, state in fallbacks.items():
            if state in ('', None) or str(state).lower() in {'missing', 'empty', 'fallback'}:
                clip_fail = True
                start = _scheduled_start(actor, str(clip))
                issues.append(_issue('FAIL', start, actor_id, 'required clip %s fell back to %s' % (clip, state or 'missing')))
        for clip in _clip_names(animation.get('missing_clips')):
            clip_fail = True
            issues.append(_issue('FAIL', _scheduled_start(actor, clip), actor_id, 'required clip %s fell back to missing' % clip))
        for clip in actor.get('clips') or []:
            if not isinstance(clip, dict):
                continue
            name = str(clip.get('clip') or clip.get('name') or '')
            start, end = clip.get('from'), clip.get('to')
            if not name or not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                continue
            for frame in range(int(start), int(end)):
                row = by_frame.get(frame)
                if row is None:
                    continue
                sample = _row_actors(row).get(actor_id)
                if not isinstance(sample, dict):
                    continue
                state = _bad_clip(sample, name)
                if state:
                    clip_fail = True
                    issues.append(_issue('FAIL', frame, actor_id, 'required clip %s fell back to %s' % (name, state)))
    for shot in stage.get('shots') or []:
        if not isinstance(shot, dict) or not _is_closeup(shot):
            continue
        subject = _shot_subject(shot, rows)
        if not subject:
            issues.append(_issue('WARN', _whole(shot.get('from')), shot.get('id') or 'closeup', 'close-up subject is unknown'))
            continue
        actor = _actors(stage).get(subject) or {}
        problem = _closeup_problem(_capabilities(actor, registry, subject))
        frames = _shot_frames(shot)
        if problem is None:
            continue
        severity, message = problem
        if severity == 'FAIL':
            closeup_fail = True
        for frame in frames:
            issues.append(_issue(severity, frame, subject, message))
    hints = []
    if clip_fail:
        hints.append(HINT_CLIP)
    if closeup_fail:
        hints.append(HINT_CLOSEUP)
    elif any(issue['severity'] == 'WARN' and 'capabilit' in issue['message'] for issue in issues):
        hints.append(HINT_CLOSEUP)
    return _result('animation_completeness', 'Animation and asset completeness', issues, hints)


def _capabilities(actor: dict, registry: dict | None, actor_id: str) -> dict:
    caps: dict = {}
    asset = _find_asset(registry, actor_id, actor.get('model') if isinstance(actor, dict) else None)
    if asset:
        caps.update(asset.get('capabilities') or {})
        if asset.get('tier'):
            caps.setdefault('tier', asset['tier'])
    inline = actor.get('capabilities') if isinstance(actor, dict) and isinstance(actor.get('capabilities'), dict) else {}
    for key, value in inline.items():
        if value is not None:
            caps[key] = value
    if isinstance(actor, dict) and actor.get('tier'):
        caps['tier'] = actor['tier']
    return caps


def _closeup_problem(caps: dict) -> tuple[str, str] | None:
    known = any(key in caps for key in ('tier', 'has_fingers', 'blendshapes', 'morph_targets'))
    if not known:
        return ('WARN', 'close-up subject capabilities are unknown')
    tier = caps.get('tier')
    fingers = caps.get('has_fingers')
    blend = caps.get('blendshapes')
    morphs = caps.get('morph_targets')
    if isinstance(blend, (list, tuple)):
        morphs = list(blend)
        blend = True
    missing = missing_facial_morphs([str(item) for item in morphs]) if isinstance(morphs, (list, tuple)) else []
    reasons = []
    if tier in {'TIER_B', 'TIER_C', 'REJECT'}:
        reasons.append('tier %s' % tier)
    if fingers is False:
        reasons.append('missing fingers')
    if blend is False or missing:
        reasons.append('missing blendshapes' + ((' ' + ', '.join(missing)) if missing else ''))
    explicit_pass = (
        tier == 'TIER_A' and fingers is not False and blend is not False and not missing
    ) or (
        fingers is True and blend is True and not missing
    )
    if explicit_pass and not reasons:
        return None
    if reasons:
        return ('FAIL', 'close-up capability failed: ' + '; '.join(reasons))
    return ('WARN', 'close-up subject capabilities are unknown')


def _bad_clip(actor: dict, scheduled: str) -> str | None:
    status = actor.get('clip_status') if isinstance(actor.get('clip_status'), dict) else {}
    state = str(status.get(scheduled, '')).lower()
    if state in {'missing', 'empty', 'fallback'}:
        return state
    key = scheduled.lower()
    for name in _clip_names(actor.get('missing_clips')):
        if name.lower() == key:
            return 'missing'
    for name in _clip_names(actor.get('clip_fallbacks')):
        if name.lower() == key:
            return 'fallback'
    for name in _clip_names(actor.get('clips')):
        lowered = name.lower()
        if lowered in {'missing', 'empty', 'fallback'}:
            return lowered
        for prefix in ('missing:', 'empty:', 'fallback:'):
            if lowered.startswith(prefix) and lowered.split(':', 1)[1] in {key, ''}:
                return prefix[:-1]
    if actor.get('clips') is not None and not _clip_names(actor.get('clips')):
        return 'empty'
    return None


def _result(check_id: str, name: str, issues: list[dict], hints: list[str]) -> dict:
    issues = sorted(issues, key=lambda item: (item.get('frame') is None, item.get('frame') if item.get('frame') is not None else 0, item.get('subject') or '', item.get('message') or ''))
    if any(item['severity'] == 'FAIL' for item in issues):
        status = 'FAIL'
    elif any(item['severity'] == 'WARN' for item in issues):
        status = 'WARN'
    else:
        status = 'PASS'
    frames = sorted({int(item['frame']) for item in issues if isinstance(item.get('frame'), int) and not isinstance(item.get('frame'), bool)})
    return {
        'id': check_id,
        'name': name,
        'status': status,
        'frames': frames,
        'issues': issues,
        'replan_hints': [] if status == 'PASS' else list(dict.fromkeys(hints)),
    }


def _overall(checks: list[dict]) -> tuple[str, list[str]]:
    rank = {'PASS': 0, 'WARN': 1, 'FAIL': 2}
    status = 'PASS'
    suggestions = []
    for check in checks:
        if rank[check['status']] > rank[status]:
            status = check['status']
        if check['status'] == 'PASS':
            continue
        for hint in check['replan_hints']:
            if hint not in suggestions:
                suggestions.append(hint)
    return status, suggestions


def _issue(severity: str, frame: int | None, subject: str, message: str) -> dict:
    return {'severity': severity, 'frame': frame, 'subject': subject, 'message': message}


def _frame_rows(telemetry) -> list[dict]:
    if isinstance(telemetry, list):
        raw = telemetry
    elif isinstance(telemetry, dict):
        raw = telemetry.get('frames', [])
    else:
        raw = []
    if isinstance(raw, dict):
        rows = []
        for key, value in raw.items():
            if isinstance(value, dict):
                row = dict(value)
                row.setdefault('frame', int(key))
                rows.append(row)
        raw = rows
    indexed = {}
    for row in raw:
        if isinstance(row, dict):
            indexed[int(row.get('frame', 0))] = row
    return [indexed[key] for key in sorted(indexed)]


def _actors(stage: dict) -> dict:
    actors = stage.get('actors')
    return actors if isinstance(actors, dict) else {}


def _row_actors(row: dict) -> dict:
    actors = row.get('actors')
    return {str(key): value for key, value in actors.items() if isinstance(value, dict)} if isinstance(actors, dict) else {}


def _row_props(row: dict) -> dict:
    props = row.get('props')
    return {str(key): value for key, value in props.items() if isinstance(value, dict)} if isinstance(props, dict) else {}


def _held_props(stage: dict) -> dict:
    raw = stage.get('props')
    if isinstance(raw, dict):
        return {str(key): value for key, value in raw.items() if isinstance(value, dict)}
    found = {}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get('id') and (item.get('owners') or item.get('owner')):
                found[str(item['id'])] = item
    return found


def _vec3(value) -> tuple[float, float, float] | None:
    if isinstance(value, dict):
        if 'pos' in value:
            return _vec3(value.get('pos'))
        try:
            return (float(value['x']), float(value['y']), float(value['z']))
        except (KeyError, TypeError, ValueError):
            return None
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            return (float(value[0]), float(value[1]), float(value[2]))
        except (TypeError, ValueError):
            return None
    return None


def _foot_y(actor: dict, side: str) -> float | None:
    direct = actor.get('%s_foot_y' % side)
    if direct is not None:
        try:
            return float(direct)
        except (TypeError, ValueError):
            return None
    position = _vec3(actor.get('%s_foot' % side))
    return None if position is None else position[1]


def _foot_xz(actor: dict, side: str) -> tuple[float, float] | None:
    position = _vec3(actor.get('%s_foot' % side))
    if position is None:
        return None
    return (position[0], position[2])


def _wheel_y(rover) -> float | None:
    if not isinstance(rover, dict):
        return None
    if rover.get('wheel_min_y') is not None:
        return _float_or_none(rover.get('wheel_min_y'))
    heights = []
    for wheel in rover.get('wheels') or []:
        position = _vec3(wheel)
        if position is not None:
            heights.append(position[1])
    return min(heights) if heights else None


def _clip_names(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    names = []
    for item in value:
        if isinstance(item, str) and item:
            names.append(item)
        elif isinstance(item, dict):
            name = item.get('clip') or item.get('name') or item.get('id')
            if name:
                names.append(str(name))
    return names


def _is_locomotion(names: list[str]) -> bool:
    return any('walk' in name.lower() or 'run' in name.lower() for name in names)


def _consecutive_runs(frames: list[int]) -> list[list[int]]:
    ordered = sorted(set(frames))
    runs: list[list[int]] = []
    current: list[int] = []
    for frame in ordered:
        if not current or frame == current[-1] + 1:
            current.append(frame)
        else:
            runs.append(current)
            current = [frame]
    if current:
        runs.append(current)
    return runs


def _frame_owners(record: dict) -> list[str]:
    if isinstance(record.get('owner'), list) or (record.get('owner') is None and isinstance(record.get('owners'), list)):
        source = record.get('owner') if record.get('owner') is not None else record.get('owners')
        return _owner_list(source)
    owners = _owner_list(record.get('owner'))
    if isinstance(record.get('owners'), list) and any(not isinstance(item, dict) or 'from' not in item for item in record['owners']):
        owners.extend(owner for owner in _owner_list(record.get('owners')) if owner not in owners)
    return owners


def _owner_list(value) -> list[str]:
    if value is None or value == '':
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        actor = value.get('actor') or value.get('id')
        return [str(actor)] if actor else []
    if isinstance(value, list):
        owners = []
        for item in value:
            for owner in _owner_list(item):
                if owner not in owners:
                    owners.append(owner)
        return owners
    return []


def _expected_owner(spec: dict, frame: int) -> str | None:
    current = None
    entries = [entry for entry in (spec.get('owners') or []) if isinstance(entry, dict)]
    for entry in sorted(entries, key=lambda item: int(item.get('from', 0))):
        if int(entry.get('from', 0)) <= frame and entry.get('actor'):
            current = str(entry['actor'])
    if current is None and spec.get('owner'):
        owners = _owner_list(spec.get('owner'))
        current = owners[0] if len(owners) == 1 else None
    return current


def _owner_parties(spec: dict) -> set[str]:
    parties = set()
    for entry in spec.get('owners') or []:
        if isinstance(entry, dict) and entry.get('actor'):
            parties.add(str(entry['actor']))
    return parties


def _handoff_windows(spec: dict, stage: dict) -> list[tuple[int, int, float]]:
    windows = []
    for entry in spec.get('owners') or []:
        if not isinstance(entry, dict):
            continue
        window = entry.get('handoff_window')
        if isinstance(window, (list, tuple)) and len(window) >= 2:
            windows.append((int(window[0]), int(window[1]), float(entry.get('max_gap') or HANDOFF_GAP_M)))
    meta = stage.get('meta') if isinstance(stage.get('meta'), dict) else {}
    window = meta.get('handoff_window')
    if isinstance(window, (list, tuple)) and len(window) >= 2:
        windows.append((int(window[0]), int(window[1]), HANDOFF_GAP_M))
    return windows


def _window_at(frame: int, windows: list[tuple[int, int, float]]) -> tuple[bool, float]:
    active = [gap for start, end, gap in windows if start <= frame <= end]
    if not active:
        return False, HAND_GAP_M
    return True, max(active)


def _hand_gap(record: dict, actor: dict | None, position) -> float | None:
    if record.get('hand_gap_m') is not None:
        return _float_or_none(record.get('hand_gap_m'))
    if actor is None or position is None:
        return None
    hands = actor.get('hands') if isinstance(actor.get('hands'), dict) else {}
    points = [point for point in (_vec3(value) for value in hands.values()) if point is not None]
    if not points:
        return None
    return min(_distance(position, point) for point in points)


def _distance(a, b) -> float:
    return math.sqrt(sum((float(a[index]) - float(b[index])) ** 2 for index in range(3)))


def _room_box(stage: dict):
    box = stage.get('room_bounds') if isinstance(stage.get('room_bounds'), dict) else None
    if box is None:
        sett = stage.get('set') if isinstance(stage.get('set'), dict) else {}
        box = sett.get('room') if isinstance(sett.get('room'), dict) else None
    if not isinstance(box, dict):
        return None
    low, high = _vec3(box.get('min')), _vec3(box.get('max'))
    if low is None or high is None:
        return None
    return low, high


def _corridor_box(stage: dict, room):
    if room is None:
        return None
    sett = stage.get('set') if isinstance(stage.get('set'), dict) else {}
    door = stage.get('door') if isinstance(stage.get('door'), dict) else {}
    if not door and isinstance(sett.get('door'), dict):
        door = sett['door']
    bounds = sett.get('camera_bounds') if isinstance(sett.get('camera_bounds'), dict) else {}
    if not door and bounds.get('corridor_max_z') is None and stage.get('corridor_max_z') is None:
        return None
    low, high = room
    door_x = float(door.get('x', 0.0) or 0.0)
    half = float(door.get('width', 1.6) or 1.6) / 2.0
    height = float(door.get('height', high[1]) or high[1])
    far = bounds.get('corridor_max_z', stage.get('corridor_max_z'))
    if far is None and door.get('corridor_depth') is not None:
        far = high[2] + float(door['corridor_depth'])
    if far is None:
        return None
    far = float(far)
    if far >= high[2] - _EPS:
        z0, z1 = high[2], far
    elif far <= low[2] + _EPS:
        z0, z1 = far, low[2]
    else:
        return None
    return (door_x - half, low[1], z0), (door_x + half, max(height, low[1]), z1)


def _inside(point, box) -> bool:
    low, high = box
    return all(low[index] - _EPS <= point[index] <= high[index] + _EPS for index in range(3))


def _clearance(point, box) -> float:
    low, high = box
    return min(
        point[0] - low[0], high[0] - point[0],
        point[1] - low[1], high[1] - point[1],
        point[2] - low[2], high[2] - point[2],
    )


def _ndc(subject: dict) -> tuple[float | None, float | None]:
    if subject.get('ndc_x') is not None or subject.get('ndc_y') is not None:
        return _float_or_none(subject.get('ndc_x')), _float_or_none(subject.get('ndc_y'))
    ndc = subject.get('ndc')
    if isinstance(ndc, (list, tuple)) and len(ndc) >= 2:
        return _float_or_none(ndc[0]), _float_or_none(ndc[1])
    return None, None


def _is_closeup(shot: dict) -> bool:
    kind = str(shot.get('type') or shot.get('shot_type') or '').lower().replace('-', '').replace('_', '')
    return kind in _CLOSEUP_TYPES or ('close' in kind and 'up' in kind)


def _shot_subject(shot: dict, rows: list[dict]) -> str | None:
    for key in ('subject', 'actor', 'focus'):
        if isinstance(shot.get(key), str) and shot[key]:
            return shot[key]
    for camera in shot.get('cameras') or []:
        if isinstance(camera, dict) and camera.get('subject'):
            return str(camera['subject'])
        if isinstance(camera, dict) and camera.get('actor'):
            return str(camera['actor'])
    frames = set(_shot_frames(shot))
    for row in rows:
        if int(row['frame']) not in frames:
            continue
        camera = row.get('camera') if isinstance(row.get('camera'), dict) else {}
        subject = camera.get('subject') if isinstance(camera.get('subject'), dict) else {}
        if subject.get('id'):
            return str(subject['id'])
    return None


def _shot_frames(shot: dict) -> list[int]:
    start, count = shot.get('from'), shot.get('frames')
    if not isinstance(start, (int, float)) or not isinstance(count, (int, float)):
        return []
    return list(range(int(start), int(start) + int(count)))


def _scheduled_start(actor: dict, clip: str) -> int:
    for item in actor.get('clips') or []:
        if isinstance(item, dict) and str(item.get('clip') or item.get('name') or '') == clip and isinstance(item.get('from'), (int, float)):
            return int(item['from'])
    return 0


def _whole(value) -> int | None:
    if isinstance(value, (int, float)) and float(value).is_integer():
        return int(value)
    return None


def _find_asset(registry: dict | None, actor_id: str, model) -> dict | None:
    if not isinstance(registry, dict):
        return None
    assets = registry.get('assets') if isinstance(registry.get('assets'), list) else []
    model_name = Path(str(model)).name if model else ''
    for asset in assets:
        if isinstance(asset, dict) and asset.get('asset_id') == actor_id:
            return asset
    for asset in assets:
        if isinstance(asset, dict) and model_name and Path(str(asset.get('path') or '')).name == model_name:
            return asset
    return None


def _load_registry(registry):
    if registry is None or isinstance(registry, dict):
        return registry
    path = Path(registry)
    if not path.is_file():
        return None
    document = json.loads(path.read_text(encoding='utf-8'))
    return document if isinstance(document, dict) else None


def _luma_stats(path: Path) -> dict:
    with Image.open(path) as image:
        rgb = np.asarray(image.convert('RGB'), dtype=np.float32)
    if rgb.size == 0 or rgb.ndim != 3:
        raise ValueError('empty image')
    luma = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    return {
        'mean': float(luma.mean()),
        'std': float(luma.std()),
        'pct_over': float((luma >= OVEREXPOSED_LUMA).mean() * 100.0),
    }


def _frame_from_name(name: str) -> int | None:
    match = re.search(r'frame[_-]?(\d+)', name, re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.search(r'(\d+)', name)
    return int(match.group(1)) if match else None


def _float_or_none(value):
    try:
        if value is None or isinstance(value, bool):
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _parse_rate(value):
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value)
    if '/' in text:
        num, den = text.split('/', 1)
        try:
            den_value = float(den)
            if den_value == 0:
                return None
            return float(num) / den_value
        except ValueError:
            return None
    return _float_or_none(text)


def _truthy(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes'}
    return bool(value)


def _write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Run deterministic and still-frame QC for a 3D episode.')
    parser.add_argument('--stage', required=True, type=Path)
    parser.add_argument('--telemetry', required=True, type=Path)
    parser.add_argument('--video', type=Path, default=None)
    parser.add_argument('--stills', type=Path, default=None)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--registry', type=Path, default=None)
    args = parser.parse_args(argv)
    stage = json.loads(args.stage.read_text(encoding='utf-8'))
    telemetry = json.loads(args.telemetry.read_text(encoding='utf-8'))
    report = run_qc(stage, telemetry, args.video, args.stills, registry=args.registry)
    _write_report(args.out, report)
    print('QC %s -> %s' % (report['status'], args.out))
    return {'PASS': 0, 'WARN': 2, 'FAIL': 1}[report['status']]


if __name__ == '__main__':
    sys.exit(main())
