"""prepare_stage3d: voice-locked six-shot timeline, blocking, cameras, and layout checks."""
from __future__ import annotations

import io
import json
import math
import struct
import tempfile
import unittest
import wave
from contextlib import redirect_stdout
from pathlib import Path

from renderer import prepare_stage3d as p3
from renderer.timeline_common import frame


def _write_tone(path: Path, seconds: float, rate: int = 24000) -> None:
    with wave.open(str(path), 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b''.join(
            struct.pack('<h', int(0.4 * math.sin(2 * math.pi * 200 * n / rate) * 32767))
            for n in range(int(seconds * rate))
        ))


def _manifest(directory: Path, **cast) -> Path:
    doc = p3.demo_manifest_doc()
    for index, track in enumerate(doc['audio_tracks']):
        wav = directory / f'line{index}.wav'
        _write_tone(wav, 0.5 + 0.13 * len(track['caption_text']))
        track['file'] = wav.name
    for actor_id, model in cast.items():
        doc['cast'][actor_id]['model'] = model
    path = directory / 'manifest.json'
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')
    return path


def _prepare(manifest: Path, output: Path) -> dict:
    with redirect_stdout(io.StringIO()):
        return p3.prepare(manifest, output, check_files=False)


class PrepareStage3DTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.manifest = _manifest(self.dir)
        self.stage = _prepare(self.manifest, self.dir / 'out' / 'stage3d.json')
        self.shots = {shot['type']: shot for shot in self.stage['shots']}
        self.fps = self.stage['fps']

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_six_contiguous_shots_about_thirty_seconds(self) -> None:
        shots = self.stage['shots']
        self.assertEqual([s['type'] for s in shots], list(p3.SHOT_TYPES))
        cursor = 0
        for shot in shots:
            self.assertEqual(shot['from'], cursor)
            cursor += shot['frames']
        self.assertEqual(cursor, self.stage['total_frames'])
        self.assertTrue(24.0 <= cursor / self.fps <= 36.0, cursor / self.fps)
        written = json.loads((self.dir / 'out' / 'stage3d.json').read_text(encoding='utf-8'))
        self.assertEqual(written['total_frames'], self.stage['total_frames'])
        self.assertEqual(written['renderer'], 'godot3d')

    def test_every_line_is_voice_locked_inside_its_shot(self) -> None:
        voices = [a for a in self.stage['audio'] if not a['loop'] and 'line' in Path(a['file']).name]
        self.assertEqual(len(voices), len(p3.DEMO_LINES))
        self.assertEqual(len(self.stage['captions']), len(p3.DEMO_LINES))
        for shot in self.stage['shots']:
            end = shot['from'] + shot['frames']
            for line in shot['lines']:
                self.assertGreaterEqual(line['from'], shot['from'] + shot['voice_lead_frames'])
                self.assertLessEqual(line['to'], end)
                caption = next(c for c in self.stage['captions'] if c['text'] == line['text'])
                self.assertEqual(caption['from'], line['from'])
                self.assertEqual(frame(line['start_ms'] / 1000, self.fps), line['from'])

    def test_lines_in_one_shot_do_not_overlap(self) -> None:
        two_shot = self.shots['two_shot']['lines']
        self.assertEqual([line['actor'] for line in two_shot], ['chen', 'linlan'])
        self.assertLess(two_shot[0]['to'], two_shot[1]['from'])

    def test_linlan_walks_in_and_arrives_before_chen_answers(self) -> None:
        linlan = self.stage['actors']['linlan']
        move = linlan['moves'][0]
        self.assertGreater(linlan['start']['pos'][1], self.stage['set']['room']['max'][2], 'starts in the doorway corridor')
        self.assertLess(move['from'], self.shots['wide_follow']['frames'])
        chen_line = self.shots['two_shot']['lines'][0]
        self.assertLessEqual(move['to'], chen_line['from'])
        self.assertGreater(move['to'], self.shots['two_shot']['from'], 'the walk carries across the first cut')

    def test_sensor_changes_hands_inside_the_over_shoulder_shot(self) -> None:
        shot = self.shots['over_shoulder']
        owners = self.stage['props']['sensor']['owners']
        self.assertEqual(owners[0], {'from': 0, 'actor': 'chen'})
        handoff = owners[1]
        self.assertEqual(handoff['actor'], 'linlan')
        lo, hi = handoff['handoff_window']
        self.assertTrue(shot['from'] <= lo < hi < shot['from'] + shot['frames'])
        for actor_id in ('chen', 'linlan'):
            reach = self.stage['actors'][actor_id]['reaches'][0]
            self.assertLessEqual(reach['from'], lo, actor_id)
            self.assertGreaterEqual(reach['to'], hi, actor_id)
            self.assertEqual(reach['target'], 'linlan' if actor_id == 'chen' else 'chen')

    def test_rover_hidden_until_it_drives_in_from_the_right(self) -> None:
        rover = self.stage['rover']
        shot = self.shots['rover_medium']
        self.assertEqual(rover['visibility'], [{'from': 0, 'visible': False}, {'from': shot['from'], 'visible': True}])
        move = rover['moves'][0]
        self.assertTrue(shot['from'] < move['from'] < move['to'] < shot['from'] + shot['frames'])
        self.assertGreater(rover['start']['pos'][0], move['points'][-1][0])
        self.assertLess(move['points'][-1][1], -2.5, 'stops by the window')
        self.assertEqual(rover['source'], 'kenney_fallback')
        speaking = [s for s in self.stage['speech'] if s['actor'] == 'at1']
        self.assertEqual(len(speaking), 1)

    def test_first_person_shot_hides_linlan_behind_a_pov_camera(self) -> None:
        shot = self.shots['first_person']
        self.assertEqual(shot['first_person'], 'linlan')
        cam = shot['cameras'][0]
        self.assertEqual(cam['mode'], 'pov')
        self.assertEqual(cam['actor'], 'linlan')
        self.assertLess(cam['look_at'][2], self.stage['set']['room']['min'][2] - 5, 'looks out of the window')
        for other in self.stage['shots']:
            if other is not shot:
                self.assertEqual(other['first_person'], '')

    def test_closeup_frames_linlan_chest_up_while_she_nods(self) -> None:
        shot = self.shots['closeup']
        cam = shot['cameras'][0]
        linlan = self.stage['set']['marks']['linlan']
        distance = math.hypot(cam['pos_to'][0] - linlan[0], cam['pos_to'][2] - linlan[1])
        self.assertTrue(0.8 <= distance <= 1.4, distance)
        covered = 2 * distance * math.tan(math.radians(cam['fov'] / 2))
        self.assertLess(covered, 0.8, 'chest-up: about head to chest in frame')
        chen = self.stage['actors']['chen']
        chen_end = chen['moves'][-1]['points'][-1]
        self.assertLess(chen['moves'][-1]['to'], shot['from'], 'Chen is back at the console before the close-up')
        to_her = math.atan2(linlan[1] - cam['pos_to'][2], linlan[0] - cam['pos_to'][0])
        to_him = math.atan2(chen_end[1] - cam['pos_to'][2], chen_end[0] - cam['pos_to'][0])
        half_h = math.degrees(math.atan(math.tan(math.radians(cam['fov'] / 2)) * 16 / 9))
        off_axis = abs(math.degrees((to_him - to_her + math.pi) % (2 * math.pi) - math.pi))
        self.assertGreater(off_axis, half_h + 10, 'Chen stays out of the close-up')
        nod = self.stage['actors']['linlan']['nods'][0]
        line = shot['lines'][0]
        self.assertTrue(shot['from'] <= nod['from'] <= line['from'] < nod['to'])

    def test_layout_cameras_and_paths_validate(self) -> None:
        self.assertEqual(p3.validate_stage3d(self.stage, check_files=False), [])
        bounds = self.stage['set']['camera_bounds']
        for shot in self.stage['shots']:
            for cam in shot['cameras']:
                if 'pos' in cam:
                    self.assertTrue(p3._inside_bounds(cam['pos'], bounds), (shot['id'], cam['pos']))

    def test_validator_reports_overlap_and_blocked_walk(self) -> None:
        broken = json.loads(json.dumps(self.stage))
        broken['set']['props'].append({'id': 'in_the_way', 'kind': 'crate', 'pos': [-0.3, 1.8], 'size': [1.0, 1.0, 1.0]})
        broken['set']['props'].append({'id': 'on_console', 'kind': 'crate', 'pos': [-3.25, -3.8], 'size': [0.8, 0.8, 0.8]})
        problems = p3.validate_stage3d(broken, check_files=False)
        self.assertTrue(any('linlan path runs into prop in_the_way' in p for p in problems), problems)
        self.assertTrue(any('on_console' in p and 'overlap' in p for p in problems), problems)

    def test_walk_speed_matches_a_natural_stride(self) -> None:
        linlan = self.stage['actors']['linlan']
        move = linlan['moves'][0]
        points = [tuple(linlan['start']['pos'])] + [tuple(p) for p in move['points']]
        length = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
        speed = length / ((move['to'] - move['from']) / self.fps)
        self.assertAlmostEqual(speed, p3.WALK_SPEED, delta=0.05)

    def test_cc0_fallback_cast_and_animation_library(self) -> None:
        for actor_id in ('linlan', 'chen'):
            actor = self.stage['actors'][actor_id]
            self.assertEqual(actor['source'], 'cc0_fallback')
            self.assertTrue(actor['model'].endswith('.gltf'))
            self.assertTrue(actor['animation']['library'].endswith('UAL1_Standard.glb'))
            self.assertIsNot(actor['look'].get('uniform', True), False)
            self.assertTrue(actor['look']['hair_model'].endswith('.gltf'))
        self.assertTrue(self.stage['actors']['linlan']['look']['ponytail'])
        self.assertTrue(self.stage['actors']['chen']['look']['badge'])

    def test_generated_model_replaces_the_fallback(self) -> None:
        model = self.dir / 'linlan_rigged.glb'
        model.write_bytes(b'glTF')
        stage = _prepare(_manifest(self.dir, linlan=model.name), self.dir / 'gen' / 'stage3d.json')
        linlan = stage['actors']['linlan']
        self.assertEqual(linlan['source'], 'fal_generated')
        self.assertEqual(Path(linlan['model']).resolve(), model.resolve())
        self.assertFalse(linlan['look']['uniform'])
        self.assertEqual(stage['actors']['chen']['source'], 'cc0_fallback')

    def test_ambient_bed_is_written_and_looped(self) -> None:
        hum = next(a for a in self.stage['audio'] if a['loop'])
        self.assertTrue(Path(hum['file']).is_file())
        with wave.open(hum['file']) as handle:
            self.assertGreater(handle.getnframes(), handle.getframerate() * 4)
        whirr = next(a for a in self.stage['audio'] if Path(a['file']).name == 'rover_whirr.wav')
        self.assertEqual(frame(whirr['start_ms'] / 1000, self.fps), self.stage['rover']['moves'][0]['from'])

    def test_cli_positional_and_flag_forms_agree(self) -> None:
        a, b = self.dir / 'a' / 'stage3d.json', self.dir / 'b' / 'stage3d.json'
        original = p3.validate_stage3d
        p3.validate_stage3d = lambda stage, check_files=True: original(stage, check_files=False)
        try:
            with redirect_stdout(io.StringIO()):
                p3.main([str(self.manifest), str(a)])
                p3.main(['--manifest', str(self.manifest), '--output', str(b)])
        finally:
            p3.validate_stage3d = original
        first = json.loads(a.read_text(encoding='utf-8'))
        second = json.loads(b.read_text(encoding='utf-8'))
        for doc in (first, second):
            doc.pop('audio')
        self.assertEqual(first, second)

    def test_rejects_a_different_shot_list(self) -> None:
        doc = json.loads(self.manifest.read_text(encoding='utf-8'))
        doc['shots'] = doc['shots'][:5]
        self.manifest.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')
        with self.assertRaises(ValueError):
            _prepare(self.manifest, self.dir / 'bad.json')

    def test_demo_manifest_without_tts_still_has_audio_for_every_line(self) -> None:
        path = self.dir / 'demo' / 'manifest.json'
        doc = p3.write_demo_manifest(path, synthesize_audio=False)
        self.assertIn('placeholder', doc['audio_source'])
        for track in doc['audio_tracks']:
            self.assertTrue(Path(track['file']).is_file(), track['file'])
        stage = _prepare(path, self.dir / 'demo' / 'stage3d.json')
        self.assertEqual(len(stage['shots']), 6)


