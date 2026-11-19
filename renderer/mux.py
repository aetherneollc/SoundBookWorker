#!/usr/bin/env python3
"""Mix a silent Godot movie with the ``stage.json`` audio bed into an H.264 MP4.

    python renderer/mux.py take.avi stage.json output.mp4

The picture is re-encoded (``libx264``, medium, CRF 20, yuv420p, faststart) and
the sound is AAC at 48 kHz. Output length follows the video frame count. A
stage with no audio tracks still produces a playable MP4 with a silent bed.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path


VIDEO_ENCODE = [
    '-c:v', 'libx264', '-preset', 'medium', '-crf', '20', '-pix_fmt', 'yuv420p',
    '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2',
    '-movflags', '+faststart',
]


class MixPlan:
    """ffmpeg inputs (the video is already input 0) and the audio filter graph."""

    def __init__(self, input_args: list[str], filter_complex: str, duration: float):
        self.input_args = input_args
        self.filter_complex = filter_complex
        self.duration = duration


def ffmpeg_executable() -> str:
    """Prefer a ``ffmpeg`` on ``PATH``, then the binary shipped with imageio-ffmpeg."""
    found = shutil.which('ffmpeg')
    if found:
        return found
    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise RuntimeError('ffmpeg not found on PATH and imageio-ffmpeg is not installed') from exc
    binary = imageio_ffmpeg.get_ffmpeg_exe()
    if not binary:
        raise RuntimeError('imageio-ffmpeg did not provide an ffmpeg binary')
    return binary


def _format_seconds(value: float) -> str:
    return f'{max(0.0, value):.6f}'


def _clock_to_seconds(hours: str, minutes: str, seconds: str) -> float:
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def probe_loop_samples(path: Path, ffmpeg: str) -> int:
    """Sample frames in one channel, which is what ``aloop``'s ``size`` counts."""
    if path.suffix.lower() == '.wav':
        try:
            with wave.open(str(path), 'rb') as handle:
                return max(1, handle.getnframes())
        except wave.Error:
            pass
    proc = subprocess.run(
        [ffmpeg, '-hide_banner', '-nostdin', '-i', str(path)],
        text=True, encoding='utf-8', errors='replace',
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    rate = re.search(r'(\d+)\s+Hz', proc.stderr)
    duration = re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)', proc.stderr)
    if rate and duration:
        seconds = _clock_to_seconds(*duration.groups())
        return max(1, int(round(seconds * int(rate.group(1)))))
    return 48000


def probe_video_duration(ffmpeg: str, path: Path, fallback_seconds: float = 0.0) -> float:
    """Duration snapped to the video frame count, so the MP4 matches the movie."""
    proc = subprocess.run(
        [ffmpeg, '-hide_banner', '-nostdin', '-i', str(path)],
        text=True, encoding='utf-8', errors='replace',
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    text = proc.stderr or ''
    duration_match = re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)', text)
    fps_match = re.search(r'(\d+(?:\.\d+)?)\s+fps', text)
    if duration_match and fps_match:
        seconds = _clock_to_seconds(*duration_match.groups())
        fps = float(fps_match.group(1))
        if seconds > 0 and fps > 0:
            frames = max(1, round(seconds * fps))
            return frames / fps
    if duration_match:
        seconds = _clock_to_seconds(*duration_match.groups())
        if seconds > 0:
            return seconds
    if fallback_seconds > 0:
        return fallback_seconds
    tail = text[-800:]
    raise RuntimeError(f'Unable to read video duration from {path.name}: {tail}')


def _track_chain(index: int, track: dict, duration: float, label: str) -> str:
    delay_ms = max(0, int(track.get('start_ms') or 0))
    volume = max(0.0, min(4.0, float(track.get('volume', 1.0))))
    remain = max(0.001, duration - delay_ms / 1000.0)
    # aloop's size is in source samples, so it has to run before the 48 kHz resample.
    if track.get('loop'):
        samples = max(1, int(track.get('loop_samples') or 48000))
        steps = [f'[{index}:a]aloop=loop=-1:size={samples}']
    else:
        steps = [f'[{index}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo']
    if track.get('loop'):
        steps.append('aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo')
    steps.append(f'atrim=0:{_format_seconds(remain)}')
    steps.append('asetpts=PTS-STARTPTS')
    steps.append(f'apad=whole_dur={_format_seconds(remain)}')
    if delay_ms:
        steps.append(f'adelay={delay_ms}:all=1')
    steps.append(f'volume={volume:.3f}')
    return ','.join(steps) + f'[{label}]'


def _finish(chain_labels: list[str], duration: float) -> str:
    duration_text = _format_seconds(duration)
    tail = (
        f'alimiter=limit=0.95,atrim=0:{duration_text},'
        f'apad=whole_dur={duration_text},aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[aout]'
    )
    if len(chain_labels) == 1:
        # The single chain was already labelled; rename by running it through anull-free trim.
        return f'{chain_labels[0]}{tail}'
    mix = (
        f"{''.join(chain_labels)}amix=inputs={len(chain_labels)}:"
        f'duration=longest:dropout_transition=0:normalize=0,{tail}'
    )
    return mix


