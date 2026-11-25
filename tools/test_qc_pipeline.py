"""Pass and fail coverage for every automated 3D QC check."""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.asset_registry import REQUIRED_FACIAL_MORPHS  # noqa: E402
from tools.qc_pipeline import (  # noqa: E402
    HINT_AUDIO,
    HINT_CAMERA,
    HINT_CLOSEUP,
    HINT_CLIP,
    HINT_EXPOSURE,
    HINT_GRIP,
    HINT_GROUND,
    HINT_HANDOFF,
    HINT_OWNER,
    HINT_WALK,
    HINT_WIDEN,
    main,
    parse_probe,
    probe_media,
    run_qc,
)


def _check(report: dict, check_id: str) -> dict:
    for item in report['checks']:
        if item['id'] == check_id:
            return item
    raise AssertionError(check_id)


def _messages(check: dict) -> str:
    return '\n'.join(issue['message'] for issue in check['issues'])


def _frame(index: int, foot_x: float = 0.0) -> dict:
    return {
        'frame': index,
        'actors': {
            'linlan': {
                'left_foot': [foot_x, 0.0, 0.0],
                'right_foot': [foot_x, 0.2, 0.1],
                'pelvis': [foot_x, 0.95, 0.0],
                'clips': ['walk'] if index < 4 else ['talk'],
                'hands': {'left': [-0.3, 1.0, 0.2], 'right': [0.3, 1.0, 0.2]},
            },
        },
        'props': {
            'sensor': {'owner': 'linlan', 'position': [0.32, 1.02, 0.22], 'hand_gap_m': 0.03},
        },
        'rover': {'wheel_min_y': 0.0, 'position': [1.0, 0.2, 1.0]},
        'camera': {
            'position': [0.0, 1.6, 2.0],
            'look_at': [0.0, 1.4, 0.0],
            'wall_distance_m': 1.2,
            'subject': {'id': 'linlan', 'ndc_x': 0.1, 'ndc_y': 0.0, 'occluded': False},
        },
    }


def episode(frames: int = 8, fps: int = 24):
    stage = {
        'version': 1,
        'renderer': 'godot3d',
        'width': 1280,
        'height': 720,
        'fps': fps,
        'total_frames': frames,
        'set': {
            'room': {'min': [-4.0, 0.0, -4.0], 'max': [4.0, 3.0, 4.0]},
            'door': {'x': 0.0, 'width': 1.2, 'height': 2.2, 'corridor_depth': 3.0},
            'camera_bounds': {'corridor_max_z': 7.0},
        },
        'actors': {
            'linlan': {
                'clips': [{'from': 0, 'to': 4, 'clip': 'walk'}],
                'capabilities': {
                    'tier': 'TIER_A',
                    'has_fingers': True,
                    'blendshapes': True,
                    'morph_targets': list(REQUIRED_FACIAL_MORPHS),
                },
            },
        },
        'rover': {'id': 'at1'},
        'props': {'sensor': {'owners': [{'from': 0, 'actor': 'linlan'}]}},
        'shots': [
            {'id': 'wide', 'type': 'wide_follow', 'from': 0, 'frames': 4, 'subject': 'linlan'},
            {'id': 'close', 'type': 'closeup', 'from': 4, 'frames': 4, 'subject': 'linlan'},
        ],
        'captions': [{'from': 0, 'to': frames, 'text': '你好'}],
    }
    telemetry = {'version': 1, 'frames': [_frame(index) for index in range(frames)]}
    return stage, telemetry


def _probe(duration: float, **overrides) -> dict:
    measured = {'duration': duration, 'width': 1280, 'height': 720, 'fps': 24.0, 'has_video': True, 'has_audio': True}
    measured.update(overrides)
    return measured


def _gradient(path: Path) -> None:
    image = np.zeros((32, 32, 3), dtype=np.uint8)
    image[:] = np.linspace(20, 200, 32, dtype=np.uint8)[None, :, None]
    Image.fromarray(image, 'RGB').save(path)


