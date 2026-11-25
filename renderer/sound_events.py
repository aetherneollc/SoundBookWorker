#!/usr/bin/env python3
"""Procedural foley and ducked PS2-era music beds for ``stage3d.json``.

Every clip is 24 kHz mono 16-bit PCM written with the standard library.
A per-event seed drives pitch and a one-pole filter so two footsteps that
share a kind do not share bytes when their frame (or seed) differs. The same
seed and frame always rebuild the same file. Music beds stay quiet enough
that a dialogue track at full level remains intelligible.
"""
from __future__ import annotations

import math
import random
import struct
import wave
import zlib
from pathlib import Path
from typing import Any

SAMPLE_RATE = 24000
MUSIC_DUCK_VOLUME = 0.18
FOLEY_KINDS: tuple[str, ...] = (
    "footstep_heavy",
    "footstep_light",
    "cloth_rustle",
    "table_slam",
    "metal_clang",
    "module_detach",
    "module_install",
    "console_beep",
    "console_error",
    "door_open_hiss",
    "door_close_thud",
    "alarm_klaxon",
    "power_down",
    "power_restore",
    "mech_dish_servo",
    "storm_thunder",
    "wind_gust",
    "camera_whoosh_impact",
)
MUSIC_STATES: tuple[str, ...] = ("tension", "conflict", "action", "climax", "release")
PHASE_MUSIC = {
    "setup": "tension",
    "escalate": "conflict",
    "tension": "tension",
    "conflict": "conflict",
    "action": "action",
    "climax": "climax",
    "release": "release",
}
FOLEY_VOLUME = {
    "footstep_heavy": 0.62,
    "footstep_light": 0.42,
    "cloth_rustle": 0.36,
    "table_slam": 0.78,
    "metal_clang": 0.55,
    "module_detach": 0.58,
    "module_install": 0.64,
    "console_beep": 0.40,
    "console_error": 0.48,
    "door_open_hiss": 0.50,
    "door_close_thud": 0.70,
    "alarm_klaxon": 0.46,
    "power_down": 0.44,
    "power_restore": 0.44,
    "mech_dish_servo": 0.40,
    "storm_thunder": 0.72,
    "wind_gust": 0.38,
    "camera_whoosh_impact": 0.34,
}


def _mix_seed(kind: str, seed: int, frame: int) -> int:
    folded = zlib.crc32(f"{kind}:{int(seed)}:{int(frame)}".encode("utf-8")) & 0xFFFFFFFF
    return folded or 1


def _rng(kind: str, seed: int, frame: int) -> random.Random:
    return random.Random(_mix_seed(kind, seed, frame))


def _write_wav(path: Path, samples: list[float], rate: int = SAMPLE_RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = bytearray(len(samples) * 2)
    for index, sample in enumerate(samples):
        struct.pack_into("<h", frames, index * 2, max(-32767, min(32767, int(sample * 32767))))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))


def _normalize(samples: list[float], peak: float = 0.9) -> list[float]:
    loudest = max((abs(sample) for sample in samples), default=0.0)
    if loudest < 1e-6:
        return samples
    scale = peak / loudest
    return [max(-1.0, min(1.0, sample * scale)) for sample in samples]


def _lowpass(samples: list[float], cutoff_hz: float, rate: int) -> list[float]:
    cutoff = max(40.0, float(cutoff_hz))
    rc = 1.0 / (2.0 * math.pi * cutoff)
    dt = 1.0 / rate
    alpha = dt / (rc + dt)
    output: list[float] = []
    state = 0.0
    for sample in samples:
        state += alpha * (sample - state)
        output.append(state)
    return output


def _noise(rng: random.Random, count: int) -> list[float]:
    return [rng.uniform(-1.0, 1.0) for _ in range(count)]


def _tone(freq: float, count: int, rate: int, decay: float) -> list[float]:
    samples: list[float] = []
    for index in range(count):
        t = index / rate
        samples.append(math.sin(2.0 * math.pi * freq * t) * math.exp(-t * decay))
    return samples