def build_mix_plan(tracks: list[dict], duration: float) -> MixPlan:
    """Build the audio graph for ``tracks`` over exactly ``duration`` seconds.

    ``tracks`` items use ``file``, ``start_ms``, ``volume``, and ``loop``.
    A looping track may also carry ``loop_samples`` (one channel of the bed).
    An empty list yields a silent stereo bed, not an error.
    """
    if duration <= 0:
        raise ValueError('video duration must be positive')
    duration_text = _format_seconds(duration)
    if not tracks:
        return MixPlan(
            ['-f', 'lavfi', '-t', duration_text, '-i', 'anullsrc=r=48000:cl=stereo'],
            f'[1:a]atrim=0:{duration_text},asetpts=PTS-STARTPTS[aout]',
            duration,
        )
    input_args: list[str] = []
    chains: list[str] = []
    labels: list[str] = []
    for offset, track in enumerate(tracks, start=1):
        file_name = str(track.get('file') or track.get('src') or '')
        if not file_name:
            raise ValueError('audio track is missing a file')
        input_args.extend(['-i', file_name])
        label = 'aout' if len(tracks) == 1 else f'a{offset}'
        # Single-track graphs finish in one chain so the label is already [aout].
        if len(tracks) == 1:
            body = _track_chain(offset, track, duration, 'src')
            chains.append(body)
            labels.append('[src]')
        else:
            chains.append(_track_chain(offset, track, duration, label))
            labels.append(f'[{label}]')
    if len(tracks) == 1:
        filter_complex = chains[0] + ';' + _finish(labels, duration)
    else:
        filter_complex = ';'.join(chains + [_finish(labels, duration)])
    return MixPlan(input_args, filter_complex, duration)


def build_ffmpeg_command(ffmpeg: str, video: Path, plan: MixPlan, output: Path) -> list[str]:
    return [
        ffmpeg, '-y', '-nostdin', '-i', str(video),
        *plan.input_args,
        '-filter_complex', plan.filter_complex,
        '-map', '0:v:0', '-map', '[aout]',
        *VIDEO_ENCODE,
        '-t', _format_seconds(plan.duration),
        str(output),
    ]


def _resolve_audio(track: dict, stage_path: Path) -> Path | None:
    raw = track.get('file') or track.get('src')
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = Path(raw)
    if path.is_file():
        return path
    beside = stage_path.parent / path
    if beside.is_file():
        return beside
    return path


def _stage_fallback_seconds(stage: dict) -> float:
    fps = float(stage.get('fps') or 0)
    frames = float(stage.get('total_frames') or 0)
    if fps > 0 and frames > 0:
        return frames / fps
    return 0.0


def _run(command: list[str]) -> None:
    proc = subprocess.run(
        command, text=True, encoding='utf-8', errors='replace',
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or '')[-4000:]
        raise RuntimeError(f'ffmpeg exited {proc.returncode}:\n{detail}')


def mux(video: Path, stage_json: Path, output: Path, ffmpeg: str | None = None) -> None:
    """Encode ``video`` plus the stage audio bed to ``output``."""
    video = Path(video)
    stage_json = Path(stage_json)
    output = Path(output)
    if not video.is_file():
        raise FileNotFoundError(video)
    stage = json.loads(stage_json.read_text(encoding='utf-8'))
    if not isinstance(stage, dict):
        raise ValueError('stage.json root must be an object')
    binary = ffmpeg or ffmpeg_executable()
    duration = probe_video_duration(binary, video, _stage_fallback_seconds(stage))
    prepared: list[dict] = []
    for track in stage.get('audio') or []:
        if not isinstance(track, dict):
            continue
        path = _resolve_audio(track, stage_json)
        if path is None:
            continue
        if not path.is_file():
            raise FileNotFoundError(path)
        item = {
            'file': str(path),
            'start_ms': max(0, int(float(track.get('start_ms') or 0))),
            'volume': max(0.0, min(4.0, float(track.get('volume', 1.0)))),
            'loop': bool(track.get('loop')),
        }
        if item['loop']:
            item['loop_samples'] = probe_loop_samples(path, binary)
        prepared.append(item)
    plan = build_mix_plan(prepared, duration)
    output.parent.mkdir(parents=True, exist_ok=True)
    command = build_ffmpeg_command(binary, video, plan, output)
    _run(command)
    print(
        f'Muxed {output.name}: {len(prepared)} audio tracks, {duration:.3f}s',
        flush=True,
    )


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3:
        raise SystemExit('usage: python renderer/mux.py <video.avi> <stage.json> <output.mp4>')
    mux(Path(args[0]), Path(args[1]), Path(args[2]))


if __name__ == '__main__':
    main()
