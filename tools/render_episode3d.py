#!/usr/bin/env python3
"""Local Godot 4.7.2 runner for a stage3d episode.

Compiles a manifest, renders a silent movie, muxes the stage audio, and writes
a QC report.

    python tools/render_episode3d.py --manifest <manifest.json> --out-dir <dir> [--video-name episode.mp4]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_GODOT_BIN = Path(
    r'C:\Users\Hero1\AppData\Local\Microsoft\WinGet\Packages'
    r'\GodotEngine.GodotEngine_Microsoft.Winget.Source_8wekyb3d8bbwe'
    r'\godot_console.exe'
)
CJK_FONT = Path('C:/Windows/Fonts/msyh.ttc')
QUIT_AFTER_PADDING = 120


def collect_sample_frames(review: object) -> list[int]:
    """Flatten ``meta.review_frames`` ints and nested lists (such as ``walk_strip``)."""
    found: list[int] = []

    def walk(value: object) -> None:
        if isinstance(value, bool):
            return
        if isinstance(value, int):
            found.append(value)
            return
        if isinstance(value, float) and value.is_integer():
            found.append(int(value))
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                walk(item)
            return
        if isinstance(value, dict):
            for item in value.values():
                walk(item)

    walk(review or {})
    return sorted({frame for frame in found if frame >= 0})


def resolve_godot() -> Path:
    override = os.environ.get('GODOT_BIN', '').strip()
    path = Path(override) if override else DEFAULT_GODOT_BIN
    if not path.is_file():
        raise SystemExit(f'Godot console executable not found: {path}')
    return path


def stage_env(out_dir: Path, sample_frames: list[int]) -> dict[str, str]:
    env = os.environ.copy()
    env['STAGE_JSON'] = (out_dir / 'stage3d.json').resolve().as_posix()
    env['STAGE_RESULT_JSON'] = (out_dir / 'stage-result.json').resolve().as_posix()
    env['STAGE_TELEMETRY_JSON'] = (out_dir / 'qc_telemetry.json').resolve().as_posix()
    env['STAGE_SAMPLE_FRAMES'] = ','.join(str(frame) for frame in sample_frames)
    env['STAGE_SAMPLE_DIR'] = (out_dir / 'review').resolve().as_posix()
    if CJK_FONT.is_file():
        env['STAGE_CJK_FONT'] = CJK_FONT.as_posix()
    return env


def godot_command(godot: Path, fps: int, total_frames: int, movie: Path) -> list[str]:
    return [
        str(godot),
        '--path', 'godot3d',
        '--rendering-method', 'gl_compatibility',
        '--rendering-driver', 'opengl3',
        '--audio-driver', 'Dummy',
        '--resolution', '1280x720',
        '--fixed-fps', str(fps),
        '--disable-vsync',
        '--quit-after', str(int(total_frames) + QUIT_AFTER_PADDING),
        '--write-movie', str(movie),
        'res://player/main.tscn',
    ]


def run_command(command: list[str], *, capture: bool = False, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=capture,
        text=True,
        encoding='utf-8',
        errors='replace',
    )


def load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def positive_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    if number <= 0 or number != number or number == float('inf'):
        return None
    return number


def print_summary(stage: dict, wall_s: float, result: dict | None, report: dict | None, video: Path) -> None:
    total = int(stage.get('total_frames') or 0)
    elapsed = positive_float((result or {}).get('elapsed_sec'))
    render_fps = positive_float((result or {}).get('render_fps'))
    if elapsed is None:
        elapsed = wall_s
    if render_fps is None:
        render_fps = (total / elapsed) if elapsed else 0.0
    status = str((report or {}).get('status') or 'MISSING')
    print(f'Render: {render_fps:.2f} fps, {elapsed:.2f}s wall', flush=True)
    print(f'QC: {status}', flush=True)
    for check in (report or {}).get('checks') or []:
        if not isinstance(check, dict):
            continue
        print(f"  {check.get('id', 'check')}: {check.get('status', 'MISSING')}", flush=True)
    print(f'Video: {video}', flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Compile, render, mux, and QC one godot3d episode.')
    parser.add_argument('--manifest', required=True, type=Path, help='episode manifest.json')
    parser.add_argument('--out-dir', required=True, type=Path, help='directory for stage3d, movie, stills, and QC')
    parser.add_argument('--video-name', default='episode.mp4', help='file name written inside --out-dir')
    args = parser.parse_args(argv)

    manifest = args.manifest.expanduser().resolve()
    if not manifest.is_file():
        raise SystemExit(f'manifest not found: {manifest}')
    out_dir = args.out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'review').mkdir(parents=True, exist_ok=True)
    video_name = Path(args.video_name)
    video = video_name.resolve() if video_name.is_absolute() else (out_dir / video_name).resolve()
    video.parent.mkdir(parents=True, exist_ok=True)

    godot = resolve_godot()
    stage_path = out_dir / 'stage3d.json'
    movie = out_dir / 'silent.avi'

    from renderer.prepare_stage3d import prepare

    try:
        stage = prepare(manifest, stage_path)
    except Exception as exc:
        raise SystemExit(f'prepare_stage3d failed: {exc}') from exc

    fps = int(stage.get('fps') or 0)
    total_frames = int(stage.get('total_frames') or 0)
    if fps <= 0 or total_frames <= 0:
        raise SystemExit(f'stage3d.json has no timeline (fps={fps}, total_frames={total_frames})')

    review = (stage.get('meta') or {}).get('review_frames') if isinstance(stage.get('meta'), dict) else {}
    sample_frames = collect_sample_frames(review)
    started = time.perf_counter()
    rendered = run_command(godot_command(godot, fps, total_frames, movie), capture=False, env=stage_env(out_dir, sample_frames))
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
    qc = run_command([
        sys.executable,
        str(ROOT / 'tools' / 'qc_pipeline.py'),
        '--stage', str(stage_path),
        '--telemetry', str(out_dir / 'qc_telemetry.json'),
        '--video', str(video),
        '--stills', str(out_dir / 'review'),
        '--registry', str(ROOT / 'media' / 'assets3d' / 'registry.json'),
        '--out', str(report_path),
    ], capture=True)
    report = load_json(report_path)
    if report is None:
        detail = (qc.stderr or qc.stdout or '').strip()
        if detail:
            print(detail, file=sys.stderr, flush=True)
    print_summary(stage, wall_s, load_json(out_dir / 'stage-result.json'), report, video)
    return qc.returncode


if __name__ == '__main__':
    raise SystemExit(main())