def _render_foley(kind: str, rng: random.Random, rate: int = SAMPLE_RATE) -> list[float]:
    pitch = 0.90 + rng.random() * 0.22
    if kind == "footstep_heavy":
        count = int((0.15 + rng.random() * 0.05) * rate)
        body = _tone(72.0 * pitch, count, rate, 16.0)
        click = _tone(240.0 * pitch, count, rate, 48.0)
        grit = _lowpass(_noise(rng, count), 500.0 + rng.random() * 400.0, rate)
        return _normalize([0.7 * body[i] + 0.25 * click[i] + 0.45 * grit[i] * math.exp(-i / rate * 20.0) for i in range(count)])
    if kind == "footstep_light":
        count = int((0.09 + rng.random() * 0.04) * rate)
        body = _tone(150.0 * pitch, count, rate, 26.0)
        grit = _lowpass(_noise(rng, count), 1200.0 + rng.random() * 800.0, rate)
        return _normalize([0.45 * body[i] + 0.4 * grit[i] * math.exp(-i / rate * 32.0) for i in range(count)])
    if kind == "cloth_rustle":
        count = int((0.18 + rng.random() * 0.08) * rate)
        grit = _lowpass(_noise(rng, count), 700.0 + rng.random() * 900.0, rate)
        return _normalize([grit[i] * (0.4 + 0.6 * math.sin(2 * math.pi * (6 + rng.random()) * i / rate)) * 0.5 for i in range(count)], 0.55)
    if kind == "table_slam":
        count = int(0.32 * rate)
        thump = _tone(58.0 * pitch, count, rate, 10.0)
        crack = _tone(640.0 * pitch, count, rate, 70.0)
        return _normalize([0.8 * thump[i] + 0.45 * crack[i] for i in range(count)])
    if kind == "metal_clang":
        count = int((0.45 + rng.random() * 0.1) * rate)
        partials = (180.0 * pitch, 430.0 * pitch, 790.0 * pitch * (0.97 + rng.random() * 0.06))
        mixed = [0.0] * count
        for freq, gain in zip(partials, (0.55, 0.30, 0.18)):
            tone = _tone(freq, count, rate, 6.5)
            for index, sample in enumerate(tone):
                mixed[index] += sample * gain
        return _normalize(mixed)
    if kind == "module_detach":
        count = int(0.28 * rate)
        scrape = _lowpass(_noise(rng, count), 1800.0 + rng.random() * 600.0, rate)
        tick = _tone(980.0 * pitch, count, rate, 40.0)
        return _normalize([0.55 * scrape[i] * (1.0 - i / count) + 0.35 * tick[i] for i in range(count)])
    if kind == "module_install":
        count = int(0.26 * rate)
        clunk = _tone(110.0 * pitch, count, rate, 14.0)
        tick = _tone(720.0 * pitch, count, rate, 36.0)
        return _normalize([0.6 * clunk[i] + 0.35 * tick[i] for i in range(count)])
    if kind == "console_beep":
        count = int(0.09 * rate)
        freq = 880.0 * pitch
        return _normalize([math.sin(2 * math.pi * freq * i / rate) * (1.0 - i / count) for i in range(count)], 0.7)
    if kind == "console_error":
        count = int(0.32 * rate)
        samples = []
        for index in range(count):
            t = index / rate
            freq = 740.0 * pitch if t < 0.14 else 520.0 * pitch
            gate = 1.0 if (t < 0.12 or 0.16 < t < 0.30) else 0.0
            samples.append(math.sin(2 * math.pi * freq * t) * gate)
        return _normalize(samples, 0.7)
    if kind == "door_open_hiss":
        count = int(0.55 * rate)
        air = _lowpass(_noise(rng, count), 900.0 + rng.random() * 400.0, rate)
        return _normalize([air[i] * math.sin(math.pi * i / count) * 0.8 for i in range(count)], 0.6)
    if kind == "door_close_thud":
        count = int(0.34 * rate)
        thud = _tone(70.0 * pitch, count, rate, 9.0)
        latch = _tone(420.0 * pitch, count, rate, 30.0)
        return _normalize([0.75 * thud[i] + 0.3 * latch[i] for i in range(count)])
    if kind == "alarm_klaxon":
        count = int(0.9 * rate)
        samples = []
        for index in range(count):
            t = index / rate
            freq = (440.0 if int(t * 6) % 2 == 0 else 554.0) * pitch
            samples.append(math.copysign(min(1.0, abs(math.sin(2 * math.pi * freq * t))), math.sin(2 * math.pi * freq * t)) * 0.45)
        return _normalize(samples, 0.65)
    if kind == "power_down":
        count = int(0.7 * rate)
        samples = []
        phase = 0.0
        for index in range(count):
            u = index / count
            freq = (420.0 * pitch) * (1.0 - u) + 60.0
            phase += 2 * math.pi * freq / rate
            samples.append(math.sin(phase) * (1.0 - u))
        return _normalize(samples, 0.7)
    if kind == "power_restore":
        count = int(0.55 * rate)
        samples = []
        phase = 0.0
        for index in range(count):
            u = index / count
            freq = 80.0 + 280.0 * u * pitch
            phase += 2 * math.pi * freq / rate
            samples.append(math.sin(phase) * min(1.0, u * 3.0) * (1.0 - 0.3 * u))
        return _normalize(samples, 0.7)
    if kind == "mech_dish_servo":
        count = int(0.8 * rate)
        samples = []
        vibrato = 3.0 + rng.random() * 2.0
        for index in range(count):
            t = index / rate
            freq = 180.0 * pitch * (1.0 + 0.04 * math.sin(2 * math.pi * vibrato * t))
            samples.append(math.sin(2 * math.pi * freq * t) * 0.45 * math.sin(math.pi * min(1.0, t / 0.8)))
        return _normalize(samples, 0.55)
    if kind == "storm_thunder":
        count = int(1.1 * rate)
        rumble = _lowpass(_noise(rng, count), 180.0 + rng.random() * 80.0, rate)
        return _normalize([rumble[i] * math.sin(math.pi * i / count) for i in range(count)])
    if kind == "wind_gust":
        count = int(0.9 * rate)
        air = _lowpass(_noise(rng, count), 600.0 + rng.random() * 300.0, rate)
        return _normalize([air[i] * (0.35 + 0.65 * math.sin(math.pi * i / count)) for i in range(count)], 0.55)
    if kind == "camera_whoosh_impact":
        count = int(0.28 * rate)
        whoosh = _lowpass(_noise(rng, count), 1500.0 * pitch, rate)
        hit = _tone(90.0 * pitch, count, rate, 18.0)
        return _normalize([
            whoosh[i] * (i / count) + (0.7 * hit[i] if i > count * 0.62 else 0.0)
            for i in range(count)
        ], 0.6)
    raise ValueError(f"unknown foley kind {kind!r}")


