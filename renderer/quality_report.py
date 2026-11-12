#!/usr/bin/env python3
"""Create a persistent technical/storyboard QA report for a rendered episode."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import math
from collections import defaultdict
from pathlib import Path


def command(args: list[str]) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()


def stage_layout(shot: dict) -> dict | None:
    cartoon = shot.get('cartoon') if isinstance(shot.get('cartoon'), dict) else {}
    value = shot.get('stage_layout') or cartoon.get('stage_layout')
    return value if isinstance(value, dict) else None


def inspect_layout(shot: dict) -> list[str]:
    issues: list[str] = []
    layout = stage_layout(shot)
    if not layout:
        return ['missing stage_layout']
    if layout.get('version') != 1:
        issues.append('unsupported stage_layout version')
    space = layout.get('coordinate_space') or {}
    if space.get('width') != 320 or space.get('height') != 180:
        issues.append('unexpected coordinate space')
    floor = layout.get('floor') or {}
    far_y, near_y = floor.get('far_y'), floor.get('near_y')
    if not isinstance(far_y, (int, float)) or not isinstance(near_y, (int, float)) or not 115 <= far_y < near_y <= 170:
        issues.append('invalid floor projection')
    dimensions = layout.get('dimensions') or {}
    if not 44 <= dimensions.get('actor_height', 0) <= 90:
        issues.append('invalid actor scale')
    if not 28 <= dimensions.get('rover_width', 0) <= 100 or not 18 <= dimensions.get('rover_height', 0) <= 65:
        issues.append('invalid rover scale')
    anchors = list((layout.get('actors') or {}).values()) + [layout.get('rover') or {}]
    for anchor in anchors:
        if not 0 <= anchor.get('x', -1) <= 1 or not 0 <= anchor.get('depth', -1) <= 1:
            issues.append('anchor is outside stage')
            break
        if anchor.get('surface') not in ('floor', 'test_platform'):
            issues.append('unknown support surface')
            break
    event = (shot.get('cartoon') or {}).get('action_event') if isinstance(shot.get('cartoon'), dict) else None
    if event:
        start, contact, end = event.get('start'), event.get('contact'), event.get('end')
        if not all(isinstance(x, (int, float)) and 0 <= x <= 1 for x in (start, contact, end)) or not start <= contact <= end:
            issues.append('invalid action_event timing')
    return issues


def validate_props(props: dict) -> tuple[list[str], dict]:
    issues: list[str] = []
    shots = props.get('shots') if isinstance(props.get('shots'), list) else []
    per_scene: dict[int, dict[str, object]] = defaultdict(lambda: {'focus': set(), 'events': 0, 'shots': 0})
    pixel_shots = 0
    for index, shot in enumerate(shots, 1):
        if not shot.get('pixel_story'):
            continue
        pixel_shots += 1
        scene_number = int(shot.get('scene_number') or 0)
        state = per_scene[scene_number]
        state['shots'] = int(state['shots']) + 1
        cartoon = shot.get('cartoon') if isinstance(shot.get('cartoon'), dict) else {}
        if cartoon.get('focus'):
            state['focus'].add(cartoon['focus'])  # type: ignore[union-attr]
        if cartoon.get('action_event'):
            state['events'] = int(state['events']) + 1
        for issue in inspect_layout(shot):
            issues.append(f'shot {index} ({scene_number}): {issue}')
    if pixel_shots and pixel_shots != len(shots):
        # A scientific serial must not silently switch renderers half way through.
        issues.append(f'mixed renderer modes in one episode ({pixel_shots}/{len(shots)} pixel-story shots)')
    for scene, state in sorted(per_scene.items()):
        if state['shots'] >= 3 and len(state['focus']) < 2:
            issues.append(f'scene {scene}: fewer than two camera compositions')
        if state['shots'] >= 3 and int(state['events']) < 2:
            issues.append(f'scene {scene}: fewer than two visible action events')
    return issues, {'shot_count': len(shots), 'pixel_story_shot_count': pixel_shots,
                    'scene_count': len(per_scene), 'scenes': {
                        str(scene): {'shot_count': state['shots'], 'focuses': sorted(state['focus']),
                                     'action_event_count': state['events']}
                        for scene, state in per_scene.items()}}


def probe(path: Path) -> dict:
    raw = command(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)])
    return json.loads(raw)


def extract_contact_sheet(video: Path, props: dict, directory: Path) -> tuple[list[str], int]:
    shots = props.get('shots') if isinstance(props.get('shots'), list) else []
    fps = float(props.get('fps') or 24)
    frames: list[tuple[str, int]] = []
    for index, shot in enumerate(shots, 1):
        start = max(0, int(shot.get('from') or 0))
        duration = max(1, int(shot.get('duration') or 1))
        frame = start + duration // 2
        seconds = frame / fps
        name = f"scene-{int(shot.get('scene_number') or 0):02d}-shot-{index:03d}.jpg"
        target = directory / name
        command(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-ss', f'{seconds:.3f}', '-i', str(video),
                 '-frames:v', '1', '-vf', 'scale=320:180', '-q:v', '5', str(target)])
        frames.append((name, frame))
    if frames:
        grid = f'tile=4x{(len(frames) + 3) // 4}:padding=4:margin=4:color=black'
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-pattern_type', 'glob', '-i',
                        str(directory / 'scene-*.jpg'), '-vf', grid, '-frames:v', '1', str(directory / 'contact.jpg')],
                       check=True, capture_output=True)
    hashes = [hashlib.sha256((directory / name).read_bytes()).hexdigest() for name, _ in frames]
    duplicate_count = len(hashes) - len(set(hashes))
    return [name for name, _ in frames], duplicate_count


def extract_action_evidence(video: Path, props: dict, directory: Path) -> tuple[list[dict], str | None]:
    """Save before/contact/after frames and motion evidence for planned actions."""
    from PIL import Image, ImageChops, ImageStat

    directory.mkdir(parents=True, exist_ok=True)
    fps = float(props.get('fps') or 24)
    evidence: list[dict] = []
    files: list[Path] = []
    for index, shot in enumerate(props.get('shots') or [], 1):
        if not shot.get('pixel_story'):
            continue
        cartoon = shot.get('cartoon') if isinstance(shot.get('cartoon'), dict) else {}
        event = cartoon.get('action_event') if isinstance(cartoon, dict) else None
        if not isinstance(event, dict):
            continue
        start_frame = max(0, int(shot.get('from') or 0))
        duration = max(1, int(shot.get('duration') or 1))
        samples = []
        for label, fraction in (('before', event.get('start')), ('contact', event.get('contact')), ('after', event.get('end'))):
            relative = max(0.0, min(1.0, float(fraction))) if isinstance(fraction, (int, float)) else .5
            frame = min(start_frame + duration - 1, start_frame + int(duration * relative))
            seconds = frame / fps
            name = f"action-shot-{index:03d}-{label}.jpg"
            target = directory / name
            command(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-ss', f'{seconds:.3f}', '-i', str(video),
                     '-frames:v', '1', '-vf', 'scale=128:72', '-q:v', '5', str(target)])
            samples.append(target)
            files.append(target)
        with Image.open(samples[0]) as first, Image.open(samples[-1]) as last:
            height = min(first.height, last.height)
            width = min(first.width, last.width)
            # Ignore the lower subtitle band so text changes do not masquerade
            # as character or prop movement.
            crop_height = max(1, int(height * .82))
            a = first.convert('RGB').crop((0, 0, width, crop_height))
            b = last.convert('RGB').crop((0, 0, width, crop_height))
            channels = ImageStat.Stat(ImageChops.difference(a, b)).mean
            difference = sum(channels) / (3 * 255)
        evidence.append({
            'shot_index': index,
            'scene_number': int(shot.get('scene_number') or 0),
            'action_kind': event.get('kind'),
            'start_contact_end': [event.get('start'), event.get('contact'), event.get('end')],
            'mean_frame_difference': round(difference, 4),
            'frames': [path.name for path in samples],
        })
    if not files:
        return evidence, None
    columns = 12
    rows = math.ceil(len(files) / columns)
    sheet = directory / 'action-contact.jpg'
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-pattern_type', 'glob', '-i',
                    str(directory / 'action-shot-*.jpg'), '-vf', f'tile={columns}x{rows}:padding=4:margin=4:color=black',
                    '-frames:v', '1', str(sheet)], check=True, capture_output=True)
    return evidence, sheet.name


def run(manifest_path: Path, props_path: Path, video: Path, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    props = json.loads(props_path.read_text(encoding='utf-8'))
    issues, storyboard = validate_props(props)
    if not video.is_file() or video.stat().st_size < 100_000:
        issues.append('rendered MP4 is missing or unexpectedly small')
        media = {}
    else:
        try:
            media = probe(video)
        except FileNotFoundError:
            issues.append('ffprobe is not installed; technical media checks could not run')
            media = {}
        except subprocess.CalledProcessError:
            issues.append('ffprobe could not read the rendered MP4')
            media = {}
    if media:
        streams = media.get('streams') or []
        videos = [stream for stream in streams if stream.get('codec_type') == 'video']
        audios = [stream for stream in streams if stream.get('codec_type') == 'audio']
        if not videos:
            issues.append('MP4 has no video stream')
        else:
            stream = videos[0]
            if (stream.get('width'), stream.get('height')) != (int(props.get('width', 1280)), int(props.get('height', 720))):
                issues.append('video dimensions do not match the requested composition')
            duration = float(media.get('format', {}).get('duration') or 0)
            expected = float(props.get('durationInFrames') or 0) / float(props.get('fps') or 24)
            if expected and abs(duration - expected) > 1.0:
                issues.append(f'video duration mismatch ({duration:.2f}s vs {expected:.2f}s)')
        if not audios:
            issues.append('MP4 has no audio stream')
    frame_names: list[str] = []
    duplicate_count = 0
    action_evidence: list[dict] = []
    action_contact_file: str | None = None
    if video.is_file() and storyboard['shot_count']:
        try:
            frame_names, duplicate_count = extract_contact_sheet(video, props, out_dir)
            if duplicate_count > max(2, len(frame_names) // 3):
                issues.append(f'too many exact duplicate shot frames ({duplicate_count}/{len(frame_names)})')
        except Exception as exc:
            issues.append(f'failed to extract storyboard frames ({type(exc).__name__})')
        try:
            action_evidence, action_contact_file = extract_action_evidence(video, props, out_dir)
        except Exception as exc:
            issues.append(f'failed to extract action evidence ({type(exc).__name__})')
    report = {
        'version': 1,
        'result': 'failed' if issues else 'technical_pass_visual_review_required',
        'issues': issues,
        'manifest_key': manifest.get('key'),
        'episode_title': manifest.get('title'),
        'media': {'bytes': video.stat().st_size if video.is_file() else 0,
                  'duration_seconds': float(media.get('format', {}).get('duration') or 0) if media else 0,
                  'video_codec': next((s.get('codec_name') for s in media.get('streams', []) if s.get('codec_type') == 'video'), None) if media else None,
                  'audio_codec': next((s.get('codec_name') for s in media.get('streams', []) if s.get('codec_type') == 'audio'), None) if media else None},
        'storyboard': storyboard,
        'exact_duplicate_center_frames': duplicate_count,
        'frame_files': frame_names,
        'action_evidence': action_evidence,
        'action_contact_file': action_contact_file,
        'visual_semantic_review': 'not_run',
    }
    (out_dir / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if issues:
        Path('qa-error.txt').write_text('\n'.join(issues[:20]), encoding='utf-8')
    return report


if __name__ == '__main__':
    if len(sys.argv) != 5:
        raise SystemExit('usage: quality_report.py manifest.json remotion-props.json output.mp4 qa/')
    result = run(*(Path(value) for value in sys.argv[1:]))
    print(json.dumps({k: result[k] for k in ('result', 'issues', 'media', 'storyboard')}, ensure_ascii=False))
    raise SystemExit(1 if result['issues'] else 0)
