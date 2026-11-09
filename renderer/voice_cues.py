"""Derive per-line mouth cues from the actual audio, with a CPU-only fallback."""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def _rhubarb(path: Path, text: str) -> list[dict] | None:
    binary = os.environ.get('RHUBARB_BIN') or shutil.which('rhubarb')
    if not binary:
        return None
    try:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wav = root / 'voice.wav'
            output = root / 'mouth.json'
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(path),
                            '-ac', '1', '-ar', '16000', str(wav)], check=True, timeout=90)
            command = [binary, '-r', 'phonetic', '-f', 'json', '-o', str(output)]
            if text:
                script = root / 'script.txt'
                script.write_text(text, encoding='utf-8')
                command.extend(['-d', str(script)])
            subprocess.run([*command, str(wav)], check=True, timeout=90,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            cues = json.loads(output.read_text(encoding='utf-8')).get('mouthCues', [])
            if not isinstance(cues, list):
                return None
            return [{'start': max(0, float(c['start'])), 'end': max(0, float(c['end'])),
                     'value': str(c['value'])} for c in cues
                    if isinstance(c, dict) and str(c.get('value', '')) in 'ABCDEFGHX'
                    and float(c.get('end', 0)) > float(c.get('start', 0))]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as error:
        print(f'Rhubarb unavailable for {path.name}: {type(error).__name__}; using audio energy', flush=True)
        return None


def _energy(path: Path) -> list[dict]:
    # This fallback drives simple open/close shapes. It never pretends to infer phonemes.
    pcm = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path), '-ac', '1',
                          '-ar', '16000', '-f', 's16le', '-'], capture_output=True,
                         check=True, timeout=90).stdout
    sample_rate, window = 16000, 800  # 50 ms
    samples = memoryview(pcm).cast('h')
    levels = []
    for start in range(0, len(samples), window):
        values = samples[start:start + window]
        if not values:
            break
        levels.append(math.sqrt(sum(v * v for v in values) / len(values)) / 32768)
    if not levels:
        return []
    peak = sorted(levels)[int((len(levels) - 1) * .92)]
    threshold = max(.003, peak * .13)
    cues: list[dict] = []
    last = None
    for i, level in enumerate(levels):
        shape = 'X' if level < threshold else ('B' if level < peak * .35 else 'C' if level < peak * .68 else 'D')
        if shape != last:
            if cues:
                cues[-1]['end'] = i * window / sample_rate
            cues.append({'start': i * window / sample_rate, 'end': 0.0, 'value': shape})
            last = shape
    cues[-1]['end'] = len(samples) / sample_rate
    return cues


def speech_cues(path: Path, text: str = '') -> list[dict]:
    cues = _rhubarb(path, text)
    return cues if cues is not None else _energy(path)