def _render_music(state: str, seconds: float, rng: random.Random, rate: int = SAMPLE_RATE) -> list[float]:
    tempos = {"tension": 72.0, "conflict": 96.0, "action": 126.0, "climax": 144.0, "release": 66.0}
    roots = {"tension": 55.0, "conflict": 49.0, "action": 41.0, "climax": 46.0, "release": 65.0}
    if state not in tempos:
        raise ValueError(f"unknown music state {state!r}")
    bpm = tempos[state] * (0.98 + rng.random() * 0.04)
    root = roots[state] * (0.99 + rng.random() * 0.02)
    count = max(1, int(max(0.25, seconds) * rate))
    beat = 60.0 / bpm
    drive = 1.15 if state == "climax" else (0.9 if state == "action" else 0.7)
    samples: list[float] = []
    for index in range(count):
        t = index / rate
        phase = (t % beat) / beat
        envelope = math.exp(-phase * (5.5 if state != "release" else 3.2))
        bass = math.sin(2 * math.pi * root * t) * envelope
        fifth = math.sin(2 * math.pi * root * 1.498 * t) * envelope * 0.32
        hat = 0.0
        if state in ("conflict", "action", "climax") and (t % (beat * 0.5)) < 0.018:
            hat = rng.uniform(-1.0, 1.0) * (0.28 if state == "climax" else 0.16)
        mixed = (bass + fifth) * drive + hat
        if state == "climax":
            mixed = math.tanh(mixed * 1.6)
        elif state == "release":
            mixed *= 0.65
        samples.append(mixed * 0.55)
    return _normalize(samples, 0.8)


