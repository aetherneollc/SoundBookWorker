"""Mouth cues from a line's loudness: steady shapes, vowel hints, and no flicker."""
from __future__ import annotations

import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from renderer import voice_cues
from renderer.voice_cues import (
    MAX_D_RATIO,
    MIN_HOLD_SEC,
    _energy,
    _syllable_plan,
    speech_cues,
    stabilize_cues,
)

RATE = 16000


def _write_tone(path: Path, segments, rate: int = RATE, channels: int = 1) -> float:
    """Write a 180 Hz tone whose amplitude follows ``(seconds, amplitude)`` segments."""
    frames = bytearray()
    index = 0
    for seconds, amplitude in segments:
        for _ in range(round(seconds * rate)):
            sample = int(amplitude * math.sin(2 * math.pi * 180 * index / rate) * 32767)
            frames.extend(struct.pack('<h', sample) * channels)
            index += 1
    with wave.open(str(path), 'wb') as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames)
    return index / rate


def _syllables(levels, seconds: float = 0.25):
    """One loudness bump per syllable: a soft onset, a nucleus, a soft tail."""
    segments = []
    for level in levels:
        segments += [(seconds * 0.2, level * 0.5), (seconds * 0.6, level), (seconds * 0.2, level * 0.5)]
    return segments


def _inner(cues):
    """Cues without the rests that open and close the line."""
    return [cue for index, cue in enumerate(cues)
            if not (cue['value'] == 'X' and index in (0, len(cues) - 1))]


def _share(cues, value: str) -> float:
    spoken = sum(cue['end'] - cue['start'] for cue in cues if cue['value'] != 'X')
    return sum(cue['end'] - cue['start'] for cue in cues if cue['value'] == value) / spoken


