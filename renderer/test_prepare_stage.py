"""Full prepare_stage path: synthetic manifest, voice, and an A-pose puppet."""
from __future__ import annotations

import json
import math
import os
import struct
import tempfile
import unittest
import wave
from pathlib import Path

import cv2

from renderer.director import validate_stage
from renderer.prepare_stage import prepare
from renderer.test_rig_mesh import _make_synthetic_character
from renderer.timeline_common import frame, parse_srt


def _write_wav(path: Path, seconds: float = 2.0, rate: int = 16000) -> None:
    with wave.open(str(path), 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        frames = bytearray()
        total = int(rate * seconds)
        for index in range(total):
            t = index / rate
            audible = 0.35 < t < 0.9 or 1.15 < t < 1.7
            env = 0.55 if audible else 0.02
            sample = int(max(-1.0, min(1.0, env * math.sin(2 * math.pi * 180 * t))) * 32767)
            frames.extend(struct.pack('<h', sample))
        handle.writeframes(frames)


class PrepareStageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Silhouette geometry keeps the synthetic puppet offline. Production still uses RTMPose.
        os.environ['SOUNDBOOK_SKIP_RTMLIB'] = '1'
        os.environ['FACE_ANCHORS_DISABLE_RTMLIB'] = '1'
        cls._temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._temporary.cleanup)
        cls.work = Path(cls._temporary.name)
        cls.master = cls.work / 'linlan.png'
        cls.side = cls.work / 'linlan-3q.png'
        cls.voice = cls.work / 'linlan.wav'
        cls.bed = cls.work / 'room.wav'
        _make_synthetic_character(cls.master)
        _make_synthetic_character(cls.side)
        side_image = cv2.imread(str(cls.side), cv2.IMREAD_COLOR)
        side_image[180:188, 176:184] = (220, 125, 70)
        cv2.imwrite(str(cls.side), side_image)
        _write_wav(cls.voice, 2.0)
        _write_wav(cls.bed, 1.0)
        cls.cache = cls.work / 'rigs'
        cls.manifest_path = cls.work / 'manifest.json'
        cls.stage_path = cls.work / 'stage.json'
        cls.manifest = {
            'version': 1,
            'renderer': 'godot2d',
            'width': 1280,
            'height': 720,
            'fps': 24,
            'timeline_mode': 'voice_locked',
            'language': 'zh',
            'episode_number': 1,
            'episode_title': '月面来信',
            'episode_subtitle': '第一章',
            'character_rigs': [{
                'id': 'linlan',
                'name': '林岚',
                'master_file': str(cls.master),
                'side_master_file': str(cls.side),
                'side_native_facing': 'right',
                'stature_scale': 0.93,
            }],
            'shots': [{
                'scene_number': 1,
                'shot_number': 1,
                'duration': 4,
                'story_motion': {
                    'framing': 'medium',
                    'actors': [{
                        'name': '林岚',
                        'character_id': 'linlan',
                        'action': 'talk',
                        'from_x': 0.38,
                        'to_x': 0.38,
                        'facing': 'right',
                    }],
                },
            }],
            'audio_tracks': [
                {
                    'file': str(cls.voice),
                    'scene_number': 1,
                    'dialogue_number': 1,
                    'speaker': '林岚',
                    'caption_text': '林岚：先看月面传回的数据。',
                    'volume': 1,
                },
                {
                    'file': str(cls.bed),
                    'loop': True,
                    'volume': 0.35,
                },
            ],
            'cta_card': {'name': '下集见', 'tagline': '关注更新'},
        }
        cls.manifest_path.write_text(json.dumps(cls.manifest, ensure_ascii=False), encoding='utf-8')

    def test_frame_and_srt_match_the_remotion_clock(self):
        self.assertEqual(frame(2.8, 24), 67)
        self.assertEqual(frame(3.8, 24), 91)
        srt = self.work / 'line.srt'
        srt.write_text('1\n00:00:01,000 --> 00:00:02,500\n你好\n\n', encoding='utf-8')
        cues = parse_srt(srt, 2.8, 24)
        self.assertEqual(cues, [{'from': frame(3.8, 24), 'to': frame(5.3, 24), 'text': '你好'}])

    def test_rejects_canvas_other_than_720p(self):
        wide = self.work / 'wide.json'
        wide.write_text(json.dumps({
            'version': 1, 'width': 1920, 'height': 1080, 'fps': 24,
            'shots': [{'duration': 1}],
        }), encoding='utf-8')
        with self.assertRaises(ValueError) as caught:
            prepare(wide, self.work / 'nope.json', self.cache)
        self.assertIn('1280x720', str(caught.exception))

    def test_voice_locked_stage_is_playable_and_cached(self):
        stage = prepare(self.manifest_path, self.stage_path, self.cache)
        self.assertEqual(validate_stage(stage), [])
        self.assertEqual(json.loads(self.stage_path.read_text(encoding='utf-8'))['version'], 1)
        self.assertEqual((stage['width'], stage['height'], stage['fps']), (1280, 720, 24))
        self.assertEqual(stage['intro']['frames'], frame(2.8, 24))
        self.assertEqual(stage['intro']['episode_number'], 1)
        self.assertEqual(stage['intro']['title'], '月面来信')
        self.assertEqual(stage['intro']['subtitle'], '第一章')
        self.assertEqual(stage['cta']['title'], '下集见')
        self.assertEqual(stage['cta']['subtitle'], '关注更新')
        self.assertEqual(stage['cta']['frames'], frame(3.8, 24))
        self.assertGreater(stage['total_frames'], stage['intro']['frames'] + stage['cta']['frames'])

        rig = stage['rigs']['linlan']
        self.assertAlmostEqual(rig['stature_scale'], 0.93)
        rig_path = Path(rig['rig_json'])
        side_rig_path = Path(rig['side_rig_json'])
        self.assertEqual(rig['side_native_facing'], 'right')
        self.assertNotEqual(rig_path, side_rig_path)
        self.assertTrue(rig_path.is_file())
        self.assertTrue(side_rig_path.is_file())
        document = json.loads(rig_path.read_text(encoding='utf-8'))
        side_document = json.loads(side_rig_path.read_text(encoding='utf-8'))
        self.assertTrue(document['bones'])
        self.assertTrue(document['groups'])
        self.assertTrue((rig_path.parent / document['texture']).is_file())
        self.assertTrue(side_document['bones'])
        self.assertTrue(side_document['groups'])
        self.assertTrue((side_rig_path.parent / side_document['texture']).is_file())

        self.assertTrue(stage['captions'])
        self.assertIn('月面', stage['captions'][0]['text'])
        self.assertLess(stage['captions'][0]['from'], stage['captions'][0]['to'])
        self.assertEqual(stage['speech'][0]['actor'], 'linlan')
        self.assertEqual(stage['speech'][0]['cues'][0][0], 0)
        self.assertTrue(all(cue[1] in 'XABCDEF' for cue in stage['speech'][0]['cues']))
        self.assertEqual(stage['shots'][0]['actors'][0]['id'], 'linlan')

        voices = [item for item in stage['audio'] if not item['loop'] and 'intro-chime' not in item['file']]
        beds = [item for item in stage['audio'] if item['loop']]
        self.assertEqual(voices[0]['start_ms'], 2800)
        self.assertEqual(beds[0]['start_ms'], 2800)
        self.assertAlmostEqual(beds[0]['volume'], 0.35)

        stamped = rig_path.stat().st_mtime_ns
        side_stamped = side_rig_path.stat().st_mtime_ns
        again = prepare(self.manifest_path, self.stage_path, self.cache)
        self.assertEqual(validate_stage(again), [])
        self.assertEqual(rig_path.stat().st_mtime_ns, stamped)
        self.assertEqual(side_rig_path.stat().st_mtime_ns, side_stamped)

        quiet = dict(self.manifest)
        quiet.pop('episode_title')
        quiet.pop('episode_subtitle')
        quiet.pop('cta_card')
        quiet['audio_tracks'] = []
        quiet['timeline_mode'] = 'authored'
        quiet_path = self.work / 'quiet.json'
        quiet_path.write_text(json.dumps(quiet, ensure_ascii=False), encoding='utf-8')
        silent = prepare(quiet_path, self.work / 'silent-stage.json', self.cache)
        self.assertEqual(validate_stage(silent), [])
        self.assertEqual(silent['audio'], [])
        self.assertEqual(silent['intro']['frames'], 0)
        self.assertEqual(rig_path.stat().st_mtime_ns, stamped)

        front_only = json.loads(json.dumps(self.manifest, ensure_ascii=False))
        front_only['character_rigs'][0].pop('side_master_file')
        front_only['character_rigs'][0].pop('side_native_facing')
        front_only['audio_tracks'] = []
        front_only['timeline_mode'] = 'authored'
        front_only_path = self.work / 'front-only.json'
        front_only_path.write_text(json.dumps(front_only, ensure_ascii=False), encoding='utf-8')
        legacy = prepare(front_only_path, self.work / 'front-only-stage.json', self.cache)
        self.assertEqual(validate_stage(legacy), [])
        self.assertNotIn('side_rig_json', legacy['rigs']['linlan'])


if __name__ == '__main__':
    unittest.main()