class GeometryTests(unittest.TestCase):
    def test_rotated_footprint_and_distance(self) -> None:
        corners = p3.prop_footprint({'kind': 'crate', 'pos': [0.0, 0.0], 'size': [2.0, 1.0, 1.0], 'yaw_deg': 90.0})
        xs = sorted(round(c[0], 6) for c in corners)
        zs = sorted(round(c[1], 6) for c in corners)
        self.assertEqual((xs[0], xs[-1], zs[0], zs[-1]), (-0.5, 0.5, -1.0, 1.0))
        self.assertAlmostEqual(p3.point_footprint_distance((1.5, 0.0), corners), 1.0)
        self.assertEqual(p3.point_footprint_distance((0.0, 0.0), corners), 0.0)

    def test_overlap(self) -> None:
        a = p3.prop_footprint({'kind': 'crate', 'pos': [0.0, 0.0], 'size': [1.0, 1.0, 1.0]})
        b = p3.prop_footprint({'kind': 'crate', 'pos': [0.9, 0.0], 'size': [1.0, 1.0, 1.0]})
        c = p3.prop_footprint({'kind': 'crate', 'pos': [1.2, 0.0], 'size': [1.0, 1.0, 1.0]})
        self.assertTrue(p3.footprints_overlap(a, b))
        self.assertFalse(p3.footprints_overlap(a, c))
        self.assertTrue(p3.footprints_overlap(a, c, margin=0.25))


if __name__ == '__main__':
    unittest.main()