def _solid(path: Path, value: int) -> None:
    image = np.full((16, 16, 3), value, dtype=np.uint8)
    Image.fromarray(image, 'RGB').save(path)


class QcPipelineTests(unittest.TestCase):
    def test_clean_episode_passes_every_check(self):
        stage, telemetry = episode()
        with tempfile.TemporaryDirectory() as tmp:
            still = Path(tmp) / 'frame_0001.png'
            _gradient(still)
            report = run_qc(stage, telemetry, probe=_probe(8 / 24), review_dir=tmp)
        self.assertEqual(report['status'], 'PASS', [(item['id'], item['status'], item['issues']) for item in report['checks']])
        self.assertEqual(report['replan_suggestions'], [])
        self.assertEqual(report['summary'], {'pass': 8, 'warn': 0, 'fail': 0})
        self.assertEqual(
            [item['id'] for item in report['checks']],
            ['grounding', 'foot_slide', 'prop_contact', 'camera_bounds', 'subject_framing', 'audio_sync', 'exposure', 'animation_completeness'],
        )
        self.assertAlmostEqual(report['timeline']['duration_s'], 8 / 24, delta=1e-6)

    def test_grounding_boundaries_pass_and_violations_fail(self):
        stage, telemetry = episode()
        telemetry['frames'][0]['actors']['linlan']['left_foot'][1] = 0.035
        telemetry['frames'][1]['actors']['linlan']['left_foot'][1] = -0.04
        telemetry['frames'][0]['rover']['wheel_min_y'] = 0.03
        telemetry['frames'][1]['rover']['wheel_min_y'] = -0.03
        passed = run_qc(stage, telemetry)
        self.assertEqual(_check(passed, 'grounding')['status'], 'PASS', _check(passed, 'grounding')['issues'])

        stage, telemetry = episode()
        telemetry['frames'][0]['actors']['linlan']['left_foot'][1] = 0.036
        telemetry['frames'][1]['actors']['linlan']['left_foot'][1] = -0.041
        telemetry['frames'][2]['rover'] = {'wheels': [[0.0, 0.05, 0.0], [0.0, 0.031, 1.0]], 'position': [1, 0, 1]}
        telemetry['frames'][3]['rover']['wheel_min_y'] = -0.031
        report = run_qc(stage, telemetry)
        grounding = _check(report, 'grounding')
        self.assertEqual(grounding['status'], 'FAIL')
        self.assertEqual(grounding['frames'], [0, 1, 2, 3])
        self.assertIn('floating', _messages(grounding))
        self.assertIn('sunk', _messages(grounding))
        self.assertIn('wheel', _messages(grounding))
        self.assertEqual(grounding['replan_hints'], [HINT_GROUND])

    def test_foot_slide_passes_under_threshold_and_flags_sustained_or_isolated(self):
        stage, telemetry = episode()
        for index, row in enumerate(telemetry['frames'][:4]):
            row['actors']['linlan']['clips'] = ['run']
            row['actors']['linlan']['left_foot'][0] = index * 0.04
        passed = run_qc(stage, telemetry)
        self.assertEqual(_check(passed, 'foot_slide')['status'], 'PASS', _check(passed, 'foot_slide')['issues'])

        stage, telemetry = episode()
        for index, row in enumerate(telemetry['frames'][:4]):
            row['actors']['linlan']['clips'] = ['walk']
            row['actors']['linlan']['left_foot'][0] = index * 0.08
        sustained = _check(run_qc(stage, telemetry), 'foot_slide')
        self.assertEqual(sustained['status'], 'FAIL')
        self.assertEqual(sustained['frames'], [1, 2, 3])
        self.assertIn('sustained', _messages(sustained))
        self.assertEqual(sustained['replan_hints'], [HINT_WALK])

        stage, telemetry = episode()
        xs = [0.0, 0.08, 0.08, 0.08]
        for index, row in enumerate(telemetry['frames'][:4]):
            row['actors']['linlan']['clips'] = ['run']
            row['actors']['linlan']['left_foot'][0] = xs[index]
        isolated = _check(run_qc(stage, telemetry), 'foot_slide')
        self.assertEqual(isolated['status'], 'WARN')
        self.assertEqual(isolated['frames'], [1])
        self.assertIn('isolated', _messages(isolated))
        self.assertIn(HINT_WALK, report_hints(isolated))

    def test_prop_handoff_is_allowed_and_bad_contact_fails(self):
        stage, telemetry = episode()
        stage['props']['sensor']['owners'] = [
            {'from': 0, 'actor': 'chen'},
            {'from': 2, 'actor': 'linlan', 'handoff_window': [1, 2], 'max_gap': 0.36},
        ]
        samples = [
            ('chen', [0.0, 1.0, 0.0], 0.02),
            ('chen', [0.35, 1.0, 0.0], 0.12),
            ('linlan', [0.85, 1.0, 0.0], 0.20),
        ]
        for index, (owner, position, gap) in enumerate(samples):
            telemetry['frames'][index]['props']['sensor'] = {'owner': owner, 'position': position, 'hand_gap_m': gap}
        for row in telemetry['frames'][3:]:
            row['props']['sensor'] = {'owner': 'linlan', 'position': [0.85, 1.0, 0.0], 'hand_gap_m': 0.05}
        passed = _check(run_qc(stage, telemetry), 'prop_contact')
        self.assertEqual(passed['status'], 'PASS', passed['issues'])

        stage, telemetry = episode()
        telemetry['frames'][0]['props']['sensor'] = {'owner': 'linlan', 'position': [0.0, 0.0, 0.0], 'hand_gap_m': 0.13}
        telemetry['frames'][1]['props']['sensor'] = {'owner': 'linlan', 'position': [1.0, 0.0, 0.0], 'hand_gap_m': 0.02}
        telemetry['frames'][2]['props']['sensor'] = {'owner': None, 'position': [1.0, 0.0, 0.0], 'hand_gap_m': 0.02}
        telemetry['frames'][3]['props']['sensor'] = {'owner': ['linlan', 'chen'], 'position': [1.0, 0.0, 0.0], 'hand_gap_m': 0.02}
        telemetry['frames'][4]['props']['sensor'] = {'owner': 'chen', 'position': [1.0, 0.0, 0.0], 'hand_gap_m': 0.02}
        for row in telemetry['frames'][5:]:
            row['props']['sensor'] = {'owner': 'linlan', 'position': [1.0, 0.0, 0.0], 'hand_gap_m': 0.02}
        failed = _check(run_qc(stage, telemetry), 'prop_contact')
        self.assertEqual(failed['status'], 'FAIL')
        self.assertEqual(failed['frames'], [0, 1, 2, 3, 4])
        text = _messages(failed)
        self.assertIn('hand gap', text)
        self.assertIn('teleported', text)
        self.assertIn('missing owner', text)
        self.assertIn('duplicate owner', text)
        self.assertIn('unexpected owner', text)
        self.assertEqual(failed['replan_hints'], [HINT_GRIP, HINT_HANDOFF, HINT_OWNER])

    def test_camera_allows_corridor_and_flags_clipping(self):
        stage, telemetry = episode()
        corridor = telemetry['frames'][0]['camera']
        corridor['position'] = [0.0, 1.5, 6.0]
        corridor.pop('wall_distance_m')
        passed = _check(run_qc(stage, telemetry), 'camera_bounds')
        self.assertEqual(passed['status'], 'PASS', passed['issues'])

        stage, telemetry = episode()
        telemetry['frames'][0]['camera']['position'] = [8.0, 1.6, 0.0]
        telemetry['frames'][0]['camera']['wall_distance_m'] = 1.5
        near = telemetry['frames'][1]['camera']
        near['position'] = [3.92, 1.6, 0.0]
        near.pop('wall_distance_m')
        telemetry['frames'][2]['camera']['wall_distance_m'] = 0.05
        failed = _check(run_qc(stage, telemetry), 'camera_bounds')
        self.assertEqual(failed['status'], 'FAIL')
        self.assertEqual(failed['frames'], [0, 1, 2])
        self.assertIn('outside room bounds', _messages(failed))
        self.assertIn('clearance', _messages(failed))
        self.assertEqual(failed['replan_hints'], [HINT_CAMERA])

    def test_subject_framing_bounds_and_occlusion(self):
        stage, telemetry = episode()
        telemetry['frames'][0]['camera']['subject']['ndc_x'] = 0.95
        telemetry['frames'][0]['camera']['subject']['ndc_y'] = -0.95
        passed = _check(run_qc(stage, telemetry), 'subject_framing')
        self.assertEqual(passed['status'], 'PASS', passed['issues'])

        stage, telemetry = episode()
        telemetry['frames'][0]['camera']['subject']['ndc_x'] = 0.96
        telemetry['frames'][1]['camera']['subject']['ndc_y'] = -1.2
        telemetry['frames'][2]['camera']['subject']['occluded'] = True
        failed = _check(run_qc(stage, telemetry), 'subject_framing')
        self.assertEqual(failed['status'], 'FAIL')
        self.assertEqual(failed['frames'], [0, 1, 2])
        self.assertIn('out of frame', _messages(failed))
        self.assertIn('occluded', _messages(failed))
        self.assertEqual(failed['replan_hints'], [HINT_WIDEN])

    def test_audio_and_captions_pass_and_fail(self):
        stage, _telemetry = episode(frames=48)
        passed = _check(run_qc(stage, _telemetry, probe=_probe(2.0)), 'audio_sync')
        self.assertEqual(passed['status'], 'PASS', passed['issues'])
        edge = _check(run_qc(stage, _telemetry, probe=_probe(2.15)), 'audio_sync')
        self.assertEqual(edge['status'], 'PASS', edge['issues'])
        captions_only = _check(run_qc(stage, _telemetry), 'audio_sync')
        self.assertEqual(captions_only['status'], 'PASS', captions_only['issues'])

        duration = _check(run_qc(stage, _telemetry, probe=_probe(2.16)), 'audio_sync')
        self.assertEqual(duration['status'], 'FAIL')
        self.assertIn('duration', _messages(duration))
        size = _check(run_qc(stage, _telemetry, probe=_probe(2.0, width=1920)), 'audio_sync')
        self.assertIn('1280x720', _messages(size))
        fps = _check(run_qc(stage, _telemetry, probe=_probe(2.0, fps=30.0)), 'audio_sync')
        self.assertIn('fps', _messages(fps))
        silent = _check(run_qc(stage, _telemetry, probe=_probe(2.0, has_audio=False)), 'audio_sync')
        self.assertIn('audio stream', _messages(silent))
        no_video = _check(run_qc(stage, _telemetry, probe=_probe(2.0, has_video=False)), 'audio_sync')
        self.assertIn('video stream', _messages(no_video))

        stage['captions'] = [{'from': -1, 'to': 10, 'text': '早'}, {'from': 40, 'to': 49, 'text': '晚'}]
        captions = _check(run_qc(stage, _telemetry, probe=_probe(2.0)), 'audio_sync')
        self.assertEqual(captions['status'], 'FAIL')
        self.assertEqual(captions['frames'], [-1, 40])
        self.assertIn('outside', _messages(captions))
        self.assertIn(HINT_AUDIO, captions['replan_hints'])
        missing = _check(run_qc(stage, _telemetry, video_path=Path('missing-episode.mp4')), 'audio_sync')
        self.assertIn('video file is missing', _messages(missing))

    def test_exposure_passes_a_gradient_and_flags_bad_stills(self):
        stage, telemetry = episode()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _gradient(root / 'frame_0004.png')
            passed = _check(run_qc(stage, telemetry, review_dir=root), 'exposure')
            self.assertEqual(passed['status'], 'PASS', passed['issues'])

            bad = Path(tmp) / 'bad'
            bad.mkdir()
            _solid(bad / 'frame_0001.png', 0)
            _solid(bad / 'frame_0002.png', 255)
            _solid(bad / 'frame_0003.png', 80)
            half = np.zeros((8, 8, 3), dtype=np.uint8)
            half[:, :4] = 255
            half[:, 4:] = 40
            Image.fromarray(half, 'RGB').save(bad / 'frame_0004.png')
            (bad / 'frame_0005.png').write_bytes(b'not an image')
            failed = _check(run_qc(stage, telemetry, review_dir=bad), 'exposure')
        self.assertEqual(failed['status'], 'FAIL')
        self.assertEqual(failed['frames'], [1, 2, 3, 4, 5])
        text = _messages(failed)
        self.assertIn('black frame', text)
        self.assertIn('blown-out frame', text)
        self.assertIn('flat frame', text)
        self.assertIn('unreadable still', text)
        self.assertEqual(failed['replan_hints'], [HINT_EXPOSURE])
        gray = [issue for issue in failed['issues'] if issue['frame'] == 3]
        self.assertTrue(gray)
        self.assertTrue(all('flat frame' in issue['message'] for issue in gray))
        self.assertNotIn('black frame', gray[0]['message'])

        omitted = _check(run_qc(stage, telemetry), 'exposure')
        self.assertEqual(omitted['status'], 'WARN')
        self.assertIn('no stills', _messages(omitted))

    def test_animation_clips_and_closeup_capability(self):
        stage, telemetry = episode()
        passed = _check(run_qc(stage, telemetry), 'animation_completeness')
        self.assertEqual(passed['status'], 'PASS', passed['issues'])

        stage, telemetry = episode()
        telemetry['frames'][1]['actors']['linlan']['clip_status'] = {'walk': 'missing'}
        telemetry['frames'][2]['actors']['linlan']['clips'] = []
        failed = _check(run_qc(stage, telemetry), 'animation_completeness')
        self.assertEqual(failed['status'], 'FAIL')
        self.assertEqual(failed['frames'], [1, 2])
        self.assertIn('fell back to missing', _messages(failed))
        self.assertIn('fell back to empty', _messages(failed))
        self.assertEqual(failed['replan_hints'], [HINT_CLIP])

        stage, telemetry = episode()
        stage['actors']['linlan']['animation'] = {'fallbacks': {'walk': 'missing'}}
        static = _check(run_qc(stage, telemetry), 'animation_completeness')
        self.assertEqual(static['status'], 'FAIL')
        self.assertIn('fell back to missing', _messages(static))

        stage, telemetry = episode()
        stage['actors']['linlan']['capabilities'] = {'tier': 'TIER_B', 'has_fingers': False, 'blendshapes': False}
        close = _check(run_qc(stage, telemetry), 'animation_completeness')
        self.assertEqual(close['status'], 'FAIL')
        self.assertEqual(close['frames'], [4, 5, 6, 7])
        self.assertIn('TIER_B', _messages(close))
        self.assertEqual(close['replan_hints'], [HINT_CLOSEUP])

        stage, telemetry = episode()
        stage['actors']['linlan'].pop('capabilities')
        unknown = _check(run_qc(stage, telemetry), 'animation_completeness')
        self.assertEqual(unknown['status'], 'WARN')
        self.assertIn('unknown', _messages(unknown))

        stage, telemetry = episode()
        stage['actors']['linlan'].pop('capabilities')
        registry = {'assets': [{
            'asset_id': 'linlan',
            'tier': 'TIER_A',
            'capabilities': {'tier': 'TIER_A', 'has_fingers': True, 'blendshapes': True, 'morph_targets': list(REQUIRED_FACIAL_MORPHS)},
        }]}
        from_registry = _check(run_qc(stage, telemetry, registry=registry), 'animation_completeness')
        self.assertEqual(from_registry['status'], 'PASS', from_registry['issues'])
        registry['assets'][0]['tier'] = 'TIER_B'
        registry['assets'][0]['capabilities'] = {'tier': 'TIER_B', 'has_fingers': False, 'blendshapes': False}
        rejected = _check(run_qc(stage, telemetry, registry=registry), 'animation_completeness')
        self.assertEqual(rejected['status'], 'FAIL')
        self.assertIn(HINT_CLOSEUP, rejected['replan_hints'])

    def test_telemetry_dict_and_ffprobe_parser(self):
        stage, telemetry = episode(frames=2)
        telemetry['frames'] = {str(row['frame']): row for row in telemetry['frames']}
        telemetry['frames']['1']['actors']['linlan']['left_foot'][1] = 0.1
        grounding = _check(run_qc(stage, telemetry), 'grounding')
        self.assertEqual(grounding['frames'], [1])

        payload = {
            'streams': [
                {'codec_type': 'video', 'width': 1280, 'height': 720, 'avg_frame_rate': '24/1'},
                {'codec_type': 'audio', 'codec_name': 'aac'},
            ],
            'format': {'duration': '2.000'},
        }
        parsed = parse_probe(payload)
        self.assertEqual(parsed['width'], 1280)
        self.assertEqual(parsed['fps'], 24.0)
        self.assertTrue(parsed['has_audio'])
        completed = subprocess.CompletedProcess(args=['ffprobe'], returncode=0, stdout=json.dumps(payload), stderr='')
        with patch('tools.qc_pipeline.subprocess.run', return_value=completed) as run:
            probed = probe_media(Path('episode.mp4'))
        self.assertEqual(probed['duration'], 2.0)
        command = run.call_args.args[0]
        self.assertEqual(command[0], 'ffprobe')
        self.assertEqual(command[-1], 'episode.mp4')

    def test_cli_writes_the_report_and_returns_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stage, telemetry = episode()
            stage_path = root / 'stage3d.json'
            telemetry_path = root / 'qc_telemetry.json'
            stills = root / 'stills'
            stills.mkdir()
            _gradient(stills / 'frame_0002.png')
            out = root / 'qc_report.json'
            stage_path.write_text(json.dumps(stage), encoding='utf-8')
            telemetry_path.write_text(json.dumps(telemetry), encoding='utf-8')
            with redirect_stdout(io.StringIO()):
                code = main(['--stage', str(stage_path), '--telemetry', str(telemetry_path), '--stills', str(stills), '--out', str(out)])
            report = json.loads(out.read_text(encoding='utf-8'))
            self.assertEqual(code, 0, report)
            self.assertEqual(report['status'], 'PASS')

            telemetry['frames'][0]['actors']['linlan']['left_foot'][1] = 0.2
            telemetry_path.write_text(json.dumps(telemetry), encoding='utf-8')
            failed_out = root / 'failed.json'
            with redirect_stdout(io.StringIO()):
                failed_code = main(['--stage', str(stage_path), '--telemetry', str(telemetry_path), '--stills', str(stills), '--out', str(failed_out)])
            failed = json.loads(failed_out.read_text(encoding='utf-8'))
            self.assertEqual(failed_code, 1)
            self.assertEqual(failed['status'], 'FAIL')
            self.assertIn(HINT_GROUND, failed['replan_suggestions'])


def report_hints(check: dict) -> list[str]:
    return list(check['replan_hints'])


if __name__ == '__main__':
    unittest.main()
