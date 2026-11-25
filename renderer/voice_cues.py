"""Derive per-line mouth cues from the actual audio, with a CPU-only fallback.

Rhubarb is used when it is installed. Otherwise the cues come from the loudness
envelope of the line, shaped by which vowels the text says are being spoken, and
held long enough that the mouth never flickers for a frame or two.

Every cue is ``{'start': seconds, 'end': seconds, 'value': shape}`` relative to the
start of the line. Shapes are Rhubarb's: ``X`` rest, ``A`` closed, ``B`` slightly
open, ``C`` open, ``D`` wide open, ``E`` rounded, ``F`` puckered.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

try:
    from zh_finals import NARROW_FINALS, OPEN_FINALS, ROUND_FINALS
except ImportError:  # ``python -m unittest`` loads this module as part of the package.
    from renderer.zh_finals import NARROW_FINALS, OPEN_FINALS, ROUND_FINALS

HOP_SEC = 0.025
SMOOTH_WEIGHTS = (0.25, 0.5, 0.25)
# A shape stays up at least this long. 150 ms is still three whole frames at 24 fps
# after both ends of a cue have been rounded to a frame; 125 ms can round down to two.
MIN_HOLD_SEC = 0.15
# Silence has to last longer than this before the mouth closes to X.
CLOSE_AFTER_SEC = 0.12
# A burst of sound shorter than this is a click or a breath, not speech.
MIN_VOICED_SEC = 0.075
# Share of the voiced time the wide-open D may take.
MAX_D_RATIO = 0.16

_OPENNESS = {'X': 0.0, 'A': 0.05, 'B': 0.35, 'C': 0.6, 'D': 1.0, 'E': 0.55, 'F': 0.3, 'G': 0.2, 'H': 0.45}
# Level thresholds (0..1 of the line's loud level) at which each vowel class steps up
# to its next shape. ``None`` stands for the line's own D threshold.
_LADDERS: dict[str, tuple[tuple[str, float | None], ...]] = {
    'a': (('B', 0.0), ('C', 0.30), ('D', None)),  # open vowels: the only ones that reach D
    'i': (('B', 0.0), ('C', 0.56)),               # narrow vowels
    'o': (('B', 0.0), ('E', 0.26)),               # rounded vowels
    '': (('B', 0.0), ('C', 0.40), ('D', None)),   # no text hint
}
_HYSTERESIS = 0.08
_DIGIT_CLASS = {'0': 'i', '1': 'i', '2': 'i', '3': 'a', '4': 'i', '5': 'o', '6': 'o', '7': 'i', '8': 'a', '9': 'o'}
_BREAKS = frozenset('，。！？；：、,.!?;:…—\n')
_HANZI_CLASS: dict[str, str] = {}


def _rhubarb(path: Path, text: str, language: str) -> list[dict] | None:
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
            recognizer = 'pocketSphinx' if language.lower().startswith('en') else 'phonetic'
            command = [binary, '-r', recognizer, '-f', 'json', '-o', str(output)]
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


def _read_mono_16k_samples(path: Path) -> tuple[list[int] | memoryview, int]:
    if path.suffix.lower() == '.wav':
        import wave
        try:
            with wave.open(str(path), 'rb') as wf:
                channels = wf.getnchannels()
                sampwidth = wf.getsampwidth()
                rate = wf.getframerate()
                raw = wf.readframes(wf.getnframes())
            if sampwidth == 2 and rate > 0:
                shorts = memoryview(raw).cast('h')
                if channels == 1:
                    return shorts, rate
                mono = [int(sum(shorts[i + c] for c in range(channels)) / channels)
                        for i in range(0, len(shorts) - channels + 1, channels)]
                return mono, rate
        except Exception:
            pass
    pcm = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path), '-ac', '1',
                          '-ar', '16000', '-f', 's16le', '-'], capture_output=True,
                         check=True, timeout=90).stdout
    return memoryview(pcm).cast('h'), 16000


def _envelope(samples, sample_rate: int) -> tuple[list[float], int]:
    """RMS per 25 ms hop, smoothed over three hops. Returns ``(levels, hop_samples)``."""
    hop = max(1, round(sample_rate * HOP_SEC))
    raw = []
    for start in range(0, len(samples), hop):
        values = samples[start:start + hop]
        if not values:
            break
        raw.append(math.sqrt(sum(v * v for v in values) / len(values)) / 32768)
    before, centre, after = SMOOTH_WEIGHTS
    smooth = []
    for index, level in enumerate(raw):
        total, weight = level * centre, centre
        if index > 0:
            total, weight = total + raw[index - 1] * before, weight + before
        if index + 1 < len(raw):
            total, weight = total + raw[index + 1] * after, weight + after
        smooth.append(total / weight)
    return smooth, hop


def _runs(labels: list) -> list[list]:
    """Run-length encode: ``[[value, start_index, length], ...]``."""
    runs: list[list] = []
    for index, value in enumerate(labels):
        if runs and runs[-1][0] == value:
            runs[-1][2] += 1
        else:
            runs.append([value, index, 1])
    return runs


def _settle_voiced(voiced: list[bool], close_hops: int, island_hops: int, hold_hops: int) -> list[bool]:
    """Bridge short silences, drop clicks, and give every voiced stretch room for one hold."""
    voiced = list(voiced)
    for _ in range(8):
        before = list(voiced)
        runs = _runs(voiced)
        for position, (value, start, length) in enumerate(runs):
            interior = 0 < position < len(runs) - 1
            if not value and interior and length < close_hops:
                voiced[start:start + length] = [True] * length
        for value, start, length in _runs(voiced):
            if value and length < island_hops:
                voiced[start:start + length] = [False] * length
        for value, start, length in _runs(voiced):
            if value and length < hold_hops:
                # Borrow from the silence after it, or before it at the end of the line.
                end = min(len(voiced), start + hold_hops)
                begin = max(0, end - hold_hops)
                voiced[begin:end] = [True] * (end - begin)
        if voiced == before:
            break
    return voiced


def _hanzi_class(char: str) -> str | None:
    if not _HANZI_CLASS:
        for name, table in (('a', OPEN_FINALS), ('o', ROUND_FINALS), ('i', NARROW_FINALS)):
            for entry in table:
                _HANZI_CLASS.setdefault(entry, name)
    return _HANZI_CLASS.get(char)


def _syllable_plan(text: str) -> list[list[str]]:
    """Vowel class of each spoken syllable, grouped into the phrases punctuation marks off.

    ``'a'`` is an open vowel, ``'i'`` a narrow one, ``'o'`` a rounded one, and ``''``
    a syllable whose vowel is unknown (Latin words, rare characters).
    """
    phrases: list[list[str]] = [[]]
    latin = ''

    def flush_latin() -> None:
        nonlocal latin
        if latin:
            if latin.isupper() and len(latin) <= 5:
                count = len(latin)  # An initialism is spelled out letter by letter.
            else:
                groups, inside = 0, False
                for letter in latin.lower():
                    vowel = letter in 'aeiouy'
                    groups += vowel and not inside
                    inside = vowel
                count = max(1, groups)
            phrases[-1].extend([''] * count)
            latin = ''

    for char in text or '':
        if char.isascii() and char.isalpha():
            latin += char
            continue
        flush_latin()
        if char in _BREAKS:
            if phrases[-1]:
                phrases.append([])
        elif char in _DIGIT_CLASS:
            phrases[-1].append(_DIGIT_CLASS[char])
        elif '\u4e00' <= char <= '\u9fff':
            phrases[-1].append(_hanzi_class(char) or '')
    flush_latin()
    return [phrase for phrase in phrases if phrase]


def _spread_syllables(voiced: list[bool], plan: list[list[str]], hop_sec: float) -> list[str]:
    """Vowel class for every hop; ``''`` where there is no hint."""
    classes = [''] * len(voiced)
    if not plan:
        return classes
    stretches = [(start, length) for value, start, length in _runs(voiced) if value]
    if not stretches:
        return classes

    def paced(length: int, count: int) -> bool:
        return 0.07 <= length * hop_sec / count <= 0.65

    # When the pauses in the audio match the punctuation, line the phrases up one to one.
    # Otherwise spread every syllable evenly over all the voiced time.
    if len(stretches) == len(plan) and all(
            paced(length, len(phrase)) for (_, length), phrase in zip(stretches, plan)):
        groups = [([stretch], phrase) for stretch, phrase in zip(stretches, plan)]
    else:
        groups = [(stretches, [name for phrase in plan for name in phrase])]
    for group, syllables in groups:
        total = sum(length for _, length in group)
        seen = 0
        for start, length in group:
            for offset in range(length):
                classes[start + offset] = syllables[min(len(syllables) - 1, seen * len(syllables) // total)]
                seen += 1
    return classes


def _percentile(ordered: list[float], share: float) -> float:
    return ordered[int((len(ordered) - 1) * share)] if ordered else 0.0


def _tier(ladder, level: float, d_on: float) -> int:
    tier = 0
    for index, (_, threshold) in enumerate(ladder):
        if level >= (d_on if threshold is None else threshold):
            tier = index
    return tier


def _hold(raw: list[str], hold_hops: int) -> list[str]:
    """Keep each shape up for at least ``hold_hops``; a change must outlast half of that."""
    def dominant(window: list[str], prefer: str) -> str:
        counts: dict[str, int] = {}
        for value in window:
            counts[value] = counts.get(value, 0) + 1
        best = max(counts.values())
        return prefer if counts.get(prefer) == best else next(v for v in window if counts[v] == best)

    current = dominant(raw[:hold_hops], raw[0])
    held, since = [], 0
    for index, value in enumerate(raw):
        if value != current and since >= hold_hops and len(raw) - index >= hold_hops:
            candidate = dominant(raw[index:index + hold_hops], value)
            if candidate != current:
                current, since = candidate, 0
        held.append(current)
        since += 1
    return held


def _mouth_labels(levels: list[float], plan: list[list[str]] | None = None,
                  hop_sec: float = HOP_SEC) -> list[str]:
    """One mouth shape per hop of the loudness envelope."""
    count = len(levels)
    if not count:
        return []
    ordered = sorted(levels)
    loudest = ordered[-1]
    if loudest < 0.0012:
        return ['X'] * count
    # The loud level ignores the odd spike; a mostly silent file still gets a usable one.
    loud = max(_percentile(ordered, 0.95), loudest * 0.5)
    noise = _percentile(ordered, 0.10)
    gate = min(max(0.003, noise * 2.5, loud * 0.10), loud * 0.5)
    release = gate * 0.7

    hold_hops = max(1, math.ceil(MIN_HOLD_SEC / hop_sec - 1e-6))
    close_hops = max(hold_hops, math.floor(CLOSE_AFTER_SEC / hop_sec + 1e-6) + 1)
    # Smoothing spreads a sound over one extra hop on each side.
    island_hops = math.ceil(MIN_VOICED_SEC / hop_sec - 1e-6) + len(SMOOTH_WEIGHTS) - 1
    voiced, speaking = [], False
    for level in levels:
        speaking = level >= (release if speaking else gate)
        voiced.append(speaking)
    voiced = _settle_voiced(voiced, close_hops, island_hops, hold_hops)
    if not any(voiced):
        return ['X'] * count

    span = max(loud - gate, 1e-9)
    relative = [min(1.5, max(0.0, (level - gate) / span)) for level in levels]
    classes = _spread_syllables(voiced, plan or [], hop_sec)
    spoken = sorted(relative[index] for index in range(count) if voiced[index])
    hinted = any(classes)
    # D is for the accented peaks only: the loudest slice of the line, and loud in itself.
    d_on = max(0.72, _percentile(spoken, 0.82)) if hinted else max(0.80, _percentile(spoken, 0.90))

    labels = ['X'] * count
    for value, start, length in _runs(voiced):
        if not value:
            continue
        raw, tier, last_class = [], 0, None
        for index in range(start, start + length):
            ladder = _LADDERS[classes[index]]
            strict = _tier(ladder, relative[index], d_on)
            if classes[index] == last_class:
                # Hysteresis: stay on the current step until the level clearly leaves it.
                tier = min(max(tier, strict), _tier(ladder, relative[index] + _HYSTERESIS, d_on))
            else:
                tier = strict
            last_class = classes[index]
            raw.append(ladder[tier][0])
        labels[start:start + length] = _hold(raw, hold_hops)

    voiced_total = sum(voiced)
    wide = [[sum(relative[start:start + length]) / length, start, length]
            for value, start, length in _runs(labels) if value == 'D']
    wide.sort()
    excess = sum(run[2] for run in wide) - MAX_D_RATIO * voiced_total
    for _, start, length in wide:
        if excess <= 0:
            break
        labels[start:start + length] = ['C'] * length  # The weakest peaks give way first.
        excess -= length
    return labels


def _energy(path: Path, text: str = '', language: str = 'zh') -> list[dict]:
    """Loudness-driven cues. The text only says which vowels to expect, not when."""
    samples, sample_rate = _read_mono_16k_samples(path)
    levels, hop = _envelope(samples, sample_rate)
    if not levels:
        return []
    plan = _syllable_plan(text) if language.lower().startswith('zh') else []
    labels = _mouth_labels(levels, plan)
    hop_sec = hop / sample_rate
    total = len(samples) / sample_rate
    cues = []
    for value, start, _length in _runs(labels):
        if cues:
            cues[-1]['end'] = round(start * hop_sec, 4)
        cues.append({'start': round(start * hop_sec, 4), 'end': 0.0, 'value': value})
    cues[-1]['end'] = round(max(total, cues[-1]['start'] + hop_sec), 4)
    return cues


def stabilize_cues(cues: list[dict], min_hold: float = MIN_HOLD_SEC) -> list[dict]:
    """Merge cues shorter than ``min_hold`` into a neighbour so no shape flashes by.

    A short closure between two open shapes is held open, a short open shape joins the
    open neighbour it resembles most, and a lone short sound between two rests either
    borrows time from the rest after it or, if it is a mere blip, is dropped. Rests at
    the very start and end of the line keep their exact length.
    """
    runs: list[dict] = []
    for cue in sorted(cues, key=lambda item: float(item['start'])):
        start, end, value = float(cue['start']), float(cue['end']), str(cue['value'])
        if end <= start:
            continue
        if runs and runs[-1]['value'] == value and start - runs[-1]['end'] < 1e-3:
            runs[-1]['end'] = max(runs[-1]['end'], end)
        else:
            runs.append({'start': start, 'end': end, 'value': value})

    def merge_equal() -> None:
        index = 1
        while index < len(runs):
            if runs[index]['value'] == runs[index - 1]['value']:
                runs[index - 1]['end'] = runs[index]['end']
                del runs[index]
            else:
                index += 1

    stuck: set[tuple[float, str]] = set()
    for _ in range(len(runs) * 4 + 4):
        short = [
            (run['end'] - run['start'], index) for index, run in enumerate(runs)
            if run['end'] - run['start'] < min_hold - 1e-6
            and (run['start'], run['value']) not in stuck
            and not (run['value'] == 'X' and index in (0, len(runs) - 1))
        ]
        if not short or len(runs) < 2:
            break
        length, index = min(short)
        run = runs[index]
        before = runs[index - 1] if index > 0 else None
        after = runs[index + 1] if index + 1 < len(runs) else None
        open_before = before is not None and before['value'] != 'X'
        open_after = after is not None and after['value'] != 'X'
        if run['value'] == 'X':
            # A closure too short to read: the mouth simply stays open through it.
            run['value'] = before['value'] if open_before else after['value']
        elif open_before or open_after:
            if open_before and open_after:
                gap_before = abs(_OPENNESS.get(before['value'], 0.5) - _OPENNESS.get(run['value'], 0.5))
                gap_after = abs(_OPENNESS.get(after['value'], 0.5) - _OPENNESS.get(run['value'], 0.5))
                run['value'] = before['value'] if gap_before <= gap_after else after['value']
            else:
                run['value'] = before['value'] if open_before else after['value']
        elif length < MIN_VOICED_SEC:
            run['value'] = 'X'
        else:
            need = min_hold - length
            last = index + 1 == len(runs) - 1
            first = index - 1 == 0
            if after is not None and (last or after['end'] - after['start'] - need >= min_hold):
                run['end'] += need
                after['start'] = run['end']
            elif before is not None and (first or before['end'] - before['start'] - need >= min_hold):
                run['start'] -= need
                before['end'] = run['start']
            else:
                stuck.add((run['start'], run['value']))
        runs[:] = [item for item in runs if item['end'] - item['start'] > 1e-6]
        merge_equal()
    return runs


def speech_cues(path: Path, text: str = '', language: str = 'zh') -> list[dict]:
    cues = _rhubarb(path, text, language)
    if cues is None:
        return _energy(path, text, language)
    # Rhubarb's phonetic recognizer chatters on Chinese speech; English keeps its timing.
    return stabilize_cues(cues) if language.lower().startswith('zh') else cues