class EnergyCueTest(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.wav = Path(self._directory.name) / 'line.wav'

    def cues(self, segments, text: str = '', **kwargs):
        self.duration = _write_tone(self.wav, segments, **kwargs)
        return _energy(self.wav, text)

    def assert_well_formed(self, cues):
        self.assertTrue(cues)
        self.assertEqual(cues[0]['start'], 0)
        self.assertAlmostEqual(cues[-1]['end'], self.duration, delta=0.03)
        for cue in cues:
            self.assertEqual(set(cue), {'start', 'end', 'value'})
            self.assertIsInstance(cue['start'], (int, float))
            self.assertIsInstance(cue['end'], float)
            self.assertIn(cue['value'], tuple('XABCDEF'))
            self.assertGreater(cue['end'], cue['start'])
        for left, right in zip(cues, cues[1:]):
            self.assertEqual(left['end'], right['start'])
            self.assertNotEqual(left['value'], right['value'])

    def test_cues_keep_the_start_end_value_shape_and_cover_the_line(self):
        cues = self.cues([(0.2, 0)] + _syllables([0.5, 0.8, 0.4, 0.9]) + [(0.3, 0)])
        self.assert_well_formed(cues)
        self.assertEqual(cues[0]['value'], 'X')
        self.assertEqual(cues[-1]['value'], 'X')

    def test_silence_is_one_rest(self):
        cues = self.cues([(1.0, 0)])
        self.assertEqual(cues, [{'start': 0.0, 'end': 1.0, 'value': 'X'}])

    def test_empty_audio_has_no_cues(self):
        self.assertEqual(self.cues([]), [])

    def test_jittery_loudness_never_flashes_a_shape(self):
        # 50 ms steps between very different levels: the old 50 ms windows twitched on every one.
        steps = [0.9, 0.2, 0.6, 0.1, 1.0, 0.3, 0.7, 0.15] * 8
        cues = self.cues([(0.1, 0)] + [(0.05, level) for level in steps] + [(0.2, 0)])
        self.assert_well_formed(cues)
        for cue in _inner(cues):
            self.assertGreaterEqual(cue['end'] - cue['start'], MIN_HOLD_SEC - 1e-6, cue)
        for fps in (24, 25, 30):
            frames = [round(cue['start'] * fps) for cue in cues] + [round(cues[-1]['end'] * fps)]
            lengths = [after - before for before, after in zip(frames, frames[1:])]
            self.assertGreaterEqual(min(lengths[1:-1]), 3, f'{fps} fps: {lengths}')

    def test_a_brief_dip_keeps_the_mouth_open_and_a_pause_closes_it(self):
        speech = _syllables([0.7, 0.7, 0.7], 0.2)
        dipped = self.cues([(0.2, 0)] + speech + [(0.08, 0)] + speech + [(0.2, 0)])
        self.assertNotIn('X', [cue['value'] for cue in _inner(dipped)])

        paused = self.cues([(0.2, 0)] + speech + [(0.4, 0)] + speech + [(0.2, 0)])
        rests = [cue for cue in _inner(paused) if cue['value'] == 'X']
        self.assertEqual(len(rests), 1)
        self.assertAlmostEqual(rests[0]['start'], 0.8, delta=0.06)
        self.assertGreaterEqual(rests[0]['end'] - rests[0]['start'], MIN_HOLD_SEC)

    def test_a_click_does_not_open_the_mouth_but_a_short_word_does(self):
        click = self.cues([(0.5, 0), (0.04, 0.8), (0.5, 0)])
        self.assertEqual([cue['value'] for cue in click], ['X'])

        word = self.cues([(0.5, 0), (0.12, 0.8), (0.5, 0)])
        spoken = [cue for cue in word if cue['value'] != 'X']
        self.assertEqual(len(spoken), 1)
        self.assertGreaterEqual(spoken[0]['end'] - spoken[0]['start'], MIN_HOLD_SEC - 1e-6)

    def test_the_gate_rises_above_a_noisy_room(self):
        # Room tone at 15% of the speech level would count as speech under a fixed 10% gate.
        speech = [(seconds, max(level, 0.03)) for seconds, level in _syllables([0.2, 0.2, 0.2], 0.2)]
        cues = self.cues([(0.5, 0.03)] + speech + [(0.6, 0.03)])
        self.assertEqual(cues[0]['value'], 'X')
        self.assertAlmostEqual(cues[0]['end'], 0.5, delta=0.08)
        self.assertEqual(cues[-1]['value'], 'X')
        self.assertGreater(cues[-1]['end'] - cues[-1]['start'], 0.45)

    def test_wide_open_is_kept_for_the_accents(self):
        even = self.cues([(0.1, 0)] + _syllables([0.8] * 12) + [(0.1, 0)])
        self.assertLessEqual(_share(even, 'D'), MAX_D_RATIO + 1e-6)

        accented = self.cues([(0.1, 0)] + _syllables([0.4, 0.45, 0.4, 1.0, 0.4, 0.45, 0.4, 0.42]) + [(0.1, 0)])
        wide = [cue for cue in accented if cue['value'] == 'D']
        self.assertEqual(len(wide), 1)
        self.assertAlmostEqual((wide[0]['start'] + wide[0]['end']) / 2, 0.1 + 3.5 * 0.25, delta=0.1)
        self.assertLessEqual(_share(accented, 'D'), MAX_D_RATIO + 1e-6)

    def test_chinese_vowels_steer_the_shape(self):
        levels = [0.4, 0.45, 0.4, 1.0, 0.4, 0.45, 0.4, 0.42]
        segments = [(0.1, 0)] + _syllables(levels) + [(0.1, 0)]

        rounded = {cue['value'] for cue in self.cues(segments, '呜呜呜呜呜呜呜呜')}
        self.assertLessEqual(rounded, {'X', 'B', 'E'})
        self.assertIn('E', rounded)

        narrow = {cue['value'] for cue in self.cues(segments, '一一一一一一一一')}
        self.assertLessEqual(narrow, {'X', 'B', 'C'})

        opened = {cue['value'] for cue in self.cues(segments, '啊啊啊啊啊啊啊啊')}
        self.assertIn('D', opened)
        self.assertNotIn('E', opened)

    def test_only_the_open_vowel_under_the_accent_goes_wide(self):
        levels = [0.4, 0.45, 0.4, 1.0, 0.4, 0.45, 0.4, 0.42]
        segments = [(0.1, 0)] + _syllables(levels) + [(0.1, 0)]
        # The accent lands on the fourth syllable: 大 (a) opens wide, 速 (u) only rounds.
        self.assertIn('D', {cue['value'] for cue in self.cues(segments, '你去不大一起去你')})
        self.assertNotIn('D', {cue['value'] for cue in self.cues(segments, '你去不速一起去你')})

    def test_other_sample_rates_and_stereo_keep_real_time(self):
        segments = [(0.2, 0)] + _syllables([0.6, 0.6, 0.6]) + [(0.25, 0)]
        for rate, channels in ((22050, 1), (48000, 2)):
            cues = self.cues(segments, rate=rate, channels=channels)
            self.assert_well_formed(cues)
            self.assertAlmostEqual(cues[0]['end'], 0.2, delta=0.06)
            self.assertAlmostEqual(cues[-1]['start'], 0.95, delta=0.06)

    def test_speech_cues_uses_the_energy_path_without_rhubarb(self):
        _write_tone(self.wav, [(0.1, 0)] + _syllables([0.5, 0.9, 0.5]) + [(0.1, 0)])
        with mock.patch.object(voice_cues, '_rhubarb', return_value=None):
            self.assertEqual(speech_cues(self.wav, '你好啊', 'zh'), _energy(self.wav, '你好啊', 'zh'))
            # Chinese vowel hints do not apply to another language.
            self.assertEqual(speech_cues(self.wav, '你好啊', 'en'), _energy(self.wav, '', 'zh'))


class SyllablePlanTest(unittest.TestCase):
    def test_text_becomes_vowel_classes_grouped_by_punctuation(self):
        self.assertEqual(
            _syllable_plan('你好，AT-1号！2003'),
            [['i', 'a'], ['', '', 'i', 'a'], ['i', 'i', 'i', 'a']],
        )

    def test_finals_follow_the_mouth_not_the_spelling(self):
        # 验 (ian) and 月 (üe) are mid vowels, 我 (uo) and 流 (iu) are rounded.
        self.assertEqual(_syllable_plan('验月我流卡'), [['i', 'i', 'o', 'o', 'a']])

    def test_words_and_blank_text(self):
        self.assertEqual(_syllable_plan('hello world'), [['', '', '']])
        self.assertEqual(_syllable_plan(''), [])
        self.assertEqual(_syllable_plan('……！'), [])


class StabilizeTest(unittest.TestCase):
    FLICKER = [
        {'start': 0.0, 'end': 0.08, 'value': 'X'},
        {'start': 0.08, 'end': 0.12, 'value': 'B'},
        {'start': 0.12, 'end': 0.16, 'value': 'D'},
        {'start': 0.16, 'end': 0.50, 'value': 'C'},
        {'start': 0.50, 'end': 0.54, 'value': 'X'},
        {'start': 0.54, 'end': 0.90, 'value': 'C'},
        {'start': 0.90, 'end': 0.94, 'value': 'A'},
        {'start': 0.94, 'end': 1.02, 'value': 'F'},
        {'start': 1.02, 'end': 1.40, 'value': 'B'},
        {'start': 1.40, 'end': 1.46, 'value': 'X'},
    ]

    def assert_steady(self, cues, source):
        self.assertEqual(cues[0]['start'], source[0]['start'])
        self.assertEqual(cues[-1]['end'], source[-1]['end'])
        for left, right in zip(cues, cues[1:]):
            self.assertEqual(left['end'], right['start'])
            self.assertNotEqual(left['value'], right['value'])
        for cue in _inner(cues):
            self.assertEqual(set(cue), {'start', 'end', 'value'})
            self.assertGreaterEqual(cue['end'] - cue['start'], MIN_HOLD_SEC - 1e-6, cue)

    def test_one_frame_shapes_and_closures_are_merged(self):
        cues = stabilize_cues(self.FLICKER)
        self.assert_steady(cues, self.FLICKER)
        # The 40 ms closure at 0.50 is gone: the mouth stays on C straight through it.
        self.assertEqual([cue['value'] for cue in cues], ['X', 'C', 'B', 'X'])
        self.assertEqual(cues[0]['end'], 0.08)  # The opening rest keeps its exact length.
        self.assertEqual(cues[-1]['start'], 1.40)

    def test_steady_cues_pass_through_unchanged(self):
        steady = [
            {'start': 0.0, 'end': 0.2, 'value': 'X'},
            {'start': 0.2, 'end': 0.5, 'value': 'B'},
            {'start': 0.5, 'end': 0.9, 'value': 'D'},
            {'start': 0.9, 'end': 1.2, 'value': 'X'},
            {'start': 1.2, 'end': 1.5, 'value': 'G'},
            {'start': 1.5, 'end': 1.6, 'value': 'X'},
        ]
        self.assertEqual(stabilize_cues(steady), steady)
        self.assertEqual(stabilize_cues(stabilize_cues(self.FLICKER)), stabilize_cues(self.FLICKER))

    def test_a_lone_short_sound_borrows_from_the_rest_or_is_dropped(self):
        word = [
            {'start': 0.0, 'end': 0.4, 'value': 'X'},
            {'start': 0.4, 'end': 0.5, 'value': 'C'},
            {'start': 0.5, 'end': 1.2, 'value': 'X'},
        ]
        held = stabilize_cues(word)
        self.assertEqual([cue['value'] for cue in held], ['X', 'C', 'X'])
        self.assertAlmostEqual(held[1]['end'] - held[1]['start'], MIN_HOLD_SEC)
        self.assertEqual(held[1]['start'], 0.4)

        blip = [dict(word[0]), {'start': 0.4, 'end': 0.44, 'value': 'C'}, {'start': 0.44, 'end': 1.2, 'value': 'X'}]
        self.assertEqual(stabilize_cues(blip), [{'start': 0.0, 'end': 1.2, 'value': 'X'}])

    def test_rhubarb_output_is_steadied_for_chinese_only(self):
        with mock.patch.object(voice_cues, '_rhubarb', return_value=[dict(cue) for cue in self.FLICKER]):
            chinese = speech_cues(Path('line.wav'), '你好', 'zh-CN')
            english = speech_cues(Path('line.wav'), 'hello', 'en')
        self.assert_steady(chinese, self.FLICKER)
        self.assertEqual(english, self.FLICKER)


if __name__ == '__main__':
    unittest.main()