class SoundEvent:
    """Synthesize foley one-shots and adaptive music stems, then stamp timeline entries."""

    def __init__(self, output_dir: Path, rate: int = SAMPLE_RATE) -> None:
        self.output_dir = Path(output_dir)
        self.rate = int(rate)

    def synthesize(
        self,
        kind: str,
        *,
        seed: int = 1,
        frame: int = 0,
        duration_s: float | None = None,
        intensity: float = 0.7,
    ) -> Path:
        """Write one varied WAV. ``duration_s`` overrides the built-in foley length for music only when kind is a music state."""
        del intensity
        if kind in MUSIC_STATES:
            return self.music_bed(kind, duration_s or 2.0, seed=seed, frame=frame)
        if kind not in FOLEY_KINDS:
            raise ValueError(f"unknown sound kind {kind!r}")
        rng = _rng(kind, seed, frame)
        samples = _render_foley(kind, rng, self.rate)
        if duration_s is not None and duration_s > 0:
            target = max(1, int(duration_s * self.rate))
            if len(samples) < target:
                samples = samples + [0.0] * (target - len(samples))
            else:
                samples = samples[:target]
        path = self.output_dir / f"{kind}_{_mix_seed(kind, seed, frame):08x}.wav"
        _write_wav(path, samples, self.rate)
        return path

    def music_bed(self, state: str, duration_s: float, seed: int = 1, frame: int = 0) -> Path:
        """A gritty bass-and-hat stem for one adaptive music state."""
        if state not in MUSIC_STATES:
            raise ValueError(f"unknown music state {state!r}")
        rng = _rng(f"music_{state}", seed, frame)
        samples = _render_music(state, duration_s, rng, self.rate)
        path = self.output_dir / f"music_{state}_{_mix_seed(state, seed, frame):08x}.wav"
        _write_wav(path, samples, self.rate)
        return path

    def timeline_entry(
        self,
        kind: str,
        *,
        frame: int,
        fps: int,
        volume: float | None = None,
        loop: bool = False,
        seed: int = 1,
        duration_s: float | None = None,
    ) -> dict[str, Any]:
        """One mux-ready audio entry whose ``start_ms`` matches ``frame / fps``."""
        fps = max(1, int(fps))
        path = self.synthesize(kind, seed=seed, frame=frame, duration_s=duration_s)
        if volume is None:
            volume = MUSIC_DUCK_VOLUME if kind in MUSIC_STATES or loop else FOLEY_VOLUME.get(kind, 0.5)
        entry: dict[str, Any] = {
            "file": path.as_posix(),
            "start_ms": int(round(int(frame) * 1000 / fps)),
            "volume": round(float(volume), 3),
            "loop": bool(loop),
            "kind": kind,
        }
        if loop:
            with wave.open(str(path), "rb") as handle:
                entry["loop_samples"] = max(1, handle.getnframes())
        return entry

    def entries_for_performance(self, beats: list[Any], fps: int) -> list[dict[str, Any]]:
        """Foley on each beat plus one ducked music bed per tension stretch."""
        entries: list[dict[str, Any]] = []
        for index, beat in enumerate(beats):
            data = beat.to_dict() if hasattr(beat, "to_dict") else dict(beat)
            kind = str(data.get("sound_event") or "").strip()
            if not kind or kind == "none" or kind not in FOLEY_KINDS:
                continue
            frame = int(data.get("from_frame", data.get("from", 0)))
            entries.append(self.timeline_entry(
                kind,
                frame=frame,
                fps=fps,
                volume=FOLEY_VOLUME.get(kind, 0.5),
                loop=False,
                seed=1000 + index * 17 + frame,
            ))
        entries.extend(self._music_for_phases(beats, fps))
        return entries

    def _music_for_phases(self, beats: list[Any], fps: int) -> list[dict[str, Any]]:
        if not beats:
            return []
        groups: list[tuple[str, int, int]] = []
        for beat in beats:
            data = beat.to_dict() if hasattr(beat, "to_dict") else dict(beat)
            phase = str(data.get("tension_phase") or "tension")
            state = PHASE_MUSIC.get(phase, phase if phase in MUSIC_STATES else "tension")
            start = int(data.get("from_frame", data.get("from", 0)))
            end = int(data.get("to_frame", data.get("to", start)))
            if groups and groups[-1][0] == state:
                kind, group_start, _group_end = groups[-1]
                groups[-1] = (kind, group_start, max(end, _group_end))
            else:
                groups.append((state, start, end))
        entries: list[dict[str, Any]] = []
        for index, (state, start, end) in enumerate(groups):
            seconds = max(0.4, (end - start) / max(1, fps))
            entries.append(self.timeline_entry(
                state,
                frame=start,
                fps=fps,
                volume=MUSIC_DUCK_VOLUME,
                loop=True,
                seed=4000 + index * 13,
                duration_s=seconds,
            ))
        return entries
