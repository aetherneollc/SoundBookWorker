"""Filter-graph construction for mux.py, plus a 1-second encode when ffmpeg exists."""
from __future__ import annotations

import json
import math
import struct
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path

from renderer.mux import (
    VIDEO_ENCODE,
    build_ffmpeg_command,
    build_mix_plan,
    ffmpeg_executable,
    mux,
    probe_video_duration,
)


def _ffmpeg():
    try:
        return ffmpeg_executable()
    except RuntimeError:
        return None


def _tone(path: Path, seconds: float = 0.6, rate: int = 16000) -> None:
    with wave.open(str(path), 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        frames = bytearray()
        for index in range(int(rate * seconds)):
            sample = int(0.4 * math.sin(2 * math.pi * 440 * index / rate) * 32767)
            frames.extend(struct.pack('<h', sample))
        handle.writeframes(frames)


class MixPlanTests(unittest.TestCase):
    def test_single_track_has_no_amix(self):
        plan = build_mix_plan(
            [{'file': 'voice.wav', 'start_ms': 0, 'volume': 1, 'loop': False}],
            1.0,
        )
        self.assertEqual(plan.input_args, ['-i', 'voice.wav'])
        self.assertNotIn('amix', plan.filter_complex)
        self.assertNotIn('aloop', plan.filter_complex)
        self.assertIn('volume=1.000', plan.filter_complex)
        self.assertIn('[aout]', plan.filter_complex)
        self.assertEqual(plan.duration, 1.0)

    def test_multiple_tracks_delay_before_the_mix(self):
        plan = build_mix_plan([
            {'file': 'a.wav', 'start_ms': 400, 'volume': 1, 'loop': False},
            {'file': 'b.wav', 'start_ms': 1200, 'volume': 0.4, 'loop': False},
        ], 3.0)
        self.assertEqual(plan.input_args, ['-i', 'a.wav', '-i', 'b.wav'])
        self.assertIn('amix=inputs=2:duration=longest:dropout_transition=0:normalize=0', plan.filter_complex)
        self.assertIn('adelay=400:all=1', plan.filter_complex)
        self.assertIn('adelay=1200:all=1', plan.filter_complex)
        self.assertIn('volume=0.400', plan.filter_complex)
        self.assertIn('[aout]', plan.filter_complex)

    def test_loop_bed_is_trimmed_to_the_picture(self):
        plan = build_mix_plan([
            {'file': 'bed.wav', 'start_ms': 2800, 'volume': 0.35, 'loop': True, 'loop_samples': 32000},
        ], 5.0)
        self.assertIn('aloop=loop=-1:size=32000', plan.filter_complex)
        self.assertIn('atrim=0:', plan.filter_complex)
        self.assertIn('adelay=2800:all=1', plan.filter_complex)
        self.assertIn('volume=0.350', plan.filter_complex)
        self.assertNotIn('amix', plan.filter_complex)

    def test_missing_audio_still_builds_a_silent_bed(self):
        plan = build_mix_plan([], 1.5)
        self.assertIn('anullsrc=r=48000:cl=stereo', plan.input_args)
        self.assertNotIn('amix', plan.filter_complex)
        self.assertIn('[aout]', plan.filter_complex)
        self.assertEqual(plan.duration, 1.5)

    def test_encode_flags_match_the_delivery_profile(self):
        plan = build_mix_plan([], 1.0)
        command = build_ffmpeg_command('ffmpeg', Path('in.avi'), plan, Path('out.mp4'))
        for flag in ('libx264', 'medium', '20', 'yuv420p', '+faststart', 'aac', '48000'):
            self.assertIn(flag, command)
        self.assertEqual(VIDEO_ENCODE[VIDEO_ENCODE.index('-crf') + 1], '20')
        self.assertEqual(VIDEO_ENCODE[VIDEO_ENCODE.index('-preset') + 1], 'medium')

    def test_mux_one_second_mp4_when_ffmpeg_is_installed(self):
        binary = _ffmpeg()
        if binary is None:
            self.skipTest('ffmpeg unavailable')
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            avi = work / 'tiny.avi'
            voice = work / 'voice.wav'
            bed = work / 'bed.wav'
            stage_path = work / 'stage.json'
            output = work / 'out.mp4'
            silent = work / 'silent.mp4'
            _tone(voice, 0.6)
            _tone(bed, 0.4, rate=8000)
            source = subprocess.run(
                [binary, '-y', '-nostdin', '-f', 'lavfi', '-i', 'color=c=0x203040:s=160x120:r=24:d=1',
                 '-c:v', 'mpeg4', '-q:v', '3', '-an', str(avi)],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            if source.returncode != 0 or not avi.is_file():
                self.fail(source.stderr[-1500:])
            picture = probe_video_duration(binary, avi)
            stage_path.write_text(json.dumps({
                'fps': 24,
                'total_frames': 24,
                'audio': [
                    {'file': str(voice), 'start_ms': 200, 'volume': 1.0, 'loop': False},
                    {'file': str(bed), 'start_ms': 0, 'volume': 0.25, 'loop': True},
                ],
            }), encoding='utf-8')
            mux(avi, stage_path, output, binary)
            self._assert_playable(binary, output, picture)
            stage_path.write_text(json.dumps({'audio': []}), encoding='utf-8')
            mux(avi, stage_path, silent, binary)
            self._assert_playable(binary, silent, picture)

    def _assert_playable(self, binary: str, path: Path, picture: float) -> None:
        self.assertTrue(path.is_file())
        self.assertGreater(path.stat().st_size, 1000)
        info = subprocess.run(
            [binary, '-hide_banner', '-nostdin', '-i', str(path)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        text = info.stderr
        self.assertIn('Video: h264', text)
        self.assertIn('yuv420p', text)
        self.assertIn('Audio: aac', text)
        self.assertIn('48000 Hz', text)
        decoded = subprocess.run(
            [binary, '-v', 'error', '-nostdin', '-i', str(path), '-f', 'null', '-'],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(decoded.returncode, 0, decoded.stderr[-1500:])
        duration = probe_video_duration(binary, path)
        self.assertAlmostEqual(duration, picture, delta=0.05)


if __name__ == '__main__':
    unittest.main()
